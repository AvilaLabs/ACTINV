# ACTINV transport interchange contract (P57)

This document pins how an `actinv-r2s-source-1` banded photon source crosses
into the formats the transport codes natively read. The ACTINV side owns the
mesh solve, the banded interchange document, and the emitter id
`actinv-source-adapter-1`; the foreign formats own nothing but their own
syntax. Uncertainty does not cross: no target format carries a covariance —
the banded document stays the sole carrier, and every emitted file embeds the
per-cell σ as comment lines plus the input sha256 pointer.

Command:

```
actinv export-source {openmc|mcnp|serpent} R2S_SOURCE.ndjson OUT
```

## Shared emit rules

- Input is `actinv-r2s-source-1` NDJSON (from `actinv export-r2s`). Header
  must carry `step`; the file's sha256 is embedded in the output provenance.
- Every cell needs `bounds_cm` as three finite `[lo, hi]` pairs with `lo <
  hi` — the emitted spatial sampling is uniform-inside-bounds and nothing
  else is invented. A non-Cartesian or missing bound fails closed.
- `groups` are filtered to `photons_s > 0`; the emitted per-cell count is
  recorded in the provenance block. A zero-strength or zero-group cell emits
  a syntactically complete zero-weight entry — never silence.
- Group probabilities `pᵢ = sᵢ/Σs` are the exact ratios — no residual
  fudging; the double-precision sum sits within a few ulp of 1.0, and
  every target format normalizes internally.
- All numbers are Rust shortest-round-trip float formatting (equivalent to
  Python `repr`).
- Direction is isotropic — the documented default in all three targets;
  no anisotropy card is emitted.

## OpenMC — XML fragment (`OUT` is `<sources>`-rooted XML)

Each cell emits:

```xml
<source type="independent" particle="photon" strength="PHOTONS_S">
  <space type="cartesian">
    <x type="uniform" parameters="xlo xhi"/>
    <y type="uniform" parameters="ylo yhi"/>
    <z type="uniform" parameters="zlo zhi"/>
  </space>
  <angle type="isotropic"/>
  <energy type="discrete"><parameters>E1…EN p1…pN</parameters></energy>
</source>
```

- `strength` is the cell's absolute `photons_s` (OpenMC normalizes the
  source mixture; the absolute scale is the per-element value).
- `<energy type="discrete">` parameters are the group centroids (eV)
  followed by the probabilities — x-list then p-list, matching
  `openmc.stats.Discrete.to_xml_element`.
- The fragment is spliceable into settings.xml, or loadable directly via
  `openmc.IndependentSource.from_xml_element(elem)` — verified against
  openmc 0.15.x in G3.
- Provenance: a single XML comment under `<sources>` carrying
  `actinv-source-adapter-1`, input sha256, step, total photons/s, and the
  per-cell σ (`sigma_independent`, `sigma_conservative`).

## MCNP — SDEF deck (plain-text include)

Each cell emits four distribution indices (sequential per cell: `base =
1 + 4·i`):

```
SDEF X=D_b Y=D_b+1 Z=D_b+2 ERG=D_b+3 WGT=w
SI_b   H xlo xhi          SP_b   0 1
SI_b+1 H ylo yhi          SP_b+1 0 1
SI_b+2 H zlo zhi          SP_b+2 0 1
SI_b+3 L E1…EN            SP_b+3 p1…pN
```

- `SI H`/`SP` gives a uniform-in-interval coordinate distribution;
  `SI L`/`SP` gives a discrete-value distribution with per-point
  probabilities. Energies are **MeV** (centroids arrive in eV and are
  divided by 10⁶).
- `WGT` is the cell share of total strength; absolute photons/s per cell is
  the user's normalization (`srcrate`) — the provenance block carries it.
- Direction is isotropic — the MCNP default for a position-distributed
  source without `DIR`.
- Zero-group cells emit `SI L 1000000`/`SP 1` with `WGT=0` — never sampled.
- Provenance: `c` comment block — same fields as OpenMC.

## Serpent 2 — `src` card block

Each cell emits:

```
src <name> p sx xlo xhi sy ylo yhi sz zlo zhi sw WGT sb NE 0 E1 p1 … EN pN
```

- `p` selects photons; `sx/sy/sz` give uniform-in-box spatial sampling;
  `sw` the relative source weight (cell share of total).
- `sb NE 0` is the INTT=0 line spectrum (Serpent input syntax manual,
  `src` card) — one `E p` pair per group; energies in **MeV**.
- `<name>` is `s_<sanitized-cell-id>_<ordinal>` (non-alphanumeric → `_`,
  leading digit prefixed) — unique per cell.
- Zero-group cells emit `se 1.0` (a single 1 MeV line) with `sw 0`.
- Direction isotropic by Serpent default (no `sd`/`sa`).
- Provenance: `%` comment block — same fields.

## Limits (contractual, not optional)

- Only nominal strengths cross. `sigma_photons_s_independent` /
  `sigma_photons_s_conservative` appear verbatim in the provenance comment;
  a tool consuming the foreign file that wants the band must read the
  banded document.
- `CEL`/`MAT`/material-bound spatial sampling needs the user's geometry —
  outside this contract; uniform-in-`bounds_cm` is what a mesh tally
  *means*.
- Group centroids emit as discrete lines — the mesh rebin already binned;
  no broadening kernel is invented.
