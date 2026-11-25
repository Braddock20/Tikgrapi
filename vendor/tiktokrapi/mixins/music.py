"""Music (sound) endpoints."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..extractors import extract_videos
from ..types import Video

if TYPE_CHECKING:
    from .. import Client


class MusicMixin:
    self: "Client"

    def music_detail(self, music_id: str) -> dict:
        return self.http.request(
            "/api/music/detail/",
            params={"musicId": music_id},
            from_page="music",
        )

    def music_videos(
        self, music_id: str, *, count: int = 30, cursor: int = 0
    ) -> tuple[list[Video], int, bool]:
        data = self.http.request(
            "/api/music/item_list/",
            params={"musicID": music_id, "count": count, "cursor": cursor},
            from_page="music",
        )
        return (
            extract_videos(data.get("itemList", [])),
            int(data.get("cursor", 0)),
            bool(data.get("hasMore", False)),
        )
