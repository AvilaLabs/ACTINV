# Data sources, terms, hashes

ACTINV never bundles nuclear data. Each run fetches (or points at) public files and records SHA-256 hashes in its
certificate. Terms of use are those of the hosts; check them before redistribution.

| data | host | notes |
|---|---|---|
| EAF-2010 neutron activation (816 targets) | IAEA-NDS `public/download-endf/EAF-2010/n/` | pointwise-complete MF=3, MF=8/9/10 isomer branching |
| TENDL-2017…2025 | IAEA-NDS `public/download-endf/TENDL-20xx/n/` | resolved resonances need reconstruction (own code) |
| FENDL-3.2c (ENDF-6, ACE, GENDF, NJOY inputs) | IAEA-NDS `fendl/` | transport library; used as the NJOY reference for our reconstruction |
| ENDF/B-VIII.0 decay sublibrary | IAEA-NDS mirror bulk zip | primary decay data (3,821 materials) |
| ENDF/B-VIII.0 neutron-induced fission-product yields | NNDC/IAEA ENDF bulk archive | MF=8/MT=454 independent and MT=459 cumulative tables; P9 production uses only MT=454 |
| JEFF-3.3 radioactive decay data | IAEA-NDS mirror bulk zip | fallback (3,852 materials) |
| FNS decay-heat benchmark set | IAEA CoNDERC `conderc/fusion/files/fns.zip` | 73 materials, 132 experiments; spectra, measurements, FISPACT-II reference runs |
| U-235 fission decay-heat set | IAEA CoNDERC fission archive | Dickens thermal pulse and Yarnell 20,000 s measurements, paired FISPACT-II inputs and reference reports |
| ALARA 2.9.2 | official [`svalinn/ALARA`](https://github.com/svalinn/ALARA) source | P9 identical-data Fe-56(n,p)Mn-56 pulse comparison; repository `LICENSE` is 3-clause BSD |
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
endfb-viii.0-nfpy, jeff-3.3-decay, conderc-fns, conderc-fission, alara-2.9.2}` with source hashes retained in the
phase results or `MANIFEST*.sha256` files.

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

## P9 fission and comparison inputs

P9 keeps evaluated data, experimental archives, reports and comparison-code builds outside the repository. The final
control records independently re-hash all physics inputs and every ACTINV certificate input.

| input | SHA-256 / identity | use |
|---|---|---|
| `ENDF-B-VIII.0_nfy.zip` | `92c5371fdb21eecf4989f48828671b904186abc6386b3d7510c8fcee2ee5ffcf` | original official NFPY archive |
| `nfy-092_U_235.endf` | `9e1320293a544fc03f33f804a15a9e3ccc3be026552ee6dbc03b8d3e24615e41` | U-235 MF=8/MT=454/459 gate evaluation |
| CoNDERC fission archive | `30756fef88c0f3637246bf8ad8ef1fc5397a3f784e5408f2861bc474993e74a5` | measurements, thermal flux and FISPACT-II histories |
| UKAEA-R(18)003 | `35495e39a3741e8d7d6e2097ba940070d42db1cf8adf6d18bfc488b91b82a2a1` | FISPACT-II U-235 decay-heat context and pulse-unit definition |
| Gauld 2019 summary, TAL-NAPC20190311-001 | `71f22abd8993f72656b00ae80bff02099bbb1bea8f8db4781e33b58f9d273f74` | ORIGEN context; its libraries differ from the ACTINV comparison |
| ENDF/B-VII.1 OpenMC U-235 HDF5 | `c2f071a2cf180c5f73bb4f054eb30a6e29b1fc963d69720e7560e32eee91b4eb` | thermal MT=18 scalar; it cancels in per-fission normalization |
| ALARA 2.9.2 source | commit `faa5b330460fe865e38fc788f1b792ea33d13d1b` | official build/sample and identical FENDL-2 subset run |

The archive is preserved byte-for-byte. Its `Yarnell_20000.csv` metadata says `Author=Akiyama` and
`Irradiation=Pulse`, although the filename, paired `U2352E4s.i` input and UKAEA report identify Yarnell and a 20,000 s
history. `Dickens_pulse.csv` labels its ordinate `MeV/f/s`, while UKAEA-R(18)003 defines the plotted pulse ordinate as
cooling time times power per fission. The control therefore retains the archive value and divides it and its
uncertainty by cooling time for the protocol's `MeV s^-1 fission^-1` C/E. These provenance anomalies are reported in
`results/g6_p9_conderc.json`; no source file is edited.

OpenMC 0.15.3 independently parses the U-235 yields and supplies CRAM48 for G1/G4. ALARA converts its own official
FENDL-2 sample text before G5; ACTINV consumes an independently generated library from that same Fe-56(n,p) card.
Neither code is a production dependency of ACTINV.

## P10 activation-library provenance

P10's complete production artifacts were built externally with Rust builder fingerprint
`7a50ba3441b30b829ae857ed192b2e52554d6c149460475f7735599f29548a43`. Archives, evaluations, caches and generated
NPZ libraries are not committed. `results/g7_p10_builds.json` re-hashes every archive/file manifest, cache entry and
final index; the compact identities are:

| corpus | archive/source identity | files | rows | output NPZ SHA-256 |
|---|---|---:|---:|---|
| TENDL-2025 neutron | archive `e547527688506cbe09813364dcefa2aed11f474139bfa129d7cd4ca24fae21fa`; deterministic working-file manifest `b1ea3fe043ec243e2df0a3894206872c2ce18c3b4541c19b35029b3ed3e7b15c` | 2,850 | 167,735 | `ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44` |
| TENDL-2025 proton | `49340a03b0d9ac86598c6b710c0bc2ec0babd3fa0717a9ff1d75f042fccc5b0b` | 2,850 | 528,057 | `0da7a35b37fd3b305ac2166ec092cdfb78123e76f8647d8808915e2c708d9790` |
| TENDL-2025 deuteron | `34f459aea0b5ac9c40820c88d898618f926ec3b52858a5393e42d57707ec5f1c` | 2,850 | 548,706 | `8050988981518cd63ac0c2ad76c6756370b154ea9f5a6d6435aa5f132b9d99ae` |
| TENDL-2025 alpha | `25520f6eb42ce024c065f85255277ed169b2f826e9fc24f5d093c99d5c60e018` | 2,850 | 489,279 | `ead1141bfe07ec1a02055af014f8db0a49effe2fd60c29d181a505f7c6d10915` |
| EAF-2010 neutron | source manifest `5cd73807a39dbc2793bcd87bf0fea23338178d38d80b5848bf6ce2e28d8e0e40`; flat-file manifest `87baeeef62650cdf8791bd3f198c906b1e6787eb7017a3ec4b02d4cee88bc15e` | 816 | 115,702 | `5de78c8efec0501417297175378490beb6d21205308f632948db25171cb9b1a2` |

The neutron working corpus differs from the immutable official extraction only by the two fail-closed Pb-208 numeric
field repairs frozen in P10 Amendments D/E; all 2,850 official hashes and both substitutions are independently
recorded. Charged validation reads official processed TENDL-2017 rows, but no licensed FISPACT-II executable was run.
P10 supplies infinite-dilution unresolved averages only; finite-dilution shielding remains explicitly out of scope.
