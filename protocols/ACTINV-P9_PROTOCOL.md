# ACTINV P9 — fission yields, coupled burn-up and pulsed histories

**Roadmap row:** P9 (first phase of the v0.5 milestone). **Opened:** after the P8/v0.2 close. **Time box:** four
calendar days. **External acts:** the principal's. This protocol covers explicit-nuclide materials, neutron-induced
independent fission yields, corrected automatic trace/coupled selection, finite piecewise-constant pulse histories,
and the promised CoNDERC/OpenMC/ALARA validation. Spontaneous-fission yields, prompt-neutron/photon transport,
criticality, changing spectral shape within one problem, cumulative-yield source terms, covariance and P10 data work
remain out of scope.

**Minimum gate input** (standing rule 7): a synthetic one-group three-nuclide fission fixture; the already-built
TENDL-2023 U-235 row and ENDF/B-VIII.0 decay file; the single official ENDF/B-VIII.0 U-235 NFPY evaluation; two
CoNDERC U-235 thermal cases (Dickens pulse and Yarnell 20,000 s); OpenMC 0.15.3; and an official ALARA 2.9.2
Fe-56(n,p)Mn-56 subset with a ten-pulse schedule. No full activation-library rebuild, all-parent NFPY audit, FNS
rerun or transport calculation is a prerequisite for a gate.

The official ENDF/B-VIII.0 NFPY archive is SHA-256
`92c5371fdb21eecf4989f48828671b904186abc6386b3d7510c8fcee2ee5ffcf`; its U-235 evaluation is
`9e1320293a544fc03f33f804a15a9e3ccc3be026552ee6dbc03b8d3e24615e41`. The CoNDERC fission archive is
`30756fef88c0f3637246bf8ad8ef1fc5397a3f784e5408f2861bc474993e74a5`. The ALARA source is official release 2.9.2
at commit `faa5b330460fe865e38fc788f1b792ea33d13d1b`. Source data remain outside the repository.

## Normative physics and wire-format choices

1. A material-composition key is either a natural element symbol or an explicit nuclide in canonical form
   `SymbolA[mN]`: for example `Fe`, `U235`, `Ba137m1`. Symbols are case-insensitive on input. Bare `m` is an alias
   for `m1`, so `Ta180m` normalizes to `Ta180m1`; two keys which normalize to the same nuclide are an error. A natural
   element and an explicit isotope of that element may not coexist in one composition because their allocation would
   be ambiguous. Malformed keys and requested nuclides absent from the decay data fail for mass-based composition;
   an `atoms_per_g` nuclide may be ledgered as absent from the solvable chain as before.
2. The three material bases retain one meaning across element and nuclide keys. `atoms_per_g` values are literal atom
   densities. `wt_percent` values are grams per 100 g. `atom_fraction` values are arbitrary atom ratios normalized to
   one gram of mixture. Natural elements use the embedded abundance/mass table. Explicit-nuclide molar masses use the
   primary/fallback decay evaluation's AWR multiplied by `1.00866491595 u` per neutron; the chosen AWR, source file
   and mass are certified. Photon-response mass fractions aggregate explicit isotopes back to their element.
3. `actinv-spec-1` gains an optional `fission_yields` object with `files`, `energy` and `fixed_energy_eV`. `files` is
   an ordered list of `{path, sha256}` references, one ENDF evaluation per parent. `energy` is either
   `spectrum_average` (default) or `fixed`; `fixed` requires one finite nonnegative `fixed_energy_eV`, while
   `spectrum_average` forbids it. Empty `files` preserves the pre-P9 no-yields path. Every declared hash is recomputed
   before parsing and included in prepared-run compatibility and the result certificate. Duplicate parent files,
   duplicate energies/products, mutation while hashing, malformed/truncated records and nonfinite or negative yields
   are hard errors.
4. The production source is ENDF-6 MF=8/MT=454 **independent** fission-product yield `Y(ZAFP,FPS,E)`. Yield
   uncertainty is retained for inspection but not propagated until P11. MF=8/MT=459 cumulative yields are parsed for
   structural/energy-grid checks and diagnostics only; they never feed the rate matrix. At each tabulated energy the
   raw independent-yield sum must satisfy `abs(sum(Y) - 2) <= 1e-6`. This fixes the roadmap's shorthand
   “nu_f-consistent” against ENDF-102: the normalization is two primary fission fragments, not prompt-neutron nubar.
   Values are not silently renormalized.
5. In `fixed` mode, the requested incident energy selects the exact yield table, clamps below/above the evaluated
   range, or linearly interpolates between its two neighbors. In `spectrum_average`, each fissioning parent uses
   `Ebar = sum_g(Erep_g sigma_f,g Phi_g) / sum_g(sigma_f,g Phi_g)`, with logarithmic-mean representative energy
   `Erep=(E_hi-E_lo)/ln(E_hi/E_lo)`, consistent with equal flux per lethargy inside a group. Products are the union of
   both bracketing tables and an absent endpoint value is zero. The effective energy, bracket, interpolation weight,
   raw/effective sums and clamp decision are certified per parent. A zero fission rate needs no invented energy.
6. The existing MT=18 loss row removes one parent atom per fission. Its `ZAP=0` product marker becomes one matrix feed
   `Y_j r_f` for every independent product. Production mapped to the decay chain plus yield-weighted leakage must
   equal the raw yield times `r_f`; a missing product is ledgered by parent/product/yield/rate. An active parent with
   no supplied NFPY retains the explicit pre-P9 `fission_no_yields_to_leakage` path rather than borrowing another
   parent's yields. Spontaneous-fission decay branches remain explicit leakage in P9.
7. Automatic mode selection uses reaction depletion, not irradiation wall time. For each initial bulk nuclide,
   `tau_i = L_i * sum_k(dt_k * flux_multiplier_k)`, where `L_i` is the base-spectrum sum of its reaction loss rates,
   and `f_i = -expm1(-tau_i)`. The ledger records the maximum optical depth, actual burn-up fraction and nuclide.
   `auto` selects `trace` only when `max(f_i) < 1e-6`, otherwise `coupled`; explicitly requested modes are always
   honored. Coupled mode evolves the complete initial material and products in one matrix. Trace mode holds initial
   bulk nuclides constant and remains the numerically preferred low-burn-up formulation.
8. The existing ordered `schedule` is the canonical arbitrary finite piecewise-constant pulse history: a positive
   multiplier is irradiation and zero is exact cooling. The multiplier scales every base reaction rate and the
   burn-up exposure; decay is always active. Results occur after every segment. Cumulative time, multiplier-weighted
   seconds and physical fluence are recorded, and adjacent equal-flux segments must be physically equivalent to their
   merged segment within solver error. No implicit averaging over an off interval and no hidden repeat/final-delay
   convention is introduced.
9. CoNDERC validation uses U-235 independent yields at the conventional thermal label 0.0253 eV and the archive's
   exact irradiation histories. The theoretical pulse is normalized as power divided by integrated fissions
   (`MeV s^-1 fission^-1`); the 20,000 s constant irradiation is normalized as power divided by its irradiation
   fission rate (`MeV per fission`). C/E is reported at every finite measured beta, gamma and total point with the
   supplied uncertainty, plus geometric-mean C/E and maximum absolute log C/E. Accuracy is reported, not converted
   into a post-hoc pass threshold. The report places ACTINV beside the official FISPACT-II UKAEA-R(18)003 curves and
   the matching ORIGEN calculations tabulated in Gauld's 2019 summary, naming any data/library differences.
10. The OpenMC comparison uses its independently parsed U-235 MT=454 yields, the same ENDF decay evaluations, the
    same fission-rate matrix and identical explicit schedules; OpenMC CRAM48 is the independent exponential action.
    The ALARA comparison uses the official 2.9.2 source, the same extracted FENDL-2 Fe-56(n,p)Mn-56 cross sections and
    decay evaluation in both codes, and ten one-second pulses separated by nine five-second gaps. Rates, initial
    inventories, schedules, code versions/commits, data hashes, normalization and numerical floors are recorded.
11. Old specifications remain valid. With no fissioning row, P9 cannot alter a pre-P9 deterministic result field.
    With fission but no yield files, the established leakage result and category remain. CLI, Python, harness and mesh
    continue through the one Rust path; prepared mesh data include the complete fission-yield configuration. P9 does
    not publish a release or claim the v0.5 milestone, which closes only after P10.

## Deliverables

1. Rust explicit-isotope material parsing/mass conversion and an ENDF MF=8/MT=454/459 NFPY reader exposed through
   `actinv-data`, with effective-yield selection and complete provenance.
2. Yield-expanded fission matrix assembly, corrected burn-up calculation/automatic selection, and per-segment
   time/exposure/fluence records through the ordinary and mesh solver paths.
3. Independent synthetic, OpenMC and ALARA controls for fission, coupled depletion and pulsed schedules.
4. CoNDERC U-235 pulse and 20,000 s C/E results with FISPACT-II/ORIGEN context, plus updated specification, method,
   data, ledger, validation and user documentation.

## Gates

**G1 Explicit composition and NFPY reader.** Rust and an independent Python/OpenMC reader agree on every U-235
MT=454/459 energy, product, isomer, yield and uncertainty to 1e-12 relative; all three independent tables satisfy the
raw two-fragment sum to 1e-6 without normalization. Explicit ground/isomer inventories under all three material bases
agree with an independent mass calculation to 1e-12. Case/`m` aliases normalize deterministically, while alias
collisions, element/isotope mixing, bad hashes, malformed keys, duplicate records, negative yields and truncation fail
closed.

**G2 Fission assembly and conservation.** On the one-group synthetic fixture, the independently assembled dense
matrix and Rust matrix agree to 1e-12: parent loss is exactly one fission rate, every product feed is `Y*r_f`, and
mapped production plus yield-weighted leakage closes to the raw source to 1e-12. Exact, interpolated and clamped
energies produce their analytic values. A missing yield product and a missing parent take their distinct declared
ledger paths; MT=459 cannot affect a result.

**G3 Coupled mode and automatic threshold.** Independent calculations reproduce every per-isotope optical depth and
burn-up fraction to 1e-12 and demonstrate that non-unit pulse multipliers change the choice. Cases immediately below
and above `1e-6` select trace and coupled respectively; explicit modes are honored. Coupled parent depletion agrees
with `N0 exp(-tau)` to 1e-10. At burn-up much less than one, trace and coupled product inventories agree within the
coupled CRAM floor and their resolvable difference is first order in burn-up.

**G4 Pulses and OpenMC.** A noncommuting production/decay fixture is checked after every on/off boundary against a
dense independent exponential and OpenMC CRAM48. Every resolvable population agrees within 1e-8 relative or the
reported numerical floor; cumulative time/exposure/fluence agree to 1e-12. Split/merged equal-flux histories agree to
1e-10, while a history with decay gaps matches its analytic value and differs from a same-fluence averaged exposure by
the independently predicted amount.

**G5 ALARA identical-data comparison.** The pinned ALARA source builds and runs its official reference pulse input.
For the extracted Fe-56(n,p)Mn-56 case, both codes independently recover the same one-group rate to 1e-12 and the same
ten-pulse/nine-gap timeline. At shutdown, every inventory above `1e-10` of the initial population agrees within
`5e-4` relative (the official ALARA text output's precision); any larger difference must be explained and the gate
fails if it is not.

**G6 CoNDERC, provenance and regression.** C/E tables cover every finite Dickens pulse and Yarnell 20,000 s point and
all available beta/gamma/total channels, report measurement uncertainties and aggregate statistics, and cite the
matching FISPACT-II and ORIGEN reference results without treating a data-library difference as code identity. Both
per-fission normalizations close independently to 1e-6. Every activation, index, decay, yield, CoNDERC and comparison
input hash independently re-matches the certificate. Workspace tests and strict Clippy pass; P5 remains P5-PASS,
P6-P8 retain their recorded conditional verdicts, and a non-fission pre-P9 result remains bit-identical.

## Verdict (`controls/check_p9.py`)

P9-PASS: G1-G6. P9-CONDITIONAL after one documented repair round on a gate. P9-FAIL otherwise. Standing rules 1-7
apply.
