from __future__ import annotations

import re
import sys
from pathlib import Path

from shawnobot.models import ProjectConfig, TaskConfig
from shawnobot.services.jobs import JobManager


def test_background_job_can_be_stopped_by_tracked_id(tmp_path: Path) -> None:
    project = ProjectConfig(
        name="demo",
        path=tmp_path,
        tasks={
            "sleep": TaskConfig(
                argv=(sys.executable, "-c", "import time; time.sleep(30)"),
                background=True,
                timeout_seconds=0,
            )
        },
    )
    manager = JobManager(tmp_path / "data", max_parallel_tasks=1)
    started = manager.run(project, "sleep", default_timeout_seconds=10)
    match = re.search(r"Job ID：`([a-f0-9]+)`", started)
    assert match is not None
    stopped = manager.stop(match.group(1))
    assert "已停止" in stopped
