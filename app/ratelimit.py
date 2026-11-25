"""Conservative request pacing.

Two limits, both applied per process:
  * a minimum gap between any two TikTok requests
  * a rolling hourly ceiling

Both exist because a datacenter IP talking to TikTok in bursts is the
fastest way to get an account flagged.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque


class RateLimiter:
    def __init__(self, min_interval: float = 2.0, max_per_hour: int = 240) -> None:
        self.min_interval = max(0.0, min_interval)
        self.max_per_hour = max(1, max_per_hour)
        self._lock = asyncio.Lock()
        self._last_call = 0.0
        self._calls: deque[float] = deque()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= 3600:
                    self._calls.popleft()

                if len(self._calls) >= self.max_per_hour:
                    wait = 3600 - (now - self._calls[0])
                    if wait > 0:
                        await asyncio.sleep(wait + 0.05)
                    continue

                wait = self.min_interval - (now - self._last_call)
                if wait > 0:
                    await asyncio.sleep(wait)
                    continue

                self._last_call = time.monotonic()
                self._calls.append(self._last_call)
                return

    def stats(self) -> dict:
        now = time.monotonic()
        recent = sum(1 for t in self._calls if now - t < 3600)
        return {
            "calls_last_hour": recent,
            "max_per_hour": self.max_per_hour,
            "min_interval": self.min_interval,
        }
