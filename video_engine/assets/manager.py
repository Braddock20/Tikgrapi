"""The asset library.

Everything under ``assets/`` is inspected once and grouped into pools the
composition engine draws from. Categories come from the directory structure
first and from the file name second, so a library can be organised either
way.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from video_engine.assets.metadata import MediaInfo, probe_media

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
AUDIO_SUFFIXES = {".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac"}

# directory name -> logical pool
POOL_BY_DIR = {
    "backgrounds": "background",
    "photographs": "photo",
    "photos": "photo",
    "overlays": "overlay",
    "textures": "texture",
    "stickers": "sticker",
    "tape": "tape",
    "paper": "paper",
    "stamps": "stamp",
    "doodles": "doodle",
    "frames": "frame",
    "decorations": "decoration",
}

KEYWORD_POOL = {
    "tape": "tape",
    "washi": "tape",
    "sticker": "sticker",
    "stamp": "stamp",
    "doodle": "doodle",
    "polaroid": "frame",
    "frame": "frame",
    "paper": "paper",
    "texture": "texture",
    "grain": "overlay",
    "overlay": "overlay",
    "photo": "photo",
    "background": "background",
}


@dataclass
class Asset:
    """One usable asset file."""

    id: str
    path: Path
    pool: str
    kind: str
    info: MediaInfo = field(default_factory=lambda: MediaInfo(path=Path("."), kind="unknown"))
    mood: str = "neutral"
    tags: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def stem(self) -> str:
        return self.path.stem


def _pool_for(path: Path, root: Path) -> str:
    parts = [p.lower() for p in path.relative_to(root).parts[:-1]]
    for part in parts:
        if part in POOL_BY_DIR:
            return POOL_BY_DIR[part]
    stem = path.stem.lower()
    for keyword, pool in KEYWORD_POOL.items():
        if keyword in stem:
            return pool
    suffix = path.suffix.lower()
    if suffix in VIDEO_SUFFIXES:
        return "background"
    if suffix in IMAGE_SUFFIXES:
        return "photo"
    return "decoration"


def _mood_for(path: Path) -> str:
    stem = path.stem.lower()
    table = {
        "nostalgic": ("nostalgic", "vintage", "retro", "old", "faded", "sepia", "memory"),
        "warm": ("warm", "sunset", "golden", "amber", "cosy", "cozy"),
        "cool": ("cool", "blue", "calm", "night", "winter", "ocean"),
        "dark": ("dark", "night", "black", "moody", "shadow"),
        "bright": ("bright", "white", "clean", "minimal", "light"),
    }
    for mood, words in table.items():
        if any(word in stem for word in words):
            return mood
    return "neutral"


class AssetLibrary:
    """All assets on disk, grouped into pools."""

    def __init__(self, assets_dir: str | Path) -> None:
        self.root = Path(assets_dir)
        self.assets: list[Asset] = []
        self._pools: dict[str, list[Asset]] = {}
        self._by_id: dict[str, Asset] = {}
        self.discover()

    # -- discovery -------------------------------------------------------
    def discover(self) -> None:
        self.assets = []
        self._pools = {}
        self._by_id = {}
        if not self.root.exists():
            return
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix not in IMAGE_SUFFIXES | VIDEO_SUFFIXES | AUDIO_SUFFIXES:
                continue
            if "fonts" in path.parts or "sounds" in path.parts:
                continue
            pool = _pool_for(path, self.root)
            if suffix in AUDIO_SUFFIXES:
                pool = "sound"
            kind = "video" if suffix in VIDEO_SUFFIXES else ("audio" if suffix in AUDIO_SUFFIXES else "image")
            asset_id = str(path.relative_to(self.root))
            asset = Asset(
                id=asset_id,
                path=path,
                pool=pool,
                kind=kind,
                info=probe_media(path),
                mood=_mood_for(path),
                tags=[p for p in path.stem.replace("-", "_").split("_") if p],
            )
            self.assets.append(asset)
            self._by_id[asset_id] = asset
            self._pools.setdefault(pool, []).append(asset)

    # -- access ----------------------------------------------------------
    def pool(self, name: str) -> list[Asset]:
        return list(self._pools.get(name, []))

    def get(self, asset_id: str) -> Asset | None:
        return self._by_id.get(asset_id)

    def photos(self) -> list[Asset]:
        return self.pool("photo")

    def backgrounds(self) -> list[Asset]:
        return [a for a in self.pool("background") if a.kind == "image"]

    def background_videos(self) -> list[Asset]:
        return [a for a in self.pool("background") if a.kind == "video"]

    def textures(self) -> list[Asset]:
        return self.pool("texture")

    def decorations(self) -> list[Asset]:
        out: list[Asset] = []
        for pool in ("sticker", "tape", "stamp", "doodle", "frame", "paper", "decoration", "overlay"):
            out.extend(self.pool(pool))
        return out

    def stickers(self) -> list[Asset]:
        return self.pool("sticker") + self.pool("doodle")

    def tapes(self) -> list[Asset]:
        return self.pool("tape")

    def stamps(self) -> list[Asset]:
        return self.pool("stamp")

    def frames(self) -> list[Asset]:
        return self.pool("frame")

    def stats(self) -> dict[str, int]:
        counts = {pool: len(items) for pool, items in sorted(self._pools.items())}
        counts["total"] = len(self.assets)
        return counts


def pick_asset(
    pool: Iterable[Asset],
    rng,
    *,
    avoid: Iterable[str] = (),
    prefer_mood: str | None = None,
) -> Asset | None:
    """Pick one asset, avoiding the ones used recently."""
    items = list(pool)
    if not items:
        return None
    avoid_set = set(avoid)
    fresh = [a for a in items if a.id not in avoid_set]
    if not fresh:
        fresh = items
    if prefer_mood:
        matched = [a for a in fresh if a.mood == prefer_mood]
        if matched:
            fresh = matched
    return rng.choice(fresh)


def asset_id(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


_SLUG = re.compile(r"[^a-z0-9]+")


def slug(text: str) -> str:
    return _SLUG.sub("-", text.lower()).strip("-")
