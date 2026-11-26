"""The creative composition engine.

Takes a piece of content and turns it into a complete design: an identity, a
palette, a layout, a font pairing, a background, decorations and an
animation plan. Every choice is constrained — by the content's mood, by the
assets that are actually available, and by a history of what was used
recently — so two videos never come out the same, and neither comes out
unreadable.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Sequence

from video_engine.assets.fonts import FontFace, FontRegistry, pair_fonts
from video_engine.content.loader import split_text
from video_engine.content.selector import Selection
from video_engine.design.layouts import LAYOUTS, PHOTO_LAYOUTS, LayoutContext
from video_engine.design.model import (
    KIND_DECORATION,
    KIND_IMAGE,
    KIND_SURFACE,
    KIND_TEXT,
    ROLE_DECORATION,
    ROLE_PRIMARY_TEXT,
    ROLE_SECONDARY_TEXT,
    Composition,
    Element,
    Page,
)
from video_engine.design.palettes import Palette, pick_palette
from video_engine.design.positioning import avoid_text_collisions
from video_engine.design.randomness import RandomSource, pick_fresh, weighted_pick


@dataclass(frozen=True)
class VisualIdentity:
    """A design family — a set of tendencies, not a template."""

    name: str
    label: str
    layouts: tuple[str, ...]
    font_identity: str
    decoration_density: float
    mood_affinity: tuple[str, ...] = ()
    category_affinity: tuple[str, ...] = ()
    prefers_photos: bool = False


IDENTITIES: dict[str, VisualIdentity] = {
    "scrapbook": VisualIdentity(
        name="scrapbook",
        label="Physical scrapbook",
        layouts=("asymmetric_scrapbook", "polaroid_collage", "layered_cards", "scattered_notes"),
        font_identity="scrapbook",
        decoration_density=0.85,
        mood_affinity=("energetic", "funny", "reflective"),
        prefers_photos=True,
    ),
    "diary": VisualIdentity(
        name="diary",
        label="Personal diary",
        layouts=("centered_diary", "handwritten_letter", "layered_cards", "scattered_notes"),
        font_identity="diary",
        decoration_density=0.45,
        mood_affinity=("reflective", "calm", "melancholic"),
        category_affinity=("reflection", "feeling"),
    ),
    "memory": VisualIdentity(
        name="memory",
        label="Nostalgic memory archive",
        layouts=("memory_archive", "polaroid_collage", "centered_diary", "layered_stationery"),
        font_identity="memory",
        decoration_density=0.4,
        mood_affinity=("nostalgic", "melancholic"),
        category_affinity=("memory", "nostalgia"),
        prefers_photos=True,
    ),
    "editorial": VisualIdentity(
        name="editorial",
        label="Editorial magazine",
        layouts=("magazine_editorial", "full_page_photo", "minimalist_typography"),
        font_identity="editorial",
        decoration_density=0.18,
        mood_affinity=("reflective", "intense", "neutral"),
        category_affinity=("quote", "story"),
    ),
    "letter": VisualIdentity(
        name="letter",
        label="Personal letter",
        layouts=("handwritten_letter", "typewriter_document", "layered_stationery"),
        font_identity="letter",
        decoration_density=0.5,
        mood_affinity=("romantic", "nostalgic", "melancholic"),
        category_affinity=("relationship", "nostalgia"),
    ),
    "minimal": VisualIdentity(
        name="minimal",
        label="Minimalist journal",
        layouts=("minimalist_typography", "magazine_editorial", "centered_diary"),
        font_identity="minimal",
        decoration_density=0.12,
        mood_affinity=("calm", "neutral"),
    ),
    "playful": VisualIdentity(
        name="playful",
        label="Playful collage",
        layouts=("scattered_notes", "polaroid_collage", "layered_cards"),
        font_identity="playful",
        decoration_density=0.95,
        mood_affinity=("funny", "energetic", "hopeful"),
        category_affinity=("humour", "question", "conversation"),
        prefers_photos=True,
    ),
}

FORMAT_TO_IDENTITY = {
    "diary": "diary",
    "scrapbook": "scrapbook",
    "editorial": "editorial",
    "letter": "letter",
    "nostalgic": "memory",
    "minimal": "minimal",
    "story": "editorial",
}

KICKERS = {
    "nostalgic": [
        "SUMMER '98", "BACK THEN", "SOMEWHERE IN 2007", "NOV 12", "THEN",
        "WINTER PICTURES", "AUG 03", "OLD FRAMES", "SEPT 21", "THE OTHER SUMMER",
    ],
    "memory": [
        "ARCHIVE 04", "SOMEWHERE IN 2007", "SUMMER '98", "AUG 03",
        "BOX 12", "RECOVERED", "NO DATE", "THE ALBUM", "FRAGMENT 07",
    ],
    "reflection": [
        "NOTES TO SELF", "03:14 AM", "A QUIET THOUGHT", "UNTITLED",
        "THROUGHT ON A WALK", "ENTRY 12", "IN THE DARK", "DRAFT",
    ],
    "question": [
        "HONESTLY?", "WHAT DO YOU THINK?", "A QUESTION", "CURIOUS",
        "NO IDEA", "WONDERING", "ASKING FOR ME", "THINK ABOUT IT",
    ],
    "humour": [
        "NOBODY ASKED", "PS:", "UPDATE", "ME, PROBABLY", "SCENE:",
        "THIS IS FINE", "CONFESSION", "NOTE TO SELF",
    ],
    "story": [
        "CHAPTER ONE", "IT STARTED LIKE THIS", "ONE NIGHT", "BEFORE",
        "THE LONG VERSION", "PART ONE", "AT THE TIME",
    ],
    "relationship": [
        "TO YOU", "DEAR YOU", "SINCE THEN", "ALWAYS", "FOR THE RECORD",
        "AFTERWARDS", "NEAR YOU", "US",
    ],
    "observation": [
        "NOTICED", "TODAY", "FIELD NOTES", "A THOUGHT", "GENERAL",
        "IN THE WILD", "CORRECTLY, PROBABLY",
    ],
    "caption": ["POV", "READ THAT AGAIN", "NOBODY", "SCENE:", "SERIOUSLY"],
    "feeling": ["FEELING", "HONESTLY", "AT 2AM", "NOT GREAT", "QUIET DAY"],
}

SIGNOFFS = [
    "— me", "yours, always", "still thinking", "tonight", "from the archive",
    "— always me", "and that is that", "no regrets", "— later", "keep this",
    "written at dawn", "— nobody",
]


class Composer:
    """Builds compositions out of the available assets."""

    def __init__(
        self,
        *,
        fonts: FontRegistry,
        asset_library,
        profile,
        rng_source: RandomSource | None = None,
        recent: Sequence[dict] | None = None,
    ) -> None:
        self.fonts = fonts
        self.assets = asset_library
        self.profile = profile
        self.rng_source = rng_source or RandomSource(0)
        self.recent = list(recent or [])

    # -- history ---------------------------------------------------------
    def _recent_keys(self, field: str) -> list[str]:
        return [str(row.get(field)) for row in self.recent if row.get(field)]

    def _recent_decorations(self) -> set[str]:
        """The decoration names used by the recent compositions."""
        names: set[str] = set()
        for row in self.recent:
            value = str(row.get("decoration_combination") or "")
            for part in value.replace("+", ",").split(","):
                part = part.strip()
                if part:
                    names.add(part)
        return names

    # -- identity --------------------------------------------------------
    def choose_identity(self, selection: Selection, rng: random.Random) -> VisualIdentity:
        item = selection.item
        declared = FORMAT_TO_IDENTITY.get(item.format)
        candidates: list[tuple[str, float]] = []
        for identity in IDENTITIES.values():
            weight = 1.0
            if identity.name == declared:
                weight += 4.0
            if selection.category in identity.category_affinity:
                weight += 2.5
            if selection.mood in identity.mood_affinity:
                weight += 2.0
            if identity.name == "editorial" and selection.content_type == "story":
                weight += 1.5
            if identity.name == "memory" and selection.content_type == "memory":
                weight += 1.5
            if identity.name == "playful" and selection.content_type == "question":
                weight += 1.5
            candidates.append((identity.name, weight))

        # Recently used identities are pushed down, not banned.
        avoid = set(self._recent_keys("visual_identity"))
        weights = [w * (0.35 if name in avoid else 1.0) for name, w in candidates]
        chosen = weighted_pick([name for name, _ in candidates], rng, weights)
        return IDENTITIES.get(chosen or "minimal", IDENTITIES["minimal"])

    # -- layout ----------------------------------------------------------
    def choose_layout(self, identity: VisualIdentity, rng: random.Random, has_photos: bool) -> str:
        pool = list(identity.layouts)
        if not has_photos:
            pool = [name for name in pool if name not in PHOTO_LAYOUTS] or pool
        if not pool:
            pool = list(LAYOUTS.keys())
        avoid = self._recent_keys("layout")
        chosen = pick_fresh(pool, rng, avoid=avoid, key=lambda n: n)
        return chosen or pool[0]

    # -- background ------------------------------------------------------
    def choose_background(self, rng: random.Random, palette: Palette) -> dict:
        videos = self.assets.background_videos()
        images = self.assets.backgrounds()
        textures = self.assets.textures()
        if videos and rng.random() < 0.18:
            asset = rng.choice(videos)
            return {"type": "video", "asset_id": asset.id, "path": str(asset.path)}
        if images:
            asset = rng.choice(images)
            return {"type": "image", "asset_id": asset.id, "path": str(asset.path)}
        if textures:
            asset = rng.choice(textures)
            return {"type": "image", "asset_id": asset.id, "path": str(asset.path)}
        return {"type": "paper", "colour": palette.paper}

    # -- text furniture --------------------------------------------------
    @staticmethod
    def _kicker(selection: Selection, rng: random.Random) -> str:
        item = selection.item
        pool = KICKERS.get(selection.category) or KICKERS.get(item.mood) or KICKERS["reflection"]
        return rng.choice(pool)

    @staticmethod
    def _signoff(rng: random.Random) -> str:
        return rng.choice(SIGNOFFS)

    # -- decorations -----------------------------------------------------
    def _add_extra_decorations(
        self,
        page: Page,
        identity: VisualIdentity,
        palette: Palette,
        rng: random.Random,
        profile,
    ) -> None:
        """Scatter a few extra objects so no page looks machine-made."""
        count = int(round(identity.decoration_density * 3))
        kinds = ["star", "flower", "heart", "splat", "scribble", "highlight", "tape", "moon"]
        # The decorations used in the recent compositions are pushed down, so
        # a batch does not keep showing the same little star.
        recent = self._recent_decorations()
        if recent:
            kinds = [k for k in kinds if k not in recent] or kinds
        for _ in range(count):
            kind = rng.choice(kinds)
            size = rng.uniform(0.05, 0.11)
            x = rng.uniform(0.12, 0.88)
            y = rng.uniform(0.08, 0.92)
            page.elements.append(
                Element(
                    kind=KIND_DECORATION,
                    x=x,
                    y=y,
                    width=size,
                    height=size * rng.uniform(0.6, 1.1),
                    rotation=rng.uniform(-25, 25),
                    opacity=rng.uniform(0.55, 0.92),
                    z_index=rng.choice([9, 11, 13]),
                    role=ROLE_DECORATION,
                    payload={"decoration": kind},
                )
            )

    # -- build -----------------------------------------------------------
    def build(self, selection: Selection, *, seed: int | None = None) -> Composition:
        rng = self.rng_source.rng("compose", selection.item.id)
        identity = self.choose_identity(selection, rng)
        palette = pick_palette(
            rng,
            identity=identity.name,
            mood=selection.mood,
            avoid=self._recent_keys("palette"),
        )
        photos = self.assets.photos()
        layout_name = self.choose_layout(identity, rng, has_photos=bool(photos))
        primary, secondary = pair_fonts(
            self.fonts,
            identity.font_identity,
            rng,
            avoid=self._recent_keys("font_combination"),
        )

        # Split a long item into pages of readable length.
        chunks = split_text(selection.item.text, max_chars=190 if len(selection.item.text) > 260 else 260)
        if not chunks:
            chunks = [selection.item.text]

        total = max(6.0, selection.duration)
        per_page = max(2.6, total / len(chunks))
        if len(chunks) > 1:
            per_page = max(2.8, total / len(chunks))

        background = self.choose_background(rng, palette)
        kicker = self._kicker(selection, rng)
        signoff = self._signoff(rng)

        pages: list[Page] = []
        for index, chunk in enumerate(chunks):
            ctx = LayoutContext(
                rng=rng,
                palette=palette,
                primary=primary,
                secondary=secondary,
                width=self.profile.width,
                height=self.profile.height,
                body=chunk,
                kicker=kicker if index == 0 else "",
                signature=signoff if index == len(chunks) - 1 else "",
                caption=" ".join(chunk.split()[:4]),
                photos=photos,
                textures=self.assets.textures(),
                identity=identity.name,
            )
            builder = LAYOUTS.get(layout_name, LAYOUTS["centered_diary"])
            elements = builder(ctx)
            page = Page(
                index=index,
                elements=elements,
                duration=per_page,
                transition="fade" if index == 0 else rng.choice(["fade", "slide", "dissolve"]),
                background=background,
            )
            self._add_extra_decorations(page, identity, palette, rng, self.profile)
            avoid_text_collisions(page.elements)
            pages.append(page)

        composition = Composition(
            pages=pages,
            identity=identity.name,
            layout=layout_name,
            palette=palette.as_dict(),
            fonts={
                "primary": primary.key,
                "secondary": secondary.key,
                "primary_path": str(primary.path),
                "secondary_path": str(secondary.path),
            },
            background=background,
            seed=seed if seed is not None else self.rng_source.base_seed,
            content_id=selection.item.id,
            notes=f"content_type={selection.content_type} mood={selection.mood} intensity={selection.intensity}",
        )
        return composition
