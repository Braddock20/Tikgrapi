"""Search."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .. import Client


# TikTok feature-flag blob — sent as URL-encoded JSON. Static across sessions.
_WEB_SEARCH_CODE = json.dumps({
    "tiktok": {
        "client_params_x": {
            "search_engine": {
                "ies_mt_user_live_video_card_use_libra": 1,
                "mt_search_general_user_live_card": 1,
            }
        },
        "search_server": {},
    }
}, separators=(",", ":"))


class SearchMixin:
    self: "Client"

    def search(self, keyword: str, *, cursor: int = 0, count: int = 12) -> dict:
        """General search (videos + users + sounds + hashtags)."""
        return self.http.request(
            "/api/search/general/full/",
            params={
                "keyword": keyword,
                "cursor": cursor,
                "offset": cursor,  # offset mirrors cursor for general search
                "count": count,
                "device_type": "web_h265",
                "is_non_personalized_search": 0,
                "web_search_code": _WEB_SEARCH_CODE,
            },
            from_page="search",
        )

    def search_users(self, keyword: str, *, cursor: int = 0, count: int = 12) -> dict:
        return self.http.request(
            "/api/search/user/full/",
            params={
                "keyword": keyword,
                "cursor": cursor,
                "offset": cursor,
                "count": count,
                "device_type": "web_h265",
                "is_non_personalized_search": 0,
                "web_search_code": _WEB_SEARCH_CODE,
            },
            from_page="search",
        )
