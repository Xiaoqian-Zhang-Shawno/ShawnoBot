from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from shawnobot.models import RequestContext
from shawnobot.services.watches import ProcessWatchService


def test_registers_persists_and_cancels_process_watch(tmp_path: Path) -> None:
    service = ProcessWatchService(tmp_path)
    context = RequestContext("ou_owner", "oc_private", "p2p")

    record = service.register(os.getpid(), "测试进程", context)

    assert service.get(record.id) == record
    assert "测试进程" in service.list_for(context)
    reloaded = ProcessWatchService(tmp_path)
    assert reloaded.get(record.id) == record
    assert "已取消" in reloaded.cancel(record.id, context)
    assert reloaded.pending() == []


def test_detects_when_watched_process_finishes(tmp_path: Path) -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(0.15)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
    )
    service = ProcessWatchService(tmp_path)
    context = RequestContext("ou_owner", "oc_private", "p2p")
    record = service.register(process.pid, "短任务", context)

    process.wait(timeout=5)
    deadline = time.monotonic() + 2
    running = True
    state = ""
    while running and time.monotonic() < deadline:
        running, state = service.is_running(record)
        if running:
            time.sleep(0.02)

    assert not running
    assert "结束" in state
