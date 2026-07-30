from __future__ import annotations

from dataclasses import dataclass

from .audit import AuditLogger
from .bot.commands import CommandRouter
from .models import Settings
from .security import Authorizer
from .services.codex import CodexService
from .services.desktop import DesktopService
from .services.jobs import JobManager
from .services.nlu import NluService
from .services.projects import ProjectService
from .services.registry import RegistryService
from .services.sessions import SessionService
from .services.system import SystemService
from .services.watches import ProcessWatchService


@dataclass(frozen=True)
class Runtime:
    settings: Settings
    audit: AuditLogger
    authorizer: Authorizer
    router: CommandRouter
    watches: ProcessWatchService


def build_runtime(settings: Settings) -> Runtime:
    audit = AuditLogger(settings.app.audit_log)
    authorizer = Authorizer(settings.feishu)
    projects = ProjectService(
        settings.projects,
        settings.limits.command_timeout_seconds,
    )
    jobs = JobManager(
        settings.app.data_dir,
        settings.limits.max_parallel_tasks,
    )
    codex = CodexService(
        settings.codex,
        data_dir=settings.app.data_dir,
        timeout_seconds=settings.limits.codex_timeout_seconds,
        max_prompt_chars=settings.limits.max_codex_prompt_chars,
        max_parallel_runs=settings.limits.max_parallel_codex_runs,
    )
    sessions = SessionService(settings.sessions)
    registry = RegistryService(settings)
    watches = ProcessWatchService(settings.app.data_dir)
    desktop = DesktopService(settings)
    nlu = NluService(settings.nlu)
    router = CommandRouter(
        settings,
        audit,
        projects,
        jobs,
        codex,
        SystemService(),
        desktop,
        sessions,
        registry,
        watches,
        nlu,
    )
    return Runtime(settings, audit, authorizer, router, watches)
