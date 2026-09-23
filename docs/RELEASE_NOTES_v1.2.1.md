# ACTINV v1.2.1 — release notes

ACTINV 1.2.1 is a patch release. It corrects the self-shielding interpolation, turns several silent
input and data problems into errors or ledger entries, and updates `rustls` past RUSTSEC-2026-0285.
There are no new features. Schemas and interfaces are unchanged apart from the stricter validation
listed below, and the embedded catalog stays `data-v1.1.0`.

Upgrade the Python package with:

```bash
python -m pip install --upgrade actinv
```

Rust CLI users can install this exact version with:

```bash
cargo install --locked --force actinv-cli --version 1.2.1
```

## Results to re-check

- **Self-shielded runs.** The σ₀ interpolation weight was mirrored (`sigma0_b` is descending). Any
  shielded run whose effective σ₀ fell strictly inside the table grid used factors from the wrong point
  in its interval: all `dilution: "composition"` runs, and any fixed σ₀ other than the grid ends. For
  example, σ₀ = 1e3 b returned the 1e4 b row. This includes the shielded rows folded into MF=33
  uncertainty. Runs at σ₀ = 0.1 b or ≥ 1e10 b were unaffected.
- **Compositions with a misspelt or abundance-free element key** (`"Fee"`, `"Tc"`). These silently
  contributed zero atoms, and with `atom_fraction` the remaining members were renormalised. They now
  fail validation.
- **Study refinement, robustness and comparison on array responses** (`inventory_per_nuclide`,
  `photon_source_per_group`). Refinement reported `satisfied` and robustness reported `0 ± 0`
  regardless of the data. These consumers now require one of the three scalar responses.
- **Runs with JEFF-3.3 or UKDD-2020 as the primary decay library.** When a state's branching ratios
  summed to less than 1, the unassigned fraction of its decays vanished; JEFF-3.3 Ir-169, for example,
  lists only its 45% alpha branch. The default ENDF/B-VIII.0 data is unaffected and gives byte-identical
  results.

## What changes

- **Decay-branch accounting.** A branching shortfall beyond a 1e-5 rounding allowance is routed to
  leakage. An excess is scaled away, so decay never creates atoms. Both cases are listed in
  `ledger.decay_branching_sums_off_unity`, which appears only when needed. Radioactive records with no
  decay modes, and branches that resolve to their own parent, also go to leakage.
- **Non-finite results fail.** A run whose result holds NaN or infinity now stops with the field's path
  (for example `steps[3].heat_W_per_g.total is NaN`). Previously the value was written as JSON `null`.
- **New validation errors:**
  - a positive `spectrum.total` with an all-zero shape;
  - a duration longer than 64 characters;
  - `self_shielding` on a charged-projectile spec;
  - study names containing `__`;
  - more than 4096 `robustness.samples`;
  - a per-step study `spectrum`;
  - duplicate (ZA, LISO) decay records;
  - a resolved-range NAPS other than 0 or 1;
  - MF=6/8/9 products without a reaction;
  - MF=33 blocks over the 1 GB array cap;
  - imported meshes over 100 million cells;
  - `--grid-density` above 64.
- **Robustness:**
  - `actinv data fetch` has network timeouts.
  - A cache lock left by a killed process is reclaimed.
  - Mesh resume streams its output.
  - Hash-pinned inputs are parsed from exactly the bytes that were hashed.
  - Trace-mode heat and photon sums no longer depend on hash-map order.
  - `build-damage`/`build-shielding` caches are keyed by projectile and code version.
  - Fission-yield files use the canonical ENDF number parser.
- **Python binding.** `run`, `reverse`, `cram_step` and `broaden` release the GIL. `broaden` raises
  `ValueError` instead of panicking on bad input.
- **Desktop and web workbench.** Numeric fields refuse "nan" and "inf". Results load off the UI thread.

## Security

- `rustls` 0.23.45 in both lockfiles (RUSTSEC-2026-0285, reachable through `actinv data fetch`). v1.2.0
  wheels and `cargo install --locked` builds carry the vulnerable 0.23.43.
- The library crates and the `actinv` binary declare `#![forbid(unsafe_code)]`.

## Qualification boundary

Unchanged — `docs/DATA_LIMITATIONS.md` and `docs/QUALIFICATION.md` apply as before. The full list of
changes is in `CHANGELOG.md`.
