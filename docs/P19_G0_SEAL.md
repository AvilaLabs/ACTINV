# P19 G0 seal — finite-dilution self-shielding opening

## Frozen protocol

- `protocols/ACTINV-P19_PROTOCOL.md`
- sha256 `8ee3d561fec513b3038fcbabfc8b57a6cce25e36c992f2af311afb6740acce16`
- Opening commit `d463d57db4bcd87715eaf0d2082d07a71ec36424`

## Scope

Opt-in unresolved-region (MF=2 LRU=2) Bondarenko self-shielding computed by a
deterministic stratified-quantile PURR-equivalent processor
(`actinv build-shielding` → `actinv-shield-table-1`), applied through a
`self_shielding` spec section. Infinite dilution is an explicit mode; absent
section gives byte-identical output to the P23-closed baseline. Resolved-region
pointwise shielding, probability-table transport, subgroup methods, and
heterogeneous corrections are explicitly out of scope.

## Inventories (recorded)

- `results/p19_lru2_inventory.json` — 2715 of 2850 TENDL-2025 neutron files
  carry LRU=2 unresolved blocks; all six test-set materials covered
  (W-186 4.341–25 keV, Ag-107 7.001–94.0 keV, Ta-181 3.997–25 keV,
  Nb-93 7–600 keV, U-238 20–149 keV, Fe-56 0.85–1.70 MeV; all LRF=2 SLBW).
  Mn-55 and Pb-208 carry none — honest-absence cases.
- `results/p19_oracle_inventory.json` — NJOY2016.79 binary identity, the live
  W-186 PURR probe (MT=152/153 produced, ~10× total cross-section response
  across the σ0 grid, PENDF preserved at `results/p19_w186_probe_pendf.tape23`),
  and the FENDL-3.2c GENDF dilution-block files for W-186/Ag-107/Fe-56.

## Identity baseline

`controls/g0_p19_battery.py` at the G0 commit records normalized SHA-256 for
CLI cold/warm, Python and one-cell mesh surfaces into
`results/g0_p19_identity_baseline.json`. Candidate gates re-run the same
battery and require byte-identical normalized results when `self_shielding`
is absent.

## Core case

`controls/p19_core/` is an Avila Core case package (contract, registry, three
package-declared `external_checker` adapters, pinned input artifacts). At G0
the package executes only the `njoy-purr` step — the independent oracle
production is itself receipt-tracked. The `actinv-shield` and `shield-compare`
adapters, capability types and requirement slots are authored now and enter
the contract workflow at the gate where their artifacts exist; each contract
revision is recorded in the campaign lineage.

## Checker

`controls/check_g0_p19.py` re-derives the baseline equality claims from stored
hashes, re-asserts all 20 prior verdicts plus P23-PASS, verifies the frozen
protocol hash and opening-commit ancestry, checks the coverage/oracle/Core
inventories, and rejects planted baseline mutations under `--self-test`.
