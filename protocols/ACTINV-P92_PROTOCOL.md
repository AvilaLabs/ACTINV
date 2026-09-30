# ACTINV-P92 — Gas production (H and He isotopes, appm); supersedes P91

Date: 2026-09-30. Status: **frozen before the changed code is written.**

## Supersedes P91 (withdrawn before any code was written)

P91 (`d6a8381e…`) routed light ejectiles into five stable sink states. That is wrong for tritium:
ejected H3 decays to He3 with a 12.3-year half-life. With stable sinks, He3 appm over long cooling
would be understated, and tritium activity would be missing from the activity and heat totals, which
matters for Li- and Be-bearing materials. FISPACT-II adds ejectiles to the inventory as the real
nuclides. P91 is withdrawn untested; its file and hash stay on record. Its cross-code thresholds are
carried over unchanged.

## Change under test

Branch `p91-gas` (name predates the withdrawal) from `7217849`. Enabled by a new spec option
`options.gas: true`, default false. With gas off, nothing changes.

1. **Ejectile table.** Light-particle multiplicities (p, d, t, He-3, α) and neutron multiplicity for
   every neutron MT in 11–45 (excluding the 18–21 and 38 fission MTs), 102–117 and 152–200, from the
   ENDF-6 manual's reaction definitions. MT 4 and 51–91 (inelastic) and MT 102 emit none. Product rows
   whose MT is not in the table contribute no ejectiles; they are reported in the ledger
   (`gas.uncovered`) with their share of the reaction rate. MT 18 is one of these: ternary-fission
   gas is not modelled.
2. **Ejectiles become inventory nuclides.** When enabled, each activation reaction also produces its
   ejectiles into the chain's own states H1, H2, H3, He3 and He4 (ZA 1001, 1002, 1003, 2003, 2004,
   ground state), at multiplicity × rate from the target's column. The reaction is counted once per
   reaction: its product rows (the rows that partition it over residual states) share it, and any row
   that does not represent a residual (for example an ejectile marker) adds nothing. Each decay mode
   adds branching × λ into He4 for each RTYP digit 4 and into H1 for each digit 7 (for example
   RTYP 1.4 gives one α). These nuclides then decay and react like any other state: H3 decays to He3,
   and secondary reactions such as He3(n,p)H3 apply when the library has them. Trace mode feeds them
   from bulk targets through the unit source, as it feeds other products. Hybrid reservoirs apply
   when H or He is a bulk constituent. The new edges are part of the graph used for pruning. If a
   light state is absent from the decay library, a stable sink stands in and the ledger names it.
3. **Output.** Each step gains a `gas` block. Per species it gives `atoms_per_g` (inventory),
   `produced_atoms_per_g` (inventory minus initial) and `appm` (produced atoms per 10⁶ initial atoms
   of the material). It also gives `H_appm`, `He_appm` and `initial_atoms_per_g`. The light nuclides
   also appear in the inventory, activity and heat, as in FISPACT-II. The ledger records the table
   version, uncovered rows and any missing light states.
4. **Scope.** Gas is refused with a clear error together with `uncertainty`, and for projectiles
   other than neutrons (v1). Mesh cells carry the block when the mesh spec enables it. `docs/SPEC.md`
   and `docs/QUANTITIES.md` document it.

## Gates

Reference: release `actinv` `0d8dc849…` (source identical to `7217849`; archived as
`target/p91/ref_actinv`). Checker `controls/check_p92.py`, under the 6 GB cgroup cap; logs in
`target/p92/`.

- **G0:** the protocol hash is registered before the change is written.
- **G1 static:** fmt; clippy `-D warnings`; `cargo test --release -p actinv-core -p actinv-data`.
  Tests include:
  - a Z/A balance of the ejectile table (target + neutron = residual + ejectiles + neutrons for every
    table MT);
  - a synthetic chain where gas equals the analytic integral;
  - H3 ejectiles decaying to He3 at the tabulated λ;
  - a decay-alpha case;
  - trace and coupled gas agreeing at low fluence (relative 1e-6).
- **G2 balance on real data:** over every neutron product row of the TENDL-2017 FNS artifact (the
  CB3 one) and of the default TENDL-2025 library, each row whose MT is in the table has a residual
  that satisfies the Z/A balance. Reported: the count and rate share of uncovered rows.
- **G3 bitwise, gas off:** the 783 P75b single specs (full result JSON without timing keys) and the
  three mesh profiles at 1 thread (bytes, footer without timing) are identical to the reference.
- **G4 non-perturbation, gas on:** on the 783 P75b specs, every inventory nuclide other than the five
  light nuclides agrees with the gas-off run to a relative 1e-9; nuclides below 1e-12 of the step's
  largest population are excluded. The activity and heat totals minus the light nuclides'
  contributions must also agree to a relative 1e-9.
- **G5 same-data cross-code (pre-registered, carried over from P91):** the CB3 configuration
  (TENDL-2017 artifact, 132 FNS experiments) is run with gas enabled. At the end of each irradiation
  step, ACTINV's produced appm per species is compared with FISPACT-II's printed `APPM OF` values from
  the frozen TENDL-2017 outputs at the same time. This covers every (experiment, species) pair with a
  FISPACT value of at least 1 % of that experiment's summed gas appm:
  - at least **90 %** of those pairs must have a ratio in **[0.90, 1.10]**;
  - the per-species pooled geometric-mean ratio must lie in **[0.97, 1.03]** for He4 and H1.

  Reported only: all pairs, the other species, the tail.
- **G6 CI replay:** every step of the local CI replay exits 0.

Merge only if G0–G6 all pass. A FAIL stands; thresholds are not lowered afterwards.
