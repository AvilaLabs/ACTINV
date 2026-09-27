# Orphan product sections and descriptors (TENDL-2023 only)

Found 2026-09-26 during decay-aware TENDL-2023 FNS library construction.
Status: superseded — spot-checked TENDL-2025 files carry no orphans.
Logged for the ledger; no upstream action needed.

## Observation

Approximately thirty TENDL-2023 evaluations contain product records whose
MT has no cross section: MF=6 (product energy-angle distributions), MF=8
(emitted-state descriptors), or MF=9 (emitted-state multiplicities) for
reactions that carry no MF=3 or MF=10. The sections can emit no rows and
are inert bookkeeping, but a fail-closed reader must either reject the
tape or silently drop declared products.

A related sub-case: `n_035-Br-79_3525.dat` carries an MF=8/LMF=10
descriptor declaring products {(35079,0), (35079,1)} for an MT with no
MF=10 emitted-state table — a declaration with no data.

## Affected files (FNS build input set)

| File | Orphan |
|---|---|
| n_038-Sr-86_3831.dat | MT106/MF6 |
| n_040-Zr-91_4028.dat | MT112/MF6 |
| n_051-Sb-128M_5147.dat | MT56/MF6 |
| n_066-Dy-156_6625.dat | MT116/MF6 |
| n_066-Dy-160_6637.dat | MT106/MF6 |
| n_068-Er-168_6843.dat | MT106/MF6 |
| n_074-W-186_7443.dat | MT107/MF6 |
| n_076-Os-186_7631.dat | MT106/MF6 |
| n_076-Os-192_7649.dat | MT104/MF6 |
| n_077-Ir-192M_7729.dat | MT18/MF8 |
| n_077-Ir-195M_7738.dat | MT51/MF6 |
| n_035-Br-79_3525.dat | MF8/LMF=10 descriptor, no MF10 |

(The full 31-file list includes isomer evaluations beyond the FNS targets;
see `~/nuclear-data/tendl-2023/fns_decay_build.log`.)

## Local handling

Under `--profile tendl`, ACTINV ledgers `orphan_sections:` /
`descriptor_orphan:` entries and continues — the records emit no rows
regardless, so tolerance does not change physics.

## Resolution in TENDL-2025

Spot-checks of `n-Sb128m`, `n-Br079`, `n-Sr086`, `n-W186` (TENDL-2025
`files/n/`) find no MF=6/8/9 sections without a matching MF=3/MF=10.
Regeneration cleared the class.
