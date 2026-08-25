# ACTINV

Open, standalone, activation-grade nuclide-inventory solver. Any neutron flux spectrum in (MCNP, PHITS, Serpent,
OpenMC, or measured); nuclide inventory, activity, decay heat, decay-photon source and waste quantities out. Rust core,
Python API, code-agnostic validation harness. Avila Labs, Oviedo, Florida. Licence: MIT OR Apache-2.0.

**Status (P3):** research-grade. Validated against the 132-experiment FNS decay-heat set (IAEA CoNDERC): median
geometric-mean C/E 1.02 vs 1.01 for the FISPACT-II/TENDL-2017 reference; see docs/VALIDATION.md. Not for licensing or
safety decisions.

## Quick start
```
# Rust toolchain (rustup), Python 3.12 venv with numpy/scipy/openmc(data only)/pypact/maturin
cargo build --release                       # builds actinv-solve and the core
./target/release/actinv-solve PROBLEM OUT   # problem-file format: crates/actinv-cli/src/main.rs
cd python && VIRTUAL_ENV=$VIRTUAL_ENV maturin develop --release   # `import actinv`
python controls/run_fns.py                   # the FNS harness (needs the data in docs/DATA.md)
python controls/check_p3.py                  # re-derives every C/E and hash; prints the verdict
```

## Principles
- Nuclear data are never bundled; every input is pinned by SHA-256 in the run certificate.
- Every run emits a missing-data ledger (docs/LEDGER.md). Fail closed, report everything.
- Protocols are hashed before evidence; verdicts are derived by checkers; ledgers are append-only (`ledger.md`,
  `protocols/`, `sessions_*.md`).
- The validation harness accepts any code's inventories (docs/HARNESS.md).

See docs/METHOD.md for the physics and docs/DATA.md for sources and terms.
