# Data sources, terms, hashes

ACTINV never bundles nuclear data. Each run fetches (or points at) public files and records SHA-256 hashes in its
certificate. Terms of use are those of the hosts; check them before redistribution.

| data | host | notes |
|---|---|---|
| EAF-2010 neutron activation (816 targets) | IAEA-NDS `public/download-endf/EAF-2010/n/` | pointwise-complete MF=3, MF=8/9/10 isomer branching |
| TENDL-2017…2025 | IAEA-NDS `public/download-endf/TENDL-20xx/n/` | resolved resonances need reconstruction (own code) |
| FENDL-3.2c (ENDF-6, ACE, GENDF, NJOY inputs) | IAEA-NDS `fendl/` | transport library; used as the NJOY reference for our reconstruction |
| ENDF/B-VIII.0 decay sublibrary | IAEA-NDS mirror bulk zip | primary decay data (3,821 materials) |
| JEFF-3.3 radioactive decay data | IAEA-NDS mirror bulk zip | fallback (3,852 materials) |
| FNS decay-heat benchmark set | IAEA CoNDERC `conderc/fusion/files/fns.zip` | 73 materials, 132 experiments; spectra, measurements, FISPACT-II reference runs |
| natural abundances / atomic masses | copied from `openmc.data` (MIT) with its citations | independent re-verification pending |
| FISPACT 709-group boundaries | `pypact` (Apache-2.0) | |
| decay-photon format | [ENDF-6 Formats Manual](https://www.nndc.bnl.gov/endf-b8.0/endf-manual-viii.0.pdf) | MF=8/MT=457 radiation-spectrum records |
| dry-air photon response | [NIST table 4](https://physics.nist.gov/PhysRefData/XrayMassCoef/ComTab/air.html) | mass energy-absorption coefficient, 1 keV–20 MeV |
| elemental photon response | [NIST table 3](https://physics.nist.gov/PhysRefData/XrayMassCoef/tab3.html) | elemental mass attenuation coefficients, H–U |
| contact-dose method | [FISPACT-II User Manual](https://fispact.ukaea.uk/manual/user_manual.pdf) | semi-infinite-slab screening expression; not transport |
| OpenMC statepoint format | [OpenMC statepoint specification](https://docs.openmc.org/en/stable/io_formats/statepoint.html) | current format 18.2; P8 accepts major 18's bounded mesh-flux subset |
| MCNP tally formats | [LANL MCNP Theory & User Manuals](https://mcnp.lanl.gov/manual.html) | P8 is implemented against the public MCNP 6.3.2 manual's traditional MESHTAL/MCTAL definitions |
| FISPACT spectrum conversion/file format | [FISPACT-II User Manual](https://fispact.ukaea.uk/manual/user_manual.pdf) and [GRPCONVERT reference](https://fispact.ukaea.uk/wiki/Keyword%3AGRPCONVERT) | standard `fluxes` records; `CNVTYPE=0` equal-flux-per-unit-lethargy conversion |

Local layout used by the controls: `~/nuclear-data/{eaf-2010, tendl-eaf-test, fendl-3.2c, endfb-viii.0-decay,
jeff-3.3-decay, conderc-fns}` with `MANIFEST*.sha256` files.

## Photon-response input

Photon interaction coefficients are deliberately separate from nuclear data and are not bundled. Build a deterministic
`actinv-photon-response-1` file directly from the official NIST HTML tables:

```bash
# Smallest P7 validation input: dry air plus iron
python3 scripts/build_photon_response.py \
  /data/photon-response/nist-xcom-air-fe.json --elements Fe --cache-dir /data/nist-html

# General elemental response (all NIST tables H through U)
python3 scripts/build_photon_response.py \
  /data/photon-response/nist-xcom-all.json --elements all --cache-dir /data/nist-html
```

The builder records each source URL and downloaded-page SHA-256, preserves duplicate absorption edges, converts MeV to
eV and excludes timestamps/machine paths so identical pages produce identical bytes. Use `--offline` to rebuild from
the raw-page cache. Declare the generated file's printed SHA-256 in `photon.response`; the core recomputes it and fails
closed on a mismatch.

The P7 gate file (dry air + Fe) has SHA-256
`4f00824ac66ef941cddbe20b93966523b7f0ff2271b35cdf8be538c48e404307`. It remains outside the repository under
`~/nuclear-data/photon-response/`, in accordance with the no-bundled-data rule.

## P8 transport-file provenance

Transport outputs remain external user data. `actinv import-flux` computes their SHA-256 while reading, verifies that
the file did not change, and embeds the source path, format selector and normalization in `actinv-flux-1`. FISPACT
group-boundary JSON is an independently hashed auxiliary input. A mesh run requires and recomputes the completed
canonical file's hash; it embeds both that hash and the canonical header's upstream hashes, so the original transport
file may be archived or removed after canonicalization without weakening the mesh certificate.

OpenMC HDF5 is read by exact-pinned `hdf5-pure` 0.39.0 (MIT) with bounded metadata/chunk caches and row windows; P8's
independent controls create and inspect the same fixtures with pinned `h5py` 3.16.0 (BSD-3-Clause). MCNP and FISPACT
fixtures use separate small Python readers. Neither OpenMC, MCNP nor FISPACT-II executables are dependencies of the
import path, and none of their output files are committed to this repository.
