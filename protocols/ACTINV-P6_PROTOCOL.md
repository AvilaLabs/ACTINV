# ACTINV P6 — v0.1 release engineering

**Roadmap row:** P6 (last of the v0.1 milestone). **Opened:** 2026-08-27. **Time box:** two calendar days.
**Minimum gate input** (standing rule 7): a small public-data subset for CI — the FNS Fe 5-minute experiment, its
709-group spectrum, the 255-target subset library and the two decay sublibraries. No full-library build.
**External acts remain the principal's**: creating any public repository, publishing a release, uploading to a registry.

## Deliverables
1. Packaging: `cargo` metadata complete for the four crates; a maturin-built wheel for the `actinv` Python module;
   `cargo install --path crates/actinv-cli` produces a working `actinv` binary.
2. CI (GitHub Actions, on the private repository): build the workspace, run the control suite that needs no bulk data,
   and run one end-to-end spec against a small fetched data subset with pinned hashes.
3. `CHANGELOG.md`, semantic version 0.1.0 across the workspace, a `LICENSE` note in each crate.
4. Documentation pass: README quick start that a stranger can follow; `docs/RELEASE_NOTES_v0.1.md` carrying the
   known-limitations table required by the roadmap.
5. Reproducibility: the same spec run on a second machine (or a clean container) reproduces the certificate.

## Gates
**G1 Clean build from a fresh clone.** A clone into an empty directory builds the workspace and produces `actinv`;
no file outside the clone is required except the nuclear data named in the spec.
**G2 Control suite in CI.** Every control that does not need the full library runs and passes from a clean checkout:
G0 coefficients, spec validation, the unit probe, and one end-to-end FNS Fe spec whose heat matches the recorded value
to the P5-G4 criterion (1e-11 μW/g absolute).
**G3 Wheel and binary.** `maturin build --release` produces a wheel that imports and runs the Fe spec in a fresh
virtual environment; `cargo install` produces a binary that runs it. Both give results identical to the development
build at 0.0.
**G4 Reproducibility.** The Fe spec run twice — once in the development tree, once from a clean clone with an
independent build — yields byte-identical result JSON apart from the timing field, and identical certificates.
**G5 Release notes.** `docs/RELEASE_NOTES_v0.1.md` states every gate any shipped phase failed, with the affected data
and the guard, per the roadmap's P6 criteria. A checker verifies the file names every entry in the roadmap's
known-limitations table.
**G6 Version and licence hygiene.** All crates at 0.1.0; every crate carries `license = "MIT OR Apache-2.0"`; both
licence files present; CHANGELOG describes P0–P6 in user-facing terms.

## Verdict (`controls/check_p6.py`)
P6-PASS: G1–G6. P6-CONDITIONAL after one repair round. P6-FAIL otherwise. Standing rules 1–7 apply.
