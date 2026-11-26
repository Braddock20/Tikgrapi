"""TikTok adapter — dispatches an operation to the backend that can perform it.

Responsibilities:
  * pick the right backend per operation (see ``app.operations``)
  * serialise TikTok access behind a lock and the shared rate limiter
  * normalise TikTok's payload shapes into stable, compact JSON
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.adapters.base import BackendUnavailable, UnsupportedOperation
from app.config import settings
from app.operations import (
    BACKEND_API,
    BACKEND_HTTP,
    BACKEND_SSR,
    BACKEND_UPLOAD,
    OPERATIONS,
    get as get_operation,
)
from app.ratelimit import RateLimiter


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
    return {
        "id": raw.get("aweme_id") or raw.get("id"),
        "desc": raw.get("desc") or "",
        "create_time": raw.get("createTime") or raw.get("create_time"),
        "play_count": stats.get("playCount"),
        "like_count": stats.get("diggCount"),
        "comment_count": stats.get("commentCount"),
        "share_count": stats.get("shareCount"),
        "author_id": author.get("id") or author.get("uid"),
        "author": author.get("uniqueId") or author.get("unique_id") or author.get("nickname"),
        "author_sec_uid": author.get("secUid") or author.get("sec_uid"),
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
        # unauthenticated, so the session can be re-minted and the call
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

    # -- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        from app.adapters.browser import BrowserBackend
        from app.adapters.http import HttpBackend

        if self.backend_name in ("browser", "auto"):
            self._browser = BrowserBackend()
            try:
                await self._browser.start()
            except Exception:
                await self._browser.stop()
                self._browser = None
                if self.backend_name == "browser":
                    raise
        self._http = HttpBackend()
        try:
            await self._http.start()
        except Exception as exc:  # noqa: BLE001
            # No session uploaded yet is not fatal — the API stays up and the
            # operator can upload one.
            self._http = None
            if self.backend_name == "http":
                raise
            import logging

            logging.getLogger(__name__).warning("HTTP backend unavailable: %s", exc)

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

    # -- helpers ---------------------------------------------------------
    def _uploader_for(self):
        """The upload pipeline is a separate client — it has its own protocol."""
        if self._uploader is None:
            from app.adapters.upload import Uploader

            self._uploader = Uploader()
        return self._uploader

    def _backend_for(self, spec) -> Any:
        if spec.backend == BACKEND_SSR:
            if self._browser is None:
                raise BackendUnavailable(
                    "This operation needs the browser backend (BACKEND=browser)"
                )
            return self._browser
        if spec.backend == BACKEND_HTTP:
            if self._http is None:
                raise BackendUnavailable("HTTP backend is not running")
            return self._http
        if spec.backend == BACKEND_UPLOAD:
            return self._uploader_for()
        if spec.backend == BACKEND_API:
            if self._browser is None:
                raise BackendUnavailable(
                    "This operation needs the browser backend (BACKEND=browser)"
                )
            return self._browser
        raise UnsupportedOperation(f"No backend for {spec.name}")

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
            handler = getattr(self, f"_op_{spec.name}")
            try:
                return await handler(self._backend_for(spec), params or {})
            except Exception as exc:
                if self.on_auth_failure is None or not _looks_like_auth_failure(exc):
                    raise
                # The session died — re-mint it and try once more.
                await self.on_auth_failure()
                return await handler(self._backend_for(spec), params or {})

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
        data = await backend.post_json(
            "/api/comment/publish/",
            {
                "aweme_id": p["video_id"],
                "text": p["text"],
                "comment_type": 0,
                "reply_id": 0,
            },
        )
        cid = ""
        if isinstance(data, dict):
            payload = data.get("comment") or data.get("data") or data
            if isinstance(payload, dict):
                cid = payload.get("cid") or payload.get("id") or ""
        return {
            "video_id": p["video_id"],
            "comment_id": cid,
            "comment": _status(data),
        }

    async def _op_comment_delete(self, backend, p):
        data = await backend.post_json(
            "/api/comment/delete/",
            {"aweme_id": p["video_id"], "cid": p["comment_id"]},
        )
        return {"video_id": p["video_id"], "comment_id": p["comment_id"], "raw": _status(data)}

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


def _looks_like_auth_failure(exc: Exception) -> bool:
    """Does this error mean the session is no longer valid?"""
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "empty body",       # the signature/token was rejected
            "10202",            # TikTok's "not logged in" status
            "illegal id",
            "invalid session",
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
