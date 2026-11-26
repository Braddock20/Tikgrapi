"""Render profiles.

A profile describes the output: size, frame rate and encoder settings.
Two tiers ship with the engine — the publishing tier and a lighter one for
constrained instances.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RenderProfile:
    """A quality tier."""

    name: str
    width: int
    height: int
    fps: int
    crf: int = 20
    preset: str = "veryfast"
    audio_bitrate: str = "128k"

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "crf": self.crf,
            "preset": self.preset,
        }


PROFILES: dict[str, RenderProfile] = {
    "standard": RenderProfile("standard", 1080, 1920, 30, crf=20, preset="veryfast"),
    "light": RenderProfile("light", 720, 1280, 24, crf=23, preset="ultrafast"),
}


def get_profile(name: str) -> RenderProfile:
    return PROFILES.get(name, PROFILES["standard"])


def profile_names() -> list[str]:
    return list(PROFILES.keys())
