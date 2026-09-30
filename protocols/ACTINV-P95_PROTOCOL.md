# ACTINV-P95 — Gas production, FISPACT-II inventory-appm convention (successor to P92)

Date: 2026-09-30. Status: **frozen before the changed code is written.**

## Why this protocol exists

P92 (`dae429bb…`) failed. G0–G4 passed:
- Z/A balance held with 0 failures over 36,451 TENDL-2017 rows and 93,417 TENDL-2025 rows;
- gas-off output was bitwise identical on 783 specs and 3 mesh profiles;
- with gas on, every non-light nuclide's inventory and activity were exactly identical.

G5 failed.
- 400 of 403 gated pairs were within ±10 % (required ≥ 90 %).
- The pooled geometric-mean ratios were:
  - He4 0.99997;
  - H2 0.99969;
  - H3 0.9999993;
  - **H1 0.513**, where [0.97, 1.03] is required.

The H1 failure comes entirely from the three hydrogenous FNS samples (I, Br, Cl; 2000exp_5min).
They are organic compounds holding 2.1–2.5 wt % hydrogen. FISPACT-II prints `APPM OF H 1` at about
3.3–3.8 × 10⁵ for them. That is the same value at time zero, and it equals the initial hydrogen atom
fraction computed from the input composition. FISPACT-II's printed APPM is therefore the species'
**inventory** per 10⁶ initial atoms, including the initial content. P92 compared it with ACTINV's
**produced** appm, which excludes the initial content. The P92 FAIL stands in the ledger.

This protocol fixes the compared quantity. Its outcome is largely known in advance. For any
species with no initial content, inventory appm equals produced appm, so those pairs repeat P92's
ratios. Only pairs where the material starts with that light nuclide change. So this protocol is a
definitional correction, not an independent test of gas physics; P92's G5 numbers are that test.

## Change under test

On branch `p91-gas`, on top of the P92 candidate (`8790401…`):

1. Each gas species gains `inventory_appm` = `atoms_per_g` / `initial_atoms_per_g` × 10⁶. This is
   FISPACT-II's printed `APPM OF` convention.
2. The block gains `H_inventory_appm` (H1+H2+H3) and `He_inventory_appm` (He3+He4).
3. The existing fields and their meanings are unchanged: `appm` is still produced appm.
4. `docs/SPEC.md` states both conventions and which one FISPACT-II prints.

## Gates

Reference: release `actinv` `0d8dc849…` (as in P92). Checker: `controls/check_p92.py`, with G5
switched to `inventory_appm` and logs in `target/p95/`.

- **G0:** the protocol hash is registered before the change is written.
- **G1:** fmt, clippy `-D warnings`, `cargo test --release -p actinv-core -p actinv-data`. A test
  checks `inventory_appm` against a material with initial hydrogen: the value at step 0 equals
  the initial atom fraction × 10⁶.
- **G2:** as P92 G2, the Z/A balance over both libraries (the table code is unchanged).
- **G3:** as P92 G3, gas-off output bitwise identical to the reference over 783 specs and 3 mesh
  profiles.
- **G4:** as P92 G4, as executed, including the heat clause
  `|(Q_on − L_on) − (Q_off − L_off)| ≤ 1e-9 |Q_off − L_off| + 1e-12 |Q_on|` per component. Here L is
  the light nuclides' activity × MF=8/MT=457 mean energy.
- **G5:** P92's G5 unchanged except that ACTINV's `inventory_appm` replaces `appm`:
  - same 132 experiments, same end-of-irradiation step;
  - same pair rule: FISPACT value ≥ 1 % of that experiment's summed gas appm;
  - same thresholds: ≥ **90 %** of ratios in **[0.90, 1.10]**, and He4 and H1 pooled
    geometric-mean ratios in **[0.97, 1.03]**.
- **G6:** CI replay, every step exits 0.

Merge only if G0–G6 all pass. A FAIL stands; thresholds are not lowered afterwards.
