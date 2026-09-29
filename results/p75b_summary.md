# P75b — composition superposition at real flux: outcome

Protocol `protocols/ACTINV-P75B_PROTOCOL.md` (sha256 `4833fc06…a6eb2`, frozen before execution).
Verdict by `controls/check_p75b.py` → `results/p75b_verdict.json` (sha-binds manifest `d872e559…`,
checkpoint, binary `8113231b…`, and the P75 yield checkpoint). 783/783 runs, 810 s solver wall
time. Executed 2026-09-29.

## Frozen verdict

| gate | result | max e_agg over 216 compared steps |
|---|---|---|
| G0 integrity | **PASS** | — |
| G1 coupled / reach | **PASS** | **1.06e-11** (all spectra, amplitudes 1e10–1e15, cooling to 100 y) |
| G2 trace / reach | **FAIL** | 0.506 (152/216 steps > 1e-6) |
| auto / rate (descriptive) | — | 2.53e-4 (1 step > 1e-6; mode mismatch between mixture and elements in 12/27) |

By amplitude (post-hoc cut of the same data):

| arm | 1e10 | 1e13 | 1e15 |
|---|---:|---:|---:|
| coupled | ≤ 1.1e-11 | ≤ 1.1e-11 | 1.06e-11 |
| trace | 2.5e-4 (concrete, K-40, 100 y) | 6.9e-3 (EUROFER, Maxwellian, Co-60) | 0.51 (EUROFER, Maxwellian, Co-60) |
| auto (shipped) | 2.5e-4 (picked trace) | 3.2e-13 (picked coupled) | 1.1e-11 (picked coupled) |

## Reading

- **Composition superposition holds to round-off in the full coupled solve at every fluence
  tested.** Per-element responses computed at a cell's actual flux can therefore be combined
  exactly: impurity budgets, material swaps and per-element attribution are linear algebra with no
  approximation beyond the solve itself.
- **Trace mode is not additive in composition, and the error grows with fluence.** Expectation R
  (≤ 1e-10) was wrong. Unconfirmed hypothesis: the worst nuclides (Co-60, Ni-63, V-52) are
  second-hop products whose intermediate (Co-59, Ni-62, V-51) is a bulk constituent of the
  mixture. In the mixture that intermediate is a constant reservoir, so production of it from
  other elements does not accumulate, whereas in the pure-element run it does. The K-40 case at
  1e10 does not obviously fit this and is untraced. Where auto mode selects trace (low fluence),
  the effect is ≤ 2.5e-4 in this population.
- Consequence for the composition operator: build it from coupled-mode element runs, or fix trace
  mode's reservoir handling first.
