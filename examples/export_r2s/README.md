# export-r2s, export-r2s-joint, export-source: banded shutdown-dose sources from a mesh

A two-cell iron mesh (`cell-a` at full FNS flux, `cell-b` at 0.35 times) solved with MF=33 uncertainty bands,
5 minutes of irradiation followed by 1 d, 30 d and 1 y of cooling (steps 2 to 4). `mesh_banded.json` is `examples/mesh_demo.json` with an `uncertainty` block that bands the activity of the
nuclides carrying the decay photons; `export-r2s` and `export-source` need those bands, and `export-r2s-joint`
needs them with the covariance sidecar. The flux, `mesh_flux_boxes.ndjson`, is the flux of `examples/mesh_flux.ndjson`
with each cell placed in space: two 1 cm cubes side by side. `export-source` and `export-openmc-mesh` need those cell
bounds to place each cell's source. The spec names the flux from the repository root, so run the commands there.

```sh
actinv data fetch
actinv data fetch tendl-2025-neutron-covariance
actinv mesh examples/export_r2s/mesh_banded.json examples/export_r2s/mesh_banded.ndjson
actinv export-r2s examples/export_r2s/mesh_banded.ndjson 2 examples/export_r2s/r2s.ndjson
actinv export-r2s-joint examples/export_r2s/mesh_banded.ndjson examples/export_r2s/mesh_banded.json 2 examples/export_r2s/r2s_joint.ndjson
actinv export-source openmc examples/export_r2s/r2s.ndjson examples/export_r2s/source_openmc.xml
actinv export-source mcnp examples/export_r2s/r2s.ndjson examples/export_r2s/source.sdef
actinv export-source serpent examples/export_r2s/r2s.ndjson examples/export_r2s/source.serpent
actinv export-source alara examples/export_r2s/r2s.ndjson examples/export_r2s/alara --shutdown-t-s 300
actinv export-openmc-mesh examples/export_r2s/mesh_banded.ndjson 2 examples/export_r2s/mesh_source.py
```

Step 2 is the 1 d cooling step. `--shutdown-t-s 300` is the end of irradiation, so the ALARA cooling time is
86400 s; `alara` needs an empty or absent output folder. Generated files are git-ignored in this folder.

Expected runtime: `mesh` solves two cells with first-order bands; it took 21 s on a laptop limited to two cores,
with the data installed (the first run also builds the prepared-data cache). The exports read the mesh output and
take well under a second, except `export-r2s-joint`, which rebuilds the sensitivities and takes about as long as the
mesh solve.

In `r2s.ndjson` (`actinv-r2s-source-1`) look at each cell's `photons_s`, its independent and conservative sigmas,
and `coverage` (the share of the photon source carried by nuclides without a band). The joint file adds the
cell-to-cell correlated sigma in its footer. `examples/twin` takes the same mesh output.
