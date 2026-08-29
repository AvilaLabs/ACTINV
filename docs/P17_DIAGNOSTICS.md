# P17 open diagnostic report

This is the pre-unseal diagnostic checkpoint. It contains only IRDFF-II Tables 18–20 and the already-seen CoNDERC FNS experiments. Accuracy is reported, never used as a hidden acceptance threshold.

## What the controls isolate

- Numerical solver and chain construction are bounded by byte-identical operator/rate controls.
- Raw-evaluation processing is bounded by the fresh ACTINV/NJOY differential.
- IRDFF-II versus TENDL-2025 changes only the activation-data roles in the diagnostic SACS calculation.
- The FNS decay variant swaps ENDF/B-VIII.0 and JEFF-3.3 priority while holding activation data, schedule, spectrum, solver, chain construction, and measurement alignment fixed.
- FISPACT-II/TENDL-2017 remains explicitly different-data context and is not called a solver comparison.

## IRDFF-II open tables

| family | variant | scored | unscored | geometric mean C/E | median abs(ln C/E) | p90 abs(ln C/E) | within 30% |
|---|---|---:|---:|---:|---:|---:|---:|
| irdff_table_18 | actinv_tendl2025 | 43 | 1 | 0.976585 | 0.0140247 | 0.0720017 | 97.7% |
| irdff_table_18 | irdff_ii_groupwise_diagnostic | 44 | 0 | 1.00044 | 0.0135715 | 0.0510156 | 100.0% |
| irdff_table_19 | actinv_tendl2025 | 26 | 0 | 1.00128 | 0.0189728 | 0.0497509 | 100.0% |
| irdff_table_19 | irdff_ii_groupwise_diagnostic | 26 | 0 | 0.998068 | 0.0219176 | 0.0474503 | 100.0% |
| irdff_table_20 | actinv_tendl2025 | 51 | 3 | 0.959026 | 0.02407 | 0.0955435 | 96.1% |
| irdff_table_20 | irdff_ii_groupwise_diagnostic | 54 | 0 | 0.990366 | 0.0210789 | 0.0869457 | 98.1% |

## CoNDERC FNS family

All 132 experiments are retained as separate families; they are not pooled into a winner score. Across those family-level reports:

- production rows scored: 2360; unscored: 47
- production experiments whose every scored row is within 30%: 59/132
- JEFF-primary decay-substitution experiments whose every scored row is within 30%: 58/132

Every outside-30% row is present in the append-only cause ledger. `unresolved` is used where the controlled evidence bounds layers but does not demonstrate a unique cause.

## Reproduction

With the hash-pinned public inputs listed in the P17 protocol and a release Python module built at `python/target/release/libactinv.so`, run:

```bash
python controls/g4_p17_diagnostics.py
python controls/check_g4_p17.py
```

The generator is resumable under `/tmp/actinv-p17-g4-fns`; caches are fingerprinted by every input identity and the calculation-implementation identity. The committed checker needs no bulk nuclear data.
