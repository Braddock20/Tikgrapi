"""The video engine.

Run it:

    python -m video_engine.engine generate
    python -m video_engine.engine generate --count 5
    python -m video_engine.engine generate --preview
    python -m video_engine.engine status

The engine is deliberately free of AI: every creative decision is made by
rules and seeded randomness, so a run is reproducible and costs nothing but
CPU.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import resource
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from video_engine.animation.timeline import assign_animations
from video_engine.assets.audio import SoundLibrary
from video_engine.assets.fonts import FontRegistry
from video_engine.assets.manager import AssetLibrary
from video_engine.config import EngineSettings, settings as default_settings
from video_engine.content.history import ContentHistory
from video_engine.content.loader import load_library
from video_engine.content.selector import ContentSelector, Selection
from video_engine.design.composer import Composer
from video_engine.design.randomness import RandomSource
from video_engine.design.validation import repair, validate_composition
from video_engine.integration.autoposter import MODE_NONE, MODE_QUEUE, MODE_UPLOAD, AutoPosterBridge
from video_engine.rendering.audio_mixer import build_audio_track, mux
from video_engine.rendering.ffmpeg import encode_frames, validate_output
from video_engine.rendering.image_renderer import SceneRenderer
from video_engine.rendering.profiles import get_profile
from video_engine.storage.cleanup import cleanup_workspace
from video_engine.storage.database import HistoryStore

log = logging.getLogger("video_engine")

HASHTAGS = {
    "thought": "thoughts",
    "reflection": "reflection",
    "memory": "memories",
    "nostalgia": "nostalgia",
    "question": "questions",
    "humour": "humor",
    "relationship": "relationships",
    "story": "stories",
    "feeling": "feelings",
    "caption": "pov",
}


@dataclass
class VideoResult:
    """The outcome of one generated video."""

    success: bool = False
    video_id: str = ""
    path: str = ""
    caption: str = ""
    content_id: str = ""
    composition: dict[str, Any] = field(default_factory=dict)
    report: dict[str, Any] = field(default_factory=dict)
    render_time: float = 0.0
    peak_memory_mb: float = 0.0
    error: str = ""
    published: bool = False
    publish_detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "video_id": self.video_id,
            "path": self.path,
            "caption": self.caption,
            "content_id": self.content_id,
            "composition": self.composition,
            "report": self.report,
            "render_time": round(self.render_time, 2),
            "peak_memory_mb": round(self.peak_memory_mb, 1),
            "error": self.error,
            "published": self.published,
            "publish_detail": self.publish_detail,
        }


def peak_memory_mb() -> float:
    """The peak resident memory of this process, in megabytes."""
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports kilobytes, macOS reports bytes.
    return usage / 1024.0 if sys.platform != "darwin" else usage / (1024.0 * 1024.0)


class VideoEngine:
    """The generator."""

    def __init__(self, config: EngineSettings | None = None) -> None:
        self.config = config or default_settings
        self.config.ensure_dirs()
        self.profile = get_profile(self.config.profile)
        self.store = HistoryStore(self.config.db_path)
        self.history = ContentHistory(self.store)
        self.fonts = FontRegistry(self.config.assets_dir / "fonts")
        self.assets = AssetLibrary(self.config.assets_dir)
        self.sounds = SoundLibrary(self.config.assets_dir / "sounds")
        self.rng_source = RandomSource(self.config.seed)
        self.bridge = AutoPosterBridge(self.config.queue_dir)

    # -- captions --------------------------------------------------------
    @staticmethod
    def build_caption(item, category: str) -> str:
        """A caption for the finished video, built from the content itself."""
        text = " ".join(item.text.split())
        if len(text) > 120:
            text = text[:117].rsplit(" ", 1)[0] + "…"
        tags = [t for t in item.tags if t.isalpha()][:3]
        if not tags:
            tag = HASHTAGS.get(category, "fyp")
            tags = [tag]
        joined = " ".join(f"#{t.replace(' ', '')}" for t in tags)
        return f"{text} {joined}".strip()

    # -- pipeline --------------------------------------------------------
    def generate_one(
        self,
        *,
        preview: bool = False,
        publish_mode: str = MODE_NONE,
        seed: int | None = None,
    ) -> VideoResult:
        """Produce one finished, validated video.

        A failed render is not the end of the road: the engine tries again
        with a different seed, which produces a different composition, so a
        broken frame or an unlucky asset does not waste a slot in the queue.
        """
        attempts = max(1, self.config.max_render_attempts)
        result = VideoResult()
        for attempt in range(attempts):
            attempt_seed = (seed if seed is not None else self.config.seed) + attempt * 9973
            try:
                result = self._attempt(preview=preview, publish_mode=publish_mode, seed=attempt_seed)
            except Exception as exc:  # noqa: BLE001
                log.exception("the render attempt blew up")
                result = VideoResult(error=f"{type(exc).__name__}: {exc}")
            if result.success:
                return result
            log.warning(
                "attempt %s/%s failed: %s — retrying with a different composition",
                attempt + 1,
                attempts,
                result.error,
            )
        return result

    def _attempt(
        self,
        *,
        preview: bool = False,
        publish_mode: str = MODE_NONE,
        seed: int | None = None,
    ) -> VideoResult:
        """One render attempt."""
        started = time.time()
        video_id = uuid.uuid4().hex[:12]
        result = VideoResult(video_id=video_id)

        library = load_library(self.config.content_dir)
        if not len(library):
            result.error = f"the content library is empty ({library.report.summary()})"
            return result

        selector = ContentSelector(
            list(library),
            usage=self.history.usage(),
            allow_reuse=self.config.allow_reuse,
            auto_recycle=True,
            seed=seed if seed is not None else self.config.seed,
        )
        selection = selector.select()
        if selection is None:
            result.error = "the content library is empty"
            return result
        if selector.recycled:
            log.info(
                "the library is exhausted — recycling '%s' with a new composition",
                selection.item.id,
            )

        item = selection.item
        result.content_id = item.id
        result.caption = self.build_caption(item, selection.category)

        # 1. sound
        sound = self.sounds.select(
            selection.mood,
            self.rng_source.rng("sound", item.id),
            avoid=list(self.store.sound_usage().keys()),
            duration=selection.duration,
        )

        # 2. composition
        composer = Composer(
            fonts=self.fonts,
            asset_library=self.assets,
            profile=self.profile,
            rng_source=self.rng_source,
            recent=self.store.recent_compositions(self.config.memory_window),
        )
        composition = composer.build(selection, seed=seed if seed is not None else self.config.seed)
        assign_animations(composition, self.rng_source.rng("timeline", item.id))

        # 3. validate (and repair once if needed)
        validation = validate_composition(composition, self.profile)
        if not validation.ok:
            repair(composition, self.profile)
            validation = validate_composition(composition, self.profile)
        if not validation.ok:
            result.error = "the composition did not pass validation: " + validation.summary()
            result.composition = composition.to_dict()
            return result

        # 4. render
        work_dir = self.config.work_dir / video_id
        work_dir.mkdir(parents=True, exist_ok=True)
        renderer = SceneRenderer(
            composition,
            self.profile,
            work_dir=work_dir,
            assets_root=self.config.assets_dir,
        )
        renderer.prepare()

        try:
            silent_video = work_dir / "video.mp4"
            frames = renderer.frames(composition.duration)
            encode_frames(frames, self.profile, silent_video)

            audio_plan = build_audio_track(sound, composition.duration, work_dir / "audio.m4a")
            target_dir = self.config.preview_dir if preview else self.config.output_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            final = target_dir / f"{video_id}.mp4"
            mux(silent_video, audio_plan.prepared_path, final, self.profile)

            report = validate_output(final, self.profile, expect_audio=True)
        except Exception as exc:  # noqa: BLE001
            log.exception("the render failed")
            result.error = f"{type(exc).__name__}: {exc}"
            self.history.release(item)
            return result
        finally:
            renderer.cleanup()
            cleanup_workspace(work_dir)

        result.render_time = time.time() - started
        result.peak_memory_mb = peak_memory_mb()
        result.report = report.as_dict()
        result.composition = composition.to_dict()
        result.path = str(final)

        if not report.ok:
            result.error = "; ".join(report.problems)
            self.history.release(item)
            return result

        # 5. history
        self.store.record_composition(
            {
                "composition_id": uuid.uuid4().hex,
                "visual_identity": composition.identity,
                "layout": composition.layout,
                "font_combination": f"{composition.fonts.get('primary')}+{composition.fonts.get('secondary')}",
                "background_id": (composition.background or {}).get("asset_id"),
                "decoration_combination": "+".join(
                    sorted(
                        {
                            str(e.payload.get("decoration"))
                            for e in composition.elements()
                            if e.payload.get("decoration")
                        }
                    )
                )[:200],
                "animation_combination": composition.identity,
                "palette": composition.palette.get("name"),
                "content_id": item.id,
                "seed": composition.seed,
            }
        )
        self.store.record_render(
            render_id=video_id,
            job_id=video_id,
            output_path=str(final),
            duration=report.duration,
            resolution=report.resolution if hasattr(report, "resolution") else f"{report.width}x{report.height}",
            render_time=result.render_time,
            status="completed",
            content_id=item.id,
        )
        if sound is not None:
            self.store.record_sound(sound.id, selection.category)

        # 6. hand over
        if publish_mode in (MODE_QUEUE, MODE_UPLOAD) and not preview:
            publish = self.bridge.queue_video(final, result.caption) if publish_mode == MODE_QUEUE else None
            if publish is not None:
                result.published = publish.submitted
                result.publish_detail = publish.detail
            self.history.mark_used(item, video_id, published=result.published)
        else:
            self.history.mark_used(item, video_id, published=False)

        result.success = True
        return result

    # -- CLI actions -----------------------------------------------------
    def maintenance(self) -> dict[str, Any]:
        """Housekeeping: clear the work directory and prune stale videos."""
        report = cleanup_workspace(
            self.config.work_dir,
            self.config.output_dir,
            max_age_days=14.0,
        )
        return {
            "files_removed": report.files_removed,
            "directories_removed": report.directories_removed,
            "bytes_removed": report.bytes_removed,
        }

    def generate(
        self,
        count: int = 1,
        *,
        preview: bool = False,
        publish_mode: str = MODE_NONE,
        seed: int | None = None,
    ) -> list[VideoResult]:
        results: list[VideoResult] = []
        # Housekeeping first: a long-running instance must not fill its disk.
        cleaned = self.maintenance()
        if cleaned["files_removed"]:
            log.info("maintenance: removed %s old file(s)", cleaned["files_removed"])
        for index in range(count):
            log.info("generating video %s/%s", index + 1, count)
            result = self.generate_one(preview=preview, publish_mode=publish_mode, seed=seed)
            results.append(result)
            if result.success:
                log.info(
                    "done: %s (%.1fs, %.0f MB peak)",
                    result.path,
                    result.render_time,
                    result.peak_memory_mb,
                )
            else:
                log.error("failed: %s", result.error)
        return results

    def watch(
        self,
        *,
        every: float,
        publish_mode: str = MODE_NONE,
        preview: bool = False,
        seed: int | None = None,
        max_runs: int | None = None,
    ) -> list[VideoResult]:
        """Keep producing videos on a schedule.

        This is the unattended mode: the engine renders, validates, queues
        and sleeps, over and over, until it is stopped. It keeps the
        library rotating, recycles content when it runs dry and cleans up
        after itself.
        """
        results: list[VideoResult] = []
        runs = 0
        stop = {"flag": False}

        def _stop(signum, frame):  # pragma: no cover - signal path
            stop["flag"] = True
            log.info("stopping after this run")

        try:
            import signal

            signal.signal(signal.SIGTERM, _stop)
            signal.signal(signal.SIGINT, _stop)
        except (ValueError, OSError):  # pragma: no cover
            pass

        while not stop["flag"]:
            result = self.generate_one(preview=preview, publish_mode=publish_mode, seed=seed)
            results.append(result)
            runs += 1
            if result.success:
                log.info("run %s done: %s", runs, result.path)
            else:
                log.error("run %s failed: %s", runs, result.error)
            if max_runs and runs >= max_runs:
                break
            if stop["flag"]:
                break
            # Sleep in small steps so a stop signal is noticed quickly.
            remaining = max(0.0, every)
            while remaining > 0 and not stop["flag"]:
                step = min(5.0, remaining)
                time.sleep(step)
                remaining -= step
        return results

    def status(self) -> dict[str, Any]:
        library = load_library(self.config.content_dir)
        usage = self.history.usage()
        used = sum(1 for value in usage.values() if value > 0)
        return {
            "engine": {
                "profile": self.profile.as_dict(),
                "content_dir": str(self.config.content_dir),
                "assets_dir": str(self.config.assets_dir),
                "output_dir": str(self.config.output_dir),
                "queue_dir": str(self.config.queue_dir),
                "database": str(self.config.db_path),
            },
            "content": {
                "files": library.report.files,
                "items": len(library),
                "used": used,
                "remaining": max(0, len(library) - used),
                "report": library.report.summary(),
                "problems": library.report.errors[:10],
            },
            "assets": {
                "fonts": len(self.fonts.all()),
                "font_categories": {name: len(self.fonts.category(name)) for name in ("handwritten", "serif", "sans", "typewriter", "decorative")},
                "pools": self.assets.stats(),
                "sounds": len(self.sounds.sounds),
            },
            "renders": self.store.render_stats(),
            "recent_renders": [
                {
                    "output_path": row["output_path"],
                    "status": row["status"],
                    "duration": row["duration"],
                    "render_time": row["render_time"],
                    "content_id": row["content_id"],
                }
                for row in self.store.recent_renders(10)
            ],
            "compositions": self.store.recent_compositions(5),
        }


# --- CLI -----------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m video_engine.engine", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    generate = sub.add_parser("generate", help="produce one or more videos")
    generate.add_argument("--count", type=int, default=1, help="how many videos to produce")
    generate.add_argument("--preview", action="store_true", help="render without publishing")
    generate.add_argument(
        "--publish",
        choices=["none", "queue", "upload"],
        default="none",
        help="hand the finished video to the autoposter",
    )
    generate.add_argument("--profile", choices=["standard", "light"], default=None)
    generate.add_argument("--seed", type=int, default=None, help="make the run reproducible")
    generate.add_argument("--content-dir", default=None)
    generate.add_argument("--assets-dir", default=None)
    generate.add_argument("--output-dir", default=None)
    generate.add_argument("--json", action="store_true", help="print the result as JSON")
    generate.add_argument(
        "--every",
        type=float,
        default=None,
        help="keep running: produce a video every N seconds (unattended mode)",
    )
    generate.add_argument(
        "--max-runs",
        type=int,
        default=None,
        help="stop the unattended loop after this many runs",
    )

    status = sub.add_parser("status", help="show what the engine has been doing")
    status.add_argument("--json", action="store_true")

    sub.add_parser("cleanup", help="remove temporary files and stale videos")

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)

    config = EngineSettings()
    if getattr(args, "profile", None):
        config.profile = args.profile
    if getattr(args, "content_dir", None):
        config.content_dir = Path(args.content_dir).resolve()
    if getattr(args, "assets_dir", None):
        config.assets_dir = Path(args.assets_dir).resolve()
    if getattr(args, "output_dir", None):
        config.output_dir = Path(args.output_dir).resolve()

    engine = VideoEngine(config)

    if args.command == "cleanup":
        data = engine.maintenance()
        print(
            f"removed {data['files_removed']} file(s), "
            f"{data['directories_removed']} directory(ies), "
            f"{data['bytes_removed'] / 1024:.0f} KB"
        )
        return 0

    if args.command == "status":
        data = engine.status()
        if args.json:
            print(json.dumps(data, indent=2, default=str))
        else:
            print(f"profile      : {data['engine']['profile']['name']} ({data['engine']['profile']['width']}x{data['engine']['profile']['height']} @ {data['engine']['profile']['fps']}fps)")
            print(f"content      : {data['content']['items']} item(s) in {data['content']['files']} file(s) — {data['content']['used']} used, {data['content']['remaining']} left")
            print(f"assets       : {data['assets']['fonts']} font(s), {data['assets']['sounds']} sound(s)")
            print(f"pools        : {data['assets']['pools']}")
            print(f"renders      : {data['renders']}")
        return 0

    publish_mode = MODE_NONE
    if args.publish != "none":
        publish_mode = args.publish
    if args.preview:
        publish_mode = MODE_NONE

    if args.every:
        results = engine.watch(
            every=args.every,
            publish_mode=publish_mode,
            preview=args.preview,
            seed=args.seed,
            max_runs=args.max_runs,
        )
    else:
        results = engine.generate(
            args.count,
            preview=args.preview,
            publish_mode=publish_mode,
            seed=args.seed,
        )

    if args.json:
        print(json.dumps([r.as_dict() for r in results], indent=2, default=str))
    else:
        for result in results:
            if result.success:
                print(f"OK   {result.path}  ({result.render_time:.1f}s, {result.peak_memory_mb:.0f} MB peak)")
                print(f"     {result.composition.get('identity')} / {result.composition.get('layout')} / {result.composition.get('palette', {}).get('name')}")
                if result.publish_detail:
                    print(f"     {result.publish_detail}")
            else:
                print(f"FAIL {result.error}")

    return 0 if all(r.success for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
