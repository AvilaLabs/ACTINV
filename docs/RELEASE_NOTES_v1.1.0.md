# ACTINV v1.1.0 — release notes

ACTINV 1.1.0 extends the fixed-spectrum inventory solver with schedule-driven feed and removal, a linear-regime
reverse calculation, damage observables, finite-dilution self-shielding, practical correlated uncertainty channels,
and a scaling surface for streamed mesh problems. It also closes four evidence phases — P17, P18, P18b and P25b —
with their frozen verdicts, including the confirmed TENDL-2025 emitted-state data defect that the shipped
`data-v1.0.0` artifacts carry. The separately versioned nuclear-data catalog remains `data-v1.0.0`.

Upgrade the Python package with:

```bash
python -m pip install --upgrade actinv
```

Rust CLI users can install this exact minor release with:

```bash
cargo install --locked --force actinv-cli --version 1.1.0
```

## What users gain

- **Feed and removal:** `actinv-spec-1` schedule steps accept constant feed rates and first-order removal
  constants, and a `removed` sink state carries removed material through named ledgers instead of silently
  dropping it.
- **Reverse calculation:** `actinv reverse` (and `actinv.reverse`) recovers the flux normalization or per-segment
  multipliers from measured activities by weighted least squares, with residuals, chi-square and explicit method
  limits.
- **Damage observables:** a `damage` spec section folds hash-pinned `actinv-damage-table-1` tables built by
  `actinv build-damage` from TENDL MF=3/MT=444 into damage-energy and NRT-dpa outputs.
- **Finite-dilution self-shielding:** an optional `self_shielding` section applies unresolved-range Bondarenko
  shielding from hash-pinned `actinv-shield-table-1` tables built by `actinv build-shielding`. Infinite dilution
  remains the explicit default; resolved-region pointwise shielding, escape corrections and probability-table
  transport stay out of scope.
- **Practical uncertainty channels:** propagated response bands now carry a per-channel breakdown across
  cross-section (MF=33), decay-constant (MF=8/MT=457) and independent fission-yield (MF=8/MT=454) uncertainty plus
  a named uncovered remainder. Malformed covariance blocks are excluded with named reasons rather than silently
  clipped or symmetrized.
- **Mesh scaling:** signature-keyed workload grouping reuses solved systems across cells sharing an identical
  rebinned flux, `cell_result_fields` selects outputs, a post-hoc `memory_limit_bytes` guard bounds working sets,
  and output-as-checkpoint resume makes a resumed run byte-identical to an uninterrupted run modulo footer timing.

## Qualification record

- P17 closed `P17-FAIL`: same-operator, identical-data, processing, provenance, independent-arithmetic and quality
  controls pass, but the phase records three falsified benchmark assumptions. No production behavior, interface,
  default, package or nuclear-data artifact changed.
- P18 closed `P18-FAIL` and its separately frozen successor P18b closed `P18b-FAIL` at the held-out gate. The
  isomeric/state-identity work is preserved as evidence; no held-out measurement authorized a release claim.
- P25b closed `P25b-FAIL`: upstream confirmed the TENDL-2025 neutron sublibrary leaks the thermal (n,p) cross
  section into the first emitted-state ordinate of certain MF=10 product records (a fix is expected in a future
  TENDL release). Three hash-pinned alternates — TENDL-2023, FENDL-3.2c, EAF-2010 — were qualified against the
  frozen machinery; none survives comparable-case nonregression. The shipped `data-v1.0.0` artifacts therefore
  carry a documented upstream defect; `docs/DATA_LIMITATIONS.md` is the release-facing disclosure.

## Compatibility and qualification boundary

Existing `actinv-spec-1` problems and the `data-v1.0.0` files remain compatible. ACTINV remains research-grade
software: this release is not approval for licensing, safety, waste-classification, or regulatory use. The
boundaries in `docs/QUALIFICATION.md`, the carried limitations in the v1.0.0 notes, and the data disclosures in
`docs/DATA_LIMITATIONS.md` still apply.
