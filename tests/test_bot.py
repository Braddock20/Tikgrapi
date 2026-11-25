"""The bot layer: command parsing, dispatch rules, safety gates."""
from __future__ import annotations

import pytest

from app.bot.commands import COMMANDS, WRITE_COMMANDS, resolve
from app.bot.dispatcher import Dispatcher, InboundMessage


class FakeEngine:
    """Stands in for the real engine — no network, no session."""

    def __init__(self, allow_writes: bool = True) -> None:
        self.allow_writes = allow_writes
        self.calls: list[tuple[str, dict]] = []
        self.paused = False

    async def call(self, operation: str, params: dict) -> dict:
        self.calls.append((operation, params))
        if operation == "me":
            return {"account": {"unique_id": "auren_and_i", "nickname": "auren_and_i",
                                "id": "1", "sec_uid": "sec"}}
        if operation == "profile":
            return {"profile": {"unique_id": params["username"], "id": "9",
                                "sec_uid": "sec9", "nickname": params["username"]},
                    "stats": {"followerCount": 5}}
        if operation == "comments":
            return {"comments": [{"cid": "c1", "text": "hi", "author": "someone"}]}
        if operation == "user_posts":
            return {"videos": [{"id": "v1", "desc": "a video"}]}
        if operation == "followers" or operation == "following":
            return {"users": [{"unique_id": "u1", "id": "2"}], "total": 1}
        if operation == "comment":
            return {"comment_id": "new1", "comment": {"status_code": 0}}
        return {"ok": True, "raw": {}}

    async def writes_allowed(self) -> bool:
        return self.allow_writes and not self.paused

    async def set_paused(self, paused: bool) -> None:
        self.paused = paused

    async def session_meta(self) -> dict:
        return {"status": "valid", "detail": "probe"}


def msg(text: str, mid: str = "m1", author: str = "me") -> InboundMessage:
    return InboundMessage(id=mid, text=text, author_id=author, author_name="me")


# -- command registry ------------------------------------------------------
def test_every_command_has_usage_and_a_summary():
    for command in COMMANDS.values():
        assert command.usage.startswith("/")
        assert command.summary


def test_commands_resolve_with_and_without_the_slash():
    assert resolve("help").name == "help"
    assert resolve("/help").name == "help"
    assert resolve("whoami").name == "whoami"
    assert resolve("me").name == "whoami"  # alias


def test_unknown_commands_do_not_resolve():
    assert resolve("definitely_not_a_command") is None


def test_write_commands_are_flagged():
    assert {"like", "unlike", "comment", "follow", "unfollow"} <= WRITE_COMMANDS
    assert "help" not in WRITE_COMMANDS


# -- dispatch --------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_command_is_run_and_answered():
    engine = FakeEngine()
    dispatcher = Dispatcher(engine)
    reply = await dispatcher.handle(msg("/whoami"))
    assert reply and "auren_and_i" in reply
    assert engine.calls[0][0] == "me"


@pytest.mark.asyncio
async def test_plain_text_is_ignored():
    dispatcher = Dispatcher(FakeEngine())
    assert await dispatcher.handle(msg("hello there")) is None


@pytest.mark.asyncio
async def test_an_unknown_command_is_ignored():
    dispatcher = Dispatcher(FakeEngine())
    assert await dispatcher.handle(msg("/nonsense")) is None


@pytest.mark.asyncio
async def test_the_same_message_is_only_handled_once():
    engine = FakeEngine()
    dispatcher = Dispatcher(engine)
    await dispatcher.handle(msg("/whoami", mid="same"))
    await dispatcher.handle(msg("/whoami", mid="same"))
    assert len(engine.calls) == 1


@pytest.mark.asyncio
async def test_write_commands_are_refused_when_writes_are_off():
    engine = FakeEngine(allow_writes=False)
    dispatcher = Dispatcher(engine)
    reply = await dispatcher.handle(msg("/like 123"))
    assert reply and "disabled" in reply.lower()
    assert engine.calls == []


@pytest.mark.asyncio
async def test_write_commands_are_refused_while_paused():
    engine = FakeEngine()
    engine.paused = True
    dispatcher = Dispatcher(engine)
    reply = await dispatcher.handle(msg("/like 123"))
    assert reply and "disabled" in reply.lower()


@pytest.mark.asyncio
async def test_other_authors_are_ignored_when_an_allowlist_is_set():
    engine = FakeEngine()
    dispatcher = Dispatcher(engine, allowed_authors={"owner"})
    assert await dispatcher.handle(msg("/whoami", mid="m-stranger", author="stranger")) is None
    assert await dispatcher.handle(msg("/whoami", mid="m-owner", author="owner")) is not None


@pytest.mark.asyncio
async def test_errors_are_reported_not_raised():
    class Boom(FakeEngine):
        async def call(self, operation, params):
            raise RuntimeError("upstream exploded")

    dispatcher = Dispatcher(Boom())
    reply = await dispatcher.handle(msg("/whoami"))
    assert reply and "failed" in reply


@pytest.mark.asyncio
async def test_help_lists_the_commands():
    dispatcher = Dispatcher(FakeEngine())
    reply = await dispatcher.handle(msg("/help"))
    assert reply and "/profile" in reply and "/like" in reply
