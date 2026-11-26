"""Composition validation.

A composition is checked *before* it is rendered: text that overflows, text
that leaves the safe area, invisible text or a major collision are all
caught here, so a bad design is never rendered and never published.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from video_engine.design.model import (
    ROLE_PHOTO,
    ROLE_PRIMARY_TEXT,
    ROLE_SECONDARY_TEXT,
    Element,
)
from video_engine.design.palettes import contrast_ratio
from video_engine.design.positioning import Box, element_box, inside_safe_area, safe_area


@dataclass
class Issue:
    severity: str  # "error" | "warning"
    code: str
    message: str
    element: int | None = None

    def __str__(self) -> str:
        return f"[{self.severity}] {self.code}: {self.message}"


@dataclass
class ValidationResult:
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        if not self.issues:
            return "composition is valid"
        return "; ".join(str(i) for i in self.issues)


def _text_overflow(element: Element, profile) -> str | None:
    payload = element.payload or {}
    lines = payload.get("lines") or []
    line_heights = payload.get("line_heights") or []
    if not lines:
        return None
    total = sum(line_heights)
    allowed = element.height * profile.height
    if total > allowed * 1.08:
        return f"text needs {int(total)}px but the box allows {int(allowed)}px"
    return None


def validate_composition(composition, profile) -> ValidationResult:
    """Check a composition against the output profile."""
    result = ValidationResult()
    area = safe_area(profile.width, profile.height, margin=0.05)

    for page_index, page in enumerate(composition.pages):
        text_elements = [
            (index, element)
            for index, element in enumerate(page.elements)
            if element.role in (ROLE_PRIMARY_TEXT, ROLE_SECONDARY_TEXT)
        ]

        for index, element in enumerate(page.elements):
            if element.opacity <= 0.02 and element.role in (ROLE_PRIMARY_TEXT, ROLE_SECONDARY_TEXT):
                result.issues.append(
                    Issue("error", "invisible_text", f"page {page_index}: text element {index} is invisible", index)
                )

            if element.role in (ROLE_PRIMARY_TEXT, ROLE_SECONDARY_TEXT):
                box = element_box(element)
                if not inside_safe_area(box, area, tolerance=0.02):
                    result.issues.append(
                        Issue(
                            "warning",
                            "outside_safe_area",
                            f"page {page_index}: text element {index} leaves the safe area",
                            index,
                        )
                    )
                overflow = _text_overflow(element, profile)
                if overflow:
                    result.issues.append(
                        Issue("error", "text_overflow", f"page {page_index}: {overflow}", index)
                    )
                colour = (element.payload or {}).get("colour")
                background = (element.payload or {}).get("background_colour")
                if colour and background and contrast_ratio(colour, background) < 2.2:
                    result.issues.append(
                        Issue(
                            "warning",
                            "low_contrast",
                            f"page {page_index}: text element {index} has low contrast",
                            index,
                        )
                    )

        # Major collisions: text on text is an error, text on photo is a warning.
        for i, (index_a, element_a) in enumerate(text_elements):
            box_a = element_box(element_a)
            for index_b, element_b in text_elements[i + 1 :]:
                box_b = element_box(element_b)
                if box_a.overlaps(box_b, tolerance=0.006):
                    overlap = box_a.overlap_area(box_b)
                    if overlap > 0.04:
                        result.issues.append(
                            Issue(
                                "error",
                                "text_collision",
                                f"page {page_index}: text {index_a} and {index_b} overlap",
                                index_b,
                            )
                        )
                    else:
                        result.issues.append(
                            Issue(
                                "warning",
                                "text_overlap",
                                f"page {page_index}: text {index_a} and {index_b} touch",
                                index_b,
                            )
                        )

        for index_a, element_a in text_elements:
            box_a = element_box(element_a)
            for index_b, element_b in enumerate(page.elements):
                if element_b.role != ROLE_PHOTO:
                    continue
                box_b = element_box(element_b)
                if box_a.overlaps(box_b, tolerance=0.01) and box_a.overlap_area(box_b) > 0.02:
                    result.issues.append(
                        Issue(
                            "warning",
                            "text_on_photo",
                            f"page {page_index}: text {index_a} sits on photo {index_b}",
                            index_a,
                        )
                    )

        if not page.elements:
            result.issues.append(Issue("error", "empty_page", f"page {page_index} has no elements"))

    if not composition.pages:
        result.issues.append(Issue("error", "no_pages", "the composition has no pages"))

    return result


def repair(composition, profile) -> ValidationResult:
    """Fix what can be fixed without redesigning the composition.

    * text that overflows is shrunk
    * elements outside the frame are pulled back in
    * overlapping text is nudged apart
    """
    from video_engine.design.positioning import clamp_to_frame, resolve_collisions

    changed = ValidationResult()
    for page in composition.pages:
        for element in page.elements:
            if element.role in (ROLE_PRIMARY_TEXT, ROLE_SECONDARY_TEXT):
                overflow = _text_overflow(element, profile)
                if overflow:
                    payload = element.payload
                    size = int(payload.get("font_size") or 48)
                    lines = payload.get("lines") or []
                    line_heights = payload.get("line_heights") or []
                    if size > 26 and lines and line_heights:
                        new_size = max(26, int(size * 0.9))
                        ratio = new_size / max(1, size)
                        payload["font_size"] = new_size
                        payload["line_heights"] = [max(20, int(h * ratio)) for h in line_heights]
                        element.height = min(element.height * 1.05, element.height * ratio + 0.01)
            clamp_to_frame(element, margin=0.015)
        resolve_collisions(page.elements)
    return changed
