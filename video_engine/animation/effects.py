"""Animation effects.

Every element carries its own animation spec. The renderer asks for the
element's state at a given moment and gets back an alpha, an offset, a
scale, a rotation and — for text — how much of the copy is visible.

Nothing here needs a GPU or a browser: it is arithmetic on top of the
static layers Pillow already draws.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# Motion types
FADE_IN = "fade_in"
FADE_OUT = "fade_out"
SLIDE_IN = "slide_in"
SLIDE_OUT = "slide_out"
ZOOM = "zoom"
PAN = "pan"
ROTATE = "rotate"
TYPEWRITER = "typewriter"
WORD_REVEAL = "word_reveal"
LINE_REVEAL = "line_reveal"
FLOAT = "float"
NONE = "none"

TEXT_EFFECTS = {TYPEWRITER, WORD_REVEAL, LINE_REVEAL}

# Easing
EASINGS = {
    "linear": lambda t: t,
    "ease_in": lambda t: t * t,
    "ease_out": lambda t: 1 - (1 - t) ** 2,
    "ease_in_out": lambda t: 3 * t * t - 2 * t * t * t,
    "bounce_out": lambda t: 1 - (1 - t) ** 2 * math.cos(t * math.pi * 1.5),
}


def ease(name: str, t: float) -> float:
    t = min(1.0, max(0.0, t))
    fn = EASINGS.get(name, EASINGS["ease_out"])
    return fn(t)


@dataclass
class ElementState:
    """Where an element is at one moment in time."""

    alpha: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    scale: float = 1.0
    rotation: float = 0.0
    visible_lines: int | None = None
    visible_chars: int | None = None
    visible_words: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "alpha": round(self.alpha, 4),
            "offset_x": round(self.offset_x, 4),
            "offset_y": round(self.offset_y, 4),
            "scale": round(self.scale, 4),
            "rotation": round(self.rotation, 4),
        }


@dataclass
class AnimationSpec:
    """A parsed animation instruction."""

    type: str = NONE
    start: float = 0.0
    duration: float = 0.8
    direction: str = "up"
    easing: str = "ease_out"
    amplitude: float = 1.0
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: dict[str, Any] | None) -> "AnimationSpec":
        if not raw:
            return cls()
        return cls(
            type=str(raw.get("type") or raw.get("animation") or NONE),
            start=float(raw.get("start") or 0.0),
            duration=float(raw.get("duration") or 0.8),
            direction=str(raw.get("direction") or "up"),
            easing=str(raw.get("easing") or "ease_out"),
            amplitude=float(raw.get("amplitude") or 1.0),
            extra={k: v for k, v in raw.items() if k not in {"type", "animation", "start", "duration", "direction", "easing", "amplitude"}},
        )


def _progress(t: float, spec: AnimationSpec) -> float:
    if spec.duration <= 0:
        return 1.0 if t >= spec.start else 0.0
    return min(1.0, max(0.0, (t - spec.start) / spec.duration))


def _slide_vector(direction: str) -> tuple[float, float]:
    return {
        "up": (0.0, 1.0),
        "down": (0.0, -1.0),
        "left": (1.0, 0.0),
        "right": (-1.0, 0.0),
        "forward": (0.0, 0.0),
    }.get(direction, (0.0, 1.0))


def element_state(spec_raw: dict[str, Any] | None, t: float, page_duration: float, base_opacity: float = 1.0) -> ElementState:
    """Compute the state of an element at time ``t`` (seconds into the page)."""
    spec = AnimationSpec.parse(spec_raw)
    state = ElementState(alpha=base_opacity)

    if spec.type in (NONE, "") or spec.duration <= 0:
        return state

    if spec.type == FADE_IN:
        p = ease(spec.easing, _progress(t, spec))
        state.alpha = base_opacity * p
    elif spec.type == FADE_OUT:
        p = ease(spec.easing, _progress(t, spec))
        state.alpha = base_opacity * (1.0 - p)
    elif spec.type == SLIDE_IN:
        p = ease(spec.easing, _progress(t, spec))
        dx, dy = _slide_vector(spec.direction)
        distance = spec.amplitude * 0.18
        state.offset_x = dx * distance * (1 - p)
        state.offset_y = dy * distance * (1 - p)
        state.alpha = base_opacity * min(1.0, p * 1.4)
    elif spec.type == SLIDE_OUT:
        p = ease(spec.easing, _progress(t, spec))
        dx, dy = _slide_vector(spec.direction)
        distance = spec.amplitude * 0.18
        state.offset_x = dx * distance * p
        state.offset_y = dy * distance * p
        state.alpha = base_opacity * max(0.0, 1.0 - p)
    elif spec.type == ZOOM:
        p = ease("linear", min(1.0, max(0.0, t / max(0.1, page_duration))))
        state.scale = 1.0 + 0.06 * spec.amplitude * p
    elif spec.type == PAN:
        p = ease("linear", min(1.0, max(0.0, t / max(0.1, page_duration))))
        dx, dy = _slide_vector(spec.direction)
        state.offset_x = dx * 0.05 * spec.amplitude * p
        state.offset_y = dy * 0.05 * spec.amplitude * p
    elif spec.type == ROTATE:
        p = ease(spec.easing, _progress(t, spec))
        state.rotation = spec.amplitude * (1 - p)
    elif spec.type == FLOAT:
        p = ease("linear", min(1.0, max(0.0, t / max(0.1, page_duration))))
        state.offset_y = math.sin(p * math.tau) * 0.012 * spec.amplitude
        state.offset_x = math.cos(p * math.tau * 0.7) * 0.006 * spec.amplitude
    elif spec.type in TEXT_EFFECTS:
        # The text is always fully readable at the end of its reveal.
        p = _progress(t, spec)
        state.alpha = base_opacity * min(1.0, p * 3.0)
        if spec.type == TYPEWRITER:
            state.visible_chars = p
        elif spec.type == WORD_REVEAL:
            state.visible_words = p
        elif spec.type == LINE_REVEAL:
            state.visible_lines = p

    return state


def clip_text(
    lines: list[str],
    state: ElementState,
) -> list[str]:
    """Apply a reveal effect to the text lines."""
    if state.visible_chars is None and state.visible_words is None and state.visible_lines is None:
        return lines

    out: list[str] = []
    if state.visible_chars is not None:
        # visible_chars is a fraction of the whole text, not a character count.
        total = sum(len(line) for line in lines)
        remaining = max(0.0, state.visible_chars) * total
        for line in lines:
            if remaining <= 0:
                break
            if remaining >= len(line):
                out.append(line)
                remaining -= len(line)
            else:
                out.append(line[: int(remaining)])
                remaining = 0
        return out

    if state.visible_words is not None:
        total_words = sum(len(line.split()) for line in lines)
        keep = total_words * state.visible_words
        counter = 0
        for line in lines:
            words = line.split()
            if counter >= keep:
                break
            take = min(len(words), int(keep - counter))
            out.append(" ".join(words[:take]))
            counter += len(words)
        return out

    if state.visible_lines is not None:
        keep = max(1, int(round(len(lines) * state.visible_lines)))
        return lines[:keep]

    return lines
