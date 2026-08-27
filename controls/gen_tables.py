#!/usr/bin/env python3
"""Regenerate crates/actinv-data/src/tables.rs from results/tables/abundance_mass.json (P5).
The Rust core embeds the tables so a run needs no external table file; the P5-G2 control checks the embedded
values against the Python harness on every FNS composition."""
import json, os, re
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
T = json.load(open(os.path.join(ROOT, "results", "tables", "abundance_mass.json")))
rows = []
for el, iso in sorted(T["abundance"].items()):
    for nuc, a in sorted(iso.items()):
        m = re.match(r"([A-Za-z]+)(\d+)(_m(\d+))?", nuc)
        rows.append((el, int(m.group(2)), int(m.group(4)) if m.group(4) else 0, a, T["mass_amu"][nuc]))
src = ["//! Natural isotopic abundances and atomic masses, generated from results/tables/abundance_mass.json.",
       "//! DO NOT EDIT — regenerate with controls/gen_tables.py. Provenance is carried in `PROVENANCE` and is written",
       "//! into every run certificate, so a result names the data it used.", "",
       "pub const PROVENANCE: &str = " + json.dumps(T["source"]) + ";", "",
       "/// (element symbol, mass number, isomeric state, natural abundance atom fraction, atomic mass in amu)",
       "pub const ISOTOPES: &[(&str, i32, i32, f64, f64)] = &["]
for el, a, liso, ab, m in rows: src.append(f'    ("{el}", {a}, {liso}, {ab!r}, {m!r}),')
src += ["];", ""]
open(os.path.join(ROOT, "crates", "actinv-data", "src", "tables.rs"), "w").write("\n".join(src))
print("tables.rs:", len(rows), "isotopes")
