# ACTINV P26 — Amendment 1 (contract population repair)

Opened 2026-09-17 during G3 preparation, before any G3 measurement ran. Under the P26
protocol this is the single append-only repair round: if the phase otherwise passes it
closes `P26-CONDITIONAL`; a second repair need fails the phase.

## Defect

The G2 contract (`results/g2_p26_contract.json`, SHA-256
`f311788383c5728308a996ef734ff0f50cef62e75ba6bc9a3cdf952237ee6eaf`) declared spectrum
`irdff_sp_mat9861` as MAT 9861 from `IRDFF-II_sp.g` with `groups: 44`. Inspection before
execution showed two errors:

1. MAT 9861's MF=3/MT=261 TAB1 carries **726 points (725 histogram bins)**, not 44 — the
   frozen field was factually wrong.
2. The raw 725-group histogram cannot feed the shipped spec interface: `structure:
   "custom"` spectra must match the library's group structure, and the pinned NPZ is
   fispact-709. As written, 8 of 16 flagship cases and 500 of 1000 campaign cases would
   have been `unsupported_input` by construction — a population defect, not a capability
   finding.

## Repair R1

`irdff_sp_mat9861` is redefined as `irdff_sp_mat9861_709`: the MAT 9861 histogram
collapsed onto the fispact-709 boundary set carried as `bounds` in the hash-pinned NPZ,
by the frozen overlap-conserving rule

    flux_709[g] = Σ_i  y_i · |[x_i, x_{i+1}] ∩ [e_lo(g), e_hi(g)]|

which conserves ∫φ(E)dE exactly. The archive pin (path + SHA-256 + member + MAT) is
unchanged; the NPZ becomes an additional pinned input for its boundary set. Population
sizes (16 flagship cases, 1000 campaign cases) and all tolerances, decision rules,
failure categories and partitions are unchanged.

No measurement under the contract had run when the defect was found; nothing was fitted
to an observed result.
