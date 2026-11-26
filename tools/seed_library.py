#!/usr/bin/env python3
"""Create a starter content library and a placeholder asset library.

The engine works with whatever you give it — this script only fills the
empty folders so the pipeline can be exercised before your own material is
in place.

    python tools/seed_library.py --content-dir content --assets-dir assets

The content written here is sample material and is meant to be replaced.
The images and sounds are generated procedurally (no stock library, no
scraping) and carry no third-party rights.
"""
from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

# --------------------------------------------------------------------------
# content
# --------------------------------------------------------------------------
LIBRARY: dict[str, list[dict]] = {
    "thoughts.json": [
        {"id": "th_001", "text": "Some days the quiet is enough, and other days it is just the absence of an answer.", "category": "reflection", "mood": "reflective", "intensity": "medium", "format": "diary", "tags": ["solitude", "quiet"]},
        {"id": "th_002", "text": "You grow up and suddenly the biggest problem is that nobody called you back.", "category": "thought", "mood": "humour", "intensity": "low", "format": "scrapbook", "tags": ["growingup", "friendship"]},
        {"id": "th_003", "text": "The version of you that laughed at everything is still in there. She just takes longer to come out.", "category": "reflection", "mood": "melancholic", "intensity": "medium", "format": "diary", "tags": ["change", "growingup"]},
        {"id": "th_004", "text": "I keep a list of things I did not say, mostly to prove that I was thinking about you.", "category": "relationship", "mood": "romantic", "intensity": "medium", "format": "letter", "tags": ["words", "silence"]},
        {"id": "th_005", "text": "Rest is not the same as being tired, but it is harder to admit.", "category": "thought", "mood": "calm", "intensity": "low", "format": "minimal", "tags": ["rest", "slow"]},
        {"id": "th_006", "text": "Nothing is as clarifying as a long walk with no destination and no headphones.", "category": "thought", "mood": "calm", "intensity": "low", "format": "diary", "tags": ["walking", "thinking"]},
        {"id": "th_007", "text": "Everyone is one bad week away from a completely different life, and almost nobody tells you that.", "category": "observation", "mood": "reflective", "intensity": "high", "format": "editorial", "tags": ["perspective", "change"]},
        {"id": "th_008", "text": "I like people who are late and honest about being late.", "category": "humour", "mood": "funny", "intensity": "low", "format": "scrapbook", "tags": ["friendship", "quirky"]},
        {"id": "th_009", "text": "The trick is not to want the old days back. It is to let them be worth having had.", "category": "reflection", "mood": "nostalgic", "intensity": "medium", "format": "nostalgic", "tags": ["nostalgia", "growth"]},
        {"id": "th_010", "text": "Some people arrive in your life like weather, and you are glad for the rain anyway.", "category": "observation", "mood": "reflective", "intensity": "medium", "format": "editorial", "tags": ["people", "weather"]},
        {"id": "th_011", "text": "I am learning that being needed is not the same as being loved.", "category": "relationship", "mood": "melancholic", "intensity": "high", "format": "diary", "tags": ["love", "honesty"]},
        {"id": "th_012", "text": "Your phone is full of photos of a person you barely speak to anymore.", "category": "observation", "mood": "nostalgic", "intensity": "low", "format": "scrapbook", "tags": ["memories", "phone"]},
    ],
    "memories.json": [
        {"id": "mem_001", "text": "There was a summer where we stayed up until four and nothing bad ever happened.", "category": "memory", "mood": "nostalgic", "intensity": "medium", "format": "nostalgic", "tags": ["summer", "friendship", "youth"]},
        {"id": "mem_002", "text": "My grandmother's kitchen always smelled like cinnamon and something I could never name.", "category": "memory", "mood": "nostalgic", "intensity": "medium", "format": "letter", "tags": ["family", "kitchen", "scent"]},
        {"id": "mem_003", "text": "We drove with the windows down and the same song on repeat for six hours.", "category": "memory", "mood": "nostalgic", "intensity": "low", "format": "nostalgic", "tags": ["roadtrip", "music"]},
        {"id": "mem_004", "text": "The old house is an apartment now, and someone else waters the plants.", "category": "memory", "mood": "melancholic", "intensity": "medium", "format": "editorial", "tags": ["home", "change"]},
        {"id": "mem_005", "text": "You sent me a voice note at 2am and I have never deleted it.", "category": "relationship", "mood": "romantic", "intensity": "medium", "format": "letter", "tags": ["voicenote", "night"]},
        {"id": "mem_006", "text": "Before everything got complicated, the plan was simple and we believed it.", "category": "memory", "mood": "nostalgic", "intensity": "high", "format": "nostalgic", "tags": ["youth", "belief"]},
        {"id": "mem_007", "text": "There was a blue bike with a bell that did not ring, and I was the only one who could fix it.", "category": "memory", "mood": "nostalgic", "intensity": "low", "format": "scrapbook", "tags": ["childhood", "bike"]},
    ],
    "questions.json": [
        {"id": "q_001", "text": "What would you do differently if you knew it was your last ordinary Tuesday?", "category": "question", "mood": "reflective", "intensity": "medium", "format": "minimal", "tags": ["question", "regret"]},
        {"id": "q_002", "text": "Have you ever stayed somewhere just to see who would come looking for you?", "category": "question", "mood": "reflective", "intensity": "high", "format": "scrapbook", "tags": ["question", "people"]},
        {"id": "q_003", "text": "Is it weird that I miss the sound of a dial-up internet more than most sounds?", "category": "question", "mood": "nostalgic", "intensity": "low", "format": "nostalgic", "tags": ["nostalgia", "internet"]},
        {"id": "q_004", "text": "Why do we say we are fine when we are clearly just busy?", "category": "question", "mood": "reflective", "intensity": "medium", "format": "editorial", "tags": ["question", "honesty"]},
        {"id": "q_005", "text": "Do you ever talk to yourself in the exact voice someone else used to use?", "category": "question", "mood": "nostalgic", "intensity": "medium", "format": "diary", "tags": ["question", "people"]},
    ],
    "observations.json": [
        {"id": "ob_001", "text": "Everyone photographs the food and forgets to photograph each other.", "category": "observation", "mood": "humour", "intensity": "low", "format": "scrapbook", "tags": ["friendship", "food"]},
        {"id": "ob_002", "text": "People are at their kindest when they do not know you are watching.", "category": "observation", "mood": "reflective", "intensity": "medium", "format": "editorial", "tags": ["kindness", "people"]},
        {"id": "ob_003", "text": "The supermarket at 6am is the only place that still feels like the world belongs to adults.", "category": "observation", "mood": "calm", "intensity": "low", "format": "minimal", "tags": ["morning", "quiet"]},
        {"id": "ob_004", "text": "Confession: the playlist I made for you was mostly songs I was already thinking about.", "category": "relationship", "mood": "romantic", "intensity": "medium", "format": "letter", "tags": ["music", "confession"]},
        {"id": "ob_005", "text": "Getting over something is mostly just getting busy enough to stop noticing.", "category": "observation", "mood": "melancholic", "intensity": "medium", "format": "diary", "tags": ["healing", "busy"]},
    ],
    "captions.json": [
        {"id": "cap_001", "text": "POV: you said one sentence and rewrote the whole evening in your head.", "category": "caption", "mood": "humour", "intensity": "low", "format": "scrapbook", "tags": ["pov", "overthinking"]},
        {"id": "cap_002", "text": "Things I did not say today, in order of increasing cowardice.", "category": "caption", "mood": "funny", "intensity": "low", "format": "scrapbook", "tags": ["pov", "regret"]},
        {"id": "cap_003", "text": "Nobody warns you that growing up is mostly learning which rooms to leave.", "category": "caption", "mood": "reflective", "intensity": "medium", "format": "editorial", "tags": ["growth", "quotes"]},
        {"id": "cap_004", "text": "Me explaining the plan: definitely a plan. Absolutely a plan.", "category": "caption", "mood": "funny", "intensity": "low", "format": "scrapbook", "tags": ["pov", "humor"]},
    ],
    "stories.json": [
        {
            "id": "st_001",
            "text": "We met on the worst night of the year. The heater had died, the bus was late, and I was sitting on the floor of a station I did not know the name of. You sat down next to me with two coffees and did not ask a single question. You just handed me one and started talking about the weather. That was the year I stopped being afraid of strangers. That was the year I stopped being afraid of most things. And that was the last year I was sure about anything at all.",
            "category": "story",
            "mood": "nostalgic",
            "intensity": "high",
            "format": "story",
            "tags": ["firstmeeting", "winter", "coffee"],
            "duration_hint": 26,
        },
        {
            "id": "st_002",
            "text": "My father never said he was proud of me. He also never once asked whether I was doing all right, and I only understood what that meant at about thirty. We fixed it the way families fix things: badly, slowly, and with a lot of talking about something else. He started asking about my week. I started calling him on Sundays. It is not the version I wanted. It is the version we got, and it turns out that is usually enough.",
            "category": "story",
            "mood": "reflective",
            "intensity": "high",
            "format": "story",
            "tags": ["family", "father", "growth"],
            "duration_hint": 26,
        },
    ],
}


def write_content(target: Path) -> int:
    target.mkdir(parents=True, exist_ok=True)
    written = 0
    for name, items in LIBRARY.items():
        path = target / name
        path.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
        written += len(items)
    return written


# --------------------------------------------------------------------------
# images
# --------------------------------------------------------------------------
PALETTES = [
    ((244, 238, 226), (198, 110, 74), (126, 140, 107)),
    ((232, 238, 243), (62, 108, 142), (201, 118, 47)),
    ((238, 229, 219), (192, 107, 78), (95, 122, 107)),
    ((234, 239, 227), (110, 139, 98), (193, 116, 63)),
    ((240, 228, 207), (169, 113, 60), (110, 127, 106)),
    ((243, 243, 241), (138, 138, 138), (178, 58, 46)),
]


def _grain(image: Image.Image, amount: float = 7.0) -> Image.Image:
    array = np.asarray(image).astype(np.float32)
    rng = np.random.default_rng(abs(hash(image.size)) % (2**31))
    array += rng.normal(0, amount, array.shape)
    return Image.fromarray(np.clip(array, 0, 255).astype(np.uint8))


def make_photo(path: Path, index: int, width: int = 900, height: int = 1200) -> None:
    base, accent, second = PALETTES[index % len(PALETTES)]
    image = Image.new("RGB", (width, height), base)
    draw = ImageDraw.Draw(image, "RGBA")

    # A soft vertical wash.
    for y in range(height):
        p = y / height
        draw.line(
            [(0, y), (width, y)],
            fill=(
                int(base[0] * (1 - p) + accent[0] * p * 0.35),
                int(base[1] * (1 - p) + accent[1] * p * 0.35),
                int(base[2] * (1 - p) + accent[2] * p * 0.35),
            ),
        )

    # Bokeh — soft circles of light.
    rng = np.random.default_rng(1000 + index)
    for _ in range(rng.integers(14, 24)):
        r = int(rng.integers(40, 220))
        cx = int(rng.integers(-60, width + 60))
        cy = int(rng.integers(-60, height + 60))
        colour = accent if rng.random() < 0.6 else second
        alpha = int(rng.integers(28, 90))
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=colour + (alpha,))

    # A horizon line gives the frame some structure.
    horizon = int(height * rng.uniform(0.55, 0.72))
    draw.rectangle([0, horizon, width, height], fill=second + (70,))
    draw.line([(0, horizon), (width, horizon)], fill=base + (120,), width=3)

    image = image.filter(ImageFilter.GaussianBlur(1.4))
    image = _grain(image, 6.0)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "JPEG", quality=88)


def make_background(path: Path, index: int, width: int = 1080, height: int = 1920) -> None:
    base, accent, second = PALETTES[(index + 3) % len(PALETTES)]
    image = Image.new("RGB", (width, height), base)
    draw = ImageDraw.Draw(image, "RGBA")
    rng = np.random.default_rng(500 + index)
    for y in range(height):
        p = y / height
        draw.line(
            [(0, y), (width, y)],
            fill=(
                int(base[0] * (1 - p * 0.6) + second[0] * p * 0.6),
                int(base[1] * (1 - p * 0.6) + second[1] * p * 0.6),
                int(base[2] * (1 - p * 0.6) + second[2] * p * 0.6),
            ),
        )
    for _ in range(rng.integers(5, 10)):
        r = int(rng.integers(200, 520))
        cx = int(rng.integers(0, width))
        cy = int(rng.integers(0, height))
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=accent + (26,))
    image = image.filter(ImageFilter.GaussianBlur(28))
    image = _grain(image, 4.0)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "JPEG", quality=90)


def make_texture(path: Path, index: int, width: int = 900, height: int = 1200) -> None:
    base, _, _ = PALETTES[index % len(PALETTES)]
    image = Image.new("RGB", (width, height), base)
    draw = ImageDraw.Draw(image, "RGBA")
    rng = np.random.default_rng(900 + index)
    for _ in range(int(width * height / 5200)):
        x = int(rng.integers(0, width))
        y = int(rng.integers(0, height))
        length = int(rng.integers(6, 34))
        draw.line([(x, y), (x + length, y + int(rng.integers(-4, 4)))], fill=(255, 255, 255, 16))
    image = _grain(image, 9.0)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "JPEG", quality=90)


# --------------------------------------------------------------------------
# sounds
# --------------------------------------------------------------------------
RATE = 22050


def _adsr(length: int, attack: float, release: float) -> np.ndarray:
    env = np.ones(length, dtype=np.float32)
    a = max(1, int(length * attack))
    r = max(1, int(length * release))
    env[:a] = np.linspace(0, 1, a)
    env[-r:] = np.linspace(1, 0, r)
    return env


def _tone(freq: float, length: int, amp: float = 0.5) -> np.ndarray:
    t = np.arange(length, dtype=np.float32) / RATE
    wave = np.sin(2 * math.pi * freq * t)
    wave += 0.32 * np.sin(2 * math.pi * freq * 2 * t)
    wave += 0.16 * np.sin(2 * math.pi * freq * 3 * t)
    return (wave * _adsr(length, 0.02, 0.55) * amp).astype(np.float32)


def _pad(chords: list[list[float]], seconds: float, amp: float = 0.32) -> np.ndarray:
    """A slow chord progression."""
    total = int(RATE * seconds)
    out = np.zeros(total, dtype=np.float32)
    step = total // max(1, len(chords))
    for index, chord in enumerate(chords):
        start = index * step
        length = min(step, total - start)
        if length <= 0:
            break
        for freq in chord:
            out[start : start + length] += _tone(freq, length, amp)
    peak = float(np.max(np.abs(out))) or 1.0
    return (out / peak * 0.75).astype(np.float32)


def _pluck(freq: float, seconds: float, amp: float = 0.4) -> np.ndarray:
    length = int(RATE * seconds)
    t = np.arange(length, dtype=np.float32) / RATE
    wave = np.sin(2 * math.pi * freq * t) * np.exp(-t * 3.4)
    wave += 0.4 * np.sin(2 * math.pi * freq * 2 * t) * np.exp(-t * 5.0)
    return (wave * amp).astype(np.float32)


def _arp(notes: list[float], seconds: float, amp: float = 0.35) -> np.ndarray:
    total = int(RATE * seconds)
    out = np.zeros(total, dtype=np.float32)
    step = total // max(1, len(notes))
    for index, freq in enumerate(notes):
        start = index * step
        length = min(step, total - start)
        out[start : start + length] += _pluck(freq, length / RATE, amp)
    return out


def _write_wav(path: Path, samples: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    peak = float(np.max(np.abs(samples))) or 1.0
    data = (samples / peak * 0.82 * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(data.tobytes())


def make_sounds(root: Path) -> int:
    library = {
        "nostalgic/piano_nostalgia.wav": lambda: _pad([[261.63, 329.63, 392.00], [220.00, 261.63, 329.63], [196.00, 246.94, 293.66], [174.61, 220.00, 261.63]], 24.0, 0.3),
        "nostalgic/warm_strings.wav": lambda: _pad([[329.63, 392.00, 493.88], [293.66, 349.23, 440.00], [261.63, 329.63, 392.00], [246.94, 311.13, 369.99]], 24.0, 0.26),
        "piano/soft_keys.wav": lambda: _arp([261.63, 329.63, 392.00, 440.00, 392.00, 329.63, 293.66, 261.63] * 2, 24.0, 0.3),
        "lofi/quiet_loop.wav": lambda: _pad([[196.00, 246.94, 293.66], [174.61, 220.00, 261.63]] * 3, 24.0, 0.34),
        "ambient/drone_calm.wav": lambda: _pad([[110.00, 164.81, 220.00]] * 4, 24.0, 0.22),
        "emotional/low_strings.wav": lambda: _pad([[146.83, 185.00, 220.00], [130.81, 164.81, 196.00], [110.00, 138.59, 164.81], [123.47, 155.56, 185.00]], 24.0, 0.28),
        "upbeat/light_bounce.wav": lambda: _arp([392.00, 493.88, 587.33, 659.25, 587.33, 493.88, 392.00, 329.63] * 2, 24.0, 0.32),
        "humorous/quirky_pluck.wav": lambda: _arp([523.25, 659.25, 783.99, 659.25, 587.33, 698.46, 523.25, 659.25] * 2, 24.0, 0.28),
        "mysterious/night_air.wav": lambda: _pad([[98.00, 146.83, 196.00], [87.31, 130.81, 174.61]] * 3, 24.0, 0.2),
    }
    for name, builder in library.items():
        path = root / name
        if path.exists():
            continue
        _write_wav(path, builder())
    return len(library)


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--content-dir", default="content")
    ap.add_argument("--assets-dir", default="assets")
    ap.add_argument("--photos", type=int, default=14)
    ap.add_argument("--backgrounds", type=int, default=5)
    args = ap.parse_args()

    content_dir = Path(args.content_dir)
    assets_dir = Path(args.assets_dir)

    items = write_content(content_dir)
    print(f"content: {items} item(s) in {content_dir}")

    photos = assets_dir / "photographs"
    for index in range(args.photos):
        path = photos / f"photo_{index + 1:02d}.jpg"
        if not path.exists():
            make_photo(path, index)
    print(f"photos : {args.photos} in {photos}")

    backgrounds = assets_dir / "backgrounds" / "images"
    for index in range(args.backgrounds):
        path = backgrounds / f"background_{index + 1:02d}.jpg"
        if not path.exists():
            make_background(path, index)
    print(f"bg     : {args.backgrounds} in {backgrounds}")

    textures = assets_dir / "backgrounds" / "textures"
    for index in range(4):
        path = textures / f"paper_texture_{index + 1:02d}.jpg"
        if not path.exists():
            make_texture(path, index)
    print(f"texture: 4 in {textures}")

    sounds = make_sounds(assets_dir / "sounds")
    print(f"sounds : {sounds} in {assets_dir / 'sounds'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
