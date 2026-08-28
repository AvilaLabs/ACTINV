# Nuclear data

## Easiest setup

Install the recommended neutron activation and decay data with:

```bash
actinv data fetch
```

This downloads about 139 MiB and installs about 229 MiB under `actinv-data/v1.0.0/`. ACTINV streams each download to a
temporary file and checks both its byte count and SHA-256 before making it visible. The two decay archives come
directly from the IAEA; both the downloaded ZIP and the one extracted member are verified. Existing correct files are
reused, and an incorrect file is left untouched unless you explicitly request a verified replacement with `--force`.

The command ends with a JSON summary and a `problem_fragment` containing the exact library, decay, spectrum, and
temperature fields for a problem specification. To install somewhere else or check an existing installation:

```bash
actinv data fetch --output /data/actinv
actinv data verify --output /data/actinv
```

Files live in a version directory, so a future catalog cannot overwrite the data used by an older calculation. The
binary carries an immutable catalog rather than downloading mutable instructions. Inspect available bundles or print
that catalog byte-for-byte with:

```bash
actinv data list
actinv data manifest
```

The default is `tendl-2025-neutron`. Optional bundles are
`tendl-2025-neutron-covariance`, `tendl-2025-proton`, `tendl-2025-deuteron`, and `tendl-2025-alpha`; pass one after
`fetch` or `verify`. Shared decay files are reused when multiple bundles use the same output directory.

The generated TENDL-2025 activation libraries and covariance sidecar are separate CC-BY-4.0 release assets. The
ENDF/B-VIII.0 and JEFF-3.3 decay files remain downloads from their official host. The installed
`ACTINV-DATA-NOTICE.md` records attribution, source and builder identities, transformations, and terms. ACTINV's
MIT/Apache-2.0 software licence does not replace a dataset's licence or source terms.

Catalog v1.0.0 is published as the immutable
[`data-v1.0.0` release](https://github.com/AvilaLabs/ACTINV/releases/tag/data-v1.0.0). Its server-reported asset digests,
source commit, green workflow, and public smoke result are recorded in `results/session_p13.json`.

For an offline installation, run `actinv data manifest` on the destination system, acquire the named files through a
controlled transfer, place them under the printed versioned paths, and run `actinv data verify`. Advanced users can
instead build libraries from raw evaluations with `actinv build-library` as documented below.

## Prepared calculation cache

The downloaded nuclear-data files are the durable, versioned inputs. Separately, ACTINV automatically creates a
disposable prepared cache the first time it uses an activation library. A compact groupwise artifact supports mesh
and prepared workflows; the ordinary single-spectrum path also stores an exact spectrum-collapsed artifact. This is
why the first calculation can take a few seconds longer while later calculations start faster and use less memory.

Every cache artifact is bound to the source-library hash, source-index hash, schema and (where applicable) the exact
flux bits. ACTINV verifies its internal SHA-256 and layout before use. A corrupt, stale or incompatible final artifact
fails with an error and is not silently replaced. Temporary files are published atomically, so an interrupted writer
cannot leave an accepted partial result.

By default the cache follows the operating system's cache-directory convention. Advanced users can choose an
absolute location with `ACTINV_CACHE_DIR`:

```bash
ACTINV_CACHE_DIR=/data/actinv-prepared actinv run problem.json result.json
```

The public iron example creates about 282 MiB of prepared files for its library and spectrum. Other libraries and
spectra differ. Removing this directory is safe: ACTINV recreates the same verified bytes on demand, and calculation
results and certificates are unchanged. Do not archive the prepared cache as nuclear-data provenance; retain the
original source files, declared hashes, problem specification and result certificate instead.

## Sources, terms, and hashes

ACTINV does not put nuclear-data payloads inside the software package or Git repository. Each run points at explicit
files and records their SHA-256 hashes in its certificate. Terms of use are those of the data providers; check them
before redistribution.

| data | host | notes |
|---|---|---|
| EAF-2010 neutron activation (816 targets) | IAEA-NDS `public/download-endf/EAF-2010/n/` | pointwise-complete MF=3, MF=8/9/10 isomer branching |
| TENDL-2017…2025 | IAEA-NDS `public/download-endf/TENDL-20xx/n/` | resolved resonances need reconstruction (own code) |
| FENDL-3.2c (ENDF-6, ACE, GENDF, NJOY inputs) | IAEA-NDS `fendl/` | transport library; used as the NJOY reference for our reconstruction |
| ENDF/B-VIII.0 decay sublibrary | IAEA-NDS mirror bulk zip | primary decay data (3,821 materials) |
| ENDF/B-VIII.0 neutron-induced fission-product yields | NNDC/IAEA ENDF bulk archive | MF=8/MT=454 independent and MT=459 cumulative tables; P9 production uses only MT=454 |
| JEFF-3.3 radioactive decay data | IAEA-NDS mirror bulk zip | fallback (3,852 materials) |
| FNS decay-heat benchmark set | IAEA CoNDERC `conderc/fusion/files/fns.zip` | 73 materials, 132 experiments; spectra, measurements, FISPACT-II reference runs |
| FNG/ITER cell-620 research archive | Zenodo record 10660030, DOI `10.5281/zenodo.10660030` | CC-BY-4.0 campaign-1 activation history and supplied one-group data used by P12 |
| U-235 fission decay-heat set | IAEA CoNDERC fission archive | Dickens thermal pulse and Yarnell 20,000 s measurements, paired FISPACT-II inputs and reference reports |
| ALARA 2.9.2 | official [`svalinn/ALARA`](https://github.com/svalinn/ALARA) source | P9 identical-data Fe-56(n,p)Mn-56 pulse comparison; repository `LICENSE` is 3-clause BSD |
| natural abundances / atomic masses | Meija et al. 2016 (IUPAC/CIAAW) and AME2020 | independently parsed from the primary tables; source hashes below |
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

## P12 embedded abundance and mass-table provenance

The 289 natural-isotope rows embedded in `actinv-data` are independently re-derived by
`controls/g2_p12_primary_tables.py`; OpenMC is no longer the provenance oracle. The abundance source is Meija et al.,
*Isotopic compositions of the elements 2013*, Pure and Applied Chemistry 88 (2016) 293–306,
DOI `10.1515/pac-2015-0503`. The [NRC archive copy](https://nrc-publications.canada.ca/eng/view/object/?id=aeb83db7-8cc2-41ad-9847-519b9471bae8)
has SHA-256 `d9079171301dc440e6ee40378da1aa5aef7c43e99d815f4cf31c1eb76561dd89`. ACTINV uses Table 1's
representative Column 9 when it is a point value and the best-measurement Column 6 when Column 9 is an interval; the
naturally occurring Ta-180 row denotes `Ta180m1`.

Ground-state masses come from the fixed-width `mass_1.mas20` file of AME2020 (Huang et al. and Wang et al., Chinese
Physics C 45 (2021) 030002/030003), obtained from the [Atomic Mass Data Center](https://amdc.impcas.ac.cn/web/masseval.html),
with SHA-256 `e8599c6d7f724fac91934e59f1b9de8fb8f63e820f4b39456b790665ed2a3307`. The independent control
extracts all 289 abundance and mass values directly from those primary files, matches every binary64 value and key,
checks every element sum within `2e-15`, and reproduces the generated Rust table byte-for-byte. The primary files
remain external and are not redistributed by ACTINV.

## P12 FNG/ITER activation reference

P12 uses the open research archive accompanying Peterson et al., *Nuclear Fusion* 64 (2024) 056011,
DOI `10.1088/1741-4326/ad32dd`. The CC-BY-4.0 archive is Zenodo record 10660030 and has SHA-256
`1c76f42dcbc3e0f488f8035c3f63e4cd4428930f76efc088329be7ec9c6b45ed`.

| archive member | SHA-256 |
|---|---|
| `microxs_620.csv` | `fa097a994e8a4ea93267603bd6435972c15d3daa1d89cb37b626e21147637651` |
| `depletion_results.h5` | `1fcd608a0a8100892b4d24ca7de05d401ab952b904ac3d80c8698de36419d4d5` |
| `flux_620.npy` | `9f2b3223164adbe5709aa493943af0a1fde3b538654ec28993b32dfe56195828` |
| `inventory.i` | `c2fdfc04547017823c533e5a48199c5bd49cfb33fe36fb7a984a88c30c20516b` |
| `fluxes` | `25bc8b50a74147f4cc4637a24e2c6d0d8b24562447abb28e7ba699bc03390fde` |
| `chain_endfb80_reduced.xml` | `f3f56d3a9ee66bcb691ea0812aad6a3696c00f6272f503de866a495b85c7270e` |

The P12 control derives temporary ACTINV library and decay inputs from those files and checks selected reaction rates
independently before comparing the supplied histories. The archive, extracted members, and generated nuclear data
remain outside Git. This is an activation-history comparison for the recorded cell, material, data, and schedule; it
is not a neutron/photon transport or general shutdown-dose validation.

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

## P11 TENDL-2025 MF=33 covariance provenance

P11 uses the same 2,850-file TENDL-2025 neutron working corpus and activation library recorded above. The covariance
source is ENDF-6 MF=33; the two P10 Pb-208 fail-closed working-file substitutions are retained in source identity but
do not alter a covariance record. The complete source manifest is
`34f2048782bd50e4cab69e269826215632675514dd88c2bad1fe70ee92ce1ac4`. An independent Python parser re-hashed and
parsed every source and reproduced all 84,489 sections and 285,023 components with zero error or omitted target.

| artifact / reference | SHA-256 or identity | role |
|---|---|---|
| TENDL-2025 neutron activation library | `ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44` | 167,735 row spectrum library to which covariance is bound |
| activation index | `8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb` | target/source/group identity |
| P11 covariance builder fingerprint | `c9825cafd8945f32efda4a00ea081af811b887562ebf07ae33ac05ea1d6846d1` | exact Rust parser/storage/aggregation source identity |
| complete covariance sidecar | `c19dec86b44ad5d90b66c9ab94d53e18641a1d354a89402a4da7986b6c530cde` | external `actinv-covariance-1` NPZ |
| complete covariance index | `9691ee5c4a7e3e89c428f912de712b5f805b29c86cc94a5b23f3c95a5951aea6` | byte-identical fresh/cached index |
| ENDF-6 Formats Manual used by P11 | `77a0fee413c3b1d5d74a161ed9fe7f77bbcbc58a654304851b7b2b400183d022` | normative MF=33 and LB=0--6/8/9 definitions |
| NJOY2016.79 | commit `ac5adf5f33d893e42f2eed7fb286b0d51c7580da`; `errorr.f90` `4fd380f6a8b8c55ea3282bc5aed0e3755bee9361474423981200aa82800b956d` | independent Fe-56 GROUPR/ERRORR collapse control |
| NJOY licence | `08dc30ca5b19bfa904168f5194b646bb13a661e3591c4e2d000e9a514554b76c` | BSD-3-Clause terms for the external control build |

The complete sidecar retains 84,489 LB=5, 116,045 LB=6 and 84,489 LB=8 components. This inventory describes the
evaluation; support for LB=0--6, 8 and 9 is exercised by synthetic controls even where this TENDL corpus does not use
each form. Raw evaluations, NJOY tapes, per-source checkpoints and the 227 MiB generated sidecar remain outside Git.
TENDL and ENDF reference terms remain those of their public hosts; users must check redistribution terms for any data
they archive. ACTINV's MIT/Apache-2.0 licence applies to the software and controls, not to third-party nuclear data.
