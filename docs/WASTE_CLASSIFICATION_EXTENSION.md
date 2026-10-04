# Planned extension: `actinv waste`, U.S. low-level waste classification

Status: **nominal classification and verified class budgets implemented, P105-PASS, 2026-10-03**. All six
implementation-commit workflows are green; see `results/p105_verdict.json` and `results/p105_ci_runs.json`.
Accepted scope and
sequencing live in [ROADMAP.md](ROADMAP.md#planned-extension--component-waste-classification-and-material-impurity-budgets-2026-10-03):
nominal single-component classification and verified nominal class budgets first; uncertainty/composition-range
classification and an opt-in draft fusion intrusion screen have separate follow-up gates. This page retains the
supporting design and preliminary source notes. The public CLI contract is in [the handbook](guide/waste.md).
The preliminary intrusion-source notes below are historical and do not qualify an intrusion screen. See also
the dated promotion and closure entries in `PARKING.md`.

## What it would answer

> At each cooling time, which waste class does this component fall into under 10 CFR 61.55?
> Does the disposal site need a site-specific intrusion assessment?
> And how much of each impurity can the material contain before the answer changes?

## Why it belongs in ACTINV and not in a separate tool

Classification rules alone are not new: U.S. nuclear plants have used commercial Part 61 classification software since
1982 ([RADMAN, NRC ML021640124](https://www.nrc.gov/docs/ML0216/ML021640124.pdf)), and published fusion element limits
also have a long history. The useful extension is component- and spectrum-specific analysis integrated with the
solver and existing decision tools:

- **Impurity limits per waste class.** Under `budget`'s supported fixed-rate configuration, activation is exactly
  linear in the initial composition (P75b, 1e-11). The table-column sums of fractions in §61.55 are linear in the
  nuclide concentrations, so they are linear in composition too. The per-element solves that `actinv budget`
  already runs (P79, `BUDGET.md`) would therefore also give:
  - the maximum impurity content that keeps a component within a selected Class A/B/C limit;
  - the specification margin factor.

  Each reported limit would be re-solved and checked, as `budget` already does. Nb-94 often decides the class of
  activated steel, and its concentration is usually taken from the specification's trace-element limit rather than
  measured. This turns the waste class into a line in the purchase specification. The shortcut inherits `budget`'s
  refusal of self-shielding, uncertainty, composition-dependent screening and schedule feed. Constraints from
  different tables/columns remain separate; the binding constraint sets the limit. Impurity limits for the draft
  intrusion screen require a separate method because its activity-share presence tests depend on composition.
- **Classification across uncertainty and composition ranges (follow-up).** Evaluate bands on the actual table sums
  with correlations, or explicitly conservative bounds, under a declared joint confidence/bounding interpretation.
  Element-run standard deviations are not additive. Report a class stable within the declared bounds only when
  every allowed case gives that class; otherwise report the possible classes and binding assumptions. Missing
  uncertainty channels remain explicit, and MF=33 coverage alone does not establish total uncertainty coverage.
- **Calculated scaling factors.** Fission plants infer hard-to-measure nuclides (Ni-59/63, C-14, Nb-94, Tc-99) from
  Co-60 using decades of sample data. Fusion has no such history. ACTINV could calculate the nuclide ratios with bands
  for a given material and spectrum. This would be a later item.

## Rule packs and evaluation

ACTINV does not choose a jurisdiction. The extension keeps that position: table values and source metadata would
be a cited, hash-pinned rule-pack file that the user selects, in the same way as the radiological tables.
Classification predicates would be reviewed code. A reference U.S. pack would cover the first two items below.
The third is a separate, explicitly selected draft-screen follow-up.

1. **10 CFR 61.55**
   - Table 1, long-lived nuclides: for a single listed nuclide, a concentration not exceeding 0.1× the limit is
     Class A; above 0.1× but not exceeding 1× is Class C; above that is not generally acceptable for near-surface
     disposal. Mixtures follow (a)(7); exact boundary interpretations are fixed at G0.
   - Table 2, short-lived nuclides: Column 1 / 2 / 3 limits give Class A / B / C. Include the aggregate category
     for all nuclides with half-life less than five years and the entries with no Class B/C limit.
   - (a)(5) sets how the two tables combine.
   - (a)(7) sum of fractions within one column. Unit test from the rule's own example: Sr-90 at 50 Ci/m³ plus Cs-137
     at 22 Ci/m³ gives 5/6 (reported as 0.83 in the rule), which is Class B.
   - "Activated metal" variants are selected by the declared waste type.
   - Units are Ci/m³ over the displaced volume, or nCi/g for alpha-emitting transuranics, Pu-241 and Cm-242.
   - (a)(6): waste containing no nuclides covered by either table is Class A. This is a concentration-based class,
     not a disposal-acceptance verdict; the separately gated fusion screen addresses additional draft criteria.
2. **NRC Concentration Averaging and Encapsulation BTP, Rev. 1 (2015)**
   - A single activated component is averaged over its displaced volume (envelope minus major voids) or its mass. A
     mesh result is converted to total activity per cell, summed over the component and divided by the applicable
     volume or mass.
   - Mixed packages (later): factor of 2 for primary gamma emitters and factor of 10 for other nuclides, or the
     per-item limits in BTP Tables 2 and 3 (e.g. Nb-94 1 mCi in every class). Items smaller than 280 cm³ are
     grouped.
3. **Draft NUREG-1556 Vol. 22: fusion intrusion screen (separately gated follow-up)**
   - The notes below were read from the March 2025 preliminary draft, ML24295A002. The February 2026 proposed rule
     identifies ML24092A377 as its accompanying draft guidance. Reconcile versions and recheck all criteria,
     values and D1/D2 before freezing a draft-screen pack; the newer PDF has not been inspected in this review.
   - A nuclide counts as present if any of these holds:
     - it is above 0.01× the disposal site's WAC limit;
     - it has no §61.55 or WAC limit and is above 0.26 MBq/cm³;
     - it reaches a DOT reportable quantity;
     - it makes up 1% or more of the package activity.
   - If any present nuclide is missing from Table 8-5, or exceeds its Table 8-5 limit for the class, a site-specific
     intrusion assessment (10 CFR 20.2008(a), 5 mSv/yr) or an alternative §61.7 method is required.
   - Typical long-lived fusion products are not in Table 8-5: Mo-93, Ag-108m, Ho-166m.
   - The text compares nuclides one at a time and does not say whether a sum of fractions applies. The extension would
     implement the test as written, report the sum as information only, and label that reading as an
     interpretation.
   - Results from this screen are labelled **proposed rule / draft guidance** (the proposed rule is 91 FR 9476,
     2026-02-26; no final rule yet). The integrated low-level waste proposed rule (91 FR 40290, 2026-07-01) keeps the
     §61.55 tables.

**Source discrepancies in the March 2025 preliminary draft** (recorded here; nothing has been sent to the NRC;
their status in the February 2026 draft remains to be checked):

- **D1.** Table 8-5 gives Cs-137 Class C as **460** Ci/m³ and cites "Values in 10 CFR 61.55", but §61.55 Table 2 says
  **4,600**. Classification follows the regulation. A draft-screen value and interpretation will be selected only
  after the source-version reconciliation; the discrepancy remains recorded against the preliminary draft.
- **D2.** The text and Appendix Q call the screening table "Table 8-4". In the document, 8-4 is the tritium bioassay
  table and the screening table is 8-5.

## Inputs beyond a normal run

- **Component definitions:** cells, mass, displaced volume (or mass and density, marked conservative) and waste type.
- **Tritium from fuel** (permeation and retention). This is not activation, so ACTINV does not compute it. For
  plasma-facing, fuel-cycle and breeder components, H-3 must be declared, for example from a FESTIM or TMAP8 result or
  a measurement. If it is not declared, H-3 is reported as Unknown and the reason is given.
- **Disposal-site WAC limits** (optional for current-rule classification). Without them, the affected draft-screen
  presence test is not evaluated and the screening result remains incomplete. Classification, intrusion screening
  and site acceptance are separate outputs; missing required tritium leaves affected conclusions conditional or
  unknown.

## Qualification boundary

- A calculated inventory is an "indirect method" under §61.55(a)(8). It is acceptable only with reasonable assurance
  that it correlates with measurements, and providing that assurance is the licensee's job.
- The preliminary draft notes a factor-of-10 target for inferred concentrations. Recheck its applicability in the
  selected guidance version; this does not replace numerical controls or establish predictive qualification.
- `QUALIFICATION.md` already excludes waste classification. This extension would need its own controls:
  - each rule-pack value checked against the regulation text;
  - the §61.55 worked example plus independent boundary, both-table mixture, aggregate-category, activated-metal,
    unit-conversion and component-aggregation vectors; applicable EPRI 3002008189 CA-BTP implementation examples;
  - an end-to-end comparison with published fusion element limits for Class C (Fetter, Cheng & Mann 1990), under a
    protocol frozen before the run with matched assumptions. The paper's derived disposal limits are physics
    context, not an exact oracle for current Part 61 classification.

## Planned phase shape

| Gate | Scope | Pass condition |
|---|---|---|
| G0 | Current-rule pack, source checks, single-component averaging basis and explicit boundary interpretations | Every value and predicate checked against its source before the protocol seal |
| G1 | `actinv waste`: nominal class and margins at selected cooling times per component, including declared external tritium | Worked-example and independent inventory vectors exact where arithmetic permits; incomplete inputs explicit |
| G2 | Nominal Class A/B/C limits from the per-element solves used by `budget` | Every limit and joint margin re-solved within 1e-6 against all applicable constraints |
| G3 | Small end-to-end case, independent arithmetic and planted input/coverage failures | Checker rederives results and rejects unsupported unconditional conclusions; required repository checks pass |
| Follow-up | Classification across uncertainty/composition ranges | Correlated table-sum propagation or conservative bounds validated under a declared interpretation |
| Follow-up | Opt-in draft intrusion screen and presence tests | Source versions and D1/D2 reconciled; every presence criterion and threshold transition independently checked |
| Later | Mixed packages under the BTP; measurement-supported scaling factors; additional jurisdictions; workbench/`twin` integration | Separate scope and protocol after the CLI contract is established |

## Sources

- [10 CFR 61.55](https://www.law.cornell.edu/cfr/text/10/61.55)
- [Proposed fusion rule, 91 FR 9476](https://www.govinfo.gov/content/pkg/FR-2026-02-26/pdf/2026-03865.pdf); draft
  NUREG-1556 Vol. 22, February 2026 draft ADAMS ML24092A377 (to be inspected); preliminary March 2025 draft
  ML24295A002 is the source of the notes above
- [Integrated low-level waste proposed rule, 91 FR 40290](https://www.govinfo.gov/content/pkg/FR-2026-07-01/html/2026-13302.htm)
- CA BTP Rev. 1 (ADAMS ML12254B065), as quoted in
  [EPRI 3002008189](https://restservice.epri.com/publicdownload/000000003002008189/0/Product)
- [Fetter, Cheng & Mann (1990)](https://dgi.umd.edu/sites/default/files/2019-08/1990-FED-RadWaste.pdf)

## Draft intrusion source reconciliation — 2026-10-03

The earlier March 2025 notes above are retained as historical notes. The February 2026
NUREG-1556 Vol. 22 source is represented by pinned PDF and extracted-text snapshots.
Manual visual PDF review covered February pages 8-70, 8-71 and Q-6, and March pages
8-71 and 8-72. Other excerpted material was transcribed from the extracted-text
snapshots and was not independently checked against rendered PDF pages. The source
snapshots and compact excerpts are recorded in
[`controls/fixtures/p111/source_reconciliation.json`](../controls/fixtures/p111/source_reconciliation.json).
G0 controls validate pinned excerpt bytes and a literal 25-row transcription; they do not
download or parse the complete PDFs, and do not claim CI reviewed the full PDF.

The selected pack is `us-nrc-nureg1556-v22-draft-2026-02-v1`. The NRC Volume 22 page
still labels ML24092A377 “Draft Report for Comment”; comments were due May 27, 2026.
The NRC [fusion rulemaking status](https://www.nrc.gov/materials/fusion/rulemaking-status)
continues to list the February 2026 proposed rule/draft guidance, and its
[strategy page](https://www.nrc.gov/materials/fusion/vision-strategy) describes final
rule and guidance in 2027. The proposed rule is [91 FR 9476](https://www.govinfo.gov/content/pkg/FR-2026-02-26/pdf/2026-03865.pdf).
This remains a draft-screen source, not final guidance or a legal determination.

The reviewed Table 8-5 contains 25 rows. All numeric values and all explicit “No limit”
cells match the March 2025 preliminary draft. The source defects are preserved: Cs-137
Class C is 460 Ci/m³ (Part 61 Table 2 says 4,600); activated-metal Nb-94 is
0.2/0.2/2 Ci/m³ (the general Nb-94 Class C value and Part 61 value are 0.2); body and
Appendix Q references to Table 8-4 point to the displayed Table 8-5; Appendix Q says
“activity or is”; and the DOT appendix title quoted in the draft is inaccurate. The pack
uses the actual printed rows and keeps every no-limit cell as a typed null, not a numeric
substitute. The short-lived aggregate uses strict half-life <5 Julian years and the printed
alpha aggregate uses its fixed eight nuclides; both remain separate constraints from any
individual named row.

The software follows the four “present” criteria as an opt-in, three-valued draft screen:
strict `>` for 1% of a supplied WAC concentration limit; strict `>` 0.26 MBq/cm³ with
criterion 2 indeterminate when only one of §61.55/WAC lists the nuclide; caller-declared
DOT RQs in whole-container Bq with individual `activity >= RQ` sufficient, while a
mixture-only crossing does not assign presence to below-RQ nuclides; and inclusive
`activity >= 1%` of declared whole-container total. Missing inputs remain visible as
unknown/indeterminate. The source gives competing cues at equality between its caption
(“below which”) and prose (>); equality therefore remains indeterminate as an assessment
indicator. No dose calculation, compliance, waste acceptance or disposal-class promotion
is implied. The declared DOT RQs and WAC are caller-supplied data, not an embedded legal
catalog or WAC.
