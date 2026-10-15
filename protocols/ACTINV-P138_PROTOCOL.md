# P138 — Importer maps the parity keywords (`import-fispact`)

Registered 2026-10-15, after implementation (`e9a37c2`, POWER units corrected in `b7a4615`) and before any evidence.

## Change under test

`actinv import-fispact` maps POWER (`power_W_cm3` with DENSITY; W/cc per the FISPACT-II wiki, `POWER p n mt…`),
WALL, PARTITION, OVER ACROSS/ALAM, GRPCONVERT/CNVTYPE/LIMGRP, DOSE 2, GROUP 0/1, BREMSSTRAHLUNG, PATH/ROUTES and
ATWO, each with a per-keyword status; the rest keep specific notes.

## Gates

Checker: `controls/check_p138.py`.

- **G0.** Protocol hash registered. **G1.** fmt, clippy, workspace tests (including the importer tests).
- **G2, no regression.** All 132 FNS decks import; `controls/check_p132.py` passes on the candidate.
- **G3, equivalence.** For each newly mapped keyword with a runnable meaning (WALL, PARTITION, OVER ALAM/ACROSS,
  GRPCONVERT, DOSE 2, GROUP 1, BREMSSTRAHLUNG, PATH, ATWO, and POWER after adding a kerma table), a small deck derived
  from the FNS Fe deck is imported and run, and its result equals a hand-written spec with the same meaning apart
  from echoes.
- **G4, honesty.** Every keyword the importer cannot map faithfully reports `approximated`, `noted` or
  `unsupported` with a reason; no deck produces a spec that silently differs from the deck's meaning in the G3 set.
- **G5.** CI green on the pushed commit.
