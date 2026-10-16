# P140 H1 adversarial review: pathways / trace attribution (P127, P134) and sampling statistics (P136)

Reviewer did not implement. Read-only review (no cargo, no binary). Base for diffs: `2f3daa4..HEAD`.
Files read in full: `crates/actinv-core/src/pathways.rs`, `crates/actinv-core/src/sample_stats.rs`; the P127/P134 regions of
`run.rs` (4090-4130, 4338-4780, 5380-5760, 6248-6465, tests 9105-9155); the `statistics` code in `study.rs`
(168-190, 430-456, 2349-2378, 2596-2900, 3017-3050, 3257-3350, 3353-3650, tests 4226-4470); protocols P127/P134/Amendment A/P136; ledger 155/158/164;
docs `specification.md`, `results.md`, `STUDY.md`, schema. Float claims (percentile rounding, histogram edges) were checked with throwaway python in the scratchpad, not with repository code.

Counts: 2 defect (F1, F3), 8 robustness (F2, F4, F5, F7, F8, F10, F11, F12), 3 documentation (F6, F9, F13); 13 findings. Ranked by (impact x confidence).

---------------------------------------------------------------------------------------------------------------------

## F1  [defect]  `std_dev` returns 0.0 (not NaN/null) for a single value -> `distribution.std = 0` at n = 1
- File: `crates/actinv-core/src/sample_stats.rs:36-38` (`variance(x).max(0.0).sqrt()`), consumed at `study.rs:3529-3546, 3577-3589`.
- `variance` returns `f64::NAN` for n < 2 (line 26-28, and its test asserts that), but `NaN.max(0.0)` is `0.0` in Rust (max ignores NaN),
  so `std_dev(&[x]) == 0.0`. `summarize` then reports `std: 0`, `sampled_std = Some(0.0)` passes the `.filter(is_finite)`, and the
  `first_order` block emits `sampled_over_first_order: 0.0` (when first-order std > 0) for a response that has no spread estimate at all.
  STUDY.md says std is "unbiased, divisor n-1"; for n = 1 it is undefined, and the module doc/test treat it as NaN.
- Reproduction (unit): `assert!(std_dev(&[3.0]).is_nan())` fails (actual 0.0). Study: any case with exactly one successful sample
  (e.g. `robustness.resource_limit_runs: 1`, which is not validated, or all but one sample failing) gives
  `sampling_statistics.responses.R.T.distribution.std == 0.0` and `first_order.sampled_over_first_order == 0.0`; expected `null`.
  The existing (pre-P136) `response_stats` has the same `(m-1).max(1)` behaviour, so both blocks agree on the wrong 0.
- Confidence: high (language semantics; read directly).
- Fix: `let v = variance(x); if v.is_nan() { f64::NAN } else { v.max(0.0).sqrt() }`; have `summarize` carry `Option<f64>` std (serialize null).
  Add test `std_dev(&[1.0]).is_nan()`.

## F2  [robustness]  Route search: one shared `floor_min` + global truncation starves later sources and big targets
- File: `pathways.rs:1103-1118` (floor_min = min over targets of path_floor*total), `852-880` (`explored >= EXPLORE_LIMIT -> truncated, return`).
- The single DFS prunes at the SMALLEST target floor. One tiny target (an explicit target with total ~1e-30, or the 10th automatic target
  whose activity is orders of magnitude below the first) lowers the floor for every target. When the tree then exceeds
  2,000,000 nodes, the DFS stops; because the DFS walks sources in order (reservoirs by chain index, or initial nodes by matrix order) and
  neighbours by node index, the first source consumes the budget and every later source gets zero exploration
  (`dfs` returns immediately). The large targets lose routes in an order unrelated to their size; the only signal is `search_truncated` + a note.
  Note the advice in the note ("raise path_floor") would also change the large targets' floors.
- Reproduction (unit-test sketch in `pathways.rs`): 8 tracked nodes, all-to-all edges rate 1e-3 (diag = -(sum of out rates)), two steps,
  `initial = [(0,1e12),(1,1e12)]`, `final_atoms` = {A: 1e12-scale, B: 1e-30}; `targets: [A, B]`, `max_depth: 10`, `path_floor: 1e-6`.
  Weight per edge <= 1/7, so bound 1e12*(1/7)^k stays above 1e-36 for all k <= 10 -> ~7^10 = 2.8e8 walks -> `search_truncated == true`,
  source 1 (node 1) never explored; `targets[A].explained_fraction` is lower than the same run with `targets: [A]` alone (which finishes).
  Expected: A's routes independent of B. Predicted: differ.
- Confidence: medium-high (follows directly from the code; not executed).
- Fix: prune per target (keep a per-target floor and prune a branch only when it can reach no target above that target's floor), or run one DFS per target
  / per floor tier; split the node budget per source (or per target) so one source cannot exhaust it; report which sources were skipped.

## F3  [defect]  `first_order` compares an all-channel sampled std with an MF=33-only first-order std
- File: `study.rs:3257-3316` (first-order run uses `channels: ["cross_section_mf33"]` only; `sampled_std` is `response_stats[..].std` over ALL enabled channels),
  new consumer `study.rs:3573-3589` (`sampled_over_first_order`, `deviation_in_std_standard_errors = (sampled - first_order)/SE(std)`).
- With `flux_rel_std`, `composition_rel_std`, `decay_constants` or `fission_yields` enabled beside `cross_section_mf33` (the docs' own example in
  `STUDY.md` and `specification.md` enables flux + xs), the sampled std includes variance the first-order propagation never contains, so the ratio exceeds 1 and
  the "gap in bootstrap standard errors" is large by construction. The new z-like statistic reads as a linearity/convergence test and is not one.
  Neither `STUDY.md:186-189` nor the spec guide states the channel restriction. (Pre-existing `local_vs_nonlinear` has the same pairing; P136 added the SE-based deviation on top.)
- Reproduction: study with `channels: {cross_section_mf33: true, flux_rel_std: 0.2}` and `statistics: {}`; expected `first_order` either null or computed against the
  xs-only sampled std (use the isolated `channel_attribution` variance for cross_section_mf33), actual compares against the total sampled std; a pure-flux-dominated response gives
  `sampled_over_first_order >> 1` and `deviation_in_std_standard_errors` in the tens, with a well-converged xs channel.
- Confidence: medium-high (code path is explicit; magnitude depends on the case).
- Fix: compare first-order std with the isolated cross_section_mf33 sampled std (already computed for attribution), or emit `first_order` only when xs is the sole enabled channel,
  and state the restriction in docs.

## F4  [robustness]  Default (first-product) trace `pathways` silently omits production fed by decay of radioactive bulk nuclides
- File: `run.rs:4476-4479` (bulk decay -> `d_src.push((r, ch.unit, v*bulk[c]))`), `6282-6285` (`ptrip` drops every unit-state entry), `6270-6278` (`agg` is built only from reaction `sources`);
  closure loop `6355-6362` only visits nuclides present in `per`.
- In trace mode the daughters of a radioactive reservoir nuclide (natural K: K-40 -> Ar-40/Ca-40; natural U/Th chains; Lu-176; Sm-147) get ALL of their
  decay-fed inventory through the unit state. The P134 repair rebuilt the decomposition from the reaction sources + tracked decay only, so such products have either
  no `pathways` entry or an entry with only their activation legs, and `pathway_closure` (which skips nuclides absent from `per`, and is dominated by near-1e-68 noise anyway, per Amendment A)
  does not flag it. (The P127 routes analysis does handle it via `unit_origin_decay`.) G3 of P134 (FNS Fe, all bulk stable) cannot see this.
- Reproduction: spec with `material: {K: 100}` (or Th-232), `options.mode: "trace"`, a flux step then cooling; mode is accepted because K-40 decays < 1e-6 over the schedule.
  Take a step inventory nuclide produced by K-40 decay (Ar-40/Ca-40). Expected: a pathway chain `K40 -> Ar40` (or documented omission). Predicted: no such entry; sum of listed chains < inventory,
  unreported.
- Confidence: medium (read from the code; not run; depends on the daughter being a tracked chain state).
- Fix: add the decay unit-source edges (`unit_origin_decay`) as pathway sources (not flux-scaled), or document that decay-fed products are outside the first-product table and count them in `pathway_closure`.

## F5  [robustness]  Candidate cap keeps the 8*max_paths loosest-bound candidates; drops are global and not noted
- File: `pathways.rs:787-801` (weight = min(max_s rate/removal, sum rate*dt)), `1122-1147`, `1288-1297`.
- The bound is valid but loose: a reservoir/first-reaction edge has bound `sum r*dt` (e.g. ~3e14 atoms/g for a 5-minute FNS irradiation) against actual ~1e11 for a short-lived product. Candidates are ranked and capped
  by this bound, so routes through short-lived intermediates (loose bound) outrank routes through long-lived ones (tight bound) irrespective of actual size. If a target has more than 8*max_paths (default 800) candidates above `floor_min`,
  the capped-out ones may carry more atoms than kept ones that then fail the `path_floor` test. The loss only shows as a global `candidates_dropped_by_cap` integer: no per-target figure, no entry in `notes`, while `paths_dropped_by_cap` (per target) reports only the later `max_paths` cut.
- Reproduction (unit-test sketch): one source, 1000 two-edge routes A -> M_i -> T where M_i for i < 900 has a huge removal rate (loose bound, tiny actual contribution) and M_i for i >= 900 is stable (tight bound, large actual);
  `max_paths = 100` (cap 800). Expected: the 100 largest routes are the stable-M_i ones. Predicted: they are cut by the bound ranking; explained_fraction collapses while `paths_dropped_by_cap == 0`.
- Confidence: medium (construction is straightforward; real-case incidence unknown, large fission networks likely).
- Fix: tighten the bound with the per-step reachable inflow (e.g. include min(1, rate/loss_to) with sojourn factor min(1, loss*T)), rank by an integrated value in a cheap pass, report dropped candidates per target and add a note when the cap triggers.

## F6  [documentation]  `docs/guide/specification.md:526-527` says a power-driven step's pathway attribution "is not emitted"
- After P134 the default trace attribution runs for power steps (`run.rs:6298-6308`; test `power_step_in_trace_mode_reports_pathways_that_close` asserts no `pathways_suppressed`). The sentence is stale and contradicts the protocol/ledger.
- Fix: delete the clause (keep the `pathways` section refusal wording).
- Confidence: high.

## F7  [robustness / doc]  `loop_floor` can discard walks that are individually above `path_floor`; docs promise "explained" only loosely
- File: `pathways.rs:1166-1182`. A walk with atoms/total >= path_floor is dropped when atoms/base_atoms < loop_floor (default 0.01). So with the default `loop_floor` the closure can leave a residual
  >> `path_floor` per walk, and the P127 G4 statement "explained >= 0.999 at path_floor 1e-6" holds only if the caller also sets `loop_floor` ~ 0. The doc (`specification.md:834-837`) describes the floors but not that the loop test is applied after, and in addition to, the path floor, nor how much it can leave.
- Reproduction: `loops_are_reported_against_their_loop_erased_path` shows it at `loop_floor 0.9`; with default 0.01 and a loop with gain 0.005 on a base path carrying 90 % of the target the dropped walk carries 0.45 % (below default path_floor) but with `path_floor: 1e-6` carries 0.45 % >> 1e-6 and is silently dropped (reported only as residual).
- Confidence: high (code), low severity.
- Fix: document explicitly; consider counting walks dropped by `loop_floor` (`loops_dropped_by_floor`, `loop_dropped_atoms_per_g`) next to `paths_dropped_by_cap`.

## F8  [robustness]  Edge labels: spontaneous-fission products, feed and removal edges are `edge`; labels are a union over all library rows and modes, not the active ones
- File: `pathways.rs:534-592` (`decay_edge_labels`: SF digit 6 -> `step_of` None -> `resolved = false`, no label), `644-692`, `694-723`.
- With `fission_yields.spontaneous` (P125) the SF-product edges exist in the matrix, but `generic[]` groups them under `edge` together with `removal`/`feed`-sink edges and gas bookkeeping edges,
  so `generic` mixes unrelated mechanisms. Also, reaction rows are labelled whether or not their collapsed rate is nonzero in this spectrum, and decay and reaction labels for one (to, from) pair are joined with `/`
  (e.g. `(n,n')/IT`), so one route can carry a label of a mechanism that has zero rate in the run.
- Reproduction: Cf-252 material with SF yields, routes target a fission product; `edges` contains `"edge"` for the Cf252 -> product step. Expected `"SF"`.
- Confidence: high (code), low impact. Fix: label SF edges, label removal/feed (`removal`, `feed`), and emit labels only for mechanisms with rate > 0 in at least one step.

## F9  [documentation]  `input_correlations.n_constant_parameters` counts every parameter when the RESPONSE is constant (or n < 2)
- File: `study.rs:3615` (`all.iter().filter(|c| c.is_none()).count()`), `sample_stats.rs:318-325` (`pearson(c, y)?` is None when y is constant, n < 2, or non-finite).
- `STUDY.md:197-199` says the count is of "parameters that never vary (zero declared uncertainty)". If a response is constant over the samples (e.g. a response insensitive to every input, or n_pairs < 2) the field equals `n_parameters`, and `top` is empty.
- Reproduction: `block_distribution...`-style fixture with a constant response -> `n_constant_parameters == 2` while both parameters vary. Expected 0 constant parameters / a note.
- Confidence: high. Fix: count constant columns separately (std of the column), and add `response_constant: true`.

## F10  [robustness]  Correlations and bootstrap reported for n = 2, 3 with no minimum-n guard
- File: `sample_stats.rs:128-153`, `study.rs:3591-3619`. `pearson` and `spearman` return +-1 for any two non-tied pairs; with n = 2..3 (a heavily failed study, `resource_limit_runs`) the ranked table lists every parameter as a perfect correlation. Skewness is `None` below 3 but nothing equivalent exists for correlation. Combined with F1, the n = 2 case shows `std` fine but correlation magnitudes meaningless.
- Reproduction: study with `resource_limit_runs: 2`; expected `input_correlations` omitted or flagged `underdetermined`, actual `top[*].spearman = +-1.0`.
- Confidence: high. Fix: require n_pairs >= 3 (or flag `n_pairs < 5`).

## F11  [robustness]  Percentile index uses a floating `ceil((n*p)/100)`; wrong rank for some decimal p
- File: `sample_stats.rs:63-67`. For p that is not dyadic (e.g. 2.2) `n*p/100` can land just above an integer. Exhaustive check against exact rational arithmetic (p = k/10, n <= 2000): 141 mismatches, e.g. `n = 1500, p = 2.2` gives k = 34, exact k = 33; `n = 750, p = 4.4`: 34 vs 33; `n = 10500, p = 95.4`: 10018 vs 10017.
  Integer and quarter-integer p (the defaults 2.5/5/16/50/84/95/97.5) had 0 mismatches for n <= 20000, so default output is correct; the documented definition ("smallest x_(k) with k/n >= p/100") is violated for those user-supplied p, and an independent checker using exact arithmetic would disagree.
- Reproduction: `percentile_sorted(&(1..=1500).map(f64::from).collect::<Vec<_>>(), 2.2)` returns 34.0; the definition gives 33.0.
- Confidence: high (computed). Fix: compute in integer/decimal (`p` as a decimal via `Decimal`/scaled integer) or subtract a relative epsilon before `ceil`: `((n as f64*p/100.0) - 1e-9*n as f64).ceil()` plus the exact-multiple check.

## F12  [robustness]  Histogram bin vs printed edge can disagree by one ulp; `total_cmp` splits +0.0/-0.0 ties
- `sample_stats.rs:87-102`: bin is `floor((v-lo)/width*bins)`, edges are `lo + width*i/bins`; a value exactly on a decimal grid can be counted in the bin whose printed left edge is 1 ulp ABOVE the value (found by random search: lo=-0.83, hi=4.93, bins=18, v=3.01 -> bin 12 with left edge 3.0100000000000002; 78 of ~200k random cases). Counts still sum to n; an exact recount from the printed edges would differ.
- `sample_stats.rs:54-58, 107-124`: `total_cmp` orders -0.0 before +0.0, so a sample holding both is two distinct ranks (Spearman) and `min` can be `-0.0`. Responses are normally non-negative so incidence is low.
- Confidence: medium (histogram computed; -0.0 by reading). Fix: place values by comparing against the printed edges (binary search on `edges`); normalize `-0.0` to `0.0` before ranking.

## F13  [documentation]  Statistics block fine print not stated in `STUDY.md`/`specification.md`
- `study.rs:3519`: non-finite response values are dropped silently (`distribution.n` can be < `samples_succeeded`; the older `responses` block uses `n_samples` over all values and gives NaN). `study.rs:3591-3596, 3500`: if the logged input layout does not match the labels (`layout_ok` false) the whole `input_correlations` key is silently omitted; no `status`. `study.rs:3497-3498`: percentile maps are `serde_json::Map` (BTreeMap), so the keys serialize in string order ("16","2.5","5","50", ...) not the requested order, contrary to the `sample_stats.rs` comment "in the order requested"; duplicate p values collapse. `study.rs:3549-3550`: bootstrap seed depends on the (response, time) ordinal, so reordering `robustness.responses` changes the standard errors (not the statistics). Defaults (`percentiles`, 200 bootstrap) are not in `specification.md:605-619`.
- Confidence: high. Fix: document, add `layout_mismatch` status instead of silent omission.

---------------------------------------------------------------------------------------------------------------------

## Test-coverage gaps worth closing with the repairs above (no separate IDs)
- P134 power sub-intervals: the only test (`run.rs:9128`) uses a one-cross-section synthetic chain whose power converges at one sub-interval, so the per-sub-interval multiplier path `intervals` (run.rs:6298-6308) with several different multipliers is exercised by no unit test or gate (G3/G4 of P134 cover flux, pulsed, and spectrum override only). A 3+ sub-interval power step with a strongly burning cross-section should be added (closure at 1e-9 per nuclide above 1e-12).
- `chain_step` clamps any negative result to zero (`pathways.rs:452-455`) with no diagnostic; a genuine loss of precision would present as a missing route. Consider counting clamps and emitting a note when a clamped value exceeds 1e-12 of the route's chain maximum.
- No test of near-equal-but-not-equal removal rates inside a chain of depth >= 3 on the unbalanced (zero-edge-in-some-step) path; the squaring refresh fixes only the diagonal and first off-diagonal.

---------------------------------------------------------------------------------------------------------------------

## What I checked and found correct
**Pathways (P127)**
- Prune bound validity: weight(from,to) = min(max_s r_s/removal_s, sum_s r_s dt_s) is a true upper bound on atoms leaving `from` through the edge per atom arriving (conservation: sum r N <= max ratio * sum loss N <= ratio * arrivals; `N <= arrivals` for the cumulative bound), including time-varying rates, multiplicities > 1 (gas bookkeeping edges), reservoir sources (removal 0 -> bound = atoms supplied), and loop walks (each walk is bounded separately). The product from the source atoms bounds the walk's final-step atoms. Depth accounting (`walk.len() > max_depth` after emitting the candidate) yields exactly `max_depth` edges.
- Walk-sum exactness: chain system with the node's FULL removal on each diagonal, per-step propagation with the step's own edge rates, repeated nodes handled as distinct chain nodes, is exactly the Dyson/walk term; loop walks and their loop-erased base paths are different walks, so Sum over reported walks never double counts. `erase_loops` (first-visit stack truncation) returns a real subpath of the walk (its edges are walk edges); base contribution positive whenever the walk's is.
- Closure arithmetic: residual = total - sum, fractions use `total > 0` guard, `paths_dropped_by_cap` counted after the floors and before the cut, generic grouping built from the retained set (consistent with `max_paths`), loop sum counts only retained loop walks. Signs correct.
- Determinism: adjacency from `BTreeSet`; candidate ordering by (bound desc, walk lexicographic); hit ordering by (atoms desc, walk); auto targets tie-broken by name; `HashMap`s used only for lookup; per-step triplet accumulation order is the input order. Duplicate sources cannot occur.
- Chain integration: diagonal similarity balancing (`D^-1 M D`) is exact and applied to start and result consistently; the unbalanced fallback triggers whenever any edge has zero rate in a step (cooling/zero flux) and is correct; `k == 1` path; zero-duration steps; `sinhc`/divided-difference switch at |half-gap| = 1 is accurate and handles equal diagonals (no Bateman singularity); `triangular_refresh` factor schedule (`scale`, then `2^(j-s)`) matches the exponent after each squaring; upper/lower orientation is right; squarings capped at 1000 with representable scale; non-finite inputs rejected.
- Trace/coupled graph input: per-step `trip` is built from the very `assemble_triplets` the solve used (spectrum slot, multiplier, feed/removal); reservoir supply split by origin (decay unit-edges, reaction unit-edges scaled by the step multiplier, feed) is guarded by the same `pos` and `route_index_of` tests as the unit edges; `tracked_reservoir` hybrids keep separate reservoir and tracked nodes; refusals (power, partition, step libraries) are enforced both in `spec.rs` and `run.rs`; `final_atoms` is the tracked-only inventory, consistent with how the routes end.
- Option validation (ranges, unknown fields, element names refused), nuclide naming through `name_of`, notes for untracked targets and non-initial sources.

**P134 trace attribution**
- Multiplier per step (`schedule_step.multiplier()` for flux steps, per-sub-interval `flux_multiplier_per_substep` with `substep_duration_s = dt/n` for power steps) matches `solve_power_step`; `rsubs[spectrum]` and `sources[spectrum]` indexing is the same `step_spectrum_id + 1` slot rule as the solve; unit states are reset to exactly 1 after each interval (cooling no longer zeroes them); reaction feeds only when `fl > 0`; suppression conditions limited to feed/removal/step library as documented. Ledger 158 numbers consistent with the amendment.

**Sampling statistics (P136)**
- Mean/variance two-pass with compensation; unbiased divisor; skewness `None` below 3 and for constant data (constant sample gives m2 exactly 0 because the compensated mean returns the value); skewness population-moment definition matches docs; nearest-rank percentile equals the documented definition for default and dyadic p (k clamp to [1, n], p = 0 and 100 correct); n = 1 percentile and min/max; histogram constant-sample case, last-bin closing, zero bins/empty; average ranks for ties (mid-rank) and Spearman = Pearson on ranks; Pearson `None` for constant input, clamp to [-1, 1]; pairwise-complete matrix symmetric with `None` for constant series (including its diagonal, documented); bootstrap: SplitMix64 fixed seed, uniform index without modulo bias, `None` for n < 2 or resamples < 2, deterministic across runs (same seed, same output), seed mixes a tag so it does not collide with sampling streams; ranking by |Spearman| with deterministic tie-breaks; `subset` recomputes ranks.
- `study.rs`: label/log layout (flux, composition `BTreeMap` order, decay with `rel > 0`, yield with `rel > 0`, xs rows with `sigma0 > 0`, clamped draws log 0.0) is consistent between `draw` and the `labels` builder; `layout_ok` guards a mismatch; resumed samples re-draw the inputs so `sample_inputs` is complete; failed samples are dropped pairwise; `statistics` enters `config_sha` only when declared; `skip_serializing_if` keeps absent-by-default output byte-identical; validation (percentile range incl. NaN, bins, top, resamples != 1) and schema agree with the code (`bootstrap_resamples` `not const 1`).
