# Four reproducible TENDL-2025 neutron threshold inconsistencies

Prepared 2026-09-14 by Connor Avila / Avila Labs, from ACTINV P25 follow-up.
Status: reproduced in locally archived source files; awaiting evaluator review.

## Observation

Four neutron evaluations contain a positive MF=10/MT=16 ground-state product
cross section at the same explicitly tabulated energy where MF=3/MT=16 is zero.
Each point is the first energy of both relevant tables, with negative reaction
Q values. No group collapse, interpolation, floating-point solver, or ACTINV
parser is needed to reproduce these discrepancies.

| File | MAT | Product ZAP / LFS | Exact energy (eV) | MF=3 total (b) | MF=10 product (b) |
|---|---:|---|---:|---:|---:|
| n-Fe053m.tendl | 2623 | 26052 / 0 | 7791974 | 0 | 107582.0 |
| n-Cl035.tendl | 1725 | 17034 / 0 | 13009500 | 0 | 24.59622 |
| n-Zr088.tendl | 4019 | 40087 / 0 | 12494780 | 0 | 70.11846 |
| n-Y088.tendl | 3922 | 39087 / 0 | 9459262 | 0 | 26.05538 |

The [ENDF-6 manual, section 10.3](https://www.oecd-nea.org/dbdata/data/endf102.htm)
requires zero cross section at the threshold of negative-Q MF=10 subsections
and requires MF=10 cross sections not to exceed the corresponding MF=3 cross
sections. These examples appear inconsistent with both requirements.

## Source identification

Library: TENDL-2025, neutron s30 ENDF archive, as identified by ACTINV's acquisition
script and per-file extraction manifest. The archive provenance is:

- Landing page: https://tendl.imperial.ac.uk/tendl_2025/tendl2025.html
- Archive: https://tendl.imperial.ac.uk/tendl_2025/tar_files/TENDL-n.tgz
- Recorded archive size: 3517450425 bytes.
- Recorded archive SHA-256: `e547527688506cbe09813364dcefa2aed11f474139bfa129d7cd4ca24fae21fa`.
- Members: the four filenames above, extracted as ordinary files.

Per-file SHA-256 values, independently recomputed and matched to the local
acquisition manifest during review:

```text
7d3a58320465837055b854a5a61860584f9b0bc8bd2f8d264a659a1ef2aa095b  n-Fe053m.tendl
c0ac6b92bad9851493b10a42bd81700eb3a7bbe8915b2627d2ce6454d3d08adf  n-Cl035.tendl
148382919d34670d07f3ae3c121b06e05f8d1c8b763c6fba30a9401edfeb40a3  n-Zr088.tendl
f6c69655d0991faddb90e8b1a620b9ccd4d0e68a56ce73f3ae52955295cfaedc  n-Y088.tendl
```

The archive was not redownloaded or rehashed during this review. The checks
establish the discrepancies in these exact local source versions; they do not
establish whether the live distribution has since changed. These four files
come from the original neutron extraction, not ACTINV's modified Pb-208 file.

## Exact source records

Physical file line numbers below are 1-based. Each unmodified 80-column record
contains three energy/value pairs; the first pair is the reported discrepancy.
Columns 67–80 identify MAT, MF, MT, and section sequence number.

Fe-53m: MF=3 line 1220; MF=10 line 59859:

```text
 7.791974+6 0.000000+0 7.800000+6 2.24586-11 8.000000+6 7.281567-92623 3 16    4
 7.791974+6 1.075820+5 7.800000+6 2.24586-11 8.000000+6 7.281567-9262310 16    4
```

Cl-35: MF=3 line 5807; MF=10 line 52273:

```text
 1.300950+7 0.000000+0 1.350000+7 7.012318-4 1.400000+7 3.986911-31725 3 16    4
 1.300950+7 2.459622+1 1.350000+7 1.381986-4 1.400000+7 7.971717-4172510 16    4
```

Zr-88: MF=3 line 1416; MF=10 line 48600:

```text
 1.249478+7 0.000000+0 1.250000+7 9.341547-6 1.300000+7 5.731728-24019 3 16    4
 1.249478+7 7.011846+1 1.250000+7 9.341547-6 1.300000+7 5.672746-2401910 16    4
```

Y-88: MF=3 line 1334; MF=10 line 51527:

```text
 9.459262+6 0.000000+0 9.500000+6 1.298063-4 1.000000+7 2.066088-23922 3 16    4
 9.459262+6 2.605538+1 9.500000+6 1.298063-4 1.000000+7 1.430201-2392210 16    4
```

## Reproduction

Use [reproduce_tendl2025_thresholds.py](../controls/reproduce_tendl2025_thresholds.py)
with Python 3 and a directory containing the four original files:

```sh
python3 reproduce_tendl2025_thresholds.py /path/to/extracted/neutron/files
```

The script uses only Python's standard library, performs no downloads or writes,
and imports no ACTINV modules. It checks full-file hashes, parses the MF=3/MF=10
TAB1 sections, selects the stated ZAP/LFS, checks negative Q and matching first
energies, and compares explicit decimal ordinates. It prints hashes, raw records,
physical line numbers, and `all_reproduced: true`. Exit 0 means all four pinned
discrepancies were reproduced; a mismatch or missing input produces exit 1.
An updated or differently formatted evaluation intentionally fails the hash check.

Local validation on 2026-09-14 reproduced all four cases, exit 0, under an enforced
6 GiB memory / 2-CPU systemd scope. The raw records can also be checked manually.

## Scope and requested adjudication

Please confirm whether these are known source-generation or formatting problems,
whether corrected evaluations exist, and which versions/users are affected. If
confirmed, please record the issues and correction status on the
[TENDL known-deficiencies page](https://tendl.imperial.ac.uk/tendl_2025/deficiencies.html)
or identify the preferred public tracking route.

No physical correction is proposed here, and no downstream inventory, dose, or
decay-heat error is quantified. A bad threshold ordinate can affect interpolation
over an adjacent interval; the impact depends on spectrum, target population,
and processing. Fe-53m is a metastable target, not ordinary bulk iron.

The broader [P25 report](P25_TENDL2025_DEFECT_REPORT.md) is supplementary context.
Its historical classifications, interpolation findings, floor handling, fission
comparator questions, and ACTINV MT=4 processing correction are not additional
confirmed defects in this four-case submission. ACTINV's P25 qualification failed;
this report does not establish overall solver superiority or library invalidity.
