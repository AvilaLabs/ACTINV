# ACTINV-P19 protocol — finite-dilution self-shielding

Status: frozen at opening commit. Amendments land as separate files named
`ACTINV-P19_AMENDMENT_*.md`; this file is never edited after opening.

## Opening context

Maintainer direction (2026-09-10): after P23 closed (`P23-PASS`), proceed to the
highest-value remaining competitive gap — finite-dilution self-shielding, the
roadmap P19 item ("explicit finite-dilution treatment from lawful open
probability-table or independently processed data, while preserving infinite
dilution as an explicit mode and recording all model/data choices. Initially
opt-in."). P18b remains open and dormant; P19 is independent of P18b evidence
and proceeds under the same explicit-ordering direction that scheduled P23.

The chosen route is *independently processed data*: ACTINV computes Bondarenko
self-shielding factors directly from ENDF-6 MF=2 LRU=2 unresolved-resonance
statistics with its own deterministic processor, rather than consuming a
third-party probability-table file. NJOY2016.79 is the independent processing
oracle; it is used only by controls, never by the production path.

## Design contract

### Data plane — `actinv build-shielding`

A new CLI subcommand and `actinv-data` builder transform one or more ENDF-6
evaluation files into a hash-pinned shielding table artifact with schema
`actinv-shield-table-1`.

For each evaluation carrying MF=2 LRU=2 unresolved ranges, the builder:

1. Reads the unresolved parameter blocks (LRF=1 or LRF=2; energy-dependent
   parameters interpolated on their declared grids), the channel radius rule
   (NAPS), spin, and scattering radius, following the NJOY2016 PURR
   formulation (`unresx`): penetrability factor `V_l`, phase shift `phi_l`,
   penetrability-weighted mean neutron width `G_n·V_l·sqrt(E)·AMUN`, spin
   statistical factor `g_J`, and the mean level spacing `D`.
2. Reproduces the ladder ensemble *deterministically*: resonance energies by
   Wigner spacing `E_i = E_{i-1} + D·sqrt(4/pi)·sqrt(-log u_i)` and partial
   widths by Porter-Thomas chi-square draws, where every draw `u` is a fixed
   stratified quantile sequence `(i+1/2)/N` over a declared sample count `N`
   — no RNG is used. The energy observation grid is a fixed uniform grid over
   the estimator window defined by PURR's `unrest` layout.
3. Accumulates total, elastic, fission and capture cross sections at each grid
   energy with the Voigt single-level profile (Doppler width from the declared
   temperature via `sqrt(kT·E/(A·m_n))`), using the same piecewise
   approximations and the same interference (imaginary-part) terms as PURR.
4. Renormalizes the ladder-mean cross sections to the analytic
   infinite-dilution fluctuation integrals (PURR `gnrx` / MC2-2 10-point
   quadrature), exactly as PURR's `nmode=1` path, so stratification bias cannot
   shift the mean.
5. Accumulates the Bondarenko moments
   `bval_x(σ0,T) = <σ_x·w>` and `bval_w = <w>` with `w = σ0/(σ0+σ_t)` and
   emits shielded cross sections `σ_x(σ0,T) = bval_x/bval_w` per group.
6. Collapses to the library group structure, preserving the unresolved-range
   overlap fraction per group so partially covered groups can be treated
   honestly at run time.

Emitted grids: σ0 = {1e10, 1e5, 1e4, 1e3, 1e2, 1e1, 3, 1, 0.3, 0.1} barn
(the FENDL/PURR convention) and T = {293.6, 600, 900, 1200} K, interpolated at
run time in `sqrt(T)`. The table records for each nuclide the unresolved
ranges used, the evaluation file hashes, the grid axes, per-(group, MT)
factor rows `f = σ_x(σ0,T)/σ_x(∞,T)`, the infinite-dilution analytic values,
coverage (nuclides lacking LRU=2 blocks are named absent), and a strict
deterministic sample count N. Missing or malformed MF=2 data fails visibly.

### Run plane — `self_shielding` spec section

Optional section on `actinv-spec-1` (and `actinv-mesh-spec-1`):

```json
"self_shielding": {
  "table": {"path": "...", "sha256": "64-hex"},
  "dilution": "composition",
  "sigma0_b": null
}
```

- `dilution: "composition"` (default) computes per-nuclide effective dilution
  `σ0_i = Σ_{j≠i} n_j·σ_p,j / n_i` over the declared material composition,
  using the potential-scattering cross sections `σ_p,j = 4πa_j²` recorded in
  the table. `dilution: "fixed"` applies the user `sigma0_b` to every covered
  nuclide and reports it as such.
- At run time the collapsed group rate for each covered nuclide×reaction is
  multiplied by the tabulated factor `f(σ0_eff, T_spec)` over the group's
  unresolved-range overlap fraction: `σ_eff = σ_group·((1−c) + c·f)` where `c`
  is the fraction of the group inside the unresolved range recorded by the
  table. Outside every unresolved range `f≡1`.
- Absent `self_shielding` section: byte-identical output. Present section with
  a nuclide absent from the table: the nuclide is named in the ledger as
  `shielding_uncovered`, its rates are unmodified, and
  `options.require_shielding_complete` (default false) fails closed.
- The certificate records the table hash, per-nuclide effective σ0 and applied
  group factors, the dilution mode, and method limits. The ledger records a
  `shielding` block analogous to `damage`.

### Identity and compatibility

- A spec without `self_shielding` produces byte-identical results to v1.0.1
  (the P23 identity baseline carries forward).
- `build-shielding` never modifies `build-library` output; the shielding table
  is a separate sidecar artifact.
- Mesh mode applies the section per cell; CLI, Python and mesh surfaces agree
  on identical inputs.
- The feature is opt-in and documented as unresolved-region shielding only:
  resolved-resonance pointwise shielding and energy-dependent (σ0 across
  groups) transport effects are out of scope and named as limits.

## Oracles and controls

G3 requires an independent processing path (the roadmap's "selected rates
match an independent open processing path"):

- **NJOY2016.79 PURR** (local binary) on TENDL-2025 evaluations for the test
  set {W-186, Ag-107, Ta-181, Nb-93, U-238, Fe-56}. The control generates
  PENDF MT=152 Bondarenko moments at the declared σ0,T grid and compares
  ACTINV's factors within a per-material tolerance frozen in the control
  (expected ≲5–10% at 64+ ladders; NJOY MC noise is measured, not assumed).
- **NJOY GROUPR** on the same PENDFs for the FISPACT-709 group structure,
  comparing shielded group cross sections directly.
- **FENDL-3.2c GENDF** dilution-block data (local `fendl-3.2c/group/`) as an
  independent-source magnitude sanity check — different evaluation, same
  physics.
- **Analytic limits**: σ0→∞ reproduces the unshielded collapse exactly;
  monotonicity of f in σ0; temperature monotonicity for the Doppler-dominant
  materials; a nuclide with no LRU=2 block reports factor 1 and uncovered.
- **Held-out resonance case**: pure-W and pure-Ta-181 fast-spectrum problems
  run shielded vs unshielded must produce the documented rate changes (f<1
  inside the unresolved range); the same problems at fixed σ0=1e10 reproduce
  baseline bytes.

### Core campaign integration

The oracle-comparison campaign additionally runs as an Avila Core case
(`controls/p19_core/`): the contract declares the comparison requirements
(per-material tolerances on the declared grid), capabilities wrap the NJOY
runner, the ACTINV table builder and the comparator, and `avila-core run`
produces receipts and four-state requirement verdicts per attempt. The Core
case is the development feedback loop — implementation iterations re-run the
case and accumulate identity-bound attempts in the campaign log. Core evidence
complements, and does not replace, the `controls/` gate batteries; both are
required for closure.

## Gates

- **G0** — protocol freeze (this file hashed at opening), identity baseline
  recorded at the parent commit, oracle feasibility demonstrated (NJOY PURR
  produces MT=152 on TENDL-2025 W-186 — probe already run), unresolved
  coverage inventory across the test set, Core case package compiles.
- **G1** — `build-shielding`: parser, deterministic factor computation, table
  schema, hash-pinning, coverage reporting. Independent control re-derives at
  least one nuclide×group factor from the ENDF file itself.
- **G2** — `self_shielding` runtime: σ0 application, composition dilution,
  overlap fractions, ledger/certificate, spec+mesh+Python surfaces,
  absent-section byte identity.
- **G3** — oracle battery: NJOY factor + group comparisons, FENDL sanity,
  analytic limits, held-out rate changes, mesh parity, Core campaign verdict.
- **G4** — documentation (SPEC, METHOD, DATA, examples incl. a shielding
  walkthrough), performance ≤1.05× on absent-feature workloads, table build
  cost recorded.
- **G5** — independent closure checker (no production imports), verdict,
  session record, manifest, commit/push.

## Explicit non-claims

- Resolved-resonance-region self-shielding, probability-table transport,
  subgroup methods, and heterogeneous/escape corrections are not delivered.
- Shielding factors apply to collapsed group rates within unresolved ranges;
  no claim is made for energies outside the evaluated unresolved blocks.
- FISPACT's internal shielding implementation remains uninspected; NJOY is the
  named independent path.
