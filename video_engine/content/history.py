"""Content history helpers.

A thin façade over :mod:`video_engine.storage.database` so the rest of the
engine does not have to know how history is stored.
"""
from __future__ import annotations

from video_engine.content.loader import ContentItem
from video_engine.storage.database import HistoryStore


class ContentHistory:
    def __init__(self, store: HistoryStore) -> None:
        self.store = store

    def usage(self) -> dict[str, int]:
        return self.store.content_used()

    def is_used(self, content_id: str) -> bool:
        return self.store.content_used().get(content_id, 0) > 0

    def mark_used(self, item: ContentItem, video_id: str, published: bool) -> None:
        self.store.mark_content_used(item.id, item.category, video_id, published)

    def release(self, item: ContentItem) -> None:
        """Undo a usage mark after a failed render."""
        self.store.release_content(item.id)
