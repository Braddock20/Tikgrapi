"""Temporary storage hygiene.

Render instances have an ephemeral filesystem, so anything left behind has
to be cleaned on purpose: work directories after a job, stale previews, and
videos older than the retention window.
"""
from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class CleanupReport:
    files_removed: int = 0
    bytes_removed: int = 0
    directories_removed: int = 0


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def clear_directory(directory: str | Path) -> CleanupReport:
    """Remove everything inside a directory but keep the directory itself."""
    directory = Path(directory)
    report = CleanupReport()
    if not directory.exists():
        return report
    for entry in sorted(directory.iterdir()):
        try:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
                report.directories_removed += 1
            else:
                report.bytes_removed += _size(entry)
                entry.unlink()
                report.files_removed += 1
        except OSError:
            continue
    return report


def prune_old_files(directory: str | Path, *, max_age_days: float = 14.0, pattern: str = "*") -> CleanupReport:
    """Delete files older than the retention window."""
    directory = Path(directory)
    report = CleanupReport()
    if not directory.exists():
        return report
    cutoff = time.time() - max_age_days * 86400
    for entry in directory.glob(pattern):
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                report.bytes_removed += _size(entry)
                entry.unlink()
                report.files_removed += 1
        except OSError:
            continue
    return report


def cleanup_workspace(
    work_dir: str | Path,
    output_dir: str | Path | None = None,
    *,
    max_age_days: float = 14.0,
) -> CleanupReport:
    """Clear the work directory and prune old videos."""
    report = clear_directory(work_dir)
    if output_dir:
        pruned = prune_old_files(output_dir, max_age_days=max_age_days, pattern="*.mp4")
        report.files_removed += pruned.files_removed
        report.bytes_removed += pruned.bytes_removed
    return report
