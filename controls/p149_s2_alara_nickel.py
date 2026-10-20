#!/usr/bin/env python3
"""P149 S2: the ALARA Ni-60 and Ni-61 differences in the P17 identical-data case, one factor at a time.

`results/g2_p17_identical_data.json` has ACTINV agreeing with the dense and OpenMC solutions to about 1e-15 but
differing from ALARA 2.9.2 by 4.3e-4 (Ni-61) and 3.9e-5 (Ni-60), above ALARA's five-significant-figure output.
Ni-61 is reached only through Co-59(n,g)Co-60(n,g)Co-61, beta decay, half-life 1.65 h, comparable to the 2-h gaps.
Each variant reruns `g2_p17_identical_data.main()` with one change, applied to every solver alike:
  baseline        the committed case;
  no_cobalt       Co-59 removed from the initial material and the ALARA mixture (the other ALARA densities scaled
                  by 3/4, since ALARA renormalizes volume fractions);
  continuous_24h  one continuous 24-h irradiation in place of four 6-h pulses with 2-h gaps;
  truncation      ALARA `truncation 1e-30` in place of 1e-24 (ALARA only).
A full-precision decay-library variant is not run: both codes read the same FENDL-2 decay text, and ALARA's
`convert_lib` stores half-lives, energies and cross sections as 32-bit floats (src/DataLib/ALARALib.C), so no
converter option writes more digits. The record says so.

Usage: p149_s2_alara_nickel.py [--out results/p149/s2_alara_nickel.json] [--only VARIANT ...]
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import g2_p17_identical_data as g2  # noqa: E402

ORIGINAL = {"SCHEDULE": list(g2.SCHEDULE), "INITIAL_KEYS": list(g2.INITIAL_KEYS)}
ORIGINAL_COMMAND = g2.command
WATCH = ("Ni60", "Ni61", "Co60", "Co61", "Ni59", "Co58")


def alara_edit(variant: str):
    """Rewrite ALARA's run input for the variant just before ALARA reads it."""
    def edit(text: str) -> str:
        if variant == "no_cobalt":
            # ALARA renormalizes the mixture's volume fractions to sum to one, so with Co deleted the other three
            # targets would start 4/3 higher; their densities are scaled by 3/4 to keep 2.5e19 atoms each, which
            # the P17 initial-population check verifies.
            lines = []
            for line in text.splitlines():
                if line.strip().startswith("element co:p17"):
                    continue
                if line.strip().startswith("element ") and ":p17" in line:
                    word = line.split()
                    word[2] = f"{float(word[2]) * 0.75:.17e}"
                    line = "    " + " ".join(word)
                lines.append(line)
            text = "\n".join(lines) + "\n"
        elif variant == "continuous_24h":
            text = text.replace("    6 h flux_1 pulsed 0 s", "    24 h flux_1 steady 0 s")
            text = text.replace("pulsehistory pulsed\n    4 2 h\nend", "pulsehistory steady\n    1 0 s\nend")
        elif variant == "truncation":
            text = text.replace("truncation 1e-24", "truncation 1e-30")
        return text

    def command(argv, *args, **kwargs):
        if len(argv) == 2 and Path(str(argv[0])) == g2.ALARA_BIN and str(argv[1]) == "p17-identical-run":
            path = Path(kwargs.get("cwd", ".")) / str(argv[1])
            before = path.read_text()
            after = edit(before)
            if variant != "baseline" and after == before:
                raise RuntimeError(f"variant {variant}: the ALARA input did not change")
            path.write_text(after)
        return ORIGINAL_COMMAND(argv, *args, **kwargs)

    return command


def run_variant(variant: str, work: Path) -> dict:
    g2.SCHEDULE = [(86400.0, 1.0)] if variant == "continuous_24h" else list(ORIGINAL["SCHEDULE"])
    g2.INITIAL_KEYS = ([key for key in ORIGINAL["INITIAL_KEYS"] if key != (27059, 0)]
                       if variant == "no_cobalt" else list(ORIGINAL["INITIAL_KEYS"]))
    g2.command = alara_edit(variant)
    g2.RESULTS = work / "results"
    import os
    os.environ["ACTINV_P17_WORK"] = str(work)
    try:
        g2.main()
    except SystemExit:
        pass
    finally:
        g2.command = ORIGINAL_COMMAND
    record = json.loads((g2.RESULTS / "g2_p17_identical_data.json").read_text())
    rows = record["comparisons"]
    return {
        "variant": variant,
        "schedule": record["schedule"],
        "initial": record["features"]["initial_target_families"],
        "alara_run": {k: record["alara_run"][k] for k in ("conversion_returncode", "run_returncode",
                                                           "run_input_sha256", "four_pulses")},
        "maximum_actinv_alara_relative_reportable": record["maximum_actinv_alara_relative_reportable"],
        "maximum_actinv_dense_relative_reportable": record["maximum_actinv_cli_dense_relative_reportable"],
        "worst_alara": sorted(((row["actinv_alara_relative"], name) for name, row in rows.items()
                               if row["reportable"]), reverse=True)[:5],
        "nuclides": {name: {k: rows[name][k] for k in ("actinv", "alara", "dense", "openmc", "actinv_alara_relative")}
                     | {"actinv_dense_relative": rows[name]["actinv_dense"]["relative"]}
                     for name in WATCH if name in rows},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "results/p149/s2_alara_nickel.json")
    ap.add_argument("--only", nargs="*", default=["baseline", "no_cobalt", "continuous_24h", "truncation"])
    args = ap.parse_args()
    variants = []
    with tempfile.TemporaryDirectory(prefix="p149-s2-", dir=str(ROOT / "target")) as directory:
        for variant in args.only:
            work = Path(directory) / variant
            work.mkdir()
            variants.append(run_variant(variant, work))
            print(f"{variant}: max ACTINV/ALARA {variants[-1]['maximum_actinv_alara_relative_reportable']:.3e}",
                  file=sys.stderr, flush=True)
    record = {
        "schema": "actinv-p149-s2-alara-nickel-1",
        "protocol": "P149 S2",
        "alara_binary": str(g2.ALARA_BIN),
        "not_run": {"full_precision_decay": "ALARA convert_lib stores half-lives, energies and cross sections as "
                                            "32-bit floats; both codes already read the same FENDL-2 decay text"},
        "variants": variants,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    print(json.dumps({v["variant"]: {n: v["nuclides"].get(n, {}).get("actinv_alara_relative") for n in ("Ni60", "Ni61")}
                      for v in variants}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
