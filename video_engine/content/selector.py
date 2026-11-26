"""Mood classification and content selection.

The mood of a piece of content is derived from its own metadata first and
from a deterministic keyword lexicon second. Nothing here calls a language
model — the rules are fixed and reproducible.
"""
from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from video_engine.content.loader import ContentItem

# --- lexicon -------------------------------------------------------------
LEXICON: dict[str, tuple[str, ...]] = {
    "nostalgic": (
        "remember", "used to", "years ago", "back then", "childhood", "old",
        "missing", "miss", "once", "grew up", "summer", "grandma", "grandpa",
        "those days", "before", "memory", "memorys", "memories", "nostalgia",
    ),
    "melancholic": (
        "sad", "lonely", "alone", "cry", "cried", "tears", "loss", "lost",
        "empty", "missing you", "goodbye", "left", "over", "hurt", "ache",
        "broke", "broken", "regret", "sorry",
    ),
    "romantic": (
        "love", "loved", "loving", "heart", "kiss", "fall for", "crush",
        "hands", "held", "together", "forever", "you and me", "sweet",
    ),
    "reflective": (
        "why", "think", "realise", "realize", "lesson", "learned", "wonder",
        "maybe", "perhaps", "believe", "truth", "meaning", "life", "question",
        "what if", "honestly", "maybe",
    ),
    "hopeful": (
        "better", "tomorrow", "future", "again", "heal", "hope", "one day",
        "begin", "start", "try", "grow", "growth", "someday", "everything will",
    ),
    "calm": (
        "quiet", "slow", "peace", "peaceful", "breathe", "still", "morning",
        "coffee", "soft", "gentle", "rest",
    ),
    "energetic": (
        "go", "run", "jump", "dance", "energy", "excited", "yolo", "hustle",
        "grind", "party", "weekend", "yes",
    ),
    "funny": (
        "lol", "haha", "funny", "weird", "awkward", "oops", "me", "my",
        "nobody", "nobody cares", "why do", "how to",
    ),
    "mysterious": (
        "strange", "secret", "nobody knows", "whisper", "shadow", "why did",
        "hidden", "unknown",
    ),
    "intense": (
        "everything", "hate", "love", "burn", "break", "deep", "forever",
        "obsessed", "crazy", "wild",
    ),
}

INTENSITY_WORDS = {
    "high": ("everything", "hate", "obsessed", "never", "always", "deeply", "crazy", "destroyed"),
    "low": ("sometimes", "maybe", "quiet", "small", "little", "slightly"),
}

QUESTION_MARKERS = ("?",)
CONVERSATION_MARKERS = ("what do you", "have you ever", "tell me", "do you", "would you", "ever")


def classify_mood(item: ContentItem) -> tuple[str, float]:
    """Return ``(mood, confidence)`` for a content item."""
    if item.mood and item.mood != "neutral":
        return item.mood, 0.9

    text = item.text.lower()
    scores: dict[str, int] = {}
    for mood, words in LEXICON.items():
        hits = sum(1 for word in words if word in text)
        if hits:
            scores[mood] = hits
    if not scores:
        return "neutral", 0.3
    best = max(scores.items(), key=lambda kv: (kv[1], -list(LEXICON).index(kv[0])))
    total = sum(scores.values())
    return best[0], min(0.95, 0.45 + 0.1 * best[1] + 0.1 * total)


def classify_intensity(item: ContentItem) -> str:
    if item.intensity and item.intensity != "medium":
        return item.intensity
    text = item.text.lower()
    for level, words in INTENSITY_WORDS.items():
        if any(word in text for word in words):
            return level
    if len(item.text) > 260:
        return "high"
    if len(item.text) < 90:
        return "low"
    return "medium"


def classify_category(item: ContentItem) -> str:
    """The declared category wins, unless the text clearly says otherwise."""
    text = item.text.lower()
    if item.category != "thought":
        return item.category
    if any(marker in text for marker in QUESTION_MARKERS) and len(text) < 120:
        return "question"
    if any(marker in text for marker in CONVERSATION_MARKERS):
        return "conversation"
    if len(text) > 320:
        return "story"
    return "thought"


def content_type_for(item: ContentItem) -> str:
    """Maps a content item onto one of the target duration buckets."""
    category = classify_category(item)
    if category == "story" or len(item.text) > 300:
        return "story"
    if category in {"memory", "nostalgia"}:
        return "memory"
    if category == "caption":
        return "caption"
    if category in {"question", "conversation"}:
        return "question"
    if len(item.text) > 160:
        return "diary"
    return "short"


DURATION_RANGES: dict[str, tuple[float, float]] = {
    "short": (7.0, 10.0),
    "caption": (7.0, 10.0),
    "question": (8.0, 12.0),
    "diary": (12.0, 15.0),
    "memory": (15.0, 20.0),
    "story": (20.0, 30.0),
}


def target_duration(content_type: str, hint: float | None = None, default: float = 15.0) -> float:
    if hint and 4.0 <= hint <= 60.0:
        return float(hint)
    low, high = DURATION_RANGES.get(content_type, (default - 2, default + 2))
    midpoint = (low + high) / 2
    return max(low, min(high, midpoint))


# --- selection -----------------------------------------------------------
@dataclass
class Selection:
    item: ContentItem
    mood: str
    intensity: str
    category: str
    content_type: str
    duration: float
    reason: str = ""


class ContentSelector:
    """Picks unused content, honouring the usage history.

    Selection is seeded, so a run can be reproduced exactly.
    """

    def __init__(
        self,
        items: Sequence[ContentItem],
        usage: dict[str, int] | None = None,
        *,
        allow_reuse: bool = False,
        auto_recycle: bool = True,
        seed: int = 0,
        avoid_categories: Iterable[str] = (),
    ) -> None:
        self.items = list(items)
        self.usage = dict(usage or {})
        self.allow_reuse = allow_reuse
        self.auto_recycle = auto_recycle
        self.seed = seed
        self.avoid_categories = set(avoid_categories)
        self._counter = 0
        self.recycled = False

    def _rng(self, salt: str = "") -> random.Random:
        self._counter += 1
        digest = hashlib.sha256(f"{self.seed}:{salt}:{self._counter}".encode()).hexdigest()
        return random.Random(int(digest[:16], 16))

    def candidates(self) -> list[ContentItem]:
        if self.allow_reuse:
            return [i for i in self.items if classify_category(i) not in self.avoid_categories]
        return [
            i
            for i in self.items
            if self.usage.get(i.id, 0) == 0 and classify_category(i) not in self.avoid_categories
        ]

    def _recycle_pool(self) -> list[ContentItem]:
        """The items that were used least often — used when the library runs dry.

        Recycling keeps the system running unattended: the same words come
        back, but the engine composes them differently every time.
        """
        pool = [i for i in self.items if classify_category(i) not in self.avoid_categories]
        if not pool:
            pool = list(self.items)
        pool.sort(key=lambda item: (self.usage.get(item.id, 0), item.id))
        # Only the least-used third is eligible, so the rotation still moves.
        limit = max(1, len(pool) // 3)
        return pool[:limit]

    def select(self, *, mood: str | None = None, category: str | None = None) -> Selection | None:
        pool = self.candidates()
        self.recycled = False
        if not pool:
            if not (self.allow_reuse or self.auto_recycle):
                return None
            pool = self._recycle_pool()
            self.recycled = True
        if not pool:
            return None

        rng = self._rng("select")
        weighted: list[tuple[ContentItem, float]] = []
        for item in pool:
            weight = 1.0
            item_mood, _ = classify_mood(item)
            if mood and item_mood == mood:
                weight += 3.0
            if category and classify_category(item) == category:
                weight += 2.5
            weighted.append((item, weight))

        total = sum(w for _, w in weighted)
        pick = rng.uniform(0, total)
        chosen = weighted[-1][0]
        upto = 0.0
        for item, weight in weighted:
            upto += weight
            if pick <= upto:
                chosen = item
                break

        item_mood, confidence = classify_mood(chosen)
        content_type = content_type_for(chosen)
        return Selection(
            item=chosen,
            mood=item_mood,
            intensity=classify_intensity(chosen),
            category=classify_category(chosen),
            content_type=content_type,
            duration=target_duration(content_type, chosen.duration_hint),
            reason=f"mood={item_mood}({confidence:.2f})",
        )
