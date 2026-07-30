from pathlib import Path

from shawnobot.models import CodexConfig, ProjectConfig
from shawnobot.services.codex import CodexService


def test_build_argv_uses_explicit_sandbox_and_stdin(tmp_path: Path) -> None:
    project = ProjectConfig(name="demo", path=tmp_path, codex_enabled=True)
    service = CodexService(
        CodexConfig(
            enabled=True,
            executable="codex",
            sandbox="workspace-write",
            model=None,
        ),
        data_dir=tmp_path,
        timeout_seconds=60,
        max_prompt_chars=1000,
        max_parallel_runs=1,
    )
    output = tmp_path / "result.md"
    argv = service.build_argv(project, output)
    assert argv[:2] == ["codex", "exec"]
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv
    assert argv[-1] == "-"


def test_build_chat_argv_persists_new_thread_and_sandboxes_resume(tmp_path: Path) -> None:
    project = ProjectConfig(name="demo", path=tmp_path, codex_enabled=True)
    service = CodexService(
        CodexConfig(True, "codex", "read-only"),
        data_dir=tmp_path,
        timeout_seconds=60,
        max_prompt_chars=1000,
        max_parallel_runs=1,
    )
    output = tmp_path / "result.md"

    new_argv = service.build_chat_argv(project, output, None)
    resume_argv = service.build_chat_argv(
        project,
        output,
        "11111111-1111-4111-8111-111111111111",
    )

    assert "--ephemeral" not in new_argv
    assert new_argv[new_argv.index("--sandbox") + 1] == "read-only"
    assert resume_argv[:3] == ["codex", "exec", "resume"]
    assert 'sandbox_mode="read-only"' in resume_argv
    assert "--dangerously-bypass-approvals-and-sandbox" not in resume_argv


def test_parses_only_thread_started_event() -> None:
    stdout = "\n".join(
        [
            '{"type":"item.completed","item":{"type":"reasoning","text":"hidden"}}',
            '{"type":"thread.started","thread_id":"11111111-1111-4111-8111-111111111111"}',
        ]
    )

    assert (
        CodexService._thread_id_from_events(stdout)
        == "11111111-1111-4111-8111-111111111111"
    )


def test_create_session_returns_persistent_thread_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    executable = tmp_path / "codex.exe"
    executable.touch()
    project = ProjectConfig(name="demo", path=tmp_path, codex_enabled=True)
    service = CodexService(
        CodexConfig(True, str(executable), "read-only"),
        data_dir=tmp_path,
        timeout_seconds=60,
        max_prompt_chars=1000,
        max_parallel_runs=1,
    )
    session_id = "11111111-1111-4111-8111-111111111111"
    monkeypatch.setattr(
        service,
        "_run_persistent",
        lambda *args, **kwargs: (session_id, "初始化完成", ""),
    )

    actual_id, response = service.create_session(
        project,
        "检查项目结构",
        title="research",
    )

    assert actual_id == session_id
    assert "Codex 会话已创建" in response
    assert "初始化完成" in response


def test_usage_queries_app_server_and_formats_remaining_quota(
    tmp_path: Path,
    monkeypatch,
) -> None:
    executable = tmp_path / "codex.exe"
    executable.touch()
    service = CodexService(
        CodexConfig(
            enabled=False,
            executable=str(executable),
            sandbox="read-only",
            model=None,
        ),
        data_dir=tmp_path,
        timeout_seconds=900,
        max_prompt_chars=1000,
        max_parallel_runs=1,
    )
    responses = {
        1: {"id": 1, "result": {"userAgent": "test"}},
        2: {
            "id": 2,
            "result": {
                "account": {"type": "chatgpt", "planType": "pro"},
                "requiresOpenaiAuth": True,
            },
        },
        3: {
            "id": 3,
            "result": {
                "rateLimits": {
                    "primary": {
                        "usedPercent": 25,
                        "windowDurationMins": 300,
                        "resetsAt": 1785398400,
                    },
                    "secondary": {
                        "usedPercent": 60.5,
                        "windowDurationMins": 10080,
                        "resetsAt": 1785916800,
                    },
                    "rateLimitReachedType": None,
                },
                "rateLimitResetCredits": {
                    "availableCount": 2,
                    "credits": [],
                },
            },
        },
    }
    captured: dict[str, str] = {}

    def fake_query(executable_path: str):
        captured["executable"] = executable_path
        return responses, "", False

    monkeypatch.setattr(service, "_query_usage_app_server", fake_query)
    result = service.usage()

    assert captured["executable"] == str(executable.resolve())
    assert "套餐：Pro" in result
    assert "5 小时额度：剩余 75%" in result
    assert "每周额度：剩余 39.5%" in result
    assert "可用额度重置：2 次" in result
    assert "固定 Token 个数" in result


def test_usage_reports_rpc_auth_error(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "codex.exe"
    executable.touch()
    service = CodexService(
        CodexConfig(False, str(executable), "read-only"),
        data_dir=tmp_path,
        timeout_seconds=20,
        max_prompt_chars=1000,
        max_parallel_runs=1,
    )

    monkeypatch.setattr(
        service,
        "_query_usage_app_server",
        lambda executable_path: (
            {
                3: {
                    "id": 3,
                    "error": {"code": -32000, "message": "authentication required"},
                }
            },
            "",
            False,
        ),
    )

    assert "codex login" in service.usage()
