# ACTINV-P173 — group-collapse and self-shielding error in ACTINV's reaction rates, against OpenMC continuous energy

This is item B1 of `docs/maintainers/WORKFLOW_ACCURACY_PLAN.md`.

## Purpose

ACTINV computes each reaction rate as `sum_g sigma_g phi_g`: a 709-group cross section, collapsed with a flat-lethargy
weight inside each group at infinite dilution, times the transport code's 709-group flux. Two approximations live in
that product:

1. **Within-group shape.** The real flux inside a group is not flat in lethargy.
2. **Self-shielding.** In a resonant material the flux dips at the resonances, so the true rate is lower than the
   infinite-dilution group cross section gives. Tungsten capture is the known case.

OpenMC's continuous-energy reaction-rate tally makes neither approximation. A third quantity separates two causes of any
difference: the same flux collapsed with group cross sections that this protocol's harness builds from OpenMC's own
pointwise data, with the weight and groups ACTINV's builder uses. The difference between ACTINV and that collapse is
processing (ACTINV's builder against NJOY); the difference between that collapse and OpenMC's tally is the collapse
itself and self-shielding. The question:

> In fusion-relevant materials and spectra, by how much do ACTINV's 709-group rates for the reactions that dominate
> shutdown dose differ from OpenMC's continuous-energy rates on the same evaluated data, and is the difference large
> enough to be worth correcting?

This protocol certifies the measurement. A PASS can show that no correction is needed. It makes no accuracy claim
against experiment.

## Disclosure of what has been seen

- No scored quantity of this protocol (an OpenMC tally, a collapsed ACTINV rate, any ratio of the two) has been
  computed or read before registration.
- The library and the OpenMC data are the ones P172 built and used. P172's records of them (SHA-256 below) were
  read; their cross-section values were not compared with OpenMC's reaction rates.
- Every parameter below (models, shells, compositions, reactions, the 0.5% target, the decision thresholds) was fixed
  from the shutdown-dose literature and the draft of this protocol, not from any run.

## Same data

- **ACTINV library.** `~/nuclear-data/endfb-viii.1-p172/endfb81_all_709g.npz`, SHA-256 `ecaca7c0…6de32`, index
  `ff59cccf…62fa`. Built by P172 from the NNDC ENDF/B-VIII.1 archive (SHA-256 `decff900…64e4`) at 293.6 K, 709
  groups, flat-lethargy weighting. It holds 551 of the 557 neutron nuclides of OpenMC's library; the six it lacks are
  actinides (U-235, U-238, Pu-239, Pu-241, Am-241, Th-229) and appear in no model here.
- **OpenMC data.** The official ENDF/B-VIII.1 HDF5 library P163 and P172 used (archive SHA-256 `b7ad59cb…c4cb`),
  at 294 K.

The two are processed from the same evaluation by different codes (NJOY for OpenMC, ACTINV's own builder). Differences
from processing are therefore part of what is measured, and the three-way decomposition (Score, below) separates them
from collapse and shielding.

What ACTINV's builder does (read from `crates/actinv-data/src/groups.rs` and `processing.rs`, not assumed): the group
structure is `fispact_709_groups.json`; the weight is flat in lethargy, `sigma_g = (integral of sigma dE/E) / ln(E_hi/E_lo)`
with the evaluation's own interpolation laws; resonance reactions (MT 102, 18) are reconstructed from the raw 0 K MF=2
plus MF=3 data and Doppler-broadened to the library temperature, 293.6 K, with ultra-narrow lines added analytically;
reactions without resonance contributions are used as evaluated.

## Models (fixed now)

Each model is a set of concentric spherical shells around an **isotropic 14.1 MeV point source at the origin**
(monoenergetic neutrons, the source energy exactly 14.1 MeV). The outer boundary of the outermost shell is **vacuum**.
Inside the innermost listed shell is void. Between listed shells the same material fills the gap, so each model is a
solid shell of one material from the first listed inner radius to the last listed outer radius; the gap shells are not
tallied. Each listed shell is one tally region, named `MODEL:r_in-r_out` (radii in cm).

| Model | Material | Shells (cm, inner–outer; solid from the first inner to the last outer radius) | Why |
|---|---|---|---|
| S | SS316L(N)-IG, ITER grade, 7.93 g/cm³ | 10–12, 20–25, 40–50 | Fast to degraded spectra in the main structural steel |
| W | Tungsten, natural, 19.3 g/cm³ | 10–10.5, 10.5–12, 15–20 | Strong resonance self-shielding, thin to thick |
| C | CuCrZr, 8.9 g/cm³ | 10–12, 20–25 | Heat-sink alloy |
| K | Ordinary concrete (PNNL-15870 Rev. 1), 2.3 g/cm³, with 1 ppm Co and 1 ppm Eu by mass | 50–60, 100–120 | Thermalised spectrum, (n,gamma) dominated |

There are four models and four OpenMC runs.

### Materials (fixed now)

Mass fractions by element, expanded by OpenMC 0.15.3 to the natural isotopic composition of its own abundance table
(`Material.add_element(..., 'wo')`); the resulting atom densities are recorded per model. The same numbers are
constants in `controls/p173_ce_rates.py` (`MATERIALS`) and are hashed by G0 through the constants digest.

| Material | Density (g/cm³) | Mass fractions |
|---|---|---|
| SS316L(N)-IG | 7.93 | Fe 65.22 %, Cr 17.5 %, Ni 12.0 %, Mo 2.5 %, Mn 1.8 %, Si 0.5 %, N 0.07 %, Cu 0.3 %, Co 0.05 %, C 0.03 %, P 0.025 %, S 0.005 % |
| Tungsten | 19.3 | W 100 % |
| CuCrZr | 8.9 | Cu 99.15 %, Cr 0.75 %, Zr 0.10 % |
| Ordinary concrete, PNNL-15870 Rev. 1 | 2.3 | H 0.022100, C 0.002484, O 0.574930, Na 0.015208, Mg 0.001266, Al 0.019953, Si 0.304627, K 0.010045, Ca 0.042951, Fe 0.006435 (fractions of 1, as published, summing to 0.999999) |
| Concrete as modelled (K) | 2.3 | the row above renormalised to sum to 1 − 2e-6, plus Co 1e-6 and Eu 1e-6 by mass |

The steel is the nominal mid-range of the ITER 316L(N)-IG specification with iron as balance. No S(alpha,beta) thermal-scattering data are used in any model (free-gas
scattering). The spectrum is the same for both codes, so this affects neither collapse nor the comparison, only which
spectra are examined.

## Reactions (fixed now, from the shutdown-dose literature, not from any run)

| Material | Reactions |
|---|---|
| Steel | Fe56(n,p)Mn56, Fe54(n,p)Mn54, Mn55(n,2n)Mn54, Mn55(n,g)Mn56, Ni58(n,p)Co58, Ni60(n,p)Co60, Co59(n,g)Co60, Cr50(n,g)Cr51, Fe58(n,g)Fe59, Ni58(n,2n)Ni57, Mo98(n,g)Mo99 |
| Tungsten | W186(n,g)W187, W184(n,g)W185, W186(n,2n)W185, W182(n,p)Ta182, W183(n,p)Ta183, W182(n,g)W183, W186(n,p)Ta186 |
| CuCrZr | Cu63(n,g)Cu64, Cu65(n,2n)Cu64, Cu63(n,a)Co60, Cr50(n,g)Cr51, Zr90(n,2n)Zr89 |
| Concrete | Na23(n,g)Na24, Al27(n,a)Na24, Co59(n,g)Co60, Eu151(n,g)Eu152, Mn55(n,g)Mn56, Fe54(n,p)Mn54, Ca44(n,g)Ca45 |

Every listed reaction is a dose-relevant reaction for the
decision rule. The rows are the product of models, regions and reactions: 78 in all (S 33, W 21, C 10, K 14).

The MT numbers are 16 (n,2n), 102 (n,g), 103 (n,p), 107 (n,a). Each rate is the total for the reaction, summed over
product states on both sides. ACTINV adds every product-state row of the target (ground state) and MT in the library
(and a total row only if there is no product row); OpenMC tallies the MT score for the nuclide. A reaction a code
cannot score, one missing from the ACTINV library, a target not present in the material, or a zero rate is reported as
not scored with a reason code, never dropped silently.

Mn55 is not in the PNNL-15870 ordinary-concrete composition, so the K row Mn55(n,g)Mn56 is expected to be reported as
not scored (target absent). It is kept in the list as registered.

## Normalisation

All rates are **per source particle, per target atom, in barn cm**.

- `R_openmc`: OpenMC reaction-rate tally for the nuclide and MT score (`(n,2n)`, `(n,gamma)`, `(n,p)`, `(n,a)`),
  track-length estimator, `multiply_density = False`, one energy bin from 1e-5 eV to 1e9 eV, in the shell's cell:
  the shell integral of `sigma(E) phi(E)`.
- `R_actinv = sum_g sigma_g L_g`: the library's group cross section (barn) times OpenMC's 709-group flux tally in the
  same cell (track-length estimator, cm per source particle, on the fispact-709 boundaries the library uses).

- `R_omg = sum_g sigma_g^OMC L_g`: the same flux with group cross sections computed by the harness from OpenMC's own
  HDF5 data for that nuclide and MT at 294 K (`/<nuc>/energy/294K` and `/<nuc>/reactions/reaction_<MT>/294K/xs`, started
  at its `threshold_idx`; linear-linear in energy, zero outside the table). `sigma_g^OMC` is the exact integral
  `(integral of sigma(E) dE/E) / ln(E_hi/E_lo)` over the fispact-709 groups, piece by piece at every grid point and group
  boundary, in closed form for a linear function times 1/E.

The target's atom density multiplies all three and cancels from the ratios. G1 checks the harness' `R_actinv` against the
collapse that `actinv inspect xs --spectrum` performs.

## Runs

- **One OpenMC fixed-source model per run**, neutron transport only, ENDF/B-VIII.1 at 294 K, source seed
  `173000 + index of the model in the order S, W, C, K`. Batches of 10^5 particles.
  The harness runs 4, 8, 16, …, 256 batches (the same seed, so each run contains the previous one) and stops at the
  first count at which **every tallied rate is above zero with a relative standard error at or below 0.5%**, or at
  2^8 = 256 batches (2.56e7 particles). Every iteration is recorded. A rate that never reaches 0.5% is reported with
  its error and excluded from the decision rule below. A rate that stays zero is reported as not scored.
- **Tallies in each region:** the 709-group flux, on the fispact-709 boundaries the library uses; and, per listed
  reaction, the continuous-energy reaction-rate tally.
- **ACTINV rate:** `sum_g sigma_g L_g`, computed by the harness from the library arrays and the tallied group flux.
- **OpenMC group rate:** `R_omg`, computed by the harness (an OpenMC-interpreter step, no transport) from OpenMC's HDF5
  data and the same tallied group flux.
- **Limits:** the repository's bounded scope (6 GiB, 2 CPUs); every OpenMC run in its own scope of the same limits.

## Score

For each (model, region, reaction), three log ratios, each with a first-order standard error:

- `r = ln(R_actinv / R_openmc)`: the total difference;
- `r_proc = ln(R_actinv / R_omg)`: processing, ACTINV's builder against NJOY, with the same weight, groups and flux;
- `r_coll = ln(R_omg / R_openmc)`: collapse and self-shielding, processing removed.

`r = r_proc + r_coll`, and G2 checks the identity.

Standard errors:

- `se(r)² = (se(R_actinv)/R_actinv)² + (se(R_openmc)/R_openmc)²` and
  `se(r_coll)² = (se(R_omg)/R_omg)² + (se(R_openmc)/R_openmc)²`, where `se(R_openmc)` is OpenMC's tally standard
  deviation and `se(R_x) = sum_g sigma_g se(L_g)` for the sums over the tallied flux (the groups taken as fully
  correlated, which is the larger value). The OpenMC tally and the flux-weighted sums are taken as independent although
  they come from the same histories. Both choices make the standard error larger than the true one.
- `r_proc` uses the same `L_g` in both sums, so flux noise largely cancels. With
  `a_g` ACTINV's summed group cross section for the reaction and `o_g = sigma_g^OMC`,
  `d r_proc = sum_g (a_g/R_actinv − o_g/R_omg) dL_g`, and with the groups fully correlated
  `se(r_proc) = sum_g |a_g/R_actinv − o_g/R_omg| se(L_g)`.

Reported per row: the three ratios and their errors, the three rates with their errors, and the region's flux-weighted
mean energy (each group at its logarithmic mean energy).

## Frozen inputs

| input | SHA-256 |
|---|---|
| `~/nuclear-data/endfb-viii.1-p172/endfb81_all_709g.npz` | `ecaca7c072bd76b49e6724dd00aaa43172698f7c6a28e6551c551de39d11de32` |
| its index | `ff59cccf0ca85e24542054fff035079baefaea79a213e41c94bde2bc647162fa` |
| OpenMC ENDF/B-VIII.1 archive `endfb81.tar.xz` | `b7ad59cb4a3d76d8a291326093a98507f8d24b6e6af629116d3f7dc85f83c4cb` |
| `~/nuclear-data/endfb-viii.1-hdf5/cross_sections.xml` | `b8ffb6104a946bf951dc0f3b9a1f57a60f7f57c4b308cd66df4fd3dd4bc557ce` |
| `crates/actinv-data/data/fispact_709_groups.json` | `ce4bf523498ffbb38d42aa9415fc014882b8bc7a7ecfb89f859ba3259d8fb34f` |

The release `actinv` binary used by the G1 collapse check was built before registration inside the bounded scope from
crates tree `fd259e18ecdeaff326672fce811996610a599bc1` (master `b1a1cc4`), copied to
`target/p173-bin/actinv` in the main checkout and passed as `ACTINV_BIN`. Its SHA-256,
`ebeeb25c8f27a6689a2ab8667b699e5abb64d34f3349e4c9b203ee1d1709a725`, is pinned in `controls/p173_ce_rates.py`
(`ACTINV_BIN_SHA256`).

These are recorded by hash in the record:
- `controls/p173_ce_rates.py` (the harness, which holds the models, materials, reactions and run schedule as constants;
  the record also carries the SHA-256 of those constants);
- `crates/actinv-data/data/fispact_709_groups.json`.

The tracked tree must be clean.

## Environment

- OpenMC 0.15.3 in `~/micromamba/envs/openmc0153dd` (as P163 and P172). Cross sections via `OPENMC_CROSS_SECTIONS`.
  The OpenMC executable is the one in that environment's `bin`.
- Each mode runs inside the AGENTS.md systemd scope (`MemoryMax=6G`, no swap, 128 tasks, 200% CPU), and every OpenMC
  run is started by the harness in its own scope of the same limits. The record keeps the cgroup limits of every
  mode and of every OpenMC run.
- `OMP_NUM_THREADS=2`, two OpenMC threads; `PYTHONHASHSEED=0`.
- The harness' ACTINV mode reads the library with numpy; no other dependency is added.

## Gates

- **G0:** this protocol's SHA-256 is in `protocols/protocol_hash.txt`; the record's harness, group-structure and
  constants hashes equal the files' and the harness' own.
- **G1:** pins, OpenMC 0.15.3, the pinned `actinv` binary, a clean tracked tree and the bounded cgroup of every mode and
  every OpenMC run recorded. The harness collapse agrees with `actinv inspect xs --spectrum` to 1e-9 relative, for
  every listed reaction of one region (the first) of each of the four models.
- **G2:** every listed (model, region, reaction) is scored or reported as not scored with a registered reason code;
  each scored row's `r`, `r_coll` and their standard errors, and `r_proc`, recompute from its stored rates, and
  `r = r_proc + r_coll`; the batch schedule is 4, 8, …, 256 batches
  of 10^5, stopping only when every rate has converged or at 256 batches.
- **G3:** the decision rules below are computed by the checker from the record and written to
  `results/p173_verdict.json` (with `--no-write`, compared with it).
- **G4:** CI green on the evidence commit.

## Decision rules (what the result permits)

**Used rows.** A scored row whose OpenMC relative standard error is at or below 0.5%.

**Material.** For x equal to `r`, `r_proc` or `r_coll`, a used row is material in x when `|x| > 0.05` and
`|x| > 3 x se(x)`. A row where ACTINV's rate is zero and OpenMC's is positive with a relative standard error at or below
0.5% (reason code `actinv_zero`) is material in `r` and `r_proc`, not in `r_coll`.

**Correction warranted.** Item 2 of the workflow plan (a continuous-energy rate correction) proceeds if at least two
distinct reactions have material `r_coll` in the same region of model S, W, C or K.

**Processing review warranted.** If any used row has material `r_proc`, the finding goes to a data-processing review
of ACTINV's builder against NJOY, with the rows listed. This does not block the correction decision.

**Allowed public sentences, if earned.**

- "On the same ENDF/B-VIII.1 data, ACTINV's 709-group reaction rates agree with OpenMC's continuous-energy rates
  within X% for the listed reactions," only if no row is material in `r` and at least one is used. X is the largest
  `exp|r| − 1` over used rows, in percent, rounded up to 0.1. If any listed row is not scored or not converged, the
  checker adds that count in a second sentence, and the first sentence does not cover them.
- Otherwise none until the correction is qualified by its own protocol.

No sentence may call a code more or less accurate than the other.

## Verdict

P173-PASS if G0–G3 hold (G4 external), whatever the agreement. One repair round, registered as an amendment first.

## Disclosures

- This compares two calculations, not calculation and measurement.
- OpenMC's continuous-energy tally is the reference only for these two approximations. It shares the evaluation's own
  errors.
- Temperature differs by 0.4 K (293.6 against 294 K), which is negligible for these reactions.
- **`R_omg` uses OpenMC's NJOY-processed data at 294 K; ACTINV's library is its own build at 293.6 K.** The 0.4 K
  difference is negligible. The decomposition attributes to `r_proc` every difference between the two processings,
  including the treatment of Doppler broadening in the resonance region, the linearisation tolerance of each code,
  ACTINV's use of the evaluation's own interpolation laws against NJOY's linear-linear tables, and the unresolved
  resonance region (OpenMC's HDF5 holds the infinite-dilution average cross section; its transport samples probability
  tables there, which `R_omg` does not, so such an effect appears in `r_coll`).
- ENDF/B-VIII.1 neutron evaluations end at 20 MeV; the 709-group library has zero cross sections above it and the flux
  tally has no contribution there, as in OpenMC's data.
- Statistical error is that of OpenMC's tallies. The standard error of `r` is conservative as described above, and the
  decision rule uses it.
- The material compositions are nominal. The result characterises these models, not a specific component.

## Not claimed

- Anything about reactions not listed, materials not modelled, or spectra other than a 14.1 MeV point source in these
  shells (a fission or thermal-reactor spectrum, a different geometry).
- That OpenMC's rate is the true rate, only that it is free of the two approximations.
- Anything about ACTINV's optional Bondarenko or geometry self-shielding. It is off in this protocol, which measures
  the default user path; its benefit is not measured here.
- Anything about speed (P163), decay heat (P172) or accuracy against measurement.
- That ACTINV's collapse is right or wrong for a model not run here. A correction to ACTINV, if one is built, is
  qualified by its own protocol.

## Rehearsal

Before registration the coordinator may run the whole pipeline once with `P173_REHEARSAL=1`: a particle number of 1000
per batch and at most 8 batches, writing only under `target/p173-rehearsal*`, never `results/`. It exists to find
harness defects before the scored run. The checker's gate lines and the processes' exit codes are the only things read;
the outputs are deleted afterwards.

It ran on 2026-10-30, before registration, with the pinned binary. Every mode exited 0. The checker first failed to
find the record, because it did not know the rehearsal paths, and then applied the scored run's 1e5 particles and 256
batches to the rehearsal. Both were fixed in `controls/check_p173.py`: under `P173_REHEARSAL=1` it reads the
rehearsal's record and takes the batch constants from the harness. After that G2 and G3 passed. G0 and G1 failed only
on the protocol hash and the binary pin, which registration supplies. No rate, ratio or summary was read, and the
outputs were deleted.

## Commands (coordinator)

From the worktree, with `PYTHONDONTWRITEBYTECODE=1`, each inside
`systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 -p TasksMax=128 -p CPUQuota=200% -- env RAYON_NUM_THREADS=2`:

1. (Before registration: `cargo build --release -p actinv-cli` with `CARGO_BUILD_JOBS=1`; the digest of
   `target/release/actinv` goes into `ACTINV_BIN_SHA256`; `python3 -I controls/check_p173.py --self-test`.)
2. `python3 controls/p173_ce_rates.py openmc --model S`, then `W`, `C` and `K`. Starts the 0.15.3 interpreter itself
   (first the cross-section collapse, then the OpenMC runs).
3. `python3 controls/p173_ce_rates.py actinv --model S`, then `W`, `C`, `K`.
4. `python3 controls/p173_ce_rates.py g1-check` (uses `ACTINV_BIN` or `target/release/actinv`).
5. `python3 controls/p173_ce_rates.py assemble`, then `python3 controls/check_p173.py`.
