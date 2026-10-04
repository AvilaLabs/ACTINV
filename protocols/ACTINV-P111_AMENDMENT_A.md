# ACTINV P111 Amendment A — retained compilation failure

Registered 2026-10-03 before repair. This consumes P111's one repair round.
Another failed gate after repair is terminal P111-FAIL.

The first workspace check exited 101: the new CLI module used `json!` without
importing that macro. Compilation stopped before executable tests or native
screen controls ran. The source/control G0 seal and its exact replay had passed
before production edits, and all 14 source regressions had passed.

Retain these initial bytes unchanged:

| Artifact | SHA-256 |
| --- | --- |
| `results/failures/p111_initial/workspace_check.log` | `b8dbdb381c7526470068864b231b671eaa253f4937b0a6d7501fec1d3af09128` |
| `results/failures/p111_initial/g0_before_amendment.json` | `b2f356eb3007845a68724b6bdd2b2504005cef85dafd954ce0b75ce2074f0696` |
| `results/failures/p111_initial/check_p111_before_amendment.py` | `bb811a5c9348fb84a3b87fa6e19b915e6f408e9a5a238f4d454984cf85553dd7` |
| `results/failures/p111_initial/waste_intrusion_before_repair.rs` | `63e7d2bd52b80b4ea45cfbe05251a26226b4e87902a489dae4b24918fbf61409` |

Restore the missing macro import and complete mechanical type/warning cleanups
identified in static review before executing the repaired gates. Do not change
the scientific criteria, population, source table, tolerances, scope or resource
limits. Update only G0's repair metadata/hash binding to this registered amendment
and the retained failure bytes; reseal and replay exactly before repaired gates.
The original G0 and checker remain available as evidence of the pre-implementation
seal. No failed or unexecuted implementation check is a PASS.
