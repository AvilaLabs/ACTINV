# Choose and install nuclear data

ACTINV software and evaluated nuclear data have separate versions. The embedded catalog identifies fixed release artifacts; installing software does not silently replace your calculation's data.

## Standard neutron setup

```bash
actinv data fetch
actinv data verify
actinv data list
```

The default bundle is `tendl-2025-neutron`, the full 2,850-target neutron library. Catalog 1.2.0 installs it under `actinv-data/v1.2.0/` with its index, ENDF/B-VIII.0 primary decay data, JEFF-3.3 fallback decay data, and the neutron damage table (see below).

Downloads are checked by size and SHA-256 before installation. Correct existing files are reused, and a file already present under an older version directory (`actinv-data/v1.1.0/`) with the same SHA-256 is copied and re-verified instead of downloaded again. An incorrect existing file is left in place unless you explicitly request replacement with `--force`.

## Damage table

Every neutron bundle also installs `tendl-2025-neutron-damage`, a table of damage energy and displacement cross sections on the 709-group structure. Naming it in a problem gives dpa, exact NRT dpa, arc-dpa and He/dpa with no table to build and no displacement energies to list: `actinv new` writes the `damage` section for you, and `actinv import-fispact` uses it for decks that give `ALLDISPEN` or `ATDISPEN`. The table covers every TENDL-2025 neutron target that the catalog's decay data lists as stable or with a half-life of at least one year; shorter-lived products are counted in the run's `covered_atom_fraction` and `uncovered_targets`, not given damage. It is built from the unmodified TENDL-2025 evaluations, with the standard displacement energies of NJOY2016 HEATR. Build your own with [`build-damage`](cli.md#build-data) for other energies or targets.

## Choose a bundle

| Calculation | Activation bundle | Matching covariance bundle |
| --- | --- | --- |
| Full neutron corpus | `tendl-2025-neutron` | `tendl-2025-neutron-covariance` |
| Derived patched neutron subset | `tendl-2025-patched-neutron` | `tendl-2025-patched-neutron-covariance` |
| Proton | `tendl-2025-proton` | None in the shipped catalog |
| Deuteron | `tendl-2025-deuteron` | None in the shipped catalog |
| Alpha | `tendl-2025-alpha` | None in the shipped catalog |

For example:

```bash
actinv data fetch tendl-2025-neutron-covariance
actinv data verify tendl-2025-neutron-covariance
```

A covariance sidecar must match the exact activation library and index. Both full and patched neutron corpora have matching sidecars; mixing them is rejected. Add the corresponding `uncertainty` section to your problem to request propagation. Downloading a sidecar alone does not enable uncertainty.

The patched subset corrects a bounded upstream defect and excludes many targets. It is not a drop-in accuracy upgrade for every material or spectrum. Read [Known data limitations](data-limits.md) before choosing between the neutron bundles.

## Use another location

```bash
actinv data fetch --output /data/actinv
actinv data verify --output /data/actinv
actinv new problem.json --data-dir /data/actinv
```

`--data-dir` writes absolute paths to the generated problem. To keep symbolic catalog references, set `ACTINV_DATA_DIR` to your installation root when using the default `actinv new` output. It names the root above `v1.2.0`, not the version folder itself.

`actinv data fetch` and `verify` use `--output` for their destination; pass it explicitly when installing or checking a custom location.

## Offline installation

Run `actinv data manifest` to print the embedded catalog. Obtain the listed archives and extracted artifacts through your usual transfer process, retain their names and versioned paths, then run `actinv data verify` on the destination machine. See the [source and extraction record](https://github.com/AvilaLabs/ACTINV/blob/master/docs/DATA.md) for provider details.

## Prepared calculation cache

The CLI creates a verified prepared cache when it first uses a library and spectrum. Later runs reuse those compact files. The public iron example creates about 282 MiB of prepared files; other libraries and spectra differ.

By default, ACTINV uses the platform's cache location. Set `ACTINV_CACHE_DIR` to an absolute path to choose another disk. This cache is disposable; removing it causes data preparation to run again. Keep the original nuclear-data inputs, problem, and result for the calculation record.

A corrupt or incompatible final cache artifact produces an error instead of being silently trusted. [Troubleshooting](troubleshooting.md) explains how to regenerate it. Desktop workers use a private cache per calculation.

## Terms and custom data

The installed `ACTINV-DATA-NOTICE.md` records data attribution and transformations. ACTINV's MIT/Apache-2.0 software licence does not replace a dataset's licence or source terms.

Advanced users can build activation, covariance, shielding, and damage tables from evaluated files. See [Advanced workflows](workflows.md#build-libraries-and-response-tables) and the [specification reference](specification.md).
