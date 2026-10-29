# ACTINV-P158 — nuclear-data uncertainty bands for impurity screening

Registered 2026-10-29, before any source change for the phase. This is the uncertainty layer that P157 set aside
(P157 protocol, introduction and item 4). With it, `actinv impurities` reports every value and limit with a confidence
bound over the nuclear-data uncertainty.

## Change under test

`actinv impurities` (P157) refuses an `uncertainty` section. Its reason is that sampled bands do not compose linearly
across element runs. That is true when each element is sampled on its own, because the draws depend on the material.
It is not true when every element run of one sample uses the same factors. With the data fixed at one draw, the
response is again linear in composition, and P157's algorithm applies to that draw unchanged.
`actinv waste budget uncertainty` (P146) already works this way for class budgets, and P158 reuses its machinery.

1. **Input.** The `actinv-impurities-1` document takes an optional `sampling` block and an optional
   `confidence_level` (default 0.95, from 0.5 to 0.999):
   - `sampling` is the block of `actinv waste budget uncertainty`: `samples` from 20 to 4096, a `seed`, and the
     `channels` and `covariance` of `study.robustness`, with an optional `keep_samples` folder relative to the document.
   - The composition channel is refused, because composition is what the screen varies.
   - A spec with fewer samples than the order-statistic minimum at its confidence level is refused, and the refusal
     names the minimum (72 at 0.95).
   - A base spec that declares its own rate, decay or yield scales, or enables gas production, is refused, as in P146.
   - The base spec's own `uncertainty` section (first-order bands) stays refused.
   - Every P157 refusal still applies. Every refusal is made before any solve, and nothing is written.
2. **Common factors.** Factor blocks are drawn once per sample by `study::draw_factor_blocks`. They come from the
   sampling plan of the full alloy at the *plan composition*: the matrix, every impurity at its range maximum, and the
   balance taking the remainder, so every element the screen can contain is in the plan. Each sample's block is
   applied by `study::apply_factor_block` to every element solve of that sample.
3. **Per-sample screen.** For each sample, P157's deterministic algorithm runs on that sample's element responses. It
   gives the matrix-only, nominal and worst-case value of every endpoint at every target, each impurity's contribution
   at nominal, the limits and `joint_k` where a limit is given, and the nominal class, worst class and per-impurity
   class limits for `waste_class`.
4. **Bands.** The order-statistic index `k` is P146's: the largest index with the Clopper–Pearson lower end of
   (n − k + 1)/n at least the confidence level. Results go in an `uncertainty` block beside each P157 entry; the P157
   fields are the nominal-data screen and are unchanged.
   - **Values** (`matrix_only`, `nominal`, `worst_case` and each impurity's `contribution_nominal`): the median and
     the upper bound, which is the k-th largest per-sample value.
   - **Limits** (each impurity's `alone`, `others_at_nominal` and `others_at_worst`, and `joint_k`): the k-th smallest
     per-sample limit. Statuses follow P146:
     - a sample capped by the range or balance enters at its cap;
     - a failed or infeasible sample enters at 0;
     - a limit is `not_established` when any sample has a positive lower bound.
   - **`waste_class`:** class frequencies at nominal and at worst case over the ranges. The probability that the
     target class is met at nominal and at worst case, with its Clopper–Pearson lower bound at the confidence level.
     Each impurity's class limit, as the k-th smallest per-sample value.
   - **Ranking:** for each endpoint and target, the fraction of samples in which each impurity is the largest
     contributor at nominal.
5. **Verification.**
   - **Limits:** for every established limit, the samples at sorted ranks k − 2 to k + 2 are re-solved with the full
     alloy and that sample's own factors, at 0.99 and 1.01 times that sample's own limit. Each 0.99 point must meet
     the limit, and each 1.01 point must not, unless the limit is capped.
   - **Values:** for every value bound, the sample at the bound's rank is re-solved at its own worst-case corner. The
     solve must agree with the composed value to 1e-6 relative.
   - A miss exits 3 after writing the output, as in P157.
6. **Example and handbook.**
   - `examples/impurities_ra_steel/impurities_uncertainty.json` is the worked example with a `sampling` block:
     72 samples, cross-section MF=33 and decay channels, and the catalog covariance.
   - The handbook chapter gains a section on reading the bands, and states the runtime measured in the evidence run.

## Reference, candidate and data

- **Reference:** `actinv` built from master `3814c21` (sha256 `925a8f40d81c46af517758a1ddef36bcbd592b7a8edb8e44a3f0f7f82c88e8a2`).
- **Candidate:** the P158 branch.
- **Data:** the installed v1.1.0 libraries, the decay files and `catalog:tendl-2025-neutron-709g-covariance`, as P146
  used them.

## Gates

- **G0.** This protocol's SHA-256 is in `protocols/protocol_hash.txt`.
- **G1.** fmt, `cargo check --workspace --tests`, clippy `-D warnings` and the workspace tests pass.
- **G2.** Existing behaviour is unchanged. Apart from the timing keys `ms`, `elapsed_ms`, `wall_time_s` and
  `cells_per_s` (at any depth), the reference and candidate give identical exit codes and outputs for:
  - `actinv impurities` on the worked example and on P157's three other cases, without `sampling`;
  - `actinv waste budget uncertainty` on P146's committed specification.
- **G3.** Common factors. With `keep_samples`, for every sample of the G4 case, the factors recorded for each element
  solve equal that sample's factor block on every key the solve uses.
- **G4.** Per-sample linearity. The case is the worked example reduced to 20 samples, run with `--no-verify`, to
  isolate the composition. Five samples are chosen: the lowest, the median and the highest index, and two at random
  from the seed. For each of them, every target and every linear endpoint, the checker builds the full-alloy spec
  with that sample's recorded factors and runs it with `actinv run`, at the nominal composition and at the worst-case
  corner. The run must agree with the composed per-sample value to 1e-6 relative.
- **G5.** Statistics. From the kept per-sample values, the checker recomputes every band:
  - `k`, every median and bound;
  - the class frequencies and probabilities, with Clopper–Pearson lower bounds;
  - the ranking fractions.

  It must agree with the output exactly; bounds are order statistics of the same numbers.
- **G6.** Refusals and verification.
  - Each item-1 refusal exits nonzero with no output: fewer samples than the minimum, the composition channel, base
    scales, gas and the base `uncertainty` section.
  - The worked example's verification passes, and its output's `verification_pass` is true.
- **G7.** Determinism. Running the G4 case twice with the same seed gives identical outputs apart from timing keys.
  A different seed changes the per-sample values.
- **G8.** The worked example runs at 72 samples, and its verification passes. The handbook section's commands and
  output excerpts match a fresh run.
- **G9.** CI green on the pushed evidence commit.

## Verdict

P158-PASS if G0–G9 hold. Development runs are disclosed. A failing gate is preserved under `results/failures/p158/`.
One repair round is allowed, registered as an amendment first.

## Not claimed

The bands are conditional on the sampled channels and assume, per sample, that the activation response is linear in
composition, as P157 does. Composition, geometry, flux-shape and model uncertainty are not sampled. Measured coverage
of these channels on the FNS corpus is below nominal (`docs/DATA_LIMITATIONS.md`), so a bound can be optimistic. The
upper bound on a value and the lower bound on a limit are one-sided order-statistic tolerance bounds. They are not
two-sided intervals and not a design margin. The worked example's composition and history are illustrative.
