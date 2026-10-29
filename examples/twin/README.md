# twin: which mesh cells clear a limit, with the nuclear-data band counted?

`actinv twin` evaluates a limit on total activity (500 Bq/g, illustrative, set by hand) against the certified bands
of each cell and cooling time of a banded mesh result, and rolls the cells up into a facility verdict. A cell clears
only when the upper edge of its band is under the limit. It uses the mesh output of `examples/export_r2s`, so run
that example's `mesh` step first; the twin spec names it as `../export_r2s/mesh_banded.ndjson`, relative to the spec.

```sh
actinv data fetch
actinv data fetch tendl-2025-neutron-covariance
actinv mesh examples/export_r2s/mesh_banded.json examples/export_r2s/mesh_banded.ndjson
actinv twin examples/twin/twin.json twin-result.json
```

Expected runtime: the mesh solve as in `examples/export_r2s` (seconds to a minute, not timed); the twin step reads
that file and takes well under a second.

In `twin-result.json` (`actinv-twin-1`) look at `per_cell` (margins by time and limit, and each
cell's verdict), `facility` (cleared and restricted cells, `binding_cells`) and `components`. The component `plate` (both cells) clears only
when every member clears at every time. Cell `cell-b` sees 0.35 times the flux of `cell-a`, so the two should clear
at different cooling times.
