"""The command set the bot understands.

A command is a thin shell over an engine operation: it parses the arguments,
calls the operation, and formats the answer for a chat message. Keeping the
mapping here (rather than in the transport) means the same commands work
whether they arrive over DM, a comment, or the HTTP API.

Commands are grouped into categories so ``/help`` stays readable on a phone.
"""
from __future__ import annotations

import json
import shlex
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

Handler = Callable[["CommandContext", list[str]], Awaitable[str]]

CATEGORY_ORDER = [
    "account",
    "videos",
    "people",
    "engagement",
    "discovery",
    "inbox",
    "publishing",
    "automation",
    "system",
]

CATEGORY_TITLES = {
    "account": "Account",
    "videos": "Videos",
    "people": "People",
    "engagement": "Likes, comments & follows",
    "discovery": "Finding things",
    "inbox": "Notifications & messages",
    "publishing": "Posting",
    "automation": "Automation",
    "system": "Engine & help",
}


@dataclass
class CommandContext:
    """What a handler needs: the engine, and who is talking."""

    engine: Any
    author_id: str = ""
    author_name: str = ""
    allow_write: bool = False
    # Extra services the handlers may use (scheduler, aliases, ...). They are
    # injected by the runner so this module stays importable on its own.
    services: dict[str, Any] = field(default_factory=dict)

    def service(self, name: str) -> Any:
        return self.services.get(name)


@dataclass
class Command:
    name: str
    usage: str
    summary: str
    handler: Handler
    write: bool = False
    aliases: tuple[str, ...] = field(default_factory=tuple)
    category: str = "system"
    # The engine operation this command drives, when it drives one.
    operation: str = ""
    # Extra usage lines shown by /help.
    examples: tuple[str, ...] = field(default_factory=tuple)

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
    parts = [f"video {video.get('id')}"]
    desc = (video.get("desc") or "").strip()
    if desc:
        parts.append(desc[:160])
    stats = []
    for label, key in (("likes", "like_count"), ("comments", "comment_count"),
                       ("plays", "play_count"), ("shares", "share_count")):
        if video.get(key) is not None:
            stats.append(f"{label} {video[key]}")
    if stats:
        parts.append(", ".join(stats))
    if video.get("author"):
        parts.append(f"by @{video['author']}")
    return "\n".join(parts)


def _fmt_comment(comment: dict) -> str:
    return f"@{comment.get('author') or '?'}: {comment.get('text') or ''}"


def _compact(text: str, limit: int = 2600) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def _resolve_user(ctx: CommandContext, username: str) -> dict | None:
    """Turn ``@name`` into the profile block the API calls need."""
    result = await ctx.engine.call("profile", {"username": username.lstrip("@")})
    profile = result.get("profile") or {}
    if not profile.get("sec_uid") or not profile.get("id"):
        return None
    return profile


# --------------------------------------------------------------------------
# account
# --------------------------------------------------------------------------
async def _whoami(ctx: CommandContext, args: list[str]) -> str:
    result = await ctx.engine.call("me", {})
    account = result.get("account", {})
    return (
        f"@{account.get('unique_id')} ({account.get('nickname')})\n"
        f"id {account.get('id')}\n"
        f"secUid {account.get('sec_uid')}"
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


async def _settings(ctx: CommandContext, args: list[str]) -> str:
    data = await ctx.engine.call("account_settings", {})
    lines = ["account settings:"]
    for key in ("language", "im_enabled", "private_account", "pro_account"):
        if data.get(key) is not None:
            lines.append(f"{key}: {data[key]}")
    return "\n".join(lines)


async def _username(ctx: CommandContext, args: list[str]) -> str:
    if not args:
        return "usage: /username <name>"
    name = args[0].lstrip("@")
    data = await ctx.engine.call("username_check", {"username": name})
    return f"@{name} — {'available' if data.get('available') else 'taken'}"


async def _setprofile(ctx: CommandContext, args: list[str]) -> str:
    if not args:
        return "usage: /setprofile <new bio>"
    await ctx.engine.call("update_profile", {"signature": " ".join(args)})
    return "bio updated"


# --------------------------------------------------------------------------
# videos
# --------------------------------------------------------------------------
async def _posts(ctx: CommandContext, args: list[str]) -> str:
    if not args:
        return "usage: /posts <username> [count]"
    username = args[0].lstrip("@")
    profile = await _resolve_user(ctx, username)
    if not profile:
        return "could not resolve that account"
    count = int(args[1]) if len(args) > 1 else 10
    result = await ctx.engine.call(
        "user_posts", {"sec_uid": profile["sec_uid"], "count": count}
    )
    videos = result.get("videos", [])
    if not videos:
        return (
            f"@{username} has no videos (or /api/post/item_list/ is blocked "
            "from this IP — /myvideos still works)"
        )
    return "\n\n".join(_fmt_video(v) for v in videos[:10])


async def _myvideos(ctx: CommandContext, args: list[str]) -> str:
    count = int(args[0]) if args else 20
    result = await ctx.engine.call("my_posts", {"count": count})
    ids = list(result.get("video_ids") or [])
    pinned = ""
    getter = getattr(ctx.engine, "video_id", None)
    if getter is not None:
        try:
            pinned = await getter()
        except Exception:  # noqa: BLE001
            pinned = ""
    if pinned and pinned not in ids:
        ids.insert(0, pinned)
    total = result.get("video_count")
    if not ids:
        if total:
            return (
                f"@{result.get('username') or 'this account'} has {total} videos, "
                "but the list is not readable from this IP.\n"
                "Open one of your videos on TikTok, copy the number after "
                "/video/ in its URL, and send /setvideo <that number> here."
            )
        return "no videos found for this account"
    lines = [f"@{result.get('username') or 'this account'} — {total or len(ids)} video(s):"]
    lines.extend(ids[:count])
    return "\n".join(lines)


async def _setvideo(ctx: CommandContext, args: list[str]) -> str:
    """Pin the video the chat runs on."""
    if not args:
        current = await ctx.engine.video_id()
        return f"the chat runs on {current}" if current else "usage: /setvideo <video_id>"
    video_id = args[0].strip()
    result = await ctx.engine.call("video_detail", {"video_id": video_id})
    video = result.get("video") or {}
    await ctx.engine.set_video_id(video_id)
    return (
        f"the chat now runs on {video_id}\n"
        f"caption: {(video.get('desc') or '(none)')[:120]}\n"
        f"by @{video.get('author') or '?'}"
    )


async def _video(ctx: CommandContext, args: list[str]) -> str:
    if not args:
        return "usage: /video <video_id>"
    result = await ctx.engine.call("video_detail", {"video_id": args[0]})
    return _fmt_video(result.get("video") or {})


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


async def _replies(ctx: CommandContext, args: list[str]) -> str:
    if len(args) < 2:
        return "usage: /replies <video_id> <comment_id>"
    result = await ctx.engine.call(
        "comment_replies", {"video_id": args[0], "comment_id": args[1]}
    )
    replies = result.get("replies", [])
    if not replies:
        return "no replies"
    return "\n".join(_fmt_comment(c) for c in replies[:20])


# --------------------------------------------------------------------------
# people
# --------------------------------------------------------------------------
async def _followers(ctx: CommandContext, args: list[str]) -> str:
    return await _user_list(ctx, args, "followers")


async def _following(ctx: CommandContext, args: list[str]) -> str:
    return await _user_list(ctx, args, "following")


async def _user_list(ctx: CommandContext, args: list[str], kind: str) -> str:
    if not args:
        return f"usage: /{kind} <username> [count]"
    username = args[0].lstrip("@")
    profile = await _resolve_user(ctx, username)
    if not profile:
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
    total = result.get("total")
    lines.append(f"total {total if total is not None else len(users)}")
    return "\n".join(lines)


async def _search(ctx: CommandContext, args: list[str]) -> str:
    if not args:
        return "usage: /search <keyword>"
    result = await ctx.engine.call("search_users", {"keyword": " ".join(args)})
    users = result.get("users", [])
    if not users:
        return "no results"
    return "\n".join(f"@{u.get('unique_id')} (id {u.get('id')})" for u in users[:15])


async def _searchvideos(ctx: CommandContext, args: list[str]) -> str:
    if not args:
        return "usage: /searchvideos <keyword>"
    result = await ctx.engine.call("search_videos", {"keyword": " ".join(args)})
    videos = result.get("videos", [])
    if not videos:
        return "no results"
    return "\n\n".join(_fmt_video(v) for v in videos[:10])


async def _searchall(ctx: CommandContext, args: list[str]) -> str:
    if not args:
        return "usage: /searchall <keyword>"
    result = await ctx.engine.call("search_general", {"keyword": " ".join(args)})
    results = result.get("results") or []
    if not results:
        return "no results"
    return json.dumps(results, indent=2, default=str)[:2200]


async def _suggested(ctx: CommandContext, args: list[str]) -> str:
    result = await ctx.engine.call("suggested_users", {"count": int(args[0]) if args else 15})
    users = result.get("users", [])
    if not users:
        return "no suggestions"
    return "\n".join(f"@{u.get('unique_id')} — {u.get('nickname') or ''}" for u in users[:20])


async def _friends(ctx: CommandContext, args: list[str]) -> str:
    result = await ctx.engine.call("friends", {"count": int(args[0]) if args else 20})
    users = result.get("users", [])
    if not users:
        return "no friends"
    return "\n".join(f"@{u.get('unique_id')}" for u in users[:30])


# --------------------------------------------------------------------------
# engagement
# --------------------------------------------------------------------------
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


async def _save(ctx: CommandContext, args: list[str]) -> str:
    if not args:
        return "usage: /save <video_id>"
    await ctx.engine.call("favorite", {"video_id": args[0]})
    return f"saved {args[0]}"


async def _unsave(ctx: CommandContext, args: list[str]) -> str:
    if not args:
        return "usage: /unsave <video_id>"
    await ctx.engine.call("unfavorite", {"video_id": args[0]})
    return f"removed {args[0]} from favourites"


async def _share(ctx: CommandContext, args: list[str]) -> str:
    if not args:
        return "usage: /share <video_id>"
    await ctx.engine.call("share", {"video_id": args[0]})
    return f"shared {args[0]}"


async def _comment(ctx: CommandContext, args: list[str]) -> str:
    if len(args) < 2:
        return "usage: /comment <video_id> <text>"
    result = await ctx.engine.call(
        "comment", {"video_id": args[0], "text": " ".join(args[1:])}
    )
    comment = result.get("comment") or {}
    code = comment.get("status_code")
    if code not in (0, None):
        # TikTok refused the write — say so instead of claiming it worked.
        detail = comment.get("status_msg") or result.get("detail") or "TikTok refused it"
        return f"the comment was not posted: {detail}"
    cid = result.get("comment_id")
    via = result.get("via")
    where = f" (through the page)" if via == "ui" else ""
    return f"comment posted on {args[0]}{where} (id {cid or '?'})"


async def _reply(ctx: CommandContext, args: list[str]) -> str:
    if len(args) < 3:
        return "usage: /reply <video_id> <comment_id> <text>"
    result = await ctx.engine.call(
        "comment_reply",
        {"video_id": args[0], "comment_id": args[1], "text": " ".join(args[2:])},
    )
    return f"reply posted (id {result.get('comment_id') or '?'})"


async def _deletecomment(ctx: CommandContext, args: list[str]) -> str:
    if len(args) < 2:
        return "usage: /deletecomment <video_id> <comment_id>"
    await ctx.engine.call("comment_delete", {"video_id": args[0], "comment_id": args[1]})
    return f"deleted comment {args[1]}"


async def _likecomment(ctx: CommandContext, args: list[str]) -> str:
    if len(args) < 2:
        return "usage: /likecomment <video_id> <comment_id>"
    await ctx.engine.call("comment_digg", {"video_id": args[0], "comment_id": args[1]})
    return f"liked comment {args[1]}"


async def _follow(ctx: CommandContext, args: list[str]) -> str:
    return await _do_follow(ctx, args, "follow")


async def _unfollow(ctx: CommandContext, args: list[str]) -> str:
    return await _do_follow(ctx, args, "unfollow")


async def _do_follow(ctx: CommandContext, args: list[str], action: str) -> str:
    if not args:
        return f"usage: /{action} <username>"
    username = args[0].lstrip("@")
    profile = await _resolve_user(ctx, username)
    if not profile:
        return "could not resolve that account"
    result = await ctx.engine.call(
        action, {"sec_uid": profile["sec_uid"], "user_id": str(profile["id"])}
    )
    state = "following" if result.get("following") else "not following"
    return f"@{username} — {state}"


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------
async def _explore(ctx: CommandContext, args: list[str]) -> str:
    result = await ctx.engine.call("explore_feed", {"count": int(args[0]) if args else 8})
    items = result.get("items", [])
    if not items:
        return "no items returned"
    return "\n\n".join(_fmt_video(v) for v in items[:10])


async def _foryou(ctx: CommandContext, args: list[str]) -> str:
    result = await ctx.engine.call("recommended_feed", {"count": int(args[0]) if args else 8})
    items = result.get("items", [])
    if not items:
        return "no items returned"
    return "\n\n".join(_fmt_video(v) for v in items[:10])


async def _hashtag(ctx: CommandContext, args: list[str]) -> str:
    if not args:
        return "usage: /hashtag <tag> [count]"
    result = await ctx.engine.call(
        "hashtag_videos", {"tag": args[0], "count": int(args[1]) if len(args) > 1 else 10}
    )
    items = result.get("items", [])
    if not items:
        return "no videos found"
    return "\n\n".join(_fmt_video(v) for v in items[:10])


async def _taginfo(ctx: CommandContext, args: list[str]) -> str:
    if not args:
        return "usage: /taginfo <tag>"
    info = await ctx.engine.call("hashtag_info", {"tag": args[0]})
    lines = [f"#{info.get('tag') or args[0].lstrip('#')}"]
    for key in ("title", "description", "video_count", "view_count", "user_count"):
        if info.get(key) not in (None, ""):
            lines.append(f"{key}: {info[key]}")
    return "\n".join(lines)


async def _music(ctx: CommandContext, args: list[str]) -> str:
    if not args:
        return "usage: /music <music_id>"
    info = await ctx.engine.call("music_info", {"music_id": args[0]})
    lines = [f"sound {info.get('title') or '?'}"]
    if info.get("author"):
        lines.append(f"by {info['author']}")
    if info.get("usage_count") is not None:
        lines.append(f"used {info['usage_count']} times")
    return "\n".join(lines)


async def _trending(ctx: CommandContext, args: list[str]) -> str:
    result = await ctx.engine.call("trending", {"count": int(args[0]) if args else 15})
    words = result.get("words") or []
    if not words:
        return "no trending words returned"
    names = []
    for word in words:
        if isinstance(word, dict):
            names.append(str(word.get("word") or word.get("title") or ""))
        else:
            names.append(str(word))
    return "\n".join(n for n in names if n)[:2000]


# --------------------------------------------------------------------------
# inbox
# --------------------------------------------------------------------------
async def _notifications(ctx: CommandContext, args: list[str]) -> str:
    result = await ctx.engine.call("notifications", {"count": int(args[0]) if args else 10})
    notices = result.get("notices") or []
    if not notices:
        return "no notifications"
    lines = []
    for notice in notices[:12]:
        if isinstance(notice, dict):
            title = notice.get("title") or notice.get("type") or "notice"
            text = (notice.get("text") or notice.get("content") or "").strip()
            lines.append(f"• {title}" + (f" — {text[:120]}" if text else ""))
        else:
            lines.append(f"• {str(notice)[:140]}")
    return "\n".join(lines)


async def _unread(ctx: CommandContext, args: list[str]) -> str:
    data = await ctx.engine.call("notification_count", {})
    total = data.get("total", 0)
    groups = data.get("groups") or {}
    detail = ", ".join(f"{k}:{v}" for k, v in groups.items() if v)
    return f"unread notifications: {total}" + (f" ({detail})" if detail else "")


async def _dms(ctx: CommandContext, args: list[str]) -> str:
    result = await ctx.engine.call("dm_conversations", {})
    conversations = result.get("conversations") or []
    if not conversations:
        return (
            "no DM conversations — TikTok's messaging service is unavailable "
            "from this IP, or the account has never been messaged"
        )
    lines = []
    for conv in conversations[:20]:
        if isinstance(conv, dict):
            lines.append(
                f"• {conv.get('nickname') or conv.get('unique_id') or conv.get('id')}"
            )
        else:
            lines.append(f"• {conv}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# publishing
# --------------------------------------------------------------------------
async def _upload(ctx: CommandContext, args: list[str]) -> str:
    if not args:
        return "usage: /upload <video_path> [caption]"
    path = args[0]
    caption = " ".join(args[1:])
    result = await ctx.engine.call("upload", {"video_path": path, "caption": caption})
    if result.get("error"):
        return f"upload failed: {result['error']}"
    return (
        f"posted {path}\n"
        f"video id: {result.get('aweme_id') or '?'}\n"
        f"status: {result.get('post_status_msg') or result.get('status_msg') or 'ok'}"
    )


async def _deletevideo(ctx: CommandContext, args: list[str]) -> str:
    if not args:
        return "usage: /deletevideo <video_id>"
    await ctx.engine.call("delete_video", {"aweme_id": args[0]})
    return f"deleted {args[0]}"


async def _visibility(ctx: CommandContext, args: list[str]) -> str:
    if len(args) < 2:
        return "usage: /visibility <video_id> <0 public|1 friends|2 private>"
    await ctx.engine.call("visibility", {"aweme_id": args[0], "visibility": int(args[1])})
    return f"{args[0]} visibility -> {args[1]}"


async def _queue(ctx: CommandContext, args: list[str]) -> str:
    from app.bot.autoposter import queue_files

    files = queue_files()
    if not files:
        return "the posting queue is empty"
    return "\n".join(f"• {f.name}" for f in files[:40])


async def _postnext(ctx: CommandContext, args: list[str]) -> str:
    """Post the next video from the queue — the autoposter's job."""
    autoposter = ctx.service("autoposter")
    if autoposter is None:
        return "the autoposter is not running"
    result = await autoposter.post_next()
    if result is None:
        return "the queue is empty — nothing to post"
    if result.get("error"):
        return f"the upload failed: {result['error']}"
    return (
        f"posted {result.get('video_path')}\n"
        f"video id: {result.get('aweme_id') or '?'}"
    )


# --------------------------------------------------------------------------
# automation
# --------------------------------------------------------------------------
async def _cron(ctx: CommandContext, args: list[str]) -> str:
    scheduler = ctx.service("scheduler")
    if scheduler is None:
        return "the scheduler is not running"
    if not args:
        return await scheduler.describe()
    action = args[0].lower()
    if action == "list":
        return await scheduler.describe()
    if action == "add":
        if len(args) < 3:
            return "usage: /cron add <every_minutes|hourly|daily> <command>"
        return await scheduler.add_from_chat(args[1], " ".join(args[2:]))
    if action in ("remove", "rm", "delete"):
        if len(args) < 2:
            return "usage: /cron remove <id>"
        return await scheduler.remove(args[1])
    if action in ("run", "fire"):
        if len(args) < 2:
            return "usage: /cron run <id>"
        return await scheduler.run_now(args[1])
    return "usage: /cron [list|add <schedule> <command>|remove <id>|run <id>]"


async def _alias(ctx: CommandContext, args: list[str]) -> str:
    aliases = ctx.service("aliases")
    if aliases is None:
        return "aliases are not available"
    if not args:
        return await aliases.describe()
    action = args[0].lower()
    if action == "list":
        return await aliases.describe()
    if action == "add":
        if len(args) < 3:
            return "usage: /alias add <name> <command>"
        return await aliases.add(args[1], " ".join(args[2:]))
    if action in ("remove", "rm", "delete"):
        if len(args) < 2:
            return "usage: /alias remove <name>"
        return await aliases.remove(args[1])
    return "usage: /alias [list|add <name> <command>|remove <name>]"


async def _automate(ctx: CommandContext, args: list[str]) -> str:
    scheduler = ctx.service("scheduler")
    aliases = ctx.service("aliases")
    parts = ["automation:"]
    if scheduler is not None:
        jobs = await scheduler.list()
        parts.append(f"scheduled jobs: {len(jobs)}")
        for job in jobs[:10]:
            parts.append(f"  • {job['id']} — {job['schedule']} → {job['command']}")
    if aliases is not None:
        names = await aliases.list()
        parts.append(f"custom commands: {len(names)}")
        for name in names[:10]:
            parts.append(f"  • /{name}")
    parts.append("use /cron and /alias to change them")
    return "\n".join(parts)


async def _run(ctx: CommandContext, args: list[str]) -> str:
    """Run another command by name — the automations' building block."""
    if not args:
        return "usage: /run <command> [args...]"
    from app.bot.dispatcher import Dispatcher

    dispatcher: Dispatcher | None = ctx.service("dispatcher")
    if dispatcher is None:
        return "the dispatcher is not available"
    token = args[0].lstrip("/")
    command = resolve(token)
    if command is None:
        return f"unknown command '{token}'"
    reply = await command.handler(ctx, args[1:])
    return reply or "(no output)"


# --------------------------------------------------------------------------
# system
# --------------------------------------------------------------------------
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


async def _status(ctx: CommandContext, args: list[str]) -> str:
    from app.config import settings
    from app.memory import snapshot

    adapter = getattr(ctx.engine, "adapter", None)
    mem = snapshot()
    lines = [
        f"engine: {settings.app_name}",
        f"backend: {settings.backend}",
        f"writes: {'enabled' if settings.allow_write_ops else 'disabled'}",
        f"memory: {mem['rss_mb']} MB used"
        + (f" / {mem['total_mb']} MB total" if mem.get("total_mb") else ""),
    ]
    if adapter is not None and hasattr(adapter, "status"):
        status = adapter.status()
        browser = status.get("browser") or {}
        lines.append(f"browser: {'running' if browser.get('running') else 'released'}"
                     f" (starts {browser.get('starts', 0)})")
        lines.append(f"tiktok calls: {status.get('calls', 0)}")
    meta = await ctx.engine.session_meta()
    lines.append(f"session: {(meta or {}).get('status', 'missing')}")
    return "\n".join(lines)


async def _memory(ctx: CommandContext, args: list[str]) -> str:
    from app.memory import snapshot

    mem = snapshot()
    lines = [
        f"rss: {mem['rss_mb']} MB",
        f"system used: {mem.get('used_mb')} MB of {mem.get('total_mb')} MB"
        + (f" ({mem['percent_used']}%)" if mem.get("percent_used") else ""),
        f"free: {mem.get('free_mb')} MB",
    ]
    if mem.get("disk_free_mb") is not None:
        lines.append(f"disk free: {mem['disk_free_mb']} MB")
    return "\n".join(lines)


async def _pause(ctx: CommandContext, args: list[str]) -> str:
    await ctx.engine.set_paused(True)
    return "writes paused"


async def _resume(ctx: CommandContext, args: list[str]) -> str:
    await ctx.engine.set_paused(False)
    return "writes resumed"


async def _ops(ctx: CommandContext, args: list[str]) -> str:
    from app.operations import CATEGORIES, by_category

    grouped = by_category()
    if args:
        wanted = args[0].lower()
        for key, items in grouped.items():
            if wanted in (key, CATEGORIES.get(key, "").lower()):
                return "\n".join(
                    f"{i['operation']} — {i['summary']}"
                    + (" (write)" if i["write"] else "")
                    for i in items
                )
        return f"unknown category '{wanted}'"
    lines = ["operations by category:"]
    for key in CATEGORIES:
        items = grouped.get(key) or []
        if not items:
            continue
        lines.append(f"\n{CATEGORIES[key]}")
        lines.extend(
            f"  {i['operation']} — {i['summary']}" + (" (write)" if i["write"] else "")
            for i in items
        )
    return "\n".join(lines)


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


async def _welcome(ctx: CommandContext, args: list[str]) -> str:
    """Re-send the welcome message through whatever channel is available."""
    from app.bot.welcome import build_welcome, deliver_welcome

    channel = ctx.service("channel")
    text = build_welcome(ctx.engine)
    if channel is None:
        return text
    ok, detail = await deliver_welcome(channel, text)
    return f"{'sent' if ok else 'could not send'}: {detail}"


async def _help(ctx: CommandContext, args: list[str]) -> str:
    if args:
        wanted = args[0].lower()
        for category in CATEGORY_ORDER:
            if wanted in (category, CATEGORY_TITLES[category].lower()):
                return _category_help(category)
        return f"unknown category '{wanted}' — try /help"
    return _category_help(None)


def _category_help(category: str | None) -> str:
    if category:
        commands = [c for c in COMMANDS.values() if c.category == category]
        lines = [f"{CATEGORY_TITLES[category]}:"]
        for command in commands:
            lines.append(f"{command.usage} — {command.summary}")
        return "\n".join(lines)

    lines = ["commands — /help <category> for one group"]
    for cat in CATEGORY_ORDER:
        commands = [c for c in COMMANDS.values() if c.category == cat]
        if not commands:
            continue
        lines.append(f"\n{CATEGORY_TITLES[cat]}")
        lines.extend(f"  {c.usage} — {c.summary}" for c in commands)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------
COMMANDS: dict[str, Command] = {}


def _register(command: Command) -> Command:
    COMMANDS[command.name] = command
    return command


def _cmd(name, usage, summary, handler, *, write=False, aliases=(), category="system",
         operation="", examples=()) -> Command:
    return _register(
        Command(
            name=name,
            usage=usage,
            summary=summary,
            handler=handler,
            write=write,
            aliases=tuple(aliases),
            category=category,
            operation=operation,
            examples=tuple(examples),
        )
    )


# --- account ---
_cmd("help", "/help [category]", "list the commands", _help,
     aliases=("commands",), category="system")
_cmd("whoami", "/whoami", "the account this session belongs to", _whoami,
     aliases=("me",), category="account", operation="me")
_cmd("profile", "/profile <username>", "a user's profile and stats", _profile,
     category="account", operation="profile")
_cmd("settings", "/settings", "the account's own settings", _settings,
     aliases=("accountsettings",), category="account", operation="account_settings")
_cmd("username", "/username <name>", "is a username still available?", _username,
     category="account", operation="username_check")
_cmd("setprofile", "/setprofile <bio>", "change the account's bio", _setprofile,
     write=True, category="account", operation="update_profile")

# --- videos ---
_cmd("posts", "/posts <username> [count]", "a user's videos", _posts,
     category="videos", operation="user_posts")
_cmd("myvideos", "/myvideos [count]", "the account's own videos", _myvideos,
     aliases=("mine",), category="videos", operation="my_posts")
_cmd("video", "/video <video_id>", "one video in detail", _video,
     category="videos", operation="video_detail")
_cmd("setvideo", "/setvideo <video_id>", "choose the video the chat runs on", _setvideo,
     category="videos")
_cmd("comments", "/comments <video_id> [count]", "comments on a video", _comments,
     category="videos", operation="comments")
_cmd("replies", "/replies <video_id> <comment_id>", "replies to a comment", _replies,
     category="videos", operation="comment_replies")

# --- people ---
_cmd("followers", "/followers <username> [count]", "who follows a user", _followers,
     category="people", operation="followers")
_cmd("following", "/following <username> [count]", "who a user follows", _following,
     category="people", operation="following")
_cmd("search", "/search <keyword>", "search for users", _search,
     aliases=("find",), category="people", operation="search_users")
_cmd("searchvideos", "/searchvideos <keyword>", "search for videos", _searchvideos,
     category="people", operation="search_videos")
_cmd("searchall", "/searchall <keyword>", "search everything", _searchall,
     category="people", operation="search_general")
_cmd("suggested", "/suggested [count]", "accounts suggested to this account", _suggested,
     category="people", operation="suggested_users")
_cmd("friends", "/friends [count]", "accounts that follow this account back", _friends,
     category="people", operation="friends")

# --- engagement ---
_cmd("like", "/like <video_id>", "like a video", _like,
     write=True, category="engagement", operation="like")
_cmd("unlike", "/unlike <video_id>", "remove a like", _unlike,
     write=True, category="engagement", operation="unlike")
_cmd("save", "/save <video_id>", "save a video to favourites", _save,
     write=True, aliases=("favorite",), category="engagement", operation="favorite")
_cmd("unsave", "/unsave <video_id>", "remove a video from favourites", _unsave,
     write=True, aliases=("unfavorite",), category="engagement", operation="unfavorite")
_cmd("share", "/share <video_id>", "count a share of a video", _share,
     write=True, category="engagement", operation="share")
_cmd("comment", "/comment <video_id> <text>", "post a comment on a video", _comment,
     write=True, category="engagement", operation="comment")
_cmd("reply", "/reply <video_id> <comment_id> <text>", "reply to a comment", _reply,
     write=True, category="engagement", operation="comment_reply")
_cmd("deletecomment", "/deletecomment <video_id> <comment_id>", "delete a comment",
     _deletecomment, write=True, category="engagement", operation="comment_delete")
_cmd("likecomment", "/likecomment <video_id> <comment_id>", "like a comment",
     _likecomment, write=True, category="engagement", operation="comment_digg")
_cmd("follow", "/follow <username>", "follow a user", _follow,
     write=True, category="engagement", operation="follow")
_cmd("unfollow", "/unfollow <username>", "unfollow a user", _unfollow,
     write=True, category="engagement", operation="unfollow")

# --- discovery ---
_cmd("explore", "/explore [count]", "videos from the Explore page", _explore,
     category="discovery", operation="explore_feed")
_cmd("foryou", "/foryou [count]", "this account's recommendations", _foryou,
     category="discovery", operation="recommended_feed")
_cmd("hashtag", "/hashtag <tag> [count]", "videos under a hashtag", _hashtag,
     aliases=("tag",), category="discovery", operation="hashtag_videos")
_cmd("taginfo", "/taginfo <tag>", "statistics about a hashtag", _taginfo,
     category="discovery", operation="hashtag_info")
_cmd("music", "/music <music_id>", "details about a sound", _music,
     aliases=("sound",), category="discovery", operation="music_info")
_cmd("trending", "/trending [count]", "trending search words", _trending,
     category="discovery", operation="trending")

# --- inbox ---
_cmd("notifications", "/notifications [count]", "the activity inbox", _notifications,
     aliases=("inbox",), category="inbox", operation="notifications")
_cmd("unread", "/unread", "unread notification counts", _unread,
     category="inbox", operation="notification_count")
_cmd("dms", "/dms", "the account's DM conversations", _dms,
     aliases=("conversations",), category="inbox", operation="dm_conversations")

# --- publishing ---
_cmd("upload", "/upload <video_path> [caption]", "post a video to the account", _upload,
     write=True, category="publishing", operation="upload")
_cmd("deletevideo", "/deletevideo <video_id>", "delete one of the account's videos",
     _deletevideo, write=True, aliases=("rmvideo",), category="publishing",
     operation="delete_video")
_cmd("visibility", "/visibility <video_id> <0|1|2>", "change a video's visibility",
     _visibility, write=True, category="publishing", operation="visibility")
_cmd("queue", "/queue", "what is waiting to be posted", _queue,
     category="publishing")
_cmd("postnext", "/postnext", "post the next queued video now", _postnext,
     write=True, category="publishing")

# --- automation ---
_cmd("cron", "/cron [list|add|remove|run]", "scheduled automations", _cron,
     aliases=("schedule", "schedules"), category="automation")
_cmd("alias", "/alias [list|add|remove]", "custom commands", _alias,
     aliases=("aliases",), category="automation")
_cmd("automate", "/automate", "everything the automations know", _automate,
     aliases=("automations",), category="automation")
_cmd("run", "/run <command> [args]", "run another command", _run,
     category="automation")

# --- system ---
_cmd("health", "/health", "engine and session status", _health,
     aliases=("status",), category="system")
_cmd("status", "/status", "engine, memory and browser status", _status,
     category="system")
_cmd("memory", "/memory", "memory and disk usage", _memory,
     aliases=("mem", "resources"), category="system")
_cmd("ops", "/ops [category]", "every operation the engine can run", _ops,
     aliases=("operations",), category="system")
_cmd("pause", "/pause", "pause all write operations", _pause,
     aliases=("stop",), category="system")
_cmd("resume", "/resume", "resume write operations", _resume,
     aliases=("start",), category="system")
_cmd("raw", '/raw <operation> {"json": "params"}',
     "run any registered operation directly", _raw, category="system")
_cmd("welcome", "/welcome", "send the welcome message again", _welcome,
     aliases=("hello", "hi"), category="system")

WRITE_COMMANDS = frozenset(name for name, c in COMMANDS.items() if c.write)


def resolve(token: str) -> Command | None:
    token = token.lstrip("/").lower()
    for command in COMMANDS.values():
        if command.matches(token):
            return command
    return None


def describe() -> list[dict]:
    return [
        {
            "command": c.name,
            "usage": c.usage,
            "summary": c.summary,
            "write": c.write,
            "category": c.category,
            "aliases": list(c.aliases),
            "operation": c.operation,
        }
        for c in COMMANDS.values()
    ]


def describe_by_category() -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for command in COMMANDS.values():
        grouped.setdefault(command.category, []).append(
            {
                "command": command.name,
                "usage": command.usage,
                "summary": command.summary,
                "write": command.write,
                "aliases": list(command.aliases),
            }
        )
    return grouped
