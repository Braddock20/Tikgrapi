"""The autoposter: queue selection, captions, state, and the write gate."""
from __future__ import annotations

import json

import pytest

from app.bot.autoposter import AutoPoster


class FakeEngine:
    def __init__(self) -> None:
        self.uploads: list[dict] = []
        self.fail = False

    async def call(self, operation: str, params: dict) -> dict:
        if operation != "upload":
            return {}
        if self.fail:
            raise RuntimeError("upload exploded")
        self.uploads.append(params)
        return {"aweme_id": f"id-{len(self.uploads)}", "status_code": 0}


def make_video(tmp_path, name: str, size: int = 10) -> None:
    path = tmp_path / name
    path.write_bytes(b"0" * size)


def test_pending_returns_videos_in_mtime_order(tmp_path):
    queue = tmp_path / "queue"
    queue.mkdir()
    make_video(queue, "b.mp4")
    make_video(queue, "a.mp4")
    poster = AutoPoster(FakeEngine(), queue)
    assert [p.name for p in poster.pending()] == ["a.mp4", "b.mp4"]


def test_non_video_files_are_ignored(tmp_path):
    queue = tmp_path / "queue"
    queue.mkdir()
    make_video(queue, "clip.mp4")
    (queue / "notes.txt").write_text("hello")
    (queue / ".hidden.mp4").write_bytes(b"x")
    poster = AutoPoster(FakeEngine(), queue)
    assert [p.name for p in poster.pending()] == ["clip.mp4"]


def test_a_posted_file_is_not_posted_twice(tmp_path):
    queue = tmp_path / "queue"
    queue.mkdir()
    make_video(queue, "clip.mp4")
    engine = FakeEngine()
    poster = AutoPoster(engine, queue)

    first = poster.post_next.__wrapped__ if False else None
    import asyncio

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(poster.post_next())
    assert len(engine.uploads) == 1
    assert poster.pending() == []


def test_state_is_persisted_between_runs(tmp_path):
    queue = tmp_path / "queue"
    queue.mkdir()
    make_video(queue, "clip.mp4")
    engine = FakeEngine()

    import asyncio

    loop = asyncio.new_event_loop()
    poster = AutoPoster(engine, queue)
    loop.run_until_complete(poster.post_next())

    fresh = AutoPoster(engine, queue)
    fresh.load_state()
    assert fresh.pending() == []
    assert "clip.mp4" in fresh.posted


def test_sidecar_caption_wins(tmp_path):
    queue = tmp_path / "queue"
    queue.mkdir()
    make_video(queue, "clip.mp4")
    (queue / "clip.txt").write_text("sidecar caption")
    poster = AutoPoster(FakeEngine(), queue)
    assert poster.caption_for(queue / "clip.mp4") == "sidecar caption"


def test_caption_list_cycles(tmp_path):
    queue = tmp_path / "queue"
    queue.mkdir()
    captions = tmp_path / "captions.txt"
    captions.write_text("# a comment\nfirst\nsecond\n")
    poster = AutoPoster(FakeEngine(), queue, captions=captions)
    video = queue / "clip.mp4"
    assert poster.caption_for(video) == "first"
    assert poster.caption_for(video) == "second"
    assert poster.caption_for(video) == "first"


def test_default_caption_is_the_fallback(tmp_path):
    queue = tmp_path / "queue"
    queue.mkdir()
    poster = AutoPoster(FakeEngine(), queue, default_caption="fallback")
    assert poster.caption_for(queue / "clip.mp4") == "fallback"


def test_tags_are_appended(tmp_path):
    queue = tmp_path / "queue"
    queue.mkdir()
    poster = AutoPoster(FakeEngine(), queue, default_caption="hi", tags=["one", "#two"])
    assert poster.build_caption("hi") == "hi #one #two"


def test_a_failed_upload_does_not_block_the_queue(tmp_path):
    queue = tmp_path / "queue"
    queue.mkdir()
    make_video(queue, "clip.mp4")
    engine = FakeEngine()
    engine.fail = True

    import asyncio

    poster = AutoPoster(engine, queue)
    result = asyncio.new_event_loop().run_until_complete(poster.post_next())
    assert "error" in result
    assert poster.pending() == []


def test_delete_after_removes_the_file(tmp_path):
    queue = tmp_path / "queue"
    queue.mkdir()
    make_video(queue, "clip.mp4")
    engine = FakeEngine()

    import asyncio

    poster = AutoPoster(engine, queue, delete_after=True)
    asyncio.new_event_loop().run_until_complete(poster.post_next())
    assert not (queue / "clip.mp4").exists()


def test_run_once_posts_up_to_the_limit(tmp_path):
    queue = tmp_path / "queue"
    queue.mkdir()
    make_video(queue, "a.mp4")
    make_video(queue, "b.mp4")
    engine = FakeEngine()

    import asyncio

    poster = AutoPoster(engine, queue, interval=0)
    results = asyncio.new_event_loop().run_until_complete(poster.run_once(1))
    assert len(results) == 1
    assert len(engine.uploads) == 1
