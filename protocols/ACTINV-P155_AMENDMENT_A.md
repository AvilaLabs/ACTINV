# P155 Amendment A — dispositions the registered text could not state exactly

Registered before any evidence run of P155, after the candidate's unit tests and before the checker has been run on
any binary. No gate is removed or loosened beyond what is stated; where the registered text could not be followed as
written, the reason and the replacement are given.

## A1. `UNCTYPE 2` is approximated, not mapped

Item 5 maps `UNCTYPE 1|2|3` onto `uncertainty.channels` as mapped. ACTINV propagates the MF=33 covariance whenever
`uncertainty.covariance` is present, whatever `channels` lists, and the spec omits the covariance only for the
flux-only channel set (P93); the decay-constant channel is built inside the covariance path. A half-life-only band
therefore cannot be expressed: `UNCTYPE 2` gives `channels: ["decay_constants"]` with the cross-section part still
propagated, reported **approximated** with that reason. `UNCTYPE 1` and `3` stay mapped. G3 checks this status.
A half-life-only band without MF=33 is a run change outside this phase.

## A2. Displacement energies the deck does not give

FISPACT-II computes displacement damage without any keyword, from its own default displacement energies (25 eV for
elements its table does not list). Item 4 is completed accordingly:
- With `--damage` and neither `ALLDISPEN` nor `ATDISPEN`, the importer writes the `damage` section with, per element,
  the energy the table was built with (table 2) or FISPACT-II's default (table 1, which records none). The report
  carries one `DISPLACEMENT` entry, mapped when those energies equal FISPACT-II's defaults and approximated otherwise.
- An element of the material the deck's keywords do not give takes the same value, and the entry says so; an element
  the deck gives that the material lacks is not written, and the entry says so.
- The mismatch refusal applies to energies the deck gives. The command it prints repeats
  `--displacement-energy EL=eV` once per element, which is the `build-damage` syntax.

## A3. Clarifications (no gate change)

- `READGG` writes `photon.group_structure: "custom"` beside `photon.group_boundaries_eV`; a `GROUP` entry it
  supersedes becomes noted.
- `SPEK` and `NOXRAY` add `photons` to `options.outputs` only when the imported spec has an outputs list; the importer
  writes none, and without a list the run reports photons by default.
- `FISCHOOSE` is mapped when the deck's pathway keywords create a `pathways` section, and noted otherwise.
- `SENSITIVITY SIGMA` creates the uncertainty section itself (with `voi.top = 10`) when no `UNCERTAINTY` keyword did.
- `--fission-yields` is used only when the deck asks for fission (`USEFISSION`); otherwise the files are listed under
  the report's `options_unused`.
- A deferred keyword inside a `PULSE 0` body (never executed) is noted as skipped instead of carrying the placeholder
  "resolved when the deck ends".
