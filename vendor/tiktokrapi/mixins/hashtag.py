"""Hashtag (challenge) endpoints."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..extractors import extract_videos
from ..types import Video

if TYPE_CHECKING:
    from .. import Client


class HashtagMixin:
    self: "Client"

    def hashtag_detail(self, name: str) -> dict:
        return self.http.request(
            "/api/challenge/detail/",
            params={"challengeName": name},
            from_page="hashtag",
        )

    def hashtag_videos(
        self, challenge_id: str, *, count: int = 30, cursor: int = 0
    ) -> tuple[list[Video], int, bool]:
        data = self.http.request(
            "/api/challenge/item_list/",
            params={"challengeID": challenge_id, "count": count, "cursor": cursor},
            from_page="hashtag",
        )
        return (
            extract_videos(data.get("itemList", [])),
            int(data.get("cursor", 0)),
            bool(data.get("hasMore", False)),
        )
