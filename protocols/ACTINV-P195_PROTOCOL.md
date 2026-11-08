# ACTINV-P195 — `actinv unfold`: the neutron spectrum from measured foil activities and reaction rates

## Purpose

A foil pack placed in an unknown neutron field records the field in its activities. Spectrum unfolding turns those
activities back into the group fluxes that made them, starting from a prior spectrum. Codes that do this today
(STAYSL-PNNL, SAND-II, GRAVEL, MAXED) expect activities already reduced to saturated reaction rates, and treat the
reduction (irradiation history, decay, burn-up, multi-step production) as the user's job.

P195 adds `actinv unfold`. It accepts two kinds of measurement:

1. **Foil activities** (Bq g⁻¹ of foil at the end of a schedule step). The model is a full ACTINV solve of the foil
   over the declared irradiation and cooling history, so decay during irradiation, pulsing, burn-up, multi-step and
   isomer production are part of the model rather than a pre-processing step.
2. **Reaction rates** (reactions per target atom per second at the spectrum as declared), for data already reduced.
   The model is the library's group cross section of one target and reaction, folded with the spectrum.

The estimate is a generalized least-squares adjustment of the logarithm of the group fluxes, with the prior's
covariance, the measurements' uncertainties and, when the problem names a covariance sidecar, the library's MF=33
cross-section covariance carried into the measurement covariance. The posterior comes with its covariance, a
consistency test, and the energy range each measurement responds to.

P55 left spectrum-shape unfolding out of scope ("parked"); the roadmap's 2026-09-24 scope line left it to STAYSL-PNNL.
The owner asked for it on 2026-11-01 and confirmed it on 2026-11-08. This protocol supersedes both lines for this
command only.

## Disclosure of what has been seen

- IRDFF-II's spectrum-averaged cross sections in the Cf-252 spontaneous-fission field (Tables 18 and 19, 70 rows)
  and the U-235 thermal-fission field (Table 20, 54 rows) were scored by P17 and P194 with TENDL-2025, against the
  IRDFF-II reference spectra: values, ratios and the TENDL-2025 production mappings are in
  `results/g4_p17_diagnostics.json`. No unfold, prior or leave-one-out prediction on them has been computed.
- The Cf-252 reference spectrum is an evaluation that itself used spectrum-averaged cross-section measurements. A
  posterior that moves toward it is therefore not independent evidence, and no gate rests on it.
- Development used only unit-test fixtures and one timing run of a nickel foil in the FNS iron spectrum with the
  existing P93 flux channel (one flux-channel solve 5.5 s, with MF=33 14 s; the 709 group derivatives summed to
  the activity to 2e-10), and smoke runs of the finished command on hand-set values (a nickel and an aluminium foil
  and three reaction rates in the FNS iron spectrum, with and without the covariance sidecar) to check that it
  converges and emits every field. Two rehearsals of the harness (`controls/p195.py rehearse`: seed 9195, two
  truths, two leave-one-out rows per G4 arm) were read for exit status, gate status and counts only, and their
  outputs deleted; the first showed that the Cf-252 arms need more than 8 outer iterations (the default became 20),
  and its console summary lines showed the χ² of the two U-235 full-data unfolds. No G3 case with seed 195 and no G4
  prediction has been read.

## The command

```
actinv unfold PROBLEM.json MEASUREMENTS.json [OUT.json]
```

**PROBLEM.json** is an `actinv-spec-1` neutron problem. Its spectrum is the prior φ⁰ (on the library's 709-group
structure; `descending` and `total` are honoured, and the prior is converted to absolute ascending group fluxes);
its schedule is the foils' irradiation and cooling history; its material is the default foil material; its library
supplies the cross sections. Refused: other projectiles, `spectrum.rebin`, `spectrum.relative_error`, step spectra,
feed or removal, power-driven, wall-loading or partition steps, and an `uncertainty` block holding anything but
`covariance` and `confidence_level` (unfold sets the responses and channels itself). Reaction-rate measurements are
refused together with `self_shielding`, because their model is the infinitely dilute group cross section.

**MEASUREMENTS.json** (`actinv-unfold-input-1`) holds:

- `prior`: `relative_uncertainty` (one number, or 709 numbers in the spectrum's order), the standard deviation of
  ln φ_g; and `correlation`, either `{"kind": "diagonal"}` or `{"kind": "lethargy_exponential", "length": L}` with
  ρ_gh = exp(−|u_g − u_h| / L) and u the logarithm of the group's geometric-mean energy. Groups with zero prior flux
  or zero uncertainty are held fixed and reported as such.
- `foils`: each with an `id`, an optional `material` (the spec's material schema; default the problem's), and
  `measurements` of `{nuclide, step, activity_Bq_per_g, sigma_Bq_per_g, model_relative_uncertainty?}`.
- `reaction_rates`: each `{id, target, mt, product?, rate_per_atom_s, sigma_per_atom_s,
  model_relative_uncertainty?}`. Without `product` the reaction's total row is used; with it, the production row of
  that product state.
- optional `report_bands_eV` (default 1e-5, 0.5, 1e5, 1e6, 1e7 eV and the structure's top; each edge snapped to
  the nearest group boundary in ln E), `emit_covariance` (default false), and `iterations` (defaults: 20 outer, 100
  inner, inner tolerance 1e-10 and outer tolerance 1e-9 on the largest Gauss–Newton step in ln φ).

Every measurement must declare its σ > 0 and a positive value. A measurement the prior does not produce is refused.

## Method

Let x = ln φ over the adjustable groups, x⁰ the prior, P⁰ = D R D its covariance (D = diag of the declared
uncertainties, R the declared correlation). For measurement i, y_i = ln of the measured value.

- **Model at a point φᵏ.** Each foil is solved once at φᵏ with the P93 flux channel, giving each measured
  activity m_i and its derivatives s_ig = ∂m_i/∂ln φ_g. A reaction rate gives m_i = 10⁻²⁴ Σ_g σ_ig φᵏ_g and
  s_ig = 10⁻²⁴ σ_ig φᵏ_g from the library (σ in barns, φ in cm⁻² s⁻¹). The local model is f_i(x) = ln(m_i + Σ_g s_ig (exp(x_g − xᵏ_g) − 1)), exact for any measurement
  linear in φ (every reaction rate, and every activity in ACTINV's trace mode). J_ig = s_ig / m_i at the point.
- **Measurement covariance.** C = diag((σ_i/value_i)² + u_i²) + C_xs, where u_i is the declared model uncertainty
  and, when the problem names a covariance sidecar, C_xs[i,j] = G_iᵀ Σ G_j / (m_i m_j): G_i is the measurement's
  derivative with respect to each library row's spectrum-collapsed cross section (an activity's MF=33 sensitivities
  from the same solve; Φᵏ at its row for a reaction rate) and Σ the sidecar collapsed at φᵏ, as in P55. Diagonal
  entries must equal each response's own MF=33 variance.
- **Inner solve.** Minimise (y − f(x))ᵀ C⁻¹ (y − f(x)) + (x − x⁰)ᵀ P⁰⁻¹ (x − x⁰) by damped Newton in the whitened
  coordinates u = L⁻¹ (x − x⁰), P⁰ = L Lᵀ, where the prior term is uᵀu and P⁰ is never inverted. The Hessian is the
  local model's exact one (f_i is the logarithm of a positive sum of exponentials, so ∂²f_i/∂x_a∂x_b = δ_ab J_ia −
  J_ia J_ib); Gauss–Newton, which drops that term, was found in development not to converge when the data contradict
  the prior by many standard deviations. A step is refused, and the damping raised, when the shifted Hessian is not
  positive definite or the objective rises by more than 1e-12 of its value (rounding); convergence needs an undamped
  step of at most the inner tolerance in every ln φ_g.
- **Outer loop.** Solve every foil at the inner solution and rebuild the local model there. The point is accepted
  when the next inner solve moves no ln φ_g by more than the outer tolerance; the posterior is then evaluated at the
  accepted point, where f(x) is the solved value itself. A trace-mode problem therefore costs two solves per foil
  after the prior's; a coupled (burn-up) problem iterates, and so does any problem with a covariance sidecar, whose C_xs
  moves with φ (in the rehearsal about tenfold per outer iteration).
- **Posterior.** P = P⁰ − P⁰ Jᵀ (J P⁰ Jᵀ + C)⁻¹ J P⁰, with J and C from the accepted point. Each band flux
  Φ_b = Σ_{g∈b} φ_g gets Var = Σ_{g,h∈b} φ_g φ_h P_gh, and its interval is Φ_b ± z σ at the problem's confidence
  level (default 0.95).
- **Reported per measurement:** measured, prior and posterior predictions; prior pull
  (y − f(x⁰))/√(C_ii + (J⁰P⁰J⁰ᵀ)_ii) and posterior pull (y − f(x))/√(C_ii − (JPJᵀ)_ii); its MF=33 relative
  uncertainty and coverage; and the energies below which 5%, 50% and 95% of its posterior response lies (the upper
  bound of the group where the ascending cumulative fraction of s_ig first reaches each level).
- **Reported per group:** prior and posterior flux, both standard deviations of ln φ, and the resolution
  1 − P_gg / P⁰_gg. **Overall:** χ² (the objective at the solution) with m degrees of freedom, iteration counts, solve
  count and wall time.

## Gates

- **G0.** The SHA-256 of this protocol, `controls/p195.py` (the harness) and `controls/check_p195.py` (the
  checker) are in `protocols/protocol_hash.txt`, added by the registration commit before any G3 or G4 run. The G3
  and G4 inputs are generated by the harness from seeds and pinned files.
- **G1. Implementation.** `cargo fmt --check`, `cargo check --workspace --tests` and
  `cargo clippy --workspace --all-targets -- -D warnings` are clean and the workspace tests pass on the candidate.
  Provenance: the record is made from the candidate commit with a clean tracked tree, names the binary's SHA-256 and
  every pinned input's (library, its index, the covariance sidecar, the IRDFF-II spectrum archive,
  `examples/fns_fe_5min.json`, `results/g4_p17_diagnostics.json`, `controls/p17_irdff.py`), and
  `results/p195_g1.json` records the exit status of each cargo gate above on the same commit and binary.
  Unit tests cover: the prior covariance kernels; the local model being exact for a linear response; the inner solve
  against a closed-form linear-Gaussian case; descent on a nonlinear case; convergence to a tight tolerance past the
  point where the objective is flat to rounding; convergence on data that contradict the prior; band snapping; the
  response-window rule; and each refusal below.
- **G2. Exactness (checker, on the G3 cases and the G4 full-data arms).** From the emitted record and its inputs,
  independently of the Rust code path:
  - every reaction-rate prediction and J row, recomputed from the library npz, within 1e-10 relative;
  - for every activity of the first G3 case, at its accepted point, six groups chosen per measurement (the three
    largest |J_ig| and three drawn with seed 195 and the measurement's id among the rest with s_ig ≠ 0), each solved
    with ln φ_g moved by ±1e-3:
    (m⁺ − m⁻)/(e^δ − e^−δ) equals s_ig within 1e-4 relative or 1e-9 m_i absolute;
  - stationarity at the solution, Jᵀ C⁻¹ (y − f(x)) = P⁰⁻¹ (x − x⁰), with P⁰⁻¹ formed in group space, to 1e-6 of the
    largest term;
  - the posterior covariance equal to (Jᵀ C⁻¹ J + P⁰⁻¹)⁻¹ computed in group space, compared on every emitted
    standard deviation and band covariance, within 1e-6 relative;
  - C_xs recomputed with the Python port of the sparse joint collapse (`build_joint_covariance` in
    `controls/check_g5_p53.py`) at the accepted point, within 1e-9 of its largest entry, and each prior measurement
    variance's MF=33 part at the prior point within 1e-9 relative, on every G4 full-data arm;
  - χ², pulls, band fluxes, intervals, resolutions and response windows recomputed within 1e-9 relative;
  - twelve planted mutations of emitted fields, one per field class, each detected.
- **G3. Calibration on synthetic foils (gate).**
  - Prior: the FNS iron spectrum of `examples/fns_fe_5min.json` (1.116e10 cm⁻² s⁻¹); relative uncertainty 0.30 on
    every group with positive flux; `lethargy_exponential` correlation with L = 1.0.
  - Foils, 1 g of the natural element, TENDL-2025 catalog library and decay data, irradiated 1 h at multiplier 1
    and measured after 1 h of cooling: Au (Au198), Co (Co60), Sc (Sc46), In (In115m, In116m), Ni (Co58, Ni57),
    Fe (Mn54, Mn56), Al (Na24), Ti (Sc46, Sc47, Sc48), Nb (Nb92m), Zr (Zr89): 16 measurements on 10 foils. Any of
    these the prior does not produce is dropped by the harness before the first truth is drawn, and the drop is
    recorded.
  - Truths: 40 spectra x = x⁰ + chol(P⁰) ξ, ξ standard normal, seed 195. Each truth's activities come from a plain
    ACTINV run at the truth, multiplied by exp(0.03 ε), ε standard normal from the same seed, declared
    σ = 0.03 × the measured value, no model uncertainty and no covariance sidecar (the truth uses the same cross
    sections).
  - Pass if all of: every case converges within the iteration limits; over the (truth, band) pairs of the default
    bands with positive prior flux, the fraction with |Φ_post − Φ_true| ≤ 1.96 σ_post lies in [0.90, 0.99] and the
    fraction within 1 σ_post lies in [0.58, 0.78]; and the root-mean-square of ln(Φ_post/Φ_true) over those pairs is
    below that of ln(Φ_prior/Φ_true).
  - Reported: the same statistics per band, the distribution of χ²/m, and wall time per unfold and per solve.
  - The truth and the unfold share one forward model, so G3 tests the inversion and its band, not the cross
    sections.
- **G4. Measured reaction rates (reported, not a gate).**
  - Fields and data: IRDFF-II Cf-252 (Tables 18–19) and U-235 (Table 20), the rows P17 scored with a TENDL-2025
    production mapping. Each becomes a reaction rate on that mapping's library row, rate = measured σ (mb) × 1e-27 ×
    the prior's total flux, σ from the published percentage; correlations between published values are not
    available and are taken as zero.
  - Priors, each normalised to a total flux of 1 cm⁻² s⁻¹ on the 709 groups (IRDFF-II spectra rebinned as in P17):
    (a) the other field's IRDFF-II spectrum; (b) a Maxwellian E exp(−E/kT) with kT = 1.2 MeV for Cf-252 and 1.5 MeV
    for U-235. Relative uncertainty 0.30, L = 1.0, as in G3. The TENDL-2025 covariance sidecar is propagated.
  - Four arms (two fields × two priors). Each arm: one unfold on all rows, and one leave-one-out unfold per row,
    predicting the left-out row. Per arm, median |ln(C/E)| of the prior's predictions and of the leave-one-out
    predictions, a paired two-sided Wilcoxon test, and a label: UNFOLD CLOSER, PRIOR CLOSER or NO SIGNIFICANT
    DIFFERENCE (p < 0.05). For context, the median |ln(C/E)| with the IRDFF-II reference spectrum and the full-data
    posterior's band fluxes against the reference spectrum's, as z-scores.
- **G5. Determinism.** The first G3 case and the Cf-252 (a) full-data arm, each run twice, are byte-identical apart
  from the wall-time fields.
- **G6. Refusals.** Each refusal named under the command exits 2 with its message, as do: a measurement with no σ,
  a non-positive value, a nuclide the prior does not produce, an unknown target or MT, a `product` with no production
  row, a correlation length ≤ 0, and every group held fixed. The checker requires each case's message to contain the
  word that names its cause (listed in `controls/check_p195.py`); a case the problem-spec validator refuses first,
  with exit 2 and a message naming the same field, also passes.
- **G7.** CI green on the evidence commit.

**P195-PASS** if G0, G1, G2, G3, G5, G6 and G7 hold. G4 is always reported and never decides the verdict. One repair
round, registered as an amendment before its run.

## Allowed sentences

On P195-PASS, filled by `controls/check_p195.py` from the record, and quoted together:

1. "`actinv unfold` estimates a neutron spectrum from foil activities, modelled with a full ACTINV solve of each
   foil's irradiation history, and from reaction rates, carrying the library's cross-section covariance into the
   fit."
2. "On {n} synthetic spectra drawn from its prior, {c95}% of the true band fluxes fell inside its 95% intervals and
   {c68}% inside one standard deviation, and its band fluxes were closer to the truth than the prior's (root-mean-
   square log error {e_post} against {e_prior})."
3. "On IRDFF-II's measured spectrum-averaged cross sections in the Cf-252 and U-235 fission fields, predicting each
   reaction left out of the fit, the median |ln(C/E)| was {per arm: prior → unfold, label, p}; the Cf-252 reference
   spectrum used similar measurements in its evaluation."

No sentence may compare `actinv unfold` with STAYSL-PNNL or another code, claim a facility measurement of foil
activities (none was used), or describe the G3 cases as experiments.

## Not claimed

- Unfolding is under-determined: the posterior's shape outside the energies the measurements respond to is the
  prior's, and the band intervals are only as right as the declared prior covariance.
- The linearised-Gaussian posterior is exact only for a linear model; in ln φ it is an approximation whose quality
  G3 measures at a 30% prior.
- Self-shielding and flux perturbation by the foils, cover corrections and counting-geometry corrections are the
  user's inputs (through the foil material, the problem's self-shielding options, or the declared model
  uncertainty), not something P195 qualifies.

## Commands (coordinator)

Inside the 6 GiB AGENTS.md scope, from a worktree at the evidence candidate with the catalog data installed:

- `python3 controls/p195.py run --actinv-bin CANDIDATE_BINARY`;
- `python3 controls/check_p195.py`.
