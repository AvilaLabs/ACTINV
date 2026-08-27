# ACTINV P11 Amendment A — corpus aggregation and conditioned controls

**Date:** 2026-08-27. **Parent protocol:** `ACTINV-P11_PROTOCOL.md`
(`fb9964d523e9bad8e2175ff24b5ca0e14982d9bfdf46962664a00a10925cc2d4`).

The first frozen-gate execution exposed three control/build defects. This amendment records their dispositions before
the repaired evidence is used. No production physics tolerance or P11 acceptance threshold changes.

1. The target checkpoint key originally included the complete activation-index hash, so a one-source mutation
   invalidated every target. It now uses the source hash and that source's activation-target identity. The per-target
   parsing/storage fingerprint is explicit and is not changed by aggregation/report edits. Full aggregation also
   revalidated and rebuilt the complete grid map after every append, producing two quadratic passes. Each checkpoint
   is still validated before append, the combined library is still validated once before atomic publication, and one
   retained grid map is now used for the linear aggregation pass.
2. The first G2 ERRORR deck used pointwise PENDF cross sections inside each CCFE group, contrary to P11 rule 8's
   declared group-constant cross-section convention. Its worst selected Fe-56 difference was `7.1125e-3`, above the
   frozen `5e-3` limit. The repaired control runs GROUPR at the same 709 boundaries and passes that GENDF to ERRORR.
   It mechanically removes MF=32 and unselected MF=33 sections from a temporary, hash-recorded copy, runs only through
   the evaluation's exact 200 MeV support, and verifies that ACTINV's remaining 20 groups are exactly zero. Formatted
   GENDF boundaries are checked at the six-significant-digit field limit; the covariance criterion remains `5e-3` or
   `1e-14 barn^2`. The repaired worst comparison is `2.7266e-4` relative.
3. G3's first five-point pass produced `1.45e-15` to `2.31e-14` response-unit/barn residues for branch/response pairs
   that the independently inspected fixture graph proves disconnected; the analytic recurrence returned exact zero.
   Division by the `1e-4 barn` perturbation amplified ordinary CRAM response roundoff. For only the two declared
   disconnected pairs (Mn-56 response versus the Mn-57 product row and conversely), G3 now records the raw derivative
   and conditions it to zero only if all four response samples span no more than
   `128 * binary64 epsilon * max(abs(sample))`. Connected pairs remain unconditioned. The frozen `1e-4` relative or
   `1e-18` absolute sensitivity criterion is unchanged.

Because a repair round was required, a successful P11 close is `P11-CONDITIONAL`, not `P11-PASS`.
