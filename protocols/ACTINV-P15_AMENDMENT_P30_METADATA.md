# P15 legacy digest compatibility for inactive P30 metadata

2026-09-18. Preserve the frozen P15 receipts, hash pins, numerical content,
and every cache-integrity acceptance condition. Only the compatibility
normalization in `controls/g3_p15_cache_integrity.py` changes.

P30 commit 4051b58 added `ledger.assembly.rate_scale`, the number of applied
rate scales, including an integer zero for unperturbed calculations. The
P15 fixture does not request perturbations. GitHub run 35362191832 passed
every runtime cache-integrity assertion, but the exact receipt comparison
failed because its normalized result hashes included this additional field.

A bounded single-run diagnostic reproduced the CI digest using application
binary SHA-256
`dbe6326130f2c21c999b6d67527840c78eaab51c076e706ce543e362bd3f4a56`:

- Current normalized result:
  `c6e6b68b04ade1373a2864444ee2bddcfed7fd836d199d63000136ac4a8c6d68`.
- Removing only `ledger.assembly.rate_scale` with value integer zero gives
  `8a21e8ffc818ab70e0c24f09bc3d02ab77b1e17205c5d91337a732475ab279b2`,
  exactly the existing frozen result hash.

Normalize only that exact integer-zero leaf to absence for this legacy
comparison. Preserve nonzero counts, booleans, floats, nulls, and strings;
they are not the canonical inactive representation. Regression assertions
check zero equivalence, retention of noncanonical values, and retention of
an inventory change. No production output is modified; no stored scientific
result is regenerated or re-pinned. The diagnostic ran under inspected
6 GiB/no-swap/128-task/200%-CPU limits and did not run the concurrent cache
fixture locally. CI executes the complete runtime control.
