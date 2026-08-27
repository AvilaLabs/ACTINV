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

EAF-2010 uses 1 for these, so a pipeline validated only against EAF passes and then loses every isomer on TENDL. The
failure is silent: the lookup misses and the production falls back to the ground state, which is a real nuclide with a
plausible half-life, so nothing errors and totals still look reasonable. In the FNS benchmark this cost 2–5 orders of
magnitude on individual experiments (Ba, Ce, Hg, Y, Rb, W) while leaving the *median* C/E almost unchanged — the median
hid it; the per-experiment spread did not.

**ACTINV:** the library builder renumbers the distinct positive `LFS` of each (MT, product) in increasing level order
onto isomeric ordinals and ledgers every remap; the solver ledgers any ground-state fallback under
`isomer_state_absent_from_decay_library_used_ground` rather than taking it silently.

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
