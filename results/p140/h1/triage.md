# P140 H1 triage (coordinator reproductions, candidate cand9 = 0b62b47c...1170, HEAD 233e3e8)

## summary/depletion review
- F1 removal missing from depletion: CONFIRMED. repro target/p140/repro/f1_removal.json (coupled, removal Fe56 1e-3, 1000 s):
  depleted_fraction 0.63212 (=1-e^-1), channels [], removal_rate_per_s -0.0 (also negative zero). Defect.
- F2 trace+uncertainty no channel uncertainty: REJECTED. f2_trace_unc vs f2_coupled_unc: trace channels carry
  relative_standard_uncertainty identical to coupled (Fe56 MT16 0.13144...). Claimed derivative drop does not occur.
- F3 excluded blocks reported as 0 uncertainty: pending unit test at repair time (no shipped data reaches it).
- F4 trace-analytic precision: CONFIRMED. f4_trace: Fe56 depleted_fraction 1.89083e-12 vs -expm1(-R dt) 1.89078e-12,
  rel err 2.6e-5 (Fe54 -1.4e-5). Robustness -> repair with expm1.
- F5 NOSTABLE keeps no-record states: documented in results.md; spec table lacks sentence -> documentation.
- F6 decay channel uncertainty ignores uncertainty.channels: design inconsistency -> documentation (state it).
- F7, F8: documentation, accepted.
- F9a NaN clamp: robustness, accept (cheap). F9b covered share 0 for zero-removal rows: accept omit-key.
- F10 report lacks depletion table: feature gap; accepted as part of GUI/report Tables work (depletion table in report).

## H4 (cand9)
- First run: 76/85; all 9 failures were checker defects (output parent dirs not created — CLI single-file outputs do not
  create parents, consistent with `run`; tab3 expected buildable from A2-only radiological responses, but tab3 lists
  Sv-unit responses only, as documented). Checker fixed (mkdir workdir parents; tab3 needs an Sv response). Rerun:
  85/85 PASS, record target/p140/p140_docs_cand9.json.
- Coverage gap noted by the checker: 11 parity features have no fenced handbook fragment (point_source, bremsstrahlung,
  group_structure, power_W_per_g, wall_loading_MW_m2, partition, overrides, spectrum.rebin, step library, step
  projectile, dilution_overrides) -> documentation work: add fragments.

## Decisions on remaining reviews (coordinator, before reproduction)
Legend: A = accept for repair (needs failing regression test first = reproduction), D = documentation fix, R = rejected (reason).

summary/depletion (review_summary_depletion.md): F1 A (already reproduced by run), F3 A, F4 A (reproduced by run),
  F5 D, F6 D, F7 D, F8 D, F9a A, F9b A (omit key when total removal is 0), F10 A (report depletion table, TAB2 trim note,
  --nuclides check against summary.nuclides), F2 R (reproduction showed uncertainty present in trace), negative zero A.
photon/regulatory (review_photon_regulatory.md): F1 A (beta- electrons per decay = sum BR of modes with digit 1; E0 = ER/f;
  f=0 -> missing_inputs), F2 A (progeny covered by parent: table-level list, coverage counts it, contribution zero, listed),
  F3 A (unlimited rows: table-level list, contribute zero, counted covered, listed), F4 A, F5 A, F6 A (closure + zero-edge
  share fields; doc midpoint rule), F7 A, F8 A, D1-D5 D.
schedule controls (review_schedule_controls.md): F1 A, F2 A, F3 D + remove dead branch, F4 A, F5 A, F6 A, F7 A (test
  first; low confidence), F8 D + ledger floor field, F9 A, F10 A, F11 A, F12 A (refuse fallback for non-neutron; doc
  total inert), F13-F16 D (F16 messages A).
power/fission (review_power_fission.md): F1 A, F2 A, F3 D (refusal documented explicitly), F4 A (= SC F4), F5 A
  (distinct messages; stderr warning on unconverged), F6 D, F7 D, F8 A (with SG-09), F9 D, F10 A (damage coverage
  fraction, build script warning).
shielding/groups/inspect (review_shielding_groups_inspect.md): 01 A (clip lines to [EL,EH] per range, ledger count of
  dropped levels; P139 evidence to be rerun), 02 A, 03 A, 04 A, 05 A, 06 A (report field), 07 A, 08 A, 09 A, 10 A,
  11 D, 12 A, 13 A, 14 D.
pathways/stats (review_pathways_stats.md): F1 A, F2 A (per-target truncation reporting; fairness across sources),
  F3 A, F4 A (flag/close gap for bulk-decay production in trace first-product table), F5 A, F6 D, F7 D, F8 A, F9 D,
  F10 A, F11 A, F12 R (one-ulp edge display, cosmetic), F13 D.
import-fispact (review_import_fispact.md): F1 A, F2 A, F3 A (keep documented deferral semantics; superseded or
  trailing TIME becomes unsupported with lost duration/flux in detail), F4 A, F5 A, F6 A, F7 A, F8 A, F9 A, F10 A,
  F11 A, F12 D, F13 A, F14 A.

## H2 (cand9)
PASS: 151/151 (133 pass, 18 refused-ok, all documented conflicts); record target/p140/p140_matrix_cand9.json.
