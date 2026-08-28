# ACTINV data v1.0.0

This is ACTINV's first immutable, ready-to-use nuclear-data release. It is separate from the software release: install
an ACTINV v1.0.0 standalone command, then let its embedded catalog select and verify only the files you need.

```bash
actinv data list
actinv data fetch
actinv data verify
```

The default `tendl-2025-neutron` bundle downloads about 139 MiB and installs the exact P10 TENDL-2025 709-group
neutron activation library plus primary ENDF/B-VIII.0 and fallback JEFF-3.3 decay data. The decay archives come
directly from the IAEA and are not assets on this release. Optional bundles provide the matching complete P11 MF=33
neutron covariance sidecar and the P10 proton, deuteron, or alpha 162-group activation libraries.

Every direct asset, official archive, and extracted decay payload has a frozen byte count and SHA-256 in
`actinv-data-catalog-v1.0.0.json`. Downloads are streamed, bounded, verified, and published atomically. `SHA256SUMS`
and `SIZES` cover every attached payload and the catalog. Files are installed under a versioned directory and are
never silently updated.

The processed TENDL-2025 assets are distributed under the CC-BY-4.0 declaration in their source evaluations. See
`ACTINV-DATA-NOTICE-v1.0.0.md` for creators, citation, source identities, transformations, the two recorded Pb-208
repairs, and the separation between data terms and ACTINV's MIT/Apache-2.0 software licence.

ACTINV and these data remain research-grade. A verified download proves file identity, not fitness for a particular
material, spectrum, safety case, or regulatory use. See the repository's qualification boundary and validation record
before relying on results in a controlled analysis chain.
