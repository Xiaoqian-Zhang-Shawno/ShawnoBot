import asyncio
import time
from types import SimpleNamespace

from shawnobot.bot.feishu import FeishuBot, is_long_running_message
from shawnobot.models import ProcessWatchRecord


def test_detects_long_running_feishu_messages() -> None:
    assert is_long_running_message("检查最近训练结果", "/")
    assert is_long_running_message("/codex attention2 分析日志", "/")
    assert is_long_running_message("/desktop-send history 继续", "/")
    assert is_long_running_message("/session-new history demo 初始化", "/")
    assert is_long_running_message("/task attention2 test_main", "/")
    assert not is_long_running_message("/usage", "/")
    assert not is_long_running_message("/help", "/")


def test_long_running_message_releases_event_handler_before_completion() -> None:
    replies: list[dict[str, str]] = []

    class FakeChannel:
        async def reply(self, message, content) -> None:
            replies.append(content)

    class FakeRouter:
        def handle(self, text, context) -> str:
            time.sleep(0.05)
            return "DONE"

    class FakeAuthorizer:
        def decide(self, context):
            return SimpleNamespace(allowed=True, reason="allowed")

    class FakeAudit:
        def record(self, *args, **kwargs) -> None:
            return None

    class FakeWatches:
        def pending(self):
            return []

    async def scenario() -> None:
        bot = object.__new__(FeishuBot)
        bot.settings = SimpleNamespace(
            app=SimpleNamespace(command_prefix="/"),
            feishu=SimpleNamespace(require_mention_in_group=True),
        )
        bot.router = FakeRouter()
        bot.authorizer = FakeAuthorizer()
        bot.audit = FakeAudit()
        bot.watches = FakeWatches()
        bot.channel = FakeChannel()
        bot._background_tasks = set()
        bot._watch_tasks = {}
        message = SimpleNamespace(
            sender_is_bot=False,
            sender_id="ou_owner",
            chat_id="oc_private",
            chat_type="p2p",
            message_id="om_1",
            body_text="检查训练状态",
        )

        await bot._on_message(message)
        assert replies == [
            {"text": "已收到，正在理解并处理；如需调用 Codex，完成后会在此回复。"}
        ]
        tasks = list(bot._background_tasks)
        assert len(tasks) == 1
        await asyncio.gather(*tasks)
        assert replies[-1] == {"markdown": "DONE"}

    asyncio.run(scenario())


def test_long_running_message_sends_progress_notice() -> None:
    replies: list[dict[str, str]] = []

    class FakeChannel:
        async def reply(self, message, content) -> None:
            replies.append(content)

    class FakeRouter:
        def handle(self, text, context) -> str:
            time.sleep(0.04)
            return "DONE"

    async def scenario() -> None:
        bot = object.__new__(FeishuBot)
        bot.router = FakeRouter()
        bot.channel = FakeChannel()
        bot.audit = SimpleNamespace(record=lambda *args, **kwargs: None)
        bot._progress_notice_seconds = 0.01
        message = SimpleNamespace()
        context = SimpleNamespace()

        await bot._handle_long_running(message, "分析训练", context)

    asyncio.run(scenario())
    assert "仍在处理中" in replies[0]["text"]
    assert replies[1] == {"markdown": "DONE"}


def test_process_watch_sends_proactive_completion_notification() -> None:
    sent: list[tuple[str, dict[str, str]]] = []
    record = ProcessWatchRecord(
        "watch123",
        987,
        1.0,
        "python.exe",
        "训练任务",
        "ou_owner",
        "oc_private",
        "p2p",
        "2026-07-30T00:00:00+00:00",
    )

    class FakeChannel:
        async def wait_ready(self, *, timeout=None) -> None:
            return None

        async def send(self, chat_id, content):
            sent.append((chat_id, content))
            return SimpleNamespace(success=True)

    class FakeWatches:
        def __init__(self) -> None:
            self.records = {record.id: record}

        def pending(self):
            return list(self.records.values())

        def get(self, watch_id):
            return self.records.get(watch_id)

        def is_running(self, current):
            return False, "已结束"

        def complete(self, watch_id):
            self.records.pop(watch_id, None)

    class FakeAudit:
        def record(self, *args, **kwargs) -> None:
            return None

    async def scenario() -> None:
        bot = object.__new__(FeishuBot)
        bot.channel = FakeChannel()
        bot.watches = FakeWatches()
        bot.audit = FakeAudit()
        bot._watch_tasks = {}
        bot._watch_poll_seconds = 0.01

        bot._sync_watch_tasks()
        tasks = list(bot._watch_tasks.values())
        await asyncio.gather(*tasks)

        assert sent[0][0] == "oc_private"
        assert "进程已完成" in sent[0][1]["markdown"]
        assert bot.watches.pending() == []

    asyncio.run(scenario())


def test_feishu_unescapes_quoted_command_arguments() -> None:
    received: list[str] = []

    class FakeChannel:
        async def reply(self, message, content) -> None:
            return None

    class FakeRouter:
        def handle(self, text, context) -> str:
            received.append(text)
            return "OK"

    class FakeAuthorizer:
        def decide(self, context):
            return SimpleNamespace(allowed=True, reason="allowed")

    class FakeAudit:
        def record(self, *args, **kwargs) -> None:
            return None

    class FakeWatches:
        def pending(self):
            return []

    async def scenario() -> None:
        bot = object.__new__(FeishuBot)
        bot.settings = SimpleNamespace(
            app=SimpleNamespace(command_prefix="/"),
            feishu=SimpleNamespace(require_mention_in_group=True),
        )
        bot.router = FakeRouter()
        bot.authorizer = FakeAuthorizer()
        bot.audit = FakeAudit()
        bot.watches = FakeWatches()
        bot.channel = FakeChannel()
        bot._background_tasks = set()
        bot._watch_tasks = {}
        message = SimpleNamespace(
            sender_is_bot=False,
            sender_id="ou_owner",
            chat_id="oc_private",
            chat_type="p2p",
            message_id="om_2",
            body_text="/watch 123 &quot;训练任务&quot;",
        )

        await bot._on_message(message)

    asyncio.run(scenario())
    assert received == ['/watch 123 "训练任务"']
