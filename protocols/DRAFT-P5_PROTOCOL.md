# DRAFT — ACTINV P5 — problem spec, CLI, Python API, pathway analysis   (not hashed; opens after P4 closes)

**Roadmap row:** P5. **Scope:** a JSON problem specification and the three ways to run it — CLI, Python, library — plus
pathway analysis. Nothing else (no new physics, no new data; TENDL/EAF libraries as built in P4).

## Spec (`actinv-spec-1`)
```
{ "spec": "actinv-spec-1",
  "library": {"path": "…/actinv_tendl2023_709g.npz", "sha256": "…"},          // pinned
  "decay":   {"primary": "endfb80", "fallback": "jeff33"},                        // provenance recorded per nuclide
  "material": {"mass_g": 1.0, "composition": {"Fe": 63.72, "Cr": 18.28, …}, "basis": "wt%" | "atom_fraction" | "atoms_per_g"},
  "spectrum": {"groups": 709 | "custom", "boundaries_eV": [...], "flux": [...], "normalise_to": 1.116e10 | null},
  "schedule": [{"dt_s": 300, "flux": 1.0}, {"dt_s": 66, "flux": 0.0}, …],       // relative flux multipliers
  "options": {"mode": "auto" | "trace" | "coupled", "prune": "rate" | "reach" | "none", "bmin_atoms_per_g": 1e-8,
              "temperature_K": 293.6, "outputs": ["inventory", "activity", "heat", "pathways", "ledger", "certificate"]} }
```
Every field has a documented default; unknown fields are an error, never ignored.

## Gates
**G1 Round-trip.** `actinv run spec.json` (Rust CLI, crate actinv-cli) = `actinv.run(spec)` (PyO3) = harness path on the
FNS Fe 5-min spec: inventories and heats identical at 0.0; certificate hashes identical.
**G2 Outputs.** Inventory (atoms/g), activity (Bq/g), heat split α/β/γ (W/g), per step; totals equal the sum of
per-nuclide values to 1e-12; units controlled against a hand calculation (Mn-56).
**G3 Pathways.** For each product nuclide at each step, the ranked list of production chains (source isotope → reaction →
… → nuclide) with their contributions; control: contributions sum to the nuclide's atoms to 1e-12 on the trace
formulation (linear superposition); planted control: remove one chain's reaction and the pathway table loses exactly
that entry.
**G4 Ledger and certificate through the API.** Every ledger category of P2–P4 present in the API result; planted
failure (deleted decay record) surfaces through `actinv.run` identically to the harness.
**G5 Mode selection.** `auto` chooses trace when the recorded burn-up fraction < 1e-6 and coupled otherwise; control:
both modes agree to 1e-8 on the FNS Fe spec; a synthetic high-fluence spec (burn-up 1e-2) flips the choice and the
coupled result differs from trace by the expected first-order amount (reported).

## Verdict
P5-PASS: G1–G5. P5-CONDITIONAL after one repair round. Estimates 2–3 days. Standing rules apply.
