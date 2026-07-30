from __future__ import annotations

import asyncio
import html
import logging
from contextlib import suppress
from typing import Any

from lark_channel import FeishuChannel, SecurityConfig

from ..audit import AuditLogger
from ..models import RequestContext, Settings
from ..security import Authorizer
from ..services.watches import ProcessWatchService
from .commands import CommandRouter

logger = logging.getLogger(__name__)


def is_long_running_message(text: str, prefix: str) -> bool:
    stripped = text.lstrip()
    if not stripped.startswith(prefix):
        return bool(stripped)
    command = stripped[len(prefix) :].split(maxsplit=1)[0].lower()
    return command in {
        "codex",
        "desktop-send",
        "桌面发送",
        "session-new",
        "新建会话",
        "task",
        "任务",
    }


class FeishuBot:
    def __init__(
        self,
        settings: Settings,
        router: CommandRouter,
        authorizer: Authorizer,
        audit: AuditLogger,
        watches: ProcessWatchService,
    ) -> None:
        self.settings = settings
        self.router = router
        self.authorizer = authorizer
        self.audit = audit
        self.watches = watches
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._watch_tasks: dict[str, asyncio.Task[None]] = {}
        self._watch_poll_seconds = 2.0
        self._progress_notice_seconds = 60.0
        self.channel = FeishuChannel(
            app_id=settings.feishu.app_id,
            app_secret=settings.feishu.app_secret,
            transport="ws",
            security=SecurityConfig(
                mode="strict",
                strict_content_text=True,
                max_ws_fragment_parts=128,
                max_ws_fragment_bytes=8 * 1024 * 1024,
                max_concurrent_ws_handlers=32,
            ),
        )
        self.channel.on("message", self._on_message)
        self.channel.on("error", self._on_error)

    async def _on_message(self, message: Any) -> None:
        if getattr(message, "sender_is_bot", False):
            return
        context = RequestContext(
            sender_id=str(getattr(message, "sender_id", "") or ""),
            chat_id=str(getattr(message, "chat_id", "") or ""),
            chat_type=str(getattr(message, "chat_type", "unknown") or "unknown"),
            message_id=str(getattr(message, "message_id", "") or ""),
        )
        decision = self.authorizer.decide(context)
        if not decision.allowed:
            self.audit.record(
                "authorization_denied",
                context=context,
                details={"reason": decision.reason},
            )
            if context.chat_type == "p2p":
                await self.channel.reply(message, {"text": "当前账号未获授权。"})
            return

        if (
            context.chat_type != "p2p"
            and self.settings.feishu.require_mention_in_group
            and not bool(getattr(message, "mentioned_bot", False))
        ):
            return

        text = html.unescape(
            str(
                getattr(message, "body_text", None)
                or getattr(message, "safe_content_text", None)
                or ""
            )
        ).strip()
        if not text:
            text = f"{self.settings.app.command_prefix}help"
        if is_long_running_message(text, self.settings.app.command_prefix):
            await self.channel.reply(
                message,
                {"text": "已收到，正在理解并处理；如需调用 Codex，完成后会在此回复。"},
            )
            task = asyncio.create_task(self._handle_long_running(message, text, context))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
            return
        response = await asyncio.to_thread(self.router.handle, text, context)
        try:
            await self.channel.reply(message, {"markdown": response})
        finally:
            self._sync_watch_tasks()

    async def _handle_long_running(
        self,
        message: Any,
        text: str,
        context: RequestContext,
    ) -> None:
        try:
            worker = asyncio.create_task(asyncio.to_thread(self.router.handle, text, context))
            try:
                response = await asyncio.wait_for(
                    asyncio.shield(worker),
                    timeout=getattr(self, "_progress_notice_seconds", 60.0),
                )
            except TimeoutError:
                await self.channel.reply(
                    message,
                    {
                        "text": (
                            "任务仍在处理中，并未卡死。你可以继续使用短指令；"
                            "发送 /codex-status 可查看当前 Codex 任务。"
                        )
                    },
                )
                response = await worker
            await self.channel.reply(message, {"markdown": response})
        except Exception as exc:
            logger.exception("Long-running Feishu task failed")
            self.audit.record(
                "long_running_reply_failed",
                context=context,
                details={"error_type": type(exc).__name__, "message": str(exc)[:500]},
            )
            with suppress(Exception):
                await self.channel.reply(
                    message,
                    {"text": "任务处理失败；详情已写入本地审计日志。"},
                )

    def _sync_watch_tasks(self) -> None:
        pending = {record.id: record for record in self.watches.pending()}
        for watch_id, task in list(self._watch_tasks.items()):
            if watch_id not in pending:
                task.cancel()
        for watch_id in pending:
            existing = self._watch_tasks.get(watch_id)
            if existing is not None and not existing.done():
                continue
            task = asyncio.create_task(self._monitor_watch(watch_id))
            self._watch_tasks[watch_id] = task
            task.add_done_callback(
                lambda finished, record_id=watch_id: self._remove_watch_task(
                    record_id,
                    finished,
                )
            )

    def _remove_watch_task(
        self,
        watch_id: str,
        finished: asyncio.Task[None],
    ) -> None:
        if self._watch_tasks.get(watch_id) is finished:
            self._watch_tasks.pop(watch_id, None)

    async def _monitor_watch(self, watch_id: str) -> None:
        while True:
            record = await asyncio.to_thread(self.watches.get, watch_id)
            if record is None:
                return
            running, state = await asyncio.to_thread(self.watches.is_running, record)
            if running:
                await asyncio.sleep(self._watch_poll_seconds)
                continue

            context = RequestContext(
                sender_id=record.sender_id,
                chat_id=record.chat_id,
                chat_type=record.chat_type,
                message_id="",
            )
            try:
                await self.channel.wait_ready(timeout=60)
                label = record.label.replace("`", "'").replace("\n", " ")
                process_name = record.process_name.replace("`", "'").replace("\n", " ")
                result = await self.channel.send(
                    record.chat_id,
                    {
                        "markdown": "\n".join(
                            [
                                "### 进程已完成",
                                f"- 名称：{label}",
                                f"- 进程：{process_name}（PID {record.pid}）",
                                f"- 状态：{state}",
                                f"- 跟踪 ID：`{record.id}`",
                            ]
                        )
                    },
                )
                if bool(getattr(result, "success", False)):
                    await asyncio.to_thread(self.watches.complete, watch_id)
                    self.audit.record(
                        "process_watch_notified",
                        context=context,
                        details={
                            "watch_id": watch_id,
                            "pid": record.pid,
                            "state": state,
                        },
                    )
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Process watch notification failed")
                self.audit.record(
                    "process_watch_notification_failed",
                    context=context,
                    details={
                        "watch_id": watch_id,
                        "error_type": type(exc).__name__,
                    },
                )
            await asyncio.sleep(30)

    async def _on_error(self, error: Any) -> None:
        logger.error("Feishu channel error: %s", error)
        self.audit.record(
            "feishu_channel_error",
            details={"error_type": type(error).__name__, "message": str(error)[:500]},
        )

    async def run(self) -> None:
        self.audit.record("bot_starting", details={"transport": "websocket"})
        self._sync_watch_tasks()
        await self.channel.connect()
