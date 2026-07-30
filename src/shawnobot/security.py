from __future__ import annotations

from dataclasses import dataclass

from .models import FeishuConfig, RequestContext


@dataclass(frozen=True)
class AuthorizationDecision:
    allowed: bool
    reason: str


class Authorizer:
    def __init__(self, config: FeishuConfig) -> None:
        self.config = config

    def decide(self, context: RequestContext) -> AuthorizationDecision:
        if not self.config.allowed_user_ids:
            return AuthorizationDecision(False, "user_allowlist_empty")
        if context.sender_id not in self.config.allowed_user_ids:
            return AuthorizationDecision(False, "sender_not_allowed")
        if context.chat_type != "p2p":
            if not self.config.allowed_chat_ids:
                return AuthorizationDecision(False, "group_allowlist_empty")
            if context.chat_id not in self.config.allowed_chat_ids:
                return AuthorizationDecision(False, "chat_not_allowed")
        return AuthorizationDecision(True, "allowed")
