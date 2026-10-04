# ACTINV P110 amendment A — explicit control identity and omission repair

Frozen 2026-10-03 before repair. Parent protocol SHA-256
`8ad8870d42d30f3f8c330021b15e7f4f570032deba1ee5b79f57b9411e40e7f9`.

The initial native gate completed all nine requests and repeat outputs, then
the planted invalid basis step caused an unhandled `StopIteration`. No G1/G2
record was persisted. Preserve `results/p110_control_failure.log`, SHA-256
`70fa3b77bc9fa70fc4859b2babee1cd94d5e339e3e17533945e016592c768139`,
the original G0 as `results/g0_p110_before_amendment.json`, SHA-256
`8b8796f2724ba6f6c4c8dcadda41c1f0fc6171165b6f963b35ebfd60d9276dc2`,
and the original oracle as `results/p110_oracle_before_amendment.py`, SHA-256
`36b442caa1cd19c8eec14797f9b117d091bfb123fb7786dd7932145cd7a1f12c`.

A bounded diagnostic of those existing outputs (no new CLI runs) found no
validator errors in seven cases. Two omission cases exposed control expectations:
pure Nb/Si runs do not contain positive Fe58, and the omission ledger's
`products_no_evaluated_decay_data` is a rate map keyed by `41094_0`, not a count.
Preserve `results/p110_initial_oracle_diagnostic.log`, SHA-256
`d982f22213590625260244389211db73157525701551a26d33d7236089e6a03f`.
This diagnostic is not a passing qualification gate.

The coordinator also attempted the nonexistent `test_p105_child_runner.py`
instead of the established `test_p105_children.py`; no lifecycle regression
executed. Preserve `results/p110_child_invocation_failure.log`, SHA-256
`428c56482e8a59a74d33dacbd734a3c7444f373a44c2e5151ab294136b279466`.
Both initial failures occurred before any repair; this amendment consumes the
one repair round and requires the real lifecycle tests to execute.

Authorize only explicit malformed basis identity rejection (wrong/missing/
duplicate steps and duplicate elements without lookup exceptions), safe coverage
record lookups, positive-Fe scoping of the Fe58 evidence assertion, and a literal
positive finite Nb94 omission-rate/defect check against the actual ledger schema.
Add source-only regressions for these identity and omission predicates and
meaningful planted identity mutations. Bind this amendment and preserved failure
artifacts in G0/verdict, set repair count one, reseal, and rerun all native gates
and read-only replay. No production Rust, generated physical population, input
domain, selected schedule, rule row, mathematical model, tolerance, coverage
downgrade or class acceptance criterion changes are authorized. Native known-
subset checks and final conservative class envelopes retain their distinct scopes.

Another failed gate after this repair closes P110 FAIL and requires a frozen
successor. Preserve P109 unchanged throughout.
