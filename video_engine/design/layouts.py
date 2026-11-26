"""Layouts.

A layout is a *structure*, not a template: it decides which elements exist,
where they sit and how they relate. Text, colours, fonts, rotation and
decoration density are decided elsewhere, so two runs of the same layout
still look different.

Every layout returns a list of elements in normalised coordinates.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable

from video_engine.assets.fonts import FontFace
from video_engine.design.model import (
    KIND_DECORATION,
    KIND_IMAGE,
    KIND_SURFACE,
    KIND_TEXT,
    ROLE_CAPTION_TEXT,
    ROLE_DECORATION,
    ROLE_PHOTO,
    ROLE_PRIMARY_TEXT,
    ROLE_SECONDARY_TEXT,
    Element,
)
from video_engine.design.palettes import Palette
from video_engine.design.typography import TextStyle, fit_text


@dataclass
class LayoutContext:
    """Everything a layout is allowed to use."""

    rng: random.Random
    palette: Palette
    primary: FontFace
    secondary: FontFace
    width: int
    height: int
    body: str = ""
    kicker: str = ""
    signature: str = ""
    caption: str = ""
    photos: list = field(default_factory=list)
    textures: list = field(default_factory=list)
    identity: str = "minimal"

    def height_for(self, width: float, aspect: float = 1.0) -> float:
        """The normalised height of a box with a given pixel aspect.

        Normalised units are not square (the frame is 9:16), so a photo
        that should be 4:5 on the screen needs a much smaller normalised
        height than its width.
        """
        return width * (self.width / self.height) * aspect

    def pick_photo(self, used: set[str] | None = None) -> dict | None:
        used = used or set()
        pool = [p for p in self.photos if p.id not in used]
        if not pool:
            pool = list(self.photos)
        if not pool:
            return None
        asset = self.rng.choice(pool)
        used.add(asset.id)
        return {"asset_id": asset.id, "path": str(asset.path)}

    def pick_texture(self) -> dict | None:
        if not self.textures:
            return None
        asset = self.rng.choice(self.textures)
        return {"asset_id": asset.id, "path": str(asset.path)}


# --- element factories ----------------------------------------------------
def make_text(
    ctx: LayoutContext,
    text: str,
    box: tuple[float, float, float, float],
    role: str,
    *,
    face: FontFace | None = None,
    align: str | None = None,
    tracking: float | None = None,
    uppercase: bool | None = None,
    colour: str | None = None,
    line_spacing: float | None = None,
    weight: int | None = None,
    max_size: int = 132,
    min_size: int = 26,
    animation: dict | None = None,
    z_index: int = 10,
    shadow: bool | None = None,
    underline: bool = False,
    highlight: str | None = None,
    anchor: str = "center",
) -> Element:
    """Create a text element, sized to fit its box.

    ``anchor`` decides how the text sits inside the box: ``center`` keeps it
    in the middle, ``top`` hangs it from the top edge (used where a letter
    or a document must start at the top of the page).
    """
    x, y, width, height = box
    face = face or (ctx.primary if role == ROLE_PRIMARY_TEXT else ctx.secondary)
    px_width = max(40, int(width * ctx.width))
    px_height = max(30, int(height * ctx.height))

    style = fit_text(
        text,
        face,
        max_width=px_width,
        max_height=px_height,
        min_size=min_size,
        max_size=max_size,
        line_spacing=line_spacing or (1.34 if role == ROLE_PRIMARY_TEXT else 1.26),
        tracking=tracking if tracking is not None else (0.0 if role == ROLE_PRIMARY_TEXT else 1.0),
        align=align or ("left" if role == ROLE_PRIMARY_TEXT else "center"),
        colour=colour or ctx.palette.ink,
        weight=weight or face.weight,
        uppercase=bool(uppercase) if uppercase is not None else (role == ROLE_SECONDARY_TEXT),
    )
    if shadow is not None:
        style.shadow = shadow

    used_height = sum(style.line_heights) / ctx.height
    actual_height = max(0.02, min(height, used_height))
    if anchor == "top":
        # Hang the text from the top edge of the box.
        y = y - height / 2 + actual_height / 2
    return Element(
        kind=KIND_TEXT,
        x=x,
        y=y,
        width=width,
        height=actual_height,
        rotation=0.0,
        opacity=1.0,
        z_index=z_index,
        role=role,
        payload={
            "text": text,
            "style": style.to_dict(),
            "font_path": style.font_path,
            "font_size": style.size,
            "font_weight": style.weight,
            "line_heights": style.line_heights,
            "lines": style.lines,
            "align": style.align,
            "colour": style.colour,
            "tracking": style.tracking,
            "shadow": style.shadow,
            "underline": underline,
            "highlight": highlight,
        },
        animation=animation,
    )


def make_surface(
    ctx: LayoutContext,
    box: tuple[float, float, float, float],
    *,
    style: str = "card",
    colour: str | None = None,
    rotation: float = 0.0,
    z_index: int = 1,
    opacity: float = 1.0,
    shadow: bool = True,
    animation: dict | None = None,
    payload: dict | None = None,
) -> Element:
    x, y, width, height = box
    return Element(
        kind=KIND_SURFACE,
        x=x,
        y=y,
        width=width,
        height=height,
        rotation=rotation,
        opacity=opacity,
        z_index=z_index,
        role=ROLE_DECORATION,
        payload={"style": style, "colour": colour or ctx.palette.paper, "shadow": shadow, **(payload or {})},
        animation=animation,
    )


def make_photo(
    ctx: LayoutContext,
    box: tuple[float, float, float, float],
    photo: dict | None,
    *,
    frame: str = "plain",
    rotation: float = 0.0,
    z_index: int = 5,
    caption: str = "",
    animation: dict | None = None,
) -> Element:
    x, y, width, height = box
    return Element(
        kind=KIND_IMAGE,
        x=x,
        y=y,
        width=width,
        height=height,
        rotation=rotation,
        opacity=1.0,
        z_index=z_index,
        role=ROLE_PHOTO,
        payload={
            "asset_id": (photo or {}).get("asset_id"),
            "path": (photo or {}).get("path"),
            "frame": frame,
            "caption": caption,
        },
        animation=animation,
    )


def make_decoration(
    ctx: LayoutContext,
    name: str,
    box: tuple[float, float, float, float],
    *,
    rotation: float = 0.0,
    opacity: float = 1.0,
    z_index: int = 8,
    animation: dict | None = None,
    text: str = "",
    colour: str | None = None,
) -> Element:
    x, y, width, height = box
    return Element(
        kind=KIND_DECORATION,
        x=x,
        y=y,
        width=width,
        height=height,
        rotation=rotation,
        opacity=opacity,
        z_index=z_index,
        role=ROLE_DECORATION,
        payload={"decoration": name, "text": text, "colour": colour},
        animation=animation,
    )


def _jitter(ctx: LayoutContext, amount: float = 0.012) -> float:
    return ctx.rng.uniform(-amount, amount)


# --- the layouts ----------------------------------------------------------
def layout_centered_diary(ctx: LayoutContext) -> list[Element]:
    """A single journal page, centred, with the text in the middle."""
    elements: list[Element] = []
    card_w = ctx.rng.uniform(0.66, 0.75)
    card_h = ctx.rng.uniform(0.46, 0.56)
    card_x = 0.5 + _jitter(ctx, 0.02)
    card_y = 0.47 + _jitter(ctx, 0.02)
    elements.append(
        make_surface(
            ctx,
            (card_x, card_y, card_w, card_h),
            style="paper",
            colour=ctx.palette.paper,
            rotation=_jitter(ctx, 1.4),
            z_index=1,
            payload={"texture": True},
        )
    )
    card_top = card_y - card_h / 2
    if ctx.kicker:
        elements.append(
            make_text(
                ctx,
                ctx.kicker,
                (card_x, card_top + 0.055, card_w * 0.7, 0.04),
                ROLE_SECONDARY_TEXT,
                align="left",
                tracking=2.2,
                colour=ctx.palette.ink_soft,
                z_index=12,
            )
        )
    # The headline hangs from just below the kicker, so the two can never
    # collide however long the text is.
    text_h = card_h * 0.62
    text_top = card_top + (0.1 if ctx.kicker else 0.05)
    elements.append(
        make_text(
            ctx,
            ctx.body,
            (card_x, text_top + text_h / 2, card_w * 0.84, text_h),
            ROLE_PRIMARY_TEXT,
            align=ctx.rng.choice(["left", "left", "center"]),
            anchor="top",
            z_index=12,
            underline=ctx.rng.random() < 0.3,
        )
    )
    if ctx.signature:
        elements.append(
            make_text(
                ctx,
                ctx.signature,
                (card_x + card_w * 0.3, card_y + card_h / 2 - 0.045, card_w * 0.4, 0.035),
                ROLE_CAPTION_TEXT,
                align="right",
                tracking=0.6,
                uppercase=False,
                colour=ctx.palette.accent,
                z_index=12,
            )
        )
    if ctx.rng.random() < 0.6:
        elements.append(
            make_decoration(
                ctx,
                ctx.rng.choice(["star", "flower", "heart", "splat"]),
                (card_x + card_w * 0.42, card_y + card_h * 0.44, 0.1, 0.06),
                rotation=ctx.rng.uniform(-20, 20),
                opacity=ctx.rng.uniform(0.7, 0.95),
                z_index=14,
            )
        )
    return elements


def layout_asymmetric_scrapbook(ctx: LayoutContext) -> list[Element]:
    """Photos at an angle, text card pinned off to one side."""
    elements: list[Element] = []
    used: set[str] = set()
    side = ctx.rng.choice([-1, 1])

    first = ctx.pick_photo(used)
    if first:
        w = ctx.rng.uniform(0.5, 0.62)
        elements.append(
            make_photo(
                ctx,
                (0.5 + side * 0.18, 0.3 + _jitter(ctx, 0.03), w, ctx.height_for(w, 1.25)),
                first,
                frame=ctx.rng.choice(["polaroid", "plain", "tape"]),
                rotation=ctx.rng.uniform(-7, 7),
                z_index=4,
            )
        )
    second = ctx.pick_photo(used)
    if second:
        w = ctx.rng.uniform(0.36, 0.46)
        elements.append(
            make_photo(
                ctx,
                (0.5 - side * 0.26, 0.58 + _jitter(ctx, 0.03), w, ctx.height_for(w, 0.82)),
                second,
                frame=ctx.rng.choice(["plain", "polaroid"]),
                rotation=ctx.rng.uniform(-11, 11),
                z_index=6,
            )
        )

    card_w = ctx.rng.uniform(0.66, 0.76)
    card_h = ctx.rng.uniform(0.3, 0.38)
    card_x = 0.5 - side * 0.05
    card_y = ctx.rng.uniform(0.68, 0.74)
    elements.append(
        make_surface(
            ctx,
            (card_x, card_y, card_w, card_h),
            style=ctx.rng.choice(["paper", "torn"]),
            colour=ctx.palette.surface,
            rotation=ctx.rng.uniform(-4, 4),
            z_index=8,
        )
    )
    elements.append(
        make_text(
            ctx,
            ctx.body,
            (card_x, card_y, card_w * 0.82, card_h * 0.74),
            ROLE_PRIMARY_TEXT,
            align="left",
            z_index=12,
        )
    )
    elements.append(
        make_decoration(
            ctx,
            "tape",
            (card_x - card_w * 0.32, card_y - card_h * 0.42, 0.26, 0.07),
            rotation=ctx.rng.uniform(-25, -8),
            opacity=0.85,
            z_index=13,
        )
    )
    if ctx.rng.random() < 0.5:
        elements.append(
            make_decoration(
                ctx,
                ctx.rng.choice(["highlight", "scribble"]),
                (card_x, card_y + card_h * 0.42, card_w * 0.5, 0.03),
                rotation=ctx.rng.uniform(-3, 3),
                opacity=0.8,
                z_index=13,
            )
        )
    return elements


def layout_magazine_editorial(ctx: LayoutContext) -> list[Element]:
    """Big type, strong hierarchy, a photograph-led lower half."""
    elements: list[Element] = []
    left = ctx.rng.choice([0.16, 0.2, 0.24])
    width = min(ctx.rng.uniform(0.72, 0.82), 0.94 - left)

    photo = ctx.pick_photo()
    if photo:
        elements.append(
            make_photo(
                ctx,
                (0.5, 0.72, 1.0, 0.56),
                photo,
                frame="none",
                z_index=1,
            )
        )
        elements.append(
            make_surface(
                ctx,
                (0.5, 0.51, 1.0, 0.14),
                style="gradient",
                colour=ctx.palette.paper,
                z_index=2,
                payload={"fade": True},
            )
        )
    if ctx.kicker:
        elements.append(
            make_text(
                ctx,
                ctx.kicker,
                (left + 0.35, 0.115, 0.7, 0.03),
                ROLE_SECONDARY_TEXT,
                align="left",
                tracking=4.0,
                colour=ctx.palette.accent,
                z_index=12,
            )
        )
    # The headline hangs from below the kicker, so the two never collide.
    head_top = 0.19 if ctx.kicker else 0.12
    elements.append(
        make_text(
            ctx,
            ctx.body,
            (left + width / 2, head_top + 0.15, width, 0.3),
            ROLE_PRIMARY_TEXT,
            align="left",
            anchor="top",
            max_size=96,
            line_spacing=1.12,
            z_index=12,
        )
    )
    elements.append(
        make_surface(
            ctx,
            (left + 0.08, head_top + 0.3, 0.16, 0.006),
            style="rule",
            colour=ctx.palette.accent,
            z_index=12,
            shadow=False,
        )
    )
    if ctx.signature:
        # The signoff sits over the photograph, so it is always light.
        elements.append(
            make_text(
                ctx,
                ctx.signature,
                (left + 0.25, 0.935, 0.5, 0.03),
                ROLE_CAPTION_TEXT,
                align="left",
                tracking=1.6,
                colour="#F2ECE1",
                z_index=12,
            )
        )
    return elements


def layout_handwritten_letter(ctx: LayoutContext) -> list[Element]:
    """A letter sheet with a date, a body and a signature."""
    elements: list[Element] = []
    page_w = ctx.rng.uniform(0.72, 0.8)
    page_h = ctx.rng.uniform(0.66, 0.76)
    page_x = 0.5 + _jitter(ctx, 0.015)
    page_y = 0.5
    elements.append(
        make_surface(
            ctx,
            (page_x, page_y, page_w, page_h),
            style="paper",
            colour=ctx.palette.paper,
            rotation=_jitter(ctx, 1.0),
            z_index=1,
            payload={"texture": True, "ruled": True},
        )
    )
    if ctx.kicker:
        elements.append(
            make_text(
                ctx,
                ctx.kicker,
                (page_x + page_w * 0.36, page_y - page_h / 2 + 0.05, page_w * 0.32, 0.032),
                ROLE_CAPTION_TEXT,
                align="right",
                tracking=1.4,
                uppercase=False,
                colour=ctx.palette.ink_soft,
                z_index=12,
            )
        )
    elements.append(
        make_text(
            ctx,
            ctx.body,
            (page_x, page_y - 0.02, page_w * 0.8, page_h * 0.58),
            ROLE_PRIMARY_TEXT,
            align="left",
            anchor="top",
            line_spacing=1.5,
            tracking=0.4,
            z_index=12,
        )
    )
    if ctx.signature:
        elements.append(
            make_text(
                ctx,
                ctx.signature,
                (page_x + page_w * 0.24, page_y + page_h / 2 - 0.075, page_w * 0.4, 0.04),
                ROLE_CAPTION_TEXT,
                align="right",
                tracking=0.4,
                uppercase=False,
                colour=ctx.palette.accent,
                z_index=12,
            )
        )
    elements.append(
        make_decoration(
            ctx,
            ctx.rng.choice(["paperclip", "stamp", "corner_fold"]),
            (page_x + page_w * 0.42, page_y - page_h / 2 + 0.03, 0.1, 0.045),
            rotation=ctx.rng.uniform(-18, 18),
            opacity=0.9,
            z_index=13,
            text=ctx.kicker,
        )
    )
    elements.append(
        make_decoration(
            ctx,
            "envelope",
            (page_x - page_w * 0.36, page_y + page_h * 0.36, 0.2, 0.12),
            rotation=ctx.rng.uniform(-9, 9),
            opacity=0.95,
            z_index=9,
        )
    )
    return elements


def layout_polaroid_collage(ctx: LayoutContext) -> list[Element]:
    """Two or three polaroids scattered across the page."""
    elements: list[Element] = []
    used: set[str] = set()
    count = ctx.rng.choice([2, 2, 3])
    slots = [
        (0.34, 0.3),
        (0.66, 0.26),
        (0.5, 0.58),
        (0.28, 0.66),
        (0.72, 0.62),
    ]
    ctx.rng.shuffle(slots)
    for index in range(count):
        photo = ctx.pick_photo(used)
        if photo is None:
            break
        sx, sy = slots[index]
        w = ctx.rng.uniform(0.4, 0.52)
        elements.append(
            make_photo(
                ctx,
                (sx + _jitter(ctx, 0.02), sy + _jitter(ctx, 0.02), w, ctx.height_for(w, 1.28)),
                photo,
                frame="polaroid",
                rotation=ctx.rng.uniform(-9, 9),
                z_index=4 + index,
                caption=ctx.caption if index == 0 else "",
            )
        )
    elements.append(
        make_surface(
            ctx,
            (0.5, 0.83, 0.72, 0.16),
            style="paper",
            colour=ctx.palette.surface,
            rotation=ctx.rng.uniform(-2, 2),
            z_index=9,
        )
    )
    elements.append(
        make_text(
            ctx,
            ctx.body,
            (0.5, 0.83, 0.64, 0.13),
            ROLE_PRIMARY_TEXT,
            align="center",
            max_size=54,
            z_index=12,
        )
    )
    elements.append(
        make_decoration(
            ctx,
            "tape",
            (0.5, 0.74, 0.3, 0.06),
            rotation=ctx.rng.uniform(-12, 12),
            opacity=0.8,
            z_index=13,
        )
    )
    return elements


def layout_memory_archive(ctx: LayoutContext) -> list[Element]:
    """A photograph framed like a slide in an archive."""
    elements: list[Element] = []
    photo = ctx.pick_photo()
    if photo:
        elements.append(
            make_photo(
                ctx,
                (0.5, 0.42, 0.78, ctx.height_for(0.78, 0.78)),
                photo,
                frame="none",
                z_index=3,
            )
        )
    elements.append(
        make_surface(
            ctx,
            (0.5, 0.05, 1.0, 0.07),
            style="film",
            colour=ctx.palette.ink,
            z_index=2,
            shadow=False,
        )
    )
    elements.append(
        make_surface(
            ctx,
            (0.5, 0.95, 1.0, 0.07),
            style="film",
            colour=ctx.palette.ink,
            z_index=2,
            shadow=False,
        )
    )
    elements.append(
        make_text(
            ctx,
            ctx.body,
            (0.5, 0.72, 0.74, 0.16),
            ROLE_PRIMARY_TEXT,
            align="center",
            max_size=60,
            z_index=12,
        )
    )
    if ctx.kicker:
        elements.append(
            make_text(
                ctx,
                ctx.kicker,
                (0.5, 0.84, 0.7, 0.035),
                ROLE_CAPTION_TEXT,
                align="center",
                tracking=3.0,
                colour=ctx.palette.ink_soft,
                z_index=12,
            )
        )
    return elements


def layout_minimalist(ctx: LayoutContext) -> list[Element]:
    """Nothing but the words, carefully placed."""
    elements: list[Element] = []
    align = ctx.rng.choice(["center", "left", "right"])
    width = 0.74 if align == "center" else 0.64
    if align == "center":
        x = 0.5
    elif align == "left":
        x = 0.18 + width / 2
    else:
        x = 0.82 - width / 2
    if ctx.kicker:
        elements.append(
            make_text(
                ctx,
                ctx.kicker,
                (x, 0.16, width, 0.028),
                ROLE_SECONDARY_TEXT,
                align=align,
                tracking=4.5,
                colour=ctx.palette.accent,
                z_index=12,
            )
        )
    elements.append(
        make_text(
            ctx,
            ctx.body,
            (x, 0.5, width, 0.46),
            ROLE_PRIMARY_TEXT,
            align=align,
            max_size=104,
            line_spacing=1.18,
            shadow=False,
            z_index=12,
        )
    )
    elements.append(
        make_surface(
            ctx,
            (x, 0.78, 0.14, 0.004),
            style="rule",
            colour=ctx.palette.accent,
            z_index=12,
            shadow=False,
        )
    )
    if ctx.signature:
        elements.append(
            make_text(
                ctx,
                ctx.signature,
                (x, 0.83, 0.4, 0.028),
                ROLE_CAPTION_TEXT,
                align=align,
                tracking=1.8,
                uppercase=False,
                colour=ctx.palette.ink_soft,
                z_index=12,
            )
        )
    return elements


def layout_layered_cards(ctx: LayoutContext) -> list[Element]:
    """Three note cards, slightly out of alignment."""
    elements: list[Element] = []
    for index in range(ctx.rng.choice([2, 3])):
        offset = (index - 1) * ctx.rng.uniform(0.03, 0.07)
        elements.append(
            make_surface(
                ctx,
                (
                    0.5 + offset + _jitter(ctx, 0.02),
                    0.5 + _jitter(ctx, 0.02) - index * 0.012,
                    ctx.rng.uniform(0.62, 0.76),
                    ctx.rng.uniform(0.44, 0.56),
                ),
                style=ctx.rng.choice(["paper", "torn", "card"]),
                colour=ctx.palette.surface if index else ctx.palette.paper,
                rotation=ctx.rng.uniform(-5, 5),
                z_index=1 + index * 2,
                payload={"texture": True},
            )
        )
    elements.append(
        make_text(
            ctx,
            ctx.body,
            (0.5, 0.48, 0.66, 0.36),
            ROLE_PRIMARY_TEXT,
            align=ctx.rng.choice(["left", "center"]),
            anchor="top",
            z_index=12,
        )
    )
    elements.append(
        make_decoration(
            ctx,
            "paperclip",
            (0.66, 0.27, 0.05, 0.09),
            rotation=ctx.rng.uniform(-20, 20),
            opacity=0.85,
            z_index=13,
        )
    )
    if ctx.kicker:
        elements.append(
            make_text(
                ctx,
                ctx.kicker,
                (0.34, 0.28, 0.4, 0.03),
                ROLE_SECONDARY_TEXT,
                align="left",
                tracking=2.4,
                colour=ctx.palette.ink_soft,
                z_index=13,
            )
        )
    return elements


def layout_full_page_photo(ctx: LayoutContext) -> list[Element]:
    """A full-bleed photograph with the words over it."""
    elements: list[Element] = []
    photo = ctx.pick_photo()
    if photo:
        elements.append(
            make_photo(
                ctx,
                (0.5, 0.5, 1.0, 1.0),
                photo,
                frame="none",
                z_index=1,
            )
        )
    elements.append(
        make_surface(
            ctx,
            (0.5, 0.72, 1.0, 0.46),
            style="gradient",
            colour="#14161A",
            z_index=2,
            payload={"fade": True, "direction": "up"},
        )
    )
    elements.append(
        make_text(
            ctx,
            ctx.body,
            (0.5, 0.74, 0.78, 0.24),
            ROLE_PRIMARY_TEXT,
            align="center",
            colour="#FDFBF5",
            max_size=72,
            z_index=12,
            shadow=True,
        )
    )
    if ctx.kicker:
        elements.append(
            make_text(
                ctx,
                ctx.kicker,
                (0.5, 0.63, 0.7, 0.03),
                ROLE_CAPTION_TEXT,
                align="center",
                tracking=3.4,
                colour="#EDE6D8",
                z_index=12,
            )
        )
    return elements


def layout_typewriter(ctx: LayoutContext) -> list[Element]:
    """A typed document."""
    elements: list[Element] = []
    page_w = 0.8
    page_h = 0.78
    elements.append(
        make_surface(
            ctx,
            (0.5, 0.5, page_w, page_h),
            style="paper",
            colour=ctx.palette.paper,
            rotation=_jitter(ctx, 0.8),
            z_index=1,
            payload={"texture": True},
        )
    )
    elements.append(
        make_text(
            ctx,
            ctx.body,
            (0.5, 0.47, page_w * 0.78, page_h * 0.6),
            ROLE_PRIMARY_TEXT,
            face=ctx.secondary if ctx.secondary.category == "typewriter" else ctx.primary,
            align="left",
            anchor="top",
            line_spacing=1.62,
            tracking=0.6,
            colour=ctx.palette.ink_soft,
            z_index=12,
            shadow=False,
        )
    )
    elements.append(
        make_surface(
            ctx,
            (0.5, 0.13, page_w * 0.78, 0.004),
            style="rule",
            colour=ctx.palette.frame,
            z_index=12,
            shadow=False,
        )
    )
    if ctx.kicker:
        elements.append(
            make_text(
                ctx,
                ctx.kicker,
                (0.5, 0.1, page_w * 0.78, 0.026),
                ROLE_CAPTION_TEXT,
                align="left",
                tracking=2.0,
                uppercase=False,
                colour=ctx.palette.accent,
                z_index=12,
            )
        )
    elements.append(
        make_decoration(
            ctx,
            "paperclip",
            (0.24, 0.2, 0.05, 0.1),
            rotation=ctx.rng.uniform(-25, 25),
            opacity=0.8,
            z_index=13,
        )
    )
    if ctx.signature:
        elements.append(
            make_text(
                ctx,
                ctx.signature,
                (0.68, 0.86, 0.3, 0.03),
                ROLE_CAPTION_TEXT,
                align="right",
                tracking=1.0,
                uppercase=False,
                colour=ctx.palette.ink_soft,
                z_index=12,
            )
        )
    return elements


def layout_scattered_notes(ctx: LayoutContext) -> list[Element]:
    """Small notes dropped across the page."""
    elements: list[Element] = []
    slots = [
        (0.32, 0.24, 0.44, 0.16),
        (0.68, 0.34, 0.38, 0.14),
        (0.38, 0.58, 0.42, 0.16),
        (0.7, 0.72, 0.34, 0.13),
    ]
    main_slot = slots[0]
    rest = slots[1:]
    ctx.rng.shuffle(rest)
    slots = [main_slot, *rest]
    # Paper tones only — a saturated block this large would shout.
    colours = [ctx.palette.paper, ctx.palette.surface, ctx.palette.paper, ctx.palette.surface]
    for index, (x, y, w, h) in enumerate(slots[:3]):
        elements.append(
            make_surface(
                ctx,
                (x + _jitter(ctx, 0.015), y + _jitter(ctx, 0.015), w, h),
                style=ctx.rng.choice(["paper", "torn", "card"]),
                colour=colours[index % len(colours)],
                rotation=ctx.rng.uniform(-6, 6),
                z_index=2 + index,
                payload={"texture": index == 0},
            )
        )
    elements.append(
        make_text(
            ctx,
            ctx.body,
            (main_slot[0], main_slot[1], main_slot[2] * 0.84, main_slot[3] * 0.9),
            ROLE_PRIMARY_TEXT,
            align="left",
            max_size=48,
            z_index=12,
        )
    )
    if ctx.kicker:
        elements.append(
            make_text(
                ctx,
                ctx.kicker,
                (slots[1][0], slots[1][1], slots[1][2] * 0.8, 0.05),
                ROLE_CAPTION_TEXT,
                align="left",
                tracking=1.6,
                uppercase=False,
                colour=ctx.palette.ink,
                z_index=12,
            )
        )
    elements.append(
        make_decoration(
            ctx,
            "paperclip",
            (slots[2][0] + slots[2][2] * 0.42, slots[2][1] - slots[2][3] * 0.44, 0.045, 0.08),
            rotation=ctx.rng.uniform(-25, 25),
            opacity=0.85,
            z_index=13,
        )
    )
    return elements


def layout_layered_stationery(ctx: LayoutContext) -> list[Element]:
    """Letter sheet, envelope and stamp, arranged together."""
    elements: list[Element] = []
    elements.append(
        make_surface(
            ctx,
            (0.52, 0.44, 0.7, 0.72),
            style="paper",
            colour=ctx.palette.paper,
            rotation=ctx.rng.uniform(-3, 3),
            z_index=1,
            payload={"texture": True, "ruled": True},
        )
    )
    elements.append(
        make_text(
            ctx,
            ctx.body,
            (0.52, 0.44, 0.56, 0.44),
            ROLE_PRIMARY_TEXT,
            align="left",
            anchor="top",
            line_spacing=1.46,
            z_index=12,
        )
    )
    elements.append(
        make_decoration(
            ctx,
            "envelope",
            (0.28, 0.76, 0.34, 0.2),
            rotation=ctx.rng.uniform(-10, 10),
            opacity=0.96,
            z_index=6,
        )
    )
    elements.append(
        make_decoration(
            ctx,
            "stamp",
            (0.76, 0.2, 0.13, 0.09),
            rotation=ctx.rng.uniform(-14, 14),
            opacity=0.92,
            z_index=13,
            text=ctx.kicker or "MEMORY",
        )
    )
    elements.append(
        make_decoration(
            ctx,
            "tape",
            (0.28, 0.76, 0.24, 0.06),
            rotation=ctx.rng.uniform(-20, 20),
            opacity=0.82,
            z_index=14,
        )
    )
    if ctx.signature:
        elements.append(
            make_text(
                ctx,
                ctx.signature,
                (0.68, 0.7, 0.28, 0.03),
                ROLE_CAPTION_TEXT,
                align="right",
                tracking=0.8,
                uppercase=False,
                colour=ctx.palette.accent,
                z_index=13,
            )
        )
    return elements


LAYOUTS: dict[str, Callable[[LayoutContext], list[Element]]] = {
    "centered_diary": layout_centered_diary,
    "asymmetric_scrapbook": layout_asymmetric_scrapbook,
    "magazine_editorial": layout_magazine_editorial,
    "handwritten_letter": layout_handwritten_letter,
    "polaroid_collage": layout_polaroid_collage,
    "memory_archive": layout_memory_archive,
    "minimalist_typography": layout_minimalist,
    "layered_cards": layout_layered_cards,
    "full_page_photo": layout_full_page_photo,
    "typewriter_document": layout_typewriter,
    "scattered_notes": layout_scattered_notes,
    "layered_stationery": layout_layered_stationery,
}

# Layouts that need at least one photograph to work.
PHOTO_LAYOUTS = {"asymmetric_scrapbook", "polaroid_collage", "memory_archive", "full_page_photo"}


def layout_names() -> list[str]:
    return list(LAYOUTS.keys())
