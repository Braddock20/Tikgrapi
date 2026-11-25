"""Comment endpoints."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..extractors import extract_comment
from ..types import Comment

if TYPE_CHECKING:
    from .. import Client


class CommentMixin:
    self: "Client"

    def comments(
        self, aweme_id: str, *, count: int = 20, cursor: int = 0
    ) -> tuple[list[Comment], int, bool]:
        data = self.http.request(
            "/api/comment/list/",
            params={"aweme_id": aweme_id, "count": count, "cursor": cursor},
            from_page="video",
        )
        items = [extract_comment(c) for c in data.get("comments", []) if c]
        return items, int(data.get("cursor", 0)), bool(data.get("has_more", 0))

    def comment(self, aweme_id: str, text: str) -> dict:
        return self.http.request(
            "/api/comment/publish/",
            method="POST",
            params={"aweme_id": aweme_id, "text": text},
            from_page="fyp",
        )

    def comment_delete(self, *, cid: str, aweme_id: str) -> dict:
        return self.http.request(
            "/api/comment/delete/",
            method="POST",
            params={"cid": cid, "aweme_id": aweme_id},
            from_page="fyp",
        )

    def comment_like(self, *, cid: str, aweme_id: str) -> dict:
        return self.http.request(
            "/api/comment/digg/",
            method="POST",
            params={"cid": cid, "aweme_id": aweme_id, "digg_type": 1},
            from_page="fyp",
        )

    def comment_unlike(self, *, cid: str, aweme_id: str) -> dict:
        return self.http.request(
            "/api/comment/digg/",
            method="POST",
            params={"cid": cid, "aweme_id": aweme_id, "digg_type": 0},
            from_page="fyp",
        )
