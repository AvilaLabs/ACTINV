# ACTINV P51 Protocol — Persistent worker / amortized latency

Date: 2026-09-25. Status: **open** — sealed at G0; gates below are frozen by that seal.

Predecessor: P50 (`P50-CONDITIONAL`). This phase delivers the extension's
speed gate: a bounded long-lived worker that amortizes data-file load and
per-call preparation across evaluations, on identical solver semantics.

## Frozen definitions

**Prepared-input cache (in `actinv-core`).** `PreparedRun` already gathers
every data-file-derived input a solve needs — activation library (verified),
covariance sidecar (validated), decay tables, photon/fission/radiological/
damage/shielding preparations, chain — under one preparation pass. This phase
adds a single-slot `PreparedCache`: a `PreparedRun` keyed by a fingerprint of
every input that determines it (library path + resolved sha256, decay refs +
shas, covariance ref + sha, photon/fission/radiological/damage/shielding
options serialized canonically, projectile, temperature, spectrum structure,
collapse-flux digest, and the prepared-artifact variant — Dense vs
Collapsed vs Groupwise — that `run` would select). A cache hit reuses the
`PreparedRun` byte-for-byte; a miss prepares and replaces the single slot.
One slot is the memory bound: a corpus `PreparedRun` is ~1–2 GB class and the
workstation scope is 6 GB; the cache never holds two.

`run_with_cache(spec, entry_point, cache)` is the only new entry point;
`run(spec, entry_point)` delegates to it with a one-shot cache, so the cold
path and the hot path execute literally the same preparation code on a miss
and the same solve code always.

**Worker (`actinv worker` in `actinv-cli`).** A subprocess mode: reads one
UTF-8 JSON object per line from stdin, serves requests sequentially (one
solve at a time — memory is the binding resource), writes one JSON object
per line to stdout. Request schema `actinv-worker-request-1`:

```json
{"schema":"actinv-worker-request-1","id":<int>,"op":"run","spec":{...}}
{"schema":"actinv-worker-request-1","id":<int>,"op":"stop"}
```

- `op:"run"` → response `{"schema":"actinv-worker-response-1","id":<id>,
  "ok":true,"result":<the run() document>,"timing_ms":{...}}` or
  `{"...","ok":false,"error":"...","timing_ms":{...}}`. The `result` document
  is the same `RunResult` serialization the CLI emits for `actinv run`.
- `timing_ms` is a side-channel ledger: `queue_ms` (request received → solve
  start), `prepare_ms` (cache hit ⇒ the fingerprint+lookup cost only),
  `solve_ms`, `warm` (bool — whether the prepared cache was hit). Timing
  fields never enter the result document.
- `op:"stop"` → a final `{"id":<id>,"ok":true,"stopped":true}` then a clean
  exit(0). EOF on stdin is an implicit stop.
- Malformed lines (not JSON, wrong schema, missing id/op/spec, unknown keys)
  get `{"id":<id-or-null>,"ok":false,"error":...}` and the worker continues;
  a malformed request never kills the worker.
- Errors in `run` are returned as `ok:false` responses with the error text —
  a failed solve does not kill the worker and does not poison the cache
  (a failed preparation leaves the previous slot intact).

**Cancellation and restart.** The worker serves sequentially; an in-flight
solve cannot be preempted in-band. Cancellation is therefore by process
termination: the supervising client may kill the worker at any time; a killed
in-flight request is simply never answered (the client marks it cancelled)
and the cache dies with the process. On restart the worker starts cold and
the next request re-warms — deterministic, so a re-issued request returns a
bit-identical result. This phase ships no Rust client: the supervisor in the
lifecycle gate is the control itself, which exercises kill → reap → respawn
through the OS. Bounded waits everywhere: the `stop` handshake is bounded
(≤ 5 s before kill + reap), and per-request waits are the caller's (no
hidden timeout — solves are legitimately minutes-scale).

**Bit-identity claim.** For a given spec, the `result` document emitted via
the worker is byte-identical to `actinv run` output modulo exactly the
declared fields: `ms` and the side-channel `timing_ms` (wall-clock), plus
`entry_point` / `certificate.entry_point` — honest provenance that records
`"worker"` instead of `"cli"`. Everything else — every number, every
nuclide row, certificate and ledger fields — identical.

## Artifact set

- `crates/actinv-core/src/run.rs` — `PreparedCache`, `run_with_cache`.
- `crates/actinv-cli/src/command.rs`, `crates/actinv-cli/src/worker.rs` —
  `actinv worker` subcommand and request/response loop.
- `controls/p51_artifacts.py` — artifact registry + drift check.
- `controls/g1_p51_mechanics.py` — protocol mechanics.
- `controls/g2_p51_identity.py` — hot/cold bit-identity battery.
- `controls/g3_p51_amortization.py` — measured cold-vs-warm ledger.
- `controls/g4_p51_lifecycle.py` — memory ceiling, cancellation, restart.
- `controls/check_g5_p51.py` — independent checker.
- `examples/p51_battery/` — the identity battery specs (synthetic + corpus).

## Gates

- **G0 (seal).** This protocol hashed; artifact set pinned; recorded in
  `results/g0_p51_seals.json`.
- **G1 (mechanics).** Request/response round-trip on the synthetic fixture;
  ids echoed; schema/id/op/spec validation rejects malformed lines and
  unknown keys without killing the worker; `stop` produces clean exit; EOF
  produces clean exit; a failing spec returns `ok:false` and the worker
  serves the next request.
- **G2 (hot/cold identity).** Battery: the P11 synthetic spec, a
  synthetic-uncertainty spec (covariance path — Dense library variant), and
  one reduced corpus spec (1 step, real data). Each spec's worker `result`
  equals the fresh `actinv run` output byte-for-byte apart from declared
  timing fields; within the worker, the second execution of the same spec
  reports `warm:true` and still matches.
- **G3 (amortization).** On the corpus spec: ≥3 cold solves (fresh process
  each) vs ≥3 warm solves (worker, after warm-up) — per-request wall time
  ledgered with hardware line (CPU, memory cap); warm median must be
  measurably below cold median (threshold: warm ≤ cold × 0.5 for the
  load-dominated corpus probe; the actual split is ledgered either way).
  Ledger: `results/g3_p51_amortization.json`.
- **G4 (lifecycle).** Memory: worker RSS sampled across a sustained battery
  (≥20 mixed requests incl. a cache miss) stays inside a declared ceiling
  (4 GB — the checker verifies samples never cross it). Cancellation: kill
  the worker mid-solve on the corpus spec → client's `cancel` returns, the
  child is reaped (no zombie/orphan: the control scans the process table),
  the next request respawns and returns a result byte-identical to a
  never-killed run. Bounded waits: all waits carry deadlines.
- **G5 (independent checker).** `controls/check_g5_p51.py` re-verifies from
  the recorded ledgers: identity comparisons re-diffed byte-for-byte,
  amortization arithmetic re-checked, lifecycle log order verified (kill →
  reaped → respawned → answered), and rejects planted mutations (swapped
  warm/cold labels, missing reap record, fabricated identity).

## Conditions and honest boundaries

- The warm path skips *data-file reload*, not qualification: the sha256
  verification and index validation run once per prepared context; the
  fingerprint carries the resolved shas, so a changed file produces a
  different fingerprint and a miss. No solver step is skipped.
- Single-slot cache: alternating between two corpora thrashes honestly —
  measured and ledgered, not hidden. The GUI sweep / optimize / per-bin
  workloads are same-corpus, which is the intended shape.
- The worker adds a trusted in-process boundary: results are the same
  `run()` path — the process isolation the spawn-per-run GUI worker gives is
  reduced to "one long-lived process per session"; crash-restart restores
  isolation per solve boundary.

## Amendment rule

Post-G0 changes to sealed artifacts are append-only amendments: recorded in
this file with reason and sha, then re-sealed. Any change to the frozen
definitions above is a new phase, not an amendment.
