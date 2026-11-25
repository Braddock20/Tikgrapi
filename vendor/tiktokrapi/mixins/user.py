"""User-related endpoints."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..extractors import extract_user, extract_videos
from ..types import User, Video

if TYPE_CHECKING:
    from .. import Client


class UserMixin:
    self: "Client"

    def user_detail(self, *, unique_id: str | None = None, sec_uid: str | None = None) -> User:
        """Fetch full user profile by username or secUid."""
        params = {}
        if unique_id:
            params["uniqueId"] = unique_id
        if sec_uid:
            params["secUid"] = sec_uid
        if not params:
            raise ValueError("provide unique_id or sec_uid")
        data = self.http.request("/api/user/detail/", params=params, from_page="user")
        return extract_user(data["userInfo"])

    def user_posts(
        self,
        sec_uid: str,
        *,
        count: int = 16,
        cursor: int = 0,
    ) -> tuple[list[Video], int, bool]:
        """List a user's videos. Returns (videos, next_cursor, has_more)."""
        params = {
            "secUid": sec_uid,
            "count": count,
            "cursor": cursor,
            "coverFormat": 2,
            "language": "en-GB",
            "needPinnedItemIds": "true",
            "post_item_list_request_type": 0,
            "video_encoding": "dash",
        }
        data = self.http.request("/api/post/item_list/", params=params, from_page="user")
        return (
            extract_videos(data.get("itemList", [])),
            int(data.get("cursor", 0)),
            bool(data.get("hasMore", False)),
        )

    def user_reposts(
        self,
        sec_uid: str,
        *,
        count: int = 30,
        cursor: int = 0,
    ) -> tuple[list[Video], int, bool]:
        params = {
            "secUid": sec_uid,
            "count": count,
            "cursor": cursor,
            "coverFormat": 2,
        }
        data = self.http.request("/api/repost/item_list/", params=params, from_page="user")
        return (
            extract_videos(data.get("itemList", [])),
            int(data.get("cursor", 0)),
            bool(data.get("hasMore", False)),
        )
