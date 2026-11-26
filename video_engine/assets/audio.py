"""Sound selection and audio preparation.

The sound library is scanned for audio files; the mood is derived from the
folder or the file name. A chosen sound is trimmed, looped and faded with
FFmpeg so it never ends abruptly.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from video_engine.assets.metadata import probe_media

AUDIO_SUFFIXES = {".mp3", ".wav", ".aac", ".m4a", ".ogg", ".flac"}

MOOD_WORDS = {
    "nostalgic": ("nostalgic", "piano", "vinyl", "old", "memory", "wistful", "acoustic"),
    "melancholic": ("sad", "melancholy", "rain", "blue", "slow", "sad_song"),
    "romantic": ("love", "romantic", "warm", "strings"),
    "reflective": ("ambient", "thinking", "thought", "quiet", "contemplative"),
    "hopeful": ("hopeful", "bright", "uplift", "sunrise"),
    "calm": ("calm", "calm_piano", "soft", "sleep", "drone", "ambient"),
    "energetic": ("upbeat", "energetic", "happy", "dance", "funky"),
    "funny": ("funny", "quirky", "comedy", "playful", "silly", "banjo"),
    "mysterious": ("mystery", "dark", "suspense"),
    "intense": ("epic", "intense", "trailer", "drums"),
    "neutral": ("lofi", "lo-fi", "chill", "default"),
}


@dataclass
class Sound:
    id: str
    path: Path
    mood: str = "neutral"
    duration: float = 0.0
    loopable: bool = True
    genre: str = ""
    license: str = "user_supplied_authorized"

    @property
    def name(self) -> str:
        return self.path.stem


def _guess_mood(path: Path) -> str:
    parts = [p.lower() for p in path.parts]
    stem = path.stem.lower()
    for part in parts + [stem]:
        for mood, words in MOOD_WORDS.items():
            if any(word in part for word in words):
                return mood
    return "neutral"


class SoundLibrary:
    """All usable audio files, indexed by mood."""

    def __init__(self, sounds_dir: str | Path) -> None:
        self.root = Path(sounds_dir)
        self.sounds: list[Sound] = []
        self.discover()

    def discover(self) -> None:
        self.sounds = []
        if not self.root.exists():
            return
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in AUDIO_SUFFIXES:
                continue
            info = probe_media(path)
            self.sounds.append(
                Sound(
                    id=str(path.relative_to(self.root)),
                    path=path,
                    mood=_guess_mood(path),
                    duration=info.duration,
                    loopable=info.duration < 30.0,
                    genre=path.parent.name.lower(),
                )
            )

    def by_mood(self, mood: str) -> list[Sound]:
        return [s for s in self.sounds if s.mood == mood]

    def select(self, mood: str, rng, avoid: Iterable[str] = (), duration: float = 15.0) -> Sound | None:
        """Pick a sound that fits the mood and the video length."""
        if not self.sounds:
            return None
        avoid_set = set(avoid)
        preferred = self.by_mood(mood)
        if not preferred:
            preferred = [s for s in self.sounds if s.mood in {"neutral", "calm"}] or self.sounds

        fresh = [s for s in preferred if s.id not in avoid_set] or preferred
        # Prefer a sound that is long enough to cover the video without looping.
        long_enough = [s for s in fresh if s.duration >= duration + 0.5]
        pool = long_enough or fresh
        # Among equals prefer the one closest to (but not far above) the target.
        pool = sorted(pool, key=lambda s: abs((s.duration or 0) - duration))[: max(1, len(pool) // 2)]
        return rng.choice(pool)


def _ffmpeg() -> str:
    binary = shutil.which("ffmpeg")
    if not binary:
        raise RuntimeError("ffmpeg is required for audio preparation")
    return binary


def prepare_audio(
    sound: Sound,
    duration: float,
    dest: str | Path,
    *,
    fade_in: float = 0.8,
    fade_out: float = 1.4,
) -> Path:
    """Trim, loop and fade a sound to exactly ``duration`` seconds.

    Returns the path of the prepared AAC track.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    fade_in = max(0.0, min(fade_in, duration / 3))
    fade_out = max(0.0, min(fade_out, duration / 3))

    filters = [f"afade=t=in:st=0:d={fade_in:.2f}", f"afade=t=out:st={max(0.0, duration - fade_out):.2f}:d={fade_out:.2f}"]

    source_duration = sound.duration or 0.0
    args = [_ffmpeg(), "-y", "-nostdin", "-loglevel", "error"]
    if source_duration and source_duration < duration:
        # Loop the source, then cut to the exact length.
        args += ["-stream_loop", "-1", "-i", str(sound.path)]
    else:
        args += ["-i", str(sound.path)]
    args += ["-t", f"{duration:.3f}", "-af", ",".join(filters), "-c:a", "aac", "-b:a", "128k", str(dest)]

    result = subprocess.run(args, capture_output=True, text=True, timeout=300, check=False)
    if result.returncode != 0 or not dest.exists():
        raise RuntimeError(f"audio preparation failed: {result.stderr.strip()[:300]}")
    return dest


def make_silence(duration: float, dest: str | Path) -> Path:
    """A silent track, used when a video must be published without sound."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = [
        _ffmpeg(),
        "-y",
        "-nostdin",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t",
        f"{duration:.3f}",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        str(dest),
    ]
    result = subprocess.run(args, capture_output=True, text=True, timeout=180, check=False)
    if result.returncode != 0 or not dest.exists():
        raise RuntimeError(f"could not create silence: {result.stderr.strip()[:200]}")
    return dest
