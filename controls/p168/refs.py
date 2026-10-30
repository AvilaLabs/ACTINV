"""High-precision reference solutions for dN/dt = A N (decay + one-group transmutation).

Everything is computed from the *decimal input strings* of the case (half-lives, cross sections, flux),
so the reference and ACTINV see the same inputs. Matrix exponential: scaling-and-squaring with a Taylor
series at a working precision far above the dynamic range (mpmath), cross-checked at a second precision;
for chains with distinct rates an independent Bateman closed form is also provided.
"""
import json
import mpmath as mp

YEAR_S = mp.mpf("31557600")  # 365.25 d, ACTINV's year
BARN = mp.mpf("1e-24")
OUT_DIGITS = 30


def fmt(x, digits=OUT_DIGITS):
    """Decimal string with `digits` significant digits, scientific notation, arbitrary exponent."""
    x = mp.mpf(x)
    if x == 0:
        return "0"
    s = mp.nstr(x, digits, min_fixed=mp.inf, max_fixed=-mp.inf, strip_zeros=False)
    return s


class Model:
    """nuclides: list of dict(name, za, liso, T12 (decimal string seconds or None), awr)
    decays: dict name -> list of (dst or None, rtyp, rfs, br)
    reactions: list of dict(src, mt, products=[(dst, sigma_b_str)])
    flux: decimal string, n/cm2/s, multiplied by the step multiplier."""

    def __init__(self, nuclides, decays, reactions, flux):
        self.nuclides = nuclides
        self.names = [n["name"] for n in nuclides]
        self.idx = {n: i for i, n in enumerate(self.names)}
        self.decays = decays
        self.reactions = reactions
        self.flux = flux

    def lam(self, name):
        for n in self.nuclides:
            if n["name"] == name:
                return mp.log(2) / mp.mpf(n["T12"]) if n["T12"] else mp.mpf(0)
        raise KeyError(name)

    def matrices(self, frozen=()):
        """(D, R): decay matrix and per-unit-multiplier reaction matrix. `frozen` nuclides (trace mode
        reservoir) keep their production terms but lose nothing."""
        n = len(self.names)
        D = mp.zeros(n, n)
        R = mp.zeros(n, n)
        for nuc in self.nuclides:
            lam = self.lam(nuc["name"])
            if lam == 0:
                continue
            i = self.idx[nuc["name"]]
            modes = self.decays.get(nuc["name"], [])
            tot = sum(mp.mpf(m[3]) for m in modes)
            assert abs(tot - 1) < mp.mpf("1e-40"), (nuc["name"], tot)
            if nuc["name"] not in frozen:
                D[i, i] -= lam
            for dst, rtyp, rfs, br in modes:
                if dst is not None:
                    D[self.idx[dst], i] += lam * mp.mpf(br)
        for r in self.reactions:
            i = self.idx[r["src"]]
            for dst, sig in r["products"]:
                rate = mp.mpf(sig) * BARN * mp.mpf(self.flux)
                if r["src"] not in frozen:
                    R[i, i] -= rate
                if dst is not None:
                    R[self.idx[dst], i] += rate
        return D, R


def expm(A, dps):
    """exp(A) by scaling and squaring with Taylor terms, at working precision dps."""
    with mp.workdps(dps):
        n = A.rows
        nrm = max(sum(abs(A[i, j]) for i in range(n)) for j in range(n))
        s = 0
        if nrm > mp.mpf("0.25"):
            s = int(mp.ceil(mp.log(nrm / mp.mpf("0.25"), 2)))
        B = A / mp.mpf(2) ** s
        E = mp.eye(n)
        term = mp.eye(n)
        k = 1
        eps = mp.mpf(10) ** (-dps - 5)
        while True:
            term = (term * B) / k
            E = E + term
            if max(abs(term[i, j]) for i in range(n) for j in range(n)) < eps:
                break
            k += 1
        for _ in range(s):
            E = E * E
        return E


def propagate(model, n0, steps, dps=400, frozen=()):
    """n0: dict name -> decimal string; steps: list of (dt_seconds_str, multiplier_str).
    Returns list of state vectors (mp lists) at each step end."""
    mp.mp.dps = dps
    D, R = model.matrices(frozen)
    cache = {}
    n = len(model.names)
    v = mp.matrix([mp.mpf(n0.get(nm, "0")) for nm in model.names])
    out = []
    t = mp.mpf(0)
    for dt, m in steps:
        key = (dt, m)
        if key not in cache:
            cache[key] = expm((D + R * mp.mpf(m)) * mp.mpf(dt), dps)
        v = cache[key] * v
        out.append([v[i] for i in range(n)])
    return out


def checked_propagate(model, n0, steps, dps=400, frozen=()):
    """Propagate at dps and 2*dps; require 40-digit agreement wherever the value is above the
    precision-limited floor (values below max*10^-(dps-60) are compared absolutely)."""
    a = propagate(model, n0, steps, dps, frozen)
    b = propagate(model, n0, steps, 2 * dps, frozen)
    mp.mp.dps = dps
    worst = mp.mpf(0)
    for va, vb in zip(a, b):
        mx = max(abs(x) for x in vb)
        floor = mx * mp.mpf(10) ** (-(dps - 60))
        for x, y in zip(va, vb):
            if abs(y) < floor and abs(x) < floor:
                continue
            rel = abs(x - y) / max(abs(x), abs(y))
            worst = max(worst, rel)
    assert worst < mp.mpf("1e-40"), f"precision cross-check failed: {worst}"
    return b, worst


def bateman_chain(lams, n0, t):
    """Pure-parent linear chain 0->1->..., distinct lams (last may be 0 = stable). Returns list of N_i(t).
    Independent closed form (cancellation-prone; needs high dps)."""
    n = len(lams)
    out = []
    for i in range(n):
        if i == n - 1 and lams[i] == 0:
            # stable end of chain: conservation
            out.append(n0 - sum(out))
            continue
        pref = n0 * mp.fprod(lams[:i]) if i else n0
        s = mp.mpf(0)
        for j in range(i + 1):
            den = mp.fprod([lams[k] - lams[j] for k in range(i + 1) if k != j])
            s += mp.exp(-lams[j] * t) / den
        out.append(pref * s)
    return out


def case_json(name, description, model, mass_g, n0, sched, results, extra=None):
    """sched: list of dict(dt_s str, dt_spec str, mult str, label). results: states per step (mp lists)."""
    mp.mp.dps = 80
    lam = {n["name"]: model.lam(n["name"]) for n in model.nuclides}
    t = mp.mpf(0)
    steps = []
    for k, (s, v) in enumerate(zip(sched, results)):
        t += mp.mpf(s["dt_s"])
        inv = {nm: fmt(v[i]) for i, nm in enumerate(model.names)}
        act = {nm: fmt(lam[nm] * v[i]) for i, nm in enumerate(model.names) if lam[nm] != 0}
        steps.append({"step": k + 1, "t_end_s": fmt(t), "label": s.get("label", ""),
                      "inventory_atoms_per_g": inv, "activity_Bq_per_g": act,
                      "total_activity_Bq_per_g": fmt(sum(lam[nm] * v[i] for i, nm in enumerate(model.names)))})
    doc = {
        "case": name, "description": description, "mass_g": mass_g,
        "conventions": {"year_s": "31557600", "ln2": fmt(mp.log(2), 40), "barn_cm2": "1e-24",
                        "rate_per_s": "sigma_b * 1e-24 * flux_n_cm2_s * flux_multiplier",
                        "inventory_unit": "atoms per gram of material (linear in the initial atoms)",
                        "precision": "mpmath scaling-and-squaring Taylor expm; per-case working precision of 120 to 500 digits, cross-checked at twice that (precision_cross_check_max_rel_diff); output 30 significant digits"},
        "nuclides": [{"name": n["name"], "za": n["za"], "liso": n["liso"], "awr": n["awr"],
                      "half_life_s": n["T12"], "lambda_per_s": fmt(lam[n["name"]]) if lam[n["name"]] != 0 else "0"}
                     for n in model.nuclides],
        "decays": {k: [{"to": d, "rtyp": r, "rfs": (model.nuclides[model.idx[d]]["liso"] if d else f), "branching": b}
                       for d, r, f, b in v] for k, v in model.decays.items()},
        "reactions": [{"from": r["src"], "mt": r["mt"], "products": [{"to": d, "sigma_b": s} for d, s in r["products"]],
                       "loss_sigma_b": fmt(sum(mp.mpf(s) for _, s in r["products"]))} for r in model.reactions],
        "flux_n_cm2_s": model.flux,
        "initial_atoms_per_g": n0,
        "schedule": sched,
        "reference_steps": steps,
    }
    if extra:
        doc.update(extra)
    return doc


def dump(doc, path):
    with open(path, "w") as f:
        json.dump(doc, f, indent=1)
        f.write("\n")
