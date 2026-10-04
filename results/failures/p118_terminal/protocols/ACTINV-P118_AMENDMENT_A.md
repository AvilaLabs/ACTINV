# P118 Amendment A — specific cgroup-limit diagnostics

Registered 2026-10-04 before changing the failed recorder source. This consumes
the sole P118 repair. A later failed required gate is terminal P118-FAIL.
Keep the original protocol and every prior disposition unchanged.

The first `rust_fmt` gate returned actual zero. The following recorder suite
ran 14 injected-runner tests and returned actual one with one assertion failure:
`test_setup_resource_failure_is_recorded_without_launching_runner`. The guard
correctly refused invalid memory limits and did not launch the injected runner,
but its generic diagnostic omitted the expected `memory.max` field name. No
G0 was sealed, no P118 main/history/science/quality gate ran, and the remaining
new control implementations were incomplete and unexecuted.

Before repair, an independently reviewed, inspected preservation job returned
actual zero and retained 15 exact files plus discovery under
`results/failures/p118_initial/`. Discovery SHA-256:
`21a94cd256bbbb858d9cfc9ebb342e15f3ada0b6d8e73ea39bf9112ac837f080`.
It binds the actually executed recorder/test sources, original protocol and
registry, both unchanged resource/child helpers against base commit
`b81e8c3365a5a08ed55e9f99c0c88f945709996d`, both gate receipts/raw/copied logs,
observed outer invocation/exit/report and the reviewed preservation writer.
Original recorder SHA is
`0c90a5bda677c67a7af1f310fe0ec8ccc0e675fc6d8e2ae648d7687737adb252`;
its test source SHA is
`8e8e96109cb2d8a6241e459d2ec1ce03623ae846717b2bb938f67762f43b9085`.
Do not invent a sealed G0, completed successor source implementation or
scientific checkpoint for this failure.

Correct only `scripts/run_p118_gate.py`'s resource diagnostic to name each
mismatched cgroup limit. Keep exact required field population/values, refusal
before child launch, path/no-overwrite/deadline/cleanup behavior, selected
limits and all existing test assertions unchanged. Complete the already
registered and previously unexecuted P118 control/history/verdict sources
within their original scope. Bind this amendment and all 16 archive files in
G0, independently verify actual failure/source/resource/log agreement, and
record `repair_rounds: 1` in G0/G3/verdict acceptance. Never describe the
original failed recorder gate as passing or restored.

After verifying complete archived byte identity, replace only the two original
gates' generated receipt/copied/raw log paths. Repeat all eleven fresh P118
receipt gates from the registered protocol, including formatting and the
unchanged fourteen recorder tests; preserve the original format PASS and
recorder FAIL separately. Seal and replay G0 before any native science.
Regenerate/replay G3 and the local verdict, execute the exact-candidate clean
worktree proof, and require all six implementation workflows plus closure CI
green. Numerical/data/source-authority boundaries, explicit historical
adoption labels, enforced serial resource limits, stage-first manifest order
and plain owner commit identity remain unchanged. No second repair is allowed.
