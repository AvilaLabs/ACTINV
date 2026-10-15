# P124 — Input parity: in-run spectrum conversion, WALL, PARTITION, named overrides, decay-only runs

Registered 2026-10-15, before any P124 result is produced, under the owner-approved FISPACT-II parity plan
(2026-10-15). Base commit: `2f3daa4`. Tracker rows: GRPCONVERT/CNVTYPE/LIMGRP, WALL, PARTITION, OVER
(ACROSS/ALAM/ADCROSS/ADLAM), decay-only runs. (MIND and NOSTABLE closed with P122.)

## Change under test

All of the following are opt-in:

1. `spectrum.rebin {convention, emin_eV, emax_eV}` converts a supplied spectrum to the library structure inside the
   run. The ledger records the conversion and the flux-conservation residual.
2. `schedule[].wall_loading_MW_m2` gives the flux by FISPACT's rule: the multiplier is the wall loading divided by a
   declared reference loading. Without a reference it falls back to the 4.44e13 n/cm²/s per MW/m² equivalence. The
   ledger names the basis used.
3. `schedule[].partition {element: fraction_kept}` is applied at step start. Removed atoms go to the removal sink.
   Coupled mode is forced, and an explicit trace mode is refused.
4. `overrides`: per-nuclide absolute or scaled one-group cross sections by MT, half-lives, and relative
   uncertainties. They are applied at network assembly and listed in the ledger.
5. `library` and `spectrum` may be omitted when no step irradiates (a decay-only run).

## Gates

Checker: `controls/check_p124.py`. Reference: `2f3daa4`. Candidate: release build of the integrated implementation.

- **G0.** This protocol's SHA-256 is in `protocols/protocol_hash.txt` in a commit before any P124 evidence.
- **G1.** fmt, clippy with `-D warnings`, and the workspace tests, including the new flux, overrides, spec and run
  tests.
- **G2, unchanged by default.** `examples/fns_fe_5min.json` is byte-identical apart from `ms`.
- **G3, rebin.**
  - The FNS Fe spectrum is aggregated by the checker to `fispact-175` (equal-lethargy split), then converted back
    by the run to 709 groups.
  - Total flux is conserved within 1e-12 relative.
  - The run's converted spectrum, read from the ledger, equals an independent Python implementation of both
    conventions within 1e-12.
  - The irradiation result is compared with the native-709 run (report-only ratio table).
- **G4, WALL.** Steps with `wall_loading_MW_m2` give results byte-identical (apart from `ms`, the echoed spec and the
  ledger `wall_loading` block) to the same spec with the equivalent explicit flux multiplier, for both bases.
- **G5, PARTITION.**
  - With FNS Fe plus a cooling step with `partition {Mn: 0.25}`, every Mn nuclide's atoms just after the partition
    are exactly 0.25 × the value before (relative 1e-14).
  - The removal sink grows by exactly the removed atoms.
  - Other elements are unchanged.
- **G6, overrides.**
  - A `half_life` override equal to the decay-file value is byte-identical apart from echoes.
  - Halving Mn56's half-life doubles its λ and changes its activity as an independent single-nuclide recomputation
    predicts, within 1e-9.
  - A `cross_section` override on Fe56 MT=103 equal to the inspect-reported one-group value is identical apart from
    echoes. A ×2 `cross_section_scale` doubles the Mn56 production rate in an independent recomputation, within 1e-9.
- **G7, decay-only.**
  - A decay-only spec (FNS Fe step-1 inventory as `atoms_per_g`, cooling steps only, no library) is accepted.
  - Its inventory equals the same cooling computed by the full spec from that inventory, within 1e-12 relative, on
    nuclides above 1e-15 of total.
- **G8.** CI green on the pushed commit, with the committed run-output baselines unchanged.

A gate that fails is recorded as P124-FAIL with its evidence; repairs need an amendment registered first.

## Not claimed

No GRPCONVERT of fluxes outside the destination range: such flux is reported, not redistributed.
