"""Loading content libraries.

The engine reads every ``*.json`` / ``*.txt`` / ``*.md`` file it finds in the
content directory, so new material can be dropped in at any time without a
code change.

Supported shapes
----------------
* a JSON list of objects
* a JSON object with ``items`` / ``content`` / a category map
* a JSON object that is a single record
* a plain-text file: one item per line, or blank-line separated paragraphs
  (a ``#`` comment line is ignored)

Records keep their original wording. Nothing is rewritten or generated.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from video_engine.content.validator import (
    ContentError,
    normalise_text,
    text_fingerprint,
    validate_category,
    validate_format,
    validate_intensity,
    validate_mood,
    validate_tags,
    validate_text,
)

TEXT_SUFFIXES = {".txt", ".md"}
JSON_SUFFIXES = {".json"}


@dataclass
class ContentItem:
    """One usable piece of content."""

    id: str
    text: str
    category: str = "thought"
    mood: str = "neutral"
    intensity: str = "medium"
    format: str = "diary"
    tags: list[str] = field(default_factory=list)
    duration_hint: float | None = None
    source: str = ""
    fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "category": self.category,
            "mood": self.mood,
            "intensity": self.intensity,
            "format": self.format,
            "tags": self.tags,
            "duration_hint": self.duration_hint,
            "source": self.source,
        }


@dataclass
class LoadReport:
    """What the loader found — and what it refused."""

    files: int = 0
    accepted: int = 0
    rejected: int = 0
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.files} file(s) — {self.accepted} item(s) accepted, "
            f"{self.rejected} rejected, {self.duplicates} duplicate(s)"
        )


class ContentLibrary:
    """An immutable, de-duplicated set of content items."""

    def __init__(self, items: Iterable[ContentItem], report: LoadReport | None = None) -> None:
        self.items = list(items)
        self.report = report or LoadReport()

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[ContentItem]:
        return iter(self.items)

    def by_category(self) -> dict[str, list[ContentItem]]:
        out: dict[str, list[ContentItem]] = {}
        for item in self.items:
            out.setdefault(item.category, []).append(item)
        return out


def _record_from_mapping(raw: dict[str, Any], source: str, index: int) -> ContentItem:
    text = raw.get("text") or raw.get("content") or raw.get("body") or raw.get("caption") or ""
    if not isinstance(text, str):
        raise ContentError(f"record {index}: 'text' is not a string")
    clean = validate_text(text)
    duration_hint = raw.get("duration_hint") or raw.get("duration")
    try:
        duration = float(duration_hint) if duration_hint is not None else None
    except (TypeError, ValueError):
        duration = None
    item_id = str(raw.get("id") or f"{Path(source).stem}_{index:04d}")
    return ContentItem(
        id=item_id,
        text=clean,
        category=validate_category(raw.get("category")),
        mood=validate_mood(raw.get("mood")),
        intensity=validate_intensity(raw.get("intensity")),
        format=validate_format(raw.get("format")),
        tags=validate_tags(raw.get("tags")),
        duration_hint=duration,
        source=source,
        fingerprint=text_fingerprint(clean),
    )


def _iter_json_records(payload: Any) -> Iterator[dict[str, Any]]:
    if isinstance(payload, list):
        for row in payload:
            if isinstance(row, dict):
                yield row
            elif isinstance(row, str):
                yield {"text": row}
    elif isinstance(payload, dict):
        for key in ("items", "content", "records", "data", "entries"):
            if isinstance(payload.get(key), list):
                yield from _iter_json_records(payload[key])
                return
        # A map of category -> list of records.
        values = list(payload.values())
        if values and all(isinstance(v, list) for v in values):
            for value in values:
                yield from _iter_json_records(value)
            return
        yield payload


def _split_plain_text(raw: str) -> list[str]:
    blocks: list[str] = []
    for chunk in raw.split("\n\n"):
        lines = [line.strip() for line in chunk.splitlines()]
        lines = [line for line in lines if line and not line.startswith("#")]
        if not lines:
            continue
        # A multi-line block is one item unless the lines look like a list.
        if len(lines) > 1 and all(len(line) < 160 for line in lines):
            blocks.extend(lines)
        else:
            blocks.append(" ".join(lines))
    return blocks


def load_library(content_dir: str | Path) -> ContentLibrary:
    """Read every supported file in ``content_dir``."""
    root = Path(content_dir)
    report = LoadReport()
    items: list[ContentItem] = []
    seen: set[str] = set()

    if not root.exists():
        report.errors.append(f"content directory does not exist: {root}")
        return ContentLibrary(items, report)

    files = sorted(
        [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES | JSON_SUFFIXES],
        key=lambda p: str(p),
    )

    for path in files:
        report.files += 1
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            report.rejected += 1
            report.errors.append(f"{path.name}: {exc}")
            continue

        records: list[dict[str, Any]] = []
        if path.suffix.lower() in JSON_SUFFIXES:
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                report.rejected += 1
                report.errors.append(f"{path.name}: invalid JSON ({exc.msg} at line {exc.lineno})")
                continue
            records = list(_iter_json_records(payload))
            if not records:
                report.errors.append(f"{path.name}: no content records found")
        else:
            records = [{"text": block} for block in _split_plain_text(raw)]
            if not records:
                report.errors.append(f"{path.name}: no text found")

        for index, record in enumerate(records, start=1):
            try:
                item = _record_from_mapping(record, str(path), index)
            except ContentError as exc:
                report.rejected += 1
                report.errors.append(f"{path.name}#{index}: {exc}")
                continue
            if item.fingerprint in seen:
                report.duplicates += 1
                continue
            seen.add(item.fingerprint)
            items.append(item)
            report.accepted += 1

    return ContentLibrary(items, report)


def split_text(text: str, max_chars: int = 220) -> list[str]:
    """Split a long item into page-sized chunks at sentence boundaries.

    The wording is preserved exactly; only the page break moves.
    """
    text = normalise_text(text)
    if len(text) <= max_chars:
        return [text]

    sentences = [s.strip() for s in text.replace("\n", " ").split(". ") if s.strip()]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current}. {sentence}" if current else sentence
        if len(candidate) > max_chars and current:
            chunks.append(current.rstrip(".") + ".")
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current.rstrip(".") + ".")

    # A single sentence longer than the limit is broken at word boundaries.
    out: list[str] = []
    for chunk in chunks:
        while len(chunk) > max_chars * 1.4:
            cut = chunk.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            out.append(chunk[:cut].rstrip(",;") + ",")
            chunk = chunk[cut + 1 :]
        out.append(chunk)
    return [c for c in out if c.strip()]
