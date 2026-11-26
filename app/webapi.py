"""The control room — a browser front end for the whole engine.

The API in :mod:`app.main` is the machine interface; this is the human one.
It exposes the video engine, the content and asset libraries, the render
history and the posting queue over a small JSON API, and serves a single
self-contained web UI.

Nothing here changes how the engine works: the same functions the CLI
calls are the ones the browser calls.
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings

WEB_DIR = Path(__file__).resolve().parent / "web"
ENGINE_ROOT = Path(__file__).resolve().parent.parent

# Only these engine verbs may be run from the browser.
ALLOWED_COMMANDS = ("generate", "status", "cleanup")

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
AUDIO_SUFFIXES = {".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac"}
FONT_SUFFIXES = {".ttf", ".otf"}
TEXT_SUFFIXES = {".json", ".txt", ".md"}


# --------------------------------------------------------------------------
# UI jobs
# --------------------------------------------------------------------------
@dataclass
class UiJob:
    """A running command, with its output kept for the chat panel."""

    id: str
    command: str
    status: str = "running"
    lines: list[str] = field(default_factory=list)
    started: float = field(default_factory=time.time)
    finished: float | None = None
    exit_code: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "command": self.command,
            "status": self.status,
            "lines": self.lines,
            "started": self.started,
            "finished": self.finished,
            "exit_code": self.exit_code,
        }


class JobRegistry:
    """In-memory registry of UI jobs."""

    def __init__(self, keep: int = 40) -> None:
        self._jobs: dict[str, UiJob] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()
        self._keep = keep

    def create(self, command: str) -> UiJob:
        job = UiJob(id=uuid.uuid4().hex[:12], command=command)
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            while len(self._order) > self._keep:
                old = self._order.pop(0)
                self._jobs.pop(old, None)
        return job

    def get(self, job_id: str) -> UiJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            ids = self._order[-limit:]
            return [self._jobs[i].as_dict() for i in reversed(ids) if i in self._jobs]


JOBS = JobRegistry()


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------
def _engine_paths() -> dict[str, Path]:
    """Where the engine keeps its things, resolved from its own settings."""
    sys.path.insert(0, str(ENGINE_ROOT))
    from video_engine.config import settings as engine_settings

    engine_settings.ensure_dirs()
    return {
        "content": engine_settings.content_dir,
        "assets": engine_settings.assets_dir,
        "output": engine_settings.output_dir,
        "preview": engine_settings.preview_dir,
        "queue": engine_settings.queue_dir,
        "db": engine_settings.db_path,
    }


def _engine_config():
    """The video engine's settings.

    The video engine is an optional extra: the chat bot, the session and the
    automations all work without it, so a missing engine is reported as a
    clean "not installed" instead of a stack trace.
    """
    sys.path.insert(0, str(ENGINE_ROOT))
    try:
        from video_engine.config import settings as engine_settings
    except ImportError as exc:  # pragma: no cover
        raise HTTPException(
            503,
            "the video engine is not installed in this deployment — the chat "
            "bot, the session and the automations still work",
        ) from exc
    return engine_settings


_ENGINE = None
_ENGINE_LOCK = threading.Lock()


def _engine():
    """One engine for the whole process.

    Rebuilding it per request would re-probe every asset on every call, so
    it is kept and reused.
    """
    global _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is None:
            from video_engine.engine import VideoEngine

            _ENGINE = VideoEngine(_engine_config())
        return _ENGINE


# --------------------------------------------------------------------------
# the JSON API
# --------------------------------------------------------------------------
router = APIRouter(prefix="/ui/api", tags=["ui"])


async def ui_auth(
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
    key: str | None = Query(default=None),
) -> None:
    """The UI uses the same API key as the rest of the service.

    Media URLs (a thumbnail, a download) cannot carry a header, so the key
    may also arrive as a query parameter.
    """
    import secrets

    if not settings.api_key or settings.api_key == "change-me":
        raise HTTPException(503, "Set a strong API_KEY before using the UI")
    token = x_api_key
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        token = key
    if not token or not secrets.compare_digest(token, settings.api_key):
        raise HTTPException(401, "Invalid API key")


@router.get("/status", dependencies=[Depends(ui_auth)])
async def ui_status() -> dict[str, Any]:
    """Everything the status panel shows.

    The video engine is optional — when it is not installed, its parts are
    simply left out and the rest of the status still comes back.
    """
    session_info = {"present": False, "status": "unknown"}
    try:
        from app.security import session_exists
        from app.sessions import current_session_meta

        meta = await current_session_meta()
        session_info = {**(meta or {}), "present": session_exists()}
    except Exception:  # noqa: BLE001
        pass

    payload: dict[str, Any] = {
        "session": session_info,
        "write_ops_enabled": settings.allow_write_ops,
        "video_engine": False,
    }

    try:
        from video_engine.content.loader import load_library

        config = _engine_config()
        engine = _engine()
        library = load_library(config.content_dir)
        usage = engine.history.usage()
        used = sum(1 for value in usage.values() if value > 0)

        payload["video_engine"] = True
        payload["engine"] = {
            "profile": engine.profile.as_dict(),
            "content_dir": str(config.content_dir),
            "assets_dir": str(config.assets_dir),
            "output_dir": str(config.output_dir),
            "queue_dir": str(config.queue_dir),
        }
        payload["content"] = {
            "files": library.report.files,
            "items": len(library),
            "used": used,
            "remaining": max(0, len(library) - used),
            "problems": library.report.errors[:10],
        }
        payload["assets"] = {
            "fonts": len(engine.fonts.all()),
            "sounds": len(engine.sounds.sounds),
            "pools": engine.assets.stats(),
        }
        payload["renders"] = engine.store.render_stats()
        payload["recent_renders"] = [
            {
                "render_id": row["render_id"],
                "output_path": row["output_path"],
                "duration": row["duration"],
                "render_time": row["render_time"],
                "status": row["status"],
                "content_id": row["content_id"],
                "created_at": row["created_at"],
            }
            for row in engine.store.recent_renders(12)
        ]
        payload["compositions"] = engine.store.recent_compositions(12)
        payload["queue"] = (
            [p.name for p in sorted(config.queue_dir.glob("*.mp4"))]
            if config.queue_dir.exists()
            else []
        )
    except HTTPException:
        payload["queue"] = []
    except Exception as exc:  # noqa: BLE001
        payload["video_engine_error"] = f"{type(exc).__name__}: {exc}"

    return payload


@router.get("/content", dependencies=[Depends(ui_auth)])
async def ui_content(limit: int = 300) -> dict[str, Any]:
    """The content library, with usage flags."""
    try:
        from video_engine.content.loader import load_library
    except ImportError as exc:  # pragma: no cover
        raise HTTPException(503, "the video engine is not installed") from exc

    config = _engine_config()
    library = load_library(config.content_dir)
    from video_engine.storage.database import HistoryStore

    store = HistoryStore(usage.db_path)
    used = store.content_used()

    return {
        "items": [
            {
                "id": item.id,
                "text": item.text,
                "category": item.category,
                "mood": item.mood,
                "format": item.format,
                "tags": item.tags,
                "source": Path(item.source).name,
                "used": used.get(item.id, 0),
            }
            for item in list(library)[:limit]
        ],
        "total": len(library),
        "report": {
            "files": library.report.files,
            "accepted": library.report.accepted,
            "rejected": library.report.rejected,
            "duplicates": library.report.duplicates,
            "problems": library.report.errors[:20],
        },
    }


@router.post("/upload", dependencies=[Depends(ui_auth)])
async def ui_upload(
    file: UploadFile = File(...),
    target: str = "auto",
) -> dict[str, Any]:
    """Add a content file, a photograph, a sound, a font or a video."""
    config = _engine_config()
    name = Path(file.filename or "upload.bin").name
    suffix = Path(name).suffix.lower()

    if suffix in TEXT_SUFFIXES:
        folder = config.content_dir
    elif suffix in FONT_SUFFIXES:
        folder = config.assets_dir / "fonts" / _font_category(name)
    elif suffix in AUDIO_SUFFIXES:
        folder = config.assets_dir / "sounds" / (target if target != "auto" else "unsorted")
    elif suffix in VIDEO_SUFFIXES:
        folder = config.assets_dir / "backgrounds" / "videos"
    elif suffix in IMAGE_SUFFIXES:
        if target in {"stickers", "tape", "paper", "stamps", "doodles", "frames", "textures", "overlays"}:
            folder = config.assets_dir / "decorations" / target
        elif target in {"backgrounds", "photographs"}:
            folder = config.assets_dir / ("backgrounds" / "images" if target == "backgrounds" else "photographs")
        else:
            folder = config.assets_dir / "photographs"
    else:
        raise HTTPException(400, f"unsupported file type: {suffix or name}")

    folder.mkdir(parents=True, exist_ok=True)
    target_path = folder / name
    counter = 1
    while target_path.exists():
        target_path = folder / f"{Path(name).stem}-{counter}{suffix}"
        counter += 1

    payload = await file.read()
    if len(payload) > 80 * 1024 * 1024:
        raise HTTPException(413, "the file is larger than 80 MB")
    target_path.write_bytes(payload)
    return {"stored": str(target_path), "name": target_path.name, "folder": str(folder), "bytes": len(payload)}


def _font_category(name: str) -> str:
    stem = name.lower()
    table = {
        "handwritten": ("hand", "caveat", "kalam", "flower", "shadow", "script", "vibes"),
        "serif": ("serif", "baskerville", "playfair", "cormorant", "garamond", "times"),
        "typewriter": ("mono", "courier", "type", "elite"),
        "decorative": ("lobster", "pacifico", "satisfy", "display"),
        "sans": ("inter", "montserrat", "jost", "grotesk", "oswald", "bebas"),
    }
    for category, words in table.items():
        if any(word in stem for word in words):
            return category
    return "sans"


@router.get("/files", dependencies=[Depends(ui_auth)])
async def ui_files() -> dict[str, Any]:
    """The rendered videos and the autoposter queue."""
    config = _engine_config()
    out: list[dict[str, Any]] = []
    for folder, kind in ((config.output_dir, "output"), (config.preview_dir, "preview"), (config.queue_dir, "queue")):
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                stat = path.stat()
            except OSError:
                continue
            out.append(
                {
                    "name": path.name,
                    "kind": kind,
                    "size": stat.st_size,
                    "created_at": stat.st_mtime,
                    "url": f"/ui/api/download/{kind}/{path.name}",
                    "caption_path": str(path.with_suffix(".txt")),
                }
            )
    return {"files": out}


@router.get("/download/{kind}/{name}", dependencies=[Depends(ui_auth)])
async def ui_download(kind: str, name: str) -> FileResponse:
    """Serve a finished video back to the browser."""
    config = _engine_config()
    folders = {
        "output": config.output_dir,
        "preview": config.preview_dir,
        "queue": config.queue_dir,
    }
    folder = folders.get(kind)
    if folder is None:
        raise HTTPException(404, "unknown folder")
    path = (folder / Path(name).name).resolve()
    if not str(path).startswith(str(folder.resolve())) or not path.exists():
        raise HTTPException(404, "not found")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@router.get("/poster/{kind}/{name}", dependencies=[Depends(ui_auth)])
async def ui_poster(kind: str, name: str) -> FileResponse:
    """A still from a finished video, for the thumbnail grid.

    A still loads instantly and works in every browser — including the ones
    that cannot decode H.264 — so the grid never shows an empty box.
    """
    config = _engine_config()
    folders = {
        "output": config.output_dir,
        "preview": config.preview_dir,
        "queue": config.queue_dir,
    }
    folder = folders.get(kind)
    if folder is None:
        raise HTTPException(404, "unknown folder")
    path = (folder / Path(name).name).resolve()
    if not str(path).startswith(str(folder.resolve())) or not path.exists():
        raise HTTPException(404, "not found")

    cache = config.db_path.parent / "posters"
    cache.mkdir(parents=True, exist_ok=True)
    poster = cache / f"{path.stem}.jpg"
    if not poster.exists():
        import subprocess

        binary = shutil.which("ffmpeg")
        if not binary:
            raise HTTPException(503, "ffmpeg is not available")
        result = subprocess.run(
            [
                binary, "-y", "-nostdin", "-loglevel", "error",
                "-ss", "0.8", "-i", str(path), "-frames:v", "1",
                "-vf", "scale=360:-2", "-q:v", "5", str(poster),
            ],
            capture_output=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0 or not poster.exists():
            raise HTTPException(500, "could not build the thumbnail")
    return FileResponse(poster, media_type="image/jpeg")


@router.post("/generate", dependencies=[Depends(ui_auth)])
async def ui_generate(payload: dict[str, Any]) -> dict[str, Any]:
    """Render videos right now (the same call the CLI makes)."""
    count = max(1, min(20, int(payload.get("count", 1))))
    profile = payload.get("profile") or "standard"
    publish = payload.get("publish") or "none"
    seed = payload.get("seed")
    preview = bool(payload.get("preview", False))

    command = ["generate", "--count", str(count), "--profile", str(profile), "--publish", str(publish)]
    if seed not in (None, ""):
        command += ["--seed", str(seed)]
    if preview:
        command.append("--preview")

    return await _run_engine_command(command, label=" ".join(command))


@router.post("/command", dependencies=[Depends(ui_auth)])
async def ui_command(payload: dict[str, Any]) -> dict[str, Any]:
    """Run an engine command in the background and stream its output."""
    raw = str(payload.get("command") or "").strip()
    if not raw:
        raise HTTPException(400, "no command given")
    try:
        parts = shlex.split(raw)
    except ValueError as exc:
        raise HTTPException(400, f"could not parse the command: {exc}") from exc
    if not parts:
        raise HTTPException(400, "no command given")
    verb = parts[0]
    if verb not in ALLOWED_COMMANDS:
        raise HTTPException(
            400,
            f"only {', '.join(ALLOWED_COMMANDS)} may be run from the browser",
        )
    return await _run_engine_command(parts, label=raw)


async def _run_engine_command(parts: list[str], label: str) -> dict[str, Any]:
    job = JOBS.create(label)
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    cwd = str(ENGINE_ROOT)

    def _run() -> None:
        command = [sys.executable, "-m", "video_engine.engine", *parts]
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except Exception as exc:  # noqa: BLE001
            job.lines.append(f"could not start the command: {exc}")
            job.status = "failed"
            job.exit_code = 1
            job.finished = time.time()
            return

        assert process.stdout is not None
        for line in process.stdout:
            job.lines.append(line.rstrip("\n"))
        process.wait()
        job.exit_code = process.returncode
        job.status = "completed" if process.returncode == 0 else "failed"
        job.finished = time.time()

    threading.Thread(target=_run, daemon=True).start()
    return job.as_dict()


@router.get("/jobs", dependencies=[Depends(ui_auth)])
async def ui_jobs() -> dict[str, Any]:
    return {"jobs": JOBS.recent()}


@router.get("/jobs/{job_id}", dependencies=[Depends(ui_auth)])
async def ui_job(job_id: str) -> dict[str, Any]:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    return job.as_dict()


@router.post("/publish", dependencies=[Depends(ui_auth)])
async def ui_publish(payload: dict[str, Any]) -> dict[str, Any]:
    """Hand a finished video to the autoposter queue."""
    name = Path(str(payload.get("name") or "")).name
    config = _engine_config()
    source = config.output_dir / name
    if not source.exists():
        raise HTTPException(404, "no such video")
    caption = str(payload.get("caption") or "")

    from video_engine.integration.autoposter import AutoPosterBridge

    bridge = AutoPosterBridge(config.queue_dir)
    result = bridge.queue_video(source, caption)
    return result.as_dict()


@router.delete("/content", dependencies=[Depends(ui_auth)])
async def ui_delete_content(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove a content item from the library."""
    item_id = str(payload.get("id") or "")
    if not item_id:
        raise HTTPException(400, "no id given")
    config = _engine_config()
    from video_engine.content.loader import load_library

    library = load_library(config.content_dir)
    item = next((i for i in library if i.id == item_id), None)
    if item is None:
        raise HTTPException(404, "no such content item")

    path = Path(item.source)
    if path.exists():
        try:
            payload_json = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload_json = None
        if isinstance(payload_json, list):
            kept = [row for row in payload_json if not (isinstance(row, dict) and row.get("id") == item_id)]
            path.write_text(json.dumps(kept, indent=2, ensure_ascii=False), encoding="utf-8")
            return {"deleted": item_id, "file": path.name}

    # A plain-text file: drop the line.
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    kept = [line for line in lines if item_id not in line]
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return {"deleted": item_id, "file": path.name}


# --------------------------------------------------------------------------
# the UI itself
# --------------------------------------------------------------------------
def mount_ui(app) -> None:
    """Attach the control room to the FastAPI app."""
    if WEB_DIR.is_dir():
        app.mount("/ui/static", StaticFiles(directory=str(WEB_DIR)), name="ui-static")

    @app.get("/", include_in_schema=False)
    async def _root() -> RedirectResponse:
        return RedirectResponse("/ui/")

    @app.get("/ui", include_in_schema=False)
    @app.get("/ui/", include_in_schema=False)
    async def _ui() -> FileResponse:
        index = WEB_DIR / "index.html"
        if not index.exists():
            raise HTTPException(404, "the UI is not installed")
        return FileResponse(index, media_type="text/html")
