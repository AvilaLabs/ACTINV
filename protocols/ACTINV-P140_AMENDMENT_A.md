# P140 Amendment A — repairs of the H1 findings

Registered 2026-10-15, after the H1 reproductions and before any repair. Base: `233e3e8` plus test-only changes.
Candidate before repair: `cand9`, release build of `233e3e8`, SHA-256 `0b62b47c…1170` (identical to `cand8`).

## Evidence before repair

- Seven independent reviews (one per module group, none by an implementer) produced 90 findings, recorded with the
  coordinator's decisions in `results/p140/h1/`.
- Every accepted finding that changes behaviour was reproduced by a regression test written to assert the correct
  behaviour, run on the unrepaired code: 107 tests named `p140_*` fail, each with the failure message of its defect,
  and the 5 guard tests pass (`results/p140/h1/red_run.txt`). Three defects were also reproduced with the `cand9`
  binary (`results/p140/h1/repro/`): scheduled removal absent from the depletion table (depleted fraction 0.632,
  no channel, `removal_rate_per_s` −0.0); trace-analytic depletion fractions off by 2.6e-5 relative on FNS Fe.
- Rejected: depletion uncertainty missing in trace mode (the `cand9` trace run carries the same channel
  uncertainties as coupled); one-ulp histogram edge display; single-letter `TIME` units in the importer (an
  unknown unit already yields an `unsupported` entry that `--strict` sees).
- H2 passed on `cand9` (151 cases: 133 pass, 18 documented refusals). H4 passed on `cand9` (85 of 85) after two
  checker defects were corrected (output directories were not created; `tab3` was expected from A2-only
  responses). Both records are kept; H2–H4 are rerun on the repaired candidate.

## Repairs

Each repair makes its `p140_*` tests pass without weakening them, except where stated. Defaults stay byte-identical
for specs that use none of the affected options.

1. **Depletion** (`depletion.rs`, `run.rs`, `report.rs`). A declared `removal` becomes a channel of kind `removal`
   in the table, total, shares and analytic exponent; trace-analytic fractions use `expm1` and a cumulative
   exponent; an excluded covariance block yields no uncertainty instead of 0; a NaN variance yields none;
   `uncertainty_covered_share` is omitted when nothing is removed; no negative zero. `actinv report` gains a
   `depletion` table, a TAB2 note for trimmed rows, and names a requested nuclide that the summary trimmed.
2. **Photons and regulatory tables** (`photon.rs`, `radiological.rs`, `spec.rs`, `scripts/regulatory/`).
   Bremsstrahlung uses f = Σ BR of β⁻ modes electrons per decay at E0 = ER/f, a nuclide without β⁻ branching is a
   missing input; completeness fields (zero-edge energy share, energy closure); a point-source dose is withheld
   when gamma power is unrepresented and for unusable distance or mass; photon extras without a photon output and
   unknown bundled selectors are refused at validation. Tables gain optional per-response `unlimited` and
   `covered_by_parent` lists (format unchanged, user tables keep their hashes); the converters emit them and the
   bundled tables are regenerated with new hashes. For 49 CFR 173.435 A1/A2 the progeny lists are taken from the
   same CFR's 173.436 entries for the same parents, recorded in the sidecar.
3. **Schedule controls and power** (`spec.rs`, `flux.rs`, `overrides.rs`, `power.rs`, `kerma.rs`, `run.rs`). A
   rebin that lands no flux is refused; rebin limits outside the spectrum are refused at validation; zero wall
   loading needs no spectrum total; the 4.44e13 fallback is refused for non-neutron projectiles; `kerma` is refused
   in decay-only specs; an absolute override is checked only against spectra that irradiate; an empty partition is
   no partition; a partition acts on bulk isotopes outside the chain; `mode_forced_coupled` only when trace would
   have run; placeholder spectra no longer appear in messages; kerma coverage is judged on chain states; power
   errors distinguish negative, non-finite and vanishing heating and name the refinement; damage rows without a
   heating row are refused.
4. **Shielding, chain, inspect, workflow** (`shielding.rs`, `resonance.rs`, `chain.rs`, `spec.rs`,
   `spallation.rs`, `inspect.rs`, `workflow.rs`, `run.rs`). Resonance lines are clipped to each range's [EL, EH]
   and the dropped levels counted (this changes W-186 geometry factors above 10 keV; the P139 checker is rerun on
   the repaired candidate and its committed result is regenerated); every named neutron structure is accepted for
   step spectra; an element `choose` reports isotopes without files as uncovered; multi-isotope MF=2 is keyed by
   isotope; the geometry density defaults to the material density and a disagreement is refused; certificate
   text names LRF=7; step-library ledgers list material nuclides without rows; the splice reports untouched
   nonzero rows; `inspect` emits object index fields, knows every named structure, compares spectrum boundaries
   at the solver's tolerance, honours or refuses `rebin`, and rejects ZA-shaped indices; `resolve_inputs` and
   `check_files` cover every path-bearing field; β-delayed fission (RTYP 2.6) is not spontaneous fission.
5. **Pathways and sampling statistics** (`pathways.rs`, `sample_stats.rs`, `study.rs`, `run.rs`). One value has
   no standard deviation; `first_order` is reported only when it is comparable; correlations and bootstrap need
   n ≥ 3; percentile ranks use exact arithmetic; the route search does not let one source or tiny target starve
   others and reports truncation and cap drops per target; SF and feed edges are labelled; trace production fed
   by decay of radioactive bulk nuclides is attributed or flagged in the closure.
6. **Importer** (`fispact_import.rs`). Three-letter abbreviations, NOHEAD/NOHEADER, MCSEED and the deprecated
   keywords are recognised; an unknown keyword after a word-list keyword gets its own entry; a superseded or
   trailing `TIME` is `unsupported` with its duration and flux (deferral semantics kept); `OVER` after a step is
   not `mapped`; `PULSE` depth and expanded work are capped before expansion; keywords inside `PULSE 0` and
   repeated deferred keywords are reported correctly; `DOSE 1` after `DOSE 2` agrees with the spec; BOM, bare CR
   and non-UTF-8 comment bytes are handled; an unresolvable fluxes entry is fatal only for irradiating decks; a
   failed `--strict` leaves no stale output and OUT may not alias the deck; `POWER` reads its declared MT count.
   The test `power_steps_emit_the_target_and_list_the_missing_kerma_table` asserted the old miscount and is
   corrected; `p140_if_f13_*` is removed (rejected finding).
7. **Documentation.** The documentation findings of every review are applied to `docs/guide/` and the converter
   docstrings, and each parity feature without a fenced handbook fragment gets one, so H4 exercises it.

## Gates after repair

The full workspace test suite, `cargo fmt`, clippy with `-D warnings`, H2, H3 and H4 rerun on the repaired
candidate, and every existing controls checker affected by a changed output (P123 photons and tables, P126 power,
P129/P139 shielding, P132/P138 importer) is rerun; a changed committed baseline is regenerated and explained in
the ledger. H5 is CI on the pushed commit.
