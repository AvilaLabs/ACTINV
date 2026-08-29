# P17 held-out validation: result and successor evidence

P17 closed `P17-FAIL` because its first post-unseal amendment made assumptions that the held-out source disproved.
That verdict is procedural and scientific: the evidence is preserved exactly as observed instead of changing the
mapping after seeing the answers. P17 changed no ACTINV production code, public interface, default, package or nuclear
data, so this result does not withdraw the released v1.0.1 software. It does narrow what can be claimed and defines
the work that P18 must address.

## What the held-out rows say

Every one of the 94 source rows is retained: 40 SPR-III spectral indices, 33 ACRR spectral indices and 21 high-
temperature Maxwellian cross sections. Rows needing an unsupplied cover or self-shielding calculation are visible as
unscored; they are not approximated as dilute foils.

| family | ACTINV/TENDL-2025 scored | geometric mean C/E | median abs(ln C/E) | p90 abs(ln C/E) | within 10% | within 30% |
|---|---:|---:|---:|---:|---:|---:|
| SPR-III Table 23 | 0 / 40 | — | — | — | — | — |
| ACRR Table 25, supported threshold responses | 12 / 33 | 1.01365 | 0.03544 | 0.08286 | 100% | 100% |
| Maxwellian Table 36 | 21 / 21 | 1.03881 | 0.07326 | 0.34832 | 61.9% | 85.7% |

The ACRR result is the cleanest positive signal: all twelve independently calculable threshold reactions are within
10%, and ACTINV's geometric-mean C/E is 1.014. The remaining ACRR rows are not losses. Twenty need publication-
specific cover or finite-foil self-shielding, and the Ni-58 monitor identity is not counted as predictive accuracy.

Eighteen of the 21 Maxwellian rows are within 30%. The material misses are the In-113 metastable branch, the In-113
ground-state branch and Ag-109 capture. Substituting the current hash-pinned IRDFF-II pointwise evaluation does not
remove those misses: it also disagrees materially on the same three rows. In-113 total capture remains near the
measurement while its two state branches move in opposite directions, bounding the problem to evaluated branch
allocation/source-version or measurement-definition behavior rather than ACTINV's numerical solver. The primary
paper itself leaves the Ag-109 evaluation-versus-experiment cause unresolved.

These numbers are post-failure diagnostics, not a repaired blind score. Once the source was unsealed, it could not be
re-sealed by renaming a file or changing a threshold.

## Why P17 failed

The committed Amendment 1 is falsified in three concrete ways:

1. Its 960-second uniform-power saturation formula does not reproduce the printed EOI spectral indices. The maximum
   relative discrepancy is essentially 100% because the publication's `Ag109g` abbreviation was mapped to the wrong
   product state. The independently evaluated pulse limit, with the publication's state-specific alias, reduces the
   maximum discrepancy to 3.58% and most rows to about 1% or less.
2. Tables 25 and 36 abbreviate the evaluated `Ag109gm` response as `Ag109g`; the raw IRDFF evaluation uses sparse
   final-level identifier 2 while ACTINV's processed decay-isomer selector is ordinal 1. Amendment 1 did not freeze
   that source-specific distinction.
3. `bare` does not mean infinitely dilute for a finite resonance foil. The source says shielding effects were treated
   rigorously but does not print the correction factors. An unshielded current-archive fold differs from the
   published Ag-109 bare index by about 240%, proving that those capture rows cannot be scored as dilute.

Fixing any of those after the committed amendment would be a second post-unseal repair. The original protocol says a
second repair closes `P17-FAIL`, so the checker derives that verdict without discretion.

## Independent controls and source anomaly

For Table 36, a 16-point Gauss-Legendre integration over every nonzero pointwise ENDF interval independently folds the
current IRDFF-II responses. The same responses folded from the distributed groupwise archive agree within 0.5% on
all 21 rows. ACTINV's shipped TENDL-2025 groups are integrated with the publication-defined Maxwellian convention:
stellar `2/sqrt(pi)` normalization except for the two laboratory Wallner U-238 measurements.

The final Table 36 row literally prints measured U-238 SACS `108.0 mb`, calculated SACS `389.9 mb` and C/E `1.011`.
The PDF image confirms the inconsistency. The independent current-IRDFF fold is `109.24 mb`, consistent with the
printed C/E. The evidence retains all three printed literals and never overwrites the bad calculated cell.

The compact checker independently repeats source hashes, EOI ratios, all C/E arithmetic, linear-percentile metrics,
row/exclusion conservation, the pointwise/groupwise differential, failure conditions and append-only cause-ledger
coverage. It rejects ten planted mutations covering values, units, row identity, inclusion, family, hashes, metrics,
cause removal, verdict flipping and the source anomaly.

## Reproduction

The committed checker is bulk-data-free:

```bash
python controls/check_g5_p17.py --no-write
```

With the exact public inputs and hashes listed in the P17 protocol, regenerate the external-data evidence with:

```bash
python controls/g5_p17_heldout.py
python controls/check_g5_p17.py
```

The raw evaluation archives, benchmark PDF, spectra and generated libraries remain outside Git. The compact result is
`results/g5_p17_heldout.json`; the append-only successor segment is `results/p17_cause_ledger_g5.json`.

## What moves to P18

P18 must use a fresh, pre-result protocol. The unsealed P17 families can guide diagnosis but can never again be
called blind evidence. The demonstrated priorities are state-specific product mapping and evaluated branch
allocation, source-version/measurement transformations, and explicit separation of unsupported self-shielding from
dilute activation. Finite-dilution physics remains P19 scope unless P18's frozen protocol explicitly limits itself to
data/mapping corrections that do not pretend to solve transport.
