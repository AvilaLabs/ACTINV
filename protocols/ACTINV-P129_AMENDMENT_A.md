# P129 Amendment A — Repair the stale table hash in `examples/shielding_demo.json`

Registered 2026-10-15, after the first P129 checker run and before any change to the example.

## Finding

The first run (`results/failures/p129_initial/p129_shielding.json`) could not evaluate G2 or G4 on
`examples/shielding_demo.json`. Both the reference (`2f3daa4`) and the candidate binary refuse it:
`SHA-256 mismatch for results/g1_p19_shield_artifact.json: declared b812251…1384, computed 8f46bde…9fe1`.

Commit `e333402` (D6e, SIGMA1 Doppler broadening) regenerated that artifact without updating the hash the example
declares. The example has been unrunnable since then, independent of P129. G3 passed and `fns_fe_5min` was
byte-identical apart from `ms`.

## Amendment

1. Set the example's `self_shielding.table.sha256` to the artifact's current SHA-256
   (`8f46bde47a9ac390057aa4a9eb1af36ec5d043536693f8a6c96efb212e1c9fe1`). No other byte of the example or the artifact
   changes.
2. G2 and G4 are then evaluated on the repaired example exactly as registered. G2 compares reference and candidate on
   the repaired example. Every other gate and threshold is unchanged.
3. Add a parking entry for the stale-hash defect and its CI blind spot: `controls/check_p18b.py` lists the example
   but did not catch it.
