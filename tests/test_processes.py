from __future__ import annotations

import sys
from pathlib import Path

from shawnobot.processes import run_command


def test_argument_is_not_interpreted_by_shell(tmp_path: Path) -> None:
    marker = tmp_path / "should_not_exist"
    payload = f"; touch {marker}"
    result = run_command(
        [sys.executable, "-c", "import sys; print(sys.argv[1])", payload],
        cwd=tmp_path,
        timeout_seconds=10,
    )
    assert result.return_code == 0
    assert payload in result.stdout
    assert not marker.exists()


def test_timeout_terminates_process(tmp_path: Path) -> None:
    result = run_command(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        cwd=tmp_path,
        timeout_seconds=1,
    )
    assert result.timed_out


def test_run_command_calls_process_lifecycle_hooks(tmp_path: Path) -> None:
    events: list[tuple[str, int]] = []
    result = run_command(
        [sys.executable, "-c", "print('ok')"],
        cwd=tmp_path,
        timeout_seconds=5,
        on_start=lambda process: events.append(("start", process.pid)),
        on_finish=lambda process: events.append(("finish", process.pid)),
    )

    assert result.return_code == 0
    assert [name for name, _ in events] == ["start", "finish"]
    assert events[0][1] == events[1][1]
