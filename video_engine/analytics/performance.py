"""Performance-driven selection.

Analytics are optional and never interfere with generation. When there is
enough data, the engine nudges the probability of the identities, layouts
and sounds that actually perform — but only slightly, so the output keeps
its variety.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from video_engine.analytics.metrics import Engagement
from video_engine.storage.database import HistoryStore

log = logging.getLogger(__name__)

# The most a good result can lift a choice's probability.
MAX_WEIGHT = 1.6
MIN_WEIGHT = 0.6


@dataclass
class PerformanceIndex:
    """Engagement scores grouped by the keys the engine chooses on."""

    by_identity: dict[str, float]
    by_layout: dict[str, float]
    by_category: dict[str, float]
    by_sound: dict[str, float]
    samples: int = 0

    def weight_for(self, table: dict[str, float], key: str) -> float:
        """A multiplier in ``[MIN_WEIGHT, MAX_WEIGHT]`` for a choice."""
        score = table.get(key)
        if score is None:
            return 1.0
        return max(MIN_WEIGHT, min(MAX_WEIGHT, score))


class PerformanceTracker:
    """Reads and writes performance records."""

    def __init__(self, store: HistoryStore) -> None:
        self.store = store

    def record(self, payload: dict) -> None:
        """Store one video's performance."""
        self.store.record_performance(payload)

    def index(self) -> PerformanceIndex:
        """Build the index used to bias future selections."""
        rows = self.store.performance_rows()
        buckets: dict[str, dict[str, list[float]]] = {
            "identity": defaultdict(list),
            "layout": defaultdict(list),
            "category": defaultdict(list),
            "sound": defaultdict(list),
        }
        usable = 0
        for row in rows:
            views = int(row.get("views") or 0)
            if views < 100:
                # A handful of views says nothing about performance.
                continue
            engagement = Engagement(
                video_id=str(row.get("video_id") or ""),
                views=views,
                likes=int(row.get("likes") or 0),
                comments=int(row.get("comments") or 0),
                shares=int(row.get("shares") or 0),
                saves=int(row.get("saves") or 0),
            )
            if not engagement.has_signal:
                continue
            usable += 1
            score = engagement.total_per_1k
            if row.get("visual_identity"):
                buckets["identity"][row["visual_identity"]].append(score)
            if row.get("layout"):
                buckets["layout"][row["layout"]].append(score)
            if row.get("category"):
                buckets["category"][row["category"]].append(score)
            if row.get("sound_id"):
                buckets["sound"][row["sound_id"]].append(score)

        def summarise(table: dict[str, list[float]]) -> dict[str, float]:
            out: dict[str, float] = {}
            if not table:
                return out
            averages = {key: sum(values) / len(values) for key, values in table.items()}
            if not averages:
                return out
            overall = sum(averages.values()) / len(averages)
            if overall <= 0:
                return out
            for key, value in averages.items():
                # 1.0 means "as good as the average".
                out[key] = max(MIN_WEIGHT, min(MAX_WEIGHT, value / overall))
            return out

        return PerformanceIndex(
            by_identity=summarise(buckets["identity"]),
            by_layout=summarise(buckets["layout"]),
            by_category=summarise(buckets["category"]),
            by_sound=summarise(buckets["sound"]),
            samples=usable,
        )
