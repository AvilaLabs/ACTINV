# ACTINV v1.7.0 — release notes

ACTINV 1.7.0 adds impurity screening for fusion materials. `actinv impurities` answers which impurities of an alloy
matter, at which cooling times, for which concern, and how much of each can be tolerated, from one command and one
input, and with a `sampling` block it bounds every answer over the nuclear-data uncertainty. The data catalog now ships a damage table, so dpa works with no setup. FISPACT-II parity grows: estimated photon
spectra, fission-yield selection and cumulative yields, more of a deck imported, and a damage-energy builder that needs
no processed MT=444. `actinv new` starts a problem from a cited material library and standard spectra. The desktop
and browser app is reorganised into numbered steps and now reaches most of the command line: an Export page that writes
a report page, charts and tables, and pages for impurity screens, certification, optimization, comparison, waste
questions, transport meshes and measurements. The additions are new commands and opt-in options; the changes that can
move a number you have are listed under "Results to re-check".

Upgrade the Python package with:

```bash
python -m pip install --upgrade actinv
```

Rust CLI users can install this exact version with:

```bash
cargo install --locked --force actinv-cli --version 1.7.0
```

## Results to re-check

- **`pathway_closure`.** It now counts only populations above 1e-12 of the step's largest, the noise cut the code
  already applied to nuclides without chains (P161). Residues of the rational decay approximation had made the quick
  start (`examples/fns_fe_5min.json`) report 4.117 (Mn-58 at 2.7e-28 atoms/g); it now reports 3.9e-15. No other
  result field changes.
- **Measurement design in `actinv decide`.** `design.top_reactions` now computes covariance blocks that are only
  positive semi-definite, which the old solve reported as `ill_conditioned` (P164). On the iron decision example every
  block was null before; Fe-56 and Fe-54 now head the list. Blocks the old solve computed keep their values.
- **Damage tables you build.** `build-damage --method kinematic` takes NJOY2016 HEATR's per-element displacement
  energies by default instead of a flat 25 eV, and counts arc-dpa displacements at the reference energy of the
  Nordlund et al. (2018) fit. It also keeps Reich-Moore resonances of opposite AJ sign in separate channels, as NJOY
  RECONR does, which changes tables built from TENDL-2025 Al-27, Cl-37, Mn-55 and Si-29. Tables built before 1.7.0
  are unchanged; rebuild one if you want the new defaults and the fix.

## What changes

### Impurity screening (P157)

`actinv impurities SPEC.json [OUT.json]` solves each element of the alloy once as a pure material and forms the
activity, heat, clearance-index and waste-class table sums of any composition as a weighted sum of those runs. For each
target step and endpoint it reports the matrix alone, the nominal composition and the worst case over the impurity
ranges you declare, each impurity's gradient and contribution with its top product nuclides, a ranking, and with a
`limit` each impurity's limit alone and the joint scale factor. The Part 61 class is given at nominal and over the
ranges. Every reported composition is re-solved in full, and `--no-verify` skips that. See
[Screen a material's impurities](guide/impurities.md) and the worked example `examples/impurities_ra_steel/`, whose
values are illustrative. The photon response that example names, `photon-response/nist-xcom-all.json`, is built with
`scripts/build_photon_response.py`; no data catalog ships it.

### Impurity screening under nuclear-data uncertainty (P158)

Add a `sampling` block (samples, seed, channels and covariance, as `actinv waste budget uncertainty` takes them) and a
`confidence_level`, and the screen is repeated over correlated draws of the cross sections and decay constants. Each
value gets an upper bound and each limit a lower bound, as exact order-statistic tolerance bounds at the confidence
level, and the waste class gets the probability of meeting the target class with a Clopper-Pearson lower bound. At
least 72 samples are needed at 0.95. The samples behind every bound are re-solved in full. The worked example
`examples/impurities_ra_steel/impurities_uncertainty.json` runs 72 samples in about 45 minutes on one laptop core; its
steel meets the 1.4e-2 W/g heat limit at 1 d of cooling with the nominal data, but exceeds it in 30 of the 72 samples
even without impurities. See [Add nuclear-data uncertainty](guide/impurities.md#add-nuclear-data-uncertainty).

### Damage tables in the catalog (P156)

Data catalog 1.2.0 adds the artifact `tendl-2025-neutron-damage`, a kinematic damage table on the 709-group structure
for the stable and long-lived TENDL-2025 neutron targets, referenced by the four neutron bundles. `actinv new` writes a
`damage` section that names it, and `actinv import-fispact` uses it for decks that give `ALLDISPEN` or `ATDISPEN`.
`actinv data fetch` copies a file already present under an older version directory when its SHA-256 matches. A
Reich-Moore reconstruction fix keeps resonances of opposite AJ sign in separate channels; the shipped v1.1.0 activation
libraries predate it and are not rebuilt.

### FISPACT-II parity (P152-P155)

- `photon.missing_spectra: "estimate"` (`SPEK`) and `photon.exclude_xrays` (`NOXRAY`) are opt-in. A nuclide with a mean
  electromagnetic energy and no photon spectrum gets an exponential spectrum, and the point-source dose is given with
  lower and upper bounds that hold for any spectrum of the same power below the cap (P152).
- `fission_yields.energy: "rate_weighted"`, `fission_yields.parents` and `fission_yields.yields: "cumulative"`, and
  `pathways.fission_parents`, are opt-in (P153).
- `actinv build-damage --method kinematic` computes damage energy from the evaluation's own distributions (ENDF MF=3,
  MF=4, MF=6) through the Lindhard-Robinson partition. A run with the resulting table-2 file adds exact-NRT and
  arc-dpa displacement outputs and, with gas output, `he_appm_per_dpa` (P154).
- `actinv import-fispact` gains `--fission-yields`, `--self-shielding-table`, `--resonance` and `--damage`, and maps
  the fission, uncertainty, self-shielding, photon and damage keywords listed in the [changelog](../CHANGELOG.md)
  (P155).

### New problems from a material library (P162)

`actinv new OUT.json --material ID --source SHAPE --flux F [--irradiate DURATION]` writes 1 g of a library material
under a standard spectrum at total flux F (neutrons per cm² per s), irradiated for DURATION (default 1 y) and cooled to
1 h, 1 d, 30 d, 1 y and 10 y after shutdown. `actinv new --list` prints the choices. The library has 19 cited
compositions of common reactor and fusion materials, each marked as a nominal composition, specification maxima or a
measured heat, with its density and a note naming the trace elements it leaves out. The four shapes (`fusion-14mev`,
`fission`, `thermal`, `one-over-e`) are textbook spectra on the 709-group structure, not a facility's spectrum.

### Measurement design (P164)

`actinv decide` ranks which measurements would narrow an answer. Its per-reaction blocks used a Cholesky solve, which
fails on the positive semi-definite blocks MF=33 often gives, and nulls ranked as zero, so on
`examples/decide/decision.json` Ca-43 to Ca-47 headed the list for 1 g of iron. A pseudo-inverse with a range check
computes those blocks, and computed blocks rank first.

### Report page and help (CLI)

`actinv report RESULT.json --format html [--problem PROBLEM.json] [--out DIR]` writes the report page the Export page
writes, and with `--out` one SVG per chart beside `report.html`. `--tables`, `--step` and `--nuclides` are refused
with `html`. Bare `actinv` and `actinv help` group commands by task. `actinv waste budget` writes its result and exits
3 when a verification point misses, as `actinv budget` does, and `actinv decide` resolves `catalog:` data names.

## Desktop and browser

Desktop `0.6.0-preview.1` groups pages as Start, Set up, Results, Studies and Tools. **Start** asks what you want to
find out and opens the page that answers it; **New problem** starts empty and three cards fill it from the material
library, a standard spectrum and a standard schedule. Five setup steps end in **Check & run**, which lists every
setting you changed with an Edit button, and Validate and Run show their outcome beside the buttons. The result pages
open on Summary, and a new **Export** page saves a report page, SVG charts, the tables of `actinv report` and the result
and problem files, into a new folder on the desktop and one zip in the browser.

Under Studies, new pages run the command-line studies from the open problem: **Impurities** (`actinv impurities`, with
optional bounds), **Certify** (`actinv decide`), **Optimize** (`actinv optimize`), **Compare** (`actinv study run`),
**Mesh & transport** (import a transport flux, activate every cell, write the shutdown-dose source for OpenMC, MCNP,
Serpent or ALARA, and check which cells clear their limits) and **Measurements** (fit the flux to measured activities,
or narrow the bands with a measured value). **Waste class** asks which of four waste questions you have. The nuclear
data move to a **Data** page under Tools, which verifies the installed files. The browser sets all of these up and
reads their results but does not solve; its Start page opens two results solved ahead of time. Results are
unchanged. See the [desktop 0.6.0-preview.1 release](releases/desktop-v0.6.0-preview.1.md) and
[Versions and releases](guide/releases.md).

## Known limitations

All limits in the [qualification boundary](guide/qualification.md) and
[data limitations](DATA_LIMITATIONS.md) still apply. In particular:

- Impurity screening is exact only where the response is linear in composition, so it refuses `self_shielding`,
  `options.screen`, a schedule `feed`, power-driven steps and an `uncertainty` section.
- The nuclear-data bounds cover only the sampled cross sections and decay constants. Composition, geometry, flux-shape
  and model uncertainty are not sampled, and measured coverage of these channels on the FNS corpus is below nominal,
  so a bound can be optimistic.
- The impurity example's values are illustrative, not a material specification, a design limit or a regulatory
  position.
- The estimated photon spectrum of `SPEK` is a mean-energy shape by decay mode; use the reported bounds, not the
  estimate, where the dose must not be under-stated.

Each verdict cited above records its exact scope; the [changelog](../CHANGELOG.md) has the full list of changes.
