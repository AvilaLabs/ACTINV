# P140 — Hardening pass for the FISPACT-II parity additions (P122–P139)

Registered 2026-10-15, before any P140 evidence. Owner direction 2026-10-15: harden the parity additions before the
v1.5.0 release ("lets do the hardening pass first"). Base commit: `5769aae`. Reference: release build of `2f3daa4`.

## Purpose

P122–P139 were each verified on narrow inputs (mostly FNS Fe and a W-186 foil), largely one feature at a time, with
implementations and checkers written by agents. P140 looks for defects outside those paths. It changes no code by
itself; every defect it finds is repaired under an amendment to this protocol, registered before the repair, naming
the defect, the fix and its regression test.

## Gates

Coordinator runs every job serially under the 6 GiB cgroup. Candidate: release build of the commit under test; the
final verdict is derived on the final candidate, with H2–H4 rerun on it.

- **H1, adversarial review.** Every module added or substantially changed since `2f3daa4` is reviewed by an agent
  that did not implement it: summary and depletion; photon outputs and regulatory tables; schedule controls
  (rebin, wall loading, partition, overrides, decay-only runs); fission counting and spontaneous fission; power and
  kerma; pathways, routes and trace attribution; projectiles and step libraries; self-shielding geometry and LRF=7;
  group structures and the high-energy splice; `inspect`; `import-fispact`; `report`; sampling statistics. Each
  finding is classed defect, robustness or documentation. The coordinator reproduces every claimed defect (a
  failing test or run) before accepting it; rejected findings are recorded with the reason. Pass: every accepted
  defect is repaired with a regression test.
- **H2, interaction matrix.** Materials: natural Fe, natural W, SS316 with Co, Nb and Ta impurities, natural U.
  Modes: trace where permitted, coupled. Option sets: baseline; summary with depletion; Steiner-22 photons with point
  source and bremsstrahlung; power-driven steps with a kerma table; wall loading; partition; overrides; pathway
  routes; geometry self-shielding; uncertainty with depletion; and every compatible option combined. Each run must
  either succeed or refuse with a message naming the conflicting options. Invariants on every successful run:
  - no NaN or infinite number anywhere in the result;
  - no inventory below −1e-12 of the step's tracked total;
  - summary totals equal the sum of their per-nuclide rows, and element rows sum to the totals, within 1e-12;
  - depletion shares sum to 1 within 1e-12;
  - each route closure satisfies total = Σ routes + residual within 1e-12;
  - a repeated run is byte-identical apart from `ms`.

  The baseline set with default options is byte-identical to the reference apart from `ms`.
- **H3, importer breadth.** Inputs: the 132 CoNDERC FNS decks, the 2 CoNDERC fission decks, the 6 reference decks of
  UKAEA's `pypact` (Apache-2.0, commit `e28091c1`), and synthetic decks exercising every keyword the importer knows,
  singly and in combinations, with valid and malformed arguments. Pass: no crash or panic; every run exits 0 or with a
  specific error; every keyword occurrence receives a status; `--strict` fails exactly when an unsupported status is
  present; specs from real decks validate.
- **H4, handbook walk-through.** Every complete spec in `docs/guide/` validates; every fragment for a parity feature,
  merged into `examples/fns_fe_5min.json`, validates; every CLI command shown for `import-fispact`, `report` and
  `inspect` runs on the shipped examples and exits as documented.
- **H5.** CI green on the pushed commit carrying the evidence, with the run-output baselines unchanged.

A gate that fails is recorded with its evidence; repairs follow amendments registered first.

## Not claimed

P140 does not prove the absence of defects, adds no comparison with FISPACT-II or with measurements, and does not
qualify triton or helion projectiles on real data.
