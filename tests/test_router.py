from __future__ import annotations

from pathlib import Path

from shawnobot.audit import AuditLogger
from shawnobot.bot.commands import CommandRouter
from shawnobot.models import (
    AppConfig,
    CodexConfig,
    FeishuConfig,
    LimitsConfig,
    ManagementConfig,
    ProcessWatchRecord,
    ProjectConfig,
    RequestContext,
    SessionConfig,
    Settings,
)
from shawnobot.services.desktop import DesktopProjectEntry
from shawnobot.services.nlu import IntentDecision


class StubProjects:
    def __init__(self, project: ProjectConfig | None = None) -> None:
        self.project = project

    def get(self, name: str) -> ProjectConfig:
        assert self.project is not None
        assert name == self.project.name
        return self.project

    def list_projects(self) -> str:
        return "PROJECTS"

    def status(self, name: str) -> str:
        return f"STATUS:{name}"

    def tail_log(self, project: str, log: str | None, lines: int) -> str:
        return f"LOG:{project}:{log}:{lines}"


class StubJobs:
    def list_jobs(self) -> str:
        return "JOBS"

    def job_log(self, job_id: str, lines: int) -> str:
        return f"JOBLOG:{job_id}:{lines}"

    def stop(self, job_id: str) -> str:
        return f"STOP:{job_id}"


class StubCodex:
    def usage(self) -> str:
        return "USAGE"

    def active_status(self) -> str:
        return "CODEX_STATUS"

    def cancel_active(self) -> str:
        return "CODEX_CANCEL"

    def chat(self, project: ProjectConfig, prompt: str, conversation_key: str) -> str:
        return f"CHAT:{project.name}:{conversation_key}:{prompt}"

    def reset_conversation(self, conversation_key: str) -> str:
        return f"RESET:{conversation_key}"

    def conversation_context(self, conversation_key: str) -> str:
        return f"CONTEXT:{conversation_key}"

    def resume_session(
        self,
        project: ProjectConfig,
        session_id: str,
        prompt: str,
        *,
        title: str,
    ) -> str:
        return f"RESUME:{project.name}:{session_id}:{title}:{prompt}"

    def create_session(
        self,
        project: ProjectConfig,
        prompt: str,
        *,
        title: str,
    ) -> tuple[str | None, str]:
        return (
            "22222222-2222-4222-8222-222222222222",
            f"### Codex 会话已创建 · {title}\n{project.name}:{prompt}",
        )


class StubSystem:
    def status(self) -> str:
        return "HOST"

    def gpu_status(self) -> str:
        return "GPU"

    def codex_desktop_status(self) -> str:
        return "DESKTOP"


class StubDesktop:
    def __init__(self, path: Path) -> None:
        self.path = path

    def status(self) -> str:
        return "DESKTOP"

    def resolve_project(self, reference: str) -> DesktopProjectEntry:
        return DesktopProjectEntry(
            1,
            "local-demo",
            "DesktopDemo",
            self.path,
            1,
            1,
            True,
            True,
            (),
            (),
        )

    @staticmethod
    def suggested_alias(entry: DesktopProjectEntry) -> str:
        return "desktop_demo"


class StubRegistry:
    def __init__(self, project: ProjectConfig) -> None:
        self.project = project
        self.sessions: list[tuple[str, str, str, str]] = []

    def create_project(self, name: str, path: str, description: str) -> ProjectConfig:
        return ProjectConfig(name, Path(path), description, True)

    def validate_session_registration(self, alias: str, project_name: str) -> None:
        return None

    def add_session(
        self,
        alias: str,
        project_name: str,
        session_id: str,
        title: str,
    ) -> SessionConfig:
        self.sessions.append((alias, project_name, session_id, title))
        return SessionConfig(alias, session_id, title, project_name)


class StubWatches:
    def register(
        self,
        pid: int,
        label: str,
        context: RequestContext,
    ) -> ProcessWatchRecord:
        return ProcessWatchRecord(
            "watch123",
            pid,
            1.0,
            "python.exe",
            label or "python.exe",
            context.sender_id,
            context.chat_id,
            context.chat_type,
            "2026-07-30T00:00:00+00:00",
        )

    def list_for(self, context: RequestContext) -> str:
        return "WATCHES"

    def cancel(self, watch_id: str, context: RequestContext) -> str:
        return f"UNWATCH:{watch_id}"


class StubNlu:
    def __init__(self, decision: IntentDecision | None = None) -> None:
        self.decision = decision

    def status(self) -> str:
        return "NLU_STATUS"

    def classify(self, text: str, catalog: str) -> IntentDecision:
        return self.decision or IntentDecision.codex(text)


def make_router(
    tmp_path: Path,
    *,
    natural_language: bool = False,
    nlu_decision: IntentDecision | None = None,
) -> CommandRouter:
    project = ProjectConfig("demo", tmp_path, codex_enabled=True)
    session = SessionConfig(
        "history",
        "11111111-1111-4111-8111-111111111111",
        "History",
        "demo",
    )
    settings = Settings(
        app=AppConfig("/", tmp_path / "data", tmp_path / "audit.jsonl", 1000),
        feishu=FeishuConfig("", "", True, frozenset(), frozenset()),
        limits=LimitsConfig(10, 10, 300, 1000, 1, 1),
        codex=CodexConfig(
            True,
            "codex",
            "read-only",
            natural_language_enabled=natural_language,
            default_project="demo",
        ),
        management=ManagementConfig(True, (tmp_path,)),
        projects={"demo": project},
        sessions={"history": session},
        config_path=tmp_path / "config.yaml",
    )
    return CommandRouter(
        settings,
        AuditLogger(tmp_path / "audit.jsonl"),
        StubProjects(project),  # type: ignore[arg-type]
        StubJobs(),  # type: ignore[arg-type]
        StubCodex(),  # type: ignore[arg-type]
        StubSystem(),  # type: ignore[arg-type]
        StubDesktop(tmp_path),  # type: ignore[arg-type]
        StubSessions(),  # type: ignore[arg-type]
        StubRegistry(project),  # type: ignore[arg-type]
        StubWatches(),  # type: ignore[arg-type]
        StubNlu(nlu_decision),  # type: ignore[arg-type]
    )


def test_routes_log_with_bounded_lines(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    result = router.handle(
        "/log demo train 9999",
        RequestContext("local", "local", "p2p"),
    )
    assert result == "LOG:demo:train:300"


def test_plain_text_does_not_become_shell_or_codex(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    result = router.handle(
        "rm -rf data",
        RequestContext("local", "local", "p2p"),
    )
    assert "/help" in result


def test_routes_codex_usage_without_arguments(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    context = RequestContext("local", "local", "p2p")

    assert router.handle("/usage", context) == "USAGE"
    assert router.handle("/额度", context) == "USAGE"
    assert "仅查询" in router.handle("/usage reset", context)
    assert router.handle("/codex-status", context) == "CODEX_STATUS"
    assert router.handle("/codex-cancel", context) == "CODEX_CANCEL"


def test_plain_text_routes_to_persistent_codex_chat_when_enabled(tmp_path: Path) -> None:
    router = make_router(tmp_path, natural_language=True)
    context = RequestContext("ou_owner", "oc_private", "p2p")

    result = router.handle("检查最近训练结果", context)

    assert result.startswith("CHAT:demo:p2p:oc_private:")
    assert "## 转译任务\n检查最近训练结果" in result
    assert "## 原始用户消息（用于校验意图，不要忽略）\n检查最近训练结果" in result
    assert router.handle("/context", context) == "CONTEXT:p2p:oc_private"
    assert router.handle("/new", context) == "RESET:p2p:oc_private"


def test_deepseek_translation_is_sent_to_codex_not_user(tmp_path: Path) -> None:
    router = make_router(
        tmp_path,
        natural_language=True,
        nlu_decision=IntentDecision.codex(
            "检查代码并修复测试，但先阅读现有实现。",
            project="demo",
            confidence=0.94,
        ),
    )
    context = RequestContext("ou_owner", "oc_private", "p2p")

    result = router.handle("把测试修好", context)

    assert result.startswith("CHAT:demo:p2p:oc_private:")
    assert "## 转译任务\n检查代码并修复测试，但先阅读现有实现。" in result
    assert "## 原始用户消息（用于校验意图，不要忽略）\n把测试修好" in result


def test_plain_text_can_route_to_safe_command_via_nlu(tmp_path: Path) -> None:
    router = make_router(
        tmp_path,
        natural_language=True,
        nlu_decision=IntentDecision("command", "gpu", (), 0.98),
    )
    context = RequestContext("ou_owner", "oc_private", "p2p")

    assert router.handle("帮我看看显卡状态", context) == "GPU"
    assert router.handle("/nlu-status", context) == "NLU_STATUS"


def test_desktop_commands_use_only_allowlisted_session(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    context = RequestContext("ou_owner", "oc_private", "p2p")

    assert router.handle("/desktop", context) == "DESKTOP\n\nSESSIONS"
    result = router.handle("/desktop-send history 继续分析", context)
    assert result.startswith(
        "RESUME:demo:11111111-1111-4111-8111-111111111111:History:"
    )
    assert "未在白名单" in router.handle("/desktop-send unknown hello", context)


def test_routes_remote_project_and_session_registration(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    context = RequestContext("ou_owner", "oc_private", "p2p")

    project_result = router.handle(
        f"/project-new new_demo {tmp_path.as_posix()}/new_demo 新项目",
        context,
    )
    session_result = router.handle(
        "/session-new research demo 检查项目结构",
        context,
    )

    assert "项目已加入白名单" in project_result
    assert "Codex 会话已创建" in session_result
    assert "已加入白名单" in session_result


def test_routes_desktop_project_import(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    context = RequestContext("ou_owner", "oc_private", "p2p")

    assert router.handle("/desktop-projects", context) == "DESKTOP"
    result = router.handle("/project-allow 1 imported_demo", context)

    assert "桌面项目已加入白名单" in result
    assert "imported_demo" in result


def test_routes_process_watch_commands(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    context = RequestContext("ou_owner", "oc_private", "p2p")

    assert "已开始跟踪进程" in router.handle("/watch 123 训练任务", context)
    assert router.handle("/watches", context) == "WATCHES"
    assert router.handle("/unwatch watch123", context) == "UNWATCH:watch123"


class StubSessions:
    def list_sessions(self) -> str:
        return "SESSIONS"

    def read(self, alias: str, message_limit: int) -> str:
        return f"SESSION:{alias}:{message_limit}"

    def get(self, alias: str):
        if alias != "history":
            return None
        return SessionConfig(
            "history",
            "11111111-1111-4111-8111-111111111111",
            "History",
            "demo",
        )
