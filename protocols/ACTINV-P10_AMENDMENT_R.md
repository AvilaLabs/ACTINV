# ACTINV P10 Amendment R — legacy result version-field normalization

**Date:** 2026-08-27. **Trigger:** After the coherent v0.5.0 milestone bump required by the close, the final G6 CI
rerun rejected the legacy omitted-projectile neutron result. A recursive comparison against the retained pre-P10
result found exactly one differing leaf: `certificate.solver` changed from `actinv-core 0.2.0` to
`actinv-core 0.5.0`. Every physics value, schema field, ledger entry, certificate input/hash and other string remained
identical after the existing timing/path normalization.

The frozen phrase “old neutron specs/results remain byte-identical” failed to account for the certificate's required
package-version identity changing when the P9–P10 technical v0.5 milestone closes. Keeping a false 0.2.0 solver label
or withholding coherent package metadata would weaken, not preserve, provenance.

## Frozen repair

1. The legacy result comparison normalizes only a `certificate.solver` value matching `actinv-core X.Y.Z` to
   `actinv-core <VERSION>`. Its new pre-P10 normalized SHA-256 is
   `0ed6be999d63820556d91ad73ab73fa7980f9b37dca8fcc00dd4c351f7cd1b1c`; independently applying the same rule to the
   v0.5.0 result gives the identical hash.
2. G6 separately requires the emitted solver identity to equal the current root workspace version. It continues to
   require no serialized neutron projectile field, historical `fluence_n_cm2`, no generic charged fluence field, and
   exact equality of every non-timing/non-path/non-version leaf.
3. Re-run G6, G1, the CI subset, clean-clone control and Rust quality gates before the P10 verdict. No runtime,
   physics, data, certificate input, ownership, concurrency or numerical tolerance changes.

This is a control normalization for intentionally versioned provenance, not a relaxation of neutron behavioral
compatibility. A successful close remains **P10-CONDITIONAL**.
