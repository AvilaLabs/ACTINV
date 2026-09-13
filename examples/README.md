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
| `mesh_demo.json` + `mesh_flux.ndjson` | `actinv mesh`: two independent cells (full and 0.35× flux) solved in parallel; the flux file is the canonical `actinv-flux-1` interchange format. The spec accepts `group_workloads`, `cell_result_fields`, `memory_limit_bytes` and `resume` — see `docs/SPEC.md`. |
| `shielding_demo.json` | `self_shielding` on `actinv-spec-1`: pure W-186 under a 4–25 keV band at fixed σ0 = 0.1 b folds the unresolved-range Bondarenko table into the group rates; the ledger records the applied factors and the W-187 activity lands ~28% below the unshielded run. The demo pins `results/g1_p19_shield_artifact.json` (built by `controls/p19_shielding.py`). |

`damage_demo.json`, `mesh_demo.json`, and specs from `actinv new` use portable `catalog:` references resolved
against `./actinv-data` or `$ACTINV_DATA_DIR` — see the *data references* paragraph in `docs/SPEC.md`.

No runnable `uncertainty` example ships here: the MF=33 covariance sidecar is a user-built artifact
(`actinv build-covariance RAW_TENDL_DIR ACTIVATION.npz OUT.cov.npz`), and its `<stem>_index.json` must hash-match the
exact activation library it pairs with. The spec surface — `covariance`, `channels` (`decay_constants`,
`fission_yields`), `confidence_level`, `require_complete` — is documented under *MF=33 uncertainty* in
`docs/SPEC.md`, and `controls/g4_p20_channels.py` is an executable end-to-end demonstration on both synthetic and
real-data legs.
