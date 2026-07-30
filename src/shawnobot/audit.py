from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from .models import RequestContext, utc_now_iso


class AuditLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(
        self,
        event: str,
        *,
        context: RequestContext | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            "timestamp": utc_now_iso(),
            "event": event,
            "details": details or {},
        }
        if context is not None:
            entry["actor"] = {
                "sender_id": context.sender_id,
                "chat_id": context.chat_id,
                "chat_type": context.chat_type,
                "message_id": context.message_id,
            }
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
