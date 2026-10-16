# P140 Amendment D — reference identity in the rerun controls checkers

Registered 2026-10-15, after the P123 rerun on `cand11` and before the other reruns finished. Candidate `cand11`,
SHA-256 `179e96a0…4536`.

## Evidence

`controls/check_p123.py` on `cand11` fails its G2 (`results/p140/controls/p123_cand11.json`): the default example
and two photon specs are not byte-identical to the 2f3daa4 reference. The recorded first differences are only
`ledger.spontaneous_fission_branches_to_leakage` (129 → 126) and `ledger.unmodelled_decay_branches_to_leakage`
(present only in the candidate), the two ledger changes of Amendment B. Every other P123 gate passes (G3 and G4
with 0 mismatches over 58,000 comparisons, G5 and G6 pass). The other rerun checkers with a reference-identity
gate (P126, P127, P129, P134, P135, P136, P139) are expected to show the same two differences, and, where a spec
has radioactive bulk nuclides, pathway attribution differences.

## Criterion

For the controls checkers rerun under Amendment A, a reference-identity gate that fails is accepted when, and only
when, every difference between candidate and reference results is in the fields Amendment B masks: `ms`, the two
ledger counts above (the spontaneous-fission count falling by at most the unmodelled count) and, for a spec
containing a radioactive bulk nuclide, `pathways` and `pathway_closure` (closure not growing). The coordinator
checks this with the H2 function `reference_compatible` of `controls/check_p140_matrix.py` applied to each failing
spec's two result files, and records the outcome per checker; a difference outside these fields is a failure.
Every other gate of each checker must pass as written. The checker records are kept as produced, failing G2
included, in `results/p140/controls/`, and the original records of P123–P139 are not overwritten.
