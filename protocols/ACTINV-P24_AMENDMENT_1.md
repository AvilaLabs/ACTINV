# ACTINV P24 Amendment 1 — mechanical input/scoring-path repairs

Recorded 2026-09-17. This is the single append-only repair round permitted by the P24 protocol's
G4 clause. If all remaining gates pass, the closure verdict is therefore `P24-CONDITIONAL`, not
`P24-PASS`. A second repair round fails the phase.

## What happened

The unseal authorization was a green workflow on the G3 commit `77f9484`. The first authorized G4
execution then proceeded as follows:

1. **Attempt 1** parsed all sixteen sealed fresh tables exactly once through the frozen scorer,
   collected the reaction bindings, verified the hash-pinned archives, loaded the groupwise and
   spectrum catalogs, and crashed while loading the **pointwise** catalog
   (`selected_endf_catalog(paths["pointwise"], ...)`).
   - Cause: `controls/endf_common.py`'s `endf_float` rejects ENDF-6 fields whose exponent sign is
     separated from its digits by padding (`-8.03362+ 6`). The pointwise archive contains such
     fields. Same input-format defect class as the G0 candidate-target extractor repair.
2. **Attempt 2** (with repair R1 applied) reached `score_fresh_partition` and crashed when a scored
   row's fold returned `undefined_monitor`: `p17_scoring.unscored_calculation` validates reasons
   against the frozen P17 `CALCULATION_REASONS` vocabulary, which predates the P24 fold-level
   outcomes `undefined_monitor`, `insufficient_spectrum_or_history` and
   `non_neutron_incident_particle`. The G2 fixture set exercised `fold_variant` directly but never
   routed those reasons through `unscored_calculation` — an audit coverage gap, now closed by
   fixtures.

3. **Attempt 3** (with R1–R3 applied) parsed all sixteen tables, collected bindings, verified all
   archives, and crashed inside `score_fresh_partition` at `find_monitor_row`:
   `row.get("reaction_label", "")` returns `None` (not the default) on degraded rows whose
   `reaction_label` key exists with value `None`, so `.startswith` raised `AttributeError`.
   - Cause: a None-safety defect on the degraded-row shape. Degraded rows carry `None` values for
     absent grammar fields; the monitor scan treated the default as unreachable. Mechanical.
4. **Attempt 4** (with R4 applied) completed the single scoring run: 460 rows ledgered, 74 scored,
   the report and row ledger written. Post-completion review then found the record omitted the
   protocol-required cause-ledger entries for its two material-mismatch keys — a
   report-completeness defect repaired by a separate annotator consuming the produced ledger, so
   no re-parse of the sealed tables was needed.

Attempts 1–3 produced no scored value, report, or ledger; attempt 4's scored output was not
modified by any repair. No fresh numerical value was inspected, used, or fitted to before the
authorized run completed. All defects are mechanical — none touches a definition, mapping, metric,
or exclusion predicate.

## The repairs

- **R1 — `controls/endf_common.py` `endf_float`**: normalize a trailing `sign + spaces + digits`
  exponent (`[+-]\s*\d+$`) before `float()`. Accepts exactly the values the field denotes.
- **R2 — `controls/p24_scorer.py`**: add `p24_unscored_calculation`, the P24-level unscored
  calculation builder validating against `P24_CALCULATION_REASONS` = the frozen P17
  `CALCULATION_REASONS` plus the three fold-level outcomes above; `score_fresh_partition` uses it.
  `controls/p17_scoring.py` remains byte-identical — P17 semantics are unchanged.
- **R3 — `controls/p24_scorer.py` `internal_consistency`**: the printed `Diff%` sign convention in
  the fresh tables is not frozen; the check now fails a row only when neither sign convention
  reproduces the printed value. Without this, a convention choice could falsely ledger rows as
  `internal_consistency_failure`. Found by post-crash self-review before the next attempt, not by
  a third crash.
- **R4 — `controls/p24_scorer.py` `find_monitor_row`**: `or ""` defaults on `reaction_label`/`label`
  so `None`-valued degraded-row fields cannot raise `AttributeError`. Third crash, attempt 3.
- **R5 — `controls/g4_p24_causes.py` + `results/g4_p24_fresh.json`**: emit the protocol-required
  cause-ledger segment for the record's material-mismatch keys. Implemented as a separate control
  that consumes `results/g4_p24_row_ledger.json` and annotates the produced report — the sealed
  tables are not re-parsed. Found by post-completion self-review, not by a crash.

## Consequences and unchanged claims

- The frozen artifacts are unchanged: protocol hash
  `ee703d208b43c3dc91fef41a259d748d343865e130f37266128685cf21001ef7`, the G1 definitions record, and
  the corrected inclusion/metric semantics. The G2 audit record is regenerated so its pinned module
  hashes cover the repaired files; every fixture and mutation test is re-run.
- The next G4 attempt re-parses the partition. "Parsed exactly once" binds each *scoring run*; both
  crashed attempts produced no scored output.
- Prior partial-exposure disclosure stands: during G4 grammar verification, header extraction on
  sealed pages incidentally displayed a small number of numeric cells from T27/T35/T45. No frozen
  artifact was modified in response and no scoring decision was fitted to those cells.
