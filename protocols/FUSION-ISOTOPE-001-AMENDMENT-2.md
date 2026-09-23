# Fusion Isotope Benchmark 001 — receipt re-seat after solver changes

Written 2026-09-23, before generating the new receipt. This amends only the evidence handling of
Amendment 1. The model, inputs, omissions, independent control, numerical acceptance and publication
comparison are unchanged.

## Reason

The reduced-chain receipt `results/fusion-isotope-001/reduced-chain.json` (GitHub Actions run
35368517712, implementation commit `710969c`) records SHA-256 identities of the probe, CRAM and sparse
sources. Three later commits changed `crates/actinv-core/src/bin/cram_probe.rs` and
`crates/actinv-core/src/sparse.rs`:

- `cc2cade` added the selective CRAM refinement gate and convergence floor;
- `9420f8c` exempted subnormal-scale rows from the residual and convergence gates;
- `0944ad8` fixed lint only.

Since then every `fusion-isotope` run has stopped at `hashes.probe_source` before comparing any
number. `cram.rs`, the coefficients, the case, the reference, the control and Amendment 1 are
unchanged.

## Procedure

- Keep the original receipt byte for byte as the record of the `710969c` run.
- Point the control at a new receipt, `results/fusion-isotope-001/reduced-chain-2026-09-23.json`. The
  control's own `--write` mode, which refuses to replace an existing file, creates it from the current
  sources. It uses the same `cram_probe`, CRAM-16 coefficients, tolerances and closed-form Bateman
  control.
- The new receipt passes only through the unchanged verification. The recorded and the fresh histories
  must each meet the predeclared analytic bound. Every identity except the platform binary hash must
  match exactly. `comparison.md` must match the receipt's Table 6 comparison.
- Source identities stay pinned. Any later change to the probe, CRAM or sparse code stops the workflow
  again and needs its own recorded re-seat.

## Not changed

No tolerance, input, omission, publication value or verdict changes. The publication comparison stays
`not_reproduced`, with no agreement verdict.
