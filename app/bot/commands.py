"""The command set the bot understands.

A command is a thin shell over an engine operation: it parses the arguments,
calls the operation, and formats the answer for a chat message. Keeping the
mapping here (rather than in the transport) means the same commands work
whether they arrive over DM, a comment, or the HTTP API.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

Handler = Callable[["CommandContext", list[str]], Awaitable[str]]


@dataclass
class CommandContext:
    """What a handler needs: the engine, and who is talking."""

    engine: Any
    author_id: str = ""
    author_name: str = ""
    allow_write: bool = False


@dataclass
class Command:
    name: str
    usage: str
    summary: str
    handler: Handler
    write: bool = False
    aliases: tuple[str, ...] = field(default_factory=tuple)

    def matches(self, token: str) -> bool:
        return token == self.name or token in self.aliases


# --------------------------------------------------------------------------
# formatting helpers
# --------------------------------------------------------------------------
def _fmt_user(user: dict) -> str:
    if not user:
        return "no data"
    stats = []
    if user.get("follower_count") is not None:
        stats.append(f"followers {user['follower_count']}")
    if user.get("following_count") is not None:
        stats.append(f"following {user['following_count']}")
    if user.get("like_count") is not None:
        stats.append(f"likes {user['like_count']}")
    if user.get("video_count") is not None:
        stats.append(f"videos {user['video_count']}")
    return (
        f"@{user.get('unique_id') or '?'} — {user.get('nickname') or '?'}\n"
        f"id {user.get('id') or '?'}\n"
        f"stats: {', '.join(stats) if stats else 'n/a'}\n"
        f"bio: {(user.get('signature') or '').strip() or '(none)'}"
    )


def _fmt_video(video: dict) -> str:
    return (
        f"video {video.get('id')}\n"
        f"{video.get('desc') or '(no caption)'}\n"
        f"likes {video.get('like_count')}, comments {video.get('comment_count')}, "
        f"plays {video.get('play_count')}"
    )


def _fmt_comment(comment: dict) -> str:
    return f"@{comment.get('author') or '?'}: {comment.get('text') or ''}"


# --------------------------------------------------------------------------
# handlers
# --------------------------------------------------------------------------
async def _whoami(ctx: CommandContext, args: list[str]) -> str:
    result = await ctx.engine.call("me", {})
    account = result.get("account", {})
    return (
        f"@{account.get('unique_id')} ({account.get('nickname')})\n"
        f"id {account.get('id')}\n"
        f"secUid {account.get('sec_uid')}"
    )


async def _health(ctx: CommandContext, args: list[str]) -> str:
    from app.config import settings

    meta = await ctx.engine.session_meta()
    status = (meta or {}).get("status", "missing")
    detail = (meta or {}).get("detail") or ""
    return (
        f"engine: {settings.app_name}\n"
        f"backend: {settings.backend}\n"
        f"session: {status} {('(' + detail + ')') if detail else ''}\n"
        f"writes: {'enabled' if settings.allow_write_ops else 'disabled'}"
    )


async def _profile(ctx: CommandContext, args: list[str]) -> str:
    if not args:
        return "usage: /profile <username>"
    result = await ctx.engine.call("profile", {"username": args[0].lstrip("@")})
    profile = result.get("profile", {})
    stats = result.get("stats", {})
    text = _fmt_user({**profile, **stats})
    if profile.get("sec_uid"):
        text += f"\nsecUid {profile['sec_uid']}"
    return text


async def _posts(ctx: CommandContext, args: list[str]) -> str:
    if not args:
        return "usage: /posts <username>"
    profile = (await ctx.engine.call("profile", {"username": args[0].lstrip("@")})).get("profile", {})
    if not profile.get("sec_uid"):
        return "could not resolve that account"
    result = await ctx.engine.call(
        "user_posts", {"sec_uid": profile["sec_uid"], "count": int(args[1]) if len(args) > 1 else 10}
    )
    videos = result.get("videos", [])
    if not videos:
        return f"@{args[0].lstrip('@')} has no videos (or the endpoint is blocked here)"
    return "\n\n".join(_fmt_video(v) for v in videos[:10])


async def _comments(ctx: CommandContext, args: list[str]) -> str:
    if not args:
        return "usage: /comments <video_id> [count]"
    result = await ctx.engine.call(
        "comments", {"video_id": args[0], "count": int(args[1]) if len(args) > 1 else 10}
    )
    comments = result.get("comments", [])
    if not comments:
        return "no comments"
    return "\n".join(_fmt_comment(c) for c in comments[:20])


async def _followers(ctx: CommandContext, args: list[str]) -> str:
    return await _user_list(ctx, args, "followers")


async def _following(ctx: CommandContext, args: list[str]) -> str:
    return await _user_list(ctx, args, "following")


async def _user_list(ctx: CommandContext, args: list[str], kind: str) -> str:
    if not args:
        return f"usage: /{kind} <username> [count]"
    username = args[0].lstrip("@")
    profile = (await ctx.engine.call("profile", {"username": username})).get("profile", {})
    if not profile.get("sec_uid") or not profile.get("id"):
        return "could not resolve that account"
    result = await ctx.engine.call(
        kind,
        {
            "sec_uid": profile["sec_uid"],
            "user_id": str(profile["id"]),
            "count": int(args[1]) if len(args) > 1 else 10,
        },
    )
    users = result.get("users", [])
    if not users:
        return f"no {kind} returned"
    lines = [f"@{u.get('unique_id') or '?'} (id {u.get('id')})" for u in users[:20]]
    lines.append(f"total {result.get('total') if result.get('total') is not None else len(users)}")
    return "\n".join(lines)


async def _like(ctx: CommandContext, args: list[str]) -> str:
    if not args:
        return "usage: /like <video_id>"
    result = await ctx.engine.call("like", {"video_id": args[0]})
    return f"liked {args[0]}" if result.get("liked") else f"could not like {args[0]}"


async def _unlike(ctx: CommandContext, args: list[str]) -> str:
    if not args:
        return "usage: /unlike <video_id>"
    await ctx.engine.call("unlike", {"video_id": args[0]})
    return f"unlike {args[0]}"


async def _comment(ctx: CommandContext, args: list[str]) -> str:
    if len(args) < 2:
        return "usage: /comment <video_id> <text>"
    result = await ctx.engine.call(
        "comment", {"video_id": args[0], "text": " ".join(args[1:])}
    )
    return f"comment posted on {args[0]} (id {result.get('comment_id') or '?'})"


async def _follow(ctx: CommandContext, args: list[str]) -> str:
    return await _do_follow(ctx, args, "follow")


async def _unfollow(ctx: CommandContext, args: list[str]) -> str:
    return await _do_follow(ctx, args, "unfollow")


async def _do_follow(ctx: CommandContext, args: list[str], action: str) -> str:
    if not args:
        return f"usage: /{action} <username>"
    username = args[0].lstrip("@")
    profile = (await ctx.engine.call("profile", {"username": username})).get("profile", {})
    if not profile.get("sec_uid") or not profile.get("id"):
        return "could not resolve that account"
    result = await ctx.engine.call(
        action, {"sec_uid": profile["sec_uid"], "user_id": str(profile["id"])}
    )
    state = "following" if result.get("following") else "not following"
    return f"@{username} — {state}"


async def _search(ctx: CommandContext, args: list[str]) -> str:
    if not args:
        return "usage: /search <keyword>"
    result = await ctx.engine.call("search_users", {"keyword": " ".join(args)})
    users = result.get("users", [])
    if not users:
        return "no results"
    return "\n".join(f"@{u.get('unique_id')} (id {u.get('id')})" for u in users[:15])


async def _pause(ctx: CommandContext, args: list[str]) -> str:
    await ctx.engine.set_paused(True)
    return "writes paused"


async def _resume(ctx: CommandContext, args: list[str]) -> str:
    await ctx.engine.set_paused(False)
    return "writes resumed"


async def _raw(ctx: CommandContext, args: list[str]) -> str:
    """Escape hatch: run any registered operation with raw JSON parameters."""
    if not args:
        return 'usage: /raw <operation> {"param": "value"}'
    operation = args[0]
    try:
        params = json.loads(" ".join(args[1:])) if len(args) > 1 else {}
    except json.JSONDecodeError as exc:
        return f"bad JSON: {exc}"
    result = await ctx.engine.call(operation, params)
    return json.dumps(result, indent=2, default=str)[:2500]


async def _help(ctx: CommandContext, args: list[str]) -> str:
    lines = ["commands:"]
    for command in COMMANDS.values():
        lines.append(f"{command.usage} — {command.summary}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------
COMMANDS: dict[str, Command] = {}


def _register(command: Command) -> Command:
    COMMANDS[command.name] = command
    return command


_register(Command(name="help", usage="/help", summary="list the commands", handler=_help,
                 aliases=("commands",)))
_register(Command(name="whoami", usage="/whoami", summary="the account this session belongs to",
                 handler=_whoami, aliases=("me",)))
_register(Command(name="health", usage="/health", summary="engine and session status",
                 handler=_health, aliases=("status",)))
_register(Command(name="profile", usage="/profile <username>", summary="a user's profile and stats",
                 handler=_profile))
_register(Command(name="posts", usage="/posts <username> [count]", summary="a user's videos",
                 handler=_posts))
_register(Command(name="comments", usage="/comments <video_id> [count]",
                 summary="comments on a video", handler=_comments))
_register(Command(name="followers", usage="/followers <username> [count]",
                 summary="who follows a user", handler=_followers))
_register(Command(name="following", usage="/following <username> [count]",
                 summary="who a user follows", handler=_following))
_register(Command(name="like", usage="/like <video_id>", summary="like a video",
                 handler=_like, write=True))
_register(Command(name="unlike", usage="/unlike <video_id>", summary="remove a like",
                 handler=_unlike, write=True))
_register(Command(name="comment", usage="/comment <video_id> <text>",
                 summary="post a comment on a video", handler=_comment, write=True))
_register(Command(name="follow", usage="/follow <username>", summary="follow a user",
                 handler=_follow, write=True))
_register(Command(name="unfollow", usage="/unfollow <username>", summary="unfollow a user",
                 handler=_unfollow, write=True))
_register(Command(name="search", usage="/search <keyword>", summary="search for users",
                 handler=_search))
_register(Command(name="pause", usage="/pause", summary="pause all write operations",
                 handler=_pause, aliases=("stop",)))
_register(Command(name="resume", usage="/resume", summary="resume write operations",
                 handler=_resume, aliases=("start",)))
_register(Command(name="raw", usage='/raw <operation> {"json": "params"}',
                 summary="run any registered operation directly", handler=_raw))

WRITE_COMMANDS = frozenset(name for name, c in COMMANDS.items() if c.write)


def resolve(token: str) -> Command | None:
    token = token.lstrip("/").lower()
    for command in COMMANDS.values():
        if command.matches(token):
            return command
    return None


def describe() -> list[dict]:
    return [
        {"command": c.name, "usage": c.usage, "summary": c.summary, "write": c.write}
        for c in COMMANDS.values()
    ]
