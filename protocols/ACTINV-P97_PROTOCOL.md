# ACTINV-P97 — Flux tally-error channel with the ledger band name restored (successor to P93 and P96)

Date: 2026-09-30. Status: **frozen before the changed code is written.**

## Why this protocol exists

P93 (`41cfc21b…`, candidate `d12ba06a…`) failed two gates.

- **G2(b):** the three uncertainty examples are not bitwise identical to the reference. The one
  difference is `ledger.uncertainty.band_name`, which the candidate dropped. The candidate's
  ledger record is built from a shared runtime record, and it lost that key; the certificate's
  record kept it. The defect is real: the ledger stopped naming the MF=33 band.
- **G3:** failed as registered, on unconverged central differences at late cooling steps where
  |R| ≤ 2.4 × 10⁻¹³ of the peak. See P96.
- G2(a), G2(c) and G4 passed. G1 passed.

P96 (`f9e4ef16…`) fixed the candidate as `d12ba06a…` and carried P93's G2 over whatever its
outcome, so P96 fails on G2. Its G3 was stopped once P93's G2(b) diagnosis made the P96 FAIL
certain. At that point only 8 of its 42 nominal (plain, flux-only) pairs had run and no
comparison had been computed. P96's fresh spec set (index offset 10, seed base 20261001) is
therefore still unseen, and this protocol uses it.

## Change under test

Branch `p93-tally-error`: P93's change plus one line. The ledger's uncertainty record gains
`band_name` from the same runtime value the certificate uses. That is `"MF=33 nuclear-data band"`
whenever MF=33 runs, and the flux-only band name in flux-only mode. The candidate is the
release `actinv` built from that branch.

## Gates

Reference: release `actinv` `0d8dc849…`, archived as `target/p93/ref_actinv`. Checkers:
`controls/check_p93.py` for G1, G2 and G4, with its two G3 fixes and the G2 return-code
indexing fix; `controls/check_p96.py` for G3, with its candidate-hash pin updated to this
candidate. Everything is rerun on this candidate; no P93 or P96 evidence carries over.

- **G0:** the protocol hash is registered before the change is written.
- **G1:** P93's G1: fmt, clippy `-D warnings`, `cargo test --release -p actinv-core -p actinv-data`,
  and P93's named unit tests.
- **G2:** P93's G2 in full, all of which must pass:
  - (a) 783 P75b specs and 3 mesh profiles bitwise;
  - (b) the three uncertainty examples bitwise;
  - (c) nominal invariance at 1e-12 on P93's G3 spec set.
- **G3:** P96's G3 exactly, on its offset-10 set:
  - seed base 20261001, h = 1e-4, h′ = 1e-3;
  - exclusion rules (a) and (b), each capped at 5 %;
  - at least 99 % of remaining comparisons within tolerance, and all within 100×.
- **G4:** P93's G4 rerun: the P32 cube, K = 64, same seed, same pass bands.
- **G5:** CI replay, every step exits 0.

Merge only if G0–G5 all pass. A FAIL stands; thresholds are not lowered afterwards.
