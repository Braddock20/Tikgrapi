"""Geometry helpers.

All layout code works in normalised coordinates (0..1 of the frame) so a
composition can be re-rendered at any resolution. This module converts
between the two and provides the collision / safe-area rules the validator
uses.
"""
from __future__ import annotations

from dataclasses import dataclass

from video_engine.design.model import Element


@dataclass(frozen=True)
class Box:
    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    @property
    def center(self) -> tuple[float, float]:
        return ((self.left + self.right) / 2, (self.top + self.bottom) / 2)

    def overlaps(self, other: "Box", tolerance: float = 0.0) -> bool:
        return not (
            self.right - tolerance <= other.left
            or other.right - tolerance <= self.left
            or self.bottom - tolerance <= other.top
            or other.bottom - tolerance <= self.top
        )

    def overlap_area(self, other: "Box") -> float:
        w = min(self.right, other.right) - max(self.left, other.left)
        h = min(self.bottom, other.bottom) - max(self.top, other.top)
        if w <= 0 or h <= 0:
            return 0.0
        return w * h


def element_box(element: Element) -> Box:
    left, top, right, bottom = element.rect()
    # Rotation and scale grow the footprint a little; account for that so
    # the validator does not mistake a tilted card for a collision.
    grow = (abs(element.rotation) / 90.0) * 0.04 + max(0.0, element.scale - 1.0) * 0.5
    pad_x = element.width * grow * 0.5
    pad_y = element.height * grow * 0.5
    return Box(left - pad_x, top - pad_y, right + pad_x, bottom + pad_y)


def safe_area(width: int, height: int, margin: float = 0.06) -> Box:
    """The area content may occupy, in normalised coordinates."""
    m = margin
    return Box(m, m, 1.0 - m, 1.0 - m)


def inside_safe_area(box: Box, area: Box, tolerance: float = 0.01) -> bool:
    return (
        box.left >= area.left - tolerance
        and box.top >= area.top - tolerance
        and box.right <= area.right + tolerance
        and box.bottom <= area.bottom + tolerance
    )


def clamp_to_frame(element: Element, margin: float = 0.02) -> Element:
    """Keep an element inside the frame; returns a corrected copy."""
    half_w = element.width / 2
    half_h = element.height / 2
    x = min(max(element.x, half_w + margin), 1.0 - half_w - margin)
    y = min(max(element.y, half_h + margin), 1.0 - half_h - margin)
    element.x = x
    element.y = y
    return element


def resolve_collisions(elements: list[Element], *, roles: tuple[str, ...] = ()) -> int:
    """Nudge overlapping elements apart.

    Only elements whose role is in ``roles`` are moved (by default the text
    roles, which must never sit on top of each other). Returns the number of
    elements that were moved.
    """
    moved = 0
    if not roles:
        roles = ("primary_text", "secondary_text", "caption_text")
    for _ in range(6):
        collided = False
        boxes = [(e, element_box(e)) for e in elements]
        for i, (a, box_a) in enumerate(boxes):
            if a.role not in roles:
                continue
            for b, box_b in boxes[i + 1 :]:
                if b.role not in roles:
                    continue
                if not box_a.overlaps(box_b, tolerance=0.004):
                    continue
                overlap_x = min(box_a.right, box_b.right) - max(box_a.left, box_b.left)
                overlap_y = min(box_a.bottom, box_b.bottom) - max(box_a.top, box_b.top)
                push = 0.012
                if overlap_y < overlap_x:
                    direction = 1.0 if b.center[1] >= a.center[1] else -1.0
                    b.y += direction * push
                else:
                    direction = 1.0 if b.center[0] >= a.center[0] else -1.0
                    b.x += direction * push
                collided = True
                moved += 1
                box_a = element_box(a)
                box_b = element_box(b)
        if not collided:
            break
    return moved


def balance_score(elements: list[Element]) -> float:
    """How centred the visual weight is. 1.0 is perfectly balanced."""
    if not elements:
        return 1.0
    total = 0.0
    cx = cy = 0.0
    for element in elements:
        weight = element.width * element.height * element.opacity
        total += weight
        cx += element.x * weight
        cy += element.y * weight
    if total <= 0:
        return 1.0
    offset = (((cx / total) - 0.5) ** 2 + ((cy / total) - 0.5) ** 2) ** 0.5
    return max(0.0, 1.0 - offset * 2.0)


def to_pixels(box: Box, width: int, height: int) -> tuple[int, int, int, int]:
    """Convert a normalised box into pixel coordinates."""
    return (
        int(round(box.left * width)),
        int(round(box.top * height)),
        int(round(box.right * width)),
        int(round(box.bottom * height)),
    )


# Roles whose boxes must stay clear of decoration.
CLEAR_ROLES = ("primary_text", "secondary_text", "caption_text")


def avoid_text_collisions(elements: list[Element], *, margin: float = 0.012) -> int:
    """Move decorations out of the way of the text.

    Decoration is placed by rule, but a star that lands on a word is a
    mistake — so every decoration that overlaps a text box is moved to the
    closest free spot (and shrunk if there is no free spot).
    """
    text_boxes = [
        element_box(e)
        for e in elements
        if e.role in CLEAR_ROLES
    ]
    if not text_boxes:
        return 0

    moved = 0
    for element in elements:
        if element.role in CLEAR_ROLES:
            continue
        if element.kind not in ("decoration",):
            continue
        for _ in range(14):
            box = element_box(element)
            if not any(box.overlaps(other, tolerance=-margin) for other in text_boxes):
                break
            # Try the four corners of the frame, then the centre gaps.
            candidates = [
                (0.12, 0.1),
                (0.88, 0.1),
                (0.12, 0.9),
                (0.88, 0.9),
                (0.12, 0.5),
                (0.88, 0.5),
                (0.5, 0.1),
                (0.5, 0.9),
            ]
            best = None
            best_cost = float("inf")
            for cx, cy in candidates:
                probe = Box(
                    cx - element.width / 2,
                    cy - element.height / 2,
                    cx + element.width / 2,
                    cy + element.height / 2,
                )
                cost = sum(probe.overlap_area(other) for other in text_boxes)
                cost += abs(cx - element.x) * 0.02 + abs(cy - element.y) * 0.02
                if cost < best_cost:
                    best_cost = cost
                    best = (cx, cy)
            if best is None:
                break
            element.x, element.y = best
            moved += 1
            if best_cost <= 0:
                break
        else:
            # Nowhere free: make the decoration smaller so it fits in a corner.
            element.scale = min(element.scale, 0.55)
    return moved
