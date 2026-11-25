"""FYP, preload, related feeds."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..extractors import extract_videos
from ..types import Video

if TYPE_CHECKING:
    from .. import Client


class FeedMixin:
    self: "Client"

    def fyp(self, *, count: int = 6) -> list[Video]:
        """For You Page feed."""
        params = {
            "count": count,
            "coverFormat": 2,
            "isNonPersonalized": "false",
            "pullType": 1,
            "vv_count": 0,
            "vv_count_fyp": 0,
            "video_encoding": "dash",
            "showAds": "true",
            "showAboutThisAd": "true",
        }
        data = self.http.request("/api/recommend/item_list/", params=params, from_page="fyp")
        return extract_videos(data.get("itemList", []))

    def preload(self, *, count: int = 3) -> list[Video]:
        params = {"count": count, "coverFormat": 2, "video_encoding": "dash"}
        data = self.http.request("/api/preload/item_list/", params=params, from_page="fyp")
        return extract_videos(data.get("itemList", []))

    def related(self, aweme_id: str, *, count: int = 16) -> list[Video]:
        params = {"itemID": aweme_id, "count": count, "coverFormat": 2}
        data = self.http.request("/api/related/item_list/", params=params, from_page="video")
        return extract_videos(data.get("itemList", []))
