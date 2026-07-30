from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from dotenv import dotenv_values, load_dotenv

from .models import (
    AppConfig,
    CodexConfig,
    FeishuConfig,
    LimitsConfig,
    ManagementConfig,
    NluConfig,
    ProjectConfig,
    SessionConfig,
    Settings,
    TaskConfig,
)


class ConfigurationError(ValueError):
    """Raised when ShawnoBot configuration is incomplete or unsafe."""


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigurationError(f"{label} 必须是 YAML 映射")
    return value


def _string_list(value: Any, label: str) -> frozenset[str]:
    if value is None:
        return frozenset()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigurationError(f"{label} 必须是字符串列表")
    return frozenset(item.strip() for item in value if item.strip())


def _resolve_path(value: str, base: Path) -> Path:
    expanded = Path(os.path.expandvars(os.path.expanduser(value)))
    if not expanded.is_absolute():
        expanded = base / expanded
    return expanded.resolve()


def _positive_int(value: Any, label: str, default: int, *, allow_zero: bool = False) -> int:
    number = default if value is None else int(value)
    minimum = 0 if allow_zero else 1
    if number < minimum:
        raise ConfigurationError(f"{label} 必须 >= {minimum}")
    return number


def _confidence(value: Any, label: str, default: float) -> float:
    number = default if value is None else float(value)
    if not 0 <= number <= 1:
        raise ConfigurationError(f"{label} 必须位于 0-1 之间")
    return number


def load_settings(config_path: str | Path) -> Settings:
    path = Path(config_path).expanduser().resolve()
    if not path.is_file():
        raise ConfigurationError(
            f"配置文件不存在：{path}。请复制 config.example.yaml 为 config.yaml 后修改。"
        )

    dotenv_path = path.parent / ".env"
    file_environment = dotenv_values(dotenv_path)
    load_dotenv(dotenv_path, override=False)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    root = _mapping(raw, "根配置")
    base = path.parent

    app_raw = _mapping(root.get("app"), "app")
    data_dir = _resolve_path(str(app_raw.get("data_dir", "./data")), base)
    audit_log = _resolve_path(str(app_raw.get("audit_log", "./data/audit.jsonl")), base)
    prefix = str(app_raw.get("command_prefix", "/")).strip()
    if not prefix or len(prefix) > 4:
        raise ConfigurationError("app.command_prefix 长度必须为 1-4 个字符")
    app = AppConfig(
        command_prefix=prefix,
        data_dir=data_dir,
        audit_log=audit_log,
        max_reply_chars=_positive_int(
            app_raw.get("max_reply_chars"), "app.max_reply_chars", 6000
        ),
    )

    feishu_raw = _mapping(root.get("feishu"), "feishu")
    feishu = FeishuConfig(
        app_id=os.environ.get("FEISHU_APP_ID", "").strip(),
        app_secret=os.environ.get("FEISHU_APP_SECRET", "").strip(),
        require_mention_in_group=bool(feishu_raw.get("require_mention_in_group", True)),
        allowed_user_ids=_string_list(
            feishu_raw.get("allowed_user_ids"), "feishu.allowed_user_ids"
        ),
        allowed_chat_ids=_string_list(
            feishu_raw.get("allowed_chat_ids"), "feishu.allowed_chat_ids"
        ),
    )

    limits_raw = _mapping(root.get("limits"), "limits")
    limits = LimitsConfig(
        command_timeout_seconds=_positive_int(
            limits_raw.get("command_timeout_seconds"),
            "limits.command_timeout_seconds",
            120,
        ),
        codex_timeout_seconds=_positive_int(
            limits_raw.get("codex_timeout_seconds"),
            "limits.codex_timeout_seconds",
            900,
        ),
        max_log_lines=_positive_int(
            limits_raw.get("max_log_lines"), "limits.max_log_lines", 300
        ),
        max_codex_prompt_chars=_positive_int(
            limits_raw.get("max_codex_prompt_chars"),
            "limits.max_codex_prompt_chars",
            8000,
        ),
        max_parallel_codex_runs=_positive_int(
            limits_raw.get("max_parallel_codex_runs"),
            "limits.max_parallel_codex_runs",
            1,
        ),
        max_parallel_tasks=_positive_int(
            limits_raw.get("max_parallel_tasks"), "limits.max_parallel_tasks", 2
        ),
    )

    codex_raw = _mapping(root.get("codex"), "codex")
    sandbox = str(codex_raw.get("sandbox", "read-only"))
    if sandbox not in {"read-only", "workspace-write"}:
        raise ConfigurationError("codex.sandbox 只能是 read-only 或 workspace-write")
    model_value = codex_raw.get("model")
    executable_value = str(codex_raw.get("executable", "codex")).strip()
    if "/" in executable_value or "\\" in executable_value:
        executable_value = str(_resolve_path(executable_value, base))
    default_project_value = codex_raw.get("default_project")
    codex = CodexConfig(
        enabled=bool(codex_raw.get("enabled", False)),
        executable=executable_value,
        sandbox=sandbox,  # type: ignore[arg-type]
        model=str(model_value) if model_value else None,
        natural_language_enabled=bool(codex_raw.get("natural_language_enabled", False)),
        default_project=str(default_project_value) if default_project_value else None,
    )

    nlu_raw = _mapping(root.get("nlu"), "nlu")
    base_url = str(nlu_raw.get("base_url", "https://api.deepseek.com")).strip().rstrip("/")
    parsed_base_url = urlparse(base_url)
    if parsed_base_url.scheme not in {"http", "https"} or not parsed_base_url.netloc:
        raise ConfigurationError("nlu.base_url 必须是有效的 HTTP(S) URL")
    model = str(nlu_raw.get("model", "deepseek-v4-pro")).strip()
    if not model:
        raise ConfigurationError("nlu.model 不能为空")
    allowed_commands = _string_list(
        nlu_raw.get("allowed_commands"),
        "nlu.allowed_commands",
    )
    nlu_defaults = NluConfig()
    nlu = NluConfig(
        enabled=bool(nlu_raw.get("enabled", False)),
        # ShawnoBot's private .env is authoritative for this optional provider.
        # This avoids a stale desktop/system variable silently shadowing a key
        # that the operator has just updated in the project.
        api_key=str(
            file_environment.get("DEEPSEEK_API_KEY")
            or os.environ.get("DEEPSEEK_API_KEY", "")
        ).strip(),
        base_url=base_url,
        model=model,
        timeout_seconds=_positive_int(
            nlu_raw.get("timeout_seconds"), "nlu.timeout_seconds", 15
        ),
        min_confidence=_confidence(
            nlu_raw.get("min_confidence"), "nlu.min_confidence", 0.85
        ),
        max_input_chars=_positive_int(
            nlu_raw.get("max_input_chars"), "nlu.max_input_chars", 2000
        ),
        max_output_tokens=_positive_int(
            nlu_raw.get("max_output_tokens"), "nlu.max_output_tokens", 300
        ),
        allowed_commands=allowed_commands or nlu_defaults.allowed_commands,
    )

    management_raw = _mapping(root.get("management"), "management")
    roots_value = management_raw.get("allowed_project_roots", [])
    if not isinstance(roots_value, list) or not all(
        isinstance(item, str) and item.strip() for item in roots_value
    ):
        raise ConfigurationError("management.allowed_project_roots 必须是非空字符串组成的列表")
    management = ManagementConfig(
        remote_registration_enabled=bool(
            management_raw.get("remote_registration_enabled", False)
        ),
        allowed_project_roots=tuple(_resolve_path(item, base) for item in roots_value),
    )

    projects_raw = _mapping(root.get("projects"), "projects")
    projects: dict[str, ProjectConfig] = {}
    for project_name, project_value in projects_raw.items():
        if not isinstance(project_name, str) or not project_name.strip():
            raise ConfigurationError("项目名称必须是非空字符串")
        project_raw = _mapping(project_value, f"projects.{project_name}")
        if "path" not in project_raw:
            raise ConfigurationError(f"projects.{project_name}.path 缺失")
        project_path = _resolve_path(str(project_raw["path"]), base)

        logs_raw = _mapping(project_raw.get("logs"), f"projects.{project_name}.logs")
        logs: dict[str, Path] = {}
        for log_name, log_value in logs_raw.items():
            if not isinstance(log_name, str) or not isinstance(log_value, str):
                raise ConfigurationError(f"projects.{project_name}.logs 必须是字符串映射")
            candidate = _resolve_path(log_value, project_path)
            try:
                candidate.relative_to(project_path)
            except ValueError as exc:
                raise ConfigurationError(
                    f"日志 {project_name}/{log_name} 必须位于项目目录内"
                ) from exc
            logs[log_name] = candidate

        tasks_raw = _mapping(project_raw.get("tasks"), f"projects.{project_name}.tasks")
        tasks: dict[str, TaskConfig] = {}
        for task_name, task_value in tasks_raw.items():
            task_raw = _mapping(task_value, f"projects.{project_name}.tasks.{task_name}")
            argv = task_raw.get("argv")
            if (
                not isinstance(argv, list)
                or not argv
                or not all(isinstance(item, str) and item for item in argv)
            ):
                raise ConfigurationError(
                    f"任务 {project_name}/{task_name} 的 argv 必须是非空字符串列表"
                )
            env_raw = _mapping(
                task_raw.get("env"), f"projects.{project_name}.tasks.{task_name}.env"
            )
            if not all(isinstance(k, str) and isinstance(v, str) for k, v in env_raw.items()):
                raise ConfigurationError(f"任务 {project_name}/{task_name} 的 env 必须是字符串映射")
            task_cwd = project_path
            if task_raw.get("cwd"):
                task_cwd = _resolve_path(str(task_raw["cwd"]), project_path)
                try:
                    task_cwd.relative_to(project_path)
                except ValueError as exc:
                    raise ConfigurationError(
                        f"任务 {project_name}/{task_name} 的 cwd 必须位于项目目录内"
                    ) from exc
            tasks[str(task_name)] = TaskConfig(
                argv=tuple(argv),
                description=str(task_raw.get("description", "")),
                background=bool(task_raw.get("background", False)),
                timeout_seconds=_positive_int(
                    task_raw.get("timeout_seconds"),
                    f"projects.{project_name}.tasks.{task_name}.timeout_seconds",
                    limits.command_timeout_seconds,
                    allow_zero=bool(task_raw.get("background", False)),
                ),
                env=dict(env_raw),
                cwd=task_cwd,
            )

        projects[project_name] = ProjectConfig(
            name=project_name,
            path=project_path,
            description=str(project_raw.get("description", "")),
            codex_enabled=bool(project_raw.get("codex_enabled", False)),
            logs=logs,
            tasks=tasks,
        )

    sessions_raw = _mapping(root.get("sessions"), "sessions")
    sessions: dict[str, SessionConfig] = {}
    for alias, session_value in sessions_raw.items():
        if not isinstance(alias, str) or not alias.strip():
            raise ConfigurationError("会话别名必须是非空字符串")
        session_raw = _mapping(session_value, f"sessions.{alias}")
        session_id = str(session_raw.get("id", "")).strip()
        if not session_id:
            raise ConfigurationError(f"sessions.{alias}.id 缺失")
        sessions[alias] = SessionConfig(
            alias=alias,
            session_id=session_id,
            title=str(session_raw.get("title", "")),
            project=str(session_raw["project"]) if session_raw.get("project") else None,
        )

    return Settings(
        app=app,
        feishu=feishu,
        limits=limits,
        codex=codex,
        management=management,
        projects=projects,
        sessions=sessions,
        config_path=path,
        nlu=nlu,
    )


def validate_runtime(settings: Settings, *, require_credentials: bool = True) -> list[str]:
    warnings: list[str] = []
    if require_credentials and (not settings.feishu.app_id or not settings.feishu.app_secret):
        raise ConfigurationError("缺少 FEISHU_APP_ID 或 FEISHU_APP_SECRET")
    if not settings.feishu.allowed_user_ids:
        warnings.append("allowed_user_ids 为空：所有远程请求都会被拒绝")
    if not settings.projects:
        warnings.append("尚未配置任何项目")
    if (
        settings.management.remote_registration_enabled
        and not settings.management.allowed_project_roots
    ):
        warnings.append("已启用远程登记，但 management.allowed_project_roots 为空")
    for root in settings.management.allowed_project_roots:
        if not root.is_dir():
            warnings.append(f"远程登记根目录不存在：{root}")
    if (
        settings.codex.natural_language_enabled
        and settings.codex.default_project not in settings.projects
    ):
        warnings.append("已启用自然语言，但 codex.default_project 未指向有效项目")
    if settings.nlu.enabled and not settings.nlu.api_key:
        warnings.append(
            "已启用 DeepSeek 自然语言路由，但缺少 DEEPSEEK_API_KEY；普通文本将直接回退到 Codex"
        )
    for name, project in settings.projects.items():
        if not project.path.is_dir():
            warnings.append(f"项目 {name} 的目录不存在：{project.path}")
    for alias, session in settings.sessions.items():
        if session.project and session.project not in settings.projects:
            warnings.append(f"会话 {alias} 引用了不存在的项目：{session.project}")
    settings.app.data_dir.mkdir(parents=True, exist_ok=True)
    settings.app.audit_log.parent.mkdir(parents=True, exist_ok=True)
    return warnings
