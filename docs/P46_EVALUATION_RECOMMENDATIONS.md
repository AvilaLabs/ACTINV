# ACTINV evaluation recommendations (P46)

Generated 2026-09-24 08:10 UTC from `results/p46_eval_tables.json` (ledger sha `b0e43f3db83cc9e3…`, scorer sha `47320d447fb905c6…`).

Every recommendation is computed on the identical spec→solve→C/E pipeline against the 132-experiment CoNDERC FNS decay-heat suite with decay data held fixed — the comparison isolates the activation cross-section corpus. Measurements are consumed C/E evidence (read before the protocol freeze; the scorer and partition froze before these scores were computed).

## Corpus accounting

| corpus | provenance | targets-covered elements | executed | failed | uncovered experiments | labels |
|--------|-----------|--------------------------|----------|---------|-----------------------|--------|
| eaf-2010 | legacy_builder | 99 | 132 | 0 | 0 | no_state_catalog |
| fendl-3.2c | converted_subset | 9 | 132 | 0 | 95 | converted_subset |
| tendl-2017 | current_builder | 87 | 132 | 0 | 0 | legacy_evaluation |
| tendl-2025 | legacy_builder | 113 | 132 | 0 | 0 | full_evaluation |
| tendl-2025-patched | current_builder | 113 | 132 | 0 | 0 | covariance_subset |

## Per-family scores (median C/E, mean |ln C/E|, fraction within ±20%)

| corpus | family | n_scored | median C/E | mean |ln C/E| | within ±20% | within 2× |
|--------|--------|----------|------------|--------------|---------------|------------|
| eaf-2010 | alloy_composition | 1176 | 1.006 | 0.296 | 62% | 91% |
| eaf-2010 | pure_element | 1184 | 1.070 | 0.238 | 74% | 93% |
| fendl-3.2c | alloy_composition | 204 | 0.022 | 3.052 | 14% | 20% |
| fendl-3.2c | pure_element | 405 | 0.878 | 1.193 | 45% | 63% |
| tendl-2017 | alloy_composition | 1176 | 1.018 | 0.275 | 67% | 90% |
| tendl-2017 | pure_element | 1184 | 1.031 | 0.310 | 75% | 90% |
| tendl-2025-patched | alloy_composition | 1176 | 0.767 | 2.021 | 44% | 61% |
| tendl-2025-patched | pure_element | 1025 | 0.860 | 2.132 | 44% | 61% |
| tendl-2025 | alloy_composition | 1176 | 1.022 | 0.295 | 66% | 90% |
| tendl-2025 | pure_element | 1184 | 1.042 | 0.265 | 68% | 91% |

## Recommendations (per material, ≥3 scored points, ≥2 corpora)

| material | recommended | mean |ln C/E| | n_scored | median C/E |
|----------|------------|--------------|----------|------------|
| Ag | tendl-2017 | 0.040 | 20 | 0.993 |
| Al | tendl-2025-patched | 0.527 | 48 | 0.982 |
| As | tendl-2017 | 0.103 | 21 | 1.084 |
| Au | tendl-2025 | 0.264 | 21 | 0.783 |
| Ba | tendl-2025 | 0.396 | 48 | 0.995 |
| Bi | eaf-2010 | 0.871 | 43 | 1.545 |
| Br | tendl-2017 | 0.027 | 21 | 1.000 |
| Ca | tendl-2017 | 0.245 | 47 | 0.889 |
| Cd | eaf-2010 | 0.075 | 22 | 1.076 |
| Ce | eaf-2010 | 0.200 | 21 | 1.050 |
| Cl | tendl-2025-patched | 0.106 | 21 | 1.101 |
| Co | tendl-2025 | 0.065 | 36 | 1.049 |
| Co | tendl-2025-patched | 0.065 | 36 | 1.049 |
| Cr | tendl-2017 | 0.239 | 50 | 0.997 |
| Cs | tendl-2017 | 0.491 | 21 | 1.216 |
| Cu | eaf-2010 | 0.062 | 51 | 0.997 |
| Dy | tendl-2025-patched | 0.708 | 20 | 1.623 |
| Er | eaf-2010 | 0.517 | 21 | 1.083 |
| Eu | tendl-2025-patched | 0.445 | 20 | 0.666 |
| F | tendl-2017 | 0.051 | 41 | 1.007 |
| Fe | eaf-2010 | 0.063 | 51 | 1.009 |
| Ga | tendl-2017 | 0.034 | 22 | 1.030 |
| Gd | eaf-2010 | 0.327 | 21 | 1.213 |
| Ge | tendl-2017 | 0.057 | 22 | 1.025 |
| Hf | tendl-2025 | 0.257 | 20 | 0.821 |
| Hg | tendl-2025 | 0.029 | 21 | 1.012 |
| Ho | tendl-2017 | 0.112 | 21 | 1.018 |
| I | eaf-2010 | 0.593 | 21 | 1.717 |
| In | tendl-2025 | 0.596 | 21 | 1.979 |
| Inc600 | tendl-2025 | 0.060 | 51 | 0.996 |
| Ir | eaf-2010 | 0.094 | 19 | 1.071 |
| K | tendl-2017 | 0.199 | 49 | 1.086 |
| La | tendl-2017 | 0.907 | 21 | 1.338 |
| Lu | eaf-2010 | 0.622 | 21 | 1.355 |
| Mg | eaf-2010 | 0.047 | 19 | 1.002 |
| Mn | tendl-2017 | 0.083 | 52 | 1.085 |
| Mo | tendl-2017 | 0.076 | 49 | 0.963 |
| Na | eaf-2010 | 0.481 | 52 | 0.840 |
| Nb | eaf-2010 | 0.089 | 48 | 1.045 |
| Nd | tendl-2017 | 0.473 | 21 | 1.714 |
| Ni | tendl-2017 | 0.046 | 51 | 1.015 |
| NiCr | tendl-2017 | 0.042 | 51 | 0.997 |
| Os | tendl-2025-patched | 0.397 | 21 | 1.234 |
| P | tendl-2017 | 0.049 | 20 | 0.973 |
| Pb | tendl-2017 | 0.283 | 47 | 0.882 |
| Pd | tendl-2017 | 0.098 | 21 | 0.937 |
| Pr | eaf-2010 | 0.341 | 21 | 1.385 |
| Pt | tendl-2017 | 0.184 | 20 | 1.240 |
| Rb | tendl-2017 | 0.032 | 21 | 0.972 |
| Re | eaf-2010 | 0.121 | 50 | 1.066 |
| Rh | tendl-2025-patched | 0.796 | 19 | 2.310 |
| Ru | tendl-2017 | 0.032 | 21 | 1.019 |
| S | eaf-2010 | 0.199 | 48 | 1.006 |
| SS304 | tendl-2017 | 0.055 | 51 | 0.939 |
| SS316 | eaf-2010 | 0.051 | 51 | 0.968 |
| Sb | tendl-2025-patched | 0.057 | 21 | 1.055 |
| Sc | tendl-2025 | 0.081 | 21 | 0.955 |
| Se | tendl-2017 | 0.072 | 21 | 0.993 |
| Si | eaf-2010 | 0.057 | 21 | 1.055 |
| Sm | tendl-2025 | 0.095 | 20 | 1.016 |
| Sn | eaf-2010 | 0.196 | 51 | 1.027 |
| Sr | tendl-2025 | 0.069 | 51 | 0.984 |
| Ta | eaf-2010 | 0.207 | 51 | 0.905 |
| Tb | eaf-2010 | 1.772 | 21 | 0.177 |
| Te | tendl-2017 | 0.023 | 21 | 1.003 |
| Ti | tendl-2017 | 0.066 | 51 | 0.967 |
| Tl | eaf-2010 | 0.174 | 20 | 1.129 |
| Tm | tendl-2025 | 0.498 | 21 | 0.934 |
| V | fendl-3.2c | 0.178 | 48 | 0.958 |
| W | tendl-2017 | 0.406 | 50 | 1.436 |
| Y | tendl-2017 | 0.321 | 51 | 0.834 |
| Yb | tendl-2017 | 0.403 | 20 | 1.065 |
| Zn | tendl-2025 | 0.013 | 21 | 0.987 |
| Zn | tendl-2025-patched | 0.013 | 21 | 0.987 |
| Zr | tendl-2025 | 0.050 | 51 | 0.986 |

A recommendation is refused at construction unless it carries its corpus identity, scorer sha, ledger sha, n_scored and median C/E — the evidence fields above are the row it stands on.
