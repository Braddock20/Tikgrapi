"""Video upload backend.

The upload pipeline is the one part of TikTok's web app that has its own
protocol: a chunked binary upload to TikTok's storage, then a publish call
to the creator endpoints. The vendored ``tiktokrapi`` library implements it
and it is verified working against the live site (a video posted in ~6 s).

Unlike the browser backend this one signs with the library's own signer, so
it is a standalone HTTP client — no Chromium needed to post.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from app.config import settings
from app.security import parse_session_payload, read_session, store_session

VIDEO_SUFFIXES = (".mp4", ".mov", ".m4v", ".webm", ".mkv")


def _ensure_vendored_client_on_path() -> None:
    """Make the vendored ``tiktokrapi`` importable.

    The package lives in ``vendor/`` and is not on PyPI. It is normally
    installed (``pip install ./vendor``), but a checkout that was never
    installed should still be able to post.
    """
    import sys
    from pathlib import Path

    vendor = Path(__file__).resolve().parent.parent.parent / "vendor"
    if vendor.is_dir() and str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))


class UploadError(RuntimeError):
    """The upload pipeline failed."""


class Uploader:
    """Posts videos to the account the session belongs to."""

    name = "upload"

    def __init__(self) -> None:
        self._client = None
        self._lock = asyncio.Lock()

    # -- lifecycle -------------------------------------------------------
    def _build_client(self):
        _ensure_vendored_client_on_path()
        from tiktokrapi import Client
        cookies = parse_session_payload(read_session())
        fd, path = tempfile.mkstemp(prefix="tt-upload-", suffix=".json")
        with os.fdopen(fd, "w") as handle:
            json.dump(
                [
                    {"name": name, "value": value, "domain": ".tiktok.com", "path": "/"}
                    for name, value in cookies.items()
                ],
                handle,
            )
        try:
            return Client.from_session(path, persist=True), path
        except Exception:
            try:
                os.unlink(path)
            except OSError:
                pass
            raise

    async def start(self) -> None:
        # Nothing to warm up: the client is built lazily on the first upload
        # so the process does not hold a Node signer open for nothing.
        return None

    async def stop(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            try:
                await asyncio.to_thread(client.close)
            except Exception:  # noqa: BLE001
                pass

    def _ensure_client(self):
        if self._client is None:
            self._client, self._session_path = self._build_client()
        return self._client

    def _persist_session(self) -> None:
        """Keep the rotated msToken so the next run starts fresh."""
        path = getattr(self, "_session_path", None)
        if not path:
            return
        try:
            raw = Path(path).read_bytes()
        except OSError:
            return
        if raw:
            try:
                store_session(raw)
            except Exception:  # noqa: BLE001
                pass
        try:
            os.unlink(path)
        except OSError:
            pass

    # -- operations ------------------------------------------------------
    async def upload(
        self,
        video_path: str,
        caption: str = "",
        *,
        visibility: int = 0,
        allow_comment: bool = True,
        allow_duet: bool = True,
        allow_stitch: bool = True,
        schedule_time: int = 0,
    ) -> dict[str, Any]:
        path = Path(video_path)
        if not path.exists():
            raise UploadError(f"video not found: {path}")
        if path.suffix.lower() not in VIDEO_SUFFIXES:
            raise UploadError(f"unsupported video type: {path.suffix}")

        async with self._lock:
            client = self._ensure_client()
            try:
                result = await asyncio.to_thread(
                    client.upload,
                    str(path),
                    caption,
                    visibility=visibility,
                    allow_comment=allow_comment,
                    allow_duet=allow_duet,
                    allow_stitch=allow_stitch,
                    schedule_time=schedule_time,
                )
            except Exception as exc:  # noqa: BLE001
                raise UploadError(f"{type(exc).__name__}: {exc}") from exc
            finally:
                self._persist_session()
        return _extract(result)

    async def delete_video(self, aweme_id: str) -> dict[str, Any]:
        async with self._lock:
            client = self._ensure_client()
            try:
                result = await asyncio.to_thread(client.delete_video, aweme_id)
            except Exception as exc:  # noqa: BLE001
                raise UploadError(f"{type(exc).__name__}: {exc}") from exc
            finally:
                self._persist_session()
        return result

    async def set_visibility(self, aweme_id: str, visibility: int) -> dict[str, Any]:
        async with self._lock:
            client = self._ensure_client()
            try:
                result = await asyncio.to_thread(
                    client.visibility, aweme_id, visibility=visibility
                )
            except Exception as exc:  # noqa: BLE001
                raise UploadError(f"{type(exc).__name__}: {exc}") from exc
            finally:
                self._persist_session()
        return result


def _extract(result: Any) -> dict[str, Any]:
    """Pull the interesting fields out of the upload response."""
    if not isinstance(result, dict):
        return {"raw": result}
    out: dict[str, Any] = {
        "status_code": result.get("status_code", 0),
        "status_msg": result.get("status_msg", ""),
        "project_id": result.get("project_id"),
    }
    posts = result.get("single_post_resp_list") or []
    if posts:
        first = posts[0]
        out["aweme_id"] = first.get("item_id")
        out["post_status_code"] = first.get("status_code")
        out["post_status_msg"] = first.get("status_msg")
    return out
