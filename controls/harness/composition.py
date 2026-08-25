"""Element wt-% -> isotopic atoms per gram. Abundance and mass tables are copied from openmc.data at build time
into results/tables/abundance_mass.json with openmc's citation (independent re-verification deferred to P3)."""
import os, json, re
NA = 6.02214076e23
_T = None
def tables(path=None):
    global _T
    if _T is None:
        path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "results", "tables", "abundance_mass.json")
        if not os.path.exists(path):
            import openmc.data
            os.makedirs(os.path.dirname(path), exist_ok=True)
            ab = {}; mass = {}
            for nuc, a in openmc.data.NATURAL_ABUNDANCE.items():
                el = re.match(r"([A-Za-z]+)", nuc).group(1); ab.setdefault(el, {})[nuc] = a
            for el, d in ab.items():
                for nuc in d: mass[nuc] = openmc.data.atomic_mass(nuc)
            json.dump({"source": "openmc.data.NATURAL_ABUNDANCE and openmc.data.atomic_mass (OpenMC %s; abundances per Meija et al., Pure Appl. Chem. 88 (2016); masses AME2020 via openmc mass data)" % openmc.__version__,
                       "abundance": ab, "mass_amu": mass}, open(path, "w"), indent=1)
        _T = json.load(open(path))
    return _T
def za(nuc):
    m = re.match(r"([A-Za-z]+)(\d+)(_m(\d+))?", nuc); from .elements import Z_OF; return Z_OF[m.group(1)] * 1000 + int(m.group(2))
def liso(nuc):
    m = re.match(r"([A-Za-z]+)(\d+)(_m(\d+))?", nuc); return int(m.group(4)) if m.group(4) else 0
def atoms_per_gram(elements_wt):
    """elements_wt: {EL: wt%} -> dict (ZA, 0) -> atoms per gram, plus diagnostics."""
    T = tables(); out = {}; diag = {"elements": {}}
    for el, w in elements_wt.items():
        el_cap = el.capitalize(); ab = T["abundance"].get(el_cap)
        if not ab: diag["elements"][el] = "no abundance data"; continue
        M = sum(a * T["mass_amu"][n] for n, a in ab.items())  # g/mol
        moles = (w / 100.0) / M
        for n, a in ab.items(): out[(za(n), liso(n))] = out.get((za(n), liso(n)), 0.0) + NA * moles * a   # natural Ta-180 is the isomer Ta180_m1
        diag["elements"][el] = {"M_g_mol": M, "atoms_per_g": NA * moles, "n_isotopes": len(ab), "abundance_sum": sum(ab.values())}
    return out, diag
