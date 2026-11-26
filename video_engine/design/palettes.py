"""Colour palettes.

Each palette is a small set of roles rather than a list of colours, so a
layout can ask for "paper" and "ink" without knowing which palette it got.
Contrast between ink and paper is guaranteed to be readable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class Palette:
    name: str
    paper: str
    ink: str
    ink_soft: str
    accent: str
    accent2: str
    surface: str
    frame: str
    shadow: str = "#00000030"
    on_accent: str = "#FFFDF8"
    mood: str = "neutral"
    tags: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "paper": self.paper,
            "ink": self.ink,
            "ink_soft": self.ink_soft,
            "accent": self.accent,
            "accent2": self.accent2,
            "surface": self.surface,
            "frame": self.frame,
            "shadow": self.shadow,
            "on_accent": self.on_accent,
        }


PALETTES: dict[str, Palette] = {
    p.name: p
    for p in [
        Palette(
            name="cream",
            paper="#F6F0E4",
            ink="#2B2620",
            ink_soft="#6C6154",
            accent="#C4633A",
            accent2="#7E8C6B",
            surface="#FFFCF5",
            frame="#DED3BE",
            mood="warm",
            tags=("diary", "scrapbook", "warm"),
        ),
        Palette(
            name="kraft",
            paper="#DCC7A8",
            ink="#3A2E24",
            ink_soft="#6B5A48",
            accent="#B4552D",
            accent2="#6B7A55",
            surface="#EFE0C6",
            frame="#BFA981",
            mood="warm",
            tags=("scrapbook", "memory", "vintage"),
        ),
        Palette(
            name="faded_blue",
            paper="#E8EEF3",
            ink="#22303C",
            ink_soft="#5B6C7A",
            accent="#3E6C8E",
            accent2="#C9762F",
            surface="#F7FAFC",
            frame="#C6D3DD",
            mood="cool",
            tags=("memory", "editorial", "calm"),
        ),
        Palette(
            name="blush",
            paper="#F8E8E3",
            ink="#33272B",
            ink_soft="#75616A",
            accent="#C86B7F",
            accent2="#7D8E7A",
            surface="#FFF6F3",
            frame="#E6CFC9",
            mood="romantic",
            tags=("letter", "romantic", "diary"),
        ),
        Palette(
            name="sage",
            paper="#EAEFE3",
            ink="#26302A",
            ink_soft="#5C6A5E",
            accent="#6E8B62",
            accent2="#C1743F",
            surface="#F7FAF3",
            frame="#CBD8C4",
            mood="calm",
            tags=("minimal", "diary", "calm"),
        ),
        Palette(
            name="sepia",
            paper="#F0E4CF",
            ink="#3B3026",
            ink_soft="#6E6152",
            accent="#A9713C",
            accent2="#6E7F6A",
            surface="#FAF1E2",
            frame="#DCC9AA",
            mood="nostalgic",
            tags=("memory", "nostalgic", "letter"),
        ),
        Palette(
            name="mono",
            paper="#F3F3F1",
            ink="#161616",
            ink_soft="#5C5C5C",
            accent="#8A8A8A",
            accent2="#B23A2E",
            surface="#FFFFFF",
            frame="#D8D8D5",
            mood="neutral",
            tags=("editorial", "minimal"),
        ),
        Palette(
            name="midnight",
            paper="#1D222A",
            ink="#EFE9DE",
            ink_soft="#A9A296",
            accent="#C9A96A",
            accent2="#7FA0B4",
            surface="#262C36",
            frame="#3A4554",
            shadow="#00000066",
            on_accent="#20242C",
            mood="dark",
            tags=("editorial", "mysterious", "intense"),
        ),
        Palette(
            name="terracotta",
            paper="#F3E4D9",
            ink="#33302C",
            ink_soft="#6B5F55",
            accent="#C06B4E",
            accent2="#5F7A6B",
            surface="#FDF6F0",
            frame="#E0C8B8",
            mood="warm",
            tags=("scrapbook", "diary"),
        ),
        Palette(
            name="butter",
            paper="#FBF3D6",
            ink="#2E2A22",
            ink_soft="#6E6550",
            accent="#D98E3A",
            accent2="#7C8C6A",
            surface="#FFFBEC",
            frame="#E8DCB8",
            mood="bright",
            tags=("playful", "question", "humour"),
        ),
        Palette(
            name="dusty_rose",
            paper="#F4EEEA",
            ink="#443A38",
            ink_soft="#7C6D69",
            accent="#B0807A",
            accent2="#6D7A6E",
            surface="#FCF8F5",
            frame="#E2D3CE",
            mood="nostalgic",
            tags=("letter", "romantic", "nostalgic"),
        ),
        Palette(
            name="ink_blue",
            paper="#EEF1F6",
            ink="#1D2733",
            ink_soft="#586678",
            accent="#2F4E6E",
            accent2="#D08A4E",
            surface="#F8FAFD",
            frame="#CBD5E2",
            mood="cool",
            tags=("editorial", "intense", "letter"),
        ),
    ]
}


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG-style contrast ratio between two hex colours."""
    lum_a = _relative_luminance(foreground)
    lum_b = _relative_luminance(background)
    lighter, darker = max(lum_a, lum_b), min(lum_a, lum_b)
    return (lighter + 0.05) / (darker + 0.05)


def _relative_luminance(hex_colour: str) -> float:
    r, g, b = _rgb(hex_colour)
    channels = []
    for value in (r, g, b):
        value /= 255.0
        channels.append(value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    value = hex_colour.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6:
        return (0, 0, 0)
    try:
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except ValueError:
        return (0, 0, 0)


def pick_palette(rng, *, identity: str = "", mood: str = "", avoid: Iterable[str] = ()) -> Palette:
    """Choose a palette that fits the identity and the mood."""
    avoid_set = set(avoid)
    pool = list(PALETTES.values())
    if identity:
        preferred = [p for p in pool if identity in p.tags]
        if preferred:
            pool = preferred
    if mood:
        matched = [p for p in pool if p.mood == mood]
        if matched:
            pool = matched
    fresh = [p for p in pool if p.name not in avoid_set]
    return rng.choice(fresh or pool)


def readable_ink(palette: Palette, background: str) -> str:
    """Return whichever of ink/accent reads better on ``background``."""
    return palette.ink if contrast_ratio(palette.ink, background) >= contrast_ratio(palette.accent, background) else palette.accent
