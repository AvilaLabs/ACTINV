# ACTINV P12 Amendment A — primary-table provenance in the P10 legacy hash

**Date:** 2026-08-27. **Trigger:** The first GitHub Actions run after P12-G2 failed
`controls/g6_p10_projectile_runtime.py`: the normalized legacy-neutron result was
`faad8e6478b698bb2ca61efedb9907099135ffe27a837ac9397ebd07524a2bdc`, not the frozen
`0ed6be999d63820556d91ad73ab73fa7980f9b37dca8fcc00dd4c351f7cd1b1c`.

A recursive comparison against the retained pre-P12 result, after the already authorized timing, working-path and
solver-version normalization, found exactly one changed leaf. `certificate.tables_provenance` changed from the
historical OpenMC-derived attribution to the Meija/AME2020 primary-source attribution and exact source hashes required
by P12-G2. No physics value, material conversion, schema field, ledger entry, certificate input hash or other string
changed. This exposed a conflict between P12's legacy-result preservation sentence and G2's requirement that every
certificate carry the corrected primary provenance.

## Frozen repair

1. The P10-G6 legacy comparison must first require `certificate.tables_provenance` to equal the nonempty `source`
   value in `results/tables/abundance_mass.json`. It then maps only that checked leaf to the frozen pre-P12 provenance
   text before deriving the unchanged P10 normalized hash. The existing solver, timing and working-path rules remain.
2. A planted alteration to the current table provenance must fail before hashing. No arbitrary provenance, missing
   leaf or broad certificate-key deletion is accepted.
3. The exact P10-G6 control, P10 checker, P12 G1-G2 controls, dependency and release-note checks, self-contained-clone
   control and all four strict Rust commands are rerun before this checkpoint is pushed.

This is a normalization of intentionally corrected provenance after an exact-source assertion, not a relaxation of
legacy physics or interface compatibility. The repair record makes the eventual P12 verdict **P12-CONDITIONAL**.
