# ACTINV-P48 Protocol — Interactive Exploration (Make Speed Visible)

**Status:** frozen 2026-09-24 for G0 seal
**Parent:** Roadmap P48 — "Deliver interactive exploration in the desktop
through the identical qualified solver path — never a second numerics
implementation. Declared sweep axes only; per-interaction compute bounded and
cancellable; a displayed result is always bound to the parameters that
produced it. Regression coverage for cancellation races and stale results is
required by the local safety rules. Latency is a measured claim on the
flagship workload."
**Depends on:** P43 (bands path — sweep displays read the same result
structure; full-band sweeps are a separate axis), P45 (latency envelope
context).

## Intent

The desktop gains a **parameter sweep panel** on the results page. Every
sweep point is a complete generated problem spec solved through the same
isolated worker protocol (`worker::spawn` — the identical code path a manual
Run uses). There is no sweep-specific numerics, no reduced model, no
approximation shortcut.

## Frozen scope

**Declared axes (exhaustive):**

- `CompositionFraction{element}` — wt% of one element, remaining composition
  renormalized to the wt_percent basis.
- `FluxNormalization` — multiplier on `spectrum.total` (shape preserved).
- `CoolingTimeS{step_index}` — duration of one schedule step in seconds.

Library/evaluation selection is a declared-but-unimplemented axis — not
shipped in this phase; named, not hidden.

**Bounds:** `MAX_SWEEP_POINTS = 32` per interaction. Each point runs through
`worker::spawn` with its own private cache (bounded stdout/stderr per the
existing worker protocol). One sweep at a time; starting a new sweep
supersedes the running one.

**Response:** total activity (Bq/g) or total heat (W/g) at a chosen step
index — the same fields the results page renders.

## Frozen verification

**G0 — seal.** Protocol hash; `sweep.rs`, `smoke.rs`, `app.rs`, `worker.rs`,
`model.rs`, `Cargo.toml` digests; binary version; envelope 60 min; one job at
a time.

**G1 — identity.** For every generated spec in the smoke sweep, the
interactive-path result (worker::spawn) must equal the direct solver result
on the identical spec — `without_timings` equality, the same convention the
existing packaged smoke test uses. This is the "no second numerics path"
check, executed on the shipped binary.

**G2 — controls.**

- *Supersession staleness*: a cancelled/superseded sweep's late results are
  inadmissible under the new generation (`admissible()` generation binding).
  Executed headlessly: sweep A cancelled, sweep B admitted; every B point
  carries generation 2; A's cache root cleaned.
- *Mid-sweep cancellation*: cancel after a completed point; the run stops
  dequeuing, in-flight worker killed, private caches removed.
- *Unit regressions*: `sweep.rs` tests — renormalization, flux scaling, dt
  write, point cap, distinct digests, generation rejection.
- *Cancellation race*: the supervisor polls the cancel channel during the
  in-flight worker's wait loop, forwards `request_cancel`, and abandons the
  queue — covered by the headless mid-sweep test.

**G3 — measured latency.** Per-point wall time on the flagship workload
(`examples/fns_fe_5min.json` — the bundled FNS Fe 5-minute spec), flux axis,
values [0.5, 1.0, 2.0], recorded per point with hardware/OS. Reported as a
measured claim — not a target, a number.

**G4 — checker.** Independently: re-run sweep spec generation through the
binary's smoke mode; verify each point's result hash-chained to its spec;
verify the staleness rejection and cancellation evidence; planted mutation —
a forged CompletedPoint under a stale generation must be inadmissible
(structural check via the `admissible` function, executed by the checker
against the smoke report + a constructed probe); machine-readable verdict.

## Closure rule

PASS only if G1 identity holds on every point, G2 controls all pass, and the
latency claim is recorded with hardware named. CONDITIONAL if the GUI panel
is verified only through the headless smoke path rather than an on-screen
interactive session (condition named verbatim — the desktop binary's smoke
mode exercises the identical sweep machinery; a human click-through is
documented separately). FAIL otherwise.
