"""Node signer subprocess. Long-lived, JSON-RPC over stdin/stdout."""
from __future__ import annotations

import atexit
import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Optional

from ..exceptions import SignerError

SIGNER_DIR = Path(__file__).parent
WORKER = SIGNER_DIR / "worker.mjs"


class Signer:
    """Persistent Node signer.

    Spawns one Node subprocess that loads the XBogus + XGnarly bundles once,
    then signs URLs on demand via JSON-RPC over stdin/stdout.

    Thread-safe via internal lock.
    """

    def __init__(self, ua: str, node_path: str = "node", startup_timeout: float = 8.0):
        self.ua = ua
        self._lock = threading.Lock()
        self._next_id = 0
        self._proc: Optional[subprocess.Popen] = None
        self._spawn(node_path, startup_timeout)
        atexit.register(self.close)

    def _spawn(self, node_path: str, timeout: float) -> None:
        if not WORKER.exists():
            raise SignerError(f"worker.mjs missing at {WORKER}")
        env = {**os.environ, "NODE_NO_WARNINGS": "1"}
        try:
            self._proc = subprocess.Popen(
                [node_path, str(WORKER)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=env,
            )
        except FileNotFoundError as e:
            raise SignerError(
                f"`{node_path}` not found. Install Node.js >= 18: brew install node"
            ) from e

        # Wait for ready signal
        line = self._proc.stdout.readline()
        if not line:
            err = self._proc.stderr.read()
            raise SignerError(f"signer did not start. stderr: {err}")
        msg = json.loads(line)
        if not msg.get("ready"):
            raise SignerError(f"signer ready handshake failed: {msg}")

    def _rpc(self, method: str, **kwargs: Any) -> dict[str, Any]:
        if not self._proc or self._proc.poll() is not None:
            raise SignerError("signer subprocess died")
        with self._lock:
            self._next_id += 1
            req = {"id": self._next_id, "method": method, **kwargs}
            self._proc.stdin.write(json.dumps(req) + "\n")
            self._proc.stdin.flush()
            line = self._proc.stdout.readline()
        if not line:
            raise SignerError("signer closed stdout unexpectedly")
        resp = json.loads(line)
        if not resp.get("ok"):
            raise SignerError(resp.get("error", "unknown signer error"))
        return resp

    def sign(self, url: str, *, counters: Optional[dict] = None, ubcode: int = 8) -> str:
        """Append X-Bogus + X-Gnarly to URL. Returns signed URL."""
        resp = self._rpc(
            "sign",
            url=url,
            ua=self.ua,
            counters=counters or {"totalXHRRequests": 150, "totalFetchRequests": 82,
                                    "interceptedXHRRequests": 50, "interceptedFetchRequests": 31},
            ubcode=ubcode,
        )
        return resp["signedUrl"]

    def ping(self) -> bool:
        try:
            return self._rpc("ping").get("pong", False)
        except SignerError:
            return False

    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.stdin.close()
                self._proc.wait(timeout=1.0)
            except Exception:
                self._proc.kill()
        self._proc = None
