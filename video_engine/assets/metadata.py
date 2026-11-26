"""Asset inspection.

Reads dimensions and metadata out of images, videos and audio files without
loading whole files into memory, and keeps a small on-disk thumbnail cache
for the assets the operator wants to look at.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

Image.MAX_IMAGE_PIXELS = 120_000_000


@dataclass
class MediaInfo:
    path: Path
    kind: str
    width: int = 0
    height: int = 0
    duration: float = 0.0
    mode: str = ""
    has_alpha: bool = False
    frames: int = 0
    fps: float = 0.0
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def aspect(self) -> float:
        return (self.width / self.height) if self.height else 0.0

    @property
    def landscape(self) -> bool:
        return self.width > self.height


def probe_media(path: str | Path) -> MediaInfo:
    """Inspect a media file with ffprobe, falling back to Pillow."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}:
        return _probe_image(path)
    if suffix in {".mp4", ".mov", ".m4v", ".webm", ".mkv"}:
        return _probe_video(path)
    if suffix in {".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac"}:
        return _probe_audio(path)
    return MediaInfo(path=path, kind="unknown")


def _probe_image(path: Path) -> MediaInfo:
    info = MediaInfo(path=path, kind="image")
    try:
        with Image.open(path) as image:
            info.width, info.height = image.size
            info.mode = image.mode
            info.has_alpha = image.mode in {"RGBA", "LA"} or "transparency" in image.info
    except (OSError, ValueError):
        pass
    return info


def _ffprobe(path: Path) -> dict[str, Any]:
    binary = shutil.which("ffprobe")
    if not binary:
        return {}
    try:
        result = subprocess.run(
            [
                binary,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_streams",
                "-show_format",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return {}
    if result.returncode != 0:
        return {}
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {}


def _probe_video(path: Path) -> MediaInfo:
    info = MediaInfo(path=path, kind="video")
    payload = _ffprobe(path)
    for stream in payload.get("streams", []):
        if stream.get("codec_type") == "video":
            info.width = int(stream.get("width") or 0)
            info.height = int(stream.get("height") or 0)
            fps = stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "0/1"
            try:
                num, den = fps.split("/")
                info.fps = float(num) / float(den) if float(den) else 0.0
            except (ValueError, ZeroDivisionError):
                info.fps = 0.0
            info.frames = int(stream.get("nb_frames") or 0)
        elif stream.get("codec_type") == "audio":
            info.has_alpha = False
    info.duration = float(payload.get("format", {}).get("duration") or 0.0)
    return info


def _probe_audio(path: Path) -> MediaInfo:
    info = MediaInfo(path=path, kind="audio")
    payload = _ffprobe(path)
    info.duration = float(payload.get("format", {}).get("duration") or 0.0)
    return info


def make_thumbnail(path: str | Path, dest_dir: str | Path, size: tuple[int, int] = (320, 320)) -> Path | None:
    """Write a small JPEG preview of an image and return its path."""
    path = Path(path)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / f"{path.stem}.jpg"
    if target.exists():
        return target
    try:
        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail(size)
            image.save(target, "JPEG", quality=82)
    except (OSError, ValueError):
        return None
    return target
