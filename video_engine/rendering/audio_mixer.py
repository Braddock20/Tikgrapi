"""Audio preparation and mixing.

The sound library holds the raw material; this module turns one file into a
track that matches the video exactly — trimmed, looped if needed, faded at
both ends and muxed into the finished MP4.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from video_engine.assets.audio import Sound, make_silence, prepare_audio
from video_engine.rendering.ffmpeg import concat_and_mux
from video_engine.rendering.profiles import RenderProfile


@dataclass
class AudioPlan:
    """What the engine decided to do with sound."""

    sound_id: str = ""
    sound_path: str = ""
    prepared_path: str = ""
    duration: float = 0.0
    looped: bool = False
    faded: bool = True
    silent: bool = False


def build_audio_track(
    sound: Sound | None,
    duration: float,
    dest: str | Path,
    *,
    work_dir: str | Path | None = None,
) -> AudioPlan:
    """Prepare an audio track of exactly ``duration`` seconds."""
    dest = Path(dest)
    plan = AudioPlan(duration=duration)
    if sound is None:
        make_silence(duration, dest)
        plan.silent = True
        plan.prepared_path = str(dest)
        return plan

    plan.sound_id = sound.id
    plan.sound_path = str(sound.path)
    plan.looped = bool(sound.duration and sound.duration < duration - 0.2)
    prepare_audio(sound, duration, dest)
    plan.prepared_path = str(dest)
    return plan


def mux(video: str | Path, audio: str | Path | None, output: str | Path, profile: RenderProfile) -> Path:
    """Put the audio and the video into the final file."""
    return concat_and_mux(video, audio, output, profile)
