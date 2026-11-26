"""The welcome message.

The operator should not have to read a manual to work out how to talk to
their own bot, so the bot says hello on TikTok as soon as it starts — and
``/welcome`` says it again on demand.

Delivery order:

1. **Direct message** — the nicest place for it, and it works as soon as
   TikTok's messaging service answers for this IP.
2. **A comment on the account's own newest video** — always available, and
   it is the channel the bot listens on, so the reply loop closes.

TikTok caps a comment at 1500 characters, so the text is split into
comment-sized pieces before it is sent.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

COMMENT_LIMIT = 1400

COMMANDS_BLOCK = """Commands (type them in this thread):
/help — every command
/help account|videos|people|engagement|discovery|inbox|publishing|automation
/profile @user — a profile
/myvideos — this account's videos
/comments <video_id> — comments on a video
/like <video_id> — like
/comment <video_id> <text> — comment
/follow @user — follow
/search <word> — find people
/explore — the explore page
/notifications — the inbox
/upload <path> — post a video
/status — engine, memory and session
/pause · /resume — stop and start write actions
/cron · /alias · /automate — scheduled and custom commands
/welcome — this message again"""

INTRO_BLOCK = """Hello — your TikTok bot is awake.

Everything runs from here: type a command in this thread and the answer
comes straight back. Replies are read every {interval}s.

The marker is "{marker}" — every command starts with it."""


def build_welcome(engine: Any = None, marker: str = "/", interval: int = 20) -> str:
    """The full welcome text."""
    intro = INTRO_BLOCK.format(marker=marker, interval=interval)
    parts = [intro, COMMANDS_BLOCK]

    if engine is not None:
        try:
            import asyncio

            coro = engine.call("me", {})
            if asyncio.iscoroutine(coro):
                # The caller is async; the text is built without the account
                # line in that case (see build_welcome_async).
                coro.close()
        except Exception:  # noqa: BLE001
            pass
    return "\n\n".join(parts)


async def build_welcome_async(engine: Any, marker: str = "/", interval: int = 20) -> str:
    """The welcome text, with the account's own name if it can be fetched."""
    intro = INTRO_BLOCK.format(marker=marker, interval=interval)
    try:
        result = await engine.call("me", {})
        account = result.get("account") or {}
        if account.get("unique_id"):
            intro = (
                f"Hello @{account['unique_id']} — your TikTok bot is awake."
                + "\n\n"
                + intro.split("\n\n", 1)[1]
            )
    except Exception as exc:  # noqa: BLE001
        log.info("could not read the account for the welcome text: %s", exc)
    return "\n\n".join([intro, COMMANDS_BLOCK])


def split_for_comment(text: str, limit: int = COMMENT_LIMIT) -> list[str]:
    """Split a message into comment-sized pieces, preferring line breaks."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.splitlines():
        # A single line longer than the limit has to be cut on its own.
        while len(line) > limit:
            if current:
                chunks.append("\n".join(current))
                current, size = [], 0
            chunks.append(line[:limit])
            line = line[limit:]
        addition = len(line) + (1 if current else 0)
        if size + addition > limit:
            chunks.append("\n".join(current))
            current, size = [], 0
            addition = len(line)
        current.append(line)
        size += addition
    if current:
        chunks.append("\n".join(current))
    return chunks


async def deliver_welcome(channel: Any, text: str) -> tuple[bool, str]:
    """Send the welcome through a channel, splitting it if necessary.

    When no channel will take it — TikTok refuses state-changing calls
    from some IPs without saying why — the message is written to
    ``data/welcome.txt`` so it is never lost, and the text is returned so
    the caller can show it.

    Returns ``(sent, detail)``.
    """
    if channel is None:
        return False, "no channel is available"

    pieces = split_for_comment(text)
    sent = 0
    last_error = ""
    for piece in pieces:
        try:
            ok = await channel.send(piece)
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
            continue
        if ok:
            sent += 1
        else:
            last_error = "the channel refused the message"

    if sent == len(pieces):
        return True, f"{sent} message(s) sent via {getattr(channel, 'name', 'chat')}"

    saved = save_copy(text)
    reason = last_error or f"only {sent}/{len(pieces)} messages were sent"
    if saved:
        return False, f"{reason} — the message was saved to {saved} instead"
    return False, reason


def save_copy(text: str) -> str:
    """Write the welcome message to disk so it is never lost."""
    try:
        from app.config import settings

        target = Path(settings.media_path).parent / "welcome.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        return str(target)
    except Exception:  # noqa: BLE001
        return ""


__all__ = [
    "build_welcome",
    "build_welcome_async",
    "deliver_welcome",
    "save_copy",
    "split_for_comment",
]
