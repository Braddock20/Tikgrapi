"""Typography engine.

Chooses a size that fits, wraps the text to the container, keeps the line
height and tracking sensible, and never lets the copy become unreadable
small. The rules are deterministic — the same text always produces the
same layout for a given container.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable

from PIL import ImageFont

from video_engine.assets.fonts import FontFace, load_font

# A caption that gets smaller than this is not readable on a phone.
MIN_FONT_SIZE = 26
MAX_FONT_SIZE = 132


@dataclass
class TextStyle:
    """A resolved typographic treatment."""

    font_path: str = ""
    font_key: str = ""
    size: int = 48
    line_spacing: float = 1.32
    tracking: float = 0.0
    align: str = "left"
    colour: str = "#222222"
    shadow: bool = False
    shadow_colour: str = "#00000022"
    uppercase: bool = False
    italic: bool = False
    weight: int = 400
    max_width: int = 800
    max_height: int = 400
    underline: bool = False
    highlight: str | None = None
    lines: list[str] = field(default_factory=list)
    line_heights: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "font": self.font_key,
            "size": self.size,
            "line_spacing": round(self.line_spacing, 3),
            "tracking": self.tracking,
            "align": self.align,
            "colour": self.colour,
            "uppercase": self.uppercase,
            "italic": self.italic,
            "weight": self.weight,
            "lines": len(self.lines),
        }


def _measure(font: ImageFont.FreeTypeFont, text: str, tracking: float = 0.0) -> int:
    if not text:
        return 0
    width = font.getlength(text)
    if tracking:
        width += tracking * max(0, len(text) - 1)
    return int(round(width))


def wrap_text(
    font: ImageFont.FreeTypeFont,
    text: str,
    max_width: int,
    tracking: float = 0.0,
) -> list[str]:
    """Greedy word wrap measured in pixels."""
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if _measure(font, candidate, tracking) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)

    # A single word longer than the line: break it by characters.
    out: list[str] = []
    for line in lines:
        while _measure(font, line, tracking) > max_width and len(line) > 1:
            cut = len(line)
            while cut > 1 and _measure(font, line[:cut], tracking) > max_width:
                cut -= 1
            out.append(line[:cut])
            line = line[cut:]
        out.append(line)
    return out


def fit_text(
    text: str,
    face: FontFace,
    *,
    max_width: int,
    max_height: int,
    min_size: int = MIN_FONT_SIZE,
    max_size: int = MAX_FONT_SIZE,
    line_spacing: float = 1.32,
    tracking: float = 0.0,
    align: str = "left",
    colour: str = "#222222",
    weight: int = 400,
    uppercase: bool = False,
    italic: bool = False,
) -> TextStyle:
    """Find the largest size at which ``text`` fits inside the container."""
    body = text.strip()
    if uppercase:
        body = body.upper()

    # Start at the largest allowed size and step down until the text fits —
    # the copy should fill its box, not sit in the middle of it.
    size = max(min_size, min(max_size, max_width // 10))
    lines = [body]
    line_heights: list[int] = []

    while size > min_size:
        font = load_font(str(face.path), size, weight)
        lines = wrap_text(font, body, max_width, tracking)
        line_heights = [int(size * line_spacing) for _ in lines]
        total_height = sum(line_heights)
        widest = max((_measure(font, line, tracking) for line in lines), default=0)
        if total_height <= max_height and widest <= max_width:
            break
        size -= 2
    size = max(min_size, size)

    font = load_font(str(face.path), size, weight)
    lines = wrap_text(font, body, max_width, tracking)
    line_heights = [int(size * line_spacing) for _ in lines]

    return TextStyle(
        font_path=str(face.path),
        font_key=face.key,
        size=size,
        line_spacing=line_spacing,
        tracking=tracking,
        align=align,
        colour=colour,
        uppercase=uppercase,
        italic=italic,
        weight=weight,
        max_width=max_width,
        max_height=max_height,
        lines=lines,
        line_heights=line_heights,
    )


def style_for_role(
    role: str,
    face: FontFace,
    palette,
    *,
    max_width: int,
    max_height: int,
    rng=None,
) -> TextStyle:
    """Build a text style appropriate for a role inside a palette."""
    align = "left"
    tracking = 0.0
    line_spacing = 1.32
    uppercase = False
    weight = face.weight
    shadow = True

    if role == "primary_text":
        colour = palette.ink
        tracking = 0.2
    elif role == "secondary_text":
        colour = palette.ink_soft
        tracking = 0.6
        uppercase = True
        line_spacing = 1.24
        weight = 600 if weight == 400 else weight
    elif role == "caption_text":
        colour = palette.ink_soft
        tracking = 1.2
        uppercase = True
        line_spacing = 1.22
        shadow = False
    else:
        colour = palette.ink

    if rng is not None:
        align = rng.choice(["left", "left", "center", "center", "right"])
        if role == "caption_text":
            align = rng.choice(["left", "center", "right"])

    style = fit_text(
        "",
        face,
        max_width=max_width,
        max_height=max_height,
        line_spacing=line_spacing,
        tracking=tracking,
        align=align,
        colour=colour,
        weight=weight,
        uppercase=uppercase,
    )
    style.shadow = shadow
    return style


def text_height(style: TextStyle) -> int:
    return sum(style.line_heights)


def text_width(style: TextStyle) -> int:
    if not style.lines:
        return 0
    font = load_font(style.font_path, style.size, style.weight)
    return max((_measure(font, line, style.tracking) for line in style.lines), default=0)


def draw_text_block(
    draw,
    style: TextStyle,
    origin: tuple[int, int],
    *,
    max_width: int,
    highlight: str | None = None,
    underline: bool = False,
):
    """Draw a text block with optional highlight and underline marks."""
    x, y = origin
    font = load_font(style.font_path, style.size, style.weight)
    offset = 0
    for index, line in enumerate(style.lines):
        line_height = style.line_heights[index] if index < len(style.line_heights) else int(style.size * style.line_spacing)
        width = _measure(font, line, style.tracking)
        if style.align == "center":
            line_x = x + (max_width - width) // 2
        elif style.align == "right":
            line_x = x + (max_width - width)
        else:
            line_x = x

        if highlight:
            draw.rectangle(
                [line_x - 6, y + offset + int(style.size * 0.08), line_x + width + 6, y + offset + int(style.size * 1.0)],
                fill=highlight,
            )
        if style.shadow:
            shadow_x = line_x + 2
            shadow_y = y + offset + 3
            _draw_line(draw, font, line, shadow_x, shadow_y, style.tracking, style.shadow_colour)
        _draw_line(draw, font, line, line_x, y + offset, style.tracking, style.colour)

        if underline:
            draw.line(
                [line_x, y + offset + int(style.size * 1.08), line_x + width, y + offset + int(style.size * 1.08)],
                fill=style.colour,
                width=max(2, style.size // 22),
            )
        offset += line_height
    return offset


def _draw_line(draw, font, text: str, x: int, y: int, tracking: float, colour: str) -> None:
    if not text:
        return
    if not tracking:
        draw.text((x, y), text, font=font, fill=colour)
        return
    cursor = float(x)
    for char in text:
        draw.text((cursor, y), char, font=font, fill=colour)
        cursor += font.getlength(char) + tracking


@lru_cache(maxsize=64)
def _cached_face_size(path: str, size: int, weight: int) -> tuple:
    font = load_font(path, size, weight)
    return (font.getlength("Wg") / 2.0, size)
