# ACTINV

Open, standalone, activation-grade nuclide-inventory solver. Any neutron flux spectrum in
(MCNP, PHITS, Serpent, OpenMC, or measured); nuclide inventory, activity, decay heat, decay-photon
source and waste-classification quantities out. Rust core, Python API.

Status: P1 (feasibility gates) — not usable yet. Licence: dual MIT OR Apache-2.0 (see LICENSE-MIT and LICENSE-APACHE); contributions under the DCO.

Rules this project keeps from day one: nuclear data are never bundled — they are fetched from their
public hosts and pinned by SHA-256; every result carries a missing-data ledger; every benchmark runner
accepts any code's inventory output; protocols are hashed before evidence and ledgers are append-only.
Avila Labs, Oviedo, Florida.
