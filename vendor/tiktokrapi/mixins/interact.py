"""Like, follow, favorite — all are POST commits."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .. import Client


class InteractMixin:
    self: "Client"

    def like(self, aweme_id: str) -> dict:
        return self.http.request(
            "/api/commit/item/digg/",
            method="POST",
            params={"aweme_id": aweme_id, "type": 1},
            from_page="fyp",
        )

    def unlike(self, aweme_id: str) -> dict:
        return self.http.request(
            "/api/commit/item/digg/",
            method="POST",
            params={"aweme_id": aweme_id, "type": 0},
            from_page="fyp",
        )

    def follow(self, *, user_id: str, sec_user_id: str) -> dict:
        return self.http.request(
            "/api/commit/follow/user/",
            method="POST",
            params={
                "user_id": user_id,
                "sec_user_id": sec_user_id,
                "action_type": 1,
                "channel_id": 0,
                "from": 18,
                "fromWeb": 1,
                "from_pre": 0,
                "type": 1,
            },
            from_page="fyp",
        )

    def unfollow(self, *, user_id: str, sec_user_id: str) -> dict:
        return self.http.request(
            "/api/commit/follow/user/",
            method="POST",
            params={
                "user_id": user_id,
                "sec_user_id": sec_user_id,
                "action_type": 0,
                "channel_id": 0,
                "from": 18,
                "fromWeb": 1,
                "from_pre": 0,
                "type": 1,
            },
            from_page="fyp",
        )

    def favorite(self, aweme_id: str, *, sec_uid: str) -> dict:
        return self.http.request(
            "/api/item/collect/",
            method="POST",
            params={"itemId": aweme_id, "secUid": sec_uid, "action": 1},
            from_page="fyp",
        )

    def unfavorite(self, aweme_id: str, *, sec_uid: str) -> dict:
        return self.http.request(
            "/api/item/collect/",
            method="POST",
            params={"itemId": aweme_id, "secUid": sec_uid, "action": 2},
            from_page="fyp",
        )
