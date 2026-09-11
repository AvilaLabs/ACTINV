# Runnable examples

Each file is a complete `actinv-spec-1` problem (or its companion data); run any of them with
`actinv run <file>.json result.json` from the repository root after `actinv data fetch`.

| file | demonstrates |
|---|---|
| `fns_fe_5min.json` | The canonical FNS iron benchmark: 709-group spectrum, 5-minute irradiation, decay chain. |
| `pulsed_demo.json` | Five 60 s shots separated by 10 min cooldowns — repeated pulse history. |
| `feed_removal_demo.json` | Continuous `feed` of Co-60 and first-order `removal` of Mn-56 during irradiation, with the audited `removed` sink. |
| `damage_demo.json` + `damage_table_demo.json` | NRT damage observables (dpa) from a hash-pinned `actinv-damage-table-1`; the demo table is synthetic and honestly names uncovered nuclides. |
| `reverse_demo_problem.json` + `reverse_demo_measurements.json` | `actinv reverse` flux estimation: the measurements were produced by a multiplier-2.0 forward run; the recovered estimate is 2.0. |
| `mesh_demo.json` + `mesh_flux.ndjson` | `actinv mesh`: two independent cells (full and 0.35× flux) solved in parallel; the flux file is the canonical `actinv-flux-1` interchange format. |
| `shielding_demo.json` | `self_shielding` on `actinv-spec-1`: pure W-186 under a 4–25 keV band at fixed σ0 = 0.1 b folds the unresolved-range Bondarenko table into the group rates; the ledger records the applied factors and the W-187 activity lands ~28% below the unshielded run. The demo pins `results/g1_p19_shield_artifact.json` (built by `controls/p19_shielding.py`). |

`damage_demo.json`, `mesh_demo.json`, and specs from `actinv new` use portable `catalog:` references resolved
against `./actinv-data` or `$ACTINV_DATA_DIR` — see the *data references* paragraph in `docs/SPEC.md`.
