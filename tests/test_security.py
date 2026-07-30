from shawnobot.models import FeishuConfig, RequestContext
from shawnobot.security import Authorizer


def config(users: set[str], chats: set[str]) -> FeishuConfig:
    return FeishuConfig(
        app_id="",
        app_secret="",
        require_mention_in_group=True,
        allowed_user_ids=frozenset(users),
        allowed_chat_ids=frozenset(chats),
    )


def test_empty_allowlist_denies_everyone() -> None:
    decision = Authorizer(config(set(), set())).decide(
        RequestContext("ou_owner", "oc_lab", "group")
    )
    assert not decision.allowed
    assert decision.reason == "user_allowlist_empty"


def test_p2p_requires_allowed_sender_only() -> None:
    authorizer = Authorizer(config({"ou_owner"}, set()))
    assert authorizer.decide(RequestContext("ou_owner", "private", "p2p")).allowed
    assert not authorizer.decide(RequestContext("ou_other", "private", "p2p")).allowed


def test_group_requires_sender_and_chat() -> None:
    authorizer = Authorizer(config({"ou_owner"}, {"oc_lab"}))
    assert authorizer.decide(RequestContext("ou_owner", "oc_lab", "group")).allowed
    assert not authorizer.decide(RequestContext("ou_owner", "oc_other", "group")).allowed
