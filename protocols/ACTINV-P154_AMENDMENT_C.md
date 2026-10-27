# P154 Amendment C — corrections to Amendment B before any evidence run

Registered before any evidence run. No candidate change. While Amendment B was being implemented, the checker was
evaluated on development run 2's outputs in reuse mode, which runs neither NJOY nor ACTINV. That evaluation showed
that Amendment B's text is wrong in three places. Tolerances are unchanged, and the raw NJOY comparisons are still
reported.

## Findings

- **B1/B2 precondition.** B picks a group when the emulated term reaches 99 % of NJOY's MT=447 (or MT=446), then
  requires the emulation to be within 0.5 %. A group where the true share is 99.0–99.5 % therefore fails whatever the
  emulation does. On development run 2 every precondition failure lies in that band. The cause is the small
  remainder from Fe-56 (n,α) and Ni-58 (n,p), not an emulation error: elsewhere the emulation reproduces NJOY to a
  median of 4e-8 (Fe-56, Cr-52), 2e-4 (Ni-58) and 7e-6 (W-184). Because B picks groups with the quantity it tests,
  an emulation that runs low would be excluded rather than fail.
- **B2 cause.** B attributes the W-184 inelastic difference to `disbar`'s 1.1-step ladder. The emulation shows the
  ladder contributes about 1e-7 there. The actual cause is that HEATR tabulates σ·W only at the PENDF grid energies
  and interpolates the product linearly between them. Just above a level's threshold both σ and W rise from zero, so
  the interpolated product is too high. In the 115 keV group NJOY gives 29.6 against about 19.5 from σ(E)·W(E).
  Applying B2's difference only at the grid energies leaves a 34 % worst group.
- **NJOY table reading.** HEATR's MT=444–447 tables print 7-digit energies for values computed at the 9-digit RECONR
  grid energies, and they drop their leading zero row. A narrow Fe-56 resonance at 149829.35 eV, printed at
  149829.4, alone moves one group by 0.9 %.

## C1. Precondition groups (replaces the selection rule in B1 and B2)

A precondition set is formed from an independent bound `U` on the terms the emulation leaves out. `U` comes from the
PENDF cross sections:
- **The recoil bound.** For each non-emulated MT, `U` uses the largest laboratory kinetic energy the residual
  nucleus can have. All emitted particles are treated as one cluster of mass `m_b`, and `m_R = A + 1 − m_b`, in
  neutron masses:
  `T_max(E) = ( √( m_b/(m_b+m_R) · (E·A/(A+1) + QI) ) + √( E·m_R/(A+1)² ) )²`, zero where `E·A/(A+1) + QI ≤ 0`.
  Then `U = Σ σ_MT(E) · T_max(E)`, collapsed like the references. No reaction's damage energy exceeds its residual's
  recoil energy, so `U` bounds the left-out terms.
- **The MTs.** B1 sums the disappearance MTs other than 102 that the PENDF carries (103–120, 600–849). B2 sums the
  MT=446 contributors other than 51–90.

A group is in the set when `U ≤ 0.005 × NJOY`. For B2, a group must also lie above the lowest discrete-level
threshold. In every group of the set the collapsed emulated term `X` must satisfy
`NJOY·(1 − 0.005) − U ≤ X ≤ NJOY·(1 + 0.005)`. An exact emulation satisfies this, because `NJOY − X` lies between 0
and `U`. The set must contain at least 10 groups, or G4 fails for that target as vacuous. The set, `U` and the
deviations are reported.

## C2. B2 reference (replaces the B2 formula)

`ref(E) = [446(E) − Σ L_MT(E)] + Σ σ_MT(E) · W_exact,MT(E)`, with both sums over MT=51–90.
- `L_MT` emulates HEATR's level term: σ_MT · W_ladder,MT at the PENDF grid energies, interpolated linearly in between.
- The second term is evaluated on the PENDF grid refined so that consecutive energies differ by at most 2 %. σ_MT is
  interpolated as the PENDF specifies.

C1's precondition applies to `Σ L_MT` against NJOY MT=446. The grid-only form, B2 as registered, is reported.

## C3. Reading NJOY's tables

Before the references are formed, each table energy is mapped to the RECONR grid energy it rounds from, and a dropped
leading zero row is restored at the preceding grid energy. The raw comparisons use the tables as read.

B1's formula and B3 are unchanged, and B3 uses the C2 reference.
