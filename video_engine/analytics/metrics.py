"""Engagement metrics.

Normalised rates make videos of different sizes comparable: "likes per
1,000 views" says more about a video than a raw like count does.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Engagement:
    """The normalised engagement of one video."""

    video_id: str = ""
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    watch_time: float | None = None
    completion_rate: float | None = None

    @property
    def likes_per_1k(self) -> float:
        return _per_1k(self.likes, self.views)

    @property
    def comments_per_1k(self) -> float:
        return _per_1k(self.comments, self.views)

    @property
    def shares_per_1k(self) -> float:
        return _per_1k(self.shares, self.views)

    @property
    def saves_per_1k(self) -> float:
        return _per_1k(self.saves, self.views)

    @property
    def total_per_1k(self) -> float:
        return self.likes_per_1k + self.comments_per_1k + self.shares_per_1k + self.saves_per_1k

    @property
    def has_signal(self) -> bool:
        return self.views >= 100

    def as_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "views": self.views,
            "likes": self.likes,
            "comments": self.comments,
            "shares": self.shares,
            "saves": self.saves,
            "likes_per_1k": round(self.likes_per_1k, 3),
            "comments_per_1k": round(self.comments_per_1k, 3),
            "shares_per_1k": round(self.shares_per_1k, 3),
            "saves_per_1k": round(self.saves_per_1k, 3),
            "total_per_1k": round(self.total_per_1k, 3),
            "watch_time": self.watch_time,
            "completion_rate": self.completion_rate,
        }


def _per_1k(value: int, views: int) -> float:
    if views <= 0:
        return 0.0
    return (value / views) * 1000.0


def collect(video_id: str, payload: dict) -> Engagement:
    """Build an :class:`Engagement` from a raw metrics payload."""
    def number(key: str) -> int:
        value = payload.get(key, 0)
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def optional(key: str) -> float | None:
        value = payload.get(key)
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    return Engagement(
        video_id=video_id,
        views=number("views"),
        likes=number("likes"),
        comments=number("comments"),
        shares=number("shares"),
        saves=number("saves"),
        watch_time=optional("watch_time"),
        completion_rate=optional("completion_rate"),
    )
