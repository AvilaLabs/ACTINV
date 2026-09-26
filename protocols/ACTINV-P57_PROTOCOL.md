# ACTINV P57 Protocol — Transport-Source Adapters (Activation Oracle)

Sealed 2026-09-26 under standing rules 1–7. Hash of this file is recorded in
`results/g0_p57_seals.json`; any change after G0 is an append-only amendment.

## Goal

ACTINV's strategic position is the activation-decision and uncertainty layer
*around* transport codes: a code produces a flux tally, ACTINV solves the
inventory under propagated nuclear-data covariance, and the result must go
back into the transport code's photon source. The inbound half exists —
`actinv import-flux` (P8) reads OpenMC statepoint, MCNP meshtal/mctal, and
FISPACT fluxes into `actinv-flux-1`; `export-r2s` / `export-r2s-joint`
(P52/P53) emit banded decay-photon sources in ACTINV-native interchange
schemas. The missing half is the last hop: **nothing a transport code can
natively read**. Every R2S user today must hand-convert `actinv-r2s-source-1`
into an MCNP SDEF deck, an OpenMC source definition, or a Serpent `src`
card. P57 closes the loop: emit the photon source directly in the formats
the transport codes read, with the banded provenance carried as embedded
commentary so the uncertainty story survives the hop.

This is the approved "activation oracle" lane: the fixed foreign-format
surface is a documented, version-pinned subset (this protocol), verified by
independent parsers rather than by running the transport codes themselves.
Zero new solver compute — pure emission over an existing interchange
document.

Relationship to the legacy emitters: `export-openmc`, `export-mcnp`, and
`export-openmc-mesh` (P7-era) emit *Python fragments* — they generate
`openmc.stats` call sites or an SDEF with a `POS=0 0 0` placeholder the user
must fix by hand, from the un-banded step result. `export-source` is the
pinned adapter surface: it consumes the banded `actinv-r2s-source-1`
interchange document, emits *file-format* output (native XML, a complete
SDEF deck, src cards) with real bounds and propagated σ commentary, and
covers Serpent for the first time. The legacy emitters stay as convenience
fragments; the pinned surface is the documented contract.

## Inputs

- **`actinv-r2s-source-1` NDJSON** (from `actinv export-r2s`, P52): header +
  per-cell records carrying `bounds_cm` (three [min,max] pairs, Cartesian),
  `volume_cm3`, `photons_s`, `sigma_photons_s_independent`,
  `sigma_photons_s_conservative`, `per_nuclide`, and `groups`
  (`{centroid_eV, photons_s}` discrete group representation). Cells whose
  `bounds_cm` are absent or non-finite are rejected — the adapters emit
  uniform-in-rectangular-volume spatial sampling and will not invent
  geometry.
- **Step** is implicit — the r2s-source document is already step-pinned by
  its header; the adapter refuses if `header.step` is absent.

## Command

```
actinv export-source {openmc|mcnp|serpent} R2S_SOURCE.ndjson OUT
```

One command, format selected by the first argument, named refusals on any
input that does not parse as `actinv-r2s-source-1`.

## Emitted documents (three pinned foreign subsets)

Every emitted file begins with a provenance block in that format's comment
syntax carrying: the emitter id `actinv-source-adapter-1`, the input
document's sha256 (read from the source header), the step, total photons/s,
per-cell σ under both combination rules, and the group-count after nonzero
filtering. Uncertainty cannot ride inside the native format — it survives as
commentary plus the pointer back to the banded document.

- **OpenMC — `OUT` is an XML fragment** splicable into settings.xml: one
  `<source type="independent" particle="photon" strength="PHOTONS_S">` per
  cell, `<space type="cartesian">` with `<x|y|z type="uniform"
  parameters="lo hi"/>`, `<angle type="isotropic"/>`, and `<energy
  type="discrete"><parameters>E₁…E_N p₁…p_N</parameters></energy>` where p is
  the group's share of cell strength (sums to 1). Matches the format written
  and read by `openmc.Source.{to,from}_xml_element` (verified against
  openmc 0.15.x). Wrapped in a `<sources>` root with the provenance in an
  XML comment.
- **MCNP — `OUT` is an SDEF deck** (include-file text): per cell,
  `SDEF X=D_x Y=D_y Z=D_z ERG=D_e WGT=w` with `SI_x L lo hi` / `SP_x 0 1`
  (uniform spatial distribution on the cell bounds), `SI_e L E₁…E_N` /
  `SP_e p₁…p_N` (discrete energies at group centroids, probabilities
  normalized). `WGT` is the cell's share of total strength; the physical
  normalization is stated in the comment block (`set srcrate`-equivalent —
  photons/s per cell is the user's normalization). Isotropic direction is
  the MCNP default for a position-distributed source without `DIR` — that
  default is the documented subset, not a card we emit.
- **Serpent — `OUT` is a `src` card block**: per cell, `src <id> p sx lo hi
  sy lo hi sz lo hi sw WGT sb NE 0 E₁ F₁ … E_N F_N` — `p` selects the photon
  particle, `sx/sy/sz` give uniform-in-box spatial sampling, `sw` the
  relative source weight, `sb NE 0` the INTT=0 line spectrum at group
  centroids with group shares as intensities. (Serpent 2 syntax manual,
  `src` card; INTT=0 is the discrete-line interpolation.)

Common rules:

- Only groups with `photons_s > 0` are emitted; the emitted count per cell
  is recorded in the provenance block (a cell with zero groups emits a
  `strength="0"`/zero-weight entry rather than being dropped, so cell counts
  reconcile).
- Group probabilities are the exact ratios `pᵢ = sᵢ/Σs` over the emitted
  subset — no residual fudging; their double-precision sum sits within a
  few ulp of 1.0, and each target format normalizes the distribution
  internally anyway (OpenMC `Discrete`, MCNP `SP`, Serpent `sb`).
- Non-Cartesian or absent `bounds_cm` → named refusal per cell; the run
  fails closed (no partial file).
- `photons_s = 0` cells emit a zero-strength record, not silence.

## Deliverables

1. `crates/actinv-core/src/source_adapter.rs` — the three writers plus the
   shared r2s-source parsing/validation path; `lib.rs` export; CLI arm
   `export-source` with the usage line.
2. `docs/INTERCHANGE_TRANSPORT.md` — the pinned foreign-format contract:
   the three subsets above with element/keyword tables, the documented
   defaults (isotropic direction, uniform-in-bounds sampling), the
   `actinv-source-adapter-1` emit header fields, and the statement that the
   banded document remains the sole carrier of correlated uncertainty.

## Gates

- **G0 seal** (opening): this file hashed into `results/g0_p57_seals.json`
  with artifact hashes over `source_adapter.rs`, `command.rs`,
  `INTERCHANGE_TRANSPORT.md`, `p57_artifacts.py`, `p57_case.py`, and the
  gate/checker scripts.
- **G1 mechanics**: synthetic `actinv-r2s-source-1` fixture (hand-built,
  two cells, mixed group counts including an all-zero cell and a
  non-Cartesian-bounds cell) → all three emits: well-formed (XML parses;
  SDEF/serpent blocks tokenize under a strict grammar), cell counts
  reconcile, provenance carries sha + step + σ. Rejections: wrong schema,
  missing step, missing bounds, unparseable NDJSON, bad format name.
- **G2 exactness**: an independent Python re-derivation of every emitted
  token — strengths, weights, box parameters, energy lists, probabilities —
  from the fixture bytes at machine precision; normalization-to-1 of each
  discrete/histogram block; zero-group handling; the σ commentary matches
  the source σ fields.
- **G3 demonstration**: `results/p52_r2s_source.ndjson` (the corpus mesh
  source) → all three formats; per-cell totals conserved exactly; group
  counts reconcile; the OpenMC fragment additionally parsed by
  `openmc.Source.from_xml_element` when the env's openmc is importable
  (skipped with a recorded reason otherwise — CI cannot assume openmc).
- **G4 determinism**: byte-identical reruns of all three emits.
- **G5 independent checker**: reparses the persisted G3 outputs with its own
  SDEF/XML/src parsers (not shared code), recomputes every numeric token
  from the r2s-source document, and rejects planted mutations — a strength
  scaled, a group probability flipped, a bound moved, a σ comment edited.

## Limits

- Adapter emits nominal strengths; correlated σ travels in the banded
  document and as comments — no foreign format carries a covariance, and
  none is faked.
- Uniform-in-bounds sampling only; `CEL`/`MAT` material bindings (which
  need the user's geometry) are out of scope — a parked extension needs the
  user-supplied cell map.
- The `groups` list is already binned by the mesh rebin; centroids are
  emitted as discrete lines, the honest representation — no line broadening
  is invented.
- Verification is by independent parse + arithmetic, not by running MCNP/
  OpenMC/Serpent (licenses); G3 uses the installed openmc package when
  importable for a real `from_xml_element` round-trip.
- Inbound material-card adapters (MCNP mat → actinv material) are parked —
  separate surface, needs its own protocol.

## Cost statement

Pure text emission over an NDJSON stream — microseconds per cell; the
corpus mesh (8 cells × ~30 nonzero groups) is a ~100-line file per format.
No solver path invoked, no covariance collapse — this is the approved
zero-compute lane.

## Amendments

- **A1 (post-G0, mechanical)**: CI clippy 1.98 repairs in
  `source_adapter.rs` — `!(lo < hi)` rewrote to `lo >= hi` (NaN is already
  excluded by the `is_finite` filter two lines above, so the check is
  semantically identical), and two `map(|v| fmt(v))` closures collapsed to
  `map(fmt)`. No emitted byte changes; the sealed artifact sha drifts
  accordingly and is re-recorded in `results/g0_p57_seals.json`.
- **A2 (pre-seal, noted for the record)**: the probability convention
  changed from "residual folded into the largest until the printed text
  sums to exactly 1.0" to "exact ratios `pᵢ = sᵢ/Σs`, sum within a few
  ulp" — the iterative fold does not converge in general (residual
  corrections re-associate differently under re-summation, and for
  spectra spanning ~55 decades no folding of the largest element reaches
  a fixed point). The honest contract is the true shares plus internal
  normalization by each consumer; discovered when G5's reparse flagged
  cells 5 and 7. Also discovered in the same pass: Python 3.12+ `sum()`
  is compensated (Neumaier) and differs from Rust's left-to-right fold at
  the last ulp — checkers use an explicit `naive_sum` helper. Both fixes
  were made and verified before G0 ran; they are listed here for
  completeness of the record.
