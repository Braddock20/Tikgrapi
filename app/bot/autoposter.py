"""Automatic posting.

Watches a directory of videos and posts them to the account on a schedule,
so a batch of content can be queued once and published without anyone
watching.

Caption resolution, in order:
  1. a sidecar file next to the video — ``clip.mp4`` -> ``clip.txt``
  2. the next line of a caption list (one caption per line, cycled)
  3. the default caption

Already-posted files are remembered in a state file, so a restart never
reposts. The engine's own write gates still apply: ``ALLOW_WRITE_OPS`` and
the operator's pause switch.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

from app.adapters.upload import VIDEO_SUFFIXES

log = logging.getLogger(__name__)


class AutoPoster:
    """Posts videos from a queue directory."""

    def __init__(
        self,
        engine,
        directory: str | Path,
        *,
        captions: str | Path | None = None,
        default_caption: str = "",
        interval: float = 3600.0,
        limit: int | None = None,
        delete_after: bool = False,
        visibility: int = 0,
        loop: bool = False,
        state_path: str | Path | None = None,
        tags: list[str] | None = None,
    ) -> None:
        self.engine = engine
        self.directory = Path(directory)
        self.captions_file = Path(captions) if captions else None
        self.default_caption = default_caption
        self.interval = max(0.0, interval)
        self.limit = limit
        self.delete_after = delete_after
        self.visibility = visibility
        self.loop = loop
        self.tags = tags or []
        self.state_path = Path(state_path) if state_path else self.directory / ".posted.json"
        self.posted: dict[str, dict] = {}
        self._caption_index = 0

    # -- state -----------------------------------------------------------
    def load_state(self) -> None:
        try:
            self.posted = json.loads(self.state_path.read_text())
        except (OSError, json.JSONDecodeError):
            self.posted = {}

    def save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.posted, indent=1))
        tmp.replace(self.state_path)

    # -- queue -----------------------------------------------------------
    def pending(self) -> list[Path]:
        if not self.directory.is_dir():
            return []
        files = [
            path
            for path in self.directory.iterdir()
            if path.is_file()
            and path.suffix.lower() in VIDEO_SUFFIXES
            and not path.name.startswith(".")
        ]
        unseen = []
        for path in sorted(files, key=lambda p: (p.stat().st_mtime, p.name)):
            stat = path.stat()
            record = self.posted.get(path.name)
            if record and record.get("size") == stat.st_size:
                continue
            unseen.append(path)
        return unseen

    def caption_for(self, video: Path) -> str:
        sidecar = video.with_suffix(".txt")
        if sidecar.exists():
            return sidecar.read_text().strip()
        if self.captions_file and self.captions_file.exists():
            lines = [
                line.strip()
                for line in self.captions_file.read_text().splitlines()
                if line.strip() and not line.startswith("#")
            ]
            if lines:
                caption = lines[self._caption_index % len(lines)]
                self._caption_index += 1
                return caption
        return self.default_caption

    def build_caption(self, caption: str) -> str:
        if not self.tags:
            return caption
        joined = " ".join(f"#{tag.lstrip('#')}" for tag in self.tags)
        return f"{caption} {joined}".strip()

    # -- posting ---------------------------------------------------------
    async def post_next(self) -> dict | None:
        """Post the next queued video. Returns the result, or None if idle."""
        queue = self.pending()
        if not queue:
            return None
        video = queue[0]
        caption = self.build_caption(self.caption_for(video))
        log.info("posting %s", video.name)
        try:
            result = await self.engine.call(
                "upload",
                {
                    "video_path": str(video),
                    "caption": caption,
                    "visibility": self.visibility,
                },
            )
        except Exception as exc:  # noqa: BLE001
            log.error("upload of %s failed: %s: %s", video.name, type(exc).__name__, exc)
            # remember the failure so a broken file does not block the queue
            stat = video.stat()
            self.posted[video.name] = {"size": stat.st_size, "failed": True, "at": time.time()}
            self.save_state()
            return {"error": f"{type(exc).__name__}: {exc}", "video_path": str(video)}

        stat = video.stat()
        self.posted[video.name] = {
            "size": stat.st_size,
            "aweme_id": (result or {}).get("aweme_id"),
            "at": time.time(),
        }
        self.save_state()
        if self.delete_after:
            try:
                video.unlink()
            except OSError:
                pass
        return result

    async def run_forever(self) -> None:
        self.load_state()
        self.directory.mkdir(parents=True, exist_ok=True)
        posted_count = 0
        while True:
            result = await self.post_next()
            if result is None:
                if not self.loop:
                    log.info("queue is empty — nothing to post")
                    return
                await asyncio.sleep(max(self.interval, 60))
                continue
            if "error" not in (result or {}):
                posted_count += 1
                log.info("posted: %s", result)
            if self.limit and posted_count >= self.limit:
                log.info("reached the limit of %s posts", self.limit)
                return
            await asyncio.sleep(self.interval)

    async def run_once(self, count: int = 1) -> list[dict]:
        """Post up to ``count`` videos and return the results."""
        self.load_state()
        self.directory.mkdir(parents=True, exist_ok=True)
        results = []
        for _ in range(count):
            result = await self.post_next()
            if result is None:
                break
            results.append(result)
            if self.interval:
                await asyncio.sleep(self.interval)
        return results
