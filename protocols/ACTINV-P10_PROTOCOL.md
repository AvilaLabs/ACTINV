# ACTINV P10 — data completeness and Rust library construction

**Roadmap row:** P10 (second phase of the v0.5 milestone). **Opened:** after the P9 close. **Time box:** five
calendar days plus checkpointed background library builds. **External acts:** the principal's. This protocol covers
the remaining activation-data physics (R-matrix-limited resolved resonances, infinite-dilution unresolved averages,
arbitrary-temperature Doppler broadening and the P4 ultra-narrow limitation), proton/deuteron/alpha activation, and
moving every production library-building path into `actinv-data`. Covariance, finite-dilution self-shielding,
probability tables, triton/helion/gamma activation, transport, release publication and P11/P12 work remain out of
scope.

The roadmap's moving term “TENDL (latest)” is resolved before evidence as **TENDL-2025**, the current thirteenth
release (official page last updated 2025-06-28; archive page 2025-07-04). P4's TENDL-2023 artifacts remain historical
baselines, not the claimed v1.0 library. The full P10 deliverable is one deterministic Rust build each of the
TENDL-2025 neutron, proton, deuteron and alpha s30 sublibraries, plus EAF-2010 as the alternative neutron library.
All generated libraries and source evaluations remain outside the repository.

## Minimum gate input and pinned references

Standing rule 7 applies before the full builds. The minimum input is four neutron evaluations (FENDL-3.2c W-186
and Ag-107 plus TENDL-2023 Fr-226 and Rb-94), TENDL-2017 and TENDL-2025 p/d/alpha Fe-56, three TENDL-2025
residual tables, one EAF sample, the official 709/162 group boundaries, NJOY2016.79, and only the three Fe-56 records
extracted from the official FISPACT-II TENDL-2017 processed library. No full TENDL download or build is prerequisite
to G1-G6.

The raw/control files already present at opening are pinned as follows:

- FENDL-3.2c W-186 ENDF, ACE, 709-group file and NJOY deck/output:
  `bf6bf3bb7a1583be49ae8aab865e75d256e0965f969f38a14d63260b3f4a8744`,
  `b11e052d8379b010a6f3dd6d67ae6a2153666bfaa759c17503ad51b919f6d5a4`,
  `022eb861b7ebdfec0b5a47fb448889f544f66a2886c6ff4de1891c06980828f0`,
  `be073dcd636ecc4422f5b42310f6bec9db299568d3f8621e176638a7a06413b6` and
  `ea9a7838b1e3e33f68708e939617f937b08fe72ead4a774bd66a1f4e2522dca0`.
- FENDL-3.2c Ag-107 equivalents:
  `0610e15630cb0837a801611d42b6cd401435ddb93dde1126e63000b83ba14185`,
  `63e0bc185dac9aa90670770ff13b60a0f697ebb0a29fe1895672d8f7b5937df5`,
  `69ab385deb610038a979eda656d21090012af6ab6a044494cd749c1f7f992d8b`,
  `7a427bc55bdfdb03defa27cbd9970c6b9abbac72cdafcd3e2b12e8c132fa60e9` and
  `19f34b3a40e9f4e9f5154a16c250d30506950880305e06de070aa5021a15137c`.
- TENDL-2023 Fr-226 and Rb-94 evaluations:
  `5a2f9fa9b5f53cdf132444694f2502b12fe4f179ca54c06cde0672228df87e67` and
  `0e25329d3881b7af74419ae3a78495c01470bf304c9f9ecc03a2a91416b693f0`.
- TENDL-2017 p/d/alpha Fe-56 evaluations:
  `a817e16d7e5b2bbcc0a8fa4091c9505e4c0364326f26e1727ac72d2c229b6d3a`,
  `ebb4e2af6ceed337b7355233ddbd29912adb223519fc966b4ea01173acedab9a` and
  `e6a2a93837a279eac7000a97bd4168f9efe91349097c0479f3645e7e8b7bac07`.
- TENDL-2025 p/d/alpha Fe-56 evaluations:
  `7a505214adb273a2e71fba7ced0ea792dae853875127111af770ee658e01740b`,
  `cd036d5529c71998d20aa10ae7e8b1d9ae1d7045200b29f46163e5b2faf6ab95` and
  `ff185f3fdf69b6a64a3e9e9eb3964bd8856262b635d674537076c329069a656e`.
  Their independent residual-table controls are Fe-56(p,x)Co-55
  `7dd9940588e92d80e0120a2fb846010667a5284c1c5feb612bd33b4f9b6e2065`, Fe-56(d,x)Co-57
  `40f2e3118fded4bba9036911b3b159cdced4341f9b76d5d8619a9b81b5ce3365`, and Fe-56(a,x)Ni-59
  `12a4344a8c6b11c3d754a5962ede1d6ce3cebfd1b0556bf0d0ab541224f66709`.
- The official FISPACT `ebins.tar.bz2` is
  `fb612c2df07269389b44e15dc101166e675d53269f4078174999650a68e1b63a`; extracted `ebins_162` and
  `ebins_709` are `4b1ba7ec855aa305b3312cb57d75cbcd6be41b4e67e93070df104bd62b500b0e` and
  `31bc68b8b042cf5bddd211508bf3a6315b56d31fdf688263f57f124e972840c4`. The official
  `TENDL2017data.tar.bz2` Git-LFS object is size 2,595,437,294 bytes with object/SHA-256
  `7f305df2277f71a7d7d6d1e1ebfec8dea9415d813e283990c1fb65804b05bec8`; the downloaded object and extracted
  Fe-56 records must independently re-match before use.
- NJOY2016 tag `2016.79` is commit `ac5adf5f33d893e42f2eed7fb286b0d51c7580da`; its `reconr.f90`,
  `unresr.f90` and licence are `054ede7a59e1c39cf3e72105d8a0b95a0fb1d8df0882eca6b949e765b62bf5db`,
  `57a3a975566d45a8f2d0db67fed121b908e50039d9aafb25ea27f628c745d650` and
  `08dc30ca5b19bfa904168f5194b646bb13a661e3591c4e2d000e9a514554b76c`.

## Normative physics, builder and wire-format choices

1. `actinv-data` owns the production ENDF reader, interpolation, resonance reconstruction, broadening, product
   derivation, group collapse, cache and `.npz`/index writer. The Python builders become thin calls into that Rust
   path or explicitly named independent controls; NumPy/SciPy/OpenMC are not runtime build dependencies. The solver,
   CLI, optional PyO3 module and library builder therefore share one Rust implementation. Existing Python-only
   scientific controls remain independent and may not be imported by production code.
2. The CLI contract is `actinv build-library INPUT OUTPUT.npz`, with checked options `--format auto|tendl|eaf`,
   `--projectile auto|neutron|proton|deuteron|alpha`, `--groups fispact-709|fispact-162|PATH`,
   `--temperature-K K`, `--workers N`, `--cache DIR` and the control-only `--grid-density D`. `INPUT` is one ENDF
   file or a directory whose regular files are processed in bytewise filename order. Auto-detection uses MF=1/MT=451
   metadata and validates every file's NSUB/AWI against the requested projectile; a mixed projectile directory,
   duplicate target `(ZA,LISO)`, symlink/non-file entry, malformed/truncated record or changed input is a hard error.
3. ENDF numeric and fixed-width parsing becomes fallible throughout: an invalid field cannot become zero, and record
   counts, interpolation ranges, section terminators, MAT/MF/MT tails, array lengths and finite/nonnegative physical
   values are checked. Production support includes the MF=1 metadata and MF=2/3/6/8/9/10 activation subsets used by
   the named libraries. A syntactically valid but unsupported law is named with file/section coordinates and fails
   that target; it is never silently replaced by MF=3 or an invented product.
4. Neutrons use CCFE-709 and a finite nonnegative requested temperature (default 293.6 K). Proton, deuteron and alpha
   use CCFE-162 and exactly 0 K; target-motion broadening for charged projectiles is not implied. Both structures are
   ascending in the `.npz`; group values are
   `integral(sigma(E) dE/E) / ln(E_hi/E_lo)` with exact ENDF interpolation-law integration. Custom boundaries are
   finite, positive and strictly increasing and are hash-certified. Energy outside an evaluation is zero, including
   the CCFE-162 groups above TENDL's 200 MeV ceiling and below its threshold/5 keV floor.
5. The existing `.npz` arrays and row shape remain `rows[target, MT, ZAP, LFS, LMF]`, `sig[row,group]`, `bounds`, so
   old readers remain valid. Every reaction has one `ZAP=-1` loss row. MF=10 is an independent product cross section;
   MF=9 is a yield multiplying its matching MF=3 reaction; MF=8/LMF=3 uses the reaction cross section. For standard
   channels without an explicit product, residual arithmetic includes the incident projectile's `(Z,A)` before
   subtracting emitted particles. Positive evaluated level indices are deterministically remapped, per `(MT,ZAP)`,
   to ordinal decay-library isomers as in P4.
6. Standard charged s30 evaluations switch to aggregate MF=6/MT=5 above 30 MeV. The loss row is MF=3/MT=5; each
   MF=8/LMF=6 residual product is `sigma_5(E)` times its matching MF=6 TAB1 multiplicity/yield, retaining multiple
   products and isomers. Distribution LAW bodies are structurally consumed and checked even though activation uses
   only the yield. Inventory nuclides including H-1/H-2/H-3/He-3/He-4 are retained; emitted free neutrons are
   explicitly ledgered and omitted from the nuclide inventory. Duplicate/conflicting MF=6/MF=8 declarations fail.
7. Resolved R-matrix-limited (`LRU=1, LRF=7`) reconstruction follows the ENDF-6 limited R-matrix collision-matrix
   equations, including spin groups, particle pairs, channel penetrability/shift/phase, eliminated capture/fission
   channels, Reich-Moore `KRM=3`, signed amplitudes and the evaluation's boundary/radius flags. W-186's two-pair,
   three-spin-group case is the minimum gate. General features encountered in the full TENDL-2025 build are either
   implemented from their declared records or fail closed; background/tabulated phase shifts, an unknown particle
   pair or an unsupported approximation may not be ignored.
8. Unresolved `LRU=2, LSSF=0` ranges produce infinite-dilution elastic, capture, fission and competitive averages
   from ENDF average parameters and width-fluctuation integrals. Cases A/B/C, LRF=1/2, energy-dependent neutron
   widths, degrees of freedom, interpolation and NJOY's ten-point chi-square quadrature constants are supported.
   `LSSF=0` adds these averages to the MF=3 background; `LSSF=1` uses MF=3 as the already-averaged cross section and
   adds nothing. Finite dilution, Bondarenko shielding and probability tables are explicitly not claimed.
9. Neutron broadening starts from the raw 0 K evaluation and applies the exact SIGMA1 kernel at the requested output
   temperature; 0 K is an identity/no-broadening path. The implementation is memory-windowed and shared rather than
   duplicated in `actinv-core`. Arbitrary temperature means any finite `K >= 0` represented by a separately built,
   hash-distinct library; ACTINV never relabels a 293.6 K library or broadens an already-broadened one.
10. For a positive isolated LRF=1/2 capture/fission line whose total width is at most `1e-4` of its Doppler width, the
    ordinary sampled line is removed from the smooth reconstruction. Its area is integrated in the
    `E = E_r + Gamma_r tan(theta)/2` coordinate, then broadened with the exact SIGMA1 delta-line kernel
    `A*x_r*[exp(-(x_r-y)^2)-exp(-(x_r+y)^2)]/(2*kT*y^2*sqrt(pi))`; at 0 K its flat-lethargy group contribution is
    `A/(E_r*ln(E_hi/E_lo))`. Other lines use ordinary adaptive linearisation. This treatment is not applied to an
    interfering formalism, negative resonance or range-edge line without an independently bounded decomposition.
    Every treated line, area, ratio and affected group is certified. Rb-94's P4 residual is instead treated as an
    exact resolved/unresolved boundary-splice problem; it is not falsely labelled an ultra-narrow line.
11. Each target checkpoint is content-addressed by the source hash, normalized options, group-boundary hash and Rust
    builder fingerprint, written atomically, and revalidated before reuse. Rows/targets are sorted after parallel
    work; floating reduction order is fixed; ZIP names/order/compression metadata and JSON key order are fixed.
    Canonical outputs contain no wall-clock timing or host path. Fresh, cached, one-worker and multi-worker builds of
    identical inputs are byte-identical. The index records schema, projectile, temperature, groups/weighting,
    per-target source hashes and ledgers, builder fingerprint, options, row counts and final `.npz` SHA-256; timing
    and profiling live in evidence, not the canonical index.
12. `actinv-spec-1` and `actinv-mesh-spec-1` gain top-level `projectile` with canonical values `neutron`, `proton`,
    `deuteron`, `alpha`; omission defaults to `neutron`, including old indexes which lack the field. Runtime verifies
    spec/index projectile, group structure, temperature and hashes before rates are formed. Charged specs require
    `options.temperature_K: 0`; non-neutron fission-yield files are rejected. Certificates, prepared-run
    compatibility, mesh headers/results and ledgers include projectile. Old neutron specs/results remain
    byte-identical; charged results use `fluence_particles_cm2`, while the historical `fluence_n_cm2` field remains
    neutron-only. Flux values retain the same group-integrated particles cm^-2 s^-1 numerical convention.
13. The public FISPACT-II executable is licensed and is not a repository or gate dependency. The roadmap's
    “FISPACT-II reference runs” is made reproducible as the reaction-rate calculation against the official processed
    TENDL-2017 `tal2015-{p,d,a}/gxs-162` data: ACTINV and an independent reader apply the same fixed 162-group spectra
    to matched residual rows. No report may claim that a FISPACT executable ran unless one actually did. Separately,
    TENDL-2025 MF=6 production is checked against its official pointwise residual tables, so the current deliverable
    is not validated only against an eight-year-old data release.

## Deliverables

1. Strict Rust ENDF-6 builder modules in `actinv-data`, a deterministic/checkpointed `build-library` CLI, and an
   optional thin PyO3 call, covering EAF-2010 and TENDL neutron/proton/deuteron/alpha inputs without Python runtime
   dependencies.
2. R-matrix-limited reconstruction, LSSF=0 infinite-dilution averages, arbitrary-temperature SIGMA1 and an analytic
   ultra-narrow path that clears the Fr-226/Rb-94 P4 limitation without changing converged data.
3. CCFE-162 data, charged-projectile product/MF=6 handling, and end-to-end projectile identity through ordinary,
   prepared, Python and mesh runs.
4. Hash-pinned controls/reports plus complete external TENDL-2025 n/p/d/alpha and EAF-2010 Rust builds, with source
   manifests, output hashes, counts, error/unsupported inventories, profiles and resume evidence.
5. Updated specification, method, data, traps, ledger, validation, CLI help, release-limitations and CI subset docs.

## Gates

**G1 Strict Rust builder, parity and determinism.** Independent Python readers and Rust agree on every parsed
MF=1/2/3/6/8/9/10 field used from the minimum Fe/W/Ag/EAF inputs. For pre-P10 supported neutron/EAF reactions, every
row and every group with either value at least `1e-12 b` agrees with the Python builders to `2e-12` relative or
`1e-14 b` absolute. Fresh/cached and one/four-worker outputs and indexes are byte-identical; a changed source or
option invalidates only its target checkpoint. The representative peak RSS is below 2 GiB per worker and no single
allocation reaches 1 GiB. Invalid numeric fields, counts, interpolation laws, tails, truncation, duplicates, NSUB,
source mutation and unsupported sections all fail with file/MF/MT context and publish no final pair.

**G2 R-matrix-limited W-186.** Against the pinned NJOY-processed ACE of the identical FENDL evaluation, reconstructed
and 293.6 K broadened MT=102 agrees within `2e-3` in every CCFE-709 group overlapping the 1e-5--1e4 eV RML range whose
reference is at least `1e-6 b`; the flat-lethargy integral over the whole range agrees within `5e-4`. Parsed particle
pairs, spin groups, resonance counts/parameters and range flags match an independent reader exactly. Planted
unsupported RML features fail rather than falling back to MF=3.

**G3 Ag-107 unresolved averages.** A fresh NJOY2016.79 UNRESR run at 293.6 K and effectively infinite dilution is
the primary reference, with the pinned ACE/deck retained as a cross-check. Capture, elastic, fission and competitive
values above `1e-8 b` agree at every one-sided interior parameter energy within `2e-4`, and CCFE-709 group capture
within the 6.5--100 keV unresolved range agrees within `5e-4`. Independent high-order quadrature agrees with Rust's
three unresolved cases to `1e-10`; synthetic LSSF=0 background addition and LSSF=1 non-addition are exact to `1e-12`.

**G4 Temperature and ultra-narrow treatment.** At 0, 293.6, 600 and 900 K, Rust agrees with independent exact-kernel
quadrature within `1e-10` for the 1/v and constant invariants, a synthetic resonance and W-186 capture; 0 K is exact
identity on the input grid. Every treated Fr-226 line area agrees with direct adaptive integration and the closed-form
frozen-width area within `1e-6`. On Fr-226 and Rb-94, capture/fission group values at grid densities 1, 2 and 4 agree
within `1e-3` for every group at or above `1e-4 b`, with no convergence flag. Every previously converged seeded P4
row changes by at most `2e-12`. A recorded representative profile shows Rust's median wall time and peak RSS no
greater than the pure-Python production predecessor.

**G5 Charged particles and FISPACT reference.** For TENDL-2025 Fe-56, the independently summed MF=3/10 and
MF=3(MT5)*MF=6 residual production for Co-55 (p), Co-57 (d) and Ni-59 (alpha) agrees with the official residual table
at each tabulated 35, 50, 100 and 200 MeV point within `2e-6` relative. On identical TENDL-2017 Fe-56 data, matched
residual CCFE-162 group rows and their one-group values under three fixed nonnegative spectra agree with the official
FISPACT processed `gxs-162` records within `2e-3`; Rust and a separate parser agree to `1e-12` on the rate dot product.
Explicit-channel arithmetic, MF=6 multiplicities, multiple products and level remapping are checked independently,
and wrong projectile/NSUB, missing yield, conflicting product and malformed LAW plants fail closed.

**G6 Runtime projectile contract.** A neutron spec/index with no projectile remains bit-identical to the pre-P10
baseline. Proton, deuteron and alpha synthetic activation cases give the analytic one-step parent loss/product feed
through CLI, PyO3, prepared and mesh paths at zero differing fields, including projectile and generic fluence. Every
spec/index/group/temperature mismatch, a charged nonzero temperature, charged fission yields, and an unknown
projectile fail before matrix assembly. Library/index/source hashes and projectile independently re-match every
certificate.

**G7 Complete builds, provenance and regression.** The Rust path completes external TENDL-2025 neutron at 293.6 K
on CCFE-709, proton/deuteron/alpha at 0 K on CCFE-162, and EAF-2010 on CCFE-709 with zero target errors, zero silent
unsupported fallbacks and zero convergence flags. Counts, source-archive/file manifests, builder/options/group hashes,
checkpoint-resume profile and final output/index hashes are recorded; no nuclear data enter Git. The EAF build's
rows/groups reproduce its P2 library within the G1 tolerance. Workspace tests, strict Clippy/rustfmt, the CI subset
and all P5-P9 controls pass; P5 remains P5-PASS and P6-P9 retain their recorded conditional verdicts. Documentation
contains no claim that TENDL-2023 is current, that finite-dilution self-shielding exists, or that licensed FISPACT ran.

## Verdict (`controls/check_p10.py`)

P10-PASS: G1-G7. P10-CONDITIONAL after one documented repair round on a gate. P10-FAIL otherwise. Standing rules
1-7 apply. A PASS or CONDITIONAL close completes the technical v0.5 milestone but does not tag, push or publish it.
