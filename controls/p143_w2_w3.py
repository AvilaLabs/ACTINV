#!/usr/bin/env python3
"""P143 W2 and W3, run with the venv python that has the candidate wheel installed, after W1.

For each of the seven Python functions it takes campaign inputs that succeeded in W1 (results/p143/w1_parity.jsonl):
W2 writes the spec's JSON text, as the dict form would serialize it, to a file beside the original and checks that the
path form and the dict form (base_dir = that folder) return the same bytes, and that no `.actinv-waste-*` file is left
behind, also after a refused dict. W3 checks, for a success and a refusal per function, that nothing reaches file
descriptors 1 or 2 and that the private temporary directory is gone (TMPDIR points at an empty folder per call).
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path

import actinv

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/p143"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "controls"))
import test_python_objects as fixtures  # noqa: E402  (self-contained waste specs, no nuclear data)

# Used only when the campaigns left no input on disk for a function (P107/P108 delete their work folders).
FALLBACK_SUCCESS = {"bounds": fixtures._bounds_spec, "composition": fixtures._composition_spec}


def fallback_refusal(function):
    if function == "classify":
        return dict(fixtures._classify_spec(), input="p143-missing-run.json")
    if function == "bounds":
        spec = fixtures._bounds_spec()
        spec["components"][0]["targets"][0]["activity_bounds_bq"]["C14"]["lower_bq"] = -1.0
        return spec
    if function == "composition":
        return dict(fixtures._composition_spec(), schema="p143-not-a-schema")
    return None


def materialize(function, document) -> Path:
    folder = Path(tempfile.mkdtemp(prefix=f"p143-w2-{function}-", dir=ROOT / "target/preflight-tmp"))
    path = folder / "spec.json"
    path.write_text(json.dumps(document, allow_nan=False), encoding="utf-8")
    return path


FUNCTIONS = ["classify", "budget", "bounds", "composition", "composition_solve", "intrusion_screen", "twin"]


def api(function):
    return actinv.twin if function == "twin" else getattr(actinv.waste, function)


@contextlib.contextmanager
def captured():
    sys.stdout.flush()
    sys.stderr.flush()
    box = {}
    with tempfile.TemporaryFile() as sink:
        saved = (os.dup(1), os.dup(2))
        try:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
            yield box
        finally:
            os.dup2(saved[0], 1)
            os.dup2(saved[1], 2)
            os.close(saved[0])
            os.close(saved[1])
            sink.seek(0)
            box["data"] = sink.read()


def leftovers(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.glob(".actinv-waste-*"))


def call_in_empty_tmp(function, spec, **kwargs):
    """Call with TMPDIR set to a fresh empty folder; return (outcome, value, stray bytes, files left in TMPDIR)."""
    tmp = Path(tempfile.mkdtemp(prefix="p143-w3-", dir=ROOT / "target/preflight-tmp"))
    old = os.environ.get("TMPDIR")
    os.environ["TMPDIR"] = str(tmp)
    tempfile.tempdir = None
    try:
        with captured() as box:
            try:
                outcome, value = "ok", api(function)(spec, text=True, **kwargs)
            except ValueError as error:
                outcome, value = "refused", str(error)
        left = sorted(p.name for p in tmp.iterdir())
    finally:
        if old is None:
            os.environ.pop("TMPDIR", None)
        else:
            os.environ["TMPDIR"] = old
        tempfile.tempdir = None
    for p in tmp.iterdir():
        p.unlink()
    tmp.rmdir()
    return outcome, value, len(box["data"]), left


def candidates(records, function, success):
    seen = set()
    for r in records:
        if r["function"] != function or r["status"] != "match" or (r["cli_exit"] == 0) != success:
            continue
        spec = Path(r["cwd"], next(a for a in r["argv"] if a.endswith(".json")))
        if spec in seen or not spec.is_file():
            continue
        seen.add(spec)
        yield spec


def w2_case(function, spec: Path) -> dict | None:
    try:
        document = json.loads(spec.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    folder = spec.parent
    twin_file = folder / f"p143-w2-{spec.stem}.json"
    twin_file.write_text(json.dumps(document, allow_nan=False), encoding="utf-8")
    try:
        path_text = api(function)(str(twin_file), text=True)
    except ValueError:
        twin_file.unlink()
        return None  # the input no longer succeeds (a later campaign case reused the folder); try another
    try:
        dict_text = api(function)(document, base_dir=folder, text=True)
        bad = dict(document, **{"spec" if function == "twin" else "schema": "p143-not-a-schema"})
        try:
            api(function)(bad, base_dir=folder, text=True)
            refused = False
        except ValueError:
            refused = True
    finally:
        twin_file.unlink()
    return {"spec": str(spec.relative_to(ROOT)), "same_bytes": path_text == dict_text,
            "bad_schema_refused": refused, "leftovers": leftovers(folder),
            "pass": path_text == dict_text and refused and not leftovers(folder)}


def main() -> int:
    records = [json.loads(line) for line in (OUT / "w1_parity.jsonl").read_text().splitlines() if line.strip()]
    w2, w3 = {}, {}
    for function in FUNCTIONS:
        pool = list(candidates(records, function, True))
        if not pool and function in FALLBACK_SUCCESS:
            pool = [materialize(function, FALLBACK_SUCCESS[function]())]
        w2[function] = next((dict(row, source="campaign" if "target/p1" in row["spec"] else "fixture")
                             for spec in pool if (row := w2_case(function, spec)) is not None),
                            {"pass": False, "reason": "no usable case"})
        rows = []
        for success in (True, False):
            spec = next(candidates(records, function, success), None)
            if spec is None:
                document = (FALLBACK_SUCCESS[function]() if success and function in FALLBACK_SUCCESS
                            else None if success else fallback_refusal(function))
                spec = materialize(function, document) if document is not None else None
            if spec is None:
                rows.append({"success_case": success, "pass": not success, "reason": "no case in the campaigns"})
                continue
            outcome, _, stray, left = call_in_empty_tmp(function, str(spec))
            rows.append({"success_case": success, "spec": str(spec.relative_to(ROOT)), "outcome": outcome,
                         "stray_bytes": stray, "tmp_left": left,
                         "pass": stray == 0 and not left and outcome == ("ok" if success else "refused")})
        w3[function] = rows
    report = {"schema": "actinv-p143-w2-w3-1", "actinv_version": actinv.__version__,
              "w2": {"cases": w2, "pass": all(r["pass"] for r in w2.values())},
              "w3": {"cases": w3, "pass": all(r["pass"] for rows in w3.values() for r in rows)}}
    report["pass"] = report["w2"]["pass"] and report["w3"]["pass"]
    (OUT / "w2_w3.json").write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"w2": report["w2"]["pass"], "w3": report["w3"]["pass"],
                      "failing": [f for f in FUNCTIONS if not w2[f]["pass"]] + [
                          f"{f}:w3" for f, rows in w3.items() if not all(r["pass"] for r in rows)]}))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
