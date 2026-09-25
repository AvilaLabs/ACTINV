#!/usr/bin/env python3
"""P51 G4 lifecycle — memory ceiling under a sustained mixed battery (the
single-slot cache forced to thrash), cancellation by kill mid-solve, child
reaping (no zombie/orphan), and restart correctness (a re-issued solve is
byte-identical modulo declared fields to a never-killed cold run).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p11_fixtures as fx  # noqa: E402
import p51_worker as pw  # noqa: E402

ACTINV = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
OUT = ROOT / "results/g4_p51_lifecycle.json"
WORK = ROOT / "target/p51-lifecycle"
CEILING_BYTES = 4 * 1024**3
SUSTAINED_N = 20
SAMPLE_HZ = 20


def rss_bytes(pid: int) -> int:
    try:
        for line in open(f"/proc/{pid}/status"):
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


def process_gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        capture_output=True, text=True).stdout.strip().startswith("Z")


def main() -> int:
    problems: list[str] = []
    events: list[dict] = []
    t0 = time.monotonic()

    def log(event: str, **kw):
        events.append({"t_s": round(time.monotonic() - t0, 2),
                       "event": event, **kw})

    WORK.mkdir(parents=True, exist_ok=True)
    fixture = fx.make_fixture(WORK)
    small = fx.specification(fixture, mode="trace", cram_order=16)
    corpus = json.load(open(ROOT / "examples/p51_battery/corpus_probe.json"))
    (WORK / "corpus_spec.json").write_text(json.dumps(corpus))

    # Continuous RSS sampler — catches solve-time peaks, not just gaps.
    samples: list[int] = []
    sampling = True

    def sampler():
        while sampling:
            pid = current[0]
            if pid:
                rss = rss_bytes(pid)
                if rss:
                    samples.append(rss)
            time.sleep(1.0 / SAMPLE_HZ)

    current = [0]
    sampler_thread = threading.Thread(target=sampler, daemon=True)
    sampler_thread.start()

    worker = pw.Worker()
    current[0] = worker.pid
    try:
        # Sustained mixed battery: alternating corpora thrash the single
        # slot — every request after the first misses.
        warm_seen = []
        for i in range(SUSTAINED_N):
            spec = small if i % 2 == 0 else corpus
            r = worker.run(spec, 1000 + i)
            if not r.get("ok"):
                problems.append(f"sustained request {i} failed: "
                                f"{r.get('error')}")
                break
            warm_seen.append(r["timing_ms"]["warm"])
        log("sustained_battery", n=len(warm_seen))
        if len(warm_seen) > 2 and all(warm_seen[1:]):
            problems.append("alternating corpora never missed — the "
                            "single-slot thrash case did not run")

        # Cancellation: kill mid-solve on the corpus probe (solve ~5 s;
        # kill lands inside the load/solve window).
        killed_pid = worker.pid
        result_box: dict = {}

        def send_corpus():
            try:
                result_box["response"] = worker.run(corpus, 2000,
                                                    timeout_s=300)
            except (TimeoutError, BrokenPipeError, OSError) as e:
                result_box["error"] = str(e)

        send = threading.Thread(target=send_corpus, daemon=True)
        send.start()
        time.sleep(0.4)
        log("killed", pid=killed_pid)
        worker.proc.kill()
        code = worker.proc.wait(timeout=10.0)
        log("reaped", exit=code)
        send.join(timeout=10.0)
        if send.is_alive():
            problems.append("in-flight request thread never returned")
        if not process_gone(killed_pid):
            problems.append("killed worker still exists (zombie/orphan)")
        log("reap_verified", gone=process_gone(killed_pid))
        current[0] = 0

        # Restart: fresh worker serves immediately; the re-issued corpus
        # solve must equal a never-killed cold run modulo declared fields.
        worker2 = pw.Worker()
        current[0] = worker2.pid
        try:
            r = worker2.run(small, 2001)
            if not r.get("ok"):
                problems.append("restarted worker did not serve")
            log("respawn_answered")
            cold = subprocess.run(
                [str(ACTINV), "run", str(WORK / "corpus_spec.json")],
                cwd=ROOT, capture_output=True, text=True, timeout=600)
            if cold.returncode != 0:
                problems.append(f"reference cold run failed: "
                                f"{cold.stderr[:200]}")
            else:
                r = worker2.run(corpus, 2002)
                if not r.get("ok"):
                    problems.append("re-issued corpus solve failed: "
                                    f"{r.get('error')}")
                else:
                    sys.path.insert(0, str(ROOT / "controls"))
                    import g2_p51_identity as g2  # noqa: E402
                    match = g2.strip(r["result"]) == g2.strip(
                        json.loads(cold.stdout))
                    if not match:
                        problems.append("restarted worker result != cold")
                    log("restart_identity", match=match)
        finally:
            worker2.stop()
            current[0] = 0
    finally:
        sampling = False
        sampler_thread.join(timeout=5.0)
        if worker.proc.poll() is None:
            worker.kill()

    max_rss = max(samples, default=0)
    log("rss", max_mib=round(max_rss / 2**20, 1), samples=len(samples))
    if max_rss > CEILING_BYTES:
        problems.append(f"worker RSS peak {max_rss/2**30:.2f}GiB over "
                        f"the {CEILING_BYTES/2**30:.0f}GiB ceiling")

    record = {"schema": "actinv-p51-g4-1", "events": events,
              "rss_ceiling_bytes": CEILING_BYTES,
              "problems": problems, "pass": not problems}
    OUT.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"pass": record["pass"], "problems": problems,
                      "events": events}))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
