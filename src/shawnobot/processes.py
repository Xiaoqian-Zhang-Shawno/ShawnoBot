from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import psutil


@dataclass(frozen=True)
class CommandResult:
    return_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


def _terminate_tree(process: subprocess.Popen[str]) -> None:
    try:
        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
        for child in children:
            child.terminate()
        parent.terminate()
        _, alive = psutil.wait_procs([*children, parent], timeout=3)
        for item in alive:
            item.kill()
    except psutil.Error:
        process.kill()


def run_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: int,
    input_text: str | None = None,
    env: Mapping[str, str] | None = None,
    on_start: Callable[[subprocess.Popen[str]], None] | None = None,
    on_finish: Callable[[subprocess.Popen[str]], None] | None = None,
) -> CommandResult:
    if not argv:
        raise ValueError("argv cannot be empty")
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    creationflags = (
        subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0  # type: ignore[attr-defined]
    )
    process = subprocess.Popen(
        list(argv),
        cwd=str(cwd),
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        env=process_env,
        creationflags=creationflags,
        start_new_session=os.name != "nt",
    )
    try:
        if on_start is not None:
            on_start(process)
        try:
            stdout, stderr = process.communicate(input=input_text, timeout=timeout_seconds)
            return CommandResult(process.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            _terminate_tree(process)
            stdout, stderr = process.communicate()
            return CommandResult(-1, stdout, stderr, timed_out=True)
    finally:
        if on_finish is not None:
            on_finish(process)


def terminate_command(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        _terminate_tree(process)


def start_background(
    argv: Sequence[str],
    *,
    cwd: Path,
    log_path: Path,
    env: Mapping[str, str] | None = None,
) -> tuple[subprocess.Popen[bytes], object]:
    if not argv:
        raise ValueError("argv cannot be empty")
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("ab", buffering=0)
    creationflags = (
        subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0  # type: ignore[attr-defined]
    )
    process = subprocess.Popen(
        list(argv),
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        shell=False,
        env=process_env,
        creationflags=creationflags,
        start_new_session=os.name != "nt",
    )
    return process, log_handle


def terminate_tracked_process(pid: int, expected_create_time: float) -> bool:
    try:
        parent = psutil.Process(pid)
        if abs(parent.create_time() - expected_create_time) > 0.01:
            return False
        children = parent.children(recursive=True)
        for child in children:
            child.terminate()
        parent.terminate()
        _, alive = psutil.wait_procs([*children, parent], timeout=5)
        for item in alive:
            item.kill()
        return True
    except psutil.NoSuchProcess:
        return True
    except psutil.Error:
        return False
