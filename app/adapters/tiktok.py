"""TikTok adapter — dispatches an operation to the backend that can perform it.

Responsibilities:
  * pick the right backend per operation (see ``app.operations``)
  * serialise TikTok access behind a lock and the shared rate limiter
  * normalise TikTok's payload shapes into stable, compact JSON
  * start the expensive browser only when an operation needs it
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.adapters.base import BackendUnavailable, UnsupportedOperation
from app.config import settings
from app.memory import free_memory_mb
from app.operations import (
    BACKEND_API,
    BACKEND_HTTP,
    BACKEND_SSR,
    BACKEND_UPLOAD,
    OPERATIONS,
    get as get_operation,
)
from app.ratelimit import RateLimiter

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# payload normalisers
# --------------------------------------------------------------------------
def _user(raw: dict) -> dict:
    stats = raw.get("stats") or {}
    return {
        "id": raw.get("id") or raw.get("uid"),
        "unique_id": raw.get("uniqueId") or raw.get("unique_id"),
        "nickname": raw.get("nickname") or raw.get("nickName"),
        "sec_uid": raw.get("secUid") or raw.get("sec_uid"),
        "signature": raw.get("signature") or "",
        "verified": raw.get("verified", False),
        "private": raw.get("privateAccount", False),
        "avatar": raw.get("avatarLarger") or raw.get("avatarMedium") or "",
        "follower_count": stats.get("followerCount"),
        "following_count": stats.get("followingCount"),
        "like_count": stats.get("heartCount") or stats.get("heart"),
        "video_count": stats.get("videoCount"),
        "friend_count": stats.get("friendCount"),
    }


def _video(raw: dict) -> dict:
    stats = raw.get("stats") or {}
    author = raw.get("author") or {}
    music = raw.get("music") or {}
    return {
        "id": raw.get("aweme_id") or raw.get("id"),
        "desc": raw.get("desc") or "",
        "create_time": raw.get("createTime") or raw.get("create_time"),
        "play_count": stats.get("playCount"),
        "like_count": stats.get("diggCount"),
        "comment_count": stats.get("commentCount"),
        "share_count": stats.get("shareCount"),
        "collect_count": stats.get("collectCount"),
        "play_count_label": raw.get("playCount") or raw.get("play_count"),
        "author_id": author.get("id") or author.get("uid"),
        "author": author.get("uniqueId") or author.get("unique_id") or author.get("nickname"),
        "author_sec_uid": author.get("secUid") or author.get("sec_uid"),
        "duration": raw.get("duration"),
        "music": music.get("title") or raw.get("music_title") or "",
        "music_author": music.get("authorName") or raw.get("music_author") or "",
        "cover": raw.get("cover") or raw.get("video_cover") or "",
    }


def _comment(raw: dict) -> dict:
    user = raw.get("user") or {}
    return {
        "cid": raw.get("cid"),
        "text": raw.get("text") or "",
        "create_time": raw.get("create_time"),
        "like_count": raw.get("digg_count"),
        "reply_count": raw.get("reply_comment_total"),
        "author_id": user.get("id") or user.get("uid"),
        "author": user.get("unique_id") or user.get("uniqueId") or user.get("nickname"),
    }


def _users(raw: dict) -> dict:
    """Normalise a user list.

    The list endpoints wrap each entry as {"user": {...}, "stats": {...}},
    while search results are flat — handle both.
    """
    items = raw.get("userInfoList") or raw.get("userList") or raw.get("users") or []
    users = []
    for item in items:
        if not isinstance(item, dict):
            continue
        user = item.get("user") if isinstance(item.get("user"), dict) else item
        stats = item.get("stats") if isinstance(item.get("stats"), dict) else {}
        users.append(_user({**user, "stats": stats}))
    return {
        "users": users,
        "has_more": bool(raw.get("hasMore", False)),
        "total": raw.get("total"),
        "cursor": raw.get("cursor"),
    }


# --------------------------------------------------------------------------
# adapter
# --------------------------------------------------------------------------
class TikTokAdapter:
    """Single-process adapter.

    Serialises all TikTok access behind one lock — the session cookies are
    shared state and must not be used concurrently.
    """

    def __init__(self, backend: str | None = None) -> None:
        self.backend_name = (backend or settings.backend).lower()
        # Set by the session keeper: called when a request comes back
        # unauthenticated, so the session can be re-minted and the call is
        # retried once.
        self.on_auth_failure = None
        self._browser = None
        self._http = None
        self._uploader = None
        self._lock = asyncio.Lock()
        self.limiter = RateLimiter(
            min_interval=settings.request_min_interval,
            max_per_hour=settings.request_max_per_hour,
        )
        self.calls = 0
        # True while an operation is running, so a handler that calls back
        # into call() fails loudly instead of deadlocking on the lock.
        self._dispatching = False

    # -- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        from app.adapters.http import HttpBackend

        self._http = HttpBackend()
        try:
            await self._http.start()
        except Exception as exc:  # noqa: BLE001
            # No session uploaded yet is not fatal — the API stays up and the
            # operator can upload one.
            self._http = None
            if self.backend_name == "http":
                raise
            log.warning("HTTP backend unavailable: %s", exc)

        if not settings.browser_enabled:
            log.info("browser backend disabled (BACKEND=%s)", self.backend_name)
            return

        if settings.http_first:
            # The browser is created but not launched. It starts on the first
            # operation that needs a signed request and is released again
            # after an idle window — this is what keeps the service inside
            # Render's free-tier memory budget.
            from app.adapters.browser import BrowserBackend

            self._browser = BrowserBackend()
            log.info("browser backend ready (starts on first signed request)")
            return

        await self._ensure_browser()

    async def _ensure_browser(self):
        """Create and start the browser backend if it is not running."""
        if self._browser is None:
            from app.adapters.browser import BrowserBackend

            self._browser = BrowserBackend()
        await self._browser.ensure_started()
        return self._browser

    async def browser(self):
        """The shared browser backend.

        Every part of the engine that needs a real Chromium uses *this* one.
        A second Chromium would double the memory footprint, which is more
        than a Render free instance can afford.
        """
        return await self._ensure_browser()

    def browser_if_running(self):
        """The shared browser backend, but only if it is already up."""
        return self._browser

    async def stop(self) -> None:
        if self._uploader is not None:
            try:
                await self._uploader.stop()
            except Exception:  # noqa: BLE001
                pass
            self._uploader = None
        for backend in (self._browser, self._http):
            if backend is not None:
                try:
                    await backend.stop()
                except Exception:  # noqa: BLE001
                    pass
        self._browser = self._http = None

    async def reload_session(self) -> None:
        """Pick up a newly uploaded session."""
        if self._http is not None:
            await self._http._refresh_session()  # noqa: SLF001 - same package
        if self._browser is not None:
            await self._browser.reload_session()

    async def release_idle_browser(self) -> bool:
        """Let the browser go if it has been idle for a while."""
        if self._browser is None:
            return False
        return await self._browser.release_if_idle()

    def status(self) -> dict:
        return {
            "backend": self.backend_name,
            "calls": self.calls,
            "http": self._http is not None,
            "browser": self._browser.status() if self._browser is not None else {"running": False},
        }

    # -- helpers ---------------------------------------------------------
    def _uploader_for(self):
        """The upload pipeline is a separate client — it has its own protocol."""
        if self._uploader is None:
            from app.adapters.upload import Uploader

            self._uploader = Uploader()
        return self._uploader

    async def _backend_for(self, spec) -> Any:
        if spec.backend == BACKEND_SSR:
            if not settings.browser_enabled:
                raise BackendUnavailable(
                    "This operation needs the browser backend (BACKEND=browser)"
                )
            return await self._ensure_browser()
        if spec.backend == BACKEND_HTTP:
            if self._http is None:
                self._http = await self._start_http()
            return self._http
        if spec.backend == BACKEND_UPLOAD:
            return self._uploader_for()
        if spec.backend == BACKEND_API:
            if not settings.browser_enabled:
                raise BackendUnavailable(
                    "This operation needs the browser backend (BACKEND=browser)"
                )
            return await self._ensure_browser()
        raise UnsupportedOperation(f"No backend for {spec.name}")

    async def _start_http(self):
        from app.adapters.http import HttpBackend

        backend = HttpBackend()
        await backend.start()
        self._http = backend
        return backend

    # -- dispatch --------------------------------------------------------
    async def call(self, operation: str, params: dict) -> dict:
        spec = get_operation(operation)
        if spec is None:
            raise UnsupportedOperation(f"{operation} is not enabled in this adapter")
        missing = spec.missing_params(params or {})
        if missing:
            raise ValueError(f"{operation} requires: {', '.join(missing)}")
        if spec.write and not settings.allow_write_ops:
            raise PermissionError(
                f"'{operation}' changes account state and ALLOW_WRITE_OPS is off"
            )

        async with self._lock:
            await self.limiter.acquire()
            self.calls += 1
            if self._dispatching:
                # The lock is not re-entrant. A handler that calls back into
                # call() would wait for the lock it already holds, which
                # looks exactly like a hang — so say what went wrong.
                raise RuntimeError(
                    f"'{operation}' was called from inside another operation — "
                    "the adapter lock cannot be taken twice"
                )
            self._dispatching = True
            handler = getattr(self, f"_op_{spec.name}")
            try:
                backend = await self._backend_for(spec)
                return await handler(backend, params or {})
            except Exception as exc:
                if self.on_auth_failure is None or not _looks_like_auth_failure(exc):
                    raise
                # The session died — re-mint it and try once more.
                await self.on_auth_failure()
                backend = await self._backend_for(spec)
                return await handler(backend, params or {})
            finally:
                self._dispatching = False

    # -- posting operations ----------------------------------------------
    async def _op_upload(self, backend, p):
        result = await backend.upload(
            p["video_path"],
            p.get("caption", ""),
            visibility=int(p.get("visibility", 0)),
            allow_comment=bool(p.get("allow_comment", True)),
            allow_duet=bool(p.get("allow_duet", True)),
            allow_stitch=bool(p.get("allow_stitch", True)),
            schedule_time=int(p.get("schedule_time", 0)),
        )
        result["video_path"] = p["video_path"]
        return result

    async def _op_delete_video(self, backend, p):
        return await backend.delete_video(p["aweme_id"])

    async def _op_visibility(self, backend, p):
        return await backend.set_visibility(
            p["aweme_id"], int(p.get("visibility", 0))
        )

    # -- read operations -------------------------------------------------
    async def _op_me(self, backend, p):
        user = await backend.account_context()
        if not user:
            raise RuntimeError("The session is not logged in to any account")
        return {"account": _user(user)}

    async def _op_profile(self, backend, p):
        info = await backend.user_detail(p["username"])
        user = info.get("user") or {}
        stats = info.get("stats") or {}
        return {"profile": _user({**user, "stats": stats}), "stats": stats}

    async def _op_my_posts(self, backend, p):
        """The account's own videos, read from the rendered profile page."""
        username = p.get("username")
        total = None
        if not username:
            # Read the username from the page that is already open. Going
            # through self.call() here would try to take the adapter's lock
            # a second time, and the lock is not re-entrant.
            user = await backend.account_context()
            username = user.get("uniqueId") or user.get("unique_id") or ""
            if not username:
                raise RuntimeError("could not read the account's own username")
        ids = await backend.own_video_ids(username, limit=int(p.get("count") or 20))
        # The page always knows how many videos there are, even when the
        # list itself is not in the server-rendered data.
        try:
            info = await backend.user_detail(username)
            total = (info.get("stats") or {}).get("videoCount")
        except Exception:  # noqa: BLE001
            pass
        return {
            "username": username,
            "video_ids": ids,
            "count": len(ids),
            "video_count": total,
        }

    async def _op_my_posts_count(self, backend, p):
        """How many videos the account has published."""
        result = await self._op_my_posts(backend, {**p, "count": 1})
        return {"username": result["username"], "count": result["count"]}

    async def _op_video_detail(self, backend, p):
        """One video: caption, stats, author, music.

        The signed endpoint is tried first, but it answers with an empty
        body from datacenter IPs, so the rendered video page is used as the
        fallback — it carries the same data and works from a server.
        """
        try:
            data = await backend.fetch_json(
                "/api/item/detail/",
                {"itemId": p["video_id"], "coverFormat": 1},
            )
            item = data.get("itemInfo") or {}
            return {"video": _video(item.get("itemStruct") or item), "source": "api"}
        except Exception as exc:  # noqa: BLE001
            log.info("the video endpoint did not answer (%s) — using the page", exc)
        item = await backend.video_page(p["video_id"])
        return {"video": _video(item), "source": "page"}

    async def _op_user_posts(self, backend, p):
        data = await backend.fetch_json(
            "/api/post/item_list/",
            {
                "secUid": p["sec_uid"],
                "count": int(p.get("count") or 16),
                "cursor": int(p.get("cursor") or 0),
            },
        )
        items = data.get("itemList") or []
        return {
            "videos": [_video(v) for v in items],
            "cursor": data.get("cursor"),
            "has_more": bool(data.get("hasMore", False)),
            "status_code": data.get("statusCode", 0),
        }

    async def _op_comments(self, backend, p):
        data = await backend.fetch_json(
            "/api/comment/list/",
            {
                "aweme_id": p["video_id"],
                "count": int(p.get("count") or 20),
                "cursor": int(p.get("cursor") or 0),
            },
        )
        items = data.get("comments") or []
        return {
            "comments": [_comment(c) for c in items],
            "cursor": data.get("cursor"),
            "has_more": bool(data.get("hasMore", False)),
            "total": data.get("total"),
        }

    async def _op_comment_replies(self, backend, p):
        data = await backend.fetch_json(
            "/api/comment/list/reply/",
            {
                "aweme_id": p["video_id"],
                "comment_id": p["comment_id"],
                "count": int(p.get("count") or 20),
                "cursor": int(p.get("cursor") or 0),
            },
        )
        items = data.get("comments") or []
        return {
            "replies": [_comment(c) for c in items],
            "cursor": data.get("cursor"),
            "total": data.get("total"),
        }

    async def _op_followers(self, backend, p):
        return await self._user_list(backend, p, list_type=0)

    async def _op_following(self, backend, p):
        return await self._user_list(backend, p, list_type=1)

    async def _user_list(self, backend, p, list_type: int):
        data = await backend.fetch_json(
            "/api/user/list/",
            {
                "secUid": p["sec_uid"],
                "targetUserId": p["user_id"],
                "scene": "151",
                "type": list_type,
                "count": int(p.get("count") or 30),
                "cursor": int(p.get("cursor") or 0),
                "minCursor": 0,
                "maxCursor": 0,
                "isNonPersonalized": "false",
            },
        )
        return _users(data)

    async def _op_explore_feed(self, backend, p):
        data = await backend.fetch_json(
            "/api/prefetch/explore/item_list/",
            {
                "count": int(p.get("count") or 10),
                "cursor": int(p.get("cursor") or 0),
                "source": "explore",
            },
        )
        items = data.get("itemList") or []
        return {"items": [_video(v) for v in items], "cursor": data.get("cursor")}

    async def _op_recommended_feed(self, backend, p):
        data = await backend.fetch_json(
            "/api/prefetch/recommend/item_list/",
            {
                "count": int(p.get("count") or 10),
                "cursor": int(p.get("cursor") or 0),
                "source": "recommend",
            },
        )
        items = data.get("itemList") or []
        return {"items": [_video(v) for v in items], "cursor": data.get("cursor")}

    async def _op_search_users(self, backend, p):
        data = await backend.fetch_json(
            "/api/search/user/full/",
            {
                "keyword": p["keyword"],
                "count": int(p.get("count") or 12),
                "cursor": int(p.get("cursor") or 0),
                "search_id": "",
            },
        )
        items = data.get("userInfoList") or data.get("user_list") or []
        return {"users": [_user(u) for u in items], "cursor": data.get("cursor")}

    async def _op_search_videos(self, backend, p):
        data = await backend.fetch_json(
            "/api/search/item/full/",
            {
                "keyword": p["keyword"],
                "count": int(p.get("count") or 20),
                "cursor": int(p.get("cursor") or 0),
                "search_id": "",
            },
        )
        items = data.get("item_list") or data.get("itemList") or []
        return {"videos": [_video(v) for v in items], "cursor": data.get("cursor")}

    async def _op_search_general(self, backend, p):
        data = await backend.fetch_json(
            "/api/search/general/full/",
            {
                "keyword": p["keyword"],
                "count": int(p.get("count") or 15),
                "cursor": int(p.get("cursor") or 0),
                "search_id": "",
            },
        )
        return {
            "results": data.get("data") or data.get("items") or [],
            "cursor": data.get("cursor"),
        }

    async def _op_hashtag_videos(self, backend, p):
        data = await backend.fetch_json(
            "/api/challenge/item_list/",
            {
                "challengeName": p["tag"].lstrip("#"),
                "count": int(p.get("count") or 20),
                "cursor": int(p.get("cursor") or 0),
            },
        )
        items = data.get("itemList") or []
        return {"items": [_video(v) for v in items], "cursor": data.get("cursor")}

    async def _op_hashtag_info(self, backend, p):
        data = await backend.fetch_json(
            "/api/challenge/detail/",
            {"challengeName": p["tag"].lstrip("#")},
        )
        info = data.get("challengeInfo") or {}
        stats = info.get("stats") or {}
        return {
            "tag": info.get("challengeName") or p["tag"],
            "title": info.get("title") or "",
            "description": info.get("description") or "",
            "video_count": stats.get("videoCount"),
            "view_count": stats.get("viewCount"),
            "user_count": stats.get("userCount"),
        }

    async def _op_music_info(self, backend, p):
        data = await backend.fetch_json(
            "/api/music/detail/",
            {"musicId": p["music_id"]},
        )
        info = data.get("musicInfo") or {}
        return {
            "id": info.get("id"),
            "title": info.get("title") or "",
            "author": info.get("authorName") or "",
            "album": info.get("albumName") or "",
            "duration": info.get("duration"),
            "usage_count": (info.get("stats") or {}).get("useCount"),
        }

    async def _op_trending(self, backend, p):
        data = await backend.fetch_json(
            "/api/trending/searchwords/", {"count": int(p.get("count") or 20)}
        )
        words = data.get("data") or data.get("word_list") or []
        return {"words": words}

    async def _op_suggested_users(self, backend, p):
        data = await backend.fetch_json(
            "/api/recommend/user/",
            {
                "count": int(p.get("count") or 20),
                "cursor": int(p.get("cursor") or 0),
                "source": "personal",
            },
        )
        items = data.get("userInfoList") or data.get("users") or []
        return {"users": [_user(u) for u in items], "cursor": data.get("cursor")}

    async def _op_friends(self, backend, p):
        data = await backend.fetch_json(
            "/api/friends/item_list/",
            {"count": int(p.get("count") or 30), "cursor": int(p.get("cursor") or 0)},
        )
        items = data.get("userList") or data.get("users") or []
        return {"users": [_user(u) for u in items], "cursor": data.get("cursor")}

    async def _op_account_settings(self, backend, p):
        data = await backend.fetch_json("/api/user/settings/", {})
        settings_block = data.get("settings") or {}
        return {
            "language": settings_block.get("languageSettings", {}).get("language"),
            "im_enabled": settings_block.get("imSet"),
            "private_account": settings_block.get("privateAccount"),
            "pro_account": settings_block.get("proAccountStatus"),
            "push_settings": settings_block.get("pushSettings", {}),
            "comment_filter": settings_block.get("commentFilterStatus"),
            "duet": settings_block.get("allowDuet"),
            "stitch": settings_block.get("allowStitch"),
            "raw_keys": sorted(settings_block.keys()),
        }

    async def _op_username_check(self, backend, p):
        data = await backend.fetch_json(
            "/api/uniqueid/check/",
            {"change_name": p["username"], "check_type": 1},
        )
        return {
            "username": p["username"],
            "available": bool(data.get("status_code") == 0 and data.get("unique_id")),
            "raw": _status(data),
        }

    async def _op_notifications(self, backend, p):
        data = await backend.fetch_json(
            "/api/inbox/notice_list/",
            {
                "count": int(p.get("count") or 20),
                "cursor": int(p.get("cursor") or 0),
                "notice_type": int(p.get("notice_type") or 0),
            },
        )
        notices = data.get("notice_list") or data.get("notices") or []
        return {
            "notices": notices,
            "total": data.get("total"),
            "cursor": data.get("cursor"),
        }

    async def _op_notification_count(self, backend, p):
        data = await backend.fetch_json("/api/inbox/notice_count/", {})
        counts = data.get("notice_count") or []
        return {
            "total": sum(int(c.get("count") or 0) for c in counts),
            "groups": {str(c.get("group")): int(c.get("count") or 0) for c in counts},
        }

    async def _op_dm_conversations(self, backend, p):
        conversations = await backend.im_conversations()
        return {"conversations": conversations, "count": len(conversations)}

    # -- write operations ------------------------------------------------
    async def _op_like(self, backend, p):
        return await self._op_digg(backend, p, digg_type=1)

    async def _op_unlike(self, backend, p):
        return await self._op_digg(backend, p, digg_type=0)

    async def _op_digg(self, backend, p, digg_type: int):
        # The site sends the like state as `type`: 1 = like, 0 = unlike, and
        # carries every parameter in the query string, not the body.
        data = await backend.post_json(
            "/api/commit/item/digg/",
            {"aweme_id": p["video_id"], "type": digg_type},
        )
        return {"video_id": p["video_id"], "liked": digg_type == 1, "raw": _status(data)}

    async def _op_comment(self, backend, p):
        """Publish a comment on a video.

        The write API answers with an empty body from some IPs — TikTok
        refuses state-changing calls from datacenter ranges without saying
        so. When that happens the comment is published through the page's
        own composer instead, which the site builds and signs itself.

        The composer is drawn by TikTok's React application, so that route
        needs the heavy bundles and roughly 700 MB of memory. On a 512 MB
        instance there is no room for it and the service would be
        OOM-killed, so the attempt is skipped and the reason is reported
        instead.
        """
        try:
            data = await backend.post_json(
                "/api/comment/publish/",
                {
                    "aweme_id": p["video_id"],
                    "text": p["text"],
                    "comment_type": 0,
                    "reply_id": 0,
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.info(
                "the comment API did not answer (%s) — using the page", exc
            )
            free = free_memory_mb()
            needed = settings.ui_fallback_min_free_mb
            if free is not None and free < needed:
                reason = (
                    f"the comment API was refused and the page's own composer "
                    f"needs about {needed} MB of free memory to boot "
                    f"(only {free} MB free) — raise the memory of the "
                    f"instance or post the comment by hand"
                )
                log.info(reason)
                return {
                    "video_id": p["video_id"],
                    "comment_id": "",
                    "comment": {"status_code": 13, "status_msg": reason},
                    "via": "none",
                    "detail": reason,
                }
            result = await backend.post_comment_via_ui(p["video_id"], p["text"])
            return {
                "video_id": p["video_id"],
                "comment_id": "",
                "comment": {"status_code": 0, "status_msg": "published through the page"},
                "via": "ui",
                "detail": result,
            }
        cid = ""
        if isinstance(data, dict):
            payload = data.get("comment") or data.get("data") or data
            if isinstance(payload, dict):
                cid = payload.get("cid") or payload.get("id") or ""
        return {
            "video_id": p["video_id"],
            "comment_id": cid,
            "comment": _status(data),
            "via": "api",
        }

    async def _op_comment_reply(self, backend, p):
        data = await backend.post_json(
            "/api/comment/publish/",
            {
                "aweme_id": p["video_id"],
                "text": p["text"],
                "comment_type": 0,
                "reply_id": p["comment_id"],
            },
        )
        cid = ""
        if isinstance(data, dict):
            payload = data.get("comment") or data.get("data") or data
            if isinstance(payload, dict):
                cid = payload.get("cid") or payload.get("id") or ""
        return {"video_id": p["video_id"], "comment_id": cid, "raw": _status(data)}

    async def _op_comment_delete(self, backend, p):
        data = await backend.post_json(
            "/api/comment/delete/",
            {"aweme_id": p["video_id"], "cid": p["comment_id"]},
        )
        return {"video_id": p["video_id"], "comment_id": p["comment_id"], "raw": _status(data)}

    async def _op_comment_digg(self, backend, p):
        data = await backend.post_json(
            "/api/comment/digg/",
            {"cid": p["comment_id"], "aweme_id": p["video_id"], "digg_type": int(p.get("digg_type") or 1)},
        )
        return {"comment_id": p["comment_id"], "raw": _status(data)}

    async def _op_follow(self, backend, p):
        return await self._follow(backend, p, follow_type=0)

    async def _op_unfollow(self, backend, p):
        return await self._follow(backend, p, follow_type=1)

    async def _follow(self, backend, p, follow_type: int):
        data = await backend.post_json(
            "/api/commit/follow/user/",
            {"user_id": p["user_id"], "sec_uid": p["sec_uid"], "type": follow_type},
        )
        return {
            "user_id": p["user_id"],
            "following": follow_type == 0,
            "raw": _status(data),
        }

    async def _op_favorite(self, backend, p):
        data = await backend.post_json(
            "/api/commit/item/favorite/",
            {"aweme_id": p["video_id"], "action": 1},
        )
        return {"video_id": p["video_id"], "saved": True, "raw": _status(data)}

    async def _op_unfavorite(self, backend, p):
        data = await backend.post_json(
            "/api/commit/item/favorite/",
            {"aweme_id": p["video_id"], "action": 0},
        )
        return {"video_id": p["video_id"], "saved": False, "raw": _status(data)}

    async def _op_share(self, backend, p):
        data = await backend.post_json(
            "/api/commit/aweme/share/",
            {"aweme_id": p["video_id"], "count": int(p.get("count") or 1)},
        )
        return {"video_id": p["video_id"], "raw": _status(data)}

    async def _op_update_profile(self, backend, p):
        payload = {}
        for key in ("signature", "nickname", "avatar"):
            if p.get(key):
                payload[key] = p[key]
        if not payload:
            raise ValueError("update_profile needs at least one of signature, nickname, avatar")
        data = await backend.post_json("/api/update/profile/", payload)
        return {"updated": sorted(payload.keys()), "raw": _status(data)}


def _looks_like_auth_failure(exc: Exception) -> bool:
    """Does this error mean the session is no longer valid?"""
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "empty body",       # the signature/token was rejected
            "10202",            # TikTok's "not logged in" status
            "10234",            # TikTok's "not logged in" status (new)
            "illegal id",
            "invalid session",
            "not logged in",
        )
    )


def _status(data: Any) -> dict:
    """Keep only the interesting part of a write response."""
    if not isinstance(data, dict):
        return {"value": data}
    return {
        "status_code": data.get("status_code", data.get("statusCode", 0)),
        "status_msg": data.get("status_msg", data.get("statusMsg", "")),
    }


__all__ = ["TikTokAdapter", "UnsupportedOperation", "BackendUnavailable", "OPERATIONS"]
