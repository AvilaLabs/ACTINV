# P133 — Trace mode held radioactive initial material undepleted

Registered 2026-10-15, before any change to mode selection. The defect was found during P124 integration (the
synthetic decay-only test) and confirmed on the base release. Parking entry 2026-10-15.

## Defect

The trace formulation treats every initial nuclide as a constant reservoir. `options.mode: "auto"` chooses trace
when the largest bulk burn-up fraction is below 1e-6, but that fraction counts only reaction losses, not decay. An
initial composition containing radionuclides (an `atoms_per_g` restart inventory, or an activated material
continued as a cooling run) is therefore solved in trace mode with its radionuclides held constant.

Evidence, base release `2f3daa4` (SHA-256 `3d34d7ca…2b5f`): Co-60 at 1e20 atoms/g, zero flux, one 5.27-year step,
`auto`. The run selects trace and reports Co-60 activity 4.167e11 Bq/g, where the expected value after one half-life
is 2.083e11. Ni-60 is reported as 6.930e19 atoms/g, where the expected value is 5.0e19. Explicit `"trace"` gives the
same.

## Change under test

1. Auto selection also computes, for each bulk nuclide, the decay depth λ_eff·T, where T is the total schedule time
   and λ_eff honours `decay_scale` and the P124 half-life overrides. It chooses coupled when the decay fraction
   1−e^(−λT) is ≥ 1e-6, the same threshold as burn-up.
2. Explicit `"trace"` with a bulk decay fraction ≥ 1e-6 is refused with a message naming the nuclide.
3. The ledger gains `bulk_decay_fraction_max` and `bulk_decay_nuclide`, serialized only when the fraction is
   ≥ 1e-6. Results for initial materials without such a nuclide are byte-identical.

## Gates

Checker: `controls/check_p133.py`.

- **G0.** This protocol's SHA-256 is in `protocols/protocol_hash.txt` in a commit before the fix.
- **G1.** fmt, clippy with `-D warnings`, and the workspace tests, including:
  - auto choosing coupled for radioactive bulk
  - explicit trace refused
  - stable bulk unchanged
  - the P124 decay-only test passing
- **G2, unchanged where not triggered.** `examples/fns_fe_5min.json` (natural Fe) and every example spec without a
  triggering radionuclide are byte-identical apart from `ms`. A natural-composition case containing K-40 (KCl) is
  included.
- **G3, the defect case.** For the Co-60 probe, the candidate selects coupled and reports:
  - Co-60 activity = λN0·2^(−t/T½) within 1e-9 relative
  - Ni-60 = N0·(1−2^(−t/T½)) within 1e-9
- **G4, an FNS restart case.** The FNS Fe step-1 inventory is used as `atoms_per_g` with a 1-day cooling step and
  `auto`. The candidate matches the full FNS spec's cooling from the same inventory (coupled) within 1e-9 relative
  on nuclides above 1e-12 of the total.
- **G5.** CI green on the pushed commit, with the committed run-output baselines unchanged. Any sealed baseline that
  does move is listed and explained before it is regenerated.

A gate that fails is recorded as P133-FAIL with its evidence; repairs need an amendment registered first.
