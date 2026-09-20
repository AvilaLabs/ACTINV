# Traps in activation data

Failure modes that produce a plausible, silently wrong inventory. Each is recorded because ACTINV hit it and a control
caught it; each names the control that now guards against it. Anyone building an activation pipeline will meet these.

## 1. `LFS` is a level index, not an isomeric-state number

MF=9/MF=10 identify a product by `ZAP` and `LFS`. `LFS` is the index of the **nuclear level** in that evaluation's level
scheme, and the numbering is library-dependent. Decay sublibraries index isomers by `LISO` = 0, 1, 2 — the isomeric
*state* ordinal. The two coincide often enough to look correct:

| product | TENDL-2023 `LFS` | decay library `LISO` |
|---|---|---|
| Ba-137m | 2 | 1 |
| Hg-199m | 7 | 1 |
| W-185m | 6 | 1 |
| Rb-86m | 2 | 1 |
| Y-90m | 2 | 1 |

The trap is deeper than product labels: a cross-section file's *own* `LISO` header field is file-order bookkeeping,
not the decay ordinal. TENDL-2017's Ta-182M file declares `LISO` = 1 for the state ENDF/B-VIII decay numbers
`LIS` = 29 / `LISO` = 2 (519.587 keV, 15.8 min), while ENDF/B-VIII assigns `LISO` = 1 to Ta-182's other isomer
(`LIS` = 1, 16.263 keV, 0.283 s). Rank-ordering labels onto ordinals lands the 15.8-minute isomer's entire
production on the 0.283-second state — it evaporates before the first cooling point, which is exactly the failure
mode the Ta-2000 FNS experiment showed before the fix.

EAF-2010 uses 1 for these, so a pipeline validated only against EAF passes and then loses every isomer on TENDL. The
failure is silent: the lookup misses and the production falls back to the ground state, which is a real nuclide with a
plausible half-life, so nothing errors and totals still look reasonable. In the FNS benchmark this cost 2–5 orders of
magnitude on individual experiments (Ba, Ce, Hg, Y, Rb, W) while leaving the *median* C/E almost unchanged — the median
hid it; the per-experiment spread did not.

**ACTINV:** the library builder resolves each product state in audited tiers: declared excitation energy
against the cross-section catalog's `ELIS` values, then the evaluator's `LFS` label against the catalog's `LIS`
index. When `build-library --decay PATH` supplies a decay sublibrary, the emitted `LFS` is that sublibrary's
`LISO` — resolved for catalog states by their own `LIS`/`ELIS` labels and for products with no catalog home by
their declared `LFS`/excitation directly. Cross-library level schemes differ: ENDF/B-VIII calls Sb-120m 151 keV /
`LIS` 4 while JEFF-3.3 and TENDL-2017 call the same 5.76-day isomer 200 keV / `LIS` 6, and several isomers differ by
0.2–0.5 keV between evaluations (Cu-68m, Cs-135m, Dy-147m, Hf-178n, Au-189m). `--decay-fallback PATH` supplies a
second sublibrary consulted when the primary misses, and a conservative loose-`ELIS` tier (±250 eV or 0.1%,
accepted only when exactly one isomer falls inside) absorbs small level-energy disagreements. Resolution order:
primary tight `ELIS`, primary `LIS`, fallback tight `ELIS`, fallback `LIS`, primary loose `ELIS`, fallback loose
`ELIS` (decisions `decay_elis_match`, `decay_lis_label_match`, `decay_fallback_elis_match`,
`decay_fallback_lis_match`, `decay_elis_loose_match`, `decay_fallback_elis_loose_match`; both library hashes are
recorded in the index). A declared level matching no decay isomer routes to ground as
`decay_no_isomer_match_to_ground` — prompt levels decay instantly, so the daughter ground state keeps the
strength. Isomer *target* identities are renumbered the same way.
One edge remains: a state that matches no decay entry cannot keep its file `LISO` if that ordinal is already
occupied by a *different* physical state in the decay sublibrary — keeping it would silently alias the nuclide onto
the wrong half-life (Tb-156's unmatched second isomer collided with the just-renumbered first). Such states emit a
synthetic ordinal `10000 + file LISO` (e.g. Tb-156N → `LISO` = 10002): outside the decay range, so the chain sees an
honest "no decay data" product rather than a wrong-state decay, and two files can never collide on one identity.
Without `--decay`, the distinct declared `LFS` of each (MT, product) rank-compresses onto isomeric ordinals
(decision `no_catalog_rank_mapped_lfs`) — a reasonable guess at the decay numbering that the `--decay` path
replaces with a lookup. Isomer states need decay data, not a cross-section file, so product-only isomers such as
Sc-50m or Ta-182n survive to feed their daughters. The solver ledgers any ground-state fallback under
`isomer_state_absent_from_decay_library_used_ground` and any decay-absent product under
`products_no_evaluated_decay_data` rather than taking either silently.

## 2. Inelastic scattering is a transmutation when it produces an isomer

(n,n′) leaves the nuclide unchanged — except when it leaves it in a metastable state, which for activation purposes is a
different nuclide with its own half-life and decay heat. Both TENDL and EAF encode this as MF=10/MT=4 partials with
`LFS` > 0. A skip list that drops MT=4 and MT=51–91 as "no transmutation" therefore loses, for example,
Y-89(n,n′)Y-89m at 0.39 b on a D–T spectrum — a dominant contributor in the first minutes after shutdown.

**ACTINV:** inelastic MTs are kept when they carry `LFS` > 0 partials; the ground-state loss is set to the sum of those
partials, never to the total inelastic cross section.

## 3. Photon heating attributed to secondary charged particles

In some Monte Carlo builds a `heating` score filtered to photons records only a small residual, because energy deposited
by photon-produced electrons and positrons is attributed to those particle types. Physical photon heating is the sum
over photons, electrons and positrons (or total minus neutron). Verified by energy conservation; the residual estimator
was low by ~10⁴.

**ACTINV:** decay heat is computed from λ N Ē with the mean light, electromagnetic and heavy energies of the decay
sublibrary, and the evaluator is checked against a hand calculation and against two independent outputs of a reference
code.

## 4. Resolved-resonance sampling that resolves the wrong scale

A grid that resolves Γ is not enough after Doppler broadening, and a grid that resolves Γ_D is not enough to capture the
0 K peak's area. At 100 keV a 0.5 eV resonance sits under a 14 eV Doppler width; sampling either scale alone gives group
values wrong by several per cent. Resonances at or just beyond a range boundary, and the step where the resolved range
meets the file's background, need explicit points on both sides.

**ACTINV:** each resonance is sampled at both scales, boundary resonances within ±200 widths are included, the range
edges are explicit grid points, broadening extends past the boundary before splicing, and a convergence control compares
group values between two grid densities on a seeded sample; targets that do not converge are flagged in the library
index and the flag propagates into every run's ledger.

## 5. Zero-length segments at discontinuities

ENDF files carry double points (two entries at the same energy) where a cross section steps. A group integral or a
broadening kernel that divides by ΔE returns NaN, which then propagates into every downstream total.

**ACTINV:** zero-length segments carry no weight in the group integral and in the broadening kernel.

## 6. Fission and unmapped products

Fission on actinide targets produces a yield distribution, not a single residual; a pipeline without yields must not
silently attribute it to anything. Reactions whose residual cannot be determined from MF=8 or MT arithmetic must not be
guessed.

**ACTINV:** both go to an explicit leakage state with their own ledger categories
(`fission_no_yields_to_leakage`, `products_unmapped_to_leakage`), reported with their rates at every step.

## 7. Reported total widths can be rounded away from their components

Some LRF=1/2 evaluations print `GT` at lower effective precision than `GN+GG+GF`. Using the rounded total for grid
placement while a reference processor uses the component sum shifts an ultra-narrow certificate enough to look like a
physics disagreement.

**ACTINV:** following NJOY semantics, `LRX=0` uses `GN+GG+GF`; other cases use the larger of `GT` and that sum. The
Fr-226 control independently derives the same width and directly integrates all 52 analytic lines.

## 8. Charged-particle MT=5 production lives in MF=6 yields

Above 30 MeV, TENDL charged-particle s30 evaluations can replace explicit channel production with aggregate MF=3/MT=5
times MF=6 residual yields. Treating MT=5 as an ordinary one-residual reaction loses products and isomers while still
producing plausible total loss.

**ACTINV:** it structurally consumes the MF=6 LAW body, multiplies every matching MF=8/LMF=6 residual yield by the
MT=5 cross section, retains multiple residuals, and fails on missing/conflicting declarations. Official TENDL-2025
residual tables independently control proton, deuteron and alpha cases.

## 9. A safety iteration cap is not a convergence tolerance

Adaptive linearization can satisfy its unchanged error criterion only after more refinement rounds than a convenient
default, especially at a deep seeded kink. Raising the tolerance or cloning an ever-larger grid would hide the actual
failure mode.

**ACTINV:** the TENDL-2025 neutron corpus was scanned before fixing the bound. Co-58 MT=102 was the unique maximum at
pass index 19, so the cap is 20 while the `2e-4` midpoint tolerance and ten-million-point memory bound remain
unchanged. A source-independent depth-19 regression and the hash-pinned Co-58 control prevent either bound drifting.

## 10. Charged-particle MT=4 is a transmutation channel, not same-nuclide inelastic

In neutron evaluations MT=4 is (n,n′) on the same nuclide — all 1,081 TENDL-2025 neutron files with an MF=8
MT=4 section declare the target's own ZAP. In charged-particle evaluations the same MT=4 carries *neutron
emission*: every one of the 2,156 TENDL-2025 proton, deuteron and alpha files with the section declares a
different-residual ZAP (712 alpha, 757 deuteron, 687 proton). `181Ta(α,n)184Re` lists its Re-184 states under
MT=4. A pipeline that applies the neutron convention — residual = target — credits the channel's production to
the wrong nuclide. The failure is silent: totals still look plausible, and the measured product (Re-184) simply
scores zero.

**ACTINV:** the builder dispatches on the declared product ZAP per projectile — charged-particle inelastic emits
the residual's states and a channel-total loss row; rebuilt artifacts declare `emission_model` so the scorer can
tell the two models apart. Corpus-wide adjudication is hash-pinned in `results/g2_p25_traces.json`
(`inelastic_adjudication`).

## 11. The 10⁻²⁰-barn floor convention fakes conservation violations

TENDL prints `1e-20` barn as an effectively-zero floor for both MF=3 totals and MF=10 state partials. A file can
declare two co-equal `1e-20` states under a `1e-20` total — a 100% relative excess with zero physical weight
(`d-Ag104m`). A conservation audit that compares emitted sums to declared totals in purely relative terms reads
this as a violation. 395 of the 397 P18b-quarantined evaluations carry a floor-kind excess; 227 fail *only* on
it — the dominant quarantine cause was a printing convention, not broken physics.

**ACTINV:** emitted-sum excesses below the absolute floor bound (`1e-15` b) are accepted without scaling and
ledgered `floor_reconciled`; anything above it is evaluated on mechanism.

## 12. Emitted partials can contradict the declared total — genuinely

Beyond conventions, TENDL-2025 contains declared-value contradictions no downstream repair can absorb. In the
quarantined population, 62 evaluations carry barn-scale contradictions — `n-Fe053m` declares 1.08×10⁵ b of
Fe-52 production under an MT=16 total of exactly zero at 7.79 MeV, `n-Zr088` 70 b, `n-Y088` 26 b,
`n-Lu174m` 9.5 b at 0.4 eV — while 23 carry only micro-to-femtobarn inconsistencies (10⁻⁶–10⁻¹⁵ b; formally
contradictory, probably grid-generation artifacts) and 10 show between-gridpoint relative excesses. A further
25 neutron files looked like grid-density interpolation artifacts, but their worst excesses run 0.11×–18.6×
the total — every one beyond the 0.03 proven-mechanism envelope, so zero were reconcilable.

**ACTINV:** the exact-decimal oracle classifies each file per (MT, ZAP) into floor / interpolation-artifact /
missing-total / genuine classes; genuine-source files fail closed and their residual defects are carried in the
emitted index as audit-ledgered source diagnostics, never silently reconciled. Classification is hash-pinned in
`results/g1_p25_census.json` and `results/g2_p25_traces.json`.

## 13. MT=18 fission partials ship without an MF=3 total

50 quarantined evaluations (7 alpha, 17 deuteron, 9 neutron, 17 proton) carry MF=10 fission partials under MT=18
with no MF=3/MT=18 total section — a conservation audit expecting the comparator finds nothing. The permitted
comparator is the `IZAP=-1` total-fission sentinel where present, else partial-sum self-consistency.

**ACTINV:** the builder resolves the comparator from the sentinel and ledgers `sentinel supplies the permitted
runtime comparator` (79 evaluations) or `missing_total_self_comparator`; a file with neither fails closed.

## 14. MF=8 declares a product state twice — ELFS and QM−QI can disagree

MF=8 identifies an emitted state by both `ELFS` (the evaluated excitation energy) and `QM`−`QI` (the excitation
implied by the evaluation's level-scheme bookkeeping). The two declarations can conflict: 14 evaluations in the
P25 corpora carry a bounded ELFS-vs-QM−QI disagreement beyond rounding tolerance. Picking either silently
relocates the state.

**ACTINV:** within a 1 keV bound the evaluated ELFS is authoritative and the resolution is ledgered
`elfs_qm_qi_conflict_resolved`; a larger conflict fails closed as a state-identity defect.

## 15. Quarantining a metastable-target evaluation silently breaks downstream families

The isomeric-state catalog is populated from each evaluation's own MF=1 header — including *metastable-target*
evaluations such as `n-Ge075m.tendl`. When such a file fails construction, every family needing its declared
states loses catalog identity: the product is emitted (ZAP and excitation present) but no catalog state matches,
so otherwise-healthy evaluations score `build_failed`. In P25, 35 quarantined metastable-target evaluations
cascaded into 38 staged-built families holding 434 ledger rows — secondary casualties of another file's defect.

**ACTINV:** the census maps each staged-built family to the catalog-supplier file it needs
(`catalog_cascade` in `results/g2_p25_traces.json`), so cascade losses are named rather than lumped with
construction failures of the family's own file.
