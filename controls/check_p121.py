#!/usr/bin/env python3
"""P121 gates G2 and G3 (protocols/ACTINV-P121_PROTOCOL.md).

  G2  Reference and candidate results are identical (apart from the top-level `ms`) on the specs where nothing is
      sub-threshold. Precondition: the reference total proxy is non-null at every step.
  G3  The light-element case: (a) the candidate reports the proxy exactly when the sub-threshold share is at most
      1e-4, (b) its sub-threshold power equals this checker's own sum, (c) its proxy matches an independent
      recomputation from the response file and the spec composition, (d) nothing else differs from the reference.

Usage:
  check_p121.py --reference BIN --candidate BIN --data-dir DIR \\
      --spec G2:PATH --spec G2:PATH --spec G2:PATH --spec G3:PATH --spec G3:PATH \\
      [--work target/p121] [--out results/p121/g2_g3.json] [--reuse]

Each spec's SHA-256 must match the protocol table. Run inside the required systemd cgroup with a disk-backed
TMPDIR; the checker runs the two binaries serially. Exit 0 only when every gate passes.
"""
import argparse
import bisect
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOLERANCE = 1e-4
TIMEOUT_S = 7200
EV_LO_KEV = 1000.0
EV_HI = 2.0e7

# Protocol table: SHA-256 -> (gate, name).
SPEC_TABLE = {
    "18c51ce8cd809fd7f668fd42f649ec6f966f8e7963b4616d01457340657e86bd": ("G2", "tih2_first_shutdown"),
    "44c7d9c608268874b4162f5cb4e70788b3685d333ee637039684b07de2de38a0": ("G2", "fe_vessel_first_shutdown"),
    "cbaa2de997c81cffa86bc6810dcff6a00670431a26ddc380c79d4dc4d9976871": ("G2", "w_first_wall_first_shutdown"),
    "ab5a864eb12c33ada52fc315bd79f6f70af877543844ec82e3271f85b012c958": ("G3", "flibe_installation_1_first_shutdown"),
    "cb7b2956574288e7e012a8a8dcaf26202120ba920ab79768520b54149e28af09": ("G3", "flibe_installation_9"),
}

# AME2020 atomic masses in u, for the nuclides of the pinned specs.
ATOMIC_MASS_U = {
    "Li6": 6.0151228874, "Li7": 7.0160034366, "Be9": 9.0121830650, "F19": 18.9984031627,
    "H1": 1.00782503223, "H2": 2.01410177812,
    "Ti46": 45.95262772, "Ti47": 46.95175879, "Ti48": 47.94794198, "Ti49": 48.94786568,
    "Ti50": 49.94478689,
    "Fe54": 53.93960899, "Fe56": 55.93493633, "Fe57": 56.93539284, "Fe58": 57.93327443,
    "W180": 179.9467108, "W182": 181.94820394, "W183": 182.95022275, "W184": 183.95093092,
    "W186": 185.9543628,
}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ---------------------------------------------------------------- pure functions


def curve_value(energies, values, e):
    """Log-log interpolation, the same rule as `curve_value` in photon.rs."""
    if e < energies[0] or e > energies[-1]:
        return None
    upper = bisect.bisect_right(energies, e)
    if upper > 0 and energies[upper - 1] == e:
        return values[upper - 1]
    if upper == 0 or upper == len(energies):
        return None
    x0, x1 = energies[upper - 1], energies[upper]
    y0, y1 = values[upper - 1], values[upper]
    if x1 == x0:
        return y1
    t = math.log(e / x0) / math.log(x1 / x0)
    return math.exp(math.log(y0) + t * math.log(y1 / y0))


def mass_fractions(composition, basis):
    """Element mass fractions from explicit-nuclide keys such as `Li6`."""
    masses = {}
    for key, value in composition.items():
        m = re.fullmatch(r"([A-Za-z]{1,2})(\d+)", key)
        if not m:
            raise ValueError(
                f"composition key '{key}' is not an explicit nuclide; this checker needs a nuclide key such as "
                "'Li6' (add natural-abundance masses to the checker or rewrite the spec)")
        symbol = m.group(1).capitalize()
        name = f"{symbol}{m.group(2)}"
        if name not in ATOMIC_MASS_U:
            raise ValueError(
                f"no atomic mass for '{name}' in the checker table; add its AME2020 mass to ATOMIC_MASS_U")
        if basis == "wt_percent":
            mass = value
        elif basis in ("atom_fraction", "atoms_per_g"):
            mass = value * ATOMIC_MASS_U[name]
        else:
            raise ValueError(f"unknown material basis '{basis}'")
        masses[symbol] = masses.get(symbol, 0.0) + mass
    total = sum(masses.values())
    if total <= 0.0:
        raise ValueError("material composition has no positive mass")
    return {k: v / total for k, v in masses.items()}


def material_mu(response, fractions, e):
    out = 0.0
    for element, fraction in fractions.items():
        curve = response["element_mass_attenuation"].get(element)
        if curve is None:
            return None
        v = curve_value(curve["energy_eV"], curve["values_cm2_g"], e)
        if v is None:
            return None
        out += fraction * v
    return out if out > 0.0 else None


def response_lowest_energy(response, fractions):
    air = response["air_mass_energy_absorption"]["energy_eV"][0]
    return max([air] + [response["element_mass_attenuation"][el]["energy_eV"][0] for el in fractions])


def recompute_proxy(by_nuclide, response, fractions, build_up):
    """Total proxy (Gy/h) over every per-nuclide group with centroid in [1 keV, 20 MeV]; None when a
    group in range has no response value."""
    air = response["air_mass_energy_absorption"]
    total = 0.0
    for nu in by_nuclide:
        for g in nu["groups"]:
            e, p = g["centroid_eV"], g["power_W_g"]
            if p <= 0.0 or e < EV_LO_KEV or e > EV_HI:
                continue
            mu_air = curve_value(air["energy_eV"], air["values_cm2_g"], e)
            mu_mat = material_mu(response, fractions, e)
            if mu_air is None or mu_mat is None:
                return None
            total += (build_up / 2.0) * (mu_air / mu_mat) * p * 1000.0 * 3600.0
    return total


def subthreshold_sum(by_nuclide, e_lo):
    """Power (W/g) of per-nuclide groups with positive power and centroid below e_lo."""
    total = 0.0
    for nu in by_nuclide:
        for g in nu["groups"]:
            if g["power_W_g"] > 0.0 and g["centroid_eV"] < e_lo:
                total += g["power_W_g"]
    return total


def total_response_power(by_nuclide):
    return sum(nu["source_power_W_g"] for nu in by_nuclide)


def rel_diff(a, b):
    scale = max(abs(a), abs(b))
    return 0.0 if scale == 0.0 else abs(a - b) / scale


def first_differences(a, b, path="", limit=10, out=None):
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}/{k}: present in only one result")
            else:
                first_differences(a[k], b[k], f"{path}/{k}", limit, out)
            if len(out) >= limit:
                break
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} vs {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            first_differences(x, y, f"{path}[{i}]", limit, out)
            if len(out) >= limit:
                break
    elif a != b:
        out.append(f"{path}: {a!r} vs {b!r}")
    return out


def canonical(obj):
    return json.dumps(obj, sort_keys=True)


def drop_ms(obj):
    obj = dict(obj)
    obj.pop("ms", None)
    return obj


def raw_only_ms_differs(raw_a, raw_b):
    """True when the two raw byte strings differ only in lines carrying a top-level-looking "ms" key."""
    if raw_a == raw_b:
        return False
    la, lb = raw_a.split(b"\n"), raw_b.split(b"\n")
    if len(la) != len(lb):
        return False
    diff = [(x, y) for x, y in zip(la, lb) if x != y]
    return bool(diff) and all(b'"ms"' in x and b'"ms"' in y for x, y in diff)


def step_pairs(result):
    steps = result["steps"]
    diags = result["ledger"]["photon_spectra"]
    if len(steps) != len(diags):
        raise ValueError(f"{len(steps)} steps but {len(diags)} photon_spectra ledger entries")
    return list(zip(steps, diags))


def mask_proxy_fields(result):
    """Deep copy without the proxy fields, the new sub-threshold fields, response_excluded_power_W_g and ms."""
    r = json.loads(json.dumps(result))
    r.pop("ms", None)
    for step, diag in step_pairs(r):
        ps = step.get("photon_source")
        if ps is not None:
            ps.pop("contact_gamma_air_dose_proxy_Gy_h", None)
            ps.pop("dose_response_subthreshold_power_W_g", None)
            for nu in ps.get("by_nuclide", []):
                nu.pop("contact_gamma_air_dose_proxy_Gy_h", None)
        diag.pop("response_subthreshold_power_W_g", None)
        diag.pop("response_excluded_power_W_g", None)
    return r


# ---------------------------------------------------------------- gates


def gate_g2(ref, cand, raw_ref, raw_cand):
    steps = ref["steps"]
    nulls = [i + 1 for i, s in enumerate(steps)
             if (s.get("photon_source") or {}).get("contact_gamma_air_dose_proxy_Gy_h") is None]
    out = {"steps": len(steps), "precondition_reference_proxy_nonnull_every_step": not nulls,
           "reference_null_steps": nulls[:20]}
    if nulls:
        out["pass"] = None
        out["note"] = "precondition failed; this spec moves to the G3 rules"
        return out
    equal = canonical(drop_ms(ref)) == canonical(drop_ms(cand))
    out["canonical_equal_without_ms"] = equal
    out["raw_bytes_identical"] = raw_ref == raw_cand
    out["raw_differs_only_in_ms_line"] = raw_only_ms_differs(raw_ref, raw_cand)
    if not equal:
        out["first_differences"] = first_differences(drop_ms(ref), drop_ms(cand))
    out["pass"] = equal
    return out


def gate_g3(ref, cand, response, fractions, build_up):
    e_lo = response_lowest_energy(response, fractions)
    ra, cb = step_pairs(ref), step_pairs(cand)
    res = {"response_lowest_energy_eV": e_lo, "response_lowest_energy_is_1keV": e_lo == EV_LO_KEV,
           "steps": len(cb), "build_up": build_up, "mass_fractions": fractions}
    # (a)
    a = {"sub_only_steps": 0, "expected_nonnull": 0, "expected_null": 0, "got_nonnull": 0, "got_null": 0,
         "borderline_steps": [], "mismatches": [], "other_refusals_unchanged": True, "other_refusal_steps": 0}
    # (b)
    b = {"steps_with_subthreshold": 0, "worst_relative_difference": 0.0, "mismatches": []}
    # (c)
    c = {"steps_compared": 0, "steps_skipped_null": 0, "worst_relative_difference": 0.0, "mismatches": []}
    # (d)
    d = {"excluded_drop_worst_relative_error": 0.0, "excluded_drop_mismatches": []}
    for i, ((rs, rd), (cs, cd)) in enumerate(zip(ra, cb), start=1):
        rps, cps = rs["photon_source"], cs["photon_source"]
        sub = subthreshold_sum(cps["by_nuclide"], e_lo) if not cd["response_missing_elements"] else 0.0
        total_power = total_response_power(cps["by_nuclide"])
        r_null = rps["contact_gamma_air_dose_proxy_Gy_h"] is None
        c_val = cps["contact_gamma_air_dose_proxy_Gy_h"]
        r_excl = rd["response_excluded_power_W_g"]
        only_sub = (r_null and sub > 0.0 and not rd["response_missing_elements"]
                    and rd["group_underflow_power_W_g"] == 0.0 and rd["group_overflow_power_W_g"] == 0.0
                    and rel_diff(r_excl, sub) <= 1e-12)
        # (a)
        if only_sub:
            a["sub_only_steps"] += 1
            share = sub / total_power if total_power > 0.0 else math.inf
            if abs(share - TOLERANCE) <= 1e-9 * TOLERANCE:
                a["borderline_steps"].append(i)
                ok = True
            else:
                want = share <= TOLERANCE
                ok = (c_val is not None) == want
                a["expected_nonnull" if want else "expected_null"] += 1
            a["got_nonnull" if c_val is not None else "got_null"] += 1
            if not ok:
                a["mismatches"].append({"step": i, "share": share, "candidate_proxy": c_val})
        elif r_null:
            a["other_refusal_steps"] += 1
            if c_val is not None:
                a["other_refusals_unchanged"] = False
                a["mismatches"].append({"step": i, "note": "reference refused for a non-sub-threshold reason"})
        # (b)
        cfield = cps.get("dose_response_subthreshold_power_W_g")
        dfield = cd.get("response_subthreshold_power_W_g")
        if sub > 0.0:
            b["steps_with_subthreshold"] += 1
            for name, value in (("source", cfield), ("ledger", dfield)):
                if value is None:
                    b["mismatches"].append({"step": i, "field": name, "note": "absent", "expected": sub})
                    continue
                rd_ = rel_diff(value, sub)
                b["worst_relative_difference"] = max(b["worst_relative_difference"], rd_)
                if rd_ > 1e-12:
                    b["mismatches"].append({"step": i, "field": name, "value": value, "expected": sub})
        elif cfield is not None or dfield is not None:
            b["mismatches"].append({"step": i, "note": "field present but checker sum is zero"})
        # (c)
        if c_val is None:
            c["steps_skipped_null"] += 1
        else:
            expected = recompute_proxy(cps["by_nuclide"], response, fractions, build_up)
            c["steps_compared"] += 1
            if expected is None:
                c["mismatches"].append({"step": i, "note": "group in range has no response value"})
            else:
                rd_ = rel_diff(c_val, expected)
                c["worst_relative_difference"] = max(c["worst_relative_difference"], rd_)
                if rd_ > 1e-6:
                    c["mismatches"].append({"step": i, "candidate": c_val, "recomputed": expected})
        # (d) excluded power drops by exactly the sub-threshold power
        want_excl = r_excl - sub
        err = abs(cd["response_excluded_power_W_g"] - want_excl) / max(abs(r_excl), 1e-300) if r_excl else \
            abs(cd["response_excluded_power_W_g"] - want_excl)
        d["excluded_drop_worst_relative_error"] = max(d["excluded_drop_worst_relative_error"], err)
        if err > 1e-12:
            d["excluded_drop_mismatches"].append({"step": i, "reference": r_excl, "candidate":
                                                  cd["response_excluded_power_W_g"], "sub": sub})
    a["pass"] = not a["mismatches"] and a["other_refusals_unchanged"]
    b["pass"] = not b["mismatches"]
    c["pass"] = not c["mismatches"]
    mref, mcand = mask_proxy_fields(ref), mask_proxy_fields(cand)
    equal = canonical(mref) == canonical(mcand)
    d["masked_results_equal"] = equal
    if not equal:
        d["first_differences"] = first_differences(mref, mcand)
    d["pass"] = equal and not d["excluded_drop_mismatches"]
    res.update({"a": a, "b": b, "c": c, "d": d})
    res["pass"] = bool(a["pass"] and b["pass"] and c["pass"] and d["pass"] and res["response_lowest_energy_is_1keV"])
    return res


# ---------------------------------------------------------------- driver


def resolve_response(spec, spec_dir, data_dir, override):
    ref = (spec.get("photon") or {}).get("response")
    if ref is None:
        raise ValueError("spec has no photon.response; the proxy cannot be checked. Add a hashed response.")
    if override:
        path = Path(override)
    elif ref["path"].startswith("catalog:"):
        want = ref.get("sha256")
        if not want:
            raise ValueError("catalog response without sha256; pass --response PATH")
        path = None
        for cand in sorted(Path(data_dir).rglob("*.json")):
            if sha256_file(cand) == want:
                path = cand
                break
        if path is None:
            raise ValueError(f"no file under {data_dir} has the response SHA-256 {want[:8]}; pass --response PATH")
    else:
        path = Path(ref["path"])
        if not path.is_absolute():
            path = spec_dir / path
    want = ref.get("sha256")
    if want and sha256_file(path) != want:
        raise ValueError(f"response {path} does not match the spec's SHA-256 {want[:8]}")
    return path


def run_binary(binary, spec_path, out_path, data_dir, log_path):
    env = dict(os.environ, ACTINV_DATA_DIR=str(data_dir))
    with open(log_path, "wb") as log:
        proc = subprocess.run([str(binary), "run", str(spec_path), str(out_path)], cwd=spec_path.parent, env=env,
                              stdout=log, stderr=subprocess.STDOUT, timeout=TIMEOUT_S)
    return proc.returncode


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--spec", action="append", required=True, metavar="ROLE:PATH")
    ap.add_argument("--work", default="target/p121")
    ap.add_argument("--out", default="results/p121/g2_g3.json")
    ap.add_argument("--response", help="response JSON override for every spec")
    ap.add_argument("--reuse", action="store_true", help="reuse existing outputs in --work")
    args = ap.parse_args(argv)

    work = Path(args.work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    record = {"protocol": "ACTINV-P121", "reference_sha256": sha256_file(args.reference),
              "candidate_sha256": sha256_file(args.candidate), "specs": {}}
    seen = set()
    overall = True
    for item in args.spec:
        role, _, p = item.partition(":")
        spec_path = Path(p).resolve()
        digest = sha256_file(spec_path)
        if digest not in SPEC_TABLE:
            print(f"refusing {spec_path}: SHA-256 {digest[:8]} is not in the P121 table. Use the pinned FARIS "
                  "validation V1 specs.", file=sys.stderr)
            return 2
        gate, name = SPEC_TABLE[digest]
        if role != gate:
            print(f"refusing {spec_path}: given role {role} but the protocol table says {gate}.", file=sys.stderr)
            return 2
        seen.add(digest)
        entry = {"gate": gate, "spec_sha256": digest, "path": str(spec_path)}
        record["specs"][name] = entry
        try:
            outs = {}
            for label, binary in (("reference", args.reference), ("candidate", args.candidate)):
                out_path = work / f"{name}.{label}.json"
                if not (args.reuse and out_path.exists()):
                    code = run_binary(binary, spec_path, out_path, args.data_dir, work / f"{name}.{label}.log")
                    if code != 0:
                        raise RuntimeError(f"{label} binary exited {code}; see {work}/{name}.{label}.log")
                outs[label] = out_path.read_bytes()
            ref, cand = json.loads(outs["reference"]), json.loads(outs["candidate"])
            spec = json.loads(spec_path.read_text())
            if gate == "G2":
                entry["g2"] = gate_g2(ref, cand, outs["reference"], outs["candidate"])
                moved = entry["g2"]["pass"] is None
                entry["pass"] = entry["g2"]["pass"] is True
            else:
                moved = True
            if moved:
                response = json.loads(resolve_response(spec, spec_path.parent, args.data_dir, args.response)
                                      .read_text())
                fractions = mass_fractions(spec["material"]["composition"], spec["material"].get("basis", "wt_percent"))
                entry["g3"] = gate_g3(ref, cand, response, fractions,
                                      (spec.get("photon") or {}).get("build_up_factor", 2.0))
                entry["pass"] = entry["g3"]["pass"]
            entry["moved_to_g3_rules"] = bool(gate == "G2" and moved)
        except (RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired, OSError) as error:
            entry["error"] = f"{type(error).__name__}: {error}"
            entry["pass"] = False
        overall = overall and bool(entry["pass"])
    missing = sorted(set(SPEC_TABLE) - seen)
    record["missing_specs"] = [SPEC_TABLE[m][1] for m in missing]
    record["pass"] = overall and not missing
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(f"P121 G2/G3: {'PASS' if record['pass'] else 'FAIL'} ({out})")
    return 0 if record["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
