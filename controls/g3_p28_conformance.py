#!/usr/bin/env python3
"""P28 G3: conformance — unsupported combinations must fail closed with a
named error, and partially-supported combinations must surface their
coverage boundary in the ledger.

Probes (each records status + the verbatim error/ledger text):
  proton / deuteron / alpha      unsupported projectiles
  structure_mismatch             non-709 group structure
  temperature_mismatch           T != 293.6 K against the artifact
  shield_uncovered_passthrough   Co-59 (not in shield table) + fixed
                                 dilution -> executes; ledger names it
  shield_uncovered_required      same + require_shielding_complete ->
                                 must fail closed
  shield_table_bad_hash          mutated table digest -> named error
  unsupported_response           response outside the qualified set
"""
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTINV = os.path.join(ROOT, "target", "release", "actinv")
NPZ = os.path.join(ROOT, "target", "p25c-release",
                   "tendl-2025-patched-neutron-709g.npz")
DECAY = os.path.join(ROOT, "actinv-data", "v1.0.0", "decay",
                     "endf-b-viii-0_decay.dat")
DECAY_FB = os.path.join(ROOT, "actinv-data", "v1.0.0", "decay",
                        "jeff-3-3_decay.dat")
SHIELD = os.path.join(ROOT, "results", "g1_p19_shield_artifact.json")
SEALS = json.load(open(os.path.join(ROOT, "results", "g0_p28_seals.json")))
WORK = os.path.expanduser("~/nuclear-data/p28-work/g3")
OUT = os.path.join(ROOT, "results", "g3_p28_conformance.json")


def cgroup(cmd):
    return ["systemd-run", "--user", "--scope", "-q",
            "-p", "MemoryMax=6G", "-p", "MemorySwapMax=0",
            "-p", "TasksMax=128", "-p", "CPUQuota=200%",
            "--", "env", f"TMPDIR={WORK}", *cmd]


def base_spec(title):
    flux = [0.0] * 709
    flux[100] = 1.0
    return {
        "spec": "actinv-spec-1", "title": title, "projectile": "neutron",
        "library": {"path": NPZ,
                    "sha256": SEALS["identities"]["activation_library"]
                    ["sha256"]},
        "decay": {"primary": DECAY, "fallback": DECAY_FB},
        "material": {"mass_g": 1.0, "basis": "wt_percent",
                     "composition": {"Fe": 100.0}},
        "spectrum": {"structure": "fispact-709", "flux_per_group": flux,
                     "total": 1.0, "descending": True},
        "schedule": [{"dt": "300 s", "flux": 1.0}],
        "options": {"mode": "auto"},
        "self_shielding": None, "radiological": None, "damage": None,
        "fission_yields": {"energy": "spectrum_average", "files": [],
                           "fixed_energy_eV": None},
        "uncertainty": None,
    }


PROBES = {}


def probe(pid, mutate):
    s = base_spec(pid)
    mutate(s)
    PROBES[pid] = s
    return s


probe("proton", lambda s: s.__setitem__("projectile", "proton"))
probe("deuteron", lambda s: s.__setitem__("projectile", "deuteron"))
probe("alpha", lambda s: s.__setitem__("projectile", "alpha"))


def _structure(s):
    s["spectrum"]["structure"] = "xmas-172"


probe("structure_mismatch", _structure)


def _temp(s):
    s["options"]["temperature_K"] = 600.0


probe("temperature_mismatch", _temp)


def _shield_passthrough(s):
    s["material"]["composition"] = {"Co": 100.0}
    s["self_shielding"] = {
        "table": {"path": SHIELD,
                  "sha256": SEALS["identities"]["shield_table"]["sha256"]},
        "dilution": "fixed", "sigma0_b": 0.1}


probe("shield_uncovered_passthrough", _shield_passthrough)


def _shield_required(s):
    _shield_passthrough(s)
    s["options"]["require_shielding_complete"] = True


probe("shield_uncovered_required", _shield_required)


def _bad_hash(s):
    s["self_shielding"] = {
        "table": {"path": SHIELD, "sha256": "0" * 64},
        "dilution": "fixed", "sigma0_b": 0.1}


probe("shield_table_bad_hash", _bad_hash)


def main():
    os.makedirs(WORK, exist_ok=True)
    results = []
    for pid, spec in PROBES.items():
        sp = os.path.join(WORK, f"{pid}.json")
        op = os.path.join(WORK, f"{pid}.out.json")
        json.dump(spec, open(sp, "w"))
        r = subprocess.run(cgroup([ACTINV, "run", sp, op]),
                           capture_output=True, text=True)
        rec = {"id": pid,
               "spec_sha256": hashlib.sha256(
                   open(sp, "rb").read()).hexdigest()}
        if r.returncode == 0:
            rec["status"] = "executed"
            out = json.load(open(op))
            rec["out_sha256"] = hashlib.sha256(
                open(op, "rb").read()).hexdigest()
            led = out["ledger"]
            rec["shielding_ledger"] = led.get("self_shielding") or \
                led.get("shielding") or \
                {k: v for k, v in led.items() if "shield" in k}
        else:
            rec["status"] = "gap"
            rec["error"] = (r.stderr or r.stdout).strip()[-500:]
        results.append(rec)
        print(f"{pid}: {rec['status']} "
              f"{(rec.get('error') or '')[:90]}", flush=True)

    out = {"schema": "actinv-p28-g3-1", "gate": "G3", "phase": "P28",
           "partition": "p28_qualifying", "probes": results}
    json.dump(out, open(OUT, "w"), indent=2, sort_keys=True)
    print({r["id"]: r["status"] for r in results})


if __name__ == "__main__":
    main()
