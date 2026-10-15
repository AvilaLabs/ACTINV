# P131 — `actinv inspect` (FISPACT-II PRINTLIB / EXTRACTXS parity)

Registered 2026-10-15, before any P131 result is produced, under the owner-approved FISPACT-II parity plan
(2026-10-15). Base commit: `2f3daa4`. Tracker row: library printing and cross-section extraction.

## Change under test

A new read-only command family, `actinv inspect {decay, xs, yields, library, groups}`, with JSON output by default
and `--table` for a fixed-width table. It reports:

- decay records: half-life, modes, branching, daughters, mean energies and discrete γ totals
- activation-library reactions: group cross sections, plus the one-group collapse and reaction rate for a supplied
  spectrum, through the same collapse path the solver uses
- independent and cumulative (MT=459) fission yields at a requested energy, through the solver's interpolation
- library structure
- named group boundaries

No solver code path changes. `chain::step_of` and `chain::rtyp_digits` become public, and
`library::read_npz_structure` is added.

## Gates

Checker: `controls/check_p131.py`, run by the coordinator under the 6 GiB cgroup. Candidate: release build of the
P131 implementation commit.

- **G0.** This protocol's SHA-256 is in `protocols/protocol_hash.txt` in a commit before any P131 evidence.
- **G1.** `cargo fmt --all -- --check`, clippy with `-D warnings`, and the workspace tests. The 18 inspect unit tests
  cover parsing, table formatting, every subcommand's JSON shape, the collapse and cumulative yields.
- **G2, decay, independent parse.** `inspect decay` on the installed ENDF/B-VIII.0 decay file for Co-60, Cs-137,
  Am-241, Cf-252, Tc-99m and Fe-55 agrees with an independent Python MF=8 MT=457 parse:
  - half-life, branching ratios and mean energies exactly as stored values
  - daughters identified correctly
  - total discrete γ energy per decay within 1e-12 relative
- **G3, cross sections, independent collapse.** For the gate library (`actinv-data/v1.1.0`, TENDL-2025 709 group)
  and the FNS Fe 5-minute spectrum (`examples/fns_fe_5min.json`), `inspect xs --target Fe56 --spectrum` gives
  one-group cross sections for every reported reaction that equal an independent NumPy collapse of the NPZ arrays
  (Σσ_gφ_g / Σφ_g) within 1e-12 relative. The reaction rate equals σ̄·1e-24·Σφ to the same tolerance.
- **G4, solver consistency.** The one-group (n,p) rate for Fe-56 from `inspect xs` equals the rate implied by the
  solver for the same spec, taken from the result's reaction-rate or ledger field if one exists. If no such field
  exists, this gate is reported as not applicable, with the reason.
- **G5, yields.** `inspect yields` for U-235 at 0.0253 eV (independent) and 0.0253 eV (`--cumulative`) agrees with an
  independent Python MF=8 MT=454/459 parse of the installed ENDF/B-VIII.0 nfpy file within 1e-12 relative for every
  product; `--energy` between tabulated points matches the documented interpolation.
- **G6.** CI green on the pushed commit, with the committed run-output baselines unchanged.

A gate that fails is recorded as P131-FAIL with its evidence; repairs need an amendment registered first.

## Not claimed

No change to any solver result. Printed MT labels cover common reactions only; others print the MT number.
