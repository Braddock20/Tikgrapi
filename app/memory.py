"""Memory guard.

Render's free tier gives a service 512 MB of RAM. A Chromium that is left
running will happily take 300–400 MB of that, which is enough to get the
process OOM-killed — and an OOM-killed process on Render is restarted from
scratch, so the session and the bot loop go down with it.

This module answers two questions cheaply and without any dependency:

* *how much memory is free right now* — read from ``/proc/meminfo``;
* *should we start a browser* — only when there is enough headroom left.

Everything degrades gracefully: on a platform without ``/proc`` the guard
simply reports "unknown" and lets the caller decide.
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path

log = logging.getLogger(__name__)

_PROC_MEMINFO = Path("/proc/meminfo")


def _read_meminfo() -> dict[str, int]:
    """Parse /proc/meminfo into a {key: kilobytes} mapping."""
    values: dict[str, int] = {}
    try:
        for line in _PROC_MEMINFO.read_text().splitlines():
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts and parts[0].isdigit():
                values[key.strip()] = int(parts[0])
    except (OSError, ValueError):
        return {}
    return values


def free_memory_mb() -> int | None:
    """Memory available to this process, in MB.

    ``MemAvailable`` is the kernel's own estimate of what can be handed out
    without swapping, which is the number that actually matters.
    """
    info = _read_meminfo()
    if not info:
        return None
    kb = info.get("MemAvailable")
    if kb is None:
        total = info.get("MemTotal")
        free = info.get("MemFree")
        if total is None or free is None:
            return None
        kb = total - free
    return kb // 1024


def total_memory_mb() -> int | None:
    info = _read_meminfo()
    kb = info.get("MemTotal")
    return kb // 1024 if kb is not None else None


def process_rss_mb() -> float:
    """Resident set size of this process, in MB."""
    try:
        with open("/proc/self/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except (OSError, ValueError, IndexError):
        pass
    # ru_maxrss is kilobytes on Linux.
    try:
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except Exception:  # noqa: BLE001
        return 0.0


def disk_free_mb(path: str | str = ".") -> int | None:
    """Free space on the filesystem that holds ``path``."""
    try:
        usage = shutil.disk_usage(path)
        return usage.free // (1024 * 1024)
    except OSError:
        return None


def can_start_browser(min_free_mb: int, limit_mb: int | None = None) -> tuple[bool, str]:
    """May we launch a browser right now?

    Returns ``(allowed, reason)`` so the reason can be surfaced to the
    operator instead of failing as an opaque 500.
    """
    free = free_memory_mb()
    if free is None:
        # No /proc (macOS, BSD, some containers). Do not block the caller.
        return True, "memory availability unknown"

    budget = min_free_mb
    if limit_mb:
        # Leave room for the rest of the service inside the stated ceiling.
        overhead = max(0, limit_mb - min_free_mb)
        if free < budget + overhead // 2:
            return False, (
                f"only {free} MB free, a browser needs about {budget} MB "
                f"plus headroom inside the {limit_mb} MB budget"
            )
    if free < budget:
        return False, f"only {free} MB free, a browser needs about {budget} MB"
    return True, f"{free} MB free"


def snapshot() -> dict:
    """A small, JSON-friendly view of what the process is using."""
    total = total_memory_mb()
    free = free_memory_mb()
    rss = process_rss_mb()
    return {
        "rss_mb": round(rss, 1),
        "total_mb": total,
        "free_mb": free,
        "used_mb": round(total - free, 1) if (total is not None and free is not None) else None,
        "percent_used": (
            round((total - free) / total * 100, 1)
            if (total and free is not None and total > 0)
            else None
        ),
        "disk_free_mb": disk_free_mb(),
        "pid": os.getpid(),
    }


class IdleRelease:
    """Tracks when a resource was last used so it can be let go again.

    Chromium is expensive to keep around and nearly free to restart, so the
    engine holds one only while it is actually being used.
    """

    def __init__(self, timeout: float) -> None:
        self.timeout = max(0.0, timeout)
        self.last_used = time.monotonic()
        self.released = 0

    def touch(self) -> None:
        self.last_used = time.monotonic()

    @property
    def idle_for(self) -> float:
        return time.monotonic() - self.last_used

    @property
    def expired(self) -> bool:
        return self.timeout > 0 and self.idle_for >= self.timeout


__all__ = [
    "IdleRelease",
    "can_start_browser",
    "disk_free_mb",
    "free_memory_mb",
    "process_rss_mb",
    "snapshot",
    "total_memory_mb",
]
