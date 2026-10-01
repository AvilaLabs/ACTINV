# ACTINV-P99 — Isomer labels without decay data: no merged physical states

Date: 2026-09-30. Status: **frozen before the changed code is written.**

## Defect

`build-library` maps each emitted product state's raw LFS to a canonical LISO, in this order:

1. a target-catalog excitation or LIS match;
2. otherwise, when `--decay` is given, the decay sublibrary;
3. otherwise, the label's rank inside the (MT, ZAP) declared LFS set
   (`no_catalog_rank_mapped_lfs`).

Rank (step 3) is computed per MT and knows nothing about the catalog-matched labels. One canonical
(ZAP, LISO) can therefore stand for two physically different states. The inventory then merges
two isomers with different half-lives under one name.

Measured on the TENDL-2025 proton library (built for P94 G2b without `--decay`, identical from
the master binary `0d8dc849…` and P94's candidate):
- 8,461 rows are rank-mapped.
- 545 (MT, ZAP, LISO) groups in 435 targets hold two raw labels. Every one pairs a rank-mapped row
  with a catalog-matched row: 531 `catalog_excitation_match`, 14 `catalog_lis_label_match`.
  - Example: Bi-196 via MT5 from many targets. The 169 keV level ranks to LISO 1; the 271 keV
    level matches catalog LISO 1.
- Counted across MTs within a target, 859 row pairs share a positive (ZAP, LISO) at excitations
  more than 1 keV apart. No pair splits one excitation across two labels.

Builds with `--decay` resolve through the decay sublibrary and show no such merge; the main
TENDL-2025 gamma build (P94/P98 G3) is one. The shipped neutron libraries are not affected:
- `tendl-2025-neutron-709g` maps through decay data;
- the patched neutron library routes unmatched labels to explicit leakage.

This is a defect in ACTINV's own builder, not in the evaluations.

## Change under test

Branch `p99-isomer-labels` from the master commit that registers this protocol. One rule is added
after state mapping, per target. It applies only to rows decided `no_catalog_rank_mapped_lfs`,
which exist only without decay data.

- Rows are grouped by positive canonical (ZAP, LISO). Two excitations agree when they lie within
  the builder's `excitation_tolerance` (max(1 eV, 5e-6 relative)).
- **A group with an anchored row** (any decision other than `no_catalog_rank_mapped_lfs`):
  - a rank-mapped row whose excitation agrees with no anchored row's becomes explicit leakage
    (`zap 0, lfs 0, lmf −3`, decision `no_catalog_rank_collision_to_leakage`);
  - its production is retained, and a ledger line names the row and recommends `--decay`.
- **A group with only rank-mapped rows:**
  - if every pair agrees, the group is kept;
  - otherwise every row in it becomes explicit leakage under the same decision.
- A rank-mapped row with no excitation (`mapping_excitation_eV` absent) in a group of two or more
  rows becomes leakage.
- Nothing else changes:
  - catalog-matched, decay-matched and ground rows keep their labels and bytes;
  - builds with `--decay` are untouched.

## Gates

Reference: master release `actinv`, whose SHA-256 the checker records. Checker `controls/check_p99.py`
runs under the 6 GB cgroup cap.

- **G0:** the protocol hash is registered before the change is written.
- **G1:** fmt; clippy `-D warnings`; `cargo test --release -p actinv-core -p actinv-data`. Unit
  tests on synthetic evaluations, without decay data, cover:
  - an anchored collision (the rank row becomes leakage, the catalog row is unchanged);
  - a cross-MT rank collision with no anchor (all its rows become leakage);
  - an agreeing rank row (kept);
  - an excitation-less rank row in a shared group (leakage);
  - the same evaluation built with decay data (unchanged).
- **G2, unchanged with decay data.** The TENDL-2025 proton library built with
  `--decay endf-b-viii-0_decay.dat --decay-fallback jeff-3-3_decay.dat --continue-on-error true` is:
  - byte-identical in `.npz` between candidate and reference;
  - identical in index apart from the builder-identity fields, which are named.
- **G3, no merged states without decay data.** On the TENDL-2025 proton library built without
  `--decay` (both binaries), the checker reads only the index and `.npz` and requires:
  - (a) Every positive (ZAP, LISO) group in every target has no rank-mapped row whose excitation
    is outside tolerance of every anchored row, and no unanchored group with a pair outside
    mutual tolerance.
  - (b) Every row the candidate routes to `no_catalog_rank_collision_to_leakage` is a
    rank-mapped row of a reference group that violates (a), and every rank-mapped row that
    violates (a) in the reference is so routed.
  - (c) Per target, the candidate's `.npz` rows equal the reference's after one step: each row of
    the (b) set is relabeled to (zap 0, lfs 0, lmf −3). Rows are compared as multisets of (MT, ZAP,
    LFS, LMF, cross-section bytes). The comparison is exact and does not depend on order, so no
    cross section changes and no row is added or lost.
  - Reported: rows rerouted, groups affected, targets affected.
- **G4:** CI replay, every step exits 0.

Merge only if G0–G4 all pass. A FAIL stands; thresholds are not lowered afterwards.
