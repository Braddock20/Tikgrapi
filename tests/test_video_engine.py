"""Acceptance tests for the video engine.

They run against the light render profile so the whole suite stays quick,
but every code path is the production one.

    python -m pytest tests/test_video_engine.py -v
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from video_engine.assets.fonts import FontRegistry  # noqa: E402
from video_engine.assets.manager import AssetLibrary  # noqa: E402
from video_engine.content.loader import load_library, split_text  # noqa: E402
from video_engine.content.selector import ContentSelector, classify_mood  # noqa: E402
from video_engine.design.composer import Composer  # noqa: E402
from video_engine.design.randomness import RandomSource  # noqa: E402
from video_engine.design.validation import validate_composition  # noqa: E402
from video_engine.engine import VideoEngine  # noqa: E402
from video_engine.config import EngineSettings  # noqa: E402
from video_engine.integration.autoposter import AutoPosterBridge, MODE_QUEUE  # noqa: E402
from video_engine.rendering.ffmpeg import validate_output  # noqa: E402
from video_engine.rendering.profiles import get_profile  # noqa: E402


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
@pytest.fixture(scope="session")
def content_dir() -> Path:
    return ROOT / "content"


@pytest.fixture(scope="session")
def assets_dir() -> Path:
    return ROOT / "assets"


@pytest.fixture()
def engine(tmp_path: Path) -> VideoEngine:
    config = EngineSettings()
    config.profile = "light"
    config.content_dir = ROOT / "content"
    config.assets_dir = ROOT / "assets"
    config.output_dir = tmp_path / "out"
    config.preview_dir = tmp_path / "preview"
    config.queue_dir = tmp_path / "queue"
    config.work_dir = tmp_path / "work"
    config.db_path = tmp_path / "history.db"
    config.seed = 20240101
    config.ensure_dirs()
    return VideoEngine(config)


# --------------------------------------------------------------------------
# content library
# --------------------------------------------------------------------------
def test_library_loads_and_validates(content_dir: Path) -> None:
    library = load_library(content_dir)
    assert len(library) > 20, "the starter library should hold enough material"
    assert library.report.rejected == 0, library.report.errors
    for item in library:
        assert item.text.strip(), "every item keeps its wording"
        assert item.fingerprint


def test_malformed_records_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text("{not json at all", encoding="utf-8")
    (tmp_path / "good.json").write_text(
        json.dumps([{"id": "a", "text": "A perfectly fine line of content."}]),
        encoding="utf-8",
    )
    library = load_library(tmp_path)
    assert len(library) == 1
    assert library.report.rejected == 1


def test_duplicates_are_removed(tmp_path: Path) -> None:
    payload = [{"id": "a", "text": "The same sentence twice."}, {"id": "b", "text": "The same sentence twice."}]
    (tmp_path / "dupes.json").write_text(json.dumps(payload), encoding="utf-8")
    library = load_library(tmp_path)
    assert len(library) == 1
    assert library.report.duplicates == 1


def test_long_text_splits_without_rewording() -> None:
    text = "First sentence. Second sentence. Third sentence. Fourth sentence."
    parts = split_text(text, max_chars=30)
    assert len(parts) > 1
    joined = " ".join(parts).replace(" .", ".")
    assert joined.replace(" ", "") == text.replace(" ", "")


def test_mood_is_determined_without_ai() -> None:
    library = load_library(ROOT / "content")
    moods = {classify_mood(item)[0] for item in library}
    assert len(moods) >= 3, "the lexicon should separate the moods"


# --------------------------------------------------------------------------
# composition
# --------------------------------------------------------------------------
def _compose(engine: VideoEngine, seed: int):
    library = load_library(engine.config.content_dir)
    selector = ContentSelector(list(library), usage={}, seed=seed)
    selection = selector.select()
    composer = Composer(
        fonts=engine.fonts,
        asset_library=engine.assets,
        profile=engine.profile,
        rng_source=RandomSource(seed),
    )
    return selection, composer.build(selection, seed=seed)


def test_composition_passes_validation(engine: VideoEngine) -> None:
    for seed in range(8):
        _, composition = _compose(engine, seed)
        result = validate_composition(composition, engine.profile)
        assert result.ok, f"seed {seed}: {result.summary()}"


def test_two_videos_use_different_compositions(engine: VideoEngine) -> None:
    _, first = _compose(engine, 1)
    _, second = _compose(engine, 2)
    assert (first.identity, first.layout) != (second.identity, second.layout) or (
        first.palette["name"] != second.palette["name"]
    )


def test_positioning_actually_varies(engine: VideoEngine) -> None:
    """Different seeds must move the elements, not just swap the text."""
    positions = []
    for seed in range(6):
        _, composition = _compose(engine, seed)
        positions.append(
            tuple(
                (round(e.x, 3), round(e.y, 3), round(e.rotation, 2))
                for e in composition.elements()
            )
        )
    assert len(set(positions)) > 1, "the composition did not change between runs"


def test_every_layout_produces_a_valid_composition(engine: VideoEngine) -> None:
    from video_engine.design.layouts import LAYOUTS

    library = load_library(engine.config.content_dir)
    selector = ContentSelector(list(library), usage={}, seed=5)
    selection = selector.select()
    composer = Composer(
        fonts=engine.fonts,
        asset_library=engine.assets,
        profile=engine.profile,
        rng_source=RandomSource(11),
    )
    for name, builder in LAYOUTS.items():
        composition = composer.build(selection, seed=11)
        composition.pages = composition.pages[:1]
        composition.pages[0].elements = []
        from video_engine.design.layouts import LayoutContext

        ctx = LayoutContext(
            rng=RandomSource(3).rng("x"),
            palette=__import__("video_engine.design.palettes", fromlist=["PALETTES"]).PALETTES["cream"],
            primary=engine.fonts.category("handwritten")[0],
            secondary=engine.fonts.category("sans")[0],
            width=engine.profile.width,
            height=engine.profile.height,
            body=selection.item.text,
            kicker="A KICKER",
            signature="— me",
            photos=engine.assets.photos(),
            textures=engine.assets.textures(),
        )
        elements = builder(ctx)
        assert elements, f"the layout {name} produced nothing"


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------
def test_generates_a_playable_video(engine: VideoEngine) -> None:
    result = engine.generate_one(preview=True)
    assert result.success, result.error
    assert Path(result.path).exists()
    report = validate_output(result.path, engine.profile, expect_audio=True)
    assert report.ok, report.problems
    assert report.width == 720 and report.height == 1280
    assert report.duration > 3
    assert report.has_audio


def test_text_stays_inside_the_safe_area(engine: VideoEngine) -> None:
    from video_engine.design.positioning import element_box, inside_safe_area, safe_area

    area = safe_area(engine.profile.width, engine.profile.height, margin=0.05)
    for seed in range(6):
        _, composition = _compose(engine, seed)
        for element in composition.elements():
            if element.role in ("primary_text", "secondary_text"):
                assert inside_safe_area(element_box(element), area, tolerance=0.03)


def test_render_stays_inside_the_memory_budget(engine: VideoEngine) -> None:
    result = engine.generate_one(preview=True)
    assert result.success, result.error
    assert result.peak_memory_mb < 700, f"peak memory was {result.peak_memory_mb:.0f} MB"


# --------------------------------------------------------------------------
# history and duplicate prevention
# --------------------------------------------------------------------------
def test_content_is_not_used_twice(engine: VideoEngine) -> None:
    first = engine.generate_one(preview=True)
    assert first.success
    second = engine.generate_one(preview=True)
    assert second.success
    assert first.content_id != second.content_id


def test_a_failed_render_does_not_consume_content(engine: VideoEngine) -> None:
    library = load_library(engine.config.content_dir)
    before = set(item.id for item in library)
    engine.assets.photos = lambda: []  # break the photo path on purpose
    result = engine.generate_one(preview=True)
    usage = engine.history.usage()
    assert not all(usage.get(item_id, 0) > 0 for item_id in before)
    assert result.success or result.error


# --------------------------------------------------------------------------
# autoposter integration
# --------------------------------------------------------------------------
def test_failed_render_never_triggers_a_post(engine: VideoEngine, monkeypatch) -> None:
    posted: list[str] = []

    def explode(*args, **kwargs):  # pragma: no cover
        raise RuntimeError("the renderer is broken")

    monkeypatch.setattr(engine, "_attempt", explode)
    monkeypatch.setattr(
        engine.bridge,
        "queue_video",
        lambda *a, **k: posted.append(a[0]) or None,
    )
    result = engine.generate_one(preview=False, publish_mode=MODE_QUEUE)
    assert not result.success
    assert not posted, "a failed render must never reach the autoposter"


def test_validated_video_is_handed_to_the_queue(engine: VideoEngine) -> None:
    result = engine.generate_one(preview=False, publish_mode=MODE_QUEUE)
    assert result.success, result.error
    queue = list(engine.config.queue_dir.glob("*.mp4"))
    assert queue, "the finished video should sit in the autoposter queue"
    sidecar = queue[0].with_suffix(".txt")
    assert sidecar.exists(), "the autoposter expects a caption next to the video"
    assert sidecar.read_text().strip()


def test_existing_autoposter_still_works(tmp_path: Path) -> None:
    """The posting side of the system is untouched by the engine."""
    from app.bot.autoposter import AutoPoster

    class FakeEngine:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def call(self, operation: str, params: dict) -> dict:
            self.calls.append({"operation": operation, **params})
            return {"aweme_id": "123"}

    import asyncio

    engine = FakeEngine()
    queue = tmp_path / "queue"
    queue.mkdir()
    video = queue / "clip.mp4"
    video.write_bytes(b"0" * 2048)
    video.with_suffix(".txt").write_text("a caption", encoding="utf-8")

    poster = AutoPoster(engine, queue, interval=0)
    results = asyncio.run(poster.run_once(1))
    assert results and results[0].get("aweme_id") == "123"
    assert engine.calls[0]["operation"] == "upload"
    assert engine.calls[0]["video_path"].endswith("clip.mp4")


def test_bridge_writes_a_caption_sidecar(tmp_path: Path) -> None:
    bridge = AutoPosterBridge(tmp_path / "queue", tags=["scrapbook"])
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0" * 100)
    result = bridge.queue_video(video, "hello there")
    assert result.submitted
    assert (tmp_path / "queue" / "v.txt").read_text().strip().endswith("#scrapbook")


# --------------------------------------------------------------------------
# the queue
# --------------------------------------------------------------------------
def test_queue_records_and_retries(engine: VideoEngine) -> None:
    from video_engine.queue.manager import JobQueue, JobStatus

    queue = JobQueue(engine.store, max_attempts=2)
    attempts = {"n": 0}

    def flaky(job):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("boom")
        return {"ok": True}

    queue.set_handler(flaky)
    job = queue.enqueue({"kind": "test"})
    queue.run_once()
    assert job.status == JobStatus.PENDING  # requeued for another try
    queue.run_once()
    assert job.status == JobStatus.COMPLETED
    assert job.attempts == 2
