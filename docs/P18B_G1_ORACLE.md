# P18b G1 — decimal and official-checker oracle

P18's conservation audit used a binary-arithmetic stress threshold that was much tighter than the precision carried
by an ENDF real field. G1 establishes the numerical rules needed to separate three different statements:

1. the printed source values obey the strict ENDF rule;
2. the values are inside the standard IAEA checker envelope; and
3. the values ACTINV will use at runtime conserve after processing and group collapse.

Only the first two are exercised here. G1 changes no production library, public artifact, runtime result, corpus
classification or measurement score.

## What was exercised

The committed fixture contains all three floating-point forms described in ENDF-102 Table 17, including the maximum
positive and negative two-digit exponents, optional and explicit signs, zero and negative zero, ordinary `E` input,
blank zero, and malformed/non-finite controls. Every field is exactly eleven ASCII bytes.

The same fixture covers:

- histogram, lin-lin, lin-log, log-lin and log-log interpolation;
- zero outside a table's domain and both sides of a repeated energy;
- MF9 multiplicity and MF10 individual/summed cross-section comparisons;
- source-conformant, printing-envelope, definite-excess, malformed and missing-total classes;
- the closed 0.1% compatibility boundary, the first value beyond it, and the corresponding exact-zero cases;
- negative-Q threshold starts; and
- large-Q excitation cancellation, the inclusive one-eV identity boundary and one printed quantum beyond it.

An independent Python implementation reads the original fields as exact base-10 numbers. It uses 80 decimal digits,
then repeats every nontrivial calculation at 120 digits. A small Rust probe uses ACTINV's checked parser and
interpolator. The Python checker imports neither implementation and independently reconstructs the fixed-point,
classification, interpolation, tape and boundary arithmetic.

## Result

| check | result |
|---|---:|
| fixed-width real cases | 18 / 18 |
| interpolation tables / queries | 7 / 12 |
| comparison / threshold / excitation cases | 12 / 4 / 4 |
| maximum relative difference, 80 vs 120 digits | `3.34e-80` |
| maximum Rust interpolation difference | 0 binary64 ULP |
| unmodified IAEA CHECKR/FIZCON decisions | 6 / 6 agree |
| independent mutation plants rejected | 7 / 7 |

The official fixtures prove the important boundary behavior directly. FIZCON accepts an MF10 sum of 1001 against a
total of 1000 and rejects the first represented case beyond it. At an exactly zero MF10 total it accepts a sum of
0.001 barn and rejects the first generated value beyond that ceiling. The MF9 inside/outside cases likewise agree
with the independent rule. CHECKR reads all generated tapes without a record-structure failure.

This does **not** declare a small printed source excess conforming. Strict source mode still reports every exact ENDF
violation. The IAEA envelope is only the separately named compatibility ceiling and, later, the maximum possible
runtime reconciliation ceiling.

## Reproducibility and provenance

- Frozen protocol: [`ACTINV-P18b_PROTOCOL.md`](../protocols/ACTINV-P18b_PROTOCOL.md)
- Generated fixture definition: [`p18b_g1_oracle.json`](../controls/fixtures/p18b_g1_oracle.json)
- Exact-decimal and official-tool control: [`g1_p18b_decimal_oracle.py`](../controls/g1_p18b_decimal_oracle.py)
- Independent checker: [`check_g1_p18b.py`](../controls/check_g1_p18b.py)
- Rust diagnostic: [`p18b_oracle_probe.rs`](../crates/actinv-data/src/bin/p18b_oracle_probe.rs)
- Evidence: [`g1_p18b_decimal_oracle.json`](../results/g1_p18b_decimal_oracle.json) and
  [`g1_p18b_check.json`](../results/g1_p18b_check.json)

The official references are ENDF-102 (SHA-256
`77a0fee413c3b1d5d74a161ed9fe7f77bbcbc58a654304851b7b2b400183d022`) and IAEA-NDS
[`ENDF-utility-codes`](https://github.com/IAEA-NDS/ENDF-utility-codes) commit
`c2a6718bd831b5c8a6e975beb1946954b1d73c40`. Their source and compiled programs remain outside Git; the evidence
records the exact source, compiler, flags, binary and generated-tape hashes.

G2 may classify the complete four-projectile source/runtime corpus only after this checkpoint is committed, pushed
and green. Measurement values remain sealed.
