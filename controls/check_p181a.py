#!/usr/bin/env python3
"""P181 Amendment A checker (protocols/ACTINV-P181_AMENDMENT_A.md). Reads results/p181a_scale.json, written by
controls/p181a_harness.py (ACTINV on the ENDF/B-VIII.1 709-group library), and P181's record
results/p181_scale.json, whose OpenMC runs it reuses. G0-G3 are evaluated here; G4 (CI on the pushed evidence commit)
is external.

  G0  P181's protocol and Amendment A are in protocols/protocol_hash.txt.
  G1  P181's G1 on the amendment record, with the ENDF/B-VIII.1 library in place of TENDL-2025 and the adapter and
      _defaults.py equal to this checkout's; the amendment, its harness and P181's harness, protocol, demo and group
      files equal to this checkout's; only the `actinv` arm planned; P181's record equal to the one committed with
      P181's evidence (e87a4ab); the same CPU model in both records.
  G2  P181's G2 for the `actinv` arm alone: every size run or not run by a registered rule (recomputed); complete at 8
      elements; every complete run has finite times, a dose, a photon strength and a 709-group flux.
  G3  P181's quiet-machine test on the amendment record. Then P181's summary is recomputed with the amendment's ACTINV
      runs in place of P181's and P181's OpenMC runs and rules unchanged, and written to results/p181a_verdict.json
      (with --no-write, compared with it).

Verdict: P181A-PASS if G0-G3 hold (G4 external). Times and reach are reported, not gated.

Usage: check_p181a.py [--record FILE] [--no-write]
       check_p181a.py --selftest
"""
import argparse
import contextlib
import copy
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import check_p181 as c  # noqa: E402
import p181_harness as h  # noqa: E402

AMENDMENT = ROOT / "protocols" / "ACTINV-P181_AMENDMENT_A.md"
AMENDMENT_HARNESS = ROOT / "controls" / "p181a_harness.py"
RECORD = ROOT / "results" / "p181a_scale.json"
P181_RECORD = ROOT / "results" / "p181_scale.json"
VERDICT = ROOT / "results" / "p181a_verdict.json"

# results/p181_scale.json as committed with P181's evidence (e87a4ab)
P181_RECORD_SHA256 = "8c79bb8bbdab17bed1d57530bca84bf38987d8588ed858f46dce9adbdd4c02f0"
# the ENDF/B-VIII.1 709-group library P178 built (endfb81_all_709g.npz), P180's pin
ENDF_LIBRARY = "a964a44ef106b692d170ba6085859ca2827ff967b3e9a6ddc60ca8fa9ac92051"
ARMS = ["actinv"]
ACTINV_DATA = "with an ENDF/B-VIII.1 activation library"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@contextlib.contextmanager
def arms(order):
    """P181's checker and harness read the arm list from p181_harness; narrow it for the amendment's own gates."""
    saved = h.ARM_ORDER
    h.ARM_ORDER = order
    try:
        yield
    finally:
        h.ARM_ORDER = saved


def g0():
    logged = c.HASH_LOG.read_text()
    return [f"{p.name} hash not in protocol_hash.txt" for p in (c.PROTOCOL, AMENDMENT)
            if not p.is_file() or f"{sha256(p)}  protocols/{p.name}" not in logged]


def g1(record, p181, repo=None, extra=None, p181_sha=None):
    saved = dict(c.PINS)
    c.PINS["actinv_library"] = ENDF_LIBRARY
    try:
        problems = c.g1(record, repo)
    finally:
        c.PINS.clear()
        c.PINS.update(saved)
    extra = extra if extra is not None else {"amendment": sha256(AMENDMENT),
                                             "amendment_harness": sha256(AMENDMENT_HARNESS)}
    inputs = record.get("inputs", {})
    for name, digest in extra.items():
        if inputs.get(name, {}).get("sha256") != digest:
            problems.append(f"input {name} hash is not this checkout's")
    if record.get("amendment") != "A":
        problems.append("not an Amendment A record")
    if record.get("plan", {}).get("arms") != ARMS:
        problems.append(f"planned arms are not {ARMS}")
    if (p181_sha if p181_sha is not None else sha256(P181_RECORD)) != P181_RECORD_SHA256:
        problems.append("P181's record is not the one committed with its evidence")
    if record.get("environment", {}).get("cpu_model") != p181.get("environment", {}).get("cpu_model"):
        problems.append("a different CPU model from P181's record")
    return problems


def merged(record, p181):
    """P181's record with its ACTINV runs replaced by the amendment's."""
    out = copy.deepcopy(p181)
    out["attempts"] = ([a for a in record.get("attempts", []) if a["arm"] == "actinv"]
                       + [a for a in p181.get("attempts", []) if a["arm"] == "openmc_flux"])
    out["skips"] = ([s for s in record.get("skips", []) if s["arm"] == "actinv"]
                    + [s for s in p181.get("skips", []) if s["arm"] == "openmc_flux"])
    out["environment"] = dict(out.get("environment", {}), cpu_model=record.get("environment", {}).get("cpu_model"))
    return out


def relabel(sentence):
    """Name ACTINV's data in P181's sentences: its runs here used the ENDF/B-VIII.1 library."""
    for old, new in (("ACTINV 1.7.1 took", f"ACTINV 1.7.1 {ACTINV_DATA} took"),
                     ("ACTINV completed meshes", f"ACTINV ({ACTINV_DATA}) completed meshes")):
        if old in sentence:
            return sentence.replace(old, new, 1)
    raise ValueError(f"unexpected P181 sentence: {sentence}")


def evaluate(record, p181, repo=None, extra=None, p181_sha=None):
    gates = {"G0": g0(), "G1": g1(record, p181, repo, extra, p181_sha)}
    with arms(ARMS):
        gates["G2"] = c.g2(record)
    gates["G3"] = c.quiet(record) if not gates["G2"] else ["fidelity failed"]
    if gates["G2"]:
        return gates, {}, []
    result, sentences = c.summary(merged(record, p181))
    return gates, result, [relabel(s) for s in sentences]


# ------------------------------------------------------------------------------------------------------ self-test

def synthetic():
    p181, repo = c.synthetic_record()
    p181["environment"]["cpu_model"] = "x"
    record = copy.deepcopy(p181)
    record["inputs"]["actinv_library"] = {"sha256": ENDF_LIBRARY}
    extra = {"amendment": "a" * 64, "amendment_harness": "b" * 64}
    record["inputs"].update({k: {"sha256": v} for k, v in extra.items()})
    record["amendment"] = "A"
    record["plan"]["arms"] = list(ARMS)
    record["attempts"] = [a for a in record["attempts"] if a["arm"] == "actinv"]
    record["skips"] = [s for s in record["skips"] if s["arm"] == "actinv"]
    return record, p181, repo, extra


def selftest():
    failures = []
    record, p181, repo, extra = synthetic()
    gates, result, sentences = evaluate(record, p181, repo, extra, P181_RECORD_SHA256)
    if any(gates[g] for g in ("G1", "G2", "G3")):
        failures.append(f"clean synthetic records fail: {gates}")
    elif not sentences or not all(ACTINV_DATA in s for s in sentences):
        failures.append(f"sentences do not name ACTINV's data: {sentences}")
    elif result["reach"]["actinv"]["elements"] != 32768:
        failures.append(f"ACTINV reach wrong: {result['reach']['actinv']}")
    if h.ARM_ORDER != ["actinv", "openmc_flux"]:
        failures.append(f"arm list not restored: {h.ARM_ORDER}")

    # the amendment's doses, not P181's, decide the accuracy checks
    record, p181, repo, extra = synthetic()
    for a in p181["attempts"]:
        if a["arm"] == "actinv":
            a["dose_rate_Sv_h"] = 0.5
    _, result, sentences = evaluate(record, p181, repo, extra, P181_RECORD_SHA256)
    if result["reach"]["actinv"]["elements"] != 32768:
        failures.append("P181's ACTINV doses leaked into the amendment's checks")
    record, p181, repo, extra = synthetic()
    for a in record["attempts"]:
        if a["dim"] == 4:
            a["dose_rate_Sv_h"] = 0.5
    _, result, sentences = evaluate(record, p181, repo, extra, P181_RECORD_SHA256)
    if result["reach"]["actinv"]["elements"] != 8 or any("64 elements" in s for s in sentences[:-1]):
        failures.append("an amendment dose outside the band still earned a sentence or reach")

    cases = {
        "TENDL library": lambda r, p: r["inputs"]["actinv_library"].update(sha256=c.PINS["actinv_library"]),
        "amendment changed": lambda r, p: r["inputs"]["amendment"].update(sha256="0" * 64),
        "amendment harness changed": lambda r, p: r["inputs"]["amendment_harness"].update(sha256="0" * 64),
        "adapter changed": lambda r, p: r["inputs"]["adapter"].update(sha256="0" * 64),
        "OpenMC arm planned": lambda r, p: r["plan"].update(arms=["actinv", "openmc_flux"]),
        "not marked": lambda r, p: r.pop("amendment"),
        "other machine": lambda r, p: r["environment"].update(cpu_model="y"),
        "smoke": lambda r, p: r.update(smoke=True),
        "dirty tree": lambda r, p: r["environment"].update(git_dirty_paths=" M x"),
        "run missing": lambda r, p: r["attempts"].pop(2),
        "8 elements incomplete": lambda r, p: r["attempts"][0].update(complete=False),
        "busy machine": lambda r, p: [a["before"].update(load_average=[5.0]) for a in r["attempts"]],
    }
    for name, mutate in cases.items():
        record, p181, repo, extra = synthetic()
        mutate(record, p181)
        gates, _, _ = evaluate(record, p181, repo, extra, P181_RECORD_SHA256)
        if not (gates["G1"] or gates["G2"] or gates["G3"]):
            failures.append(f"not caught: {name}")
    record, p181, repo, extra = synthetic()
    gates, _, _ = evaluate(record, p181, repo, extra, "0" * 64)
    if not gates["G1"]:
        failures.append("not caught: P181 record changed")
    if not c.selftest():
        failures.append("P181's selftest fails")
    for failure in failures:
        print("selftest:", failure)
    print("selftest (P181A):", "PASS" if not failures else "FAIL")
    return not failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", type=Path, default=RECORD)
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        sys.exit(0 if selftest() else 1)
    record = json.loads(args.record.read_text())
    p181 = json.loads(P181_RECORD.read_text())
    gates, result, sentences = evaluate(record, p181)
    passed = all(not problems for problems in gates.values())
    if not passed:
        sentences = []
    verdict = {
        "protocol": "ACTINV-P181 Amendment A",
        "verdict": "P181A-PASS (pending G4, CI on the evidence commit)" if passed else "P181A-FAIL",
        "gates": {name: {"pass": not problems, "problems": problems} for name, problems in gates.items()},
        "summary": result,
        "allowed_sentences": sentences,
    }
    for name, problems in gates.items():
        print(f"{name}: {'PASS' if not problems else 'FAIL'}")
        for problem in problems[:20]:
            print(f"  {problem}")
    if args.no_write:
        if VERDICT.is_file():
            recorded = json.loads(VERDICT.read_text())
            if recorded.get("summary") != json.loads(json.dumps(result)) or recorded.get(
                    "allowed_sentences") != sentences:
                print("G3: FAIL (verdict file differs from the recomputed summary)")
                passed = False
    elif args.record.resolve() == RECORD.resolve():
        VERDICT.write_text(json.dumps(verdict, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    print("outcome:")
    for sentence in sentences:
        print(f"  {sentence}")
    print(verdict["verdict"] if passed else "P181A-FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
