"""Font registry and pairing rules.

Fonts are discovered from ``assets/fonts/<category>/*.ttf``. The engine
pairs them by *role*, not at random: a handwritten face always sits with a
readable companion, an editorial serif never carries a decorative script.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from PIL import ImageFont

CATEGORIES = ("handwritten", "serif", "sans", "typewriter", "decorative")


@dataclass
class FontFace:
    """One font file plus the axes a caller can turn."""

    path: Path
    category: str
    family: str = ""
    style: str = ""
    weight: int = 400
    variation_axes: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.family or self.path.stem

    @property
    def key(self) -> str:
        return f"{self.category}:{self.path.stem}"


@lru_cache(maxsize=256)
def _probe(path_str: str) -> tuple[str, str, tuple[str, ...]]:
    path = Path(path_str)
    try:
        font = ImageFont.truetype(str(path), 32)
    except (OSError, ValueError):
        return path.stem, "Regular", ()
    try:
        family, style = font.getname()
        axes: tuple[str, ...] = ()
        try:
            axes = tuple(ax["name"] for ax in font.get_variation_axes())
        except (AttributeError, OSError):
            axes = ()
        return family or path.stem, style or "Regular", axes
    except (OSError, ValueError):
        return path.stem, "Regular", ()


@lru_cache(maxsize=256)
def load_font(path_str: str, size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    """Load a font at a size, setting the weight axis for variable fonts."""
    path = Path(path_str)
    font = ImageFont.truetype(str(path), max(6, int(size)))
    family, style, axes = _probe(path_str)
    if "Weight" in axes:
        try:
            font.set_variation_by_axes([float(weight)])
        except (OSError, ValueError):
            try:
                font.set_variation_by_name("Regular")
            except (OSError, ValueError):
                pass
    del family, style
    return font


class FontRegistry:
    """All fonts the engine may use, grouped by category."""

    def __init__(self, fonts_dir: str | Path) -> None:
        self.root = Path(fonts_dir)
        self.faces: list[FontFace] = []
        self._by_category: dict[str, list[FontFace]] = {c: [] for c in CATEGORIES}
        self._discover()

    def _discover(self) -> None:
        if not self.root.exists():
            return
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {".ttf", ".otf"}:
                continue
            category = path.parent.name.lower()
            if category not in self._by_category:
                category = self._guess_category(path)
            family, style, axes = _probe(str(path))
            face = FontFace(
                path=path,
                category=category,
                family=family,
                style=style,
                weight=700 if "bold" in style.lower() else 400,
                variation_axes=list(axes),
            )
            self.faces.append(face)
            self._by_category.setdefault(category, []).append(face)

    @staticmethod
    def _guess_category(path: Path) -> str:
        stem = path.stem.lower()
        if any(k in stem for k in ("mono", "courier", "type", "elite")):
            return "typewriter"
        if any(k in stem for k in ("hand", "caveat", "kalam", "flower", "shadow", "script", "vibes")):
            return "handwritten"
        if any(k in stem for k in ("serif", "baskerville", "playfair", "cormorant", "garamond", "times", "roman")):
            return "serif"
        if any(k in stem for k in ("lobster", "pacifico", "satisfy", "display")):
            return "decorative"
        return "sans"

    def category(self, name: str) -> list[FontFace]:
        return list(self._by_category.get(name, []))

    def all(self) -> list[FontFace]:
        return list(self.faces)

    def get(self, key: str) -> FontFace | None:
        for face in self.faces:
            if face.key == key or face.path.stem == key:
                return face
        return None

    def has(self, category: str) -> bool:
        return bool(self._by_category.get(category))


# --- pairing rules -------------------------------------------------------
# Each identity names the role that carries the message and the role that
# carries supporting information, plus which categories are acceptable.
@dataclass(frozen=True)
class FontRole:
    category: str
    weight: int = 400
    tracking: float = 0.0


@dataclass(frozen=True)
class PairingRule:
    primary: FontRole
    secondary: FontRole
    allowed: tuple[str, ...]


PAIRING_RULES: dict[str, PairingRule] = {
    "scrapbook": PairingRule(
        primary=FontRole("handwritten", 400),
        secondary=FontRole("sans", 400),
        allowed=("handwritten", "sans", "typewriter"),
    ),
    "diary": PairingRule(
        primary=FontRole("handwritten", 400),
        secondary=FontRole("handwritten", 400),
        allowed=("handwritten", "serif", "sans"),
    ),
    "memory": PairingRule(
        primary=FontRole("serif", 400),
        secondary=FontRole("typewriter", 400),
        allowed=("serif", "typewriter", "handwritten"),
    ),
    "editorial": PairingRule(
        primary=FontRole("serif", 700),
        secondary=FontRole("sans", 400),
        allowed=("serif", "sans"),
    ),
    "letter": PairingRule(
        primary=FontRole("typewriter", 400),
        secondary=FontRole("handwritten", 400),
        allowed=("typewriter", "handwritten", "serif"),
    ),
    "minimal": PairingRule(
        primary=FontRole("sans", 400),
        secondary=FontRole("sans", 400),
        allowed=("sans", "serif"),
    ),
    "playful": PairingRule(
        primary=FontRole("decorative", 400),
        secondary=FontRole("sans", 400),
        allowed=("decorative", "sans", "handwritten"),
    ),
}


def pair_fonts(registry: FontRegistry, identity: str, rng, avoid: Iterable[str] = ()) -> tuple[FontFace, FontFace]:
    """Choose a compatible (primary, secondary) font pair.

    ``rng`` is a seeded ``random.Random``; ``avoid`` holds recently used
    font keys so the same pair does not repeat back to back.
    """
    rule = PAIRING_RULES.get(identity, PAIRING_RULES["minimal"])
    avoid_set = set(avoid)

    def pick(role: FontRole) -> FontFace | None:
        candidates = [f for f in registry.category(role.category)]
        if not candidates:
            for fallback in rule.allowed:
                candidates = registry.category(fallback)
                if candidates:
                    break
        if not candidates:
            return None
        fresh = [f for f in candidates if f.key not in avoid_set]
        pool = fresh or candidates
        return rng.choice(pool)

    primary = pick(rule.primary)
    if primary is None:
        primary = rng.choice(registry.all()) if registry.all() else None
    if primary is None:
        raise RuntimeError("no fonts available — run tools/fetch_fonts.py")
    secondary = pick(rule.secondary)
    if secondary is None or secondary.key == primary.key:
        pool = [f for f in registry.all() if f.key != primary.key]
        secondary = rng.choice(pool) if pool else primary
    return primary, secondary
