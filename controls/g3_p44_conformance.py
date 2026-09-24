#!/usr/bin/env python3
"""P44 G3 conformance: the sealed scorer refuses mismatched identities
and double scoring; unsealed rescoring on the development partition
remains free."""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "results", "g3_p44_conformance.json")
DATA = os.path.expanduser("~/nuclear-data")
WORK = os.path.join(DATA, "p44-work")
SEAL = os.path.join(ROOT, "results", "g0_p44_seals.json")
SEALED_OUT = os.path.join(ROOT, "results", "p44_sealed_coverage.json")
SCORER = os.path.join(ROOT, "controls", "p44_band_coverage.py")


def run(args):
    p = subprocess.run([sys.executable, SCORER, *args],
                       capture_output=True, text=True)
    return p.returncode, (p.stderr or "") + (p.stdout or "")


def doctored_seal(mutate):
    seal = json.load(open(SEAL))
    mutate(seal)
    p = os.path.join(WORK, "g3_doctored_seal.json")
    json.dump(seal, open(p, "w"))
    return p


probes = {}

# sealed scoring refuses when the output already exists
os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
if not os.path.exists(SEALED_OUT):
    json.dump({"planted": "occupies the one-shot output"},
              open(SEALED_OUT, "w"))
rc, log = run(["--sealed", "--seal", SEAL, "--partition", "sealed"])
probes["sealed_double_scoring_refused"] = {
    "rejected": rc != 0 and "already scored" in log}
os.remove(SEALED_OUT)

# sealed scoring refuses a doctored partition
p = doctored_seal(lambda s: s["partitions"]["sealed"].pop())
rc, log = run(["--sealed", "--seal", p, "--partition", "sealed"])
probes["partition_mismatch_refused"] = {
    "rejected": rc != 0 and "partition mismatch" in log}

# sealed scoring refuses a doctored code hash
p = doctored_seal(lambda s: s["code_sha256"].__setitem__("scorer", "0" * 64))
rc, log = run(["--sealed", "--seal", p, "--partition", "sealed"])
probes["code_hash_mismatch_refused"] = {
    "rejected": rc != 0 and "hash mismatch" in log}

# sealed scoring refuses without a seal
rc, log = run(["--sealed", "--partition", "sealed"])
probes["sealed_without_seal_refused"] = {
    "rejected": rc != 0 and "requires --seal" in log}

# non-sealed development scoring remains free
dev_out = os.path.join(WORK, "g3_dev_score.json")
rc, log = run(["--partition", "development", "--out", dev_out])
ok = rc == 0 and os.path.exists(dev_out)
if ok:
    rep = json.load(open(dev_out))
    ok = rep["n_points"] > 0 and not rep["sealed"]
probes["unsealed_development_scoring_free"] = {"accepted": ok}

# development partition cannot be sealed-scored
rc, log = run(["--sealed", "--seal", SEAL, "--partition", "development"])
probes["development_as_sealed_refused"] = {
    "rejected": rc != 0 and "partition mismatch" in log}

doc = {"gate": "G3", "phase": "P44", "probes": probes,
       "pass": all(p.get("rejected", p.get("accepted", False))
                  for p in probes.values())}
json.dump(doc, open(OUT, "w"), indent=2, sort_keys=True)
print(json.dumps(doc, indent=1))
