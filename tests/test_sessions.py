from __future__ import annotations

import json
from pathlib import Path

from shawnobot.models import SessionConfig
from shawnobot.services.sessions import SessionService


def write_item(handle: object, value: dict[str, object]) -> None:
    handle.write(json.dumps(value, ensure_ascii=False) + "\n")  # type: ignore[attr-defined]


def test_reads_only_allowlisted_user_and_assistant_messages(tmp_path: Path) -> None:
    session_id = "11111111-1111-4111-8111-111111111111"
    folder = tmp_path / "sessions" / "2026" / "07" / "28"
    folder.mkdir(parents=True)
    rollout = folder / f"rollout-test-{session_id}.jsonl"
    with rollout.open("w", encoding="utf-8") as handle:
        write_item(
            handle,
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "secret instruction"}],
                },
            },
        )
        write_item(
            handle,
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "检查训练状态"}],
                },
            },
        )
        write_item(
            handle,
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "训练正常"}],
                },
            },
        )
        write_item(
            handle,
            {
                "type": "response_item",
                "payload": {
                    "type": "reasoning",
                    "summary": [{"text": "private reasoning"}],
                },
            },
        )

    service = SessionService(
        {
            "history": SessionConfig(
                alias="history",
                session_id=session_id,
                title="历史",
            )
        },
        codex_home=tmp_path,
    )
    result = service.read("history", 10)
    assert "检查训练状态" in result
    assert "训练正常" in result
    assert "secret instruction" not in result
    assert "private reasoning" not in result


def test_rejects_unlisted_session_alias(tmp_path: Path) -> None:
    service = SessionService({}, codex_home=tmp_path)
    assert "未在白名单" in service.read("unknown")
