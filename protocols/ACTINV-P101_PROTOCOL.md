# ACTINV-P101 — Photonuclear activation, P100 with G5b at the reference's printed precision

Date: 2026-10-01. Status: **frozen before any P101 gate is run.**

## Why this protocol exists

P100 (`85b8966d…`) failed G5b as frozen. P100 checked FISPACT-II's processed MT5 values against
rule R within 1e-5 relative, on a stated premise that "the processed files carry 7 significant
digits". **That premise is false.** In `tal2017-g/gxs-162`, the MF=10 data fields use a
10-character E format.
- A two-digit exponent leaves 5 significant digits (`1.0659E-12`); a one-digit exponent leaves 6
  (`2.92425E-8`).
- Half a unit in the 5th digit is up to 5e-5 relative.

The P100 run found 751 of 1,002 sections within 1e-5 and the rest within 4.36e-5. Every
deviation inspected sits in a value printed with 5 digits. This was observed before this protocol
was written, and the tolerance below is chosen knowing it.

**P100's other results, recorded in ledger Entry 76:**
- G1 PASS, on candidate `b70d4953…` (branch `p100-gamma`, `2a555cb`).
- G2 PASS.
- G5a PASS: 95 of 95 non-MT5 values within 2e-3, maximum 1.26e-6.
- **G3 not completed.** The checker's comparator was killed by the 6 GB cap before comparing: it
  held about 5e5 × 162 values as Python floats. It now compares SHA-256 digests of each row's exact
  bytes.
- **G4.** The TENDL-2025 half passed. The TENDL-2017 half matched every value to 1e-13, but its
  extension count was one higher for Nb-93, Ta-181 and W-186. The independent `left_value_at`
  returned a table's first-point value at its own first energy, where the left limit is 0. That
  triggered a zero-width extension at MTs whose states start exactly at E3. The checker now uses
  the true left limit, and the summed value for the added points.

**The checkers changed after a P100 run.** Both changes correct the checker's implementation of
P100's text, not its thresholds, and both are disclosed here.

## Change under test

Identical to P100: branch `p100-gamma`, the MF=3 threshold extension. No builder code changes.

## Gates

Identical to P100's gates, run afresh under `ACTINV_GAMMA_PROTOCOL=P101` into `target/p101/` and
`results/p101_verdict.json`. The reference is P100's (`target/p101/ref_actinv`, master release
`c1777524…`). G0–G4, G5a and G6 are word for word P100's; nothing carries over from the P100 run.
Only G5b changes:

- **G5b, the FISPACT-II MT5 rule at printed precision.**
  - Scope and rule R are as in P100: every MF=10 MT5 section with ZAP > 1 in the 8 processed
    records, every group below 200 MeV where either value is at least 1e-12 b, with the raw state
    recovered by rank.
  - Each processed group value F is read together with its printed field text. Let u(F) be one
    unit in the last printed digit, from the mantissa's decimal places and the exponent.
  - Pass if |F − R| ≤ u(F) for every compared group.
  - A printed zero with R ≥ 1e-12 b fails.
  - u(F) is one unit, not half: half a unit covers rounding, and an equal allowance covers the
    reference's own arithmetic. The largest |F − R| / u(F) is reported.

Merge only if G0–G6 all pass. A FAIL stands; thresholds are not lowered afterwards.
