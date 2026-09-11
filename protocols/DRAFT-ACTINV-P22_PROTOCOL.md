# DRAFT ACTINV-P22 protocol — public re-score and release

Status: **draft — not frozen**. Working draft for maintainer review; freezes
only on explicit direction as `ACTINV-P22_PROTOCOL.md`. Runs last, after
P18–P21.

## Opening context

Roadmap row (post-v1 program, frozen by CB1): "Rerun frozen CB1 against
v1.0.0 and the candidate; score P17 held-out evidence; repeat open-code,
install, memory, runtime and mesh exercises; publish raw machine-readable
evidence, limitations and narrowly supported claims." Prerequisites:
P18–P21 complete.

P22 is the scorecard phase: every improvement landed by P17–P21 is
re-measured against the frozen CB1 battery, and every remaining loss is
named. The P17-sealed held-out evidence is read exactly once here.

## Design contract (draft)

### Re-score legs

- Frozen CB1 numerical battery on the candidate binary: operator identity,
  identical-data ALARA comparison, FNS 132-experiment family.
- Open-code and install exercises repeated: clean clone build, wheel install,
  first-example time, memory peak.
- Runtime and mesh exercises at the P21-evidenced scale.
- Self-shielding and uncertainty cells re-scored with the P19/P20 evidence.

### Held-out read

The P17 held-out values are read exactly once through unchanged scoring
code; every sealed row is accounted; no post-read metric or exclusion change.

### Publication

Raw machine-readable evidence tables, the limitations register, and the
narrowly-supported claims are committed; a superlative is allowed only where
it names the exact executed workload and comparator set.

## Gates (draft)

- **G0** — protocol freeze; confirm all P18–P21 verdicts and seals.
- **G1** — candidate binary pinned; CB1 battery re-run and re-scored.
- **G2** — install/memory/runtime/mesh exercises re-run.
- **G3** — held-out read (one-time); score published.
- **G4** — release candidate assembly.
- **G5** — independent closure; additive release decision.

## Explicit non-claims

- No metric, threshold, eligibility or exclusion changes after the held-out
  read.
- No "best overall" claim — only workload-scoped, evidenced claims.
