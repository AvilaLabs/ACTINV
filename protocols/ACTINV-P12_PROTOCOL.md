# ACTINV P12 — v1.0 hardening and release candidate

**Roadmap row:** P12 (final phase of the v1.0 milestone). **Opened:** after the verified P11 close. **Time box:**
one bounded implementation session plus one external-reference run. **External acts:** the principal's. This protocol
covers configurable clearance/waste indices, ingestion/inhalation dose coefficients, primary-source table
re-verification, parser fuzzing, the FNG/ITER activation step, licensing-chain documentation and the technical v1.0
release commit. It does not authorize publishing crates or wheels, creating a GitHub Release, signing artifacts, or
moving a public tag. Those remain principal acts.

ACTINV 1.0 is a stable technical interface and a traceable calculation component. It is not, by itself, qualified
software, an approved nuclear-data library, a regulatory limit, a safety margin, or a licensing conclusion. A user
who places it in a licensing chain owns jurisdiction selection, data qualification, independent review, configuration
control and approval of the resulting safety case.

## Minimum gate input and pinned references

Standing rule 7 applies before any complete-library or million-case work. G1 starts with a two-nuclide synthetic
radiological table and one three-step synthetic activation result. G3 starts with 10,000 deterministic mutations and
profiles that executable before the 1,000,000-case gate. G4 uses one spatial cell and the already published one-group
cross sections; it does not rerun neutron or photon transport.

- The IUPAC Technical Report by Meija et al., *Pure and Applied Chemistry* 88 (2016) 293–306,
  DOI `10.1515/pac-2015-0503`, is the primary abundance reference. The open publisher copy retrieved through the
  National Research Council Canada archive is
  `d9079171301dc440e6ee40378da1aa5aef7c43e99d815f4cf31c1eb76561dd89` (SHA-256). ACTINV's point table follows the
  paper's stated convention: Column 9 representative values, except that Column 6's best measurement supplies a
  point when Column 9 is an interval. Natural Ta-180 is represented by the physical isomer key `Ta180m1`.
- AME2020 `mass_1.mas20`, published with Huang et al. and Wang et al., *Chinese Physics C* 45 (2021) 030002/030003,
  is the primary mass reference. The exact ASCII source is
  `e8599c6d7f724fac91934e59f1b9de8fb8f63e820f4b39456b790665ed2a3307` (SHA-256). The official AMDC/IAEA URL,
  original papers, file-format columns and every compared row are recorded by the control; an OpenMC installation
  may supply a byte-identical mirror but OpenMC values or APIs are not the reference parser.
- ICRP Publication 119, *Compendium of Dose Coefficients based on ICRP Publication 60* (2012), is the complete
  legacy coefficient reference named by the v1.0 roadmap. ICRP's official free publication and educational database
  remain external and copyrighted. ACTINV must not copy, bundle, choose a chemical form, choose an aerosol size, or
  silently select the largest coefficient. Newer ICRP Publication 103-based series may be supplied through the same
  interface only when their edition and scenario are explicitly labelled.
- Peterson et al., *Nuclear Fusion* 64 (2024) 056011, DOI `10.1088/1741-4326/ad32dd`, is the open FNG/ITER
  reference. Its CC-BY-4.0 research archive is Zenodo record `10660030`, DOI `10.5281/zenodo.10660030`; archive
  `research_data.tar.xz` is `1c76f42dcbc3e0f488f8035c3f63e4cd4428930f76efc088329be7ec9c6b45ed` (SHA-256).
  Cell 620 files `microxs_620.csv`, `depletion_results.h5`, `flux_620.npy`, `inventory.i`, and `fluxes` are respectively
  `fa097a994e8a4ea93267603bd6435972c15d3daa1d89cb37b626e21147637651`,
  `1fcd608a0a8100892b4d24ca7de05d401ab952b904ac3d80c8698de36419d4d5`,
  `9f2b3223164adbe5709aa493943af0a1fde3b538654ec28993b32dfe56195828`,
  `c2fdfc04547017823c533e5a48199c5bd49cfb33fe36fb7a984a88c30c20516b`, and
  `25bc8b50a74147f4cc4637a24e2c6d0d8b24562447abb28e7ba699bc03390fde`. The supplied reduced ENDF/B-VIII.0 chain
  XML is `f3f56d3a9ee66bcb691ea0812aad6a3696c00f6272f503de866a495b85c7270e`.

None of those external source files or generated libraries enters Git.

## Normative radiological-response contract

1. The optional top-level spec object is

   ```json
   "radiological": {
     "table": {"path": "responses.json", "sha256": "64 hexadecimal digits"},
     "responses": ["eu-clearance", "icrp119-worker-ingestion"],
     "require_complete": false
   }
   ```

   An absent object preserves pre-P12 result bytes except for the intentional 1.0 solver-version leaf. An empty
   selector list means every response in file order. Selectors and table response IDs are unique.
2. The hash-pinned external file is strict JSON with format `actinv-radiological-table-1`, a nonempty title, a source
   object containing nonempty `citation`, `edition`, `url` and `jurisdiction` strings, and an ordered response array.
   Each response has a unique nonempty `id`, one of `clearance_index`, `waste_index`, `ingestion_dose` or
   `inhalation_dose`, a nonempty human-readable `basis`, and a nonempty map of canonical nuclide names to finite,
   strictly positive coefficients. Unknown fields, malformed nuclides, duplicate canonical names and bad values fail.
3. Clearance and waste coefficient values are limits in Bq/kg. For step activities `a_i` in Bq/g, their dimensionless
   sum-of-fractions index is `sum(1000*a_i/limit_i)`. Ingestion and inhalation values are effective-dose coefficients
   in Sv/Bq; the reported response is `sum(a_i*h_i)` in `Sv/g_material_intake`. ACTINV does not multiply by an intake
   mass, occupancy, frequency, retention factor or regulatory safety factor.
4. Every response reports value, unit, covered and missing activity in Bq/g, activity coverage fraction, contributing
   nuclide count and the exact sorted missing-active-nuclide list. Zero total activity has coverage one. No missing
   coefficient is treated as zero. `require_complete` rejects the complete run if any selected response misses any
   positive-activity nuclide at any reported step.
5. The table's computed SHA-256, declared SHA-256, source metadata, response kind/basis and coefficient count appear
   in the certificate. Per-step coverage appears in the result and the ledger. CLI, PyO3, prepared and mesh paths use
   the same immutable prepared table; mesh workers borrow it and do not reread or clone it per cell.

## Normative fuzz and FNG choices

1. `parser_fuzz_probe` is a deterministic, seed-recording mutation harness over the production parsers for run and
   mesh specs, photon responses, group structures, ENDF records/sections, activation evaluations, MF=33 covariance,
   decay, fission yields, activation-library NPZ and canonical flux streams. Valid seeds are mutated by truncation,
   insertion, deletion, byte/bit replacement, duplicated spans, length/count plants, non-UTF-8 and numeric edge
   values. The harness may add in-memory wrappers around existing parsers but may not maintain a second parser.
2. A case is one mutated input delivered to one named production parser. The fixed partition totals exactly
   1,000,000 cases; every named family receives cases, NPZ and flux containers receive at least 10,000 each, and the
   exact counts, seed, corpus hashes, elapsed time and peak RSS are recorded. Errors are expected. Panic, abort,
   signal, timeout, unbounded allocation or nondeterministic summary is failure. No `unsafe` or nightly-only tooling
   is introduced. CI runs a 10,000-case smoke partition; G3 runs the full partition once after profiling.
3. The FNG control derives—not commits—a one-group ACTINV library from all nonzero cell-620 microscopic cross
   sections. Reaction products are mapped by charge/mass conservation for the 19 supplied non-fission reaction
   labels, using a ground-state product when that state exists and loss-only otherwise. It independently verifies
   selected HDF5 reaction rates as `sigma_b * sum(flux_620) * source_rate / volume * 1e-24` before solving.
4. The control converts the supplied XML half-lives/branches to a temporary decay file, reads the initial SS316 atom
   inventory and cell volume from the published result, and uses all 170 published interval endpoints and source
   rates. ACTINV runs coupled, unpruned and CRAM-48. `Co58`, `Tc99_m1`, `Mn56` and `Cr51`—the four histories selected
   in the reference study—are compared at every endpoint. Where the reference population is at least `1e6` atoms,
   maximum relative error is at most `1e-4`; below that threshold, absolute error divided by the initial total atom
   count is at most `1e-18`. The archive/file hashes, transformation counts and maximum errors are recorded.

## Deliverables

1. Strict hash-pinned radiological table support and fully covered clearance, waste, ingestion and inhalation outputs
   through every entry path, with formula, scenario and missing-data semantics in the certificate and ledger.
2. Independent primary-source abundance/mass re-verification and corrected, more precise table provenance text.
3. Deterministic parser fuzz harness, regression tests for every crash found and a one-million-case evidence record.
4. Reproducible FNG/ITER campaign-1 cell-620 activation comparison using only external published data.
5. A licensing-chain integration guide, v1.0 specification/method/data/validation/limitation documentation, release
   notes, changelog, version `1.0.0`, buildable crate/wheel artifacts and CI coverage. No raw or bulk nuclear data.
6. P12 checker, session record, manifest and technical release commit pushed to GitHub after all gates are green.

## Gates

**G1 Configurable radiological responses.** Rust and an independent dense calculation agree within `2e-15` relative
or `1e-30` absolute on every response at every synthetic step, including mixed covered/missing activity and zero
activity. Hash/source/basis/unit/coverage fields survive CLI, PyO3, prepared and mesh paths without scientific
difference. Wrong hashes, unknown fields/kinds/selectors, duplicate IDs/canonical nuclides, empty metadata,
nonpositive/nonfinite coefficients and planted `require_complete` omissions fail without a result.

**G2 Primary table re-verification.** An independent parser extracts all 289 ACTINV natural-abundance point values
from the pinned Meija table using the frozen Column 6/9 rule and all 289 ground-state atomic masses from the pinned
AME2020 fixed-width file. Key sets and binary64 values match `results/tables/abundance_mass.json` exactly; each
element sums to one within `2e-15`. The generated Rust table is reproduced byte-for-byte, its certificate provenance
names both primary works and source hashes, and no OpenMC runtime or copied OpenMC constant is used as the oracle.

**G3 Parser fuzzing.** The fixed 1,000,000-case partition completes with zero crash, abort, signal, timeout or
allocation violation, below 1 GiB peak RSS. Repeating the 10,000-case CI partition produces the identical summary.
Every discovered pre-close crash has a minimized regression test and remains counted in the append-only repair
record; zero discoveries is explicitly reported rather than assumed.

**G4 FNG/ITER activation.** Every source and transformation invariant in the normative FNG choices passes. The four
published nuclide histories satisfy both frozen error bounds at all 170 endpoints. Fresh and repeated temporary
library/result scientific bytes match, and no archive member or generated nuclear-data library is tracked by Git.

**G5 Prior gates, interfaces and release candidate.** Every P1-P11 checker verdict remains green and CI rechecks all
committed verdict evidence plus every prior data-independent control it can execute without a bulk library. The four
strict Rust commands in `AGENTS.md`, Python dependency check, self-contained-clone control, release-note checker,
end-to-end CLI/Python control and P12 CI subset pass. CLI/Python/package versions are `1.0.0`; crate packages, wheel
and standalone binary build from the clean clone. Documentation states qualification boundaries, regulatory-table
selection duties, data hashes, validation applicability and every carried limitation without claiming approval.

**G6 Closure and reproducibility.** G1-G5 are independently re-derived by `controls/check_p12.py`; the session,
manifest, protocol/amendment hashes, result hashes and release commit agree, regeneration leaves the tree clean and
the pushed GitHub Actions run is green. No tag, registry upload, GitHub Release or licensing approval is implied.

## Verdict (`controls/check_p12.py`)

P12-PASS: G1-G6. P12-CONDITIONAL after one or more documented repair amendments. P12-FAIL otherwise. A PASS or
CONDITIONAL close completes the technical v1.0 repository release; public tagging/publishing remains the principal's
separate act. Standing rules 1-7 apply.
