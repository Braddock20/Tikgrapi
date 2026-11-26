"""Settings for the video engine.

Everything is path- and environment-driven so the engine can run standalone
(``python -m video_engine.engine generate``) inside the repository without
touching the web service.

Paths are resolved against the project root, so the behaviour does not
depend on the working directory the command is started from.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from video_engine.rendering.profiles import PROFILES, RenderProfile, get_profile

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _abs(value: str | Path) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (PROJECT_ROOT / p)


@dataclass
class EngineSettings:
    content_dir: Path = field(default_factory=lambda: _abs(os.getenv("VE_CONTENT_DIR", "./content")))
    assets_dir: Path = field(default_factory=lambda: _abs(os.getenv("VE_ASSETS_DIR", "./assets")))
    output_dir: Path = field(default_factory=lambda: _abs(os.getenv("VE_OUTPUT_DIR", "./data/video_engine/out")))
    preview_dir: Path = field(default_factory=lambda: _abs(os.getenv("VE_PREVIEW_DIR", "./data/video_engine/preview")))
    queue_dir: Path = field(default_factory=lambda: _abs(os.getenv("VE_QUEUE_DIR", "./data/queue")))
    work_dir: Path = field(default_factory=lambda: _abs(os.getenv("VE_WORK_DIR", "./data/video_engine/work")))
    db_path: Path = field(default_factory=lambda: _abs(os.getenv("VE_DB_PATH", "./data/video_engine/history.db")))

    profile: str = os.getenv("VE_PROFILE", "standard")
    default_duration: float = 15.0
    seed: int = int(os.getenv("VE_SEED", "0"))
    # How many recent compositions are kept out of the rotation.
    memory_window: int = 24
    # Allow a content item to be used again once its whole library is spent.
    allow_reuse: bool = os.getenv("VE_ALLOW_REUSE", "").strip().lower() in {"1", "true", "yes"}
    max_render_attempts: int = int(os.getenv("VE_MAX_ATTEMPTS", "3"))
    # Automatic publishing is off by default: the engine renders and queues.
    publish: bool = os.getenv("VE_PUBLISH", "").strip().lower() in {"1", "true", "yes"}
    captions_file: Path | None = None

    def __post_init__(self) -> None:
        self.content_dir = _abs(self.content_dir)
        self.assets_dir = _abs(self.assets_dir)
        self.output_dir = _abs(self.output_dir)
        self.preview_dir = _abs(self.preview_dir)
        self.queue_dir = _abs(self.queue_dir)
        self.work_dir = _abs(self.work_dir)
        self.db_path = _abs(self.db_path)
        if os.getenv("VE_CAPTIONS_FILE"):
            self.captions_file = _abs(os.environ["VE_CAPTIONS_FILE"])

    @property
    def render_profile(self) -> RenderProfile:
        return PROFILES.get(self.profile, PROFILES["standard"])

    def ensure_dirs(self) -> None:
        for path in (self.output_dir, self.preview_dir, self.queue_dir, self.work_dir, self.db_path.parent):
            path.mkdir(parents=True, exist_ok=True)


settings = EngineSettings()
