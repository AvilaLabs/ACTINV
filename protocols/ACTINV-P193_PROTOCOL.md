# ACTINV-P193 — A per-reaction choice of evaluation, scored leave-one-facility-out on UKAEA's validation table

## Purpose

P192 found FISPACT-II's published TENDL-2017 values closer than ACTINV's TENDL-2025 values to UKAEA's 365 measured
effective cross sections. ACTINV's and FISPACT-II's calculations agree at the median, so the gap is in the data: a
minority of reactions, mostly product-state branchings, where TENDL-2025 moved away from the measurements. Stage 3 of
the accuracy program is a per-reaction choice among evaluations. The question:

> If each reaction's evaluation is chosen among TENDL-2025, TENDL-2023, TENDL-2017 and ENDF/B-VIII.1 by a fixed
> rule fitted on two of the three facilities in the table (FNG, JAEA-FNS, TU Dresden), how close is ACTINV to the
> measurements at the third, against TENDL-2025 alone and against FISPACT-II's published values?

The answer is a measurement. A choice that does not help is still a PASS of this protocol.

## Disclosure of what has been seen

- All of P192: its record, its decision, and the post-hoc diagnostic in ledger 365. That covers every row's measured
  value, FISPACT-II's value and ACTINV's TENDL-2025 value, and the twelve rows where ACTINV is furthest behind.
- For nine reactions, raw TENDL-2025 cross sections at 14.1 MeV were compared with ACTINV's library.
- No value from TENDL-2023, TENDL-2017 or ENDF/B-VIII.1 for any row. One rehearsal was read for exit status, gate
  results and counts only: values per library (TENDL-2025 365, TENDL-2023 362, TENDL-2017 365, ENDF/B-VIII.1 291),
  held-out rows per fold (JAEA-FNS 72, FNG 102, TU Dresden 19), and G2 passing. Its output is deleted.
- An inventory found no unseen measured cross-section set on the workstation: P17's IRDFF-II tables, P167's Řež rates
  and P172's FNS data were all scored before. Leave-one-facility-out within P192's table is therefore the test, and
  the choice can never see the facility it is scored on.
- EAF-2010, also built, is not a candidate: it was adjusted to UKAEA's integral data, likely including these rows.

## Inputs (pinned in `controls/check_p193.py`, never tracked)

- P192's table and 23 spectra (P192's pins), read through `controls/p192.py` (P192's sealed file), with P192's label
  mapping and its equal-lethargy spectrum conversion.
- Four ACTINV-format 709-group neutron libraries with identical group boundaries:
  - TENDL-2025, the default (`ec4c72bf…`);
  - TENDL-2023 (`a9f90234…`);
  - TENDL-2017 (`21815670…`), a partial build of about 1,070 targets;
  - ENDF/B-VIII.1 (`ecaca7c0…`), P172's build, with few product-state rows.
- The ACTINV binary built in the bounded scope from a clean checkout of the registration commit (P192's pin, since
  no Rust source changes).

## Method

- **Values (`controls/p193.py run`).** Every row is collapsed with every library that has the reaction, by P192's
  rules. A library missing the target, reaction or product state has no value for that row. For 8 rows per
  non-default library, drawn with seed 193, `actinv inspect xs` must agree within 1e-9 relative.
- **The rule (`controls/check_p193.py`).** For a reaction label and a set of fit rows of that label, the libraries
  that compete are those with a value on every fit row and on every row the choice will be applied to. Each has a
  score: mean |ln(C/E)| over the fit rows. The lowest score wins, with ties broken in the order above. It replaces
  TENDL-2025 only if it beats TENDL-2025's score by more than 0.05.
- **Leave-one-facility-out.** For each facility F, the fit rows are the other two facilities' rows of each label, and
  the choice is applied to F's rows of the same label. Rows whose label was measured only at F are not held out. The
  held-out rows of the three folds are pooled.
- **Scores.** For each of three things on the pooled held-out rows: median |ln(C/E)|, geometric-mean C/E, share
  within 2u, and root-mean-square z. The three things are the selected values, TENDL-2025 alone, and FISPACT-II as
  published.
- **Paired tests.** Two-sided Wilcoxon signed-rank tests, as in P192, with the direction taken from the sign of z.
  Each comparison is labelled FIRST CLOSER, SECOND CLOSER or NO SIGNIFICANT DIFFERENCE (p < 0.05), where FIRST is
  the selected values:
  - selected against TENDL-2025 (FIRST CLOSER means the choice helps);
  - selected against FISPACT-II (FIRST CLOSER means ACTINV is closer);
  - TENDL-2025 against FISPACT-II, for context.
- **Reported, not scored.** The rule fitted on all three facilities, which would be a shipped choice, with its
  counts per library. Each library's median |ln(C/E)| on all rows where it has values.

## Gates

- **G0:** this protocol's SHA-256, and those of `controls/p193.py` and `controls/check_p193.py`, are in
  `protocols/protocol_hash.txt`.
- **G1:** provenance. The table, the spectra, the four libraries and their indexes, P192's sealed `controls/p192.py`
  and the binary match their pins; the record comes from the registration commit with a clean tracked tree; 365
  rows; not a rehearsal.
- **G2:** integrity. Every row's status is P192's. Every TENDL-2025 value equals P192's committed value within 1e-12
  relative. All 24 cross-checks agree.
- **G3:** the measurement above, always reported, never a gate.
- **G4:** CI green on the evidence commit.

**P193-PASS** if G0, G1, G2 and G4 hold. One repair round (an amendment with a fresh run) is allowed.

## Allowed sentences

On P193-PASS, filled by `controls/check_p193.py` from the record, and quoted together:

1. "In a leave-one-facility-out test on the {n} rows of UKAEA's integro-differential validation table whose reaction
   was also measured at another facility, choosing each reaction's evaluation among TENDL-2025, TENDL-2023,
   TENDL-2017 and ENDF/B-VIII.1 by a fixed rule fitted on the other facilities gave ACTINV a median |ln(C/E)| of
   {s}, against {d} with TENDL-2025 alone and {f} for FISPACT-II's published TENDL-2017 values; {choice clause}
   (p = {p}), and {comparison clause} (p = {q})."
2. "The table's measurements had been compared with ACTINV's TENDL-2025 values before this test (P192); in each fold
   the held-out facility played no part in the choice."

No sentence may say the choice is shipped; shipping it would be a separate change.

## Commands (coordinator)

From a worktree at the registration commit, with the binary built there, inside the 6 GiB AGENTS.md scope:

- `python3 controls/p193.py run --actinv-bin BINARY`;
- `python3 controls/check_p193.py`.
