# P148 — A proven rate-pruning bound and certified response bounds

Registered 2026-10-19, before any source change for this phase. Owner direction 2026-10-19 (JRNC revision, reviewer
comment 3): replace the rate-significance estimate with a bound that is proven, and report what pruning can change
in activity and decay heat.

## Defect being repaired

`prune::reachable_values` (`crates/actinv-core/src/prune.rs:56-138`, unchanged since v1.0.0 in this respect)
propagates `b_i = max_j b_j · c_ji` over a worklist. Where several parents feed one nuclide it keeps the largest
single contribution instead of their sum, so `b_i` can be smaller than the nuclide's true population. It also ignores
product multiplicity, keeps a source's bound at its initial value when the source is itself produced, and its feed
bound `f` uses the same maximum. The documentation (`docs/METHOD.md`, the v1.0.0 paper §2.3) calls the result a
conservative bound; it is not one in general. No committed control shows an inventory change beyond 2.8e-9
relative (`results/g3_pruning.json`, rate against reachable-set pruning on the 132 FNS experiments), so the defect is
in the claim and the decision rule, not in a known result. The activity bound exists only with `options.screen`, and
no response bound covers kept nuclides whose inflow from a dropped nuclide was cut.

## Change under test

Notation. `A` is the assembled rate matrix of one run (decay, feed, removal and every step's reaction triplets, as
passed to pruning today). Off-diagonal entries are non-negative. For each edge `j → i` let `a_ji` be its largest rate
over the schedule: decay, feed and removal entries as assembled; a reaction entry is the sum of its triplets within
one spectrum, times `φmax` (the largest step flux multiplier), maximised over the run's spectra. `T` is the total
schedule duration including cooling. `N0` is the initial vector (the trace unit state is 1). For each state `j` and
spectrum `s`, `d_j^s(φ) = λ_j + Σ_j^s φ` is its loss rate without removal partitions, where `Σ_j^s` is the negated
diagonal of spectrum `s`'s reaction triplets.

The edge fraction is the largest share of `j`'s losses that can follow the edge:

- decay `j → i` with rate `λ_j·br`: `g_ji = br` (the branch share of decay, the largest over any flux);
- reaction `j → i`: `g_ji = max_s σ_ji^s φmax / (λ_j + Σ_j^s φmax)`, which is the share at the highest flux because it
  increases with `φ`; multiplicity is carried in `σ_ji^s` as assembled;
- removal edges into the removed sink: `g_ji = 1`;
- an edge out of a state with no loss term (the trace unit state, a feed source): `g_ji = ∞`.

Two bounds per state, both on the unpruned problem:

- throughput `Θ_i` ≥ `N0_i` + every atom that enters `i` over `[0, T]`;
- population `S_i` ≥ `sup_{t≤T} N_i(t)`.

They satisfy

```
Θ_i ≥ N0_i + Σ_j min(a_ji T S_j, g_ji Θ_j)
F_i = Σ_j a_ji S_j                           (bound on the inflow rate)
S_i ≥ min(Θ_i, N0_i + F_i / λ_i)             (second term only for λ_i > 0)
```

Proof sketch, to appear in `docs/METHOD.md`: atoms moving along `j → i` over `[0, T]` number at most
`∫ a_ji N_j dt ≤ a_ji T S_j`, and at most `g_ji` times the atoms leaving `j`, which cannot exceed `Θ_j` because
`N_j(T) ≥ 0`; `N_i ≤ Θ_i`; and `dN_i/dt ≤ F_i − λ_i N_i` gives `N_i ≤ N0_i + F_i/λ_i`. The map is monotone, so
any vector that satisfies the inequalities from above stays an upper bound when the map is applied again.

Computation. States are grouped into strongly connected components (Tarjan) and processed in topological order.
A component without a cycle is evaluated once from its predecessors. For a cyclic component, a Collatz–Wielandt
weight `w > 0` (from power iteration of `I + Cᵀ`, with `C_ij = min(a_ji T, g_ji)` inside the component) gives
`q = max_j (Cᵀw)_j / w_j ≥ ρ(C)`. If `q < 1`, every member starts at `(w·b)/((1−q) w_i)`, where `b` is the
component's inflow bound from outside plus `N0`, and the map is applied repeatedly until no value falls by more than
1e-12 relative, or for at most 1000 sweeps. Every iterate is a valid bound. If `q ≥ 1`, the component and every state
it can reach are marked unbounded and kept, and the ledger names them. Bounds hold up to floating-point rounding.

Decision rule. In `rate` mode a state is dropped if `S_i < bmin` (unchanged `options.bmin_atoms_per_g`, default 1e-8,
or the `screen` threshold), and it is not a source, the unit, leakage or the removed sink. `reach` and `none` are
unchanged.

Response bounds, always written to `ledger.rate_pruning` when states are dropped:

- for each dropped state, `atoms_per_g_bound = S_i` and `feed_atoms_per_g_s_bound = F_i`;
- `dropped_activity_Bq_per_g_bound = Σ_dropped λ_i S_i` and `dropped_heat_W_per_g_bound = Σ_dropped λ_i Q_i S_i`,
  with `Q_i` the evaluated light, EM and heavy energies per decay;
- the cut-edge error: the pruned populations of kept states are not larger than the unpruned ones (the kept diagonal
  is unchanged and only non-negative inflow is removed). Their shortfall `e` obeys the same inequalities with
  `N0 = 0` and an extra inflow `J_k = Σ_dropped min(a_dk T S_d, g_dk Θ_d)` (rate `Σ_dropped a_dk S_d`). The same
  solver gives `E_k ≥ sup e_k`, and `kept_activity_Bq_per_g_bound = Σ λ_k E_k`,
  `kept_heat_W_per_g_bound = Σ λ_k Q_k E_k`;
- `activity_Bq_per_g_bound` and `heat_W_per_g_bound`, the sums of the dropped and kept terms, bound the change in
  total activity and total decay heat at every step;
- `unbounded_components` lists the states kept because no contraction was found.

The ledger keeps `threshold_atoms_per_g`, now reporting the threshold actually used (the screen threshold when
`screen` is set). The old `removed_heat_W_per_g_bound` is renamed `dropped_heat_W_per_g_bound` (spec echo and schema
documented). The `screen` certificate uses `activity_Bq_per_g_bound` and `heat_W_per_g_bound` in place of the
dropped-state-only bounds.

## Required evidence

Only the coordinator runs executable jobs, serially, under the required systemd cgroup, with disk-backed `TMPDIR`.
Inputs are committed with the candidate, before any evidence run. No `.npz`, `.zip` or `.endf` files are tracked.

- **R1, independent recomputation.** A new probe binary, `crates/actinv-core/src/bin/prune_probe.rs` (the pattern of
  the existing `cram_probe` and `fission_probe`), prepares a spec through the ordinary path and writes the pruning
  inputs (decay, feed and removal triplets, each spectrum's reaction triplets, the schedule and `N0`) and the
  library's outputs as JSON. It adds no user-facing option. An independent Python checker reads that file and
  recomputes `a`, `g`, the components,
  `Θ`, `S`, `F`, `E`, the drop set and every ledger bound. The drop set must be equal, and every bound must agree
  within 1e-10 relative. Cases:
  - a synthetic battery of at least 30 problems that reaches: a converging pair of paths where the v1.0.0 maximum
    is below the true population; a long linear chain; decay and reaction cycles with `q < 1`; an isomer pair with
    branch 1; an edge with multiplicity above 1; trace mode with the unit state; coupled mode with a produced source;
    feed and removal schedules; two spectra; cooling steps; and a component with `q ≥ 1` (the checker reports branch
    coverage, and each item must be hit at least once);
  - the 132 FNS experiments at the benchmark settings.
- **R2, dominance.** For every R1 case, R3 case and threshold, the problem is also solved with `prune: none` at CRAM
  order 48 (the reference). At every step: every dropped state's reference population is at most `S_i`; every kept
  state's shortfall is at most `E_k`; the change in total activity and in total heat is at most
  `activity_Bq_per_g_bound` and `heat_W_per_g_bound`. Reference values below `1e-30` atoms/g count as zero, and a
  difference counts as a violation only if it exceeds the bound by more than `1e-12` relative to the reference total
  plus the CRAM-16/CRAM-48 difference measured on the unpruned problem. Every comparison must pass.
- **R3, threshold study** (descriptive; it feeds the paper). Thresholds `1e-12, 1e-10, 1e-8, 1e-6, 1e-4, 1e-2`
  atoms/g on all 132 FNS experiments and on two committed long-history cases: the FNS iron example extended to one
  year at constant flux with one year of cooling (coupled mode), and the bundled pulsed example. Reported per
  threshold: kept states, solve time, the actual and bounded change in total activity and heat (largest and median
  over steps and experiments), and the ratio of bound to actual change. No pass value is set; R2 applies.
- **R4, the repaired case.** A committed converging-path fixture where the v1.0.0 algorithm drops a state whose
  reference population exceeds the threshold, and the repaired rule keeps it. This becomes a Rust regression test
  and a checker case.
- **R5, baselines.** Every committed result that changes is listed, regenerated from the candidate's release binary,
  and committed with the evidence. For each one the checker shows that only the drop set, the ledger pruning block,
  the certificate and the normalized-result hash changed, or that the changed values differ from the previous ones by
  less than the new `heat_W_per_g_bound` and `activity_Bq_per_g_bound` of the run. Any other change is a failure.
- **R6.** Workspace tests, fmt, clippy `-D warnings` native and wasm, Python binding checks, module tests, handbook
  build, the P13 data-distribution step and the checker selftest, all run after the last edit; `docs/METHOD.md`,
  `docs/guide/method.md`, `docs/guide/specification.md` and `docs/LEDGER.md` describe the bound and fields;
  CI green on the pushed commit for every triggered workflow.

## Verdict

P148-PASS if R1–R6 hold. A failing gate is preserved under `results/failures/p148/`; one repair round, registered as
an amendment first.

## Not claimed

The bound covers the linear problem ACTINV assembles: the stated decay and reaction data, the schedule's largest
flux multiplier for every step, and the estimated flux of a power-driven step (`docs/guide/specification.md`). It
does not cover nuclear-data, flux or model uncertainty, numerical error of the solver, responses other than total
activity and total decay heat (partial heat responses inherit the total heat bound), or mesh-mode aggregation
beyond the per-cell bound.
