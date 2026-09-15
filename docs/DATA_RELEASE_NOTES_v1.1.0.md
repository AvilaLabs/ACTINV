# ACTINV data v1.1.0

This release adds `tendl-2025-patched`, an Avila Labs remediation derivative of the TENDL-2025 neutron sublibrary,
as a separately labeled data release. It is not an official TENDL release. It exists because ACTINV's P25 audit
confirmed that the official TENDL-2025 neutron sublibrary leaks the thermal (n,p) cross section into the first
emitted-state ordinate of certain MF=10 product records; the defect was reported upstream and acknowledged by the
TENDL authors, with a corrected library expected in a future TENDL release.

```bash
actinv data list
actinv data fetch
actinv data verify
```

The new default `tendl-2025-patched-neutron` bundle installs the P25c-qualified patched 709-group neutron
activation library plus primary ENDF/B-VIII.0 and fallback JEFF-3.3 decay data. The patch zeroes exactly the 44
enumerated leaked leading ordinates across the 28 confirmed-signature files and is byte-identical to the official
archive everywhere else. The shipped artifact additionally carries the two documented P10 Pb-208 nonfinite repairs
(the sealed corpus retains the official `NaN` fields), so its built source equals the P10 working corpus plus the
P25c patch — see `ACTINV-DATA-NOTICE-v1.1.0.md` and `docs/DATA_LIMITATIONS.md`. The
`tendl-2025-patched-neutron-covariance` bundle adds the MF=33 covariance sidecar
rebuilt against the patched artifact. The proton, deuteron, alpha and decay assets are unchanged from data-v1.0.0
and keep their existing artifact IDs, so `catalog:<id>` references in existing problem files resolve to exactly the
same bytes.

The legacy `tendl-2025-neutron` bundles remain available for byte-compatible reproduction of earlier work, but are
superseded: the unpatched corpus carries the upstream-confirmed defect. New work should use the patched bundles.

**Qualification boundary.** The patched corpus passed the frozen P25c construction, coverage, surgical-integrity,
leak-clearance and retrospective nonregression gates. That is retrospective evidence of internal consistency, not
blind validation. The shipped artifact holds **1,679 targets / 87,075 rows**: the full-corpus strict build
ledgered **1,172 source-file exclusions** (one superseded by the carried Pb-208 repair) across all defect classes —
far beyond the flagged subsets the earlier censuses covered. Five dosimetry-critical targets (Ni-58, Nb-93, Ag-109,
In-113, Au-197) still fail closed under non-signature defect classes this patch does not repair, and 16 of the 17
previously defect-bearing IRDFF-II targets are absent. Read `docs/DATA_LIMITATIONS.md` before relying on this data.

Every direct asset, official archive, and extracted decay payload has a frozen byte count and SHA-256 in
`actinv-data-catalog-v1.1.0.json`. Downloads are streamed, bounded, verified, and published atomically. `SHA256SUMS`
and `SIZES` cover every attached payload and the catalog. Files are installed under a versioned directory and are
never silently updated.

The processed TENDL-2025 assets and the `tendl-2025-patched` derivative are distributed under the CC-BY-4.0
declaration in their source evaluations. See `ACTINV-DATA-NOTICE-v1.1.0.md` for creators, citation, source
identities, transformations, the Pb-208 repairs, the patch scope, and the separation between data terms and
ACTINV's MIT/Apache-2.0 software licence.

ACTINV and these data remain research-grade. A verified download proves file identity, not fitness for a particular
material, spectrum, safety case, or regulatory use. See the repository's qualification boundary and validation
record before relying on results in a controlled analysis chain.
