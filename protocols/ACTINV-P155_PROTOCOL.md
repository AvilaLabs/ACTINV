# P155 — FISPACT-II importer sweep: fission, photon, damage, uncertainty and self-shielding keywords (tracker rows 8, 17)

Registered 2026-10-27, before any source change for the phase. Second parity wave; manual reading in
`docs/maintainers/FISPACT_MANUAL_KEYWORD_AUDIT.md` (P151). Depends on P152 (photon estimate, `exclude_xrays`), P153
(fission-yield selection) and P154 (native damage energy) being merged first; the builder starts from a master that
holds all three.

## Change under test

`actinv import-fispact` still reports about twenty keywords `unsupported` because, when the importer was written,
ACTINV had no spec field for them or the deck does not name the hash-pinned data the field needs. P152–P154 added
the fields; this phase maps the keywords and adds importer options that supply the missing data. Keyword strings
appear only in the importer and its report. Behaviour is taken from the manual sections listed in the audit and
described in our own words.

1. **New importer options**, each taking files that the importer hashes into the spec:
   `--fission-yields FILE...` (ENDF neutron-induced yield files), `--damage TABLE` (a damage table, e.g. from
   `actinv build-damage --method kinematic`), `--self-shielding-table FILE` (Bondarenko table), `--resonance FILE...`
   (ENDF resonance evaluations for geometry self-shielding). Without them every import is byte-identical to v1.6.1.
2. **Fission.** `USEFISSION` with `--fission-yields` fills `fission_yields.files`; without the option it stays
   unsupported and the report names the option. `NOFISS` forces no yield files. `CUMFYLD` → `fission_yields.yields:
   "cumulative"`. `FISYIELD n` → `fission_yields.parents` (`n > 0` include, `n < 0` exclude, `n = 0` exclude every
   parent with a yield file). With `USEFISSION` and no `FISYIELD`, the FISPACT-II default parent set (U-235, U-238,
   Pu-239) is mapped as an include list, intersected with the supplied yield files; dropped names are reported.
   `FISCHOOSE` → `pathways.fission_parents`. All **mapped**.
3. **Photon.** `NOXRAY` → `photon.exclude_xrays: true` (mapped). `SPEK` (now noted as library preparation) →
   `photon.missing_spectra: "estimate"` (approximated: ACTINV's estimate carries certified dose bounds and uses the
   mean energy per decay mode; the report says so). Either adds `photons` to `options.outputs` when absent and
   reports it. `READGG` → `photon.group_boundaries_eV` read from the files file's `ggbins` entry (mapped when the file
   parses, unsupported naming the problem otherwise).
4. **Damage.** `ALLDISPEN` / `ATDISPEN` (per-element values override the all-element value regardless of order) →
   `damage.displacement_energy_eV` over the deck's elements, with `damage.table` from `--damage`. Mapped when the
   table's recorded displacement energy for each element equals the deck's; otherwise unsupported, with the
   `actinv build-damage --method kinematic --displacement-energy EL=E` command that would build a matching table.
   Without `--damage`, unsupported naming the option.
5. **Uncertainty.** `UNCTYPE 1|2|3` → `uncertainty.channels` (`cross_section_mf33`, `decay_constants`, both):
   mapped. `COVARIANCE` → noted (ACTINV propagates the full MF=33 covariance; there is no collapse step).
   `SENSITIVITY SIGMA` → `uncertainty.voi` (approximated: first-order ranked variance shares in place of sampled
   sensitivity coefficients); `SENSITIVITY LAMBDA` unsupported. `MCSAMPLE` / `MCSEED` → noted when `SENSITIVITY` is
   mapped (sampling replaced by the analytic first-order result), unsupported otherwise. `ERROR` → per-reaction
   `overrides` `cross_section_uncertainty` for a non-negative fractional error, read as a relative standard
   uncertainty (approximated, interpretation stated in the report); `-1` (library value) → noted.
6. **Self-shielding.** `SSFGEOMETRY` (+ `SSFCHOOSE`, `SSFMASS`, `SSFFUEL`) with `--resonance` →
   `self_shielding.geometry` (shape and lengths from the deck, density from `DENSITY`, resonance files from the
   option): mapped for the shapes ACTINV has, unsupported for others. `PROBTABLE` with `--self-shielding-table` →
   `self_shielding.table` (approximated: Bondarenko factors in place of probability tables). `SSFDILUTION` with
   `--self-shielding-table` → `self_shielding.dilution_overrides`, group index converted to the table's convention
   (mapped). Without the options, unsupported naming the option.
7. **Corrections.** `XSTHRESHOLD` → noted (a threshold for FISPACT-II's own pathway uncertainty, which ACTINV
   replaces with full covariance propagation). `DEPLETION` → noted (an output request; per-step atoms are in every
   result step). `RESULT` → noted when the deck defines the inventory itself, unsupported otherwise. `IRON`'s reason
   string no longer describes a behaviour: the 2023 manual has no such keyword. `READSF`, `USESPALLATION`,
   `USEXSEXTRA`, mid-schedule `GETXS` / `GETDECAY` and the `OVER ADCROSS` / `ADLAM` conversions stay unsupported with
   their reasons.

Beyond FISPACT-II: the importer hashes every supplied file into the spec, refuses a damage table whose displacement
energies disagree with the deck instead of silently mixing them, and reports each approximation in the import
report.

## Gates

Checker: `controls/check_p155.py` (with `--self-check`), written from this protocol and the documented options without
reading the implementation. Reference: v1.6.1 release (SHA-256
`79e1e8600a8c2ad3ce42e72dcf34ce4dd8fa9bf60288db24e9eeb077781c9072`). Data: the 132 FNS decks (as P132), the five
pypact reference decks and one example deck under `~/nuclear-data/ext-decks/pypact`, the two CONDERC fission decks,
ENDF/B-VIII.0 neutron fission yields, a P154 kinematic damage table, the TENDL-2025 activation library, all
hash-pinned and not tracked.

- **G0.** This protocol's SHA-256 is in `protocols/protocol_hash.txt`.
- **G1.** fmt, clippy `-D warnings`, workspace tests including a test per mapped keyword and per refusal.
- **G2, defaults unchanged.** All 132 FNS decks (which use none of the keywords above) import to specs byte-identical
  to the reference's, with the same report statuses. The pypact and CONDERC decks imported without the new options
  differ from the reference only in spec fields and statuses set by keywords of items 2–7 that need no option (e.g.
  `SPEK`, `READGG`, `XSTHRESHOLD`); every difference is listed and attributed to its keyword.
- **G3, keyword mapping.** For each keyword in items 2–7, a synthetic deck (generated by the checker) imports to the
  spec fragment this protocol states, with the stated status; the per-element override order of `ALLDISPEN` /
  `ATDISPEN` holds both ways; `FISYIELD` with positive, negative and zero counts gives the stated selections.
- **G4, imported specs run.** For the fission, photon and damage cases, the imported spec runs, and its result is
  byte-identical (apart from `ms`) to a hand-written spec with the same fields; each supplied file's SHA-256 in the
  spec equals the checker's own hash.
- **G5, corpus.** The two CONDERC fission decks, with `--fission-yields`, import with no unsupported physics keyword
  and their specs run; the pypact decks import with no unsupported keyword except those item 7 keeps.
- **G6, refusals.** Each missing-option case, a damage table with a mismatched displacement energy, an unparsable
  `ggbins` file and `SENSITIVITY LAMBDA` are reported unsupported with the stated reason, and `--strict` exits nonzero
  and writes nothing.
- **G7.** The scorecard (`COMPETITIVE_BENCHMARK.md`) and the handbook's import page list the new options and statuses;
  the handbook walk-through (`check_p140_docs.py`) passes.
- **G8.** CI green on the pushed commit; committed baselines unchanged.

## Verdict

P155-PASS if G1–G8 hold. A failing gate is preserved under `results/failures/p155/`; one repair round, registered as
an amendment first.

## Not claimed

No FISPACT-II run is compared; the gates check that the importer writes the fields this protocol names and that
ACTINV runs them. The physics of the mapped fields is P152–P154's. Spontaneous-fission yields (`READSF`), spallation
and extra cross-section streams, mid-run library changes and FISPACT-II error-factor conversions remain outside the
importer.
