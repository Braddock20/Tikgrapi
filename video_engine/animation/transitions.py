"""Page transitions.

Pages are drawn one after the other; a transition decides how the outgoing
page hands over to the incoming one. The renderer blends the last frames of
one page with the first frames of the next.
"""
from __future__ import annotations

from dataclasses import dataclass

FADE = "fade"
DISSOLVE = "dissolve"
SLIDE = "slide"
NONE = "none"


@dataclass
class Transition:
    kind: str = FADE
    duration: float = 0.45

    @classmethod
    def parse(cls, kind: str | None, duration: float = 0.45) -> "Transition":
        kind = (kind or FADE).lower()
        if kind not in {FADE, DISSOLVE, SLIDE, NONE}:
            kind = FADE
        return cls(kind=kind, duration=max(0.0, duration))


def transition_weight(kind: str, local_t: float, duration: float) -> float:
    """Weight of the incoming page at ``local_t`` seconds into the transition.

    ``0`` means the outgoing page is fully visible, ``1`` the incoming one.
    """
    if kind == NONE or duration <= 0:
        return 1.0
    p = min(1.0, max(0.0, local_t / duration))
    if kind == SLIDE:
        return p * p * (3 - 2 * p)
    if kind == DISSOLVE:
        return p ** 1.6
    return p


def slide_offset(kind: str, local_t: float, duration: float, width: int) -> int:
    """Horizontal offset applied to the incoming page during a slide."""
    if kind != SLIDE or duration <= 0:
        return 0
    p = min(1.0, max(0.0, local_t / duration))
    eased = p * p * (3 - 2 * p)
    return int((1.0 - eased) * width)
