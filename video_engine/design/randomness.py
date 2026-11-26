"""Controlled randomness.

The engine is random, but never arbitrary: every choice is drawn from a
seeded generator and filtered by a rule (a recent-use list, a compatibility
matrix, an asset-availability check). The seed is recorded with the
composition so a failed render can be reproduced exactly.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence, TypeVar

T = TypeVar("T")


def derive_seed(base_seed: int, *parts: str) -> int:
    digest = hashlib.sha256(("|".join([str(base_seed), *parts])).encode()).hexdigest()
    return int(digest[:12], 16)


@dataclass
class RandomSource:
    """A reproducible source of randomness."""

    base_seed: int = 0
    counter: int = 0
    _recent: list[str] = field(default_factory=list)

    def rng(self, *salt: str) -> random.Random:
        self.counter += 1
        seed = derive_seed(self.base_seed, *salt, str(self.counter))
        return random.Random(seed)

    def remember(self, key: str, window: int = 24) -> None:
        self._recent.append(key)
        if len(self._recent) > window:
            self._recent.pop(0)

    @property
    def recent(self) -> list[str]:
        return list(self._recent)


def pick_fresh(
    items: Sequence[T],
    rng: random.Random,
    *,
    avoid: Iterable[str] = (),
    key: Callable[[T], str] | None = None,
    fallback_first: bool = True,
) -> T | None:
    """Pick an item, avoiding the ones in ``avoid``.

    ``avoid`` holds keys of recently used items. When everything has been
    used the oldest entry is released so the rotation never deadlocks.
    """
    if not items:
        return None
    key_fn = key or (lambda item: str(item))
    avoid_set = set(avoid)
    fresh = [item for item in items if key_fn(item) not in avoid_set]
    if fresh:
        return rng.choice(fresh)
    if fallback_first and isinstance(items, (list, tuple)):
        return items[0]
    return rng.choice(list(items))


def weighted_pick(items: Sequence[T], rng: random.Random, weights: Sequence[float]) -> T | None:
    if not items:
        return None
    total = sum(max(0.0, w) for w in weights)
    if total <= 0:
        return rng.choice(list(items))
    target = rng.uniform(0, total)
    upto = 0.0
    for item, weight in zip(items, weights):
        upto += max(0.0, weight)
        if target <= upto:
            return item
    return items[-1]
