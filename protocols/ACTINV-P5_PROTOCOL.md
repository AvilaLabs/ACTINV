# ACTINV P5 — problem specification, Rust core from spec to result, three entry points, pathway analysis

**Roadmap row:** P5. **Opened:** 2026-08-27. **Time box:** four calendar days. **External acts:** the principal's.
**Architecture (principal's decision, 2026-08-26):** the Rust core owns the entire path from specification to result.
Python keeps library building, the FNS harness and the checkers, and no longer assembles problems. The CLI, the Python
API and the harness must therefore be **one binary reached three ways**, which is what makes the identity gate
meaningful and the certificate's solver hash real.

**Minimum gate input** (standing rule 7): the FNS Fe 1996exp_5min problem plus the 132-experiment FNS set, both already
local. No new nuclear data. No library rebuild.

## Deliverables
1. `actinv-spec-1` JSON specification (docs/SPEC.md, draft written in P4) — every field defaulted, unknown fields are an
   error, never ignored.
2. Rust: spec parsing and validation; library (`.npz`) and decay-sublibrary readers; abundance and atomic-mass tables
   embedded with provenance; material → atoms per gram; library → one-group rates; chain assembly; trace/coupled
   selection from the recorded burn-up fraction; schedule stepping; inventory, activity and decay heat split α/β/γ;
   pathway analysis; ledger and certificate emission.
3. `actinv run spec.json` (CLI), `actinv.run(spec)` (PyO3), and the harness calling the same code.
4. Linked, not reimplemented (per the standing rule — I/O, not numerics): `serde`/`serde_json` (JSON), `zip`/`flate2`
   (the `.npz` container). Every one MIT or Apache-2.0; licences recorded in the ledger before use.

## Gates
**G1 Readers.** Rust decay parser vs the Python parser on all 3,821 ENDF/B-VIII.0 materials: half-life, every branching
ratio, Q value and mean energy equal to 1e-12 relative. Rust library reader vs numpy on the full TENDL-2023 library:
every row and every group value bit-identical.
**G2 Composition.** Rust material → atoms per gram vs the Python harness on all 132 FNS compositions: 1e-12 relative,
abundance sums exact, mass balance to 1e-12. Embedded tables carry their provenance string.
**G3 Identity across entry points.** On the FNS Fe 1996exp_5min spec: CLI = PyO3 = harness at **0.0** on every
inventory, activity and heat value, and the three certificates identical apart from the entry-point field.
**G4 Physics unchanged.** The 132-experiment FNS set run through the new path reproduces the P4b records at 1e-12 on
every matched decay-heat point. Any difference is a defect, not an improvement.
**G5 Pathways.** For every product at every step, ranked production chains with contributions; contributions sum to the
nuclide's atoms to 1e-12 under the trace formulation; a planted control removing one chain's reaction removes exactly
that entry and no other.
**G6 Ledger and certificate through every entry point.** Every category of docs/LEDGER.md present; a planted failure
(deleted decay record) surfaces identically through CLI, Python and harness.
**G7 Mode selection.** `auto` picks trace below a burn-up fraction of 1e-6 and coupled above; both agree to 1e-8 on the
FNS Fe spec; a synthetic high-fluence spec flips the choice and the coupled result differs from trace by the expected
first-order amount, reported.

## Verdict (`controls/check_p5.py`)
P5-PASS: G1–G7. P5-CONDITIONAL after one repair round on a gate. P5-FAIL otherwise. UNSCORED: time box.
Standing rules 1–7 apply, including: reimplement only what a control can verify end to end, link everything else;
license-check every dependency before the build-versus-link decision; the verification layer stays in Python.
