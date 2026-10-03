# P103 Amendment A — persisted G0 descriptive metadata

2026-10-03. The first runtime-control invocation stopped before any production
waste CLI invocation: all six source checks passed, input identities matched,
but whole-result equality rejected three newly emitted descriptive fields absent
from the original G0 result (`version`, `ecfr_source_url`, `source_as_of`).
`results/p103_runtime_attempt_1.json` retains that outcome.

Correct the persisted-seal comparison to require exact equality of every original
evidence field while allowing only those three additional fields. Their values
remain independently checked against the frozen pack and literal source identity.
All source checks must still pass; source, pack, protocol, interpretation, vectors,
classes and numerical acceptance thresholds are unchanged. Never rewrite the
original G0 seal to hide this mismatch.

This records the control-layer repair before G1/G2 production evidence. Retain
the original runtime-control seal as revision 1 and bind the repaired checker as
revision 2. This is the phase's one recorded repair round under the standing rules.
