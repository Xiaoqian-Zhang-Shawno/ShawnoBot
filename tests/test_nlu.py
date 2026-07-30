from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from shawnobot.models import NluConfig
from shawnobot.services.nlu import NluError, NluService


def response_for(value: dict[str, object]) -> bytes:
    return json.dumps(
        {"choices": [{"message": {"content": json.dumps(value)}}]}
    ).encode()


def test_deepseek_nlu_returns_allowlisted_structured_command() -> None:
    captured: dict[str, object] = {}

    def requester(request: urllib.request.Request, timeout: float) -> bytes:
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data.decode())  # type: ignore[union-attr]
        return response_for(
            {
                "kind": "command",
                "command": "status",
                "args": ["attention2"],
                "confidence": 0.96,
            }
        )

    service = NluService(
        NluConfig(
            enabled=True,
            api_key="test-key",
            model="deepseek-v4-pro",
            allowed_commands=frozenset({"status"}),
        ),
        requester=requester,
    )

    decision = service.classify("看看 attention2 的状态", "- 项目 attention2")

    assert decision.kind == "command"
    assert decision.command == "status"
    assert decision.args == ("attention2",)
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["response_format"] == {"type": "json_object"}
    system_prompt = payload["messages"][0]["content"]
    assert "绝不会直接展示给用户" in system_prompt
    assert '"kind":"codex"' in system_prompt


def test_deepseek_nlu_translates_conversation_into_internal_codex_task() -> None:
    service = NluService(
        NluConfig(enabled=True, api_key="test-key"),
        requester=lambda request, timeout: response_for(
            {
                "kind": "codex",
                "project": "attention2",
                "prompt": "检查第五折训练结果并解释差异，不要修改文件。",
                "confidence": 0.91,
            }
        ),
    )

    decision = service.classify("第五折为什么比较差", "- 项目 attention2")

    assert decision.kind == "codex"
    assert decision.project == "attention2"
    assert decision.codex_prompt == "检查第五折训练结果并解释差异，不要修改文件。"
    assert decision.confidence == 0.91


def test_nlu_rejects_disallowed_or_low_confidence_command() -> None:
    responses = iter(
        [
            response_for(
                {
                    "kind": "command",
                    "command": "stop",
                    "args": ["job1"],
                    "confidence": 0.99,
                }
            ),
            response_for(
                {
                    "kind": "command",
                    "command": "gpu",
                    "args": [],
                    "confidence": 0.5,
                }
            ),
        ]
    )
    service = NluService(
        NluConfig(
            enabled=True,
            api_key="test-key",
            min_confidence=0.85,
            allowed_commands=frozenset({"gpu"}),
        ),
        requester=lambda request, timeout: next(responses),
    )

    with pytest.raises(NluError, match="未授权"):
        service.classify("停止任务", "无")
    with pytest.raises(NluError, match="置信度不足"):
        service.classify("看看 GPU", "无")


def test_nlu_failure_is_reported_without_exposing_provider_body() -> None:
    service = NluService(
        NluConfig(enabled=True, api_key="test-key"),
        requester=lambda request, timeout: b"not-json",
    )

    with pytest.raises(NluError, match="请求失败"):
        service.classify("查看状态", "无")

    assert "JSONDecodeError" in service.status()


def test_nlu_status_records_http_code_without_response_body() -> None:
    def requester(request: urllib.request.Request, timeout: float) -> bytes:
        raise urllib.error.HTTPError(request.full_url, 401, "secret body", {}, None)

    service = NluService(
        NluConfig(enabled=True, api_key="test-key"),
        requester=requester,
    )

    with pytest.raises(NluError):
        service.classify("查看状态", "无")

    status = service.status()
    assert "HTTP 401" in status
    assert "secret body" not in status
