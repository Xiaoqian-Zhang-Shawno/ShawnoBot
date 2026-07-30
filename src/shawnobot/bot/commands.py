from __future__ import annotations

import shlex

from ..audit import AuditLogger
from ..models import ProjectConfig, RequestContext, Settings
from ..services.codex import CodexService
from ..services.desktop import DesktopProjectError, DesktopService
from ..services.jobs import JobManager
from ..services.nlu import NluError, NluService
from ..services.projects import ProjectService
from ..services.registry import RegistryError, RegistryService
from ..services.sessions import SessionService
from ..services.system import SystemService
from ..services.watches import ProcessWatchError, ProcessWatchService


class CommandRouter:
    def __init__(
        self,
        settings: Settings,
        audit: AuditLogger,
        projects: ProjectService,
        jobs: JobManager,
        codex: CodexService,
        system: SystemService,
        desktop: DesktopService,
        sessions: SessionService,
        registry: RegistryService,
        watches: ProcessWatchService,
        nlu: NluService,
    ) -> None:
        self.settings = settings
        self.audit = audit
        self.projects = projects
        self.jobs = jobs
        self.codex = codex
        self.system = system
        self.desktop = desktop
        self.sessions = sessions
        self.registry = registry
        self.watches = watches
        self.nlu = nlu

    def handle(self, text: str, context: RequestContext) -> str:
        raw = text.strip()
        prefix = self.settings.app.command_prefix
        if not raw.startswith(prefix):
            return self._handle_natural_language(raw, context)
        try:
            parts = shlex.split(raw, posix=True)
        except ValueError as exc:
            return f"指令解析失败：{exc}"
        if not parts:
            return self.help_text()

        command = parts[0][len(prefix) :].lower()
        args = parts[1:]
        self.audit.record(
            "command_received",
            context=context,
            details={"command": command, "arg_count": len(args)},
        )
        try:
            response = self._dispatch(command, args, context)
            self.audit.record(
                "command_completed",
                context=context,
                details={"command": command, "response_chars": len(response)},
            )
        except Exception as exc:
            self.audit.record(
                "command_failed",
                context=context,
                details={"command": command, "error_type": type(exc).__name__},
            )
            response = f"指令执行失败：{type(exc).__name__}。详情已写入本地审计日志。"
        return self._truncate(response)

    def _dispatch(
        self,
        command: str,
        args: list[str],
        context: RequestContext,
    ) -> str:
        if command in {"help", "帮助"}:
            return self.help_text()
        if command in {"nlu-status", "语义路由"}:
            if args:
                return "用法：`/nlu-status`"
            return self.nlu.status()
        if command in {"projects", "项目"}:
            return self.projects.list_projects()
        if command in {"project-new", "新建项目"}:
            return self._project_new(args)
        if command in {"project-allow", "项目授权"}:
            return self._project_allow(args)
        if command in {"host", "主机"}:
            return self.system.status()
        if command in {"gpu"}:
            return self.system.gpu_status()
        if command in {"status", "状态"}:
            if not args:
                return self.system.status() + "\n\n" + self.jobs.list_jobs()
            return self.projects.status(args[0])
        if command in {"log", "日志"}:
            return self._log(args)
        if command in {"task", "任务"}:
            return self._task(args)
        if command in {"jobs", "任务列表"}:
            return self.jobs.list_jobs()
        if command in {"joblog", "任务日志"}:
            if len(args) != 1:
                return "用法：`/joblog <job_id>`"
            return self.jobs.job_log(args[0], min(100, self.settings.limits.max_log_lines))
        if command in {"stop", "停止"}:
            if len(args) != 1:
                return "用法：`/stop <job_id>`"
            return self.jobs.stop(args[0])
        if command in {"codex"}:
            return self._codex(args)
        if command in {"codex-status", "codex状态"}:
            if args:
                return "用法：`/codex-status`"
            return self.codex.active_status()
        if command in {"codex-cancel", "取消codex"}:
            if args:
                return "用法：`/codex-cancel`"
            return self.codex.cancel_active()
        if command in {"new", "新对话"}:
            if args:
                return "用法：`/new`"
            return self.codex.reset_conversation(self._conversation_key(context))
        if command in {"context", "上下文"}:
            if args:
                return "用法：`/context`"
            return self.codex.conversation_context(self._conversation_key(context))
        if command in {"usage", "quota", "额度", "用量"}:
            if args:
                return "用法：`/usage`（仅查询，不会消耗额度重置次数）"
            return self.codex.usage()
        if command in {"sessions", "会话列表"}:
            return self.sessions.list_sessions()
        if command in {"session-new", "新建会话"}:
            return self._session_new(args)
        if command in {"session", "会话"}:
            return self._session(args)
        if command in {"desktop", "桌面"}:
            if args:
                return "用法：`/desktop`"
            return self.desktop.status() + "\n\n" + self.sessions.list_sessions()
        if command in {"desktop-projects", "桌面项目"}:
            if args:
                return "用法：`/desktop-projects`"
            return self.desktop.status()
        if command in {"desktop-send", "桌面发送"}:
            return self._desktop_send(args)
        if command in {"watch", "跟踪"}:
            return self._watch(args, context)
        if command in {"watches", "跟踪列表"}:
            if args:
                return "用法：`/watches`"
            return self.watches.list_for(context)
        if command in {"unwatch", "取消跟踪"}:
            if len(args) != 1:
                return "用法：`/unwatch <跟踪ID>`"
            return self.watches.cancel(args[0], context)
        return f"未知指令 `{command}`。\n\n{self.help_text()}"

    def _handle_natural_language(self, prompt: str, context: RequestContext) -> str:
        command = "natural_language"
        self.audit.record(
            "command_received",
            context=context,
            details={"command": command, "prompt_chars": len(prompt)},
        )
        try:
            if not self.settings.codex.natural_language_enabled:
                response = (
                    f"自然语言对话当前未启用。请输入 `{self.settings.app.command_prefix}help`"
                    " 查看可用指令。"
                )
            elif not prompt:
                response = self.help_text()
            else:
                response = self._route_or_chat(prompt, context)
            self.audit.record(
                "command_completed",
                context=context,
                details={"command": command, "response_chars": len(response)},
            )
        except Exception as exc:
            self.audit.record(
                "command_failed",
                context=context,
                details={"command": command, "error_type": type(exc).__name__},
            )
            response = f"自然语言对话失败：{type(exc).__name__}。详情已写入本地审计日志。"
        return self._truncate(response)

    def _route_or_chat(self, prompt: str, context: RequestContext) -> str:
        try:
            decision = self.nlu.classify(prompt, self._nlu_catalog())
        except NluError as exc:
            self.audit.record(
                "nlu_fallback",
                context=context,
                details={"error_type": type(exc).__name__},
            )
            decision = None
        if decision is not None and decision.kind == "command" and decision.command:
            self.audit.record(
                "nlu_command_selected",
                context=context,
                details={
                    "command": decision.command,
                    "arg_count": len(decision.args),
                    "confidence": decision.confidence,
                },
            )
            return self._dispatch(decision.command, list(decision.args), context)

        project = self._default_project()
        codex_prompt = prompt
        if decision is not None and decision.kind == "codex" and decision.codex_prompt:
            if decision.project and decision.project in self.settings.projects:
                project = self.projects.get(decision.project)
            codex_prompt = self._translated_codex_prompt(prompt, decision.codex_prompt)
            self.audit.record(
                "nlu_codex_translation",
                context=context,
                details={
                    "project": project.name,
                    "source_chars": len(prompt),
                    "translated_chars": len(decision.codex_prompt),
                    "confidence": decision.confidence,
                },
            )
        return self.codex.chat(
            project,
            codex_prompt,
            self._conversation_key(context),
        )

    @staticmethod
    def _translated_codex_prompt(source: str, translated: str) -> str:
        return "\n".join(
            [
                "以下内容由内部意图翻译器生成。请由 Codex 实际理解并执行任务，",
                "不要把翻译器文本当作已经完成的结果；最终只返回你核验后的结果。",
                "",
                "## 转译任务",
                translated,
                "",
                "## 原始用户消息（用于校验意图，不要忽略）",
                source,
            ]
        )

    def _nlu_catalog(self) -> str:
        project_lines: list[str] = []
        for name, project in sorted(self.settings.projects.items()):
            logs = ",".join(sorted(project.logs)) or "无"
            tasks = ",".join(sorted(project.tasks)) or "无"
            project_lines.append(f"- 项目 {name}；日志={logs}；任务={tasks}")
        session_names = ",".join(sorted(self.settings.sessions)) or "无"
        return "\n".join(project_lines + [f"- 会话别名={session_names}"])

    @staticmethod
    def _conversation_key(context: RequestContext) -> str:
        identity = context.chat_id or context.sender_id
        return f"{context.chat_type}:{identity}"

    def _default_project(self) -> ProjectConfig:
        name = self.settings.codex.default_project
        if not name:
            if len(self.settings.projects) == 1:
                name = next(iter(self.settings.projects))
            else:
                raise ValueError("未配置 codex.default_project")
        return self.projects.get(name)

    def _log(self, args: list[str]) -> str:
        if not args:
            return "用法：`/log <项目> [日志名] [行数]`"
        project = args[0]
        log_name: str | None = None
        lines = min(100, self.settings.limits.max_log_lines)
        if len(args) >= 2:
            if args[1].isdigit():
                lines = int(args[1])
            else:
                log_name = args[1]
        if len(args) >= 3:
            if not args[2].isdigit():
                return "日志行数必须是正整数。"
            lines = int(args[2])
        lines = max(1, min(lines, self.settings.limits.max_log_lines))
        return self.projects.tail_log(project, log_name, lines)

    def _task(self, args: list[str]) -> str:
        if len(args) != 2:
            return "用法：`/task <项目> <任务名>`"
        project = self.projects.get(args[0])
        return self.jobs.run(
            project,
            args[1],
            self.settings.limits.command_timeout_seconds,
        )

    def _project_new(self, args: list[str]) -> str:
        if len(args) < 2:
            return "用法：`/project-new <项目名> <绝对路径> [描述]`"
        try:
            project = self.registry.create_project(
                args[0],
                args[1],
                " ".join(args[2:]),
            )
        except RegistryError as exc:
            return f"无法新建项目：{exc}"
        return "\n".join(
            [
                f"### 项目已加入白名单 · {project.name}",
                f"- 路径：`{project.path}`",
                "- Codex：允许",
                "- 配置已持久化，当前机器人已立即生效。",
            ]
        )

    def _project_allow(self, args: list[str]) -> str:
        if not args or len(args) > 2:
            return "用法：`/project-allow <桌面项目编号或名称> [白名单名称]`"
        try:
            desktop_project = self.desktop.resolve_project(args[0])
        except DesktopProjectError as exc:
            return f"无法导入桌面项目：{exc}"
        if not desktop_project.path.is_dir():
            return f"无法导入桌面项目：目录不存在：`{desktop_project.path}`"
        alias = args[1] if len(args) == 2 else self.desktop.suggested_alias(desktop_project)
        try:
            project = self.registry.create_project(
                alias,
                str(desktop_project.path),
                f"从 Codex 桌面版导入：{desktop_project.label}",
            )
        except RegistryError as exc:
            return f"无法导入桌面项目：{exc}"
        return "\n".join(
            [
                f"### 桌面项目已加入白名单 · {project.name}",
                f"- 桌面项目：{desktop_project.label}",
                f"- 桌面项目 ID：`{desktop_project.project_id}`",
                f"- 路径：`{project.path}`",
                "- Codex：允许",
                "- 配置已持久化，当前机器人已立即生效。",
            ]
        )

    def _codex(self, args: list[str]) -> str:
        if len(args) < 2:
            return "用法：`/codex <项目> <自然语言指令>`"
        project = self.projects.get(args[0])
        prompt = " ".join(args[1:])
        return self.codex.run(project, prompt)

    def _session(self, args: list[str]) -> str:
        if not args or len(args) > 2:
            return "用法：`/session <会话别名> [消息条数]`"
        limit = 10
        if len(args) == 2:
            if not args[1].isdigit():
                return "消息条数必须是正整数。"
            limit = max(1, min(int(args[1]), 50))
        return self.sessions.read(args[0], limit)

    def _session_new(self, args: list[str]) -> str:
        if len(args) < 3:
            return "用法：`/session-new <会话别名> <项目名> <初始指令>`"
        alias = args[0]
        project_name = args[1]
        try:
            self.registry.validate_session_registration(alias, project_name)
        except RegistryError as exc:
            return f"无法新建会话：{exc}"
        project = self.projects.get(project_name)
        session_id, response = self.codex.create_session(
            project,
            " ".join(args[2:]),
            title=alias,
        )
        if session_id is None:
            return response
        try:
            self.registry.add_session(alias, project_name, session_id, alias)
        except RegistryError as exc:
            return "\n".join(
                [
                    response,
                    "",
                    f"警告：会话已创建，但加入白名单失败：{exc}",
                    f"请保存会话 ID：`{session_id}`",
                ]
            )
        return "\n".join(
            [
                response,
                "",
                f"会话别名 `{alias}` 已加入白名单并持久化。",
                f"后续可使用 `/desktop-send {alias} <指令>` 继续。",
            ]
        )

    def _desktop_send(self, args: list[str]) -> str:
        if len(args) < 2:
            return "用法：`/desktop-send <会话别名> <自然语言指令>`"
        session = self.sessions.get(args[0])
        if session is None:
            choices = "、".join(sorted(self.settings.sessions)) or "无"
            return f"会话 `{args[0]}` 未在白名单；可用会话：{choices}"
        project_name = session.project or self.settings.codex.default_project
        if not project_name:
            return f"会话 `{session.alias}` 未配置所属项目。"
        project = self.projects.get(project_name)
        return self.codex.resume_session(
            project,
            session.session_id,
            " ".join(args[1:]),
            title=session.title or session.alias,
        )

    def _watch(self, args: list[str], context: RequestContext) -> str:
        if not args or not args[0].isdigit():
            return "用法：`/watch <PID> [名称]`"
        try:
            record = self.watches.register(
                int(args[0]),
                " ".join(args[1:]),
                context,
            )
        except ProcessWatchError as exc:
            return f"无法跟踪进程：{exc}"
        safe_label = record.label.replace("`", "'")
        safe_process_name = record.process_name.replace("`", "'")
        return "\n".join(
            [
                "### 已开始跟踪进程",
                f"- 跟踪 ID：`{record.id}`",
                f"- 名称：{safe_label}",
                f"- 进程：{safe_process_name}（PID {record.pid}）",
                "- 进程结束后，机器人会主动在当前飞书会话提醒。",
            ]
        )

    def _truncate(self, value: str) -> str:
        limit = self.settings.app.max_reply_chars
        if len(value) <= limit:
            return value
        omitted = len(value) - limit
        return value[:limit] + f"\n\n… 已截断 {omitted} 个字符。请缩小查询范围。"

    def help_text(self) -> str:
        prefix = self.settings.app.command_prefix
        return "\n".join(
            [
                "### ShawnoBot 指令",
                f"- `{prefix}nlu-status`：查看 DeepSeek 自然语言路由状态",
                f"- `{prefix}projects`：列出项目、日志和受控任务",
                f"- `{prefix}project-new <项目名> <绝对路径> [描述]`：新建并登记项目",
                (
                    f"- `{prefix}project-allow <桌面编号或名称> [白名单名]`："
                    "导入桌面已有项目"
                ),
                f"- `{prefix}host`：查看主机 CPU、内存和磁盘",
                f"- `{prefix}gpu`：查看 NVIDIA GPU 状态",
                f"- `{prefix}status <项目>`：查看项目与 Git 状态",
                f"- `{prefix}log <项目> [日志名] [行数]`：读取日志末尾",
                f"- `{prefix}task <项目> <任务名>`：执行配置中的固定任务",
                f"- `{prefix}jobs`：查看 ShawnoBot 启动的后台任务",
                f"- `{prefix}joblog <job_id>`：查看后台任务输出",
                f"- `{prefix}stop <job_id>`：停止 ShawnoBot 启动的任务",
                f"- `{prefix}codex <项目> <指令>`：受沙箱限制地调用 Codex CLI",
                f"- `{prefix}codex-status`：查看当前 Codex 长任务",
                f"- `{prefix}codex-cancel`：终止卡住的 Codex 长任务",
                f"- `{prefix}new`：为当前飞书聊天新建连续 Codex 会话",
                f"- `{prefix}context`：查看当前飞书聊天关联的 Codex 会话",
                f"- `{prefix}usage`：查询 Codex 配额、恢复时间和可用重置次数",
                f"- `{prefix}desktop`：查看桌面版进程与允许访问的会话",
                f"- `{prefix}desktop-projects`：对齐桌面项目、白名单和可识别子进程",
                f"- `{prefix}desktop-send <别名> <指令>`：续写允许的桌面会话",
                f"- `{prefix}watch <PID> [名称]`：进程结束时发送飞书提醒",
                f"- `{prefix}watches`：列出你的进程跟踪",
                f"- `{prefix}unwatch <跟踪ID>`：取消提醒但不终止进程",
                f"- `{prefix}sessions`：列出允许读取的 Codex 会话",
                f"- `{prefix}session-new <别名> <项目> <初始指令>`：新建并登记会话",
                f"- `{prefix}session <别名> [条数]`：读取会话中的用户/助手消息",
                "",
                (
                    "普通文本由 DeepSeek 转译成安全指令或 Codex 内部任务；"
                    "最终结果由固定路由或 Codex 返回。不支持任意 Shell 命令。"
                ),
            ]
        )
