# ACTINV-P64 — `actinv decide`: the decision loop in one command

## Question

The platform already ships the pieces of the product story — banded
activation, certified constraint margins (P56), completeness audit
(P61), measurement-design ranking (P60) — but they are separate
artifacts a user must assemble. Can one command run the whole loop:
solve with bands, evaluate declared constraints at band edges, and emit
a single decision document that states what passes, what would have
overcertified on nominal alone, how clean the underlying chain data is,
and which measurement would shrink the band most?

## Deliverable

`actinv decide DECISION.json [OUT.json]` — a thin orchestration layer,
no new solver math:

- Decision spec `actinv-decide-1`: `{"run_spec": <path-or-object>,
  "decision": {"constraints": [{name, response, time_s, edge, sense,
  limit}], "measurement_top": N}}`. Edges/senses reuse the P56
  vocabulary (`nominal`, `normal_lower/upper`, `conservative_lower/upper`;
  `le`/`ge`).
- The run spec must declare `uncertainty` (covariance + responses) —
  `decide` errors otherwise ("decision requires a banded run"). The
  effective spec additionally requests `outputs: ["audit"]` and
  `uncertainty.design` when absent; the emitted document hashes both
  the authored and effective specs so the injection is visible.
- Per constraint: band-edge value, nominal value, margin_fraction
  (violation / |limit|, same sign convention as optimize), satisfied,
  nominal_would_satisfy — the nominal-overcertify accounting, free.
- Verdict block: `certified` (all constraints satisfied at declared
  edges), `binding` (|margin| within 1e-9), `nominal_would_overcertify`
  count, and a plain-language statement.
- `completeness`: the run's P61 audit verdict, verbatim.
- `measurements`: for each constraint response at its step, the
  top-N entries of the emitted `design` block (parameters + reaction
  blocks) — "what to measure next", keyed by `response@t_s`.
- Output doc `actinv-decision-1`: all of the above plus the run
  result's sha256 — one file that is the whole decision record.

## Honesty rules

- No banded run ⇒ no decision document. `decide` refuses to certify
  point-estimate runs.
- Constraint evaluation uses the identical `select_step`/`response_edge`
  machinery as P56 (same time-alignment tolerance, same edge semantics).
- `measurements` reproduces the emitted design block verbatim (ranked
  lists truncated to `measurement_top`); it does not re-derive anything.
- The document never says "certified" without naming confidence level,
  covariance identity, and `unmodeled_relative` if declared.

## Gates

- **G1 mechanics** — fixture decision spec with one certifying and one
  failing constraint: margin values match manual edge extraction; the
  nominal-overcertify count is exact; audit+design blocks present;
  missing `uncertainty` rejected.
- **G2 exactness** — checker reparses the emitted document, recomputes
  every margin/verdict from the raw run result, verifies sha bindings.
- **G3 demo** — real-data single-material case (W, TENDL-2025 709g
  covariance): a limit set to certify at the declared edge; the
  document lists concrete measurement targets.
- **G4 determinism** — same decide twice → byte-identical document.
- **G5 checker** — planted mutations (flipped satisfied flag, forged
  margin, swapped sha) caught on reparse.
