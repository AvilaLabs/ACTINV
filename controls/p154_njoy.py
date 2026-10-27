#!/usr/bin/env python3
"""P154 NJOY reference driver (protocols/ACTINV-P154_PROTOCOL.md): damage energy production from NJOY2016.

Runs RECONR (0 K, no BROADR) and HEATR with the partial damage-energy MTs 444 (total), 445 (elastic), 446 (inelastic,
MT51-91) and 447 (disappearance, MT102-120 and 600-849) at a stated displacement energy `ed` (HEATR card 2, the
variable `break` in heatr.f90: recoil energies below it give zero damage), and reads the pointwise MF=3 sections of
the PENDF back with this script's own reader. `collapse` integrates a pointwise table onto group boundaries with the
flat-in-lethargy weight, sum over segments of the integral of y dE/E divided by ln(hi/lo), which is how the ACTINV
builders collapse (specification.md: "the flat-lethargy weighting of the other builders").

Deck (tape20 is the ASCII evaluation; negative units are NJOY binary):

    moder / 20 -21 /
    reconr / -21 -22 / 'title'/ MAT 1 0 / ERR / 'card'/ 0 /
    heatr / -21 -22 -23 / MAT 4 0 0 0 0 ED / 444 445 446 447 /
    moder / -23 24 /
    stop

Usage: p154_njoy.py --endf FILE --workdir DIR --ed EV [--err 0.001] [--njoy PATH] [--timeout 3600] [--reuse]
Run inside the required systemd cgroup. Exit 0 when the PENDF carries MT 444-447, 2 otherwise.
"""
import argparse
import bisect
import json
import math
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import p126_njoy_fe56 as p126  # noqa: E402  (own ENDF-6 reader and tape helpers from the P126 reference driver)

DEFAULT_NJOY = "~/nuclear-data/njoy2016.79-build/njoy"
DAMAGE_MTS = (444, 445, 446, 447)
RECONR_ERR = 0.001


def njoy_deck(mat, ed, err=RECONR_ERR, title="TENDL-2025 pointwise for ACTINV P154"):
    """The input deck text for one material (see the module docstring)."""
    lines = [
        "moder",
        " 20 -21/",
        "reconr",
        " -21 -22/",
        f" '{title}'/",
        f" {mat} 1 0/",
        f" {err!r}/",
        " 'processed for the ACTINV P154 damage-energy check'/",
        " 0/",
        "heatr",
        " -21 -22 -23/",
        f" {mat} {len(DAMAGE_MTS)} 0 0 0 0 {float(ed)!r}/",
        " " + " ".join(str(m) for m in DAMAGE_MTS) + "/",
        "moder",
        " -23 24/",
        "stop",
    ]
    return "\n".join(lines) + "\n"


def run_njoy(endf, workdir, ed, njoy=DEFAULT_NJOY, err=RECONR_ERR, timeout=3600, reuse=False):
    """Run the deck in `workdir`. Returns a record dict; record['pendf'] is the ASCII PENDF path on success."""
    workdir = Path(workdir).expanduser().resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    njoy = Path(njoy).expanduser().resolve()
    endf = Path(endf).expanduser().resolve()
    record = {"njoy": {"path": str(njoy)}, "endf": {"path": str(endf)}, "ed_eV": float(ed), "reconr_err": err}
    if not njoy.is_file():
        record["error"] = f"njoy executable not found: {njoy}"
        return record
    if not endf.is_file():
        record["error"] = f"evaluation not found: {endf}"
        return record
    record["njoy"]["sha256"] = p126.sha256_file(njoy)
    record["endf"]["sha256"] = p126.sha256_file(endf)
    mat, za, awr = p126.endf_mat_and_za(endf)
    record["evaluation"] = {"mat": mat, "za": za, "awr": awr}
    deck = njoy_deck(mat, ed, err)
    (workdir / "input").write_text(deck)
    record["deck"] = deck
    pendf = workdir / "tape24"
    if not (reuse and pendf.exists()):
        for name in ("tape20", "tape21", "tape22", "tape23", "tape24"):
            stale = workdir / name
            if stale.exists() or stale.is_symlink():
                stale.unlink()
        (workdir / "tape20").write_bytes(endf.read_bytes())
        code, error = p126.run([njoy], workdir, timeout, stdin_path=workdir / "input",
                               stdout_path=workdir / "output", stderr_path=workdir / "njoy.stderr")
        record["run"] = {"exit_code": code, "error": error}
        if code != 0 or not pendf.exists():
            record["error"] = error or f"njoy exit code {code}; tape24 written: {pendf.exists()}"
            record["output_tail"] = p126.tail(workdir / "output")
            record["stderr_tail"] = p126.tail(workdir / "njoy.stderr")
            return record
    record["pendf"] = str(pendf)
    record["pendf_sha256"] = p126.sha256_file(pendf)
    output = workdir / "output"
    if output.exists():
        record["output_sha256"] = p126.sha256_file(output)
        record["version"] = njoy_version(output)
    return record


def njoy_version(output_path):
    """The `NJOY2016.NN` / `njoy 2016.NN` banner of an NJOY output file, or None."""
    try:
        with open(output_path, "r", errors="replace") as handle:
            for _ in range(200):
                line = handle.readline()
                if not line:
                    break
                match = re.search(r"njoy\s*2016\.?\s*(\d+)", line, re.IGNORECASE)
                if match:
                    return line.strip()
    except OSError:
        pass
    return None


def read_pendf(path, mts=DAMAGE_MTS):
    """MF=3 sections of a PENDF: {mt: {"x": [...], "y": [...], "ranges": [(nbt, int)]}} (first material only)."""
    found, seen_mat = {}, None
    for mat, mf, mt, lines in p126.sections(path):
        if seen_mat is None:
            seen_mat = mat
        if mat != seen_mat or mf != 3 or mt not in mts:
            continue
        found[mt] = parse_tab1(lines)
    return found


def parse_tab1(lines):
    """HEAD record on line 0, TAB1 record on line 1: (QM, QI, 0, LR, NR, NP), NR (NBT, INT) pairs, NP (x, y) pairs.
    The table also carries the record's `qm` and `qi` (HEATR's `q` for a discrete level is QI)."""
    qm, qi, _l1, _lr, nr, npts = p126.record(lines, 1)
    words, index = p126.data_words(lines, 2, 2 * nr)
    ranges = [(p126.endf_integer(words[2 * k]), p126.endf_integer(words[2 * k + 1])) for k in range(nr)]
    words, _ = p126.data_words(lines, index, 2 * npts)
    x = [p126.endf_number(words[2 * k]) for k in range(npts)]
    y = [p126.endf_number(words[2 * k + 1]) for k in range(npts)]
    if ranges and ranges[-1][0] != npts:
        raise ValueError(f"TAB1 ranges end at point {ranges[-1][0]} but NP = {npts}")
    return {"x": x, "y": y, "ranges": ranges, "qm": qm, "qi": qi}


def segment_integral(law, x1, x2, y1, y2, a, b):
    """Integral over [a, b] inside [x1, x2] of the interpolated y(E) dE/E under ENDF law INT = 1, 2, 3, 5."""
    r = (b - a) / a
    lr = math.log1p(r)
    if law == 1:
        return y1 * lr
    if law == 2:
        slope = (y2 - y1) / (x2 - x1)
        return (y1 + slope * (a - x1)) * lr + slope * a * (r - lr)
    if law == 3:
        span = math.log(x2 / x1)
        ya = y1 + math.log(a / x1) / span * (y2 - y1)
        yb = y1 + math.log(b / x1) / span * (y2 - y1)
        return 0.5 * (ya + yb) * lr
    if law == 5:
        if y1 <= 0.0 or y2 <= 0.0:
            return 0.5 * (y1 + y2) * lr if (y1 == y2) else _lin_fallback(x1, x2, y1, y2, a, b)
        power = math.log(y2 / y1) / math.log(x2 / x1)
        ya = y1 * math.exp(power * math.log(a / x1))
        if abs(power) < 1e-12:
            return ya * lr
        return ya * ((b / a) ** power - 1.0) / power
    raise ValueError(f"unsupported ENDF interpolation law INT={law}")


def _lin_fallback(x1, x2, y1, y2, a, b):
    return segment_integral(2, x1, x2, y1, y2, a, b)


def law_of_segment(ranges, i):
    """INT for the segment between 0-based points i and i+1 (NBT counts points 1-based)."""
    for nbt, law in ranges:
        if i + 2 <= nbt:
            return law
    return ranges[-1][1]


def collapse(table, boundaries):
    """Flat-in-lethargy group values of a pointwise table; zero outside its support.

    `boundaries` may be ascending or descending; the result follows the boundary order (one value per pair)."""
    x, y, ranges = table["x"], table["y"], table["ranges"]
    pairs = [(min(b0, b1), max(b0, b1)) for b0, b1 in zip(boundaries[:-1], boundaries[1:])]
    order = sorted(range(len(pairs)), key=lambda k: pairs[k][0])
    los = [pairs[k][0] for k in order]
    sums = [0.0] * len(pairs)
    for i in range(len(x) - 1):
        x1, x2 = x[i], x[i + 1]
        if not x2 > x1 or x1 <= 0.0:
            continue  # zero-width step (a discontinuity) or a point at zero energy carries no lethargy width
        law = law_of_segment(ranges, i)
        # first group whose upper edge is above x1
        k = max(bisect.bisect_right(los, x1) - 1, 0)
        while k < len(order):
            lo, hi = pairs[order[k]]
            if lo >= x2:
                break
            a, b = max(lo, x1), min(hi, x2)
            if b > a:
                sums[order[k]] += segment_integral(law, x1, x2, y[i], y[i + 1], a, b)
            k += 1
    return [s / math.log(hi / lo) for s, (lo, hi) in zip(sums, pairs)]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--endf", required=True)
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--ed", type=float, required=True, help="displacement energy in eV (HEATR card 2)")
    ap.add_argument("--err", type=float, default=RECONR_ERR)
    ap.add_argument("--njoy", default=DEFAULT_NJOY)
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--reuse", action="store_true")
    args = ap.parse_args(argv)
    record = run_njoy(args.endf, args.workdir, args.ed, args.njoy, args.err, args.timeout, args.reuse)
    if "pendf" in record:
        tabs = read_pendf(record["pendf"])
        record["mf3_sections"] = {str(mt): len(t["x"]) for mt, t in sorted(tabs.items())}
        if set(tabs) != set(DAMAGE_MTS):
            record["error"] = f"PENDF carries MT {sorted(tabs)}, expected {list(DAMAGE_MTS)}"
    print(json.dumps({k: v for k, v in record.items() if k != "deck"}, indent=1, sort_keys=True))
    return 0 if "error" not in record else 2


if __name__ == "__main__":
    sys.exit(main())
