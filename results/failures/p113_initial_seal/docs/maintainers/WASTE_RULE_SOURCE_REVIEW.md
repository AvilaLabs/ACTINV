# U.S. Part 61 waste-rule source review

Status: **G0 source review, 2026-10-03.** This maintainer record supports the P103 protocol and selected rule pack. It
is not a legal opinion or disposal-site acceptance decision. The official 2025 annual CFR XML was downloaded to
`target/waste-sources/61.55.xml` (SHA-256
`7c08a6cb64d21e082ecf62a94fc3ef0be42c64fb68443658eff393ddc0bc0864`) and its table values were cross-checked against
eCFR Title 10 as of **2026-10-01**. The pack records both sources. Recheck amendments at any later protocol revision.

## Primary sources checked

1. [Official 2025 annual CFR XML, §61.55](https://www.govinfo.gov/content/pkg/CFR-2025-title10-vol2/xml/CFR-2025-title10-vol2-sec61-55.xml), locally downloaded and hashed as above; especially paragraphs (a)(3)–(8) and Tables 1–2.
2. [10 CFR 61.55, eCFR point-in-time page](https://www.ecfr.gov/on/2026-10-01/title-10/chapter-I/part-61/subpart-D/section-61.55). The eCFR text, up to date 2026-10-01, was cross-checked against the annual CFR XML. The eCFR notes that it is authoritative but is not the official legal edition.
3. NRC, [Branch Technical Position on Concentration Averaging and Encapsulation](https://www.nrc.gov/facilities-safety/low-level-waste-disposal/decision-support/branch-technical-position-on-concentration-averaging-and-encapsulation), Revision 1, Volume 1, February 2015, ADAMS accession **ML12254B065**. Its §3.5 and Table 4 are identified as relevant by NRC/EPRI; the direct NRC PDF was not retrievable here. P103 claims only the averaging permission in §61.55(a)(8), and does not claim full CA BTP conformity. BTP source inspection remains relevant to later mixed-package/component extensions, not a P103 gate.
4. NRC, [Federal Register notice issuing CA BTP Revision 1](https://www.federalregister.gov/documents/2015/02/25/2015-03913/concentration-averaging-and-encapsulation-branch-technical-position), 80 FR 10165 (2015-02-25). This identifies the BTP as NRC staff guidance on acceptable methods for averaging under §61.55(a)(8), not a regulation.

The [EPRI 3002008189 implementation guidance](https://restservice.epri.com/publicdownload/000000003002008189/0/Product), §5.2 / discussion of BTP §3.5 and Table 4, describes the routine volume basis for activated-metal components as material volume less major void spaces. EPRI is secondary guidance. Because P103 does not claim BTP conformity, it uses an explicitly declared single-component displaced volume as an input definition under §61.55(a)(8); source inspection of the BTP should precede any future claim of full BTP conformity or mixed-package support. NRC's site confirms §61.55(a)(8) permits averaging over waste volume, or weight if the concentration limit is in nCi/g.

## Rule-pack values and metadata

Table 1 concentrations are in Ci/m³ except rows footnoted 1, which are in nCi/g. The category-specific values in the
10/01/2026 eCFR text are:

| Table 1 entry | Limit | Applicability / interpretation to freeze |
|---|---:|---|
| C-14 | 8 Ci/m³ | General row when the waste is not declared activated metal. |
| C-14 in activated metal | 80 Ci/m³ | Activated-metal variant replaces, rather than adds to, the general C-14 row. |
| Ni-59 in activated metal | 220 Ci/m³ | Applies only to activated metal; otherwise Ni-59 is not a Table 1 entry. |
| Nb-94 in activated metal | 0.2 Ci/m³ | Applies only to activated metal. |
| Tc-99 | 3 Ci/m³ | General Table 1 entry. |
| I-129 | 0.08 Ci/m³ | General Table 1 entry. |
| Alpha-emitting transuranic nuclides, half-life >5 years | 100 nCi/g | Aggregate category; the explicit Pu-241 row should take precedence for Pu-241 to avoid duplicate counting. |
| Pu-241 | 3,500 nCi/g | Explicit nuclide-specific row; treat as the applicable Pu-241 entry rather than also including it in the alpha-TRU aggregate. |
| Cm-242 | 20,000 nCi/g | Explicit nuclide-specific row, despite its half-life being under five years. |

Table 2 concentrations are in Ci/m³:

| Table 2 entry | Column 1 (A) | Column 2 (B) | Column 3 (C) | Regulatory note |
|---|---:|---:|---:|---|
| Total of all nuclides with half-life <5 years | 700 | no limit | no limit | Footnote 1: Class B unless other Table 2 nuclides independently determine Class C; practical transport, handling and disposal considerations still constrain concentration. |
| H-3 | 40 | no limit | no limit | Same footnote 1. |
| Co-60 | 700 | no limit | no limit | Same footnote 1. |
| Ni-63 | 3.5 | 70 | 700 | — |
| Ni-63 in activated metal | 35 | 700 | 7,000 | Activated-metal variant replaces general Ni-63 row. |
| Sr-90 | 0.04 | 150 | 7,000 | — |
| Cs-137 | 1 | 44 | 4,600 | — |

“No limit” is distinct from zero and from a missing table entry. Preserve the footnote and its Class B/default behavior
in the pack. It must not be reported as an unbounded disposal acceptance: external practical and site constraints remain
outside concentration classification.

## Rule interpretation proposed for the sealed contract

These are explicit implementation decisions recommended for owner approval at protocol seal. Where noted as an
interpretation, the regulation itself does not spell out the software rule in these exact terms.

1. **Waste form and activated-metal variants.** Require an explicit waste-type field. Select the activated-metal rows
   only when that type is declared; for an isotope with both generic and activated-metal rows (C-14, Ni-63), use the
   activated-metal row alone. Do not infer activated-metal status from material composition. The current phase covers
   a single activated component and does not apply mixed-package BTP factors, per-piece activity caps, blending, or
   package dilution.
2. **Aggregate categories.** H-3 (half-life approximately 12.3 years) and Co-60 (approximately 5.27 years) are not
   members of the Table 2 under-five-year aggregate; my earlier overlap example was incorrect and is withdrawn.
   P103's selected conservative interpretation includes every nuclide with half-life <5 years in that aggregate
   unless it has a dedicated Table 2 row. Thus Cm-242 contributes to the Table 2 aggregate because its
   half-life is under five years, even though it also has an explicit Table 1 row; retain the separate Table 1 and
   Table 2 constraints. The protocol selects this interpretation for Cm-242 alone and in mixed-table waste.
   Table 1's alpha-TRU aggregate overlaps the explicit Pu-241 row; P103 resolves this within Table 1 by applying the
   specific Pu-241 limit and excluding it from the generic alpha-TRU aggregate. These precedence decisions are
   interpretations of overlapping regulatory categories and require independent control vectors.
3. **Unknown short-lived nuclides.** The literal aggregate row covers all nuclides with half-life under five years.
   Therefore require half-life and element/isotope identity for emitted nuclides that could enter that sum. If their
   classification coverage is unknown, report the affected classification as incomplete/unknown rather than silently
   omitting activity. Nuclides in neither Table 1 nor Table 2 and not within an aggregate category do not affect the
   concentration class under (a)(6); that rule is not a disposal-acceptance conclusion.
4. **Table 1 thresholds.** For one contributing Table 1 nuclide, follow (a)(3): at or below 0.1× its limit is Class
   A; above 0.1× and at or below 1× is Class C; above 1× is not generally acceptable for near-surface disposal.
   For mixtures of more than one Table 1 nuclide, (a)(7) requires the sum of fractions to be strictly below 1 at the
   relevant criterion. P103 therefore uses strict `S1 < 0.1` for Class A and strict `S1 < 1` for Class C; equality
   fails that class criterion. This preserves the distinction between inclusive single-nuclide limits in (a)(3) and
   the explicit strict mixture rule in (a)(7). Exact-equality controls are required.
5. **Table 2 thresholds and mixtures.** For a single nuclide with finite limits, follow (a)(4): equality at a column
   limit stays in that column's class. For mixtures, apply (a)(7) separately within one column of Table 2; the sum
   must be **strictly less than 1.0** to establish that column's class. If a mixture's sum reaches/exceeds a column
   limit, test the next class column; a sum at or above the Class C column limit is above Class C. For the unbounded
   footnote-1 rows, preserve their explicit Class B rule unless other Table 2 nuclides independently set Class C.
   The §61.55 example is an independent oracle: Sr-90 50 Ci/m³ + Cs-137 22 Ci/m³ gives Column 2 sum `50/150 +
   22/44 = 5/6`, hence Class B.
6. **Mixed Table 1 and Table 2.** Apply (a)(5) after evaluating each table. If every Table 1 applicable concentration
   (or Table 1 mixture sum) is at or below 0.1× its limit, the Table 2 class controls. If any Table 1 nuclide/fraction
   is above 0.1× and at or below its limit, the result is Class C only when the Table 2 mixture remains within the
   Class C column; a Table 1 value/fraction above its limit or a Table 2 Class C exceedance yields above Class C.
   This encodes the cross-table rule in (a)(5), while retaining all separate table/column constraints for budget work.
7. **Concentration basis.** For an activated-metal component, derive average concentration using total activity
   divided by the component's displaced material volume (envelope volume minus major voids) for Ci/m³ entries, or
   component mass for nCi/g entries. Mesh cells are summed as activity first; only then divide by the declared whole
   component denominator. Do not treat mesh-cell volume as disposal averaging volume and do not dilute by a package.
   Require positive finite volume and mass as needed by the selected entries. If the chosen object is not activated
   metal, its waste-form-specific averaging basis requires a distinct sealed rule; do not silently apply the
   activated-metal basis to every waste type.
8. **External tritium.** H-3 retained from fuel is not in the activation result unless explicitly modeled. Accept a
   declared external H-3 amount with stated source, time, and unit; convert to the selected component basis once and
   add it once to the calculated H-3 activity. For waste types where H-3 is required by the scope, absence makes the
   H-3-related conclusion conditional/unknown. This data contract is an ACTINV coverage rule, not text in §61.55.
9. **Meaning of result.** Report only the concentration-based §61.55 class. Do not label Class A/B/C as acceptance by
   a particular disposal facility, compliance with §61.56 waste form/stability requirements, or a site-specific
   intrusion result. Section (a)(8) allows indirect methods only with reasonable assurance of correlation to actual
   measurements; ACTINV's arithmetic controls do not provide that licensee-specific assurance.

## Required source-derived controls before G0 can seal

- Encode all table rows above, their units, waste-type conditions, half-life/category predicates and footnote-1
  behavior from the selected CFR snapshot; independently transcribe them into the control checker.
- Normalize nuclide identities through `actinv_data::composition::material_key` and its element-Z data; reject
  mismatched supplied atomic numbers, duplicate normalized aliases, invalid metadata, non-finite/negative activities,
  and non-finite aggregate results. Require explicit alpha-emitter metadata and positive half-life for every active
  nuclide needing category evaluation. Missing properties must produce unknown classification plus the
  calculated-only class, never a zero contribution.
- Validate custom rule packs before evaluation: supported schema/id/version, unique row IDs and selectors within
  applicability, supported units/table numbers, exact column counts, and finite positive finite limits where present.
  A null B/C limit is only accepted where the selected official row has no limit and its footnote is preserved.
- Check the Sr-90/Cs-137 worked example and exact below/equal/above values at every finite threshold, including 0.1×
  and 1× Table 1 values, all three Table 2 columns, strict sum-of-fractions equality, and every table/column boundary.
- Include mixed Table 1/Table 2 vectors, activated-metal row substitution, Pu-241 alpha-TRU overlap, Cm-242's
  Table 1/Table 2 aggregate interaction, a five-year half-life boundary vector, aggregate short-lived and alpha-TRU
  cases, and an unknown short-lived product that forces incomplete coverage.
- Independently check activity-to-Ci/m³ and activity-to-nCi/g conversions, mesh aggregation before concentration
  division, displaced-volume subtraction, mass fallback, invalid denominator refusal, and no component-cell overlap.
- P103's explicitly declared single-component displaced volume follows the averaging permission in §61.55(a)(8).
  Inspect ML12254B065 §3.5/Table 4 before later claiming full BTP conformity or adding component-size, sections/pieces,
  mixed-container or encapsulation rules.

## Source links

- [10 CFR 61.55, point-in-time 2026-10-01](https://www.ecfr.gov/on/2026-10-01/title-10/chapter-I/part-61/subpart-D/section-61.55)
- [NRC CA BTP Revision 1 landing page (ML12254B065 and ML12326A611)](https://www.nrc.gov/facilities-safety/low-level-waste-disposal/decision-support/branch-technical-position-on-concentration-averaging-and-encapsulation)
- [NRC CA BTP Revision 1, Volume 1 PDF](https://www.nrc.gov/docs/ML1225/ML12254B065.pdf)
- [Federal Register: issuance of CA BTP Revision 1, 80 FR 10165](https://www.federalregister.gov/documents/2015/02/25/2015-03913/concentration-averaging-and-encapsulation-branch-technical-position)
- [EPRI 3002008189 implementation guidance](https://restservice.epri.com/publicdownload/000000003002008189/0/Product) (secondary corroboration for §3.5/Table 4 only)
