#!/usr/bin/env python3
"""Build a contact sheet from finished videos.

One frame per video, tiled — the fastest way to see whether a batch looks
varied or whether something is broken.

    python tools/contact_sheet.py --dir data/video_engine/out --out data/qa/sheet.png
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image


def frame_at(video: Path, timestamp: float, dest: Path) -> Path | None:
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-ss", f"{timestamp:.2f}",
            "-i", str(video),
            "-frames:v", "1",
            "-vf", "scale=360:-1",
            str(dest),
        ],
        capture_output=True,
        check=False,
    )
    return dest if dest.exists() else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", default="data/video_engine/out")
    ap.add_argument("--out", default="data/qa/sheet.png")
    ap.add_argument("--at", type=float, default=5.0, help="the second to sample")
    ap.add_argument("--columns", type=int, default=4)
    args = ap.parse_args()

    videos = sorted(Path(args.dir).glob("*.mp4"))
    if not videos:
        print(f"no videos in {args.dir}")
        return 1

    tiles: list[Image.Image] = []
    with tempfile.TemporaryDirectory() as tmp:
        for index, video in enumerate(videos):
            path = frame_at(video, args.at, Path(tmp) / f"f{index}.png")
            if path is None:
                continue
            with Image.open(path) as image:
                tiles.append(image.convert("RGB"))

    if not tiles:
        print("no frames could be extracted")
        return 1

    columns = max(1, min(args.columns, len(tiles)))
    rows = (len(tiles) + columns - 1) // columns
    tile_w = max(t.width for t in tiles)
    tile_h = max(t.height for t in tiles)
    sheet = Image.new("RGB", (columns * tile_w, rows * tile_h), (18, 18, 20))
    for index, tile in enumerate(tiles):
        x = (index % columns) * tile_w
        y = (index // columns) * tile_h
        sheet.paste(tile, (x, y))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    print(f"{len(tiles)} frame(s) -> {out} ({sheet.width}x{sheet.height})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
