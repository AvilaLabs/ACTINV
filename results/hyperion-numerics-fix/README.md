# Numerical-repair verification receipts

These receipts record the completed local verification of the three numerical
Rust files published with this fix. Their SHA-256 values match the tested source.
The subsequent upstream commit 8146073 changes only `docs/PARKING.md`; all other
tracked source is identical to the tested base before applying this patch.

The workspace run passed formatting, check, Clippy with warnings denied, and
182 tests. One existing desktop-fixture test was ignored, not executed.
The 27 fresh HYPERION cases and independent controls are summarized in
`fixed_summary.json`; tolerances and limitations are documented in
`../../docs/HYPERION_NUMERICAL_REPAIR.md` and the frozen protocol.

Paths under `target/` in these records identify local run/build artifacts, not
files shipped in this commit. Full raw runs, historical failures and the verified
handoff archive remain preserved locally. Nuclear-data inputs, generated
libraries, email drafts and handoff attachments are intentionally not published
as part of this source fix. These receipts are evidence of executed checks, not
a self-contained nuclear-data distribution or a universal accuracy guarantee.
