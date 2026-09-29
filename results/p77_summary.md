# P77 — trace reservoir fix and exact mesh collapse: outcome

Protocol `protocols/ACTINV-P77_PROTOCOL.md` (sha256 `dae03e2b…d5b4`, frozen before the patched binary
ran). Worktree `../actinv-wt-p77`, branch `p77-trace-mesh` from `25fdb8d`. Patched binary
`target/p77/actinv` sha256 `8f7777ee…9688`. Verdict: `results/p77_verdict.json`
(`controls/p77_verify.py check`). Executed 2026-09-29.

## Frozen verdict

| gate | result | number |
|---|---|---|
| G0 integrity | **PASS** | 783/783 runs; mesh and FNS runs rc 0 |
| G1 trace additivity (P75b R arm) | **PASS** | max e_agg **6.8e-12** (was 0.51 in P75b) |
| G2 coupled path bitwise unchanged | **PASS** | 460/460 runs identical |
| G3 mesh records bitwise unchanged | **FAIL** | `ss316_r2s` identical; `fe_p21like` differs |

## Why G3 failed (post-hoc, not a revision of the verdict)

The protocol wrongly treated both mesh profiles as pure tests of the collapse change. `fe_p21like`
runs in auto mode, which selects **trace**. Change 1 deliberately alters trace results: Fe-57
produced from Fe-56 is now tracked instead of dropped. The observed differences are at most
3.6e-8 (activity-weighted relative); FNS iron changes by at most 1.6e-13 in total activity.

Isolating the collapse change: the same 60-cell iron mesh in **coupled** mode is **bitwise
identical** between binaries (`target/meshprof/fe_coupled.{old,new}.ndjson`).

## Speed (descriptive, one thread, 60 distinct spectra)

| mesh | old cells/s | new cells/s | speedup |
|---|---:|---:|---:|
| Fe, 2 steps, auto (trace) | 3.02 | 9.62 | 3.2× |
| Fe, 2 steps, coupled | 3.47 | 11.80 | 3.4× |
| SS316LN, 5 y + 1e6 s, photons | 1.93 | 3.79 | 2.0× |

## Consequences

- Trace mode now approximates only the constancy of the bulk. Production into bulk nuclides from
  tracked or bulk parents is carried in tracked hybrid states. `bulk_production_dropped` stays in
  the ledger schema but should now always be empty.
- A latent overwrite was fixed at both sites: a fed or hybrid reservoir nuclide's tracked activity
  used to replace its bulk activity in the output and response snapshot.
- Controls that pin trace-mode numbers for multi-element materials will move. The FNS corpus
  (pure elements, trace) moves at the ~1e-13 level. CI baselines have not been re-run.
- The remaining per-cell cost after the span change still sits outside the CRAM solve.
  Restricting the collapse to rows reachable from the material is the next exact speedup (not
  done).
