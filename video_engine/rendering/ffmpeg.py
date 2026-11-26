"""FFmpeg bridge.

Frames are streamed straight into the encoder over a pipe — no frame files
are ever written to disk. The same module owns the output validation, so a
file that cannot be decoded or does not match the profile never leaves the
engine.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from video_engine.rendering.profiles import RenderProfile


class FFmpegError(RuntimeError):
    """FFmpeg refused to do the job."""


def ffmpeg_binary() -> str:
    binary = shutil.which("ffmpeg")
    if not binary:
        raise FFmpegError("ffmpeg is not installed")
    return binary


def ffprobe_binary() -> str:
    binary = shutil.which("ffprobe")
    if not binary:
        raise FFmpegError("ffprobe is not installed")
    return binary


def encode_frames(
    frames: Iterable,
    profile: RenderProfile,
    output: str | Path,
    *,
    audio: str | Path | None = None,
    extra_filters: str | None = None,
) -> Path:
    """Encode an iterable of PIL frames into an MP4."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    args = [
        ffmpeg_binary(),
        "-y",
        "-nostdin",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{profile.width}x{profile.height}",
        "-r",
        str(profile.fps),
        "-i",
        "-",
    ]
    if audio:
        args += ["-i", str(audio)]
    filters = extra_filters or f"format=yuv420p"
    args += [
        "-vf",
        filters,
        "-c:v",
        "libx264",
        "-preset",
        profile.preset,
        "-crf",
        str(profile.crf),
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "high",
        "-level",
        "4.1",
        "-movflags",
        "+faststart",
    ]
    if audio:
        args += ["-c:a", "aac", "-b:a", profile.audio_bitrate, "-ar", "44100", "-shortest"]
    else:
        args += ["-an"]
    args += [str(output)]

    process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for frame in frames:
            process.stdin.write(frame.tobytes())
    except BrokenPipeError:  # pragma: no cover - the encoder died
        pass
    finally:
        try:
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        # communicate() must not try to flush the pipe we just closed.
        process.stdin = None
    stdout, stderr = process.communicate()
    if process.returncode != 0:
        raise FFmpegError(f"ffmpeg failed: {stderr.decode('utf-8', 'replace').strip()[:400]}")
    if not output.exists() or output.stat().st_size == 0:
        raise FFmpegError("ffmpeg produced no output")
    return output


def probe(path: str | Path) -> dict:
    """Run ffprobe and return the parsed report."""
    args = [
        ffprobe_binary(),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    result = subprocess.run(args, capture_output=True, text=True, timeout=120, check=False)
    if result.returncode != 0:
        raise FFmpegError(f"ffprobe failed: {result.stderr.strip()[:300]}")
    try:
        return json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise FFmpegError(f"ffprobe returned invalid JSON: {exc}") from exc


@dataclass
class OutputReport:
    """What the validator found in a finished file."""

    ok: bool
    path: str = ""
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_audio: bool = False
    video_codec: str = ""
    audio_codec: str = ""
    size_bytes: int = 0
    blank_frames: int = 0
    sampled_frames: int = 0
    problems: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.problems is None:
            self.problems = []

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "path": self.path,
            "duration": round(self.duration, 3),
            "width": self.width,
            "height": self.height,
            "fps": round(self.fps, 2),
            "has_audio": self.has_audio,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "size_bytes": self.size_bytes,
            "blank_frames": self.blank_frames,
            "sampled_frames": self.sampled_frames,
            "problems": list(self.problems),
        }


def sample_blank_frames(path: str | Path, samples: int = 8) -> tuple[int, int]:
    """Count blank frames among a sample of the video.

    Returns ``(blank, sampled)``. A frame is blank when its standard
    deviation is almost zero — a flat colour or a failed render.
    """
    try:
        report = probe(path)
    except FFmpegError:
        return (0, 0)
    duration = float(report.get("format", {}).get("duration") or 0.0)
    if duration <= 0:
        return (0, 0)
    step = max(0.1, duration / samples)
    blank = 0
    counted = 0
    for index in range(samples):
        timestamp = min(duration - 0.05, step * index + step / 2)
        if timestamp < 0:
            continue
        args = [
            ffmpeg_binary(),
            "-v",
            "error",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-s",
            "64x64",
            "-",
        ]
        try:
            result = subprocess.run(args, capture_output=True, timeout=60, check=False)
        except (subprocess.TimeoutExpired, OSError):
            continue
        if result.returncode != 0 or not result.stdout:
            continue
        counted += 1
        data = result.stdout
        pixels = [value for value in data]
        if len(pixels) < 16:
            continue
        mean = sum(pixels) / len(pixels)
        variance = sum((value - mean) ** 2 for value in pixels) / len(pixels)
        if variance < 1.2:
            blank += 1
    return blank, counted


def validate_output(
    path: str | Path,
    profile: RenderProfile,
    *,
    expect_audio: bool = True,
    check_blank: bool = True,
) -> OutputReport:
    """Check a finished MP4 against the profile and the quality rules."""
    path = Path(path)
    report = OutputReport(ok=False, path=str(path))
    problems = report.problems

    if not path.exists():
        problems.append("the output file does not exist")
        return report
    report.size_bytes = path.stat().st_size
    if report.size_bytes < 4096:
        problems.append(f"the output file is suspiciously small ({report.size_bytes} bytes)")

    try:
        info = probe(path)
    except FFmpegError as exc:
        problems.append(str(exc))
        return report

    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video:
        problems.append("the file has no video stream")
        return report

    report.width = int(video.get("width") or 0)
    report.height = int(video.get("height") or 0)
    report.video_codec = video.get("codec_name") or ""
    rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"
    try:
        num, den = rate.split("/")
        report.fps = float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        report.fps = 0.0
    report.duration = float(info.get("format", {}).get("duration") or 0.0)
    report.has_audio = audio is not None
    if audio:
        report.audio_codec = audio.get("codec_name") or ""

    if report.width != profile.width or report.height != profile.height:
        problems.append(f"resolution is {report.width}x{report.height}, expected {profile.resolution}")
    if report.height <= report.width:
        problems.append("the video is not vertically oriented")
    if report.duration <= 0.2:
        problems.append("the video has no usable duration")
    if expect_audio and not report.has_audio:
        problems.append("the video has no audio track")
    if abs(report.fps - profile.fps) > 1.0:
        problems.append(f"frame rate is {report.fps:.2f}, expected {profile.fps}")

    if check_blank:
        blank, sampled = sample_blank_frames(path)
        report.blank_frames = blank
        report.sampled_frames = sampled
        if sampled and blank == sampled:
            problems.append("every sampled frame is blank")

    report.ok = not problems
    return report


def concat_and_mux(
    video: str | Path,
    audio: str | Path | None,
    output: str | Path,
    profile: RenderProfile,
) -> Path:
    """Mux a rendered video with an audio track."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    args = [ffmpeg_binary(), "-y", "-nostdin", "-loglevel", "error", "-i", str(video)]
    if audio:
        args += ["-i", str(audio)]
    args += ["-c:v", "copy"]
    if audio:
        args += ["-c:a", "aac", "-b:a", profile.audio_bitrate, "-ar", "44100", "-shortest"]
    else:
        args += ["-an"]
    args += ["-movflags", "+faststart", str(output)]
    result = subprocess.run(args, capture_output=True, text=True, timeout=300, check=False)
    if result.returncode != 0 or not output.exists():
        raise FFmpegError(f"mux failed: {result.stderr.strip()[:300]}")
    return output
