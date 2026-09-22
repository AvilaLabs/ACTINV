# Supplement: TENDL-2025 threshold consistency and rate sensitivity

## Files

- `reproduce_tendl2025_thresholds.py`: existing ACTINV standalone reproducer,
  copied without alteration. Reads explicit decimal records and verifies hashes.
- `compare.py`: primary analytic folds and exactly one changed source field.
- `check.py`: independent parser, adaptive numerical quadrature, exact-decimal
  removed-area checks, point controls, analytic fixtures and corrupted-hash check.
- `comparison.json`: all original/corrected folds, rates, direct differences,
  source/variant identities and the exact changed records.
- `check.json`: independent verification and result-file digest.
- `SOURCE_IDENTITIES.md`: human-readable source/variant identities.
- `environment.json`: recorded scientific environment.

The zip also includes the frozen `PROTOCOL.md`, execution log, this README,
and the source identity files. It contains no bulk third-party nuclear data.

## Inputs

Obtain the exact original files `n-Fe053m.tendl`, `n-Cl035.tendl`,
`n-Zr088.tendl`, and `n-Y088.tendl` from the archived TENDL-2025 neutron
distribution. The original archive URL is:

https://tendl.imperial.ac.uk/tendl_2025/tar_files/TENDL-n.tgz

Original archive acquisition SHA-256:
`e547527688506cbe09813364dcefa2aed11f474139bfa129d7cd4ca24fae21fa`.
The present study rehashed the selected files, not the archive. A changed live
download may no longer match. There is no automatic fallback to another version.

The TENDL source headers declare CC-BY-4.0 and name A.J. Koning and D. Rochman;
the library reference is Koning et al., Nuclear Data Sheets 155 (2019) 1–55,
https://doi.org/10.1016/j.nds.2019.01.002. Exact source snippets retain the
original records; variant records are explicitly marked as modifications.
The copied ACTINV reproducer originates from https://github.com/AvilaLabs/ACTINV,
whose software is available under MIT OR Apache-2.0.

## Commands

Use GNU/Linux with a user systemd session. The primary and checker verify
memory, swap, task and CPU limits before calculation, following the ACTINV
workstation requirements. Run one command at a time. From the extracted package
root (the directory containing `PROTOCOL.md` and `supplement/`):

```sh
mkdir -p work
TCS_INPUT=/absolute/path/to/original/neutron/files
systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 -p TasksMax=128 -p CPUQuota=200% -- env PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/work" /usr/bin/python3 supplement/reproduce_tendl2025_thresholds.py "$TCS_INPUT"
systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 -p TasksMax=128 -p CPUQuota=200% -- env PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/work" /usr/bin/python3 supplement/compare.py "$TCS_INPUT"
systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 -p TasksMax=128 -p CPUQuota=200% -- env PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 TMPDIR="$PWD/work" /usr/bin/python3 supplement/check.py "$TCS_INPUT"
```

The primary and checker overwrite their generated JSON files when rerun; preserve
the supplied evidence if performing a changed-method study. A failed assertion
means the corresponding check has not passed. Neither script writes source files.

## Interpretation

The one-field variant is a sensitivity intervention, not an official evaluation.
The Gaussian (14 MeV mean, 0.5 MeV standard deviation) and uniform spectra are
normalized on 0–20 MeV and are synthetic. Rates are per target nucleus at an
integrated flux of 1e14 cm^-2 s^-1. No actual material or decay network is solved.
Relative excess uses the corrected value as denominator, not the original value.
Tiny direct Gaussian differences are included for transparency but lie below
the independent absolute check tolerance; do not infer validated relative
precision or a resolved subtraction from them.
