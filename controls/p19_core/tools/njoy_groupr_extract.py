#!/usr/bin/env python3
"""P19 G3 oracle driver: run NJOY GROUPR on PURR-processed PENDFs and extract
the FISPACT-709 GENDF MF=3 group cross sections.

Arguments (exact positional argv bound by the adapter):
  materials-manifest  JSON [{"material","file"}...] — eval files staged in the
                      step directory alongside tape20.
  njoy-executable     Path to a digest-pinned NJOY binary.
  eval slots          One staged ENDF-6 file per declared material.
  groups              JSON group-structure descriptor (the 709 boundaries are
                      read from it and passed to groupr ign=1).
  output              Workspace path for `groupr_gendf.json`.

Per material the deck is reconr -> broadr -> purr -> groupr: the PURR PENDF
carries the unresolved-resonance probability tables GROUPR needs. GROUPR runs
ign=1 (read-in FISPACT-709 structure, ascending), iwt=3 (1/E lethargy weight,
matching ACTINV's collapse convention), lord=0, at the frozen sigma0/temperature
grid. The reaction list repeats once per temperature (GROUPR card9 semantics),
then a matd=0 card ends the run.

The emitted JSON records, per material and MF=3 MT section, the GENDF LIST
payloads: for each (temperature, group) the nsigz flux values and nsigz cross
sections on the declared sigma0 grid. It interprets nothing beyond record
extraction.
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SIG0 = [1.0e10, 1.0e5, 1.0e4, 1.0e3, 1.0e2, 1.0e1, 3.0, 1.0, 0.3, 0.1]
TEMPS = [293.6, 600.0, 900.0, 1200.0]
NLADR = 64
NJOY_TIMEOUT_S = 2400
# Channels compared against the artifact: total, elastic, fission, capture.
MTS_OF_INTEREST = (1, 2, 18, 102)

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


def mf3_mts(eval_path):
    """MF=3 section list (the ENDF directory row) -> MTs present."""
    mts = set()
    with open(eval_path, errors="replace") as handle:
        for line in handle:
            try:
                if int(line[70:72]) == 3:
                    mts.add(int(line[72:75]))
            except ValueError:
                continue
    return sorted(mts)


def write_deck(path, mat, label, mts, bounds):
    temps = " ".join(f"{t:g}" for t in TEMPS)
    ntemp, nsig = len(TEMPS), len(SIG0)
    bnd = "\n".join(
        " ".join(f"{v:.6E}" for v in bounds[i : i + 6])
        for i in range(0, len(bounds), 6)
    )
    sigz = " ".join(f"{s:.5E}" for s in SIG0)
    reactions = "".join(f"3 {mt}/\n" for mt in mts) + "0/\n"
    deck = f"""reconr
20 21
'PENDF {label}'/
{mat} 2/
0.001 0.0 0.002/
'{label}'/
'p19 g3 groupr oracle'/
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
{sigz}
0/
groupr
20 23 0 31 /
{mat} 1 0 3 0 {ntemp} {nsig} 1 0/
'{label} groupr'/
{temps}
{sigz}
   {len(bounds) - 1}
{bnd}
{reactions * ntemp}0/
stop
"""
    path.write_text(deck)


def parse_gendf(gendf, wanted):
    """Extract MF=3 group constants per (MT, temperature, group).

    GENDF MF=3 layout (njoy2016 groupr.f90, the write at ~line 884): section
    HEAD [ZA, AWR, L1, NZ, 0, NGN]; then NGN x NTEMP records, each a CONT
    [T, 0, NG2, IG2LO, NW=NL*NZ*NG2, IG] followed by a LIST of NW values
    packed sub-block-major: for it in 1..NG2, for iz in 1..NZ, for il in
    1..NL. With the Bondarenko flux model and nsigz>1, NL=2: per genflx,
    il=1 is the Bondarenko flux phi*sigma0/(sigma0+sigma_t) and il=2 is the
    transport-corrected component, deeper-suppressed. NG2=2 for MF=3 cross
    sections: it=1 is the group flux block, it=2 the cross-section block.
    Groups are 1-based ascending in energy.
    """
    with open(gendf, errors="replace") as handle:
        lines = handle.readlines()

    def fields(line):
        return [endf_field(line, k * 11, (k + 1) * 11) for k in range(6)]

    def is_record(line, mf, mt):
        try:
            return int(line[70:72]) == mf and int(line[72:75]) == mt
        except ValueError:
            return False

    sections = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        try:
            mf, mt = int(line[70:72]), int(line[72:75])
        except ValueError:
            i += 1
            continue
        if mf != 3 or mt not in wanted:
            i += 1
            continue
        # Section HEAD carries NZ (the declared sigma0 count).
        head = fields(line)
        nz = int(head[3])
        i += 1
        records = {}
        while i < len(lines) and is_record(lines[i], 3, mt):
            cont = fields(lines[i])
            temp, ng2, nw, group = cont[0], int(cont[2]), int(cont[4]), int(cont[5])
            if nw <= 0 or group <= 0:
                i += 1
                continue
            i += 1
            values = []
            while len(values) < nw and i < len(lines):
                if not is_record(lines[i], 3, mt):
                    break
                values.extend(fields(lines[i]))
                i += 1
            values = values[:nw]
            if nz <= 0 or ng2 < 1 or nw < nz * ng2:
                continue
            nl = nw // (nz * ng2)
            if nl < 1:
                continue
            # sub-block it: values[it*nz*nl + iz*nl + il]. Per genflx,
            # il=1 is the Bondarenko flux phi*sigma0/(sigma0+sigma_t) — the
            # weighting whose xs ratio is the self-shielded constant — and
            # il=2 is the transport-corrected component
            # phi*w*(sigma_pot+sigma0)/(sigma_t+sigma0), deeper-suppressed
            # and used only for the elastic transport correction.
            flux = [values[iz * nl] for iz in range(nz)]
            xs_sh = [values[nz * nl + iz * nl] for iz in range(nz)]
            w_flux = (
                [values[iz * nl + 1] for iz in range(nz)] if nl >= 2 else list(flux)
            )
            xs_tr = (
                [values[nz * nl + iz * nl + 1] for iz in range(nz)]
                if nl >= 2 else list(xs_sh)
            )
            records.setdefault(temp, {})[group] = {
                "flux": flux,
                "transport_flux": w_flux,
                "xs_b": xs_sh,
                "xs_transport_b": xs_tr,
            }
        # GROUPR emits one MF=3 section per temperature; merge them.
        if records:
            sections.setdefault(mt, {}).update(records)
    return sections


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


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--parse-only":
        # Regenerate the JSON from persisted GENDF tapes without rerunning NJOY:
        # --parse-only GENDF_DIR GROUPS_JSON MATERIALS_JSON OUT_JSON
        gendf_dir = Path(sys.argv[2])
        groups_path = sys.argv[3]
        manifest = json.loads(Path(sys.argv[4]).read_text())
        output = Path(sys.argv[5])
        import hashlib
        groups_doc = json.loads(Path(groups_path).read_text())
        groups_sha = hashlib.sha256(Path(groups_path).read_bytes()).hexdigest()
        bounds = sorted(
            float(v) for v in groups_doc.get("boundaries_eV", groups_doc.get("boundaries", []))
        )
        result = {
            "schema": "avila.actinv/groupr-gendf/v1",
            "sigma0_b": SIG0,
            "temperatures_K": TEMPS,
            "nladr": NLADR,
            "iwt": 3,
            "groups_descriptor_sha256": groups_sha,
            "group_count": len(bounds) - 1,
            "materials": {},
        }
        for entry in manifest["materials"]:
            label = entry["material"]
            tape = gendf_dir / f"{label}.gendf"
            if not tape.is_file():
                print(f"missing persisted GENDF for {label}", file=sys.stderr)
                return 2
            eval_path = None
            mts = list(MTS_OF_INTEREST)
            sections = parse_gendf(tape, set(mts))
            if not sections:
                print(f"no MF=3 sections parsed for {label}", file=sys.stderr)
                return 5
            result["materials"][label] = {"mat": entry.get("mat"), "mf3_sections": {
                str(mt): recs for mt, recs in sections.items()
            }}
        result["material_count"] = len(result["materials"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(exactify(result), indent=1, sort_keys=True) + "\n")
        return 0
    manifest = json.loads(Path(sys.argv[1]).read_text())
    njoy = str(Path(sys.argv[2]).resolve())
    groups_path = sys.argv[-2]
    output = Path(sys.argv[-1])
    evals = sys.argv[3:-2]
    by_name = {Path(p).name: p for p in evals}
    materials = manifest["materials"]
    missing = [m["file"] for m in materials if m["file"] not in by_name]
    if missing:
        print(f"missing staged evals: {missing}", file=sys.stderr)
        return 2
    import hashlib

    groups_doc = json.loads(Path(groups_path).read_text())
    groups_sha = hashlib.sha256(Path(groups_path).read_bytes()).hexdigest()
    bounds = sorted(
        float(v) for v in groups_doc.get("boundaries_eV", groups_doc.get("boundaries", []))
    )
    if len(bounds) < 2:
        print("group descriptor carries no boundaries", file=sys.stderr)
        return 2
    result = {
        "schema": "avila.actinv/groupr-gendf/v1",
        "sigma0_b": SIG0,
        "temperatures_K": TEMPS,
        "nladr": NLADR,
        "iwt": 3,
        "groups_descriptor_sha256": groups_sha,
        "group_count": len(bounds) - 1,
        "materials": {},
    }
    with tempfile.TemporaryDirectory(prefix="p19-groupr-") as work:
        workdir = Path(work)
        for entry in materials:
            name, label = entry["file"], entry["material"]
            eval_path = by_name[name]
            mat = read_mat(eval_path)
            mts = [mt for mt in MTS_OF_INTEREST if mt in mf3_mts(eval_path)]
            stepdir = workdir / label
            stepdir.mkdir()
            (stepdir / "tape20").write_bytes(Path(eval_path).read_bytes())
            write_deck(stepdir / "deck.nji", mat, f"{label} TENDL-2025", mts, bounds)
            print(f"[groupr] {label} (mat {mat}, mts {mts}) start",
                  file=sys.stderr, flush=True)
            proc = subprocess.run(
                [njoy],
                cwd=stepdir,
                stdin=open(stepdir / "deck.nji"),
                stdout=open(stepdir / "out.txt", "w"),
                stderr=subprocess.PIPE,
                timeout=NJOY_TIMEOUT_S,
                check=False,
            )
            print(f"[groupr] {label} rc={proc.returncode}", file=sys.stderr,
                  flush=True)
            if proc.returncode != 0:
                print(f"njoy failed for {label}: rc={proc.returncode}",
                      file=sys.stderr)
                return proc.returncode or 3
            gendf = stepdir / "tape31"
            if not gendf.is_file():
                print(f"njoy produced no GENDF for {label}", file=sys.stderr)
                return 4
            # Persist the raw GENDF next to the output so parse fixes never
            # need an NJOY re-run.
            gendf_store = output.parent / "gendf" / f"{label}.gendf"
            gendf_store.parent.mkdir(parents=True, exist_ok=True)
            gendf_store.write_bytes(gendf.read_bytes())
            sections = parse_gendf(gendf, set(mts))
            if not sections:
                print(f"no MF=3 sections in GENDF for {label}", file=sys.stderr)
                return 5
            result["materials"][label] = {"mat": mat, "mf3_sections": {
                str(mt): recs for mt, recs in sections.items()
            }}
    result["material_count"] = len(result["materials"])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(exactify(result), indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
