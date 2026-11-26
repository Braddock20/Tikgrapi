"""Integration with the existing TikTok autoposter.

The engine never replaces the posting system and never touches it. It
produces a finished MP4 and hands it over in one of two ways:

``queue``
    The file (plus a sidecar ``.txt`` caption, which the existing
    ``AutoPoster`` already understands) is copied into the autoposter's
    queue directory. The autoposter picks it up on its own schedule.

``upload``
    The file is posted directly through the existing adapter — the same
    ``upload`` operation the API and the bot use. Nothing new is invented
    and no write path is bypassed.

Generation and posting stay separate: a video is only ever handed over
after it has been rendered *and* validated.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

MODE_NONE = "none"
MODE_QUEUE = "queue"
MODE_UPLOAD = "upload"


@dataclass
class PublishResult:
    """The outcome of handing a video over."""

    submitted: bool = False
    mode: str = MODE_NONE
    path: str = ""
    detail: str = ""
    result: dict | None = None

    def as_dict(self) -> dict:
        return {
            "submitted": self.submitted,
            "mode": self.mode,
            "path": self.path,
            "detail": self.detail,
            "result": self.result,
        }


class AutoPosterBridge:
    """Hands finished videos to the existing posting pipeline."""

    def __init__(self, queue_dir: str | Path, *, default_caption: str = "", tags: list[str] | None = None) -> None:
        self.queue_dir = Path(queue_dir)
        self.default_caption = default_caption
        self.tags = tags or []

    # -- queue mode ------------------------------------------------------
    def build_caption(self, caption: str) -> str:
        caption = (caption or self.default_caption).strip()
        if not self.tags:
            return caption
        joined = " ".join(f"#{tag.lstrip('#')}" for tag in self.tags)
        return f"{caption} {joined}".strip()

    def queue_video(self, video_path: str | Path, caption: str = "") -> PublishResult:
        """Copy the video and its caption into the autoposter queue."""
        video_path = Path(video_path)
        if not video_path.exists():
            return PublishResult(False, MODE_QUEUE, str(video_path), "the video does not exist")
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        target = self.queue_dir / video_path.name
        try:
            if target.resolve() != video_path.resolve():
                shutil.copy2(video_path, target)
        except OSError as exc:
            return PublishResult(False, MODE_QUEUE, str(target), f"could not copy the video: {exc}")
        sidecar = target.with_suffix(".txt")
        try:
            sidecar.write_text(self.build_caption(caption), encoding="utf-8")
        except OSError as exc:
            return PublishResult(False, MODE_QUEUE, str(target), f"could not write the caption: {exc}")
        return PublishResult(True, MODE_QUEUE, str(target), "queued for the autoposter")

    # -- direct upload ---------------------------------------------------
    async def upload_video(self, video_path: str | Path, caption: str = "") -> PublishResult:
        """Post the video through the existing adapter."""
        video_path = Path(video_path)
        if not video_path.exists():
            return PublishResult(False, MODE_UPLOAD, str(video_path), "the video does not exist")

        try:
            from app.adapters.tiktok import TikTokAdapter
        except ImportError as exc:
            return PublishResult(
                False, MODE_UPLOAD, str(video_path), f"the posting adapter is not available: {exc}"
            )

        adapter = TikTokAdapter()
        try:
            await adapter.start()
            result = await adapter.call(
                "upload",
                {"video_path": str(video_path), "caption": self.build_caption(caption)},
            )
        except Exception as exc:  # noqa: BLE001
            log.error("the upload failed: %s: %s", type(exc).__name__, exc)
            return PublishResult(False, MODE_UPLOAD, str(video_path), f"{type(exc).__name__}: {exc}")
        finally:
            try:
                await adapter.stop()
            except Exception:  # noqa: BLE001
                pass
        return PublishResult(True, MODE_UPLOAD, str(video_path), "posted", result if isinstance(result, dict) else {"raw": result})

    async def submit(self, video_path: str | Path, caption: str = "", mode: str = MODE_QUEUE) -> PublishResult:
        """Hand a validated video over to the posting pipeline."""
        if mode == MODE_UPLOAD:
            return await self.upload_video(video_path, caption)
        if mode == MODE_QUEUE:
            return self.queue_video(video_path, caption)
        return PublishResult(False, MODE_NONE, str(video_path), "publishing is disabled")


def submit(video_path: str | Path, caption: str = "") -> PublishResult:
    """The interface the specification sketches, in its simplest form."""
    from video_engine.config import settings

    bridge = AutoPosterBridge(settings.queue_dir)
    return bridge.queue_video(video_path, caption)
