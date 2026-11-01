# ACTINV-P178 — mixture self-shielding on held-out models, against OpenMC continuous energy

This is the qualification step of item 2 of `docs/maintainers/WORKFLOW_ACCURACY_PLAN.md`.

## Purpose

P173 measured ACTINV's 709-group reaction rates against OpenMC's continuous-energy tallies on the same ENDF/B-VIII.1
data and found that resonance capture in steel and copper is over-estimated at infinite dilution: eleven of its 36
capture rows were material, up to 0.188 in `ln(R_actinv/R_openmc)`. P176 and P177 tried ACTINV's existing optional
self-shielding on P173's tallies (the Bondarenko table, then the geometry mode); neither gave a candidate. Ledger 316 recorded a lead: per-group narrow-resonance factors from the mixture's own pointwise total cross
section, folded with P173's flux, took the material capture rows from 11 to 0. That lead was formed on P173's own
rows, so it is not evidence. ACTINV now implements it as `self_shielding.mixture` (`b7cadb2`). The question:

> On four fusion-relevant models that P173 and the lead never used, does ACTINV's `self_shielding.mixture` bring its
> 709-group resonance-capture rates within materiality of OpenMC's continuous-energy rates, without moving the rates it
> is not meant to move?

This protocol certifies the measurement. A PASS can show that the correction fails. It makes no accuracy claim against
experiment.

## Disclosure of what has been seen

- No scored quantity of this protocol (an OpenMC tally on these models, a collapsed ACTINV rate, a mixture factor for
  these materials, any ratio) has been computed or read before registration.
- P173's records and ledger 316's lead on P173's rows were read in full. They shaped the method, not these models.
- The four models, their materials, regions and reactions were fixed by the harness draft (`9836a0a`, then the mixture
  arm `9e77ea2`) from nominal specifications and the shutdown-dose literature, before the corrected library existed.
- The corrected library's index was compared with P172's for completeness: the same 551 targets and 10,804 rows; it
  differs only in the parse notes (P172's MF=1/MF=32 NaN-repair notes are gone) and in the MT 102 reconstruction notes of
  the 15 R-matrix-limited targets ledger 316 lists. No cross-section value was compared with OpenMC's.
- The pipeline was rehearsed before registration (see Rehearsal). Only exit codes and gate lines were read.

## What is held out

The models differ from P173's in material, composition, region radii and body size:

| Model | Material | Regions (cm) | Body to (cm) |
|---|---|---|---|
| E | Eurofer-97, 7.75 g/cm³ | 5–7, 15–18, 30–35 | 45 |
| I | Inconel 718, 8.19 g/cm³ | 3–5, 12–15, 25–30 | 35 |
| H | Tungsten heavy alloy W-7Ni-3Fe, 17.0 g/cm³ | 1–2, 4–6, 8–10 | 12 |
| B | Copper (OFHC), 8.96 g/cm³ | 3–5, 10–14 | 18 |

They share nuclides with P173's models (Fe, Cr, Mn, W, Cu, Ni). A nuclide's capture resonances are the same evaluation
in both. What is held out is the mixture around them and the spectrum: tungsten diluted by nickel and iron, copper with
no chromium or zirconium, steel with 9 % Cr and 1 % W, a nickel alloy with niobium and molybdenum.

Geometry, as P173: concentric spheres; void inside the first region; the material solid from the first region's inner
radius to the body radius; vacuum outside; an isotropic 14.1 MeV point source at the origin. Each listed region is one
tally cell, named `MODEL:r_in-r_out`; the material between and beyond them is not tallied.

### Materials (fixed now)

Mass fractions by element, nominal specification values, expanded by OpenMC 0.15.3 to natural isotopes; the sums are
renormalised to 1 (`final_wo`). The numbers are constants of `controls/p178_ce_rates.py` (`MATERIALS`), hashed by G0
through the constants digest.

| Material | Mass fractions |
|---|---|
| Eurofer-97 | Fe 89.04 %, Cr 9.0 %, W 1.1 %, Mn 0.4 %, V 0.2 %, Ta 0.12 %, C 0.11 %, N 0.03 % |
| Inconel 718 | Ni 52.5 %, Cr 19.0 %, Fe 18.5 %, Nb 5.1 %, Mo 3.05 %, Ti 0.9 %, Al 0.5 %, Co 0.5 %, Mn 0.2 %, Si 0.2 %, Cu 0.15 % (sum 100.6 %, renormalised) |
| W-7Ni-3Fe | W 90 %, Ni 7 %, Fe 3 % |
| Copper | Cu 100 % |

## Reactions (fixed now)

| Model | Capture (MT 102) | Controls (threshold reactions) |
|---|---|---|
| E | Cr50, Mn55, Fe58, W186, W184, W182, Ta181, V51 | Fe56(n,p), Fe54(n,p), Fe54(n,a), Cr52(n,2n), Mn55(n,2n), N14(n,p) |
| I | Ni58, Ni62, Nb93, Mo98, Co59, Cr50, Fe58, Mn55, Cu63 | Ni58(n,p), Ni60(n,p), Ni58(n,2n), Nb93(n,2n), Mo92(n,p), Fe54(n,p), Ti48(n,p), Al27(n,a) |
| H | W186, W184, W182, W183, Ni62, Fe58 | W186(n,2n), W182(n,p), W183(n,p), Ni58(n,p), Ni58(n,2n), Fe54(n,p) |
| B | Cu63, Cu65 | Cu63(n,2n), Cu65(n,2n), Cu63(n,a), Cu63(n,p), Cu65(n,p) |

Rows are models × regions × reactions: 143 in all, of which 73 are capture rows (E 24, I 27, H 18, B 4) and 70 are
controls. Each rate is the reaction total over product states, as P173 defines it. A row that cannot be scored is
reported with a reason code, never dropped.

## Same data

- **ACTINV library.** `~/nuclear-data/endfb-viii.1-p178/endfb81_all_709g.npz`, rebuilt on 2026-10-31 from the P172
  staged ENDF/B-VIII.1 tapes with ACTINV 1.7.1 (which carries the ledger 316 repair), 293.6 K, 709 groups,
  flat-lethargy weighting; the same six actinides as P172's failed to build and appear in no model here.
- **Evaluation tapes for the mixture.** The raw ENDF/B-VIII.1 tapes P172 staged
  (`~/nuclear-data/endfb-viii.1-p172/stage/`), each checked against the stage manifest's SHA-256. A material nuclide
  takes its tape when the tape has one MF=2/MT=151 isotope whose first range is resolved; the others enter the mixture
  total through the analytic potential-scattering estimate (repaired in `b7cadb2`, ledger 317) and are recorded.
- **OpenMC data.** The ENDF/B-VIII.1 HDF5 library of P163, P172 and P173, at 294 K.

## Arms and normalisation

All rates per source particle, per target atom, in barn cm, as P173:

- `R_openmc`: OpenMC's continuous-energy reaction-rate tally (track length, `multiply_density = False`).
- `R_actinv = sum_g sigma_g L_g`: infinite dilution, the library's group cross sections and OpenMC's 709-group flux.
- `R_omg = sum_g sigma_g^OMC L_g`: OpenMC's own pointwise data collapsed by the harness (P173's definition).
- `R_mixture = sum_g sigma_g s_g L_g`: the rate ACTINV applies with `self_shielding.mixture`. `s_g` is the target's
  factor in the row's channel (MT 102 capture; MT 16, 103, 107 the total factor) that one `actinv run` per region
  reports in `ledger.shielding.applied_factors`. The run's material is the OpenMC model's own atom densities
  (`atom_fraction`), at 293.6 K, profile `endfb8`, its spectrum the region's OpenMC flux. The factors are the solver's;
  the harness only applies them. A group the ledger does not list has `s_g = 1`.

Log ratios, each with P173's conservative first-order standard error:

- `r = ln(R_actinv / R_openmc)`, `r_proc = ln(R_actinv / R_omg)`, `r_coll = ln(R_omg / R_openmc)` (P173's);
- `r_mix = ln(R_mixture / R_openmc)`, with `se(r_mix)² = (se(R_mixture)/R_mixture)² + (se(R_openmc)/R_openmc)²` and
  `se(R_mixture) = sum_g sigma_g s_g se(L_g)`.

## Runs

As P173 in every respect except the models, the library and the seed base (178000):

- One OpenMC fixed-source run per model, neutron transport only, batches of 10^5 particles, 4, 8, …, 256 batches,
  stopping at the first count at which every tallied rate is positive with relative standard error ≤ 0.5 %, or at 256.
- Two OpenMC threads, each run in its own bounded scope (6 GiB, no swap, 128 tasks, 200 % CPU).
- `actinv run` for the mixture arm, one per region, through the pinned binary, inside the mode's bounded scope.

## Frozen inputs

| input | SHA-256 |
|---|---|
| `~/nuclear-data/endfb-viii.1-p178/endfb81_all_709g.npz` | `a964a44ef106b692d170ba6085859ca2827ff967b3e9a6ddc60ca8fa9ac92051` |
| its index `endfb81_all_709g_index.json` | `b5a4b668324a92bd96e8e90c5feb521a79ca8838e42a517ac080652074b7f41a` |
| `~/nuclear-data/endfb-viii.1-p172/stage_manifest.json` | `d16b9233e7d8855cf9f08be3a1b5c79b945454f8f21d0f66996adc7a937f861e` |
| OpenMC ENDF/B-VIII.1 archive, `cross_sections.xml`, `fispact_709_groups.json` | as P173 |
| the `actinv` binary, `target/p178-bin/actinv` (`ACTINV_BIN_SHA256_P178`) | `f4800d0c25196721a0ababfe039fc9eb24b0d12b65af5af5ccc756280a4b8827` |

The binary (`actinv 1.7.1`) was built inside the bounded scope from crates tree
`b18274fcaa0e1e51ef9399e2d9c67543ee52e2b2`, the tree of master `f77a7e9`, which carries `self_shielding.mixture` and
the zero-K clip the rehearsal called for (see Rehearsal). It was copied to `target/p178-bin/actinv` in the main
checkout and is passed as `ACTINV_BIN`. Every scored mode refuses to run while a pin holds the placeholder or a pinned file does
not match. The tracked tree must be clean.

## Gates

- **G0:** this protocol's SHA-256 is in `protocols/protocol_hash.txt`; the record's harness, P173-harness, constants and
  mixture-constants hashes equal the files' and the harness' own.
- **G1:** pins (library, index, stage manifest, binary), OpenMC 0.15.3, a clean tracked tree and the bounded cgroup of
  every mode and OpenMC run are recorded and match. P173's collapse check (harness `R_actinv` against
  `actinv inspect xs --spectrum` to 1e-9 relative, first region of each model) passes. Every mixture run used the
  pinned binary, the pinned library and tapes whose SHA-256 match the stage manifest.
- **G2:** every listed (model, region, reaction) is scored or reported with a reason code; each scored row's `r`,
  `r_proc`, `r_coll`, `r_mix` and their standard errors recompute from the stored rates; `r = r_proc + r_coll`; for
  every row whose target has no mixture factors, `R_mixture = R_actinv` to 1e-12 relative; the batch schedule is P173's.
- **G3:** the decision rules below are computed by the checker from the record and written to
  `results/p178_verdict.json` (with `--no-write`, compared with it).
- **G4:** CI green on the evidence commit.

## Decision rules (what the result permits)

**Used rows.** A row with `R_openmc` scored at relative standard error ≤ 0.5 % and both `r` and `r_mix` scored.

**Material.** For x equal to `r` or `r_mix`, a used row is material in x when `|x| > 0.05` and `|x| > 3 se(x)`.

**Capture outcome**, over used capture rows (MT 102), decided in this order:

1. **NOT QUALIFIED** if any used capture row is material in `r_mix`.
2. **QUALIFIED** if at least one used capture row is material in `r`, and none is material in `r_mix`.
3. **NOTHING TO CORRECT** if no used capture row is material in `r` or `r_mix`.

**Controls.** The mixture must leave the threshold rows alone. The controls hold if, for every used control row,
`|r_mix − r| ≤ 0.01` and the row is not material in `r_mix` unless it is material in `r`. Controls that fail turn
QUALIFIED and NOTHING TO CORRECT into NOT QUALIFIED.

**Reported, not decided on:** per row `r_mix − r_proc` (the mixture rate with processing removed), the effective scale
`R_mixture / R_actinv`, the number of groups scaled and the smallest factor; the nuclides that entered only through the
analytic estimate; every zero-K clip the mixture runs recorded; per model the largest `|r|` and `|r_mix|` over used capture rows. A capture row that is material in
`r_mix` but not in `r_mix − r_proc` is flagged `processing_limited`; it still counts as material, and those rows go to
a data-processing review.

**Allowed public sentences, if earned.**

- QUALIFIED: "On four fusion materials held out from its development (Eurofer-97, Inconel 718, a W-Ni-Fe heavy alloy
  and copper), ACTINV's mixture self-shielding brought every listed resonance-capture rate within X% of OpenMC's
  continuous-energy rate on the same ENDF/B-VIII.1 data, where the unshielded rates differed by up to Y%." X is the
  largest `exp|r_mix| − 1` and Y the largest `exp|r| − 1` over used capture rows, in percent, rounded up to 0.1. If any
  listed capture row is not used, the checker adds a second sentence, "N of 73 listed capture rows were not scored or
  did not reach a 0.5% standard error and are not covered by that sentence."
- NOTHING TO CORRECT: "On four held-out fusion materials, ACTINV's unshielded resonance-capture rates were already
  within Y% of OpenMC's continuous-energy rates on the same data, and its mixture self-shielding kept them within X%."
  With the same second sentence.
- NOT QUALIFIED: none. With no used capture row at all the outcome is NOTHING TO CORRECT and no sentence is allowed.

No sentence may call a code more or less accurate than the other, or claim anything for materials not modelled.

**What QUALIFIED permits.** The handbook may replace "not yet qualified against measurements" in the mixture section
with the sentence above (it remains a comparison with a calculation), and a later change may make mixture shielding the
default of the OpenMC adapter. Neither follows automatically; each is its own change.

## Verdict

P178-PASS if G0–G3 hold (G4 external), whatever the outcome. One repair round, registered as an amendment first.

## Disclosures

- A comparison of two calculations, not of calculation and measurement.
- OpenMC's tally is the reference only for collapse and self-shielding; it shares the evaluation's errors.
- The narrow-resonance flux `1/Sigma_t` ignores slowing-down between resonances and spatial flux gradients; the
  per-region spectrum is OpenMC's. The question is whether that is good enough in these models, not whether it is
  exact.
- The resolved range only: the unresolved range is not shielded by the mixture mode (`s_g = 1` there), while
  OpenMC samples probability tables. Any effect from it appears in `r_mix`.
- 293.6 K against 294 K; negligible for these reactions.
- Nominal compositions; the result characterises these models, not a component.
- The decision uses the total `r_mix`, which includes processing (`r_proc`); the diagnostic separates it.

## Not claimed

- Anything for reactions, materials, geometries or spectra not modelled (a thermal or fission spectrum, a mixture of
  materials in one cell, a material with a strong absorber not listed).
- That mixture shielding is right in a model not run here, or that OpenMC's rate is the true rate.
- Anything about the Bondarenko table or geometry modes, speed, decay heat, or accuracy against measurement.

## Rehearsal

Before registration the coordinator may run the whole pipeline once with `--rehearsal` (1000 particles per batch, at
most 8 batches, outputs only under `target/p178-rehearsal*`). The checker's gate lines and the exit codes are the only
things read; the outputs are deleted afterwards. The result of the rehearsal is stated in the registration's ledger
entry.

A first rehearsal, with a binary from `d9551ba`, stopped in the mixture mode of models E, I and H: ENDF/B-VIII.1 Fe-54
has a negative MF=3 elastic background above 1 keV, and under the interference minimum below its 7.8 keV resonance the
0 K elastic went negative, which the solver refused. NJOY RECONR, which made OpenMC's library, sets such values to zero.
The solver now does the same in mixture mode and records each clip in the ledger (`f77a7e9`), and the binary above
was built from it. Only exit codes and error messages of that rehearsal were read.

## Commands (coordinator)

From the worktree, with `PYTHONDONTWRITEBYTECODE=1`, each mode inside the AGENTS.md scope (200 % CPU):

1. (Before registration: the release binary copied to `target/p178-bin/actinv`, its digest into
   `ACTINV_BIN_SHA256_P178`; `python3 controls/test_p178_ce_rates.py`; `python3 controls/check_p178.py --self-test`.)
   Every later step runs with `ACTINV_BIN` set to that copy.
2. `python3 controls/p178_ce_rates.py openmc --model E`, then `I`, `H`, `B`.
3. `python3 controls/p178_ce_rates.py actinv --model E`, then `I`, `H`, `B`.
4. `python3 controls/p178_ce_rates.py actinv-mixture --model E`, then `I`, `H`, `B`.
5. `python3 controls/p178_ce_rates.py g1-check`.
6. `python3 controls/p178_ce_rates.py assemble`, then `python3 controls/check_p178.py`.
