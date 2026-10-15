#!/usr/bin/env python3
"""P135 checker: the opt-in depletion table (`summary.depletion`, FISPACT-II DEPLETION).

G0, G1 and G6 are external (protocol hash registry, fmt/clippy/tests, CI); they are recorded as external and do not
enter the verdict. This checker evaluates G2-G5.

G2  Default unchanged. examples/fns_fe_5min.json and examples/shielding_demo.json, and a short FNS Fe spec with a
    `summary` block that does not ask for depletion, are byte-identical between reference and candidate apart from
    `ms`; the candidate's summary-without-depletion run carries no `depletion` key in any step summary, in the ledger
    or in the certificate.
G3  Coupled mode, depletion on. Three runs: FNS Fe as shipped, FNS Fe with the flux multiplier raised so Fe-56
    depletes by at least 1e-4 (schedule: 300 s at x2e8, 600 s off, 300 s at x1e8, 1 h off), and a radioactive
    initial material (Fe56 + Fe55 + Mn54) so the decay channel is not vacuous (natural Fe has only stable isotopes
    and therefore no decay channel). Per nuclide and step: start atoms equal the previous step's end (the initial
    atoms for step 1, which for natural Fe are recomputed here from the embedded abundance table), end atoms equal
    the step's inventory entry, depleted = start - end, the fractions are depleted/start and cumulative/initial,
    channel shares sum to 1 within 1e-12, the decay channel equals ln2/T1/2 from this script's own MT=457 read
    (primary first, fallback for keys the primary lacks) within 1e-9, and every reaction channel equals the sum of the
    nuclide's library loss rows (ZAP=-1) of that MT, collapsed here as sum(sigma_g phi_g)/sum(phi_g) * 1e-24 *
    sum(phi_g) and multiplied by the step's flux multiplier, within 1e-9. The channel set is exactly the set with
    positive rate; channels are listed in descending rate; without a covariance run no uncertainty key appears. The
    ledger and certificate record the request (model, nuclide count, uncertainty "none").
G4  Trace mode. The shipped FNS Fe schedule (mode auto, which must select trace) and the scaled schedule (mode
    trace): channels as in G3 and, per nuclide, the step and cumulative depleted fractions equal -expm1(-R dt) and
    -expm1(-sum R dt) within 1e-9 relative. The depleted fraction is computed by the candidate as (start - end) /
    start, so a fraction below about 1e-6 loses digits to the subtraction itself; where the expectation is below
    that, agreement is accepted within 6e-16 absolute (one rounding of start) and such values are counted as
    `resolution_limited` (the scaled run is the strict test).
G5  Uncertainty. The scaled-down short FNS schedule with the TENDL-2025 MF=33 covariance sidecar: every reaction
    channel's relative standard uncertainty equals an independent sqrt(w^T C w) within 1e-6, where C is the
    covariance of the nuclide's loss rows assembled from the sidecar here (components.npy / grid_offsets.npy /
    grid_values.npy / values.npy, kinds relative, absolute, LB=8 and LB=9 short range, cross-MT blocks included
    for the total) and collapsed under the spectrum with weights w_g = sigma_g phi_g / sum(sigma phi). A channel
    carries an uncertainty iff its (target, MT) has a self-covariance component. `uncertainty_covered_share` equals
    the covered share of the total removal; the joint total is checked wherever every channel is covered (it may
    be that no Fe nuclide qualifies; that is reported as not evaluable and does not fail the gate, which the
    protocol states for channels). A channel whose independently assembled self-variance is negative is reported
    unresolved and fails the gate (the candidate would have excluded the block, and this script does not
    reproduce its exclusion rule).

Usage: check_p135.py --reference REF --candidate CAND [--workdir target/p135-work] [--out results/p135_depletion.json]
       check_p135.py --self-check      (offline unit checks of the covariance collapse; runs no binary)
"""
import argparse
import copy
import json
import math
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_p127 as base  # noqa: E402  (parsers and the library reader; the physics below is recomputed here)

ROOT = Path(__file__).resolve().parent.parent
GATE_SPEC = ROOT / "examples" / "fns_fe_5min.json"
DEMO_SPEC = ROOT / "examples" / "shielding_demo.json"
PROTOCOL = ROOT / "protocols" / "ACTINV-P135_PROTOCOL.md"
COVARIANCE = ROOT / "actinv-data" / "v1.1.0" / "uncertainty" / "tendl-2025-neutron-709g.cov.npz"

RATE_TOL = 1e-9
SHARE_TOL = 1e-12
BOOKKEEPING_TOL = 1e-12
FRACTION_TOL = 1e-9
UNCERTAINTY_TOL = 1e-6
FRACTION_FLOOR = 6e-16  # one rounding of `start` in (start - end) / start
MEASURABLE = 1e-4
LIST_LIMIT = 20
BARN_CM2 = base.BARN_CM2

# Flux multipliers: the Fe-56 total loss rate under the FNS spectrum is about 6.3e-15 /s per unit multiplier, so a
# 300 s step at x2e8 depletes it by 3.8e-4 and the second irradiation adds 1.9e-4.
SCALED_SCHEDULE = [{"dt": "300 s", "flux": 2.0e8}, {"dt": "600 s", "flux": 0.0}, {"dt": "300 s", "flux": 1.0e8},
                   {"dt": "3600 s", "flux": 0.0}]
SHORT_SCHEDULE = [{"dt": "300 s", "flux": 1.0}, {"dt": "600 s", "flux": 0.0}, {"dt": "300 s", "flux": 0.5},
                  {"dt": "3600 s", "flux": 0.0}]
DECAY_SCHEDULE = [{"dt": "300 s", "flux": 1.0e8}, {"dt": "1 d", "flux": 0.0}, {"dt": "300 s", "flux": 1.0e8},
                  {"dt": "1 d", "flux": 0.0}]
DECAY_COMPOSITION = {"Fe56": 98.0, "Fe55": 1.0, "Mn54": 1.0}


class GateError(Exception):
    """A gate could not be evaluated (as opposed to evaluating to a mismatch)."""


def sha256(path):
    return base.sha256_file(path)


def rel_diff(a, b):
    return base.rel_diff(a, b)


def finite(x):
    return x if isinstance(x, (int, float)) and math.isfinite(x) else None


# ------------------------------------------------------------------------------ running and specs


def run(binary, spec, out, timeout):
    proc = subprocess.run([binary, "run", str(spec), str(out)], cwd=ROOT, capture_output=True, text=True,
                          timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"{binary} failed on {spec}: {proc.stderr.strip()[-400:]}")
    return json.loads(Path(out).read_text())


def absolute(spec):
    spec = copy.deepcopy(spec)
    spec["library"]["path"] = str(ROOT / spec["library"]["path"])
    for key in ("primary", "fallback"):
        if spec.get("decay", {}).get(key):
            spec["decay"][key] = str(ROOT / spec["decay"][key])
    unc = spec.get("uncertainty")
    if unc and unc.get("covariance") and not Path(unc["covariance"]["path"]).is_absolute():
        unc["covariance"]["path"] = str(ROOT / unc["covariance"]["path"])
    return spec


def make_spec(base_spec, *, mode, schedule=None, depletion=True, composition=None, uncertainty=None, tag=""):
    spec = absolute(base_spec)
    spec["options"] = dict(spec.get("options", {}), mode=mode)
    if schedule is not None:
        spec["schedule"] = copy.deepcopy(schedule)
    if composition is not None:
        spec["material"] = dict(spec["material"], composition=dict(composition))
    spec["summary"] = {"depletion": True} if depletion else {}
    if uncertainty is not None:
        spec["uncertainty"] = uncertainty
    if tag:
        spec["title"] = f"{spec.get('title', 'P135')} [{tag}]"
    return spec


def write_spec(work, label, spec):
    path = work / f"{label}.spec.json"
    path.write_text(json.dumps(spec))
    return path


# ------------------------------------------------------------------------------ independent decay read


def scan_decay(path, needed):
    """{(ZA, LISO): (half_life, d_half_life, nst)} for the `needed` keys, and every key present, from an ENDF-6
    decay sublibrary (MF=8 MT=457: the first LIST's C1/C2 are T1/2 and its uncertainty)."""
    records, all_keys = {}, set()
    state = {"cur": None, "buf": []}

    def flush():
        buf = state["buf"]
        if state["cur"] is None or not buf:
            return
        head = base.cont(buf[0])
        key = (int(round(head[0])), head[3])
        all_keys.add(key)
        if key in needed:
            energy_head, _values, _nxt = base.read_list(buf, 1)
            records[key] = (energy_head[0], energy_head[1], head[4])

    with open(path, "r", encoding="ascii", errors="replace") as handle:
        for raw in handle:
            line = raw.rstrip("\r\n")
            if len(line) < 75:
                continue
            if line[70:75] == " 8457":
                mat = line[66:70]
                if state["cur"] != mat:
                    state["cur"], state["buf"] = mat, []
                state["buf"].append(line)
            elif state["cur"] is not None and state["buf"]:
                flush()
                state["cur"], state["buf"] = None, []
        flush()
    return records, all_keys


_DECAY_CACHE = {}


def read_decay(spec, keys):
    """Half-life data of `keys`; the primary wins, the fallback supplies keys the primary lacks. Results are cached
    per (primary, fallback, key); a key with no record at all is cached as None."""
    primary, fallback = base.decay_files(spec)
    needed = {k for k in keys if (primary, fallback, k) not in _DECAY_CACHE}
    if needed:
        records, present = scan_decay(primary, needed)
        if fallback:
            more, _ = scan_decay(fallback, needed - present)
            for key, rec in more.items():
                records.setdefault(key, rec)
        for key in needed:
            _DECAY_CACHE[(primary, fallback, key)] = records.get(key)
    return {k: _DECAY_CACHE[(primary, fallback, k)] for k in keys}


def decay_constant(rec):
    if rec is None:
        return 0.0, None
    half_life, d_half_life, nst = rec
    if nst == 1 or half_life <= 0.0:
        return 0.0, None
    relative = d_half_life / half_life if d_half_life > 0.0 else None
    return math.log(2.0) / half_life, relative


# ------------------------------------------------------------------------------ independent rate collapse


class Expectation:
    """Per-nuclide loss rows and decay constants recomputed from the library NPZ and the decay files."""

    def __init__(self, library, spec):
        self.library = library
        self.spec = spec
        self.phi = base.ascending_flux(spec["spectrum"])
        self.flux_sum = math.fsum(self.phi.tolist())
        self.schedule = base.schedule_of(spec)
        self._loss = {}
        self._decay = {}

    def loss_rows(self, key):
        """[(mt, library row, rate per atom at multiplier 1, one-group sigma in barns)] of the ZAP=-1 rows."""
        if key not in self._loss:
            rows = self.library.row_indices(key)
            collapsed = self.library.collapse([key], self.phi)[key]
            out = []
            for row, (mt, zap, _lfs, _lmf, rate) in zip(rows, collapsed):
                if zap == -1:
                    sigma = rate / (BARN_CM2 * self.flux_sum) if self.flux_sum > 0.0 else 0.0
                    out.append((mt, row, rate, sigma))
            self._loss[key] = out
        return self._loss[key]

    def prime(self, keys):
        """Read the decay records of all `keys` in one pass over the files."""
        read_decay(self.spec, keys)

    def decay(self, key):
        if key not in self._decay:
            self._decay[key] = decay_constant(read_decay(self.spec, {key})[key])
        return self._decay[key]

    def channels(self, key, multiplier):
        """{channel name: rate} the candidate must list: decay if lambda > 0, each MT whose summed rate is > 0."""
        out = {}
        lam, _ = self.decay(key)
        if lam > 0.0:
            out["decay"] = lam
        if multiplier > 0.0:
            by_mt = {}
            for mt, _row, rate, _sigma in self.loss_rows(key):
                scaled = rate * multiplier
                if scaled > 0.0:
                    by_mt.setdefault(mt, []).append(scaled)
            for mt, parts in by_mt.items():
                out[f"MT{mt}"] = math.fsum(parts)
        return out


def initial_keys(spec):
    """Initial nuclides the spec's composition creates, by (ZA, LISO), and atoms/g when the composition is natural."""
    composition = spec["material"]["composition"]
    explicit = [name for name in composition if base.NAME_RE.match(name)]
    if explicit and len(explicit) != len(composition):
        raise GateError("mixed natural and explicit composition is not handled")
    if explicit:
        return {base.key_of(name): None for name in explicit}
    return base.bulk_atoms_per_g(spec, base.load_isotopes())


# ------------------------------------------------------------------------------ the table check


class Findings:
    def __init__(self):
        self.bad = []
        self.n_bad = 0
        self.compared = 0
        self.worst = {}
        self.counts = {}
        self.fe56_cumulative = []

    def count(self, name, n=1):
        self.counts[name] = self.counts.get(name, 0) + n

    def see(self, name, value):
        self.compared += 1
        self.count(name)
        if math.isfinite(value):
            self.worst[name] = max(self.worst.get(name, 0.0), value)
        else:
            self.worst[name] = math.inf

    def fail(self, **item):
        self.n_bad += 1
        if len(self.bad) < LIST_LIMIT:
            self.bad.append({k: (finite(v) if isinstance(v, float) else v) for k, v in item.items()})


def close(found, name, a, b, tol, where, floor=0.0):
    """Record the relative difference of a and b under `name`; fail above `tol` (or above `floor` absolute)."""
    d = rel_diff(a, b)
    found.see(name, d)
    if d > tol and abs(a - b) > floor:
        found.fail(check=name, reported=a, expected=b, relative=d, **where)
        return False
    return True


def check_table(result, spec, ex, *, model, coupled, initial, with_covariance):
    """Gate checks of `steps[].summary.depletion` against independent expectations. `initial` maps
    (ZA, LISO) -> atoms/g for natural compositions (None entries for explicit ones)."""
    found = Findings()
    steps = result["steps"]
    schedule = ex.schedule
    if len(steps) != len(schedule):
        found.fail(check="step_count", reported=len(steps), expected=len(schedule))
        return found
    ledger = result["ledger"].get("depletion")
    certificate = result["certificate"].get("depletion")
    absent = set()
    if not isinstance(ledger, dict):
        found.fail(check="ledger_depletion_missing")
    else:
        absent = {base.name_of((int(token.split("_")[0]), int(token.split("_")[1])))
                  for token in ledger.get("initial_nuclides_absent_from_decay_library", [])}
        for field, want in (("requested", True), ("model", model),
                            ("uncertainty", "mf33" if with_covariance else "none")):
            if ledger.get(field) != want:
                found.fail(check="ledger_depletion_field", field=field, reported=str(ledger.get(field)),
                           expected=str(want))
    if not isinstance(certificate, dict) or not certificate.get("formula"):
        found.fail(check="certificate_depletion_missing")
    expected_names = {base.name_of(key) for key in initial}
    previous_end, initial_reported, cumulative_exponent = {}, {}, {}
    fe56_fraction = []
    for si, step in enumerate(steps):
        dt, multiplier = schedule[si]
        table = step.get("summary", {}).get("depletion")
        if table is None:
            found.fail(check="table_missing", step=si + 1)
            continue
        if table.get("model") != model:
            found.fail(check="model", step=si + 1, reported=str(table.get("model")), expected=model)
        rows = {n["nuclide"]: n for n in table["nuclides"]}
        wanted = expected_names - absent
        if set(rows) != wanted:
            found.fail(check="nuclide_set", step=si + 1, reported=sorted(rows), expected=sorted(wanted))
        initial_list = [n["initial_atoms_per_g"] for n in table["nuclides"]]
        if any(a < b for a, b in zip(initial_list, initial_list[1:])):
            found.fail(check="nuclide_order", step=si + 1, reported=str(initial_list))
        inventory = {row["nuclide"]: row["atoms_per_g"] for row in step.get("inventory", [])}
        for name, row in rows.items():
            key = base.key_of(name)
            where = {"step": si + 1, "nuclide": name}
            if (row["Z"], row["A"], row["LISO"]) != (key[0] // 1000, key[0] % 1000, key[1]):
                found.fail(check="identity", **where)
            n0 = row["initial_atoms_per_g"]
            if si == 0:
                initial_reported[name] = n0
                expected_n0 = initial.get(key)
                if expected_n0 is not None:
                    close(found, "initial_atoms", n0, expected_n0, BOOKKEEPING_TOL, where)
                previous_end[name] = n0
            else:
                close(found, "initial_atoms_constant", n0, initial_reported[name], 0.0, where)
            start, end = row["atoms_start_per_g"], row["atoms_end_per_g"]
            close(found, "start_is_previous_end", start, previous_end[name], BOOKKEEPING_TOL, where)
            previous_end[name] = end
            if coupled:
                if name not in inventory:
                    found.fail(check="end_not_in_inventory", **where)
                else:
                    close(found, "end_equals_inventory", end, inventory[name], BOOKKEEPING_TOL, where)
            depleted = row["depleted_atoms_per_g"]
            close(found, "depleted_is_start_minus_end", depleted, start - end, BOOKKEEPING_TOL,
                  where, floor=BOOKKEEPING_TOL * abs(start))
            fraction = depleted / start if start > 0.0 else 0.0
            close(found, "depleted_fraction_bookkeeping", row["depleted_fraction"], fraction, BOOKKEEPING_TOL,
                  where, floor=FRACTION_FLOOR)
            cumulative = n0 - end
            close(found, "cumulative_is_initial_minus_end", row["cumulative_depleted_atoms_per_g"], cumulative,
                  BOOKKEEPING_TOL, where, floor=BOOKKEEPING_TOL * abs(n0))
            close(found, "cumulative_fraction_bookkeeping", row["cumulative_depleted_fraction"],
                  cumulative / n0 if n0 > 0.0 else 0.0, BOOKKEEPING_TOL, where, floor=FRACTION_FLOOR)
            if name == "Fe56":
                fe56_fraction.append(row["cumulative_depleted_fraction"])
            # channels
            expected = ex.channels(key, multiplier)
            listed = row["channels"]
            names = [c["channel"] for c in listed]
            if set(names) != set(expected) or len(names) != len(set(names)):
                found.fail(check="channel_set", reported=names, expected=sorted(expected), **where)
                continue
            rates = [c["rate_per_s"] for c in listed]
            if any(a < b for a, b in zip(rates, rates[1:])):
                found.fail(check="channel_order", reported=str(rates), **where)
            total_expected = math.fsum(expected.values())
            for c in listed:
                cname = c["channel"]
                tolerance = RATE_TOL
                found.count("channels_checked")
                if cname == "decay":
                    lam, _ = ex.decay(key)
                    close(found, "decay_channel_vs_ln2_over_half_life", c["rate_per_s"], lam, tolerance, where)
                    if c.get("kind") != "decay" or c.get("mt") is not None:
                        found.fail(check="decay_channel_identity", channel=cname, **where)
                else:
                    close(found, "reaction_channel_vs_collapse", c["rate_per_s"], expected[cname], tolerance,
                          {"channel": cname, **where})
                    if c.get("kind") != "reaction" or cname != f"MT{c.get('mt')}":
                        found.fail(check="reaction_channel_identity", channel=cname, **where)
                close(found, "share", c["share"], expected[cname] / total_expected, SHARE_TOL,
                      {"channel": cname, **where})
                if not with_covariance and "relative_standard_uncertainty" in c:
                    found.fail(check="uncertainty_key_without_covariance", channel=cname, **where)
            if listed:
                found.see("share_sum_minus_1", abs(math.fsum(c["share"] for c in listed) - 1.0))
                if abs(math.fsum(c["share"] for c in listed) - 1.0) > SHARE_TOL:
                    found.fail(check="shares_sum", reported=math.fsum(c["share"] for c in listed), **where)
            close(found, "removal_rate", row["removal_rate_per_s"], total_expected, RATE_TOL, where)
            close(found, "removal_rate_is_channel_sum", row["removal_rate_per_s"],
                  math.fsum(rates), SHARE_TOL, where)
            if not with_covariance and ("removal_rate_relative_standard_uncertainty" in row
                                        or "uncertainty_covered_share" in row):
                found.fail(check="uncertainty_key_without_covariance", **where)
            if not coupled:
                # trace_analytic: N(t) = N0 exp(-sum R dt); the fractions via expm1 for small R dt
                total = total_expected
                before = cumulative_exponent.get(name, 0.0)
                after = before + total * dt
                cumulative_exponent[name] = after
                close(found, "analytic_atoms_end", end, n0 * math.exp(-after), BOOKKEEPING_TOL, where)
                for label, reported, exponent in (("analytic_step_fraction", row["depleted_fraction"], total * dt),
                                                  ("analytic_cumulative_fraction",
                                                   row["cumulative_depleted_fraction"], after)):
                    want = -math.expm1(-exponent)
                    limited = want < 1e-6
                    found.count("resolution_limited" if limited else "strict_fraction")
                    close(found, label + ("_resolution_limited" if limited else ""), reported, want, FRACTION_TOL,
                          where, floor=FRACTION_FLOOR if limited else 0.0)
    found.fe56_cumulative = fe56_fraction
    return found


def summarise(found, **extra):
    out = {"compared": found.compared, "n_failures": found.n_bad, "failures": found.bad, "counts": found.counts,
           "worst_relative": {k: finite(v) if v != math.inf else "inf" for k, v in sorted(found.worst.items())}}
    out.update(extra)
    return out


# ------------------------------------------------------------------------------ the covariance sidecar


def read_slices(npz_path, member, wanted):
    """{(offset, length): float64 array} of the element ranges `wanted` of an npz member, streamed in order."""
    out = {}
    with zipfile.ZipFile(npz_path) as zf:
        def open_stream():
            fp = zf.open(member + ".npy")
            version = np.lib.format.read_magic(fp)
            reader = (np.lib.format.read_array_header_1_0 if version == (1, 0)
                      else np.lib.format.read_array_header_2_0)
            shape, fortran, dtype = reader(fp)
            if fortran or dtype != np.dtype("<f8") or len(shape) != 1:
                raise GateError(f"{member}.npy is {shape} {dtype} fortran={fortran}; expected 1-D <f8")
            return fp, shape[0]

        def skip(fp, n_bytes):
            while n_bytes > 0:
                chunk = fp.read(min(n_bytes, 1 << 26))
                if not chunk:
                    raise GateError(f"{member}.npy ended early")
                n_bytes -= len(chunk)

        def take(fp, n_bytes):
            parts = []
            while n_bytes > 0:
                chunk = fp.read(n_bytes)
                if not chunk:
                    raise GateError(f"{member}.npy ended early")
                parts.append(chunk)
                n_bytes -= len(chunk)
            return b"".join(parts)

        fp, total = open_stream()
        position = 0
        for offset, length in sorted(set(wanted)):
            if offset + length > total:
                raise GateError(f"{member}.npy: range {offset}+{length} beyond {total}")
            if offset < position:
                fp.close()
                fp, total = open_stream()
                position = 0
            skip(fp, (offset - position) * 8)
            out[(offset, length)] = np.frombuffer(take(fp, length * 8), dtype="<f8").copy()
            position = offset + length
        fp.close()
    return out


def overlap(grid, low, high):
    """Width of each (group, bin) intersection: groups [low, high) against bins [grid[i], grid[i+1])."""
    lo = np.maximum(low[:, None], grid[None, :-1])
    hi = np.minimum(high[:, None], grid[None, 1:])
    return np.clip(hi - lo, 0.0, None)


def component_value(kind, row_grid, col_grid, values, phi, bounds, sigma_left, sigma_right):
    """One MF=33 component collapsed under the spectrum `phi` (ascending group fluxes) for two loss rows (barn^2).
    Groups carry constant flux per unit energy: density_g = phi_g / (E_hi - E_lo) / sum(phi). Relative components
    multiply the row's group cross section, absolute ones do not (a loss row is its own base row), and the
    short-range kinds LB=8 and LB=9 are diagonal in group and bin."""
    total = math.fsum(phi.tolist())
    if total <= 0.0:
        return 0.0
    active = phi > 0.0
    low, high = bounds[:-1][active], bounds[1:][active]
    density = phi[active] / (high - low) / total
    if kind in (0, 1):
        n_rows, n_cols = len(row_grid) - 1, len(col_grid) - 1
        if len(values) != n_rows * n_cols:
            raise GateError(f"component values {len(values)} != {n_rows} x {n_cols}")
        left = density * (sigma_left[active] if kind == 1 else 1.0)
        right = density * (sigma_right[active] if kind == 1 else 1.0)
        v_left = left @ overlap(row_grid, low, high)
        v_right = right @ overlap(col_grid, low, high)
        return float(v_left @ values.reshape(n_rows, n_cols) @ v_right)
    if kind in (8, 9):
        n_bins = len(row_grid) - 1
        if len(values) != n_bins:
            raise GateError(f"short-range component has {len(values)} factors for {n_bins} bins")
        seg = overlap(row_grid, low, high)  # (groups, bins)
        cov_width = (row_grid[1:] - row_grid[:-1])[None, :]
        with np.errstate(divide="ignore", invalid="ignore"):
            if kind == 8:
                variance = np.where(seg > 0.0, values[None, :] * cov_width / seg, 0.0)
            else:
                variance = np.where(seg > 0.0, values[None, :] * (1.0 - seg / cov_width), 0.0)
        weight = density[:, None] * seg
        return float(np.sum(weight * weight * variance))
    raise GateError(f"unknown covariance component kind {kind}")


class Covariance:
    """The `actinv-covariance-1` sidecar, read here: components (target, MT, MT1, LB, kind, row grid, column grid,
    value offset, value length), grid offsets/values and the flat value array."""

    def __init__(self, path, library):
        self.path = Path(path)
        text = str(self.path)
        self.index_path = Path(text[:-4] + "_index.json" if text.endswith(".npz") else text + "_index.json")
        index = json.loads(self.index_path.read_text())
        self.targets = {int(t["target"]): (int(t["za"]), int(t["liso"])) for t in index["targets"]}
        self.library = library
        with np.load(self.path) as z:
            self.components = np.array(z["components"])
            self.grid_offsets = np.array(z["grid_offsets"])
            self.grid_values = np.array(z["grid_values"])
        if self.components.ndim != 2 or self.components.shape[1] != 9:
            raise GateError(f"components has shape {self.components.shape}")
        self._values = {}

    def target_rows(self, ti):
        if self.targets.get(ti) != self.library.targets[ti]:
            raise GateError(f"covariance target {ti} is {self.targets.get(ti)}, library has {self.library.targets[ti]}")
        return self.components[self.components[:, 0] == ti]

    def preload(self, target_indices):
        wanted = []
        for ti in target_indices:
            for comp in self.target_rows(ti):
                wanted.append((int(comp[7]), int(comp[8])))
        self._values.update(read_slices(self.path, "values", wanted))

    def grid(self, index):
        return self.grid_values[self.grid_offsets[index]:self.grid_offsets[index + 1]]

    def self_covered(self, ti):
        comps = self.target_rows(ti)
        return {int(c[1]) for c in comps if c[1] == c[2]}

    def matrix(self, ti, mts, sigma_rows, phi, bounds):
        """Covariance (barn^2) between the loss rows of the MTs `mts` (one row per MT), summing every component of
        the target; a cross component adds its value to both off-diagonal entries, as the sidecar is stored once."""
        index = {mt: i for i, mt in enumerate(mts)}
        matrix = np.zeros((len(mts), len(mts)))
        for comp in self.target_rows(ti):
            mt, mt1 = int(comp[1]), int(comp[2])
            if mt not in index or mt1 not in index:
                continue
            values = self._values[(int(comp[7]), int(comp[8]))]
            value = component_value(int(comp[4]), self.grid(int(comp[5])), self.grid(int(comp[6])), values, phi,
                                    bounds, sigma_rows[mt], sigma_rows[mt1])
            matrix[index[mt], index[mt1]] += value
            if mt != mt1:
                matrix[index[mt1], index[mt]] += value
        return matrix


# ------------------------------------------------------------------------------ G5


def check_uncertainty(result, spec, ex, cov, initial):
    found = Findings()
    library = ex.library
    keys = sorted(initial)
    cov.preload([library.target_index[k] for k in keys])
    steps = result["steps"]
    schedule = ex.schedule
    unresolved, joint_evaluated, joint_skipped = [], 0, []
    per_nuclide = {}
    for key in keys:
        name = base.name_of(key)
        ti = library.target_index[key]
        loss = ex.loss_rows(key)
        by_mt = {}
        for mt, row, rate, sigma in loss:
            by_mt.setdefault(mt, []).append((row, rate, sigma))
        if any(len(v) != 1 for v in by_mt.values()):
            raise GateError(f"{name}: more than one loss row for an MT; this checker assumes one")
        covered_set = cov.self_covered(ti)
        covered = sorted(mt for mt in by_mt if mt in covered_set)
        wanted_rows = sorted(by_mt[mt][0][0] for mt in covered)
        sigma_rows = {}
        by_row = {by_mt[mt][0][0]: mt for mt in covered}
        for row, sig in library._stream(wanted_rows):
            sigma_rows[by_row[row]] = np.array(sig, dtype="<f8")
        matrix = cov.matrix(ti, covered, sigma_rows, ex.phi, library.bounds) if covered else np.zeros((0, 0))
        position = {mt: i for i, mt in enumerate(covered)}
        per_nuclide[name] = {"loss_mts": sorted(by_mt), "covered_mts": covered,
                             "uncovered_mts": sorted(set(by_mt) - set(covered))}
        for si, step in enumerate(steps):
            dt, multiplier = schedule[si]
            table = step["summary"]["depletion"]
            row = next((n for n in table["nuclides"] if n["nuclide"] == name), None)
            if row is None:
                found.fail(check="nuclide_missing", step=si + 1, nuclide=name)
                continue
            where = {"step": si + 1, "nuclide": name}
            expected = ex.channels(key, multiplier)
            lam, lam_rel = ex.decay(key)
            channel_by_name = {c["channel"]: c for c in row["channels"]}
            covered_rate, all_covered = 0.0, True
            for cname, rate in expected.items():
                channel = channel_by_name.get(cname)
                if channel is None:
                    found.fail(check="channel_missing", channel=cname, **where)
                    continue
                reported = channel.get("relative_standard_uncertainty")
                if cname == "decay":
                    if lam_rel is None:
                        all_covered = False
                        if reported is not None:
                            found.fail(check="decay_uncertainty_without_half_life_uncertainty", **where)
                    else:
                        covered_rate += rate
                        if reported is None:
                            found.fail(check="decay_channel_without_uncertainty", expected=lam_rel, **where)
                        else:
                            close(found, "decay_channel_relative_uncertainty", reported, lam_rel,
                                  UNCERTAINTY_TOL, {"channel": cname, **where})
                    continue
                mt = int(cname[2:])
                if mt not in position:
                    all_covered = False
                    found.count("uncovered_channels_without_uncertainty")
                    if reported is not None:
                        found.fail(check="uncovered_channel_has_uncertainty", channel=cname, reported=reported,
                                   **where)
                    continue
                variance = float(matrix[position[mt], position[mt]])
                if variance < 0.0:
                    unresolved.append({"nuclide": name, "channel": cname, "self_variance_barn2": variance})
                    continue
                want = math.sqrt(variance) / by_mt[mt][0][2]
                covered_rate += rate
                if reported is None:
                    found.fail(check="covered_channel_without_uncertainty", channel=cname, expected=want, **where)
                    continue
                close(found, "channel_relative_uncertainty", reported, want, UNCERTAINTY_TOL,
                      {"channel": cname, **where})
            total = math.fsum(expected.values())
            reported_share = row.get("uncertainty_covered_share")
            want_share = covered_rate / total if total > 0.0 else 0.0
            if reported_share is None:
                found.fail(check="covered_share_missing", **where)
            else:
                close(found, "uncovered_share", reported_share, want_share, UNCERTAINTY_TOL, where, floor=1e-15)
            reported_total = row.get("removal_rate_relative_standard_uncertainty")
            if total > 0.0 and all_covered:
                weight = multiplier * BARN_CM2 * ex.flux_sum  # d(rate)/d(sigma) per row, s^-1 per barn
                ws = np.array([weight if f"MT{mt}" in expected else 0.0 for mt in covered])
                variance = float(ws @ matrix @ ws) if covered else 0.0
                if lam_rel is not None and "decay" in expected:
                    variance += (expected["decay"] * lam_rel) ** 2
                want_total = math.sqrt(max(variance, 0.0)) / total
                joint_evaluated += 1
                if reported_total is None:
                    found.fail(check="joint_total_missing", expected=want_total, **where)
                else:
                    close(found, "joint_total_relative_uncertainty", reported_total, want_total, UNCERTAINTY_TOL,
                          where)
            else:
                if total > 0.0:
                    joint_skipped.append(f"{name} step {si + 1}")
                if reported_total is not None:
                    found.fail(check="joint_total_present_with_uncovered_channel", reported=reported_total, **where)
                found.count("joint_total_absent_as_expected")
    covered_channels = found.counts.get("channel_relative_uncertainty", 0)
    out = summarise(found, per_nuclide=per_nuclide, joint_total_evaluations=joint_evaluated,
                    joint_total_not_evaluable=(
                        "every nuclide has at least one channel without an MF=33 self-covariance, so the candidate "
                        "reports no joint total and the cross-MT sum cannot be compared" if joint_evaluated == 0
                        else None),
                    unresolved=unresolved[:LIST_LIMIT], n_unresolved=len(unresolved))
    out["pass"] = bool(covered_channels > 0 and found.n_bad == 0 and not unresolved)
    return out


# ------------------------------------------------------------------------------ gates


def data_inputs(spec):
    library = Path(spec["library"]["path"])
    index = Path(str(library)[:-4] + "_index.json")
    primary, fallback = base.decay_files(spec)
    out = {"library": {"path": str(library), "sha256": sha256(library)},
           "library_index": {"path": str(index), "sha256": sha256(index)},
           "decay_primary": {"path": str(primary), "sha256": sha256(primary)}}
    if fallback:
        out["decay_fallback"] = {"path": str(fallback), "sha256": sha256(fallback)}
    return out


def guarded(fn):
    try:
        return fn()
    except (RuntimeError, GateError, KeyError, subprocess.TimeoutExpired, ValueError) as error:
        return {"pass": False, "error": f"{type(error).__name__}: {error}"}


def gate_g2(ref, cand, work, base_spec, timeout):
    cases = {}
    for label, spec in (("fns_fe_5min", GATE_SPEC), ("shielding_demo", DEMO_SPEC)):
        a, b = work / f"{label}.ref.json", work / f"{label}.cand.json"
        run(ref, spec, a, timeout)
        run(cand, spec, b, timeout)
        same = base.mask_ms(a.read_bytes())[0] == base.mask_ms(b.read_bytes())[0]
        cases[label] = {"identical_apart_from_ms": same, "spec_sha256": sha256(spec)}
    short = make_spec(base_spec, mode="coupled", schedule=SHORT_SCHEDULE, depletion=False, tag="summary only")
    path = write_spec(work, "summary_only", short)
    a, b = work / "summary_only.ref.json", work / "summary_only.cand.json"
    run(ref, path, a, timeout)
    cand_result = run(cand, path, b, timeout)
    same = base.mask_ms(a.read_bytes())[0] == base.mask_ms(b.read_bytes())[0]
    absent = (all("depletion" not in step.get("summary", {}) for step in cand_result["steps"])
              and "depletion" not in cand_result["ledger"] and "depletion" not in cand_result["certificate"]
              and b'"depletion"' not in b.read_bytes())
    cases["summary_without_depletion"] = {"identical_apart_from_ms": same, "no_depletion_key_anywhere": absent}
    ok = all(v["identical_apart_from_ms"] for v in cases.values()) and absent
    return {"pass": bool(ok), "cases": cases}


def gate_g3(cand, work, base_spec, library, timeout):
    cases = {}
    plans = (("fns_unscaled", dict(mode="coupled", schedule=None, composition=None)),
             ("fns_scaled", dict(mode="coupled", schedule=SCALED_SCHEDULE, composition=None)),
             ("radioactive_initial", dict(mode="coupled", schedule=DECAY_SCHEDULE, composition=DECAY_COMPOSITION)))
    for label, plan in plans:
        def one(label=label, plan=plan):
            spec = make_spec(base_spec, tag=f"P135 G3 {label}", **plan)
            result = run(cand, write_spec(work, f"g3_{label}", spec), work / f"g3_{label}.cand.json", timeout)
            ex = Expectation(library, spec)
            initial = initial_keys(spec)
            ex.prime(set(initial))
            found = check_table(result, spec, ex, model="coupled", coupled=True, initial=initial,
                                with_covariance=False)
            extra = {"mode": result["mode"], "steps": len(result["steps"]), "nuclides": sorted(
                base.name_of(k) for k in initial)}
            ok = found.n_bad == 0 and found.counts.get("channels_checked", 0) > 0 and result["mode"] == "coupled"
            if label == "fns_scaled":
                fe56 = getattr(found, "fe56_cumulative", [])
                extra["fe56_cumulative_depleted_fraction_by_step"] = fe56
                extra["measurable_threshold"] = MEASURABLE
                extra["measurable"] = bool(fe56 and fe56[-1] >= MEASURABLE)
                ok = ok and extra["measurable"]
                loss_total = math.fsum(rate for _mt, _row, rate, _s in ex.loss_rows((26056, 0)))
                exponent = sum(loss_total * m * dt for dt, m in ex.schedule)
                extra["fe56_analytic_cumulative_fraction_no_ingrowth"] = -math.expm1(-exponent)
            if label == "radioactive_initial":
                decays = found.counts.get("decay_channel_vs_ln2_over_half_life", 0)
                extra["decay_channels_compared"] = decays
                ok = ok and decays > 0
            return {"pass": bool(ok), **summarise(found, **extra)}
        cases[label] = guarded(one)
    return {"pass": all(c.get("pass") for c in cases.values()), "cases": cases}


def gate_g4(cand, work, base_spec, library, timeout):
    cases = {}
    plans = (("fns_auto", dict(mode="auto", schedule=None)), ("fns_scaled_trace", dict(mode="trace",
                                                                                  schedule=SCALED_SCHEDULE)))
    for label, plan in plans:
        def one(label=label, plan=plan):
            spec = make_spec(base_spec, composition=None, tag=f"P135 G4 {label}", **plan)
            result = run(cand, write_spec(work, f"g4_{label}", spec), work / f"g4_{label}.cand.json", timeout)
            ex = Expectation(library, spec)
            initial = initial_keys(spec)
            ex.prime(set(initial))
            found = check_table(result, spec, ex, model="trace_analytic", coupled=False, initial=initial,
                                with_covariance=False)
            strict = found.counts.get("strict_fraction", 0)
            ok = found.n_bad == 0 and result["mode"] == "trace" and found.counts.get("channels_checked", 0) > 0
            if label == "fns_scaled_trace":
                ok = ok and strict > 0
            return {"pass": bool(ok), **summarise(found, mode=result["mode"], steps=len(result["steps"]),
                                                  strict_fractions=strict,
                                                  resolution_limited=found.counts.get("resolution_limited", 0))}
        cases[label] = guarded(one)
    return {"pass": all(c.get("pass") for c in cases.values()), "cases": cases}


def gate_g5(cand, work, base_spec, library, timeout):
    def one():
        digest = sha256(COVARIANCE)
        uncertainty = {"covariance": {"path": str(COVARIANCE), "sha256": digest}, "responses": ["heat.total"]}
        spec = make_spec(base_spec, mode="coupled", schedule=SHORT_SCHEDULE, composition=None,
                         uncertainty=uncertainty, tag="P135 G5")
        result = run(cand, write_spec(work, "g5", spec), work / "g5.cand.json", timeout)
        ex = Expectation(library, spec)
        initial = initial_keys(spec)
        ex.prime(set(initial))
        table = check_table(result, spec, ex, model="coupled", coupled=True, initial=initial, with_covariance=True)
        cov = Covariance(COVARIANCE, library)
        unc = check_uncertainty(result, spec, ex, cov, initial)
        unc["table_consistency"] = summarise(table)
        unc["covariance_sha256"] = digest
        unc["tolerance"] = UNCERTAINTY_TOL
        unc["pass"] = bool(unc["pass"] and table.n_bad == 0)
        return unc
    return guarded(one)


# ------------------------------------------------------------------------------ self-check


def self_check():
    """Offline checks of the collapse helpers against hand-derived values (no binary, no data)."""
    bounds = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    phi = np.full(4, 1.25e23)
    ones = np.ones(4)
    one_bin = np.array([1.0, 5.0])
    # one relative bin covering the library, perfectly correlated: rel = sqrt(0.04) = 0.2 at sigma = 1 b
    v = component_value(1, one_bin, one_bin, np.array([0.04]), phi, bounds, ones, ones)
    assert abs(math.sqrt(v) - 0.2) < 1e-14, v
    # absolute: weight fractions sum to 1, so the value is the matrix entry
    v = component_value(0, one_bin, one_bin, np.array([0.25]), phi, bounds, ones, ones)
    assert abs(v - 0.25) < 1e-14, v
    # LB=8 over one bin equal to a group: weight 1/4 each, F * cov_width / seg * (1/4)^2 * seg^2 * ... summed over
    # the four groups against the single 4 eV bin: seg = 1, cov_width = 4
    v = component_value(8, one_bin, one_bin, np.array([0.5]), phi, bounds, ones, ones)
    assert abs(v - 4 * (0.25 ** 2) * 0.5 * 4 / 1.0 * 1.0) < 1e-14, v
    v = component_value(9, one_bin, one_bin, np.array([0.5]), phi, bounds, ones, ones)
    assert abs(v - 4 * (0.25 ** 2) * 0.5 * (1.0 - 1.0 / 4.0)) < 1e-14, v
    # a shifted spectrum: sigma = (1, 2, 3, 4) b with equal flux, mean 2.5 b, perfect correlation 10 %
    sigma = np.array([1.0, 2.0, 3.0, 4.0])
    v = component_value(1, one_bin, one_bin, np.array([0.01]), phi, bounds, sigma, sigma)
    assert abs(math.sqrt(v) / 2.5 - 0.1) < 1e-14, v
    assert decay_constant((10.0, 1.0, 0)) == (math.log(2.0) / 10.0, 0.1)
    assert decay_constant((0.0, 0.0, 1)) == (0.0, None)
    print("P135 self-check: PASS")
    return 0


# ------------------------------------------------------------------------------ main


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reference")
    ap.add_argument("--candidate")
    ap.add_argument("--workdir", default="target/p135-work")
    ap.add_argument("--out", default="results/p135_depletion.json")
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args(argv)
    if args.self_check:
        return self_check()
    if not args.reference or not args.candidate:
        ap.error("--reference and --candidate are required")
    ref = str(Path(args.reference).resolve())
    cand = str(Path(args.candidate).resolve())
    work = (ROOT / args.workdir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    base_spec = json.loads(GATE_SPEC.read_text())
    library = base.Library(ROOT / base_spec["library"]["path"])

    gates = {g: {"pass": None, "external": True} for g in ("G0", "G1", "G6")}
    gates["G0"]["note"] = "protocol SHA-256 in protocols/protocol_hash.txt (external)"
    gates["G1"]["note"] = "fmt, clippy -D warnings, workspace tests (external)"
    gates["G6"]["note"] = "CI green on the pushed commit; committed baselines unchanged (external)"
    gates["G2"] = guarded(lambda: gate_g2(ref, cand, work, base_spec, args.timeout))
    gates["G3"] = gate_g3(cand, work, base_spec, library, args.timeout)
    gates["G4"] = gate_g4(cand, work, base_spec, library, args.timeout)
    gates["G5"] = gate_g5(cand, work, base_spec, library, args.timeout)

    judged = ("G2", "G3", "G4", "G5")
    verdict = all(gates[g].get("pass") is True for g in judged)
    spec_inputs = guarded(lambda: data_inputs(absolute(base_spec)))
    record = {"protocol": "protocols/ACTINV-P135_PROTOCOL.md", "protocol_sha256": sha256(PROTOCOL),
              "reference_sha256": sha256(ref), "candidate_sha256": sha256(cand),
              "checker_sha256": sha256(__file__), "inputs": spec_inputs, "gates": gates,
              "verdict": "PASS" if verdict else "FAIL"}
    out = ROOT / args.out
    out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    for g in judged:
        print(f"P135 {g}: {'PASS' if gates[g].get('pass') else 'FAIL'}")
        for label, case in (gates[g].get("cases") or {}).items():
            if "error" in case:
                print(f"  {g}/{label}: ERROR {case['error']}")
            elif "compared" in case:
                print(f"  {g}/{label}: {'PASS' if case.get('pass') else 'FAIL'}, {case['compared']} comparisons, "
                      f"{case['n_failures']} failures")
        if "error" in gates[g]:
            print(f"  {g}: ERROR {gates[g]['error']}")
        if g == "G5" and "compared" in gates[g]:
            print(f"  G5: {gates[g]['compared']} comparisons, {gates[g]['n_failures']} failures, "
                  f"joint totals evaluated {gates[g]['joint_total_evaluations']}")
    print("P135 G0, G1, G6: external (not evaluated here)")
    print(f"P135: {record['verdict']} ({out})")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
