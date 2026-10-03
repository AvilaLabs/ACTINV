# P104 Amendment A — distinct cell timestamps and above-limit fixture

2026-10-03. P104 G1 attempt 1 passed all 126 source-derived vectors, external
H-3 relocation, unequal-mass aggregation and planted output mutations. Its
timestamp refusal fixture failed because both mesh records referenced the same
Python `cell_result` object. `deepcopy` retained that alias: changing cell B's
timestamp also changed cell A's timestamp. The CLI correctly accepted equal
timestamps. The retained attempt is `results/p104_g1_attempt_1.json`, SHA-256
`9aecb748a93cacef3906962c3f64f019a1ce1db8b2d915ad3fcdac08d440e80f`.

Construct the two records with separate deep copies, so the refusal plant
actually presents 0 s and 1 s in the same component. Preserve the original
successor control seal as `results/g0_p104_successor_v1.json` and reseal repaired
control code before another runtime invocation.

Before any G2 invocation, static review also found that the planned above-limit
composition increment of 1e-6 relative is smaller than the frozen cooling loss:
`1 - exp(-ln(2) * 1e6 / 1e11)`, approximately 6.93145e-6. That plant therefore
cannot establish above-C classification at both target times. Use
`upper + max(abs(upper) * 1e-4, 1e-6)` wt% within the existing composition cap.
The 1e-4 increment exceeds the analytically known cooling loss; no measured G2
outcome selected it. The target times, nuclear fixture, independent formula,
rows, acceptance tolerances and classification boundaries are unchanged.

The same pre-G2 review found incomplete comparisons of already required evidence
fields. Require both usable Nb intervals and the joint interval, compare row
units/concentrations/limits and complete constraint/contributor metadata, and
check class/verification flags and the zero external H-3 offset for every target.
Missing or duplicated records must fail. Add sensitive planted-field mutations
through those independent comparisons. These enforce the original field contract
and do not introduce another population or acceptance threshold.

The control-child runner's final post-kill wait must also be bounded, with
already-exited termination/kill races handled. Before resealing, independently
exercise normal exit, timeout termination, exit during termination, and exit
during kill using separately launched small Python children. These lifecycle
checks enforce the existing workstation rule and do not run Rust test executables
as child applications.

These are control fixture corrections. Production Rust, the source rule pack,
protocol scope and public vectors are unchanged. This is P104's one repair round
under the standing rules. Retain this amendment and both seals; a subsequent
failed gate closes P104 FAIL.
