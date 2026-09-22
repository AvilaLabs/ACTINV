# P18b runtime checker alignment with P38

2026-09-18. This amendment governs the current-binary generated fixture in
`controls/check_g3_p18b.py`; it does not rewrite the frozen P18b protocol,
historical evidence, or historical verdict.

P38 explicitly admits non-fission MF=10 production-only sections using their
partial sum as a comparator, with the permanent `missing_total_self_comparator`
caveat. The old P18b generated `missing_total_still_fails` expectation therefore
contradicts the already implemented P38 contract. GitHub run 35360216366
demonstrated that mismatch after the Rust quality gates passed.

Replace only that current-runtime leg with `missing_total_self_comparator`.
For the one-product MT102 fixture, require successful construction, one loss
row and one production row, both independently equal to the lethargy collapse
of the specified 0.5 barn source table, closure, and the explicit ledger
statement that conservation is not anchored to an independent total.
The generated result is checked before setting its pass flag. All seven
other generated legs and the real-corpus controls remain unchanged.

This is compatibility with the pre-existing P38 decision, not a new relaxation
of production validation. Do not overwrite historical P18b reports to disguise
their original behavior. Current CI uses `--no-write`. This amendment carries
no claim about the fusion benchmark's scientific reproduction.
