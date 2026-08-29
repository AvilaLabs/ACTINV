# P18 G1 physical product-state identity

P18 G1 passes. ACTINV's TENDL builder no longer treats a positive ENDF `LFS` number as a decay-isomer ordinal. It
retains the target and product state metadata at source precision, constructs a corpus-level catalog from evaluated
target states, and emits a positive inventory state only when the product excitation has one unambiguous catalog
match. Unsupported identities remain numerically accounted as explicit non-emitted production; they are never
silently renamed.

The candidate uses `actinv-library-index-2`. Every mapped row records raw `LFS`, MF=8 `ELFS`, MF=9/10 `QM` and `QI`,
`QM-QI`, the excitation used for matching, catalog `LIS`/`LISO`/`ELIS`, source-file hash, tolerance and decision. EAF
libraries retain their established `actinv-library-index-1` interpretation. Old v1 indexes remain readable by the
covariance builder.

## Decisive real cases

All inputs are external TENDL-2025 files; only their hashes and compact mapping metadata are committed.

| case | physical source identity | candidate result |
|---|---|---|
| Ag-109 capture to Ag-110m | raw `LFS=2`, `ELFS=117590 eV`, `QM-QI=117590 eV`; evaluated target `LIS=2`, `LISO=1`, `ELIS=117590 eV` | canonical `m1`, not rank-compressed from the number 2 |
| Ag-116 first isomer | raw level 1 at 47.9 keV; evaluated `LIS=1`, `LISO=1` | canonical `m1` |
| Ag-116 second isomer | raw level 4 at 129.8 keV; evaluated `LIS=4`, `LISO=2` | canonical `m2`, proving omitted lower levels do not change identity |

The Ag-110 case is the exact sparse-level cause exposed by P17. The Ag-116 case exercises two evaluated isomers
through three target-state evaluations. Reversing bytewise file order changes physical row placement and therefore
the raw archive hash, but the normalized catalog and every mapping decision remain identical.

Generated ENDF fixtures and Rust unit cases additionally cover ground state, distinct and ambiguous isomers, multiple
reaction MTs, shuffled row order, agreeing and conflicting duplicates, `LFS=98`, missing excitation, no catalog
match, exact tolerance boundaries, unchanged production strength and explicit non-emitted accounting. The independent
checker rejects mutations to raw level, canonical state, catalog excitation and Q identity.

This is an identity gate, not an accuracy verdict or release authorization. Diagnostic ratios have not been used by
G1, and all 180 held-out families and 1,945 held-out rows remain sealed. G2 next audits every product declaration in
the four frozen corpora and enforces raw and collapsed state-partial conservation.

## Reproduction

With the six hash-pinned TENDL files at the external data root and a current release binary:

```text
ACTINV_BIN=target/release/actinv python controls/g1_p18_state_identity.py --no-write
python controls/check_g1_p18.py --no-write
```

The compact evidence is `results/g1_p18_state_identity.json`; the separately implemented verdict is
`results/g1_p18_check.json`.
