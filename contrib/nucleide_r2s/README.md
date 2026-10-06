# ACTINV in a Nucleide ALARA/R2S workflow

This optional example passes Nucleide material, neutron-flux, and schedule inputs to ACTINV, runs a fresh activation calculation, then reads ACTINV's decay-photon output through Nucleide's existing ALARA/R2S readers. Nucleide is an optional dependency. `demo.py` remains a lightweight output-only compatibility check; it does not run an activation calculation.

## Versions and data

- Nucleide `0.16.0` and NumPy `2.5.3`, pinned in `requirements.txt` (Python 3.12+ for this pair). The output-only demo needs only Nucleide and supports Python 3.10+.
- ACTINV source commit `0353d5e3288a90cfefaabe7d2263b22fbaeeecad`, which reports version 1.4.0 and contains `export-source alara`. The published 1.4.0 release predates that command. Use the source build below until a release includes it.
- ACTINV data catalog `1.1.0`, default TENDL-2025 neutron bundle. ACTINV fetches and verifies the large data artifacts; they are not checked in.

| Data artifact | SHA-256 |
| --- | --- |
| TENDL-2025 neutron 709-group library | `ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44` |
| Library index | `8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb` |
| ENDF/B-VIII.0 primary decay data | `6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb` |
| JEFF-3.3 fallback decay data | `850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123` |

## Build and run

For the local Linux workstation, run builds and solver jobs inside the bounded systemd scope required by the repository's `AGENTS.md`. From the ACTINV repository root:

```sh
mkdir -p target/nucleide-r2s-source target/nucleide-r2s-build target/nucleide-r2s-data target/preflight-tmp
git clone https://github.com/AvilaLabs/ACTINV.git target/nucleide-r2s-source
git -C target/nucleide-r2s-source checkout 0353d5e3288a90cfefaabe7d2263b22fbaeeecad
bounded() {
  systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 -p TasksMax=128 -p CPUQuota=200% -- \
    env CARGO_BUILD_JOBS=1 RUST_TEST_THREADS=1 RAYON_NUM_THREADS=2 \
    TMPDIR="$PWD/target/preflight-tmp" "$@"
}
bounded bash -c 'cg=$(sed -n "s/^0:://p" /proc/self/cgroup); for f in memory.max memory.swap.max pids.max cpu.max; do cat "/sys/fs/cgroup$cg/$f"; done'
```

Expected values are `6442450944`, `0`, `128`, and `200000 100000`. Stop if limits differ. Build the source CLI, install and verify its standard neutron data bundle, then install the optional Python packages and run the example:

```sh
bounded env CARGO_TARGET_DIR="$PWD/target/nucleide-r2s-build" \
  cargo build --manifest-path target/nucleide-r2s-source/Cargo.toml --release -p actinv-cli --bin actinv --locked
ACTINV="$PWD/target/nucleide-r2s-build/release/actinv"
bounded "$ACTINV" data fetch --output "$PWD/target/nucleide-r2s-data"
bounded "$ACTINV" data verify --output "$PWD/target/nucleide-r2s-data"
python3 -m venv target/nucleide-r2s-venv
target/nucleide-r2s-venv/bin/python -m pip install --only-binary=:all: -r contrib/nucleide_r2s/requirements.txt
bounded target/nucleide-r2s-venv/bin/python contrib/nucleide_r2s/e2e.py \
  --actinv-bin "$ACTINV" \
  --data-root "$PWD/target/nucleide-r2s-data" \
  --out target/nucleide-r2s-e2e
```

The example writes the translated mesh inputs, fresh mesh result, exported R2S source, ALARA `.photonSrc`, and a JSON receipt under `target/nucleide-r2s-e2e/`. It also runs the independently specified literal `actinv-spec-1` control in `direct_case.py` and compares its result with the mapped mesh result. `report.json` records tested software versions, data hashes, schedule, flux normalization, and photon-source conservation. The output directory must be new; choose another `--out` path for each run.

To keep the old output-only compatibility check, run:

```sh
bounded env ACTINV_BIN="$PWD/target/nucleide-r2s-build/release/actinv" \
  target/nucleide-r2s-venv/bin/python contrib/nucleide_r2s/demo.py results/p52_r2s_source.ndjson \
  --shutdown-t-s 300 --out target/nucleide-r2s-output-only
```

This command only runs `actinv export-source alara` and Nucleide's readers; it consumes the committed corpus photon-source fixture and performs no activation.

## Example boundary conventions

The fixture supplies one reference gram of pure Fe-56 in Nucleide's material input, with density `7.874 g/cm³` and zone bounds `[0,2] × [0,1] × [0,1] cm`. The reference composition sets weight proportions, not the zone's absolute mass. The zone volume is `2 cm³`; ACTINV receives `mass_g = 7.874 × 2 = 15.748 g` and `wt_percent: {FE56: 100}`. ACTINV inventories are per gram, while `mass_g` scales the whole-zone photon rate. ALARA source strengths are per volume, so conservation is `group_sum × volume_cm3 = photons_s`; apply volume once. This example accepts one component with `mass_fraction: 1`; it does not implement general multi-component blending.

The group manifest contains 710 explicit eV boundaries; `flux.txt` contains 709 group-integrated flux values. The ALARA order is descending (fast to thermal), with values in `n cm^-2 s^-1` per group. Its populated group 88 spans 14.0–14.2 MeV and contains `5.0e11`; the deck scale `2.0` makes the physical total `1.0e12 n cm^-2 s^-1`. These are already physical prescribed-flux values, not normalized transport tallies, so no source-rate conversion is applied. ACTINV's mesh stream requires ascending energy boundaries; the adapter reverses boundaries and the matching group values together, and rejects nonmatching boundaries or group counts.

The Nucleide schedule expands to 300 s of irradiation at ACTINV flux multiplier 1, followed by 3600 s of cooling at multiplier 0. ACTINV's final result time is 3900 s from schedule start; shutdown is at 300 s. The adapter derives the final one-based result step and shutdown reference from the translated schedule; the ALARA exporter receives `--shutdown-t-s 300`. Neutron input uses FISPACT-709; emitted photons use FISPACT-24, a separate structure.

| Nucleide identity | ALARA nuclide dialect | ACTINV material key | Handling |
| --- | --- | --- | --- |
| Fe-56 ground state (`Fe56`) | `fe:56` | `FE56` | Explicitly mapped in the example. The deck's `fe56` is a material reference. |
| Metastable state (e.g. `Ba137_m1`) | Unsupported | — | Rejected. Do not strip state markers or map to ground state. |

The emitted ALARA file currently contains `TOTAL` rows without nuclide labels, so no nuclide-dialect conversion is needed on the photon-output side.

Nucleide 0.16.0 has no Python ALARA material-library parser. Composition goes through `nucleide.material.mix_by_mass`; `r2s_from_deck` resolves the zone/flux association, and the parsed `mix1` reference must match the external `fe56` material. The Python deck summary also omits `mat_loading` entries, so a small explicit check restricts that block to `zone1 mix1`. The deck must reference exactly `flux.txt`. Nucleide's ALARA dialect uses ground-state names such as `fe:56`; it cannot encode an isomer. Its canonical isomer spelling, such as `Ba137_m1`, is therefore rejected at this example's material boundary.

The runnable example is deliberately fixed to the supplied Fe-56 case. `--inputs` relocates copies of these fixtures; changing their physical material, flux distribution, or schedule is rejected before solving because the independent control specifies this case literally. Schedule unit tests also exercise repeated pulse histories and their zero-flux delays, but the full calculation currently covers only the stated 300 s irradiation and 1 h cooldown.

## Evidence and limits

This demonstrates Nucleide input handling, ACTINV input translation, fresh activation/photon calculation, reader compatibility, and volume-integrated source conservation. The direct control checks that the translated material, flux, schedule, and solver options produce the same result as a directly specified ACTINV case. It is interoperability evidence, not independent physics validation or a transport dose validation.

ACTINV's R2S interchange stores per-cell photon totals and centroids, not per-nuclide spectra or photon bin edges. The ALARA exporter emits only `TOTAL` and labels the source groups using the sorted union of photon centroids. A centroid is not a bin boundary; these labels do not establish transport-ready energy intervals. A transport calculation needs an independently supplied and reviewed photon boundary grid and an explicit source-to-bin mapping. The example is a one-zone prescribed-flux case and does not model transport or a full geometry/material map.

The exporter requires an uncertainty report to carry photon output through `actinv export-r2s`. The fixture supplies zero relative statistical flux errors and requests only the flux-only channel; the resulting zero band excludes nuclear-data, decay, material, model, and transport uncertainty. It is not a complete uncertainty statement.

The retained output-only `demo.py` defaults to `results/p52_r2s_source.ndjson` and shutdown at 300 s. It reads with `nucleide.r2s.photon_group_sums`, `nucleide.alara.alara_photon_total_strength`, and `nucleide.r2s.tag_zone_strength`. Its 8-cell corpus has 220.303 photons/s and 115 centroid labels. Those cells have unit volume, so that fixture alone does not test non-unit-volume scaling; the demo now multiplies per-volume strength by each cell's volume when preparing voxel strengths.

## Verification and maintainer review

Run the focused boundary tests, the actual evaluated-data example, and the retained compatibility check:

```sh
bounded env ACTINV_BIN="$ACTINV" ACTINV_DATA_DIR="$PWD/target/nucleide-r2s-data" \
  target/nucleide-r2s-venv/bin/python -m unittest \
  contrib/nucleide_r2s/test_e2e.py contrib/nucleide_r2s/test_nucleide_r2s.py -v
```

Without Nucleide or the binary/data environment variables, dependent checks explicitly skip; a skip is not verification. Explicitly configured missing binary/data paths fail. The subprocess tests exercise timeout termination/reaping and a child-exit/timeout race. All CLI children are leaf processes with bounded waits.

On 2026-10-06, the source build above with Python 3.13.14, Nucleide 0.16.0 and NumPy 2.5.3 produced **463393807.7641661 photons/s**, at 3600 s after shutdown. Nucleide read 18 nonzero photon groups and reconstructed 463393807.76416606 photons/s from density times the 2 cm³ volume: relative error **1.2863e-16**. The direct ACTINV case matched the full 24-group photon vector and Mn56 activity (**20622094.12329952 Bq/g**) exactly. These numbers document the integration run, not a measurement benchmark.

All 22 scoped tests passed with no skips after the review on 2026-10-06. They cover input translation, rejected zone/material/flux mappings, missing or nonfinite result fields, photon-grid alignment and empty-bin conventions, the expected final time, the full calculation, and the retained output-only checks. The standalone example was rerun with fresh retained outputs and the same numerical result. The tested source binary's SHA-256 was `d2c0fb6dd931b3a9e052abdf30f265ead408bc6a386554b8bd0a074b1a6ca4dd` (build-dependent).

For Nucleide maintainer review: confirm the external material dictionary linked to `mix1`, whether the Python facade could expose the zone-to-mixture mapping, the zero-based flux `skip` interpretation and deck scalar, and cooling times interpreted as offsets from shutdown (converted to incremental ACTINV steps). The neutron boundary/units sidecar is necessary because the current flux reader carries neither. Photon-centroid export remains a separate boundary to review before transport use. No ACTINV core or Nucleide contract changes are required.
