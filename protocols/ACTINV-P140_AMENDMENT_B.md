# P140 Amendment B — baseline identity after the Amendment A repairs

Registered 2026-10-15, after H2 on the repaired candidate and before any change made in response to it. Candidate:
`cand10`, release build of `164fa49` plus the Amendment A repairs, SHA-256 `c8e8b077…1c66`.

## Evidence

H2 on `cand10` fails (`target/p140/p140_matrix_cand10.json`, kept as `results/p140/h2_cand10.json`): 151 cases,
128 pass, 18 documented refusals, 5 fail. All 5 failures are `reference_identical` on the default-option baseline
cases (the shipped `examples/fns_fe_5min.json`, and Fe, W, SS316 and natural U in `auto` mode); every invariant
of every case holds. Field-by-field comparison with the reference results shows inventories, summaries and every
other field byte-identical, except:

1. All five: the ledger count `spontaneous_fission_branches_to_leakage` falls from 129 to 126. Repair 4 (P140 PF F1)
   no longer counts a branch whose RTYP chains a fission after another transition (2.6, β-delayed fission) as
   spontaneous fission of the parent.
2. W, SS316 and U: `pathways` (and for U `pathway_closure`) change. Repair 5 (P140 PS F4) attributes trace
   production fed by decay of radioactive bulk nuclides: W-180 α decay to Hf-176 (atoms changed by about 5e-9
   relative), Mo-100 double-β decay to Ru-100 (now the leading route, 1.38e-5 atoms/g, consistent with
   N·λ·t for 1.5e19 atoms/g, λ = 3.1e-27 s⁻¹ and about 300 s), and the U-238 and U-234 chains in natural U, whose
   closure gap falls from 0.99999999999995 to 4.2e-15. Fe has no radioactive bulk nuclide and its pathways are
   unchanged.

Both differences are the registered behaviour of Amendment A repairs, not regressions. The H2 criterion "the
baseline set with default options is byte-identical to the reference apart from `ms`" cannot hold together with
those repairs.

## Defect found

The three branches reclassified by repair 4 reach leakage through the unknown-mode path, whose count
(`unknown_modes` in the chain ledger) appears in no result field, so they leak without a ledger entry.

**Repair.** The result ledger gains `unmodelled_decay_branches_to_leakage`, always emitted: the number of decay
branches with an elementary mode the chain does not model, booked to leakage. When it is above zero the
certificate's decay defects gain a class of the same name. Regression test: a nuclide with an RTYP 2.6 branch
gives a count of 1 in the chain ledger and the key in the result ledger. Adding the key changes every result's
ledger, so committed run-output baselines whose normalised hash covers the ledger are regenerated and explained in
the ledger.

## Criterion for H2 baseline cases

For the five baseline cases on the repaired candidate, `reference_identical` is replaced by `reference_compatible`:

- the candidate and reference results are byte-identical after both are masked for `ms`, the ledger keys
  `spontaneous_fission_branches_to_leakage` and `unmodelled_decay_branches_to_leakage`, and, for W, SS316 and U
  only, `pathways` and `pathway_closure`;
- the spontaneous-fission count may only fall, and the fall is at most the candidate's
  `unmodelled_decay_branches_to_leakage`;
- for W, SS316 and U, a numeric `pathway_closure` of the candidate is no larger than the reference's, and the
  candidate's pathway invariants of H2 hold.

Fe and the shipped example must therefore match the reference in `pathways`. All other H2 invariants are
unchanged. H2, H3 and H4 are rerun on the candidate carrying this repair; the `cand10` record is kept.
