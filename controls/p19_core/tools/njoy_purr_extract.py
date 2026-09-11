#!/usr/bin/env python3
"""P19 oracle driver: run NJOY PURR over the declared test set and extract MT=152.

Arguments (exact positional argv bound by the adapter):
  materials-manifest  JSON [{"material","eval_path"}...] — eval_path resolved
                      under the supplied nuclear-data source root.
  njoy-executable     Path to a digest-pinned NJOY binary.
  eval slots          One staged ENDF-6 file per declared material.
  groups              JSON group-structure descriptor (recorded, unused by PURR).
  output              Workspace path for `purr_mt152.json`.

The driver writes one NJOY input deck per material (reconr, broadr at each
declared temperature, purr), executes NJOY with a bounded timeout in a private
step directory, then parses the emitted PENDF MT=152 Bondarenko cross sections
into the declared JSON layout. It interprets nothing beyond record extraction.
"""
import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# Frozen grid shared with the P19 protocol.
SIG0 = [1.0e10, 1.0e5, 1.0e4, 1.0e3, 1.0e2, 1.0e1, 3.0, 1.0, 0.3, 0.1]
TEMPS = [293.6, 600.0, 900.0, 1200.0]
NLADR = 64
NJOY_TIMEOUT_S = 2400

ENDF_FLOAT = re.compile(r"^([+-]?\d+\.?\d*)([+-]\d+)$")


def endf_field(line, start, stop):
    text = line[start:stop].strip()
    if not text:
        return 0.0
    if "e" not in text.lower():
        text = ENDF_FLOAT.sub(r"\1e\2", text)
    return float(text)


def read_mat(eval_path):
    with open(eval_path, errors="replace") as handle:
        handle.readline()  # TPID line carries a version field, not MAT
        line = handle.readline()
    return int(line[66:70])


def write_deck(path, mat, label):
    temps = " ".join(f"{t:g}" for t in TEMPS)
    # reconr + broadr at the first temperature, then purr with all
    # temperatures on one card. PURR internally loops sigma0 x temperature.
    ntemp = len(TEMPS)
    nsig = len(SIG0)
    deck = f"""reconr
20 21
'PENDF {label}'/
{mat} 2/
0.001 0.0 0.002/
'{label}'/
'p19 oracle campaign'/
0/
broadr
20 21 22
{mat} {ntemp} 0 0 0./
0.001 0.0 0.002/
{temps}
0/
purr
20 22 23
{mat} {ntemp} {nsig} 20 {NLADR}/
{temps}
{' '.join(f'{s:.5E}' for s in SIG0)}
0/
stop
"""
    path.write_text(deck)


CHANNELS = ("total", "elastic", "fission", "capture", "heating")


def exactify(value):
    """Authoritative JSON: floats become exact decimal strings; ints stay."""
    from decimal import Decimal

    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return format(Decimal(repr(value)), "f")
    if isinstance(value, dict):
        return {k: exactify(v) for k, v in value.items()}
    if isinstance(value, list):
        return [exactify(v) for v in value]
    return value


def parse_mt152(pendf):
    """Extract NJOY PENDF MT=152 Bondarenko tables.

    Layout (from njoy2016 purr.f90): a section HEAD [ZA,AWR,LSSF,0,0,INTUNR],
    then per temperature a CONT [T,0,5,NSIGZ,NW,NUNX] followed by a LIST of NW
    words: NSIGZ sigma0 values, then NUNX blocks of [energy, 5 x NSIGZ cross
    sections] where the five channels are total, elastic, fission, capture,
    heating — the shielded cross sections at each (energy, sigma0).
    """
    with open(pendf, errors="replace") as handle:
        lines = handle.readlines()

    def fields(line):
        return [endf_field(line, k * 11, (k + 1) * 11) for k in range(6)]

    def is_record(line, mf, mt):
        try:
            return int(line[70:72]) == mf and int(line[72:75]) == mt
        except ValueError:
            return False

    rows = []
    i = 0
    while i < len(lines):
        if not is_record(lines[i], 2, 152):
            i += 1
            continue
        # section HEAD: [ZA, AWR, LSSF, 0, 0, INTUNR]
        head = fields(lines[i])
        section = {"za": head[0], "awr": head[1], "lssf": int(head[2]),
                   "intunr": int(head[5]), "temperatures": []}
        i += 1
        # per-temperature CONT + LIST blocks until SEND/other section
        while i < len(lines) and is_record(lines[i], 2, 152):
            cont = fields(lines[i])
            temp, nsigz = cont[0], int(cont[3])
            nw, nunx = int(cont[4]), int(cont[5])
            i += 1
            values = []
            while len(values) < nw and i < len(lines):
                if not is_record(lines[i], 2, 152):
                    break
                values.extend(fields(lines[i]))
                i += 1
            values = values[:nw]
            sigma0 = values[:nsigz]
            block = {"T": temp, "sigma0": sigma0, "energies": []}
            pos = nsigz
            for _ in range(nunx):
                energy = values[pos]
                pos += 1
                entry = {"E": energy}
                for ch in CHANNELS:
                    entry[ch] = values[pos : pos + nsigz]
                    pos += nsigz
                block["energies"].append(entry)
            section["temperatures"].append(block)
        rows.append(section)
    return rows


def main() -> int:
    manifest = json.loads(Path(sys.argv[1]).read_text())
    njoy = str(Path(sys.argv[2]).resolve())
    groups_path = sys.argv[-2]
    output = Path(sys.argv[-1])
    evals = sys.argv[3:-2]
    by_name = {}
    for path in evals:
        name = Path(path).name
        by_name[name] = path
    materials = manifest["materials"]
    missing = [m["file"] for m in materials if m["file"] not in by_name]
    if missing:
        print(f"missing staged evals: {missing}", file=sys.stderr)
        return 2
    import hashlib
    groups_sha = hashlib.sha256(Path(groups_path).read_bytes()).hexdigest()
    result = {"schema": "avila.actinv/purr-mt152/v1", "sigma0_b": SIG0,
              "temperatures_K": TEMPS, "nladr": NLADR,
              "groups_descriptor_sha256": groups_sha, "materials": {}}
    with tempfile.TemporaryDirectory(prefix="p19-njoy-") as work:
        workdir = Path(work)
        for entry in materials:
            name, label = entry["file"], entry["material"]
            eval_path = by_name[name]
            mat = read_mat(eval_path)
            stepdir = workdir / label
            stepdir.mkdir()
            (stepdir / "tape20").write_bytes(Path(eval_path).read_bytes())
            write_deck(stepdir / "deck.nji", mat, f"{label} TENDL-2025")
            print(f"[purr] {label} (mat {mat}) start", file=sys.stderr, flush=True)
            proc = subprocess.run(
                [njoy],
                cwd=stepdir,
                stdin=open(stepdir / "deck.nji"),
                stdout=open(stepdir / "out.txt", "w"),
                stderr=subprocess.PIPE,
                timeout=NJOY_TIMEOUT_S,
                check=False,
            )
            print(f"[purr] {label} rc={proc.returncode}", file=sys.stderr, flush=True)
            if proc.returncode != 0:
                print(f"njoy failed for {label}: rc={proc.returncode}", file=sys.stderr)
                return proc.returncode or 3
            pendf = stepdir / "tape23"
            if not pendf.is_file():
                print(f"njoy produced no PENDF for {label}", file=sys.stderr)
                return 4
            rows = parse_mt152(pendf)
            if not rows:
                print(f"no MT=152 in PENDF for {label}", file=sys.stderr)
                return 5
            result["materials"][label] = {"mat": mat, "mt152_sections": rows}
    total_cells = sum(
        len(e)
        for m in result["materials"].values()
        for s in m["mt152_sections"]
        for t in s["temperatures"]
        for e in [t["energies"]]
    )
    result["row_count"] = total_cells
    # NJOY emits one MT=152 section per temperature; each carries a single
    # CONT+LIST block for that temperature.
    def material_complete(m):
        blocks = [t for s in m["mt152_sections"] for t in s["temperatures"]]
        return (
            len(blocks) == len(TEMPS)
            and all(
                len(t["sigma0"]) == len(SIG0) and len(t["energies"]) > 0
                for t in blocks
            )
        )
    result["status"] = (
        "complete"
        if len(result["materials"]) == len(manifest["materials"])
        and all(material_complete(m) for m in result["materials"].values())
        else "partial"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(exactify(result), indent=1, sort_keys=True) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
