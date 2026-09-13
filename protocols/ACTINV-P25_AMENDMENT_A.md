# ACTINV P25 Amendment A — corrected field reading for the MF=8 state-declaration example

Recorded 2026-09-13, after the protocol freeze commit
`77dfaef8c1cb4067c8cc8e1b7c4a686123161ea7` and before any P25 gate beyond G0 executes. This amendment
corrects a factual detail in the frozen protocol's third finding and changes no gate, threshold,
taxonomy class, scope limit or evidence rule.

## Correction

The frozen protocol's finding 3 states that `d-Ag104m.tendl` "MF=10/MT=103 carries two state tables
(LFS=0 and LFS=1) while its MF=8 declares NSP=1."

The MF=8 field was misread. The MF=8/MT=103 header declares **NSP=2**, and its two product records
are ZAP=47105 LFS=0 and ZAP=47105 LFS=1 at ELFS=25468 eV — consistent in count and identity with the
two MF=10 state tables. No MF=8-vs-MF=10 state-count inconsistency exists in this file.

The defect actually observed in this file is the recorded construction failure itself: the emitted
mutually-exclusive state sum for MT103/MF=10 ZAP=47105 exceeds the runtime total by relative excess
1.0 at group 20, where both magnitudes are at the ~1e-21–1e-22 barn floor. Whether that excess is a
floor-value artifact of two co-equal floor states, a genuine source inconsistency at physical
energies, or a processing defect is exactly what G2's mechanism traces must adjudicate — the frozen
taxonomy (`tiny_absolute_discrepancy` vs `genuine_source_inconsistency` vs `processing_bug`) already
covers all three outcomes.

## Unchanged confirmations

- `a-Ta181.tendl` MF=8/MT=4 declares residual ZAP=75184 (Re-184) — the residual is a different
  nuclide than the target, confirming that MT=4 in this charged-particle file is a neutron-emission
  channel, not same-residual inelastic scattering.
- The frozen taxonomy, repair rules, acceptance gates, evidence rules and gate structure are
  unchanged.
