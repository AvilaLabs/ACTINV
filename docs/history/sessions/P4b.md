# ACTINV P4b — archived session close, 2026-08-27

**Protocol:** protocols/ACTINV-P4b_PROTOCOL.md (72b1955c…), hashed before any correction.
**Verdict (controls/check_p4b.py): P4b-PASS.**

| control | result |
|---|---|
| C1 non-inelastic vs pointwise collapse | **PASS** — 757 reactions, 4.3e-16 |
| C1 inelastic internal consistency (loss = Σ isomer partials) | **PASS** — 14 reactions, 0.0 |
| C2 subset vs full library, 132 FNS experiments | **PASS** — 2.5e-6 against a 1e-4 physical threshold |
| no regression: G1, G2a, G3, G4 | **PASS** |
| G2c (out of scope) | still FAIL — real, bounded, flagged, routed to P10 |

**What this phase did and did not do.** It corrected two controls that compared the wrong quantities, each with the
evidence that showed the premise was wrong before the fix was written. It did not touch the library, the solver, any
physics, or the failing G2c threshold, and it did not rewrite P4's P4-FAIL close. The distinction the record has to
preserve: a control may be corrected when its premise is demonstrably wrong; a threshold may not be moved because a
result missed it.

**State after P4/P4b:** full TENDL-2023 library (2,847 targets, 164,315 rows, 0 errors) built entirely by ACTINV's own
pipeline, validated on 132 FNS decay-heat experiments at median C/E 1.035 against the licensed reference's 1.009, with
every input hashed and every C/E re-derivable. One documented limitation. Next: P5.
