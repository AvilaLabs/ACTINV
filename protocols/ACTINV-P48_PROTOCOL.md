# ACTINV-P48 protocol — interactive exploration

**Status:** DRAFT — unopened, unhashed | **Drafted:** 2026-09-23 |
**Parent:** roadmap draft innovation extension P48 (`docs/ROADMAP.md`,
commit `6db406d`) | **Depends on:** P43 (band source), P45 (measured
latency envelope); optionally P46 (evaluation-selection axis)

Frozen sweep axes, compute bounds, latency envelope and the measured
workload are placeholders to be fixed at the freeze; this document
authorizes no execution.

## Intent

Make the speed lead visible. The desktop gains live re-solve on
declared sweep axes through the *identical* spec→result machinery —
never a second numerics implementation. A displayed result is always
bound to the parameters that produced it. This is the conversion of
the CRAM/prepared-run advantage into a capability no competitor offers
at any price: interactive exploration of a qualified activation
calculation.

## Scope

- Declared sweep axes (frozen list): composition fraction of a named
  element, flux normalization multiplier, cooling-time selection; an
  evaluation-selection axis is admitted only if P46 shipped its
  qualified surface. Anything outside the frozen list is refused, not
  improvised.
- Interactive execution: the GUI generates a variant spec per parameter
  point and solves it through the same in-process prepared-run path the
  CLI uses. Per-interaction compute is bounded (frozen cap on
  concurrent variants and on samples per point), and a parameter change
  cancels superseded work.
- Band overlay: where the P43 campaign envelope permits, an optional
  sampled-band layer over the swept response — its per-point sample
  count and total budget are frozen; where the budget cannot hold, the
  band is absent by declaration, never faked.
- Result binding: every rendered result carries a request generation;
  a response whose generation is not the current one is never painted
  under new parameters (the stale-result race is a named defect class).
- Latency claim: measured time from parameter change to rendered result
  on the frozen flagship workload, recorded with hardware — reported,
  not aspired to.

## Out of scope

- A second numerics path, speculative/precomputed answers presented as
  fresh solves, unbounded background work, and any GUI-only shortcut
  that bypasses spec validation.
- New physics, new responses, or interactive *study* authoring beyond
  the frozen sweep axes.

## Gates (draft)

- **G0** — seal: protocol hash, opening commit, frozen axes and bounds,
  candidate identity, workload identity.
- **G1** — mechanics: sweep → spec → solve → render path working on the
  frozen axes within the frozen compute bounds.
- **G2** — controls: interactive-path outputs identical to `actinv run`
  on the same generated specs (identity control across the sweep grid);
  a planted superseded-generation result cannot render; cancellation
  during a solve terminates and reaps the worker (bounded wait — the
  local process-spawning rules apply); compute bounds refuse a
  declared-over-budget interaction.
- **G3** — measurement: interaction latency on the flagship workload
  recorded with hardware, thread count and prepared-run state; cold and
  warm figures separated.
- **G4** — independent closure: checker re-derives the identity and
  race controls from recorded artifacts, verifies bounds and gate
  ordering, rejects planted mutations, emits the verdict.

## Closure rule (draft)

PASS only if the identity control holds on every frozen axis point,
stale-render and cancellation races are demonstrably impossible within
the tested battery, and latency is measured. CONDITIONAL if an
amendment was used or the band overlay shipped budget-limited (named).
FAIL otherwise.
