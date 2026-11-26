"""Animation timeline.

Assigns an animation to every element of every page. The rules keep the
result readable: elements enter one after another instead of all at once,
text is fully visible long enough to be read, and nothing animates so
hardly that it distracts from the words.
"""
from __future__ import annotations

import random

from video_engine.animation.effects import (
    FADE_IN,
    FADE_OUT,
    FLOAT,
    LINE_REVEAL,
    NONE,
    ROTATE,
    SLIDE_IN,
    SLIDE_OUT,
    TYPEWRITER,
    WORD_REVEAL,
    ZOOM,
    AnimationSpec,
)
from video_engine.design.model import (
    KIND_DECORATION,
    KIND_IMAGE,
    KIND_SURFACE,
    KIND_TEXT,
    ROLE_CAPTION_TEXT,
    ROLE_DECORATION,
    ROLE_PRIMARY_TEXT,
    ROLE_SECONDARY_TEXT,
    Composition,
    Element,
)

# The text must stay fully visible for at least this long before it moves on.
READING_HOLD = 2.2

TEXT_REVEALS = {
    "diary": (TYPEWRITER, LINE_REVEAL),
    "scrapbook": (WORD_REVEAL, LINE_REVEAL),
    "memory": (LINE_REVEAL, TYPEWRITER),
    "editorial": (LINE_REVEAL, NONE),
    "letter": (TYPEWRITER, LINE_REVEAL),
    "minimal": (LINE_REVEAL, NONE),
    "playful": (WORD_REVEAL, TYPEWRITER),
}


def _reveal_duration(text: str) -> float:
    """Typing speed is roughly 30 characters per second, clamped."""
    return max(1.1, min(4.5, len(text) / 30.0))


def assign_animations(composition: Composition, rng: random.Random) -> Composition:
    """Give every element an animation, page by page."""
    identity = composition.identity
    reveals = TEXT_REVEALS.get(identity, (LINE_REVEAL, NONE))

    for page in composition.pages:
        page_duration = max(2.0, page.duration)
        ordered = sorted(page.elements, key=lambda e: (e.z_index, e.y))
        stagger = 0.14
        cursor = 0.0

        for element in ordered:
            spec = AnimationSpec()

            if element.kind == KIND_TEXT:
                if element.role == ROLE_PRIMARY_TEXT:
                    kind = rng.choice(reveals)
                    if kind == NONE:
                        kind = LINE_REVEAL
                    duration = _reveal_duration(str(element.payload.get("text", "")))
                    spec = AnimationSpec(
                        type=kind,
                        start=min(0.6, cursor),
                        duration=duration,
                        easing="ease_out",
                    )
                    cursor += 0.2
                elif element.role in (ROLE_SECONDARY_TEXT, ROLE_CAPTION_TEXT):
                    spec = AnimationSpec(
                        type=rng.choice([FADE_IN, SLIDE_IN]),
                        start=min(0.8, cursor),
                        duration=0.55,
                        direction=rng.choice(["up", "left", "right", "down"]),
                        easing="ease_out",
                    )
                    cursor += 0.1
                else:
                    spec = AnimationSpec(type=FADE_IN, start=0.0, duration=0.6)

            elif element.kind == KIND_IMAGE:
                spec = AnimationSpec(
                    type=rng.choice([SLIDE_IN, FADE_IN, ZOOM]),
                    start=min(0.5, cursor),
                    duration=rng.uniform(0.7, 1.3),
                    direction=rng.choice(["up", "left", "right", "down"]),
                    easing="ease_out",
                    amplitude=0.6,
                )
                if spec.type == ZOOM:
                    spec.start = 0.0
                    spec.duration = page_duration
                cursor += 0.12

            elif element.kind == KIND_SURFACE:
                spec = AnimationSpec(
                    type=rng.choice([FADE_IN, SLIDE_IN]),
                    start=min(0.45, cursor),
                    duration=rng.uniform(0.5, 0.9),
                    direction=rng.choice(["up", "down", "left", "right"]),
                    easing="ease_out",
                )
                cursor += 0.08

            elif element.kind == KIND_DECORATION:
                spec = AnimationSpec(
                    type=rng.choice([FADE_IN, FLOAT, ROTATE, NONE]),
                    start=rng.uniform(0.4, min(2.0, page_duration * 0.6)),
                    duration=rng.uniform(0.6, 1.4),
                    direction=rng.choice(["up", "down"]),
                    easing="ease_out",
                    amplitude=rng.uniform(0.4, 1.0),
                )
                if spec.type in (FLOAT, ROTATE):
                    spec.duration = page_duration
                    spec.start = 0.0

            element.animation = {
                "type": spec.type,
                "start": round(spec.start, 3),
                "duration": round(spec.duration, 3),
                "direction": spec.direction,
                "easing": spec.easing,
                "amplitude": round(spec.amplitude, 3),
            }

        # Everything that is not text fades out gently at the very end.
        for element in page.elements:
            if element.kind == KIND_TEXT and element.role == ROLE_PRIMARY_TEXT:
                continue
            if element.kind == KIND_DECORATION:
                continue
            element.animation = dict(element.animation or {})
            element.animation.setdefault("type", FADE_IN)
            element.animation["out"] = {
                "type": FADE_OUT,
                "start": max(0.0, page_duration - 0.5),
                "duration": 0.45,
            }

    return composition


def total_duration(composition: Composition) -> float:
    return sum(page.duration for page in composition.pages)
