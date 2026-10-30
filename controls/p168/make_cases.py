#!/usr/bin/env python3
"""Write the raw case_<name>.json high-precision references (mpmath; no ACTINV needed), then thin them into
results/p168/refs with p168.thin. This is the only place besides refs.py that needs mpmath.

Usage: make_cases.py [--raw-dir DIR] [--refs-dir DIR] [--verify] [CASE_NUMBER ...]   (default: all of 1..7, ~1 min)
  --verify  regenerate into --raw-dir, thin into a temporary directory and compare every file's SHA-256 with
            results/p168/refs_manifest.json (needs all cases).
"""
import argparse, hashlib, json, os, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "controls"))
import mpmath as mp  # noqa: E402
from p168 import refs, thin  # noqa: E402
from p168.refs import Model, YEAR_S, fmt  # noqa: E402

OUT = str(ROOT / "target" / "p168-raw")
mp.mp.dps = 80
LN2 = mp.log(2)


def t7(x):
    """Round to 7 significant digits (the precision an ENDF 11-column field can carry) -> decimal string."""
    return mp.nstr(mp.mpf(x), 7, min_fixed=mp.inf, max_fixed=-mp.inf, strip_zeros=True).replace("e+", "e")


def nuc(name, Z, A, liso=0, T12=None):
    return {"name": name, "za": 1000 * Z + A, "liso": liso, "awr": f"{A * 0.9914:.4f}", "T12": T12}


def hl(value, unit):
    f = {"s": 1, "min": 60, "h": 3600, "d": 86400, "y": YEAR_S, "us": mp.mpf("1e-6")}[unit]
    return t7(mp.mpf(value) * f)


def sched_item(dt_s, mult, label=""):
    dt = mp.mpf(dt_s)
    spec = repr(float(dt)) + " s"
    return {"dt_s": dt_s if isinstance(dt_s, str) else mp.nstr(dt, 40, min_fixed=mp.inf, max_fixed=-mp.inf),
            "dt_spec": spec, "flux_multiplier": mult, "label": label}


def run(name, desc, model, n0, sched, mass=1.0, frozen=(), dps=400, extra=None, mode="coupled"):
    t0 = time.time()
    steps = [(s["dt_s"], s["flux_multiplier"]) for s in sched]
    res, worst = refs.checked_propagate(model, n0, steps, dps, frozen)
    ex = {"actinv_mode": mode, "precision_cross_check_max_rel_diff": fmt(worst, 3)}
    if extra:
        ex.update(extra)
    doc = refs.case_json(name, desc, model, mass, n0, sched, res, ex)
    refs.dump(doc, os.path.join(OUT, f"case_{name}.json"))
    print(f"case_{name}.json  ({time.time() - t0:.1f}s, cross-check {mp.nstr(worst, 3)})", flush=True)
    return res


def decay_model(nuclides, decays, reactions=(), flux="0"):
    return Model(nuclides, decays, list(reactions), flux)


# ---------------------------------------------------------------- case 1: U-238 chain
def case1():
    spec = [("U238", 92, 238, 0, "4.468e9", "y", ("Th234", 4)),
            ("Th234", 90, 234, 0, "24.1", "d", ("Pa234m1", 1)),
            ("Pa234m1", 91, 234, 1, "1.17", "min", ("U234", 1)),
            ("U234", 92, 234, 0, "2.455e5", "y", ("Th230", 4)),
            ("Th230", 90, 230, 0, "7.54e4", "y", ("Ra226", 4)),
            ("Ra226", 88, 226, 0, "1600", "y", ("Rn222", 4)),
            ("Rn222", 86, 222, 0, "3.8235", "d", ("Po218", 4)),
            ("Po218", 84, 218, 0, "3.098", "min", ("Pb214", 4)),
            ("Pb214", 82, 214, 0, "26.8", "min", ("Bi214", 1)),
            ("Bi214", 83, 214, 0, "19.9", "min", ("Po214", 1)),
            ("Po214", 84, 214, 0, "164.3", "us", ("Pb210", 4)),
            ("Pb210", 82, 210, 0, "22.2", "y", ("Bi210", 1)),
            ("Bi210", 83, 210, 0, "5.012", "d", ("Po210", 1)),
            ("Po210", 84, 210, 0, "138.376", "d", ("Pb206", 4))]
    nucs = [nuc(n, Z, A, l, hl(v, u)) for n, Z, A, l, v, u, _ in spec] + [nuc("Pb206", 82, 206)]
    decays = {n: [(d, rt, 0, "1")] for n, _, _, _, _, _, (d, rt) in spec}
    model = decay_model(nucs, decays)
    N0 = "2.53e21"
    sched = [sched_item(mp.nstr(mp.mpf(k) * YEAR_S, 30, min_fixed=mp.inf, max_fixed=-mp.inf), "0", lab)
             for k, lab in [(10 ** 3, "1e3 y"), (10 ** 6, "1e6 y"), (10 ** 9, "1e9 y")]]
    for s in sched:
        s["dt_s"] = str(int(mp.mpf(s["dt_s"])))
    extra = {"note_steps": "step durations are 1e3, 1e6, 1e9 years run consecutively (cumulative 1e3, 1.001e6, 1.001001e9 y)"}
    res = run("1a_u238_chain_pure_parent", "U-238 series, 14 radioactive members + stable Pb-206, pure parent, decay only, "
              "steps of 1e3/1e6/1e9 y", model, {"U238": N0}, sched, mass=1.0, dps=500, extra=extra, mode="coupled")
    # independent closed-form cross-check (distinct rates -> Bateman) at cumulative times
    mp.mp.dps = 900
    lams = [LN2 / mp.mpf(n["T12"]) for n in nucs[:-1]] + [mp.mpf(0)]
    t = mp.mpf(0)
    worst = mp.mpf(0)
    for s, v in zip(sched, res):
        t += mp.mpf(s["dt_s"])
        b = refs.bateman_chain(lams, mp.mpf(N0), t)
        for x, y in zip(v, b):
            if abs(y) > mp.mpf("1e-60") * mp.mpf(N0):
                worst = max(worst, abs(x - y) / abs(y))
    mp.mp.dps = 80
    assert worst < mp.mpf("1e-30"), worst
    print("  Bateman agreement (max rel diff, members > 1e-60 N0):", mp.nstr(worst, 3))
    # pre-equilibrated: secular equilibrium A_i = A_0, Pb-206 empty
    n0eq = {n["name"]: fmt(mp.mpf(N0) * (LN2 / mp.mpf(nucs[0]["T12"])) / (LN2 / mp.mpf(n["T12"])), 30)
            for n in nucs[:-1]}
    run("1b_u238_chain_equilibrated", "Same chain started in secular equilibrium (all activities equal, Pb-206 empty); "
        "probes preservation of equilibrium over 1e9 y while the parent decays by ~15%", model, n0eq, sched,
        dps=500, extra=extra, mode="coupled")


# ---------------------------------------------------------------- case 2: very short times
def case2():
    pairs = [("a", ("Na", 11, 30), ("Mg", 12, 30), ("Al", 13, 30), "6.931472e9", "6.931472e-10",
              "lam 1e-10 -> 1e9 (slow parent, very fast daughter)"),
             ("b", ("Sc", 21, 50), ("Ti", 22, 50), ("V", 23, 50), "6.931472e-10", "6.931472e9",
              "lam 1e9 -> 1e-10 (very fast parent, near-stable daughter)"),
             ("c", ("Ga", 31, 70), ("Ge", 32, 70), ("As", 33, 70), "0.6931472", "0.6931465",
              "lam 1.0 -> 1.000001 (nearly equal rates, Bateman cancellation)"),
             ("d", ("Nb", 41, 90), ("Mo", 42, 90), ("Tc", 43, 90), "693.1472", "693.1472",
              "lam 1e-3 = 1e-3 (exactly equal: defective Jordan block)"),
             ("e", ("Cs", 55, 140), ("Ba", 56, 140), ("La", 57, 140), "6.931472e-10", "6.931472e-10",
              "lam 1e9 = 1e9 (equal, very fast)")]
    nucs, decays, n0 = [], {}, {}
    for tag, a, b, c, ta, tb, _ in pairs:
        na, nb, nc = f"{a[0]}{a[2]}", f"{b[0]}{b[2]}", f"{c[0]}{c[2]}"
        nucs += [nuc(na, a[1], a[2], 0, ta), nuc(nb, b[1], b[2], 0, tb), nuc(nc, c[1], c[2])]
        decays[na] = [(nb, 1, 0, "1")]
        decays[nb] = [(nc, 1, 0, "1")]
        n0[na] = "1e20"
    model = decay_model(nucs, decays)
    durs = [("1e-9", "1 ns"), ("1e-6", "1 us"), ("1e-3", "1 ms"), ("1", "1 s"), ("1000", "1e3 s")]
    sched = [sched_item(d, "0", lab) for d, lab in durs]
    # per-pair independent check of the equal-rate cases against the analytic Jordan form
    res = run("2_very_short_times", "Five independent A->B->C (C stable) decay chains with rates 1e-10..1e9 s^-1 incl. equal "
              "and nearly equal pairs; steps 1 ns, 1 us, 1 ms (then 1 s, 1e3 s), consecutive; decay only",
              model, n0, sched, dps=300, mode="coupled",
              extra={"note": "each step is a separate propagation interval; cumulative times 1e-9, 1.001e-6, 1.001001e-3, ..."})
    mp.mp.dps = 80
    t = mp.mpf(0)
    names = [n["name"] for n in nucs]
    for s, v in zip(sched, res):
        t += mp.mpf(s["dt_s"])
        lam = LN2 / mp.mpf("693.1472")
        iB = names.index("Mo90")
        analytic = mp.mpf("1e20") * lam * t * mp.exp(-lam * t)
        assert abs(v[iB] - analytic) / analytic < mp.mpf("1e-40")
    print("  equal-rate pair d agrees with N0*lam*t*exp(-lam*t)")


# ---------------------------------------------------------------- case 3: Co-59 high flux
def case3():
    nucs = [nuc("Co59", 27, 59), nuc("Co60m1", 27, 60, 1, hl("10.47", "min")), nuc("Co60", 27, 60, 0, hl("5.2714", "y")),
            nuc("Co61", 27, 61, 0, hl("1.65", "h")), nuc("Ni60", 28, 60), nuc("Ni61", 28, 61)]
    decays = {"Co60m1": [("Co60", 3, 0, "0.9976"), ("Ni60", 1, 0, "0.0024")], "Co60": [("Ni60", 1, 0, "1")],
              "Co61": [("Ni61", 1, 0, "1")]}
    rx = [{"src": "Co59", "mt": 102, "products": [("Co60m1", "10"), ("Co60", "10")]},
          {"src": "Co60", "mt": 102, "products": [("Co61", "2")]}]
    n0 = {"Co59": "1.0219e22"}
    h = 3600
    sched = ([sched_item(str(h), "1", "irradiate to 1 h"), sched_item(str(8765 * h), "1", "irradiate to 1 y"),
              sched_item(str(int(9 * YEAR_S)), "1", "irradiate to 10 y")] +
             [sched_item(str(d), "0", lab) for d, lab in
              [(h, "cool 1 h"), (23 * h, "cool 1 d"), (29 * 86400, "cool 30 d"),
               (int(335.25 * 86400), "cool 1 y"), (int(9 * YEAR_S), "cool 10 y")]])
    for phi in ["1e14", "1e15", "1e16"]:
        model = Model(nucs, decays, rx, phi)
        run(f"3_co59_flux{phi}", f"Co-59(n,g) with 20 b total (10 b to Co-60m, 10 b to Co-60), Co-60(n,g) 2 b to Co-61, flux {phi}; "
            "irradiate to 1 h, 1 y, 10 y then cool; Co-59 burns out (sigma*phi*t = 63 at 1e16/10 y)",
            model, n0, sched, dps=400, mode="coupled")


# ---------------------------------------------------------------- case 4: pulse train
def mat_pow(M, n):
    R = mp.eye(M.rows)
    B = M
    while n:
        if n & 1:
            R = R * B
        B = B * B
        n >>= 1
    return R


def case4():
    sigma, phi = "98.65", "1e20"
    nucs = [nuc("Au197", 79, 197), nuc("Au198", 79, 198, 0, hl("2.6947", "d")), nuc("Hg198", 80, 198)]
    decays = {"Au198": [("Hg198", 1, 0, "1")]}
    rx = [{"src": "Au197", "mt": 102, "products": [("Au198", sigma)]}]
    model = Model(nucs, decays, rx, phi)
    N0 = "3.057e21"
    ton = "1e-9"
    for tag, period, npulse in [("0p1s_1e4", "0.1", 10 ** 4), ("0p1s_1e6", "0.1", 10 ** 6), ("1s_1e6", "1", 10 ** 6)]:
        toff = str(mp.mpf(period) - mp.mpf(ton))
        toff = mp.nstr(mp.mpf(period) - mp.mpf(ton), 20, min_fixed=mp.inf, max_fixed=-mp.inf, strip_zeros=True)
        toff = toff.rstrip("0") if "." in toff else toff
        mp.mp.dps = 400
        D, R = model.matrices()
        Eon = refs.expm((D + R) * mp.mpf(ton), 300)
        Eoff = refs.expm(D * mp.mpf(toff), 300)
        P = Eoff * Eon
        v0 = mp.matrix([mp.mpf(N0), 0, 0])
        lam = LN2 / mp.mpf(nucs[1]["T12"])
        rate = mp.mpf(sigma) * refs.BARN * mp.mpf(phi)
        T = mp.mpf(period)
        q = mp.exp(-lam * T)
        Rp = mp.mpf(N0) * rate  # atoms/s of Au-198 production, undepleted Au-197
        A_ss = Rp * (1 - mp.exp(-lam * mp.mpf(ton))) / (1 - q)  # periodic steady-state activity at end of pulse, Bq/g
        checkpoints = []
        n = 1
        while n <= npulse:
            checkpoints.append(n)
            n *= 10
        rows = []
        for n in checkpoints:
            Pn1 = mat_pow(P, n - 1)
            end_pulse = Eon * (Pn1 * v0)
            end_period = P * (Pn1 * v0)
            c = (Rp / lam) * (1 - mp.exp(-lam * mp.mpf(ton)))
            x_cf = c * (1 - q ** n) / (1 - q)  # undepleted-target closed form, N198 at end of pulse n
            rows.append({
                "pulse_count": n, "t_end_of_pulse_s": fmt((n - 1) * T + mp.mpf(ton)), "t_end_of_period_s": fmt(n * T),
                "end_of_pulse": {nm: fmt(end_pulse[i]) for i, nm in enumerate(model.names)},
                "end_of_period": {nm: fmt(end_period[i]) for i, nm in enumerate(model.names)},
                "au198_activity_end_of_pulse_Bq_per_g": fmt(lam * end_pulse[1]),
                "closed_form_undepleted_N198_end_of_pulse": fmt(x_cf),
                "closed_form_undepleted_activity_Bq_per_g": fmt(lam * x_cf),
                "exact_over_closed_form_minus_1": fmt(end_pulse[1] / x_cf - 1, 6),
                "fraction_of_periodic_steady_state_activity": fmt(lam * end_pulse[1] / A_ss, 12)})
        doc = {"case": f"4_pulse_train_{tag}",
               "description": "Au-197(n,g)Au-198 (2.6947 d) -> stable Hg-198; N pulses of 1 ns (flux 1e20 n/cm2/s during the pulse, "
                              "sigma 98.65 b -> 1e-11 captures per atom per pulse), period " + period + " s. Reference by exact "
                              "period-propagator powers; per-step values only for the 1e4 case.",
               "mass_g": 1.0, "nuclides": [{"name": n["name"], "za": n["za"], "liso": n["liso"], "awr": n["awr"], "half_life_s": n["T12"]} for n in nucs],
               "decays": {"Au198": [{"to": "Hg198", "rtyp": 1, "rfs": 0, "branching": "1"}]},
               "reactions": [{"from": "Au197", "mt": 102, "products": [{"to": "Au198", "sigma_b": sigma}], "loss_sigma_b": sigma}],
               "flux_n_cm2_s": phi, "initial_atoms_per_g": {"Au197": N0},
               "schedule": {"pulse_train": {"n_pulses": npulse, "on": {"dt_s": ton, "dt_spec": "1e-9 s", "flux_multiplier": "1"},
                                            "off": {"dt_s": toff, "dt_spec": toff + " s", "flux_multiplier": "0"},
                                            "steps_total": 2 * npulse,
                                            "note": "ACTINV needs 2*n_pulses explicit schedule steps (on, off, on, off, ...)"}},
               "periodic_steady_state": {
                   "formula": "A_ss(end of pulse) = R (1 - exp(-lam t_on)) / (1 - exp(-lam T)), R = N(Au197) * sigma*phi*1e-24 (undepleted)",
                   "lambda_per_s": fmt(lam), "A_ss_Bq_per_g": fmt(A_ss),
                   "fraction_of_steady_state_reached_after_all_pulses": rows[-1]["fraction_of_periodic_steady_state_activity"]},
               "reference_checkpoints": rows}
        if npulse == 10 ** 4:
            # full per-step reference by plain stepping (independent of the matrix-power route), checked against it
            steps = [(ton, "1"), (toff, "0")] * npulse
            res, worst = refs.checked_propagate(model, {"Au197": N0}, steps, 120)
            per = []
            for k, v in enumerate(res):
                per.append([fmt(x) for x in v] + [fmt(lam * v[1])])
            doc["reference_per_step_columns"] = ["Au197", "Au198", "Hg198", "Au198_activity_Bq_per_g"]
            doc["reference_per_step"] = per
            mp.mp.dps = 120
            c = rows[-1]
            last_on = res[2 * npulse - 2]
            assert abs(last_on[1] - mp.mpf(c["end_of_pulse"]["Au198"])) / last_on[1] < mp.mpf("1e-25")
            doc["precision_cross_check_max_rel_diff"] = fmt(worst, 3)
        refs.dump(doc, os.path.join(OUT, f"case_4_pulse_train_{tag}.json"))
        print(f"case_4_pulse_train_{tag}.json  steady-state fraction at end {rows[-1]['fraction_of_periodic_steady_state_activity']}", flush=True)
        mp.mp.dps = 80


# ---------------------------------------------------------------- case 5: loops and defective
def case5():
    # 5a: A -(n,g)-> B ; B -(n,2n)-> A ; B -beta-> C
    nucs = [nuc("Mn55", 25, 55), nuc("Mn56", 25, 56, 0, hl("2.5789", "h")), nuc("Fe56", 26, 56)]
    decays = {"Mn56": [("Fe56", 1, 0, "1")]}
    sched = ([sched_item(str(d), "1", lab) for d, lab in
              [(86400, "irr to 1 d"), (29 * 86400, "irr to 30 d"), (int(335.25 * 86400), "irr to 1 y"),
               (int(9 * YEAR_S), "irr to 10 y")]] +
             [sched_item(str(d), "0", lab) for d, lab in [(3600, "cool 1 h"), (9 * 3600, "cool 10 h")]])
    for tag, s2n, note in [("weak", "0.5", "realistic (n,2n) 0.5 b"), ("strong", "7.5e4",
                                                                      "synthetic (n,2n) 7.5e4 b so the reverse rate equals the Mn-56 decay constant at 1e15")]:
        rx = [{"src": "Mn55", "mt": 102, "products": [("Mn56", "13.3")]}, {"src": "Mn56", "mt": 16, "products": [("Mn55", s2n)]}]
        run(f"5a_loop_{tag}", f"Mn-55 <-> Mn-56 loop (n,g)/(n,2n), Mn-56 -> Fe-56 decay, flux 1e15, {note}",
            Model(nucs, decays, rx, "1e15"), {"Mn55": "1.0948e22"}, sched, dps=300, mode="coupled")
    # 5b: isomer loop, ground<->metastable
    nucs = [nuc("Ir191", 77, 191), nuc("Ir192m1", 77, 192, 1, hl("1.45", "min")), nuc("Ir192", 77, 192, 0, hl("73.829", "d")),
            nuc("Pt192", 78, 192), nuc("Os192", 76, 192)]
    decays = {"Ir192m1": [("Ir192", 3, 0, "0.9998"), ("Pt192", 1, 0, "0.0002")],
              "Ir192": [("Pt192", 1, 0, "0.9513"), ("Os192", 2, 0, "0.0487")]}
    rx = [{"src": "Ir191", "mt": 102, "products": [("Ir192m1", "300"), ("Ir192", "654")]},
          {"src": "Ir192", "mt": 4, "products": [("Ir192m1", "0.2")]},
          {"src": "Ir192", "mt": 16, "products": [("Ir191", "2")]},
          {"src": "Ir192m1", "mt": 16, "products": [("Ir191", "2")]}]
    sched = ([sched_item(str(d), "1", lab) for d, lab in
              [(3600, "irr to 1 h"), (86400 - 3600, "irr to 1 d"), (29 * 86400, "irr to 30 d"), (int(335.25 * 86400), "irr to 1 y")]] +
             [sched_item(str(d), "0", lab) for d, lab in [(600, "cool 10 min"), (86400 - 600, "cool 1 d"), (29 * 86400, "cool 30 d")]])
    for tag, phi in [("", "1e15"), ("_strong", "1e19")]:
        run(f"5b_isomer_loop{tag}", f"Ir-191(n,g) -> Ir-192m1 (300 b) / Ir-192 g (654 b); Ir-192g(n,n')Ir-192m1 0.2 b; "
            f"Ir-192m1 IT -> Ir-192g (1.45 min); (n,2n) 2 b back to Ir-191 from both states; flux {phi}",
            Model(nucs, decays, rx, phi), {"Ir191": "3.153e21"}, sched, dps=300, mode="coupled")
    # 5c: defective: capture rate == decay constant of the daughter
    nucs = [nuc("Mn55", 25, 55), nuc("Mn56", 25, 56, 0, "9284.04"), nuc("Fe56", 26, 56)]
    lamB = LN2 / mp.mpf("9284.04")
    mp.mp.dps = 60
    phi = mp.nstr(lamB / refs.BARN, 50, min_fixed=mp.inf, max_fixed=-mp.inf)  # sigma = 1 b -> a == lamB to 50 digits
    mp.mp.dps = 80
    rx = [{"src": "Mn55", "mt": 102, "products": [("Mn56", "1")]}]
    sched = [sched_item(str(d), "1", f"irr to {d} s") for d in (1000, 9000, 90000)] + [sched_item("10000", "0", "cool 1e4 s")]
    res = run("5c_defective_capture_decay", "Mn-55 -(n,g)-> Mn-56 -> Fe-56 with sigma*phi equal to the Mn-56 decay constant (Jordan block); "
              f"sigma = 1 b, flux = {phi[:20]}... n/cm2/s (reference uses the full 50-digit value)",
              Model(nucs, {"Mn56": [("Fe56", 1, 0, "1")]}, rx, phi), {"Mn55": "1e22"}, sched, dps=300, mode="coupled")
    t = mp.mpf(1000)
    N = mp.mpf("1e22")
    an = N * lamB * t * mp.exp(-lamB * t)
    assert abs(res[0][1] - an) / an < mp.mpf("1e-40")
    print("  5c agrees with N0 a t exp(-a t)")
    # 5d: three-member equal-half-life decay chain (Jordan block of size 3)
    nucs = [nuc("Co58", 27, 58, 0, "1000"), nuc("Ni58", 28, 58, 0, "1000"), nuc("Cu58", 29, 58, 0, "1000"), nuc("Zn58", 30, 58)]
    dec = {"Co58": [("Ni58", 1, 0, "1")], "Ni58": [("Cu58", 1, 0, "1")], "Cu58": [("Zn58", 1, 0, "1")]}
    sched = [sched_item(d, "0", f"{d} s") for d in ("100", "900", "9000", "90000")]
    res = run("5d_defective_decay_triple", "Three radioactive members of identical half-life 1000 s in a chain (Jordan block size 3), decay only",
              Model(nucs, dec, [], "0"), {"Co58": "1e22"}, sched, dps=300, mode="coupled")
    lam = LN2 / 1000
    t = mp.mpf(100)
    assert abs(res[0][2] - mp.mpf("1e22") * (lam * t) ** 2 / 2 * mp.exp(-lam * t)) / res[0][2] < mp.mpf("1e-40")
    print("  5d agrees with N0 (lam t)^2/2 exp(-lam t)")


# ---------------------------------------------------------------- case 6: dynamic range
def case6():
    nucs = [nuc("Si28", 14, 28), nuc("Si29", 14, 29), nuc("Na23", 11, 23), nuc("Na24", 11, 24, 0, "693.1472"),
            nuc("Mg24", 12, 24, 0, "6.931472e14"), nuc("Al24", 13, 24)]
    decays = {"Na24": [("Mg24", 1, 0, "1")], "Mg24": [("Al24", 1, 0, "1")]}
    rx = [{"src": "Si28", "mt": 102, "products": [("Si29", "0.17")]}, {"src": "Na23", "mt": 102, "products": [("Na24", "0.53")]}]
    n0 = {"Si28": "1e22", "Na23": "1e13"}
    h = 3600
    sched = [sched_item(str(h), "1", "irradiate 1 h")] + [sched_item(str(d), "0", lab) for d, lab in
                                                          [(600, "cool 10 min"), (3000, "cool 1 h"), (23 * h, "cool 1 d"), (86400 * 29, "cool 30 d")]]
    model = Model(nucs, decays, rx, "1e14")
    desc = ("1 kg (mass_g = 1000, so 1e25 atoms) stable Si-28 matrix at 1e22 atoms/g with a 1 ppb Na-23 impurity (1e13 atoms/g); "
            "Na-23(n,g)Na-24 with a synthetic lam = 1e-3 s^-1, then Na-24 -> Mg-24 (synthetic lam 1e-15 s^-1) -> Al-24 stable; "
            "flux 1e14. Al-24 is about 1e-12 of the Mg-24 inventory, ~1e-30 of the matrix.")
    run("6a_dynamic_range_coupled", desc + " Full coupled reference (matrix and impurity deplete).", model, n0, sched, mass=1000.0,
        dps=500, mode="coupled")
    run("6b_dynamic_range_trace", desc + " Trace-mode reference: Si-28 and Na-23 held constant (their loss terms removed, production kept); "
        "differs from 6a at the 1e-7 relative level through the impurity depletion.", model, n0, sched, mass=1000.0, frozen=("Si28", "Na23"),
        dps=500, mode="trace", extra={"trace_reservoir_nuclides": ["Si28", "Na23"]})


# ---------------------------------------------------------------- case 7: burn-up
def case7():
    nucs = [nuc("W186", 74, 186), nuc("W187", 74, 187, 0, hl("23.72", "h")), nuc("Re187", 75, 187),
            nuc("Re188", 75, 188, 0, hl("17.0", "h")), nuc("Os188", 76, 188)]
    decays = {"W187": [("Re187", 1, 0, "1")], "Re188": [("Os188", 1, 0, "1")]}
    rx = [{"src": "W186", "mt": 102, "products": [("W187", "38")]}, {"src": "Re187", "mt": 102, "products": [("Re188", "75")]}]
    sched = [sched_item(str(int(k * YEAR_S / 2)), "1", f"irr {k / 2} y") for k in [1]] + \
            [sched_item(str(int(YEAR_S * x)), "1", lab) for x, lab in [(0.5, "irr to 1 y"), (1, "irr to 2 y"), (1, "irr to 3 y"), (1, "irr to 4 y"), (1, "irr to 5 y")]] + \
            [sched_item(str(d), "0", lab) for d, lab in [(86400, "cool 1 d"), (9 * 86400, "cool 10 d"), (int(355.25 * 86400), "cool 1 y")]]
    sched[0]["label"] = "irr to 0.5 y"
    n0 = {"W186": "3.2376e21"}
    run("7_tungsten_burnup", "W-186(n,g)W-187 (38 b; 23.72 h) -> Re-187 (treated as stable) (n,g) Re-188 (75 b; 17.0 h) -> Os-188; "
        "flux 1e14 for 5 y (sigma*phi*t = 0.60 for W-186, 45% of the target transmuted); then cooling",
        Model(nucs, decays, rx, "1e14"), n0, sched, dps=300, mode="coupled")


def main():
    global OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="*", choices=["1", "2", "3", "4", "5", "6", "7"])
    ap.add_argument("--raw-dir", default=OUT)
    ap.add_argument("--refs-dir", default=str(ROOT / "results" / "p168" / "refs"))
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    OUT = a.raw_dir
    os.makedirs(OUT, exist_ok=True)
    which = a.cases or ["1", "2", "3", "4", "5", "6", "7"]
    for w in which:
        globals()["case" + w]()
    if a.verify:
        if len(which) != 7:
            sys.exit("--verify needs every case")
        with tempfile.TemporaryDirectory() as temporary:
            thin.build_refs(OUT, temporary)
            recorded = json.loads((ROOT / "results" / "p168" / "refs_manifest.json").read_text())["files"]
            fresh = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(temporary).glob("*.json"))}
        print("verify:", "MATCH" if fresh == recorded else "DIFFERENT")
        sys.exit(0 if fresh == recorded else 1)
    thin.build_refs(OUT, a.refs_dir)
    thin.write_manifest(a.refs_dir, Path(a.refs_dir).parent / "refs_manifest.json", mp.__version__)
    print("thinned references written to", a.refs_dir)


if __name__ == "__main__":
    main()
