# ACTINV problem specification — draft for P5 (`actinv-spec-1`)

*Draft. Becomes normative when P5's protocol is hashed. Every field has a default; unknown fields are an error.*

A problem is one JSON document. The same document drives the command line (`actinv run problem.json`), the Python
API (`actinv.run(problem)`) and the validation harness, and is embedded verbatim in the run certificate.

```json
{
  "spec": "actinv-spec-1",
  "title": "FNS Fe, 5-minute irradiation, 1996 campaign",

  "library": { "path": "~/nuclear-data/tendl-2023/actinv_tendl2023_709g.npz", "sha256": "…" },
  "decay":   { "primary": "endfb80", "fallback": "jeff33" },

  "material": {
    "mass_g": 1.0,
    "basis": "wt_percent",
    "composition": { "Fe": 100.0 }
  },

  "spectrum": {
    "structure": "fispact-709",
    "flux_per_group": [ … 709 values, ascending energy … ],
    "total": 1.116e10
  },

  "schedule": [
    { "dt": "5 min", "flux": 1.0 },
    { "dt": "66 s",  "flux": 0.0 },
    { "dt": "16 s",  "flux": 0.0 }
  ],

  "options": {
    "mode": "auto",
    "prune": "rate",
    "bmin_atoms_per_g": 1e-8,
    "temperature_K": 293.6,
    "outputs": ["inventory", "activity", "heat", "pathways", "ledger", "certificate"]
  }
}
```

## Fields

| field | meaning | default |
|---|---|---|
| `library.path`, `library.sha256` | 709-group activation library built by `actinv-data`; the hash is checked before use | required |
| `decay.primary`, `decay.fallback` | decay sublibraries; the source used for each nuclide is written to the ledger | `endfb80`, `jeff33` |
| `material.basis` | `wt_percent`, `atom_fraction`, or `atoms_per_g` | `wt_percent` |
| `material.composition` | elements (natural abundance) or explicit nuclides (`"Fe56"`, `"Ta180m"`) | required |
| `spectrum.structure` | `fispact-709`, `vitamin-j-175`, or `custom` with `boundaries_eV` | required |
| `spectrum.flux_per_group` | group fluxes, ascending energy; scaled to `total` if given | required |
| `schedule[].dt` | duration with unit (`s`, `min`, `h`, `d`, `y`) | required |
| `schedule[].flux` | multiplier of the spectrum's total during the step (0 = cooling) | required |
| `options.mode` | `auto` (trace when burn-up < 1e-6, else coupled), `trace`, `coupled` | `auto` |
| `options.prune` | `rate` (bounded rate-significance), `reach`, `none` | `rate` |
| `options.bmin_atoms_per_g` | rate-pruning threshold; dropped nuclides and bounds go to the ledger | `1e-8` |
| `options.temperature_K` | must match the library's temperature; mismatch is an error | library's |
| `options.outputs` | any of `inventory`, `activity`, `heat`, `photons`, `pathways`, `ledger`, `certificate` | all |

## Result

```json
{
  "spec_sha256": "…",
  "steps": [ { "t_s": 300.0, "flux": 1.0,
               "inventory": [ {"nuclide": "Fe55", "Z": 26, "A": 55, "LISO": 0, "atoms_per_g": 1.23e10} ],
               "activity_Bq_per_g": {"Mn56": 8.8e5, "…": 0},
               "heat_W_per_g": {"total": 3.0e-7, "alpha": 0.0, "beta": 1.1e-7, "gamma": 1.9e-7} } ],
  "pathways": { "Mn56": [ {"chain": ["Fe56", "(n,p)", "Mn56"], "fraction": 0.98}, {"chain": ["Fe57", "(n,d)", "Mn56"], "fraction": 0.02} ] },
  "ledger": { "…": "every category of docs/LEDGER.md" },
  "certificate": { "inputs": { "library": "sha256…", "decay_primary": "…" }, "solver": "actinv-core 0.0.1", "…": "…" }
}
```

Units: atoms per gram of material, Bq per gram, W per gram; energies in eV; times in seconds in results regardless of
the input unit.

## Controls P5 will impose on this spec
Round-trip identity CLI = Python = harness at 0.0; totals equal sums of parts to 1e-12; pathway fractions sum to 1
per nuclide on the trace formulation; every ledger category present; planted failures surface identically through
every entry point.
