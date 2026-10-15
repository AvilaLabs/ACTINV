# Move a FISPACT-II deck to ACTINV

`actinv import-fispact` reads a FISPACT-II input deck (`.i`) and writes an ACTINV [problem specification](specification.md). It converts the problem description: the material, the neutron (or charged-particle) spectrum, the irradiation and cooling schedule, and the few output settings that have an ACTINV counterpart. It does not read FISPACT-II's nuclear data. The activation library and decay data of the converted problem are ACTINV's, so results differ from a FISPACT-II run by the difference between the two data sets as well as by the solvers.

```text
actinv import-fispact DECK.i OUT.json [--fluxes FLUXES] [--files FILES]
                      [--library LIB.npz] [--decay DECAY]
                      [--covariance COV.npz] [--strict]
```

The command writes `OUT.json` (the problem) and `OUT.import.json` (the import report, below). Run and check the converted problem in the usual way:

```text
actinv import-fispact TENDL-2017_1996exp_5min.i fe_5min.json
actinv validate fe_5min.json
actinv run fe_5min.json fe_5min_result.json
```

## What is read

| Input | Used for |
| --- | --- |
| `DECK.i` | material, flux, schedule, projectile, and the keywords listed below |
| `--fluxes FLUXES` | the group spectrum: a FISPACT-II `fluxes` file (values highest energy first, first-wall loading, title) |
| `--files FILES` | a FISPACT-II `files` file. Its `fluxes` entry names the spectrum when `--fluxes` is absent. Without `--files`, `DECK_files` and then `files` beside the deck are tried. |
| `--library`, `--decay` | the ACTINV activation library and decay data for the problem |
| `--covariance` | an MF=33 covariance sidecar paired with `--library`, used when the deck asks for uncertainties |

The `xs_endf`, `dk_endf` and other data entries of a files file are listed in the report for reference and never read. Without `--library` and `--decay` the problem uses the catalog references that `actinv new` uses (`catalog:tendl-2025-neutron-709g`, `catalog:endfb-viii-0-decay` with the JEFF-3.3 fallback, and for charged particles the matching 162-group library); install them with `actinv data fetch`. A gamma-projectile deck needs `--library`.

The group count of a `fluxes` file is taken from the file itself. ACTINV reads 709-group neutron spectra and 162-group charged-particle spectra; a `GETXS 1 N` line must agree with the file. Convert other structures to 709 groups before importing (the `GRPCONVERT` keyword is not imported).

## How the deck is read

The importer follows the FISPACT-II input rules:

- Keywords are free format and case-insensitive. Only the leading characters matter, so `HAZA`, `UNCERT` and `TOLER` are `HAZARDS`, `UNCERTAINTY` and `TOLERANCE`.
- `<< ... >>` comments may appear anywhere, including inside a line, and may span lines. A line whose first character is `*` is text (the title after `FISPACT`, and after `END`); `<<` has no meaning inside it.
- The library phase ends at `FISPACT`, the initial conditions at the first `TIME`, and the deck at `END`. Nothing after `END` is read.
- `TIME t [SECS|MINS|HOURS|DAYS|YEARS]` sets an interval; the next `ATOMS`, `STEP` or `SPECTRUM` runs it. Each run becomes one step of the ACTINV schedule. An interval that no output keyword runs is reported, and is not scheduled.
- `FLUX` sets the flux for the steps that follow. The first positive value becomes `spectrum.total`, and each step's `flux` multiplier is its flux divided by that value, so a step at `FLUX 0` is a cooling step. `ZERO` also sets the flux to zero, as in FISPACT-II.
- `PULSE n ... ENDPULSE` loops, nested to any depth, are expanded into explicit steps (at most 100000).

## Keyword translation

| FISPACT-II | ACTINV |
| --- | --- |
| `MASS kg n` and `ELEMENT percent` lines | `material` with `mass_g` and basis `wt_percent` |
| `FUEL n` and `NUCLIDE atoms` lines | `material` with basis `atoms_per_g` and explicit nuclide keys; the mass is taken from mass numbers (the atom counts are exact) |
| `FLUX`, `ZERO`, `TIME`, `ATOMS`, `STEP`, `SPECTRUM`, `PULSE` | `spectrum.total` and `schedule` |
| `PROJECTILE n` | `projectile` (1 neutron, 2 deuteron, 3 proton, 4 alpha, 5 gamma) |
| `FISPACT` title line | `title` |
| `MIND`, `NOSTABLE`, `SORTDOMINANT` | the `summary` block: `min_atoms`, `include_stable`, `dominant_top` |
| `UNCERTAINTY 1` or `2` | `uncertainty` with an MF=33 covariance (see below) |
| `NOERROR` | no `uncertainty` block |
| `GETXS`, `GETDECAY` | the library and decay references; a `GETXS 1 N` group count is checked against the spectrum |
| `HALF`, `HAZARDS`, `GRAPH`, `NUCGRAPH`, `TAB1` to `TAB4`, `NOT1` to `NOT4`, `TOLERANCE`, `PRINTLIB`, `MONITOR`, `CLOBBER` and similar | recorded as `noted`: they select FISPACT-II printing, files or solver controls and do not change the calculated physics |

`UNCERTAINTY` needs a covariance sidecar. With `--covariance FILE` it is hashed and used. With the default neutron library the catalog covariance sidecar is used and the entry is `approximated`, because its uncertainties are the TENDL-2025 MF=33 data, not those the deck's FISPACT-II library carried. With another `--library` and no `--covariance` the entry is `unsupported`. Level 3 (pathways only) produces no band.

`DENSITY` has no spec field and is recorded in the report. The `summary` block carries the summary options; a build without them rejects a spec that contains it.

## The import report

Nothing is dropped silently. `OUT.import.json` (`actinv-fispact-import-report-1`) lists every keyword with its line, the section of the deck it appeared in, its arguments, and one status:

| Status | Meaning |
| --- | --- |
| `mapped` | translated into a spec field |
| `approximated` | translated with a stated caveat, given in `detail` |
| `noted` | no effect on the calculated physics (printing, files, solver tolerances) |
| `unsupported` | would change the physics and has no ACTINV equivalent, or is not a FISPACT-II keyword (a misspelling, for instance) |

The report also holds the counts, the spectrum source and total, the library chosen, the content of the files file, the SHA-256 of the deck, and a `schedule_map` giving, for each ACTINV step, the deck line that defined it, whether it is irradiation or cooling, and the FISPACT-II output keyword that ran it. ACTINV reports every step in full, so the choice between `ATOMS`, `STEP` and `SPECTRUM` does not change what is computed.

Check every `approximated` and `unsupported` entry before trusting a converted problem. `--strict` makes the command fail, after writing the report, when any entry is `unsupported`.

## Not translated

These have no equivalent and are reported as `unsupported`: `POWER` and `WALL` flux targets, `PARTITION`, `OVER` data overrides, the self-shielding keywords (`PROBTABLE`, `SSF*`; ACTINV's `self_shielding` takes a Bondarenko table instead), `GRPCONVERT`, `BREMSSTRAHLUNG`, displacement-damage keywords (`ALLDISPEN`, `ATDISPEN`; see the `damage` option), `USEFISSION` and the fission-yield keywords (use `fission_yields.files`), targeted pathway queries (`PATH`, `ROUTES`, `RESULT`, `SENSITIVITY`), `IRON`, point-source `DOSE`, and a `GETXS` that changes the spectrum partway through the run. A material defined after the first `TIME` is not representable. Triton and helion projectiles stop the import.

## Convert a directory of decks

`controls/p132_import_all.py` imports every `*/*.i` deck under a directory with the release binary and prints the status counts per keyword:

```text
cargo build --release -p actinv-cli
python3 controls/p132_import_all.py ~/nuclear-data/conderc-fns/fns --out /tmp/p132-imports
```

It passes the library, decay and covariance files named by `ACTINV_LIBRARY`, `ACTINV_ENDF_DECAY` and `ACTINV_COVARIANCE`, when set, and otherwise lets the importer use the catalog references.
