# P162 Amendment A — G5 checks the composition where a run records it

Registered before any evidence run, after development run 1 found the problem. This is not the phase's repair
round: no evidence run has been made, so that round is still available. No other gate or criterion changes.

## Finding that requires it

Development run 1 ran the two G5 cases on the candidate as it then stood. Both `actinv new` calls and both
`actinv run` calls exited 0. G5 still failed, because it asks that "both results' material blocks equal the library
composition", and a run result has no material block. Its top-level keys are `spec_title`, `entry_point`, `mode`,
`pruned_states`, `total_states`, `steps`, `pathways`, `pathway_closure`, `ledger`, `certificate` and `ms`. The
composition is recorded only in the ledger, as totals and flags:

- `composition_basis`;
- `composition_input_total` and `composition_weight_percent_total`;
- `composition_not_summing_to_100`;
- `composition_elements_unknown`.

The certificate does not record the problem's hash. The gate was written before the result's layout was checked
and cannot be met by any run. The record is in `results/failures/p162/dev_run_1/g5.json`.

Development run 1 also passed G2, G3 and G4. In G3, every group agreed with the independent recomputation to
6e-14 relative or better.

## A1. G5 as amended

For each of the two cases, `actinv new --material eurofer97 --source fusion-14mev --flux 1e12` and `--material
ss316 --source fission --flux 1e13`:

- `actinv new` exits 0. The problem it writes has a `material.composition` equal to the library entry's
  `composition_wt_percent`: the same element symbols, compared without regard to case, and every value within
  1e-9. Its `material.basis` is `wt_percent`.
- `actinv run` on that same file, unchanged, exits 0 on the standard data.
- The result's ledger reads the composition back:
  - `composition_basis` is `wt_percent`;
  - `composition_input_total` is 100 within 1e-9;
  - `composition_not_summing_to_100` is false;
  - `composition_elements_unknown` is empty.

The checker records each value it compared. Nothing else in G5 changes.

## A2. Evidence

The evidence run uses the checker as amended. Gates G0–G4 and G6 are unchanged. G0 also requires this amendment's
SHA-256 in `protocols/protocol_hash.txt`.
