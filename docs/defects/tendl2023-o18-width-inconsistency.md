# O-18 Breit-Wigner total width below component sum (TENDL-2023 only)

Found 2026-09-26 during decay-aware TENDL-2023 FNS library construction.
Status: superseded — TENDL-2025's O-18 evaluation is regenerated and does
not carry this record. Logged for the ledger; no upstream action needed.

## Observation

`n_008-O-18_0831.dat`, MF=2/MT=151 resolved-resonance LIST record at
physical line 88 declares a total width below the sum of its components
(GT < GN+GG+GF):

```text
 4.090000+6 5.000000-1 6.500000+3 6.499980+3 2.300000-1 0.000000+0 831 2151   15
```

ER = 4.090000+6 eV, AJ = 0.5, GT = 6500.00 eV,
GN+GG+GF = 6499.98 + 0.23 + 0 = 6500.21 eV — total short by 0.21 eV.

A strict reader that validates GT ≥ GN+GG+GF fails the evaluation; ACTINV's
`fix_bw_gt` normalization rewrites GT to the component sum and ledgers the
intervention.

## Source

```text
9b51409a3da72adc1945910cb0b5959aa70a1e443bfdc4ea6a3b6da05200f2df  n_008-O-18_0831.dat  (TENDL-2023)
```

## Resolution in TENDL-2025

`n-O018.tendl` (TENDL-2025) is an entirely regenerated evaluation —
different resonance-range boundaries (1e-5 … 9.090110+5 eV vs
1e-5 … 3.15e6 eV) and different parameters; the offending record does not
exist.
