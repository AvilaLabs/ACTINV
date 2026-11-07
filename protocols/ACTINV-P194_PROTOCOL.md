# ACTINV-P194 — A composite library built from P193's choice, kept whole per target and reaction, and checked on earlier data

## Purpose

P193 found that choosing each reaction label's evaluation among TENDL-2025, TENDL-2023, TENDL-2017 and ENDF/B-VIII.1
by a fixed rule brings ACTINV level with FISPACT-II on UKAEA's validation table, scored leave-one-facility-out. That
choice was made per label: a label names a target, a reaction (MT) and a product state. ACTINV takes a target's
burn-up from a reaction's total row and its products from the reaction's product rows. A library that mixes rows of
one reaction from two evaluations loses that balance, so a shippable library must take each (target, MT) block whole
from one evaluation. In 19 of P193's 214 blocks, two labels chose different evaluations.

P194 moves P193's rule to blocks, builds the resulting library, and asks two questions:

> 1. Scored leave-one-facility-out on P193's held-out rows, how close is the block choice to the measurements,
>    against TENDL-2025 alone and against FISPACT-II's published values?
> 2. On data scored before with TENDL-2025 (IRDFF-II spectrum-averaged cross sections, the Řež activation rates and
>    the JAEA-FNS decay heat), does the composite move ACTINV further from the measurements?

Both answers are measurements. The composite is built on this workstation and is not distributed; shipping it is a
separate decision.

## Disclosure of what has been seen

- All of P192 and P193, including P193's committed record: every row's value in each of the four libraries. From
  P193's per-label final choice: 106 labels changed, 84 of them measured once; 100 of 214 blocks would change, and 19
  blocks hold labels that chose different evaluations. No block-level score has been computed.
- Structure, not values, of the four libraries' blocks: TENDL-2025 holds every block; ENDF/B-VIII.1 lacks a total row
  in 12 blocks; state rows do not always sum to the total row in any of the libraries.
- IRDFF-II (P17), Řež (P167) and JAEA-FNS (P172) were scored before with TENDL-2025, and P172 also with ENDF/B-VIII.1.
  The JAEA-FNS decay heat was measured at one of the table's three facilities, so it is not independent of the
  choice. No set on the workstation is (P193's inventory).
- Licences: only the TENDL-2025 evaluations state one (CC-BY-4.0, in every file's first line). The IAEA copies of
  TENDL-2017 and TENDL-2023 used here, the ENDF/B-VIII.1 files, and the TENDL and NNDC web pages state none.
- One rehearsal was read for exit status, gate results and counts only. Its output is deleted. It showed that four
  IRDFF-II rows have no TENDL-2025 value, as in P17. The checker had required one, and was corrected before
  registration.

## Inputs (pinned in `controls/check_p194.py`)

- P193's committed record and the four libraries and indexes it pins.
- Two ACTINV binaries: P192's, for the table cross-check, and P167's, for the Řež rates. Also P172's Python module,
  for the decay heat. Each set is scored with the code that scored it before.
- The reused harnesses, imported unchanged and pinned with the committed results they must reproduce:
  - `controls/p17_irdff.py` with `results/g4_p17_diagnostics.json`;
  - `controls/p167_rez.py` with `results/p167_rez.json` and `results/p167/rez_tables.json`;
  - `controls/cb2_fns.py` with `results/p172/actinv_tendl2025.json`;
  - `controls/p192.py` and `controls/p193.py`.
- P17's IRDFF-II inputs and P172's decay files and FNS archive, with their earlier pins.

## Method

- **Blocks.** A block is a (target, MT) pair of the table. For every block and library, `controls/p194.py` records
  three things: its row count, whether it has a total row, and whether it has a production row (zap = the product's
  ZA) for each label's product.
- **The rule (`controls/check_p194.py`).** A library competes for a block if:
  - it has a value on every fit row and on every row the choice will be applied to;
  - for any library but TENDL-2025, its block has a total row and a production row for every label's product.

  Each competitor has a score: the mean |ln(C/E)| over all the block's fit rows, of every label. The lowest score
  wins, with ties broken in P193's order. The winner replaces TENDL-2025 only if it beats TENDL-2025's score by more
  than 0.05.
- **Leave-one-facility-out.** For each facility F, the rule is fitted on the other facilities' rows of each block and
  applied to F's rows of that block. The held-out rows are P193's: F's rows whose label was measured at another
  facility. The three folds are pooled. The statistics and paired Wilcoxon tests are P193's. The three comparisons
  are block choice against TENDL-2025, block choice against FISPACT-II, and TENDL-2025 against FISPACT-II.
- **The composite.** The rule is fitted on all scored rows of each block. TENDL-2025's rows are copied in order. Each
  chosen block is replaced at its place, whole, by the chosen library's block, with the target column remapped. The
  index is TENDL-2025's, with row counts, `sha256_npz` and a ledger line per replaced block updated. The output is
  `~/nuclear-data/p194-composite/tendl-2025-composite-p194-709g.npz` and its index, never tracked.
- **Checks of the composite:**
  - every (target, MT) group is bit-identical, in order, to the group of its source library, and the bounds are
    TENDL-2025's;
  - every table row, collapsed by P192's rules, equals P193's value for its block's library;
  - 24 rows of replaced blocks, drawn with seed 194, agree with `actinv inspect xs` on the composite within 1e-9.
- **Earlier sets.** Each set is scored twice, with TENDL-2025 and with the composite, through its own harness.
  Only the library path and its two pins are redirected, as P172 did for its ENDF/B-VIII.1 arm:
  - IRDFF-II: P17's 124 rows. Four (BH3, B10He4, Li6He4, B10H3) have no TENDL-2025 value in P17's record, and the
    composite must leave them without one;
  - Řež: P167's 34 rows, 29 of them scored;
  - JAEA-FNS: P172's 132 experiments and 2,360 points, in fresh work directories.

  Each set's TENDL-2025 arm must reproduce its committed values. For each set, the median |ln(C/E)| of both
  libraries and a paired Wilcoxon test on |ln(C/E)| are reported, with unchanged values dropped as zeros. Each set is
  labelled COMPOSITE CLOSER, TENDL-2025 CLOSER, NO SIGNIFICANT DIFFERENCE (p < 0.05) or UNCHANGED. Non-regression
  holds if no set is TENDL-2025 CLOSER. The composite against FISPACT-II's TENDL-2017 decay heat, on the FNS points
  that have it, is also reported.
- **Reported, not scored.** The composite on all table rows (in sample), against TENDL-2025 and FISPACT-II.

## Gates

- **G0:** this protocol's SHA-256, and those of `controls/p194.py` and `controls/check_p194.py`, are in
  `protocols/protocol_hash.txt`.
- **G1:** provenance.
  - Every pinned input, binary, module, harness and committed result matches its pin.
  - The record comes from the registration commit with a clean tracked tree.
  - The record is not a rehearsal.
- **G2:** integrity.
  - The composite's blocks are the checker's block choice from P193's record.
  - The three checks of the composite pass.
  - Each TENDL-2025 arm reproduces its committed values within 1e-12 relative.
  - Each composite arm is complete: 124 IRDFF-II rows, valued wherever TENDL-2025 is; 34 Řež rows; and 132 FNS
    experiments with matching certificates and every point valued.
- **G3:** the measurements above, always reported, never a gate.
- **G4:** CI green on the evidence commit.

**P194-PASS** if G0, G1, G2 and G4 hold. One repair round (an amendment with a fresh run) is allowed.

## Allowed sentences

On P194-PASS, filled by `controls/check_p194.py` from the record, and quoted together:

1. "A composite ACTINV library, TENDL-2025 with {k} of the {b} target-reaction blocks measured in UKAEA's
   integro-differential validation table taken whole from TENDL-2017, TENDL-2023 or ENDF/B-VIII.1 by a fixed rule,
   scored a median |ln(C/E)| of {s} in a leave-one-facility-out test on the table's {n} rows whose reaction was also
   measured at another facility, against {d} with TENDL-2025 alone and {f} for FISPACT-II's published TENDL-2017
   values; {choice clause} (p = {p}), and {comparison clause} (p = {q})."
2. "On data scored before with TENDL-2025, the composite's median |ln(C/E)| was: {per set: composite against
   TENDL-2025, the label's clause, values changed, p}."
3. "None of these data was independent of earlier work: the table was compared with TENDL-2025 before (P192), the
   JAEA-FNS decay heat was measured at one of the table's three facilities, and the other two sets were scored before
   (P17, P167); in each fold of the table test the held-out facility played no part in the choice."

No sentence may say the composite is shipped, distributed or the default.

## Commands (coordinator)

From a worktree at the registration commit, inside the 6 GiB AGENTS.md scope:

- `python3 controls/p194.py run --actinv-bin P192_BINARY --rez-bin P167_BINARY --module P172_MODULE`;
- `python3 controls/check_p194.py`.
