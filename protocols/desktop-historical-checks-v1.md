# Desktop integration: historical dependency evidence

Frozen before implementing the CI repair, 2026-09-08.

The P16 dependency-identity claim covers its opening commit
`0332779401363d2f39722efe7a0b7218afcfb270` through its recorded source-evidence
commit `ede20289ff63951e61db536e2e36dffa5809bd62`. Adding the desktop exposed
that the dependency checker instead enumerates today's workspace and compares
it against the historical opening. Apply the same fixed-endpoint interpretation
already used by P16's source-difference check to its dependency inventory.

Both independent checkers must compare the exact historical manifest bytes at
those two commits. The stored P16 evidence, protocol, and verdict stay unchanged.
Current quantity-source checks, compile-pass/fail consumers, doctests, workspace
quality gates, desktop/CLI parity, and dependency-declaration checks remain active.

Regression checks must accept the stored historical inventory, reject missing
or altered hashes, and show that the desktop manifest is absent from the frozen
inventory. Missing historical files or commits must fail closed. This repair
does not assert that today's workspace has P16's dependency graph.
