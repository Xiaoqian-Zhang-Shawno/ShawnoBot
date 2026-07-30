from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from ..models import SessionConfig

_SESSION_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_HIDDEN_USER_PREFIXES = (
    "<environment_context>",
    "<permissions instructions>",
    "<recommended_plugins>",
)


class SessionService:
    def __init__(
        self,
        sessions: dict[str, SessionConfig],
        *,
        codex_home: Path | None = None,
    ) -> None:
        self.sessions = sessions
        configured_home = os.environ.get("CODEX_HOME")
        self.codex_home = (
            codex_home
            or (Path(configured_home).expanduser() if configured_home else Path.home() / ".codex")
        ).resolve()

    def list_sessions(self) -> str:
        if not self.sessions:
            return "尚未配置允许读取的 Codex 会话。"
        lines = ["### 允许读取的 Codex 会话"]
        for alias, session in sorted(self.sessions.copy().items()):
            title = f" · {session.title}" if session.title else ""
            state = "已找到" if self._find_rollout(session.session_id) else "本机未找到"
            lines.append(f"- `{alias}`{title} · {state}")
        return "\n".join(lines)

    def get(self, alias: str) -> SessionConfig | None:
        return self.sessions.get(alias)

    def read(self, alias: str, message_limit: int = 10) -> str:
        session = self.sessions.get(alias)
        if not session:
            choices = "、".join(sorted(self.sessions)) or "无"
            return f"会话 `{alias}` 未在白名单；可用会话：{choices}"
        path = self._find_rollout(session.session_id)
        if path is None:
            return f"本机未找到会话 `{alias}` 的记录。"
        messages = self._read_messages(path)
        if not messages:
            return f"会话 `{alias}` 中没有可显示的用户/助手消息。"
        selected = messages[-max(1, min(message_limit, 50)) :]
        title = session.title or alias
        lines = [f"### Codex 会话 · {title}", f"- ID：`{session.session_id}`"]
        for role, text in selected:
            label = "用户" if role == "user" else "Codex"
            safe_text = text.strip()
            if len(safe_text) > 1400:
                safe_text = safe_text[:1400] + "\n…（单条消息已截断）"
            lines.extend(["", f"**{label}**", safe_text])
        return "\n".join(lines)

    def _find_rollout(self, session_id: str) -> Path | None:
        if not _SESSION_ID_RE.fullmatch(session_id):
            return None
        sessions_root = self.codex_home / "sessions"
        if not sessions_root.is_dir():
            return None
        matches = list(sessions_root.rglob(f"*{session_id}.jsonl"))
        return matches[0] if len(matches) == 1 else None

    def _read_messages(self, path: Path) -> list[tuple[str, str]]:
        messages: list[tuple[str, str]] = []
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    item: dict[str, Any] = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if item.get("type") != "response_item":
                    continue
                payload = item.get("payload")
                if not isinstance(payload, dict) or payload.get("type") != "message":
                    continue
                role = payload.get("role")
                if role not in {"user", "assistant"}:
                    continue
                content = payload.get("content")
                if not isinstance(content, list):
                    continue
                parts: list[str] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") not in {"input_text", "output_text"}:
                        continue
                    text = block.get("text")
                    if not isinstance(text, str) or not text.strip():
                        continue
                    if role == "user" and text.lstrip().startswith(_HIDDEN_USER_PREFIXES):
                        continue
                    parts.append(text.strip())
                if parts:
                    messages.append((role, "\n\n".join(parts)))
        return messages
