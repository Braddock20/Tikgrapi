"""The element model shared by the design layer.

A composition is a list of pages; a page is a stack of elements. Elements
carry normalised geometry (0..1 of the frame), a layer order, an animation
spec and a kind-specific payload.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

# Element roles — the layout decides what each element *is for*.
ROLE_PRIMARY_TEXT = "primary_text"
ROLE_SECONDARY_TEXT = "secondary_text"
ROLE_CAPTION_TEXT = "caption_text"
ROLE_PHOTO = "photo"
ROLE_DECORATION = "decoration"
ROLE_SURFACE = "surface"

# Element kinds
KIND_TEXT = "text"
KIND_IMAGE = "image"
KIND_DECORATION = "decoration"
KIND_SURFACE = "surface"


_UID = itertools.count(1)


@dataclass
class Element:
    """One drawable object on a page."""

    kind: str
    x: float = 0.5
    y: float = 0.5
    width: float = 0.8
    height: float = 0.2
    rotation: float = 0.0
    scale: float = 1.0
    opacity: float = 1.0
    z_index: int = 0
    role: str = ROLE_DECORATION
    payload: dict[str, Any] = field(default_factory=dict)
    animation: dict[str, Any] | None = None
    uid: str = field(default_factory=lambda: f"e{next(_UID):06d}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "x": round(self.x, 4),
            "y": round(self.y, 4),
            "width": round(self.width, 4),
            "height": round(self.height, 4),
            "rotation": round(self.rotation, 2),
            "scale": round(self.scale, 3),
            "opacity": round(self.opacity, 3),
            "z_index": self.z_index,
            "role": self.role,
            "payload": self.payload,
            "animation": self.animation,
        }

    def rect(self) -> tuple[float, float, float, float]:
        """``(left, top, right, bottom)`` in normalised space."""
        return (self.x - self.width / 2, self.y - self.height / 2, self.x + self.width / 2, self.y + self.height / 2)


@dataclass
class Page:
    """One screen of the video."""

    index: int = 0
    elements: list[Element] = field(default_factory=list)
    duration: float = 5.0
    transition: str = "fade"
    background: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "duration": self.duration,
            "transition": self.transition,
            "background": self.background,
            "elements": [e.to_dict() for e in self.elements],
        }


@dataclass
class Composition:
    """A complete, renderable design."""

    pages: list[Page] = field(default_factory=list)
    identity: str = "minimal"
    layout: str = "centered_diary"
    palette: dict[str, Any] = field(default_factory=dict)
    fonts: dict[str, Any] = field(default_factory=dict)
    background: dict[str, Any] = field(default_factory=dict)
    seed: int = 0
    content_id: str = ""
    sound_id: str = ""
    title: str = ""
    notes: str = ""

    @property
    def duration(self) -> float:
        return sum(page.duration for page in self.pages)

    def elements(self) -> list[Element]:
        out: list[Element] = []
        for page in self.pages:
            out.extend(page.elements)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity,
            "layout": self.layout,
            "palette": self.palette,
            "fonts": self.fonts,
            "background": self.background,
            "seed": self.seed,
            "content_id": self.content_id,
            "sound_id": self.sound_id,
            "duration": self.duration,
            "pages": [p.to_dict() for p in self.pages],
        }
