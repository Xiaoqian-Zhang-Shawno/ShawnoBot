from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path

import psutil

from ..models import ProcessWatchRecord, RequestContext, utc_now_iso


class ProcessWatchError(ValueError):
    """Raised when a process watch cannot be registered safely."""


class ProcessWatchService:
    def __init__(self, data_dir: Path, *, max_watches: int = 32) -> None:
        self.path = data_dir / "process_watches.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_watches = max_watches
        self._lock = threading.Lock()

    def register(
        self,
        pid: int,
        label: str,
        context: RequestContext,
    ) -> ProcessWatchRecord:
        if pid <= 0:
            raise ProcessWatchError("PID 必须是正整数")
        if not context.chat_id:
            raise ProcessWatchError("当前消息没有可用于提醒的 chat_id")
        clean_label = " ".join(label.split())
        if len(clean_label) > 120:
            raise ProcessWatchError("跟踪名称不能超过 120 个字符")
        try:
            process = psutil.Process(pid)
            create_time = process.create_time()
            process_name = process.name()
            if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                raise ProcessWatchError(f"PID {pid} 已经结束")
        except psutil.NoSuchProcess as exc:
            raise ProcessWatchError(f"未找到 PID {pid}") from exc
        except (psutil.AccessDenied, psutil.ZombieProcess) as exc:
            raise ProcessWatchError(f"无法读取 PID {pid} 的进程信息") from exc

        with self._lock:
            records = self._load()
            if len(records) >= self.max_watches:
                raise ProcessWatchError(f"跟踪数量已达到上限 {self.max_watches}")
            for existing in records.values():
                if (
                    existing.pid == pid
                    and abs(existing.process_create_time - create_time) <= 0.01
                    and existing.sender_id == context.sender_id
                ):
                    raise ProcessWatchError(
                        f"该进程已被跟踪：`{existing.id}`（{existing.label}）"
                    )
            watch_id = uuid.uuid4().hex[:10]
            record = ProcessWatchRecord(
                id=watch_id,
                pid=pid,
                process_create_time=create_time,
                process_name=process_name,
                label=clean_label or process_name,
                sender_id=context.sender_id,
                chat_id=context.chat_id,
                chat_type=context.chat_type,
                created_at=utc_now_iso(),
            )
            records[watch_id] = record
            self._write(records)
        return record

    def list_for(self, context: RequestContext) -> str:
        records = [
            record
            for record in self.pending()
            if record.sender_id == context.sender_id
        ]
        if not records:
            return "当前没有你登记的进程跟踪。"
        lines = ["### 进程跟踪"]
        for record in records:
            running, state = self.is_running(record)
            state_label = "运行中" if running else state
            lines.append(
                f"- `{record.id}` · {record.label} · PID {record.pid} · {state_label}"
            )
        return "\n".join(lines)

    def cancel(self, watch_id: str, context: RequestContext) -> str:
        with self._lock:
            records = self._load()
            record = records.get(watch_id)
            if record is None or record.sender_id != context.sender_id:
                return f"未找到属于你的进程跟踪 `{watch_id}`。"
            records.pop(watch_id)
            self._write(records)
        return f"已取消进程跟踪 `{watch_id}`；不会终止 PID {record.pid}。"

    def pending(self) -> list[ProcessWatchRecord]:
        with self._lock:
            return list(self._load().values())

    def get(self, watch_id: str) -> ProcessWatchRecord | None:
        with self._lock:
            return self._load().get(watch_id)

    def complete(self, watch_id: str) -> None:
        with self._lock:
            records = self._load()
            if records.pop(watch_id, None) is not None:
                self._write(records)

    @staticmethod
    def is_running(record: ProcessWatchRecord) -> tuple[bool, str]:
        try:
            process = psutil.Process(record.pid)
            if abs(process.create_time() - record.process_create_time) > 0.01:
                return False, "原进程已结束（PID 已被复用）"
            if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                return False, "已结束"
            return True, "运行中"
        except psutil.NoSuchProcess:
            return False, "已结束"
        except psutil.ZombieProcess:
            return False, "已结束"
        except psutil.AccessDenied:
            return True, "无权读取，但进程仍存在"

    def _load(self) -> dict[str, ProcessWatchRecord]:
        if not self.path.is_file():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, list):
            return {}
        records: dict[str, ProcessWatchRecord] = {}
        for value in raw:
            if not isinstance(value, dict):
                continue
            try:
                record = ProcessWatchRecord.from_dict(value)
            except (KeyError, TypeError, ValueError):
                continue
            records[record.id] = record
        return records

    def _write(self, records: dict[str, ProcessWatchRecord]) -> None:
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                [record.to_dict() for record in records.values()],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
