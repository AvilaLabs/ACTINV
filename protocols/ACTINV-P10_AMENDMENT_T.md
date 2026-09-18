# P10 amendment T — inactive P30 ledger metadata

2026-09-18. The same metadata-only drift diagnosed in
`ACTINV-P15_AMENDMENT_P30_METADATA.md` also affects the P10 legacy neutron
digest. Keep the existing P10 result hash and all physical/provenance
acceptance conditions. Normalize only the exact integer-zero value of
`ledger.assembly.rate_scale` to its pre-P30 absence for the legacy digest.
Do not normalize the ordinary CLI/Python/mesh result comparisons.

A one-run diagnostic using the binary identified in the P15 amendment
produced hash
`14a66280198b0d928d624349b123ab2b71d506aa377752030c18ecfe79a4808b`.
Removing just that zero-valued leaf yielded
`a80fed9578efd6af61cc48a08144a383093ef6aa955fd07abc360cea2fa61307`,
exactly the already pinned P10 value. The diagnostic used the same fixture,
composition, flux, duration, and mode as the existing legacy control and ran
under the required systemd limits. No historical result was regenerated.

The control runs regression assertions before its runtime cases: only an
integer zero may disappear; active/noncanonical values and inventory changes
must remain distinguishable in the digest. Neither solver behavior nor
historical receipts are changed by this amendment.
