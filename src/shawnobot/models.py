from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

DEFAULT_NLU_COMMANDS = frozenset(
    {
        "help",
        "projects",
        "project-new",
        "project-allow",
        "host",
        "gpu",
        "status",
        "log",
        "task",
        "jobs",
        "joblog",
        "stop",
        "codex-status",
        "codex-cancel",
        "new",
        "context",
        "usage",
        "sessions",
        "session-new",
        "session",
        "desktop",
        "desktop-projects",
        "desktop-send",
        "watch",
        "watches",
        "unwatch",
    }
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class TaskConfig:
    argv: tuple[str, ...]
    description: str = ""
    background: bool = False
    timeout_seconds: int = 120
    env: dict[str, str] = field(default_factory=dict)
    cwd: Path | None = None


@dataclass(frozen=True)
class ProjectConfig:
    name: str
    path: Path
    description: str = ""
    codex_enabled: bool = False
    logs: dict[str, Path] = field(default_factory=dict)
    tasks: dict[str, TaskConfig] = field(default_factory=dict)


@dataclass(frozen=True)
class SessionConfig:
    alias: str
    session_id: str
    title: str = ""
    project: str | None = None


@dataclass(frozen=True)
class AppConfig:
    command_prefix: str
    data_dir: Path
    audit_log: Path
    max_reply_chars: int


@dataclass(frozen=True)
class FeishuConfig:
    app_id: str
    app_secret: str
    require_mention_in_group: bool
    allowed_user_ids: frozenset[str]
    allowed_chat_ids: frozenset[str]


@dataclass(frozen=True)
class LimitsConfig:
    command_timeout_seconds: int
    codex_timeout_seconds: int
    max_log_lines: int
    max_codex_prompt_chars: int
    max_parallel_codex_runs: int
    max_parallel_tasks: int


@dataclass(frozen=True)
class CodexConfig:
    enabled: bool
    executable: str
    sandbox: Literal["read-only", "workspace-write"]
    model: str | None = None
    natural_language_enabled: bool = False
    default_project: str | None = None


@dataclass(frozen=True)
class NluConfig:
    enabled: bool = False
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-pro"
    timeout_seconds: int = 15
    min_confidence: float = 0.85
    max_input_chars: int = 2000
    max_output_tokens: int = 300
    allowed_commands: frozenset[str] = DEFAULT_NLU_COMMANDS


@dataclass(frozen=True)
class ManagementConfig:
    remote_registration_enabled: bool
    allowed_project_roots: tuple[Path, ...]


@dataclass(frozen=True)
class Settings:
    app: AppConfig
    feishu: FeishuConfig
    limits: LimitsConfig
    codex: CodexConfig
    management: ManagementConfig
    projects: dict[str, ProjectConfig]
    sessions: dict[str, SessionConfig]
    config_path: Path
    nlu: NluConfig = field(default_factory=NluConfig)


@dataclass(frozen=True)
class RequestContext:
    sender_id: str
    chat_id: str
    chat_type: str
    message_id: str = ""


@dataclass(frozen=True)
class ProcessWatchRecord:
    id: str
    pid: int
    process_create_time: float
    process_name: str
    label: str
    sender_id: str
    chat_id: str
    chat_type: str
    created_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "pid": self.pid,
            "process_create_time": self.process_create_time,
            "process_name": self.process_name,
            "label": self.label,
            "sender_id": self.sender_id,
            "chat_id": self.chat_id,
            "chat_type": self.chat_type,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> ProcessWatchRecord:
        return cls(
            id=str(value["id"]),
            pid=int(value["pid"]),
            process_create_time=float(value["process_create_time"]),
            process_name=str(value["process_name"]),
            label=str(value["label"]),
            sender_id=str(value["sender_id"]),
            chat_id=str(value["chat_id"]),
            chat_type=str(value["chat_type"]),
            created_at=str(value["created_at"]),
        )


JobStatus = Literal["running", "completed", "failed", "stopped", "unknown"]


@dataclass
class JobRecord:
    id: str
    project: str
    task: str
    pid: int
    process_create_time: float
    status: JobStatus
    started_at: str
    log_path: str
    return_code: int | None = None
    ended_at: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "project": self.project,
            "task": self.task,
            "pid": self.pid,
            "process_create_time": self.process_create_time,
            "status": self.status,
            "started_at": self.started_at,
            "log_path": self.log_path,
            "return_code": self.return_code,
            "ended_at": self.ended_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> JobRecord:
        return cls(
            id=str(value["id"]),
            project=str(value["project"]),
            task=str(value["task"]),
            pid=int(value["pid"]),
            process_create_time=float(value["process_create_time"]),
            status=str(value["status"]),  # type: ignore[arg-type]
            started_at=str(value["started_at"]),
            log_path=str(value["log_path"]),
            return_code=(
                int(value["return_code"]) if value.get("return_code") is not None else None
            ),
            ended_at=str(value["ended_at"]) if value.get("ended_at") else None,
        )
