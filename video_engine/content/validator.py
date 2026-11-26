"""Validation of content records.

A malformed record is skipped, never silently rewritten: the loader keeps
the original wording of everything it accepts.
"""
from __future__ import annotations

import re
import unicodedata

MAX_TEXT = 1200
MIN_TEXT = 2

CATEGORIES = {
    "thought",
    "observation",
    "memory",
    "nostalgia",
    "relationship",
    "feeling",
    "reflection",
    "humour",
    "question",
    "conversation",
    "quote",
    "caption",
    "story",
}

MOODS = {
    "nostalgic",
    "romantic",
    "melancholic",
    "reflective",
    "hopeful",
    "calm",
    "energetic",
    "funny",
    "mysterious",
    "intense",
    "neutral",
}

INTENSITIES = {"low", "medium", "high"}

_WHITESPACE = re.compile(r"\s+")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class ContentError(ValueError):
    """A record cannot be used as-is."""


def normalise_text(text: str) -> str:
    """Collapse whitespace and drop control characters."""
    cleaned = _CONTROL.sub("", text or "")
    cleaned = unicodedata.normalize("NFC", cleaned)
    return _WHITESPACE.sub(" ", cleaned).strip()


def text_fingerprint(text: str) -> str:
    """Stable key used to detect duplicates across files."""
    lowered = normalise_text(text).lower()
    stripped = re.sub(r"[^\w\s]", "", lowered)
    return re.sub(r"\s+", " ", stripped).strip()


def validate_text(text: str) -> str:
    clean = normalise_text(text)
    if len(clean) < MIN_TEXT:
        raise ContentError(f"text too short ({len(clean)} characters)")
    if len(clean) > MAX_TEXT:
        raise ContentError(f"text too long ({len(clean)} characters)")
    return clean


def validate_category(value: str | None) -> str:
    if not value:
        return "thought"
    value = str(value).strip().lower()
    return value if value in CATEGORIES else "thought"


def validate_mood(value: str | None) -> str:
    if not value:
        return "neutral"
    value = str(value).strip().lower()
    if value in MOODS:
        return value
    synonyms = {
        "sad": "melancholic",
        "happy": "hopeful",
        "joy": "hopeful",
        "love": "romantic",
        "funny": "funny",
        "fun": "funny",
        "humor": "funny",
        "humour": "funny",
        "calm": "calm",
        "peaceful": "calm",
        "wistful": "nostalgic",
        "memory": "nostalgic",
        "memories": "nostalgic",
        "thoughtful": "reflective",
        "deep": "intense",
        "energetic": "energetic",
    }
    return synonyms.get(value, "neutral")


def validate_intensity(value: str | None) -> str:
    if not value:
        return "medium"
    value = str(value).strip().lower()
    return value if value in INTENSITIES else "medium"


def validate_format(value: str | None) -> str:
    if not value:
        return "diary"
    value = str(value).strip().lower()
    allowed = {"diary", "scrapbook", "editorial", "letter", "nostalgic", "minimal", "story"}
    return value if value in allowed else "diary"


def validate_tags(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = [p.strip() for p in re.split(r"[,;]", value)]
    elif isinstance(value, (list, tuple, set)):
        parts = [str(p).strip() for p in value]
    else:
        return []
    return [p.lower()[:40] for p in parts if p][:12]
