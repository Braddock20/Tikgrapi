"""Captcha solver hooks. Pluggable backends — anti-captcha + custom.

TikTok's web login captcha types observed:
  1. SHAPES — pick 2 matching shapes from grid (ImageToCoordinatesTask)
  2. PUZZLE — drag puzzle piece into slot (TikTokCaptchaTaskProxyless)
  3. ROTATE — rotate image to upright (TikTokCaptchaTaskProxyless)
  4. ICON — pick icons matching prompt (ImageToCoordinatesTask)

Anti-captcha pricing (Apr 2026): $0.5-2 per 1000 solves, ~5-15s latency.
"""
from __future__ import annotations

import base64
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

import httpx


class CaptchaSolver(ABC):
    """Base solver. Implement for any captcha provider."""

    @abstractmethod
    def solve_image_to_coords(
        self, image_bytes: bytes, prompt: Optional[str] = None
    ) -> list[tuple[int, int]]:
        """Return list of (x, y) click points for shape/icon-pick captchas."""

    @abstractmethod
    def solve_slider(
        self, bg_image: bytes, slider_image: bytes
    ) -> int:
        """Return X offset (px) to slide the puzzle piece."""

    @abstractmethod
    def solve_rotate(self, image_bytes: bytes) -> int:
        """Return rotation angle (degrees) needed to make image upright."""


class NoOpSolver(CaptchaSolver):
    """No solver configured. Raises if captcha hit."""

    def solve_image_to_coords(self, image_bytes, prompt=None):
        raise RuntimeError("captcha hit but no solver configured. Pass solver=AntiCaptchaSolver(api_key)")

    def solve_slider(self, bg_image, slider_image):
        raise RuntimeError("captcha hit but no solver configured.")

    def solve_rotate(self, image_bytes):
        raise RuntimeError("captcha hit but no solver configured.")


class AntiCaptchaSolver(CaptchaSolver):
    """anti-captcha.com solver. Cheapest reliable option for TikTok shapes/slider."""

    BASE = "https://api.anti-captcha.com"

    def __init__(self, api_key: str, *, timeout: float = 120.0, poll_interval: float = 3.0):
        self.api_key = api_key
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.client = httpx.Client(timeout=30.0)

    def _create_task(self, task: dict[str, Any]) -> int:
        r = self.client.post(
            f"{self.BASE}/createTask",
            json={"clientKey": self.api_key, "task": task},
        )
        r.raise_for_status()
        data = r.json()
        if data.get("errorId") != 0:
            raise RuntimeError(f"anti-captcha createTask error: {data.get('errorDescription')}")
        return data["taskId"]

    def _poll(self, task_id: int) -> dict[str, Any]:
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            time.sleep(self.poll_interval)
            r = self.client.post(
                f"{self.BASE}/getTaskResult",
                json={"clientKey": self.api_key, "taskId": task_id},
            )
            r.raise_for_status()
            data = r.json()
            if data.get("errorId") != 0:
                raise RuntimeError(f"anti-captcha getTaskResult error: {data.get('errorDescription')}")
            if data.get("status") == "ready":
                return data["solution"]
        raise TimeoutError(f"anti-captcha timed out after {self.timeout}s")

    def solve_image_to_coords(
        self, image_bytes: bytes, prompt: Optional[str] = None
    ) -> list[tuple[int, int]]:
        body_b64 = base64.b64encode(image_bytes).decode()
        task: dict[str, Any] = {
            "type": "ImageToCoordinatesTask",
            "body": body_b64,
        }
        if prompt:
            task["comment"] = prompt
        task_id = self._create_task(task)
        sol = self._poll(task_id)
        return [(int(c[0]), int(c[1])) for c in sol.get("coordinates", [])]

    def solve_slider(self, bg_image: bytes, slider_image: bytes) -> int:
        """anti-captcha doesn't natively support slider — use generic ImageToTextTask
        with a prompt asking for X offset of the slot."""
        # Concat bg + slider into one image for the worker to see both
        # In practice you'd use cv2 to find contour offset locally — fallback to manual
        raise NotImplementedError(
            "Slider auto-solve not implemented. Use OpenCV template-matching fallback "
            "or send a custom-built image to ImageToCoordinatesTask."
        )

    def solve_rotate(self, image_bytes: bytes) -> int:
        # anti-captcha has RotateTask
        body_b64 = base64.b64encode(image_bytes).decode()
        task = {
            "type": "RotateTask",
            "body": body_b64,
        }
        task_id = self._create_task(task)
        sol = self._poll(task_id)
        return int(sol.get("rotate", 0))

    def balance(self) -> float:
        """Get account balance in USD. Useful sanity check."""
        r = self.client.post(f"{self.BASE}/getBalance", json={"clientKey": self.api_key})
        r.raise_for_status()
        data = r.json()
        if data.get("errorId") != 0:
            raise RuntimeError(f"anti-captcha balance error: {data.get('errorDescription')}")
        return float(data["balance"])

    def close(self) -> None:
        self.client.close()
