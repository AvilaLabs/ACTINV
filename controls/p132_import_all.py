#!/usr/bin/env python3
"""P132a: import every FISPACT-II deck under a directory with the release binary and summarize the
import-report statuses per keyword.

Usage: p132_import_all.py [FNS_ROOT] [--out DIR] [--strict]

Each `FNS_ROOT/*/*.i` deck is converted with `actinv import-fispact`. The deck's `<stem>_files`
file beside it supplies the group spectrum. `ACTINV_LIBRARY` / `ACTINV_ENDF_DECAY` /
`ACTINV_COVARIANCE` (when set) are passed as --library / --decay / --covariance; otherwise the
importer uses the catalog references (a custom library needs its own covariance sidecar, or the
decks' UNCERTAINTY keyword is reported unsupported). The binary is
`ACTINV_BIN`, else `target/release/actinv`. Nothing here runs the converted problems.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FNS = Path(
    os.environ.get("ACTINV_FNS_ROOT", Path.home() / "nuclear-data/conderc-fns/fns")
)
STATUSES = ("mapped", "approximated", "noted", "unsupported")


def binary() -> Path:
    path = Path(os.environ.get("ACTINV_BIN", ROOT / "target/release/actinv"))
    if not path.is_file():
        raise SystemExit(
            f"build the release binary first: cargo build --release -p actinv-cli ({path})"
        )
    return path


def import_deck(exe: Path, deck: Path, out: Path, strict: bool) -> tuple[bool, str]:
    command = [str(exe), "import-fispact", str(deck), str(out)]
    if os.environ.get("ACTINV_LIBRARY"):
        command += ["--library", os.environ["ACTINV_LIBRARY"]]
    if os.environ.get("ACTINV_ENDF_DECAY"):
        command += ["--decay", os.environ["ACTINV_ENDF_DECAY"]]
    if os.environ.get("ACTINV_COVARIANCE"):
        command += ["--covariance", os.environ["ACTINV_COVARIANCE"]]
    if strict:
        command.append("--strict")
    done = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
    message = (done.stdout + done.stderr).strip().splitlines()
    return done.returncode == 0, message[-1] if message else ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", nargs="?", type=Path, default=DEFAULT_FNS)
    parser.add_argument("--out", type=Path, default=Path("/tmp/p132-imports"))
    parser.add_argument(
        "--strict",
        action="store_true",
        help="count a deck as failed when any keyword is unsupported",
    )
    args = parser.parse_args()

    exe = binary()
    decks = sorted(args.root.glob("*/*.i"))
    if not decks:
        raise SystemExit(f"no */*.i decks under {args.root}")
    args.out.mkdir(parents=True, exist_ok=True)

    failures: list[tuple[str, str]] = []
    by_keyword: dict[str, Counter] = defaultdict(Counter)
    totals: Counter = Counter()
    for deck in decks:
        out = args.out / f"{deck.parent.name}__{deck.stem}.json"
        ok, message = import_deck(exe, deck, out, args.strict)
        report_path = out.with_name(out.stem + ".import.json")
        if report_path.is_file():
            report = json.loads(report_path.read_text())
            for entry in report["entries"]:
                by_keyword[entry["keyword"]][entry["status"]] += 1
                totals[entry["status"]] += 1
        if not ok:
            failures.append((f"{deck.parent.name}/{deck.name}", message))

    print(f"{len(decks)} decks, {len(decks) - len(failures)} imported, {len(failures)} failed")
    print("entry statuses: " + ", ".join(f"{s}={totals[s]}" for s in STATUSES))
    print(f"{'keyword':<14}" + "".join(f"{s:>14}" for s in STATUSES))
    for keyword in sorted(by_keyword):
        counts = by_keyword[keyword]
        print(f"{keyword:<14}" + "".join(f"{counts[s]:>14}" for s in STATUSES))
    for name, message in failures:
        print(f"FAILED {name}: {message}", file=sys.stderr)
    (args.out / "summary.json").write_text(
        json.dumps(
            {
                "decks": len(decks),
                "failed": [{"deck": name, "message": message} for name, message in failures],
                "totals": {s: totals[s] for s in STATUSES},
                "by_keyword": {k: dict(v) for k, v in sorted(by_keyword.items())},
            },
            indent=2,
        )
        + "\n"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
