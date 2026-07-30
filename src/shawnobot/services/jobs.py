from __future__ import annotations

import json
import subprocess
import threading
import uuid
from contextlib import suppress
from pathlib import Path

import psutil

from ..models import JobRecord, ProjectConfig, TaskConfig, utc_now_iso
from ..processes import run_command, start_background, terminate_tracked_process


class JobManager:
    def __init__(self, data_dir: Path, max_parallel_tasks: int) -> None:
        self.data_dir = data_dir
        self.log_dir = data_dir / "job_logs"
        self.registry_path = data_dir / "jobs.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._max_parallel_tasks = max_parallel_tasks
        self._active_foreground = 0
        self._foreground_slots = threading.BoundedSemaphore(max_parallel_tasks)
        self._jobs = self._load()
        self._handles: dict[str, object] = {}
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self.refresh()

    def _load(self) -> dict[str, JobRecord]:
        if not self.registry_path.is_file():
            return {}
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
            return {item["id"]: JobRecord.from_dict(item) for item in data}
        except (OSError, ValueError, KeyError, TypeError):
            return {}

    def _save(self) -> None:
        temp = self.registry_path.with_suffix(".tmp")
        payload = [job.to_dict() for job in self._jobs.values()]
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp.replace(self.registry_path)

    def refresh(self) -> None:
        with self._lock:
            changed = False
            for job in self._jobs.values():
                if job.status != "running":
                    continue
                launched = self._processes.get(job.id)
                if launched is not None:
                    return_code = launched.poll()
                    if return_code is not None:
                        job.return_code = return_code
                        job.status = "completed" if return_code == 0 else "failed"
                        job.ended_at = utc_now_iso()
                        handle = self._handles.pop(job.id, None)
                        if handle and hasattr(handle, "close"):
                            handle.close()
                        self._processes.pop(job.id, None)
                        changed = True
                        continue
                try:
                    process = psutil.Process(job.pid)
                    same_process = abs(process.create_time() - job.process_create_time) <= 0.01
                    if not same_process:
                        job.status = "unknown"
                        job.ended_at = utc_now_iso()
                        changed = True
                    elif not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                        job.status = "completed"
                        job.ended_at = utc_now_iso()
                        changed = True
                except psutil.NoSuchProcess:
                    job.status = "completed"
                    job.ended_at = utc_now_iso()
                    changed = True
                except psutil.Error:
                    pass
            if changed:
                self._save()

    def run(
        self,
        project: ProjectConfig,
        task_name: str,
        default_timeout_seconds: int,
    ) -> str:
        if task_name not in project.tasks:
            choices = "、".join(sorted(project.tasks)) or "无"
            return f"未知任务 `{task_name}`；项目 `{project.name}` 的任务：{choices}"
        task = project.tasks[task_name]
        if not project.path.is_dir():
            return f"项目目录不存在：`{project.path}`"
        if task.background:
            return self._start_background(project, task_name, task)
        return self._run_foreground(project, task_name, task, default_timeout_seconds)

    def _run_foreground(
        self,
        project: ProjectConfig,
        task_name: str,
        task: TaskConfig,
        default_timeout_seconds: int,
    ) -> str:
        self.refresh()
        with self._lock:
            background_count = sum(job.status == "running" for job in self._jobs.values())
            if background_count + self._active_foreground >= self._max_parallel_tasks:
                return "任务并发数已达上限，请稍后再试。"
            if not self._foreground_slots.acquire(blocking=False):
                return "任务并发数已达上限，请稍后再试。"
            self._active_foreground += 1
        try:
            timeout = task.timeout_seconds or default_timeout_seconds
            result = run_command(
                task.argv,
                cwd=task.cwd or project.path,
                timeout_seconds=timeout,
                env=task.env,
            )
            if result.timed_out:
                return f"任务 `{project.name}/{task_name}` 超时并已终止。"
            output = (result.stdout + ("\n" + result.stderr if result.stderr else "")).strip()
            state = "成功" if result.return_code == 0 else f"失败（退出码 {result.return_code}）"
            return "\n".join(
                [
                    f"### 任务 · {project.name}/{task_name}",
                    f"- 结果：{state}",
                    "```text",
                    output or "（无输出）",
                    "```",
                ]
            )
        except OSError as exc:
            return f"任务启动失败：{exc}"
        finally:
            with self._lock:
                self._active_foreground -= 1
                self._foreground_slots.release()

    def _start_background(
        self, project: ProjectConfig, task_name: str, task: TaskConfig
    ) -> str:
        self.refresh()
        with self._lock:
            active = sum(job.status == "running" for job in self._jobs.values())
            if active + self._active_foreground >= self._max_parallel_tasks:
                return "后台任务并发数已达上限，请先停止或等待已有任务。"
            job_id = uuid.uuid4().hex[:10]
            log_path = self.log_dir / f"{job_id}.log"
            try:
                process, handle = start_background(
                    task.argv,
                    cwd=task.cwd or project.path,
                    log_path=log_path,
                    env=task.env,
                )
                create_time = psutil.Process(process.pid).create_time()
            except (OSError, psutil.Error) as exc:
                return f"后台任务启动失败：{exc}"
            job = JobRecord(
                id=job_id,
                project=project.name,
                task=task_name,
                pid=process.pid,
                process_create_time=create_time,
                status="running",
                started_at=utc_now_iso(),
                log_path=str(log_path),
            )
            self._jobs[job_id] = job
            self._handles[job_id] = handle
            self._processes[job_id] = process
            self._save()
            return "\n".join(
                [
                    f"后台任务 `{project.name}/{task_name}` 已启动。",
                    f"- Job ID：`{job_id}`",
                    f"- PID：{process.pid}",
                    f"- 输出：`{log_path}`",
                ]
            )

    def list_jobs(self) -> str:
        self.refresh()
        with self._lock:
            if not self._jobs:
                return "尚无 ShawnoBot 任务记录。"
            lines = ["### 任务记录"]
            for job in sorted(self._jobs.values(), key=lambda item: item.started_at, reverse=True)[
                :20
            ]:
                lines.append(
                    f"- `{job.id}` · {job.project}/{job.task} · {job.status} · PID {job.pid}"
                )
            return "\n".join(lines)

    def stop(self, job_id: str) -> str:
        self.refresh()
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return f"未知 Job ID：`{job_id}`"
            if job.status != "running":
                return f"任务 `{job_id}` 当前状态为 `{job.status}`，无需停止。"
            if not terminate_tracked_process(job.pid, job.process_create_time):
                return "拒绝停止：进程身份无法确认，可能发生了 PID 重用。"
            job.status = "stopped"
            job.ended_at = utc_now_iso()
            self._save()
            handle = self._handles.pop(job_id, None)
            if handle and hasattr(handle, "close"):
                handle.close()
            launched = self._processes.pop(job_id, None)
            if launched is not None:
                with suppress(subprocess.TimeoutExpired):
                    launched.wait(timeout=1)
            return f"任务 `{job_id}` 已停止。"

    def job_log(self, job_id: str, lines: int = 100) -> str:
        self.refresh()
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return f"未知 Job ID：`{job_id}`"
            path = Path(job.log_path)
        if not path.is_file():
            return f"任务 `{job_id}` 暂无输出。"
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(
            [
                f"### 任务日志 · {job_id}",
                "```text",
                "\n".join(content[-lines:]) or "（空）",
                "```",
            ]
        )
