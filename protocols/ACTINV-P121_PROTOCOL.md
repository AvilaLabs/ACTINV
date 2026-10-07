# P121 — Contact-dose proxy with sub-keV photon power omitted and reported

Registered 2026-10-07, before any change to the contact-dose proxy and after P120 (whose CI repair lands first), after the owner approved this fix ("go ahead",
2026-10-06). Base commit: `9af5486` (origin/master). Parking entry: `docs/PARKING.md`, 2026-10-06, "Contact-dose
proxy refused for light-element materials".

## Defect

`photon::source_for_step` returns `contact_gamma_air_dose_proxy_Gy_h: null` for the whole step whenever any photon
power is excluded from the dose response (`response_excluded_power_W_g > 0`). A per-nuclide photon group is excluded
when its centroid energy lies outside the response tables, which start at 1 keV (NIST XCOM / Hubbell–Seltzer). The
activation products of light-element materials emit K X-rays below 1 keV (F-18, N-16, B-12, C-11, O-15, Na-22 and
others; lines at 183–849 eV), and in the FISPACT 24-group structure those lines are the only content of the nuclide's
0–10 keV group, so its centroid falls below 1 keV. Measured on activated FLiBe (FARIS maintenance coupling
validation, variant V1): `dose_response_power_coverage` 0.99999936, proxy null at every step. About 6 parts in 10
million of the photon power blocks the proxy for any material with light activation products.

## Change under test

One rule in `photon::source_for_step`. Nothing else in the photon source, groups, lines, gamma constants, heat or
inventory changes.

1. Let `E_lo` be the largest first tabulated energy among the air energy-absorption curve and the attenuation curves
   of every element in the material (1 keV for the shipped NIST response). A per-nuclide group with photon power whose
   centroid energy is below `E_lo` is **sub-threshold**: it contributes nothing to the contact proxy, and its power is
   added to a new per-step quantity `dose_response_subthreshold_power_W_g` instead of to
   `response_excluded_power_W_g`.
2. **Tolerance.** The step's total proxy is reported only if the sub-threshold power is at most `1e-4` of the
   response's total photon power for that step (`SUBTHRESHOLD_POWER_TOLERANCE`, a fixed constant, not a spec option).
   A nuclide's own proxy follows the same rule with that nuclide's powers. Reason for 1e-4: below 1 keV the ratio
   of air energy absorption to material attenuation is not tabulated; in low-Z materials it is of order one near
   1 keV (FLiBe at 1 keV: 3599 / ≈4500 cm²/g) and can move by up to about an order of magnitude across the K edges
   below it, so a power share of 1e-4 leaves the proxy low by at most about 1e-3. The proxy remains a screening
   quantity; this rule does not add precision it never had.
3. **Still refused, unchanged:** group centroids above the response's highest energy; elements missing from the
   response (`response_missing_elements`); power outside the photon group structure (under/overflow). Any of these
   makes the proxy null exactly as today, whatever the sub-threshold share.
4. The total proxy is the sum of every nuclide's covered contribution when no refusal applies (a nuclide whose own
   share exceeds the tolerance still adds its covered part to the total, which is governed by the total share).
5. **Reporting.** `PhotonSourceOut.dose_response_subthreshold_power_W_g` and
   `PhotonDiagnostics.response_subthreshold_power_W_g` (in the ledger's `photon_spectra`) are serialized only when
   non-zero, so results for materials without sub-threshold groups are byte-identical to today. When present, the
   value is the omitted power; `dose_response_power_coverage` keeps its definition (included / total) and so still
   shows the omitted share.
6. Documentation: the handbook's specification and results pages and `docs/METHOD.md` state the rule, the
   tolerance and its reason, and `docs/LEDGER.md` the new field. The parking entry is marked resolved by P121.

## Gates

Reference binary: release `actinv` built from `9af5486` (base), SHA-256 recorded by the checker. Candidate: release
build of the implementation commit. Checker: `controls/check_p121.py`, run by the coordinator under the 6 GiB
cgroup, serially. Inputs (outside the repository, pinned here by SHA-256; FARIS validation V1 specs, NIST all-element
response `9efa4048…acd8`, TENDL-2025 709-group library):

| Role | Spec | SHA-256 |
| --- | --- | --- |
| G2 | TiH2 shield, first shutdown | `18c51ce8cd809fd7f668fd42f649ec6f966f8e7963b4616d01457340657e86bd` |
| G2 | Fe vessel, first shutdown | `44c7d9c608268874b4162f5cb4e70788b3685d333ee637039684b07de2de38a0` |
| G2 | W first wall, first shutdown | `cbaa2de997c81cffa86bc6810dcff6a00670431a26ddc380c79d4dc4d9976871` |
| G3 | FLiBe blanket, installation 1, first shutdown | `ab5a864eb12c33ada52fc315bd79f6f70af877543844ec82e3271f85b012c958` |
| G3 | FLiBe blanket, installation 9 | `cb7b2956574288e7e012a8a8dcaf26202120ba920ab79768520b54149e28af09` |

- **G0.** This protocol's SHA-256 is in `protocols/protocol_hash.txt` in a commit before any change to
  `photon.rs`.
- **G1.** `cargo fmt --all -- --check`; `cargo clippy --workspace --all-targets --all-features -- -D warnings`;
  `cargo test --workspace --all-targets --all-features`. New unit tests in `photon.rs` on synthetic nuclides and a
  synthetic response cover:
  (a) a sub-threshold group within tolerance: proxy present and equal to the hand-computed sum over covered groups,
  sub-threshold field equal to that group's power, coverage below one;
  (b) a sub-threshold share above tolerance: total and nuclide proxy null, field reported;
  (c) sub-threshold plus a centroid above the top energy: null;
  (d) sub-threshold plus a missing element: null;
  (e) sub-threshold plus group under/overflow: null;
  (f) no sub-threshold group: the serialized step has no sub-threshold key and every value equals the pre-change
  computation.
- **G2, unchanged where nothing is sub-threshold.** For each G2 spec, the reference result has a non-null total
  proxy at every step (precondition; a spec that fails it is reported and moves to G3's rules). Candidate and
  reference results are byte-identical apart from the top-level `ms`.
- **G3, the light-element case.** For each G3 spec, with the candidate result `C` and reference result `R`:
  (a) every step where `R`'s proxy is null and `R`'s only exclusions are sub-threshold groups has a non-null proxy
  in `C` when its share is at most 1e-4, and null otherwise; the checker reports both counts;
  (b) the sub-threshold power equals the checker's own sum of `C`'s per-nuclide group powers with centroid below
  1 keV, within 1e-12 relative;
  (c) independent recomputation: the checker computes the proxy from `C`'s per-nuclide groups, the response file and
  the spec's composition (mass fractions from the composition with AME2020 atomic masses), as
  `(B/2)·(μ_en,air/μ_mat)·P·1000·3600` summed over groups with centroid in [1 keV, 20 MeV], with log-log
  interpolation; it agrees with `C`'s total proxy within 1e-6 relative (atomic-mass tables may differ at that level);
  (d) apart from the proxy fields, the new sub-threshold fields, `response_excluded_power_W_g` (which must drop by
  exactly the sub-threshold power) and `ms`, `C` and `R` are byte-identical in value.
- **G4.** CI: all implementation workflows green on the pushed commit, including the existing photon/dose controls
  and the committed run-output baselines unchanged.

A gate that fails is recorded as P121-FAIL with its evidence; repairs need an amendment registered before they are
made.

## Not claimed

The proxy is not made more accurate. Sub-keV photons are omitted, not modelled; a response extended below 1 keV
would be a separate phase. No change to transported dose, gamma constants or any heat quantity.
