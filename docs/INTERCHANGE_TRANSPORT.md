# ACTINV transport interchange contract (P57/P102)

This document pins how an `actinv-r2s-source-1` banded photon source crosses
into the formats the transport codes natively read. The ACTINV side owns the
mesh solve, the banded interchange document, and the emitter id
`actinv-source-adapter-1`; the foreign formats own nothing but their own
syntax. Uncertainty does not cross: no target format carries a covariance —
the banded document stays the sole carrier, and every emitted file embeds the
per-cell σ as comment lines (or, for ALARA, in the provenance sidecar) plus
the input sha256 pointer.

Commands:

```
actinv export-source {openmc|mcnp|serpent} R2S_SOURCE.ndjson OUT
actinv export-source alara R2S_SOURCE.ndjson OUT_DIR --shutdown-t-s T
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

## ALARA — one `.photonSrc` file per cell, `OUT` is a directory (P102)

`nucleide` (nukehub-dev/nucleide) and PyNE build rigorous two-step (R2S)
shutdown-dose workflows around ALARA, and both read ALARA's plain-text
photon-source file directly — no new parser needed on their side. ALARA
writes one row per (nuclide, decay time): tab-separated
`<nuclide>\t<time>\t<s_1>…<s_G>`, where `<time>` is `shutdown` or `<value>
<unit>` (unit `s`). PyNE's `alara.photon_source_to_hdf5` splits on `\t` and
counts groups from line 1; `nucleide`'s `alara-io::photon` reader splits on
whitespace and does not track element identity across rows.

- **One file per cell, `TOTAL` row only.** `OUT/<ordinal>_<id>.photonSrc`
  carries exactly one row, `TOTAL\t<time>\t<densities…>`. `<id>` is the
  cell id sanitized to ASCII `[A-Za-z0-9._-]` — every other **byte** (not
  character; a multi-byte UTF-8 character becomes one `_` per byte) becomes
  `_`. `<ordinal>` is zero-padded to the width the cell count needs, so file
  names sort in document order. A multi-cell file of `TOTAL`-only rows
  would read as element index 0 under PyNE's rule for every cell, and
  `nucleide` cannot split one file into per-cell zones at all — one file
  per cell is the only representation both readers parse correctly.
  `actinv-r2s-source-1` carries per-cell photon totals, not per-nuclide
  group spectra, so only `TOTAL` rows are emitted — a documented limit, not
  an omission.
- **Densities, not totals.** Each group value is `photons_s_g /
  volume_cm3` (photons·s⁻¹·cm⁻³), matching what ALARA itself writes. A cell
  with absent, non-finite, zero or negative `volume_cm3` is refused by
  name; the run fails closed with no output directory created at all.
- **Group grid.** The grid is the union of `centroid_eV` over every cell's
  nonzero groups (the same `photons_s > 0` filter the OpenMC/MCNP/Serpent
  emitters use), sorted ascending, exact float equality defining "same
  group". Every file from one document carries the full grid, `0` where a
  cell has no group there, so file byte-length and group count `G`
  reconcile across every file. The grid is recorded in the index (below)
  so the user can write the matching ALARA `photon_source` energy bounds —
  no bounds are invented, since the interchange document carries centroids
  only.
- **Decay time.** `--shutdown-t-s T` is required; cooling = `step_t_s − T`.
  Every cell's `step_t_s` must be finite and identical (one irradiation-step
  basis); unequal values refuse the run by name. Negative cooling refuses.
  `cooling == 0` writes `shutdown`; otherwise `<cooling> s` with the number
  in Rust `{}` formatting. Without `--shutdown-t-s`, the run refuses,
  naming the missing flag — ACTINV does not guess where shutdown is.
- **Provenance sidecar, not in-file comments.** In-file comments would
  break PyNE's reader, so `OUT/actinv-alara-index.json` carries the emitter
  id, `input_sha256`, `step`, `step_t_s`, `shutdown_t_s`, `cooling_s`, the
  time token, `units` (`"photons/s/cm3"`), `group_order`
  (`"ascending centroid_eV"`), `group_centroids_eV`, and one entry per cell
  (file name, id, ordinal, `bounds_cm`, `volume_cm3`, `photons_s`, both σ
  fields — `null` when the input cell carries neither — the nonzero group
  count, and the file's own sha256). The banded document remains the sole
  carrier of correlated uncertainty, as in the OpenMC/MCNP/Serpent emitters.
- **Zero-strength cells** write a row of zeros, never silence, so file
  counts always equal cell counts.
- `OUT_DIR` must not exist, or must exist and be empty; otherwise the run
  refuses before writing anything, so no file is ever overwritten.

Verification is by the consumers' own parsers — `nucleide` 0.16.0 (pinned;
later versions are a re-run, not a claim; the package is pre-alpha) and a
parse applying PyNE's `photon_source_to_hdf5` splitting/indexing rules — not
by running ALARA or a transport code. See `contrib/nucleide_r2s/` for a
working round-trip demo.

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
