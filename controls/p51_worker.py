#!/usr/bin/env python3
"""P51 shared worker-session helper — spawn `actinv worker`, exchange
actinv-worker-request-1 lines with bounded waits, and reap. Every wait has
a deadline; a stuck child is killed and reaped, never leaked.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
REQUEST_SCHEMA = "actinv-worker-request-1"


class Worker:
    def __init__(self, cwd: Path = ROOT):
        self.proc = subprocess.Popen(
            [str(ACTINV), "worker"], cwd=cwd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, bufsize=0)
        self.pid = self.proc.pid
        self._buf = b""

    def _readline(self, deadline_s: float) -> str:
        start = time.monotonic()
        fd = self.proc.stdout.fileno()
        while time.monotonic() - start < deadline_s:
            if b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                return line.decode() + "\n"
            import select
            ready, _, _ = select.select([fd], [], [], 0.1)
            if not ready:
                if self.proc.poll() is not None and not self._buf:
                    raise TimeoutError("worker exited without answering")
                continue
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                raise TimeoutError("worker closed its stdout")
            self._buf += chunk
        raise TimeoutError(f"no response line within {deadline_s}s")

    def request(self, request: dict, timeout_s: float = 600.0) -> dict:
        self.proc.stdin.write(json.dumps(request).encode() + b"\n")
        self.proc.stdin.flush()
        line = self._readline(timeout_s)
        return json.loads(line)

    def run(self, spec: dict, request_id: int, timeout_s: float = 600.0) -> dict:
        return self.request({
            "schema": REQUEST_SCHEMA, "id": request_id,
            "op": "run", "spec": spec}, timeout_s)

    def stop(self, timeout_s: float = 5.0) -> int:
        try:
            response = self.request(
                {"schema": REQUEST_SCHEMA, "id": 999999, "op": "stop"},
                timeout_s=timeout_s)
            assert response.get("stopped") is True
        except (BrokenPipeError, TimeoutError, AssertionError):
            pass
        try:
            return self.proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return self.proc.wait(timeout=timeout_s)

    def kill(self, timeout_s: float = 10.0) -> int:
        self.proc.kill()
        return self.proc.wait(timeout=timeout_s)

    def rss_bytes(self) -> int:
        for line in open(f"/proc/{self.pid}/status"):
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
        return 0
