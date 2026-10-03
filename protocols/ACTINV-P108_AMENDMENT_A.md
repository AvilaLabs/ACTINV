# ACTINV P108 Amendment A — mechanical core type corrections

Frozen 2026-10-03 before repair. Preserve the first workspace-check failure in
`results/p108_g3_check_attempt_1.log`. G0 has not sealed and no production CLI
composition case has executed.

Rust compilation reported two mechanical typing defects: the sorting closure
receives references to coordinate references, so BTreeMap indexing needs the
string slice of each key; and the exact feasibility expansion's empty Vec
needs its `f64` element type stated explicitly before arithmetic method lookup.
Repair these declarations only. The comparison, expansion algorithm, directed
rounding, schemas, artificial population, thresholds and qualification limits
remain unchanged.

G0 must bind this amendment and its registration, the preserved failure hash,
and the final independent control hashes before CLI evidence. Repeat all final
workspace quality gates on the repaired source. P108's single repair round is
now consumed; any further failed gate closes P108 FAIL and needs a separately
frozen successor. Preserve predecessor verdicts and all attempts.
