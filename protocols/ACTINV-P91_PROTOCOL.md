# ACTINV-P91 — Gas production (H and He isotopes, appm)

Date: 2026-09-30. Status: **frozen before the changed code is written.**

## Background

Fusion-materials studies need hydrogen and helium production, which FISPACT-II reports as gas
rates and appm. ACTINV reports neither. Its library rows carry only the residual product of each
reaction, and the builder skips the evaluators' gas-production totals (MT 203–207). Rebuilding the
libraries to add them would take hours.

Every neutron activation row does carry its ENDF MT number, and for MT 11–45, 102–117 and 152–200
the ENDF-6 format fixes exactly which light particles the reaction emits. So gas production can be
computed from the rows already shipped, with no rebuild.

## Change under test

Branch from master `e546292`. Enabled by a new spec option `options.gas: true`, default false.

1. **Ejectile table.** This is a table of light-particle multiplicities (p, d, t, He-3, α) and
   neutron multiplicity for every neutron MT in 11–45 (excluding the 18–21 and 38 fission MTs),
   102–117 and 152–200, taken from the ENDF-6 manual's reaction definitions. MT 4 and 51–91
   (inelastic) and MT 102 emit no gas. Rows whose MT is not in the table contribute no gas and are
   reported in a ledger (`gas_uncovered`), with their share of the total reaction rate. MT 18 is one
   of these rows: ternary-fission gas is not modelled.
2. **Sinks.** When enabled, five stable sink states (H1, H2, H3, He3, He4) are appended to the state
   vector after the existing leakage and removal states. Each activation reaction adds
   multiplicity × rate from its target's column into each sink. Each decay step adds
   branching × λ × multiplicity: one He4 for each RTYP digit 4 and one H1 for each digit 7, so that
   for example RTYP 1.4 gives one α. Trace mode feeds the sinks from bulk targets through the unit
   source, exactly as it feeds other non-bulk products. Sinks have no outgoing terms.
3. **Output.** When enabled, each step gains a `gas` block. Per species it gives `from_reactions`,
   `from_decay`, `in_inventory` (atoms of that nuclide in the inventory minus its initial atoms) and
   `total`, all in atoms per g, plus `appm`. Totals `H_appm` and `He_appm` are also given. appm is
   atoms per 10⁶ initial atoms of the material. The sinks are excluded from `total_atoms_per_g`,
   `n_states_populated`, activity, heat and the inventory list. They are reported only in `gas`.
   Ledger entries record the table used and the uncovered rows.
4. **Scope.** Gas is refused, with a clear error, in combination with `uncertainty` and for
   projectiles other than neutrons (v1). Mesh cells carry the block when the mesh spec enables it.
   The CLI, Python and docs (`docs/SPEC.md`, `docs/QUANTITIES.md`) document it.

## Gates

Reference: master `e546292` release `actinv` (archived as `target/p91/ref_actinv`). Checker
`controls/check_p91.py`, under the 6 GB cgroup cap.

- **G0:** the protocol hash is registered before the change is written.
- **G1 static:** fmt; clippy `-D warnings`; `cargo test --release -p actinv-core -p actinv-data`.
  Tests include a Z/A balance of the ejectile table: for every table MT, target + neutron = residual
  + ejectiles + neutrons. They also include a synthetic-chain test where gas equals the analytic
  integral, a decay-alpha test, and agreement of trace and coupled gas at low fluence (relative 1e-6).
- **G2 balance on real data:** over every neutron row of the TENDL-2017 FNS artifact (the CB3 one)
  and of the default TENDL-2025 library, each row whose MT is in the table has a residual that
  satisfies the Z/A balance. Reported: the count and rate share of uncovered rows.
- **G3 bitwise, gas off:** the 783 P75b single specs (full result JSON without timing keys) and the
  three mesh profiles at 1 thread (bytes, footer without timing) are identical to the reference.
- **G4 non-perturbation, gas on:** on the 783 P75b specs, every step's activity total, heat total and
  each inventory nuclide's atoms per g agree with the gas-off run to a relative 1e-9. Nuclides below
  1e-12 of the step's largest population are excluded.
- **G5 same-data cross-code (pre-registered):** the CB3 configuration (TENDL-2017 artifact, 132 FNS
  experiments) is run with gas enabled. At the end of each irradiation step, ACTINV's produced appm
  per species (total minus initial) is compared with FISPACT-II's printed `APPM OF` values from the
  frozen TENDL-2017 outputs at the same time. This covers every (experiment, species) pair with a
  FISPACT value of at least 1 % of that experiment's summed gas appm:
  - at least **90 %** of those pairs must have a ratio in **[0.90, 1.10]**;
  - the per-species pooled geometric-mean ratio must lie in **[0.97, 1.03]** for He4 and H1.

  Reported only: all pairs, the other species' means, the tail.
- **G6 CI replay:** every step of the local CI replay exits 0.

Merge only if G0–G6 all pass. A FAIL stands; thresholds are not lowered afterwards.
