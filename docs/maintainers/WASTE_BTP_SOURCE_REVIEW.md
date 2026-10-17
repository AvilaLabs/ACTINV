# Source review: NRC Concentration Averaging and Encapsulation BTP, Rev. 1 (February 2015)

Purpose: primary-source grounding for P147 (mixed packages of discrete items and encapsulation for 10 CFR 61.55 classification). Frozen 2026-10-17 before the P147 protocol. The PDFs are not tracked; their SHA-256 values below identify the copies read.

Convention: "V1 p.N" = Volume 1 printed page N (printed page = PDF page; 49 PDF pages). "V2 p.N" = Volume 2 printed page N. "V1 Sec. x" = section number. Quotes are verbatim from the extracted text (line breaks joined; footnote markers kept where they matter). Where a number is not in the text, this document says so. Statements marked **[inference]** are mine, not the BTP's. Statements marked **[outside the BTP]** use knowledge of 10 CFR 61.55 that is not in the text obtained and must be checked against the CFR.

---

## 1. Source identity and retrieval record

### 1.1 Documents

| Item | Value |
|---|---|
| Title (PDF metadata) | "Concentration Averaging and Encapsulation Branch Technical Position, Revision 1, Volume 1." |
| Issuer | U.S. NRC, Office of Nuclear Material Safety and Safeguards, February 2015 |
| ADAMS | ML12254B065 (Vol. 1); ML12326A611 (Vol. 2, "Response to Stakeholder Comments and Technical Basis") |
| Vol. 1 | 49 pages, PDF 1.6, created 2015-02-20 |
| Vol. 2 | 94 pages, PDF 1.6, created 2015-02-20 |
| Status of guidance | V1 Sec. 3.9 p.41: "The positions in this document are not intended as a substitute for regulations, and compliance with them is not required." V1 Sec. 3.10 p.41: "Compliance with this CA BTP is not an NRC requirement and licensees and applicants may choose this or another method to achieve compliance with this provision in the 10 CFR Part 61." |

### 1.2 PRIMARY SOURCE WAS OBTAINED (both volumes)

Volume 1 was obtained two independent ways; extracted text is byte-identical (`cmp` on `pdftotext -layout` output: identical, 2219 lines).

| Route | Result | SHA-256 |
|---|---|---|
| WebFetch tool on `https://www.nrc.gov/docs/ML1225/ML12254B065.pdf` (live NRC copy) | SUCCESS, 379,540 bytes, 49 pp; PDF ModDate 2023-06-08 | `c27e4187a221d2b5f27ad47c01d99d659717b21da505b32ab248918a0846dae6` |
| `curl https://web.archive.org/web/2018id_/https://www.nrc.gov/docs/ML1225/ML12254B065.pdf` | SUCCESS, HTTP 200, `application/pdf`, 379,797 bytes, 49 pp; ModDate 2015-02-23 (the 2015 original) | `4e0e3df3017b0f546630c94fbbd637c7fb4ee124f830afa575dfc05ad67ba7eb` |
| Vol. 2: `curl https://web.archive.org/web/2018id_/https://www.nrc.gov/docs/ML1232/ML12326A611.pdf` | SUCCESS, HTTP 200, 485,078 bytes, 94 pp (the 2020id_ snapshot has the same size) | `30bac2d3193b84707b682c5f737f3096495c79466aeac6f281e2c2478fd9d0e8` |

The two Vol. 1 hashes differ only because the live NRC file was re-saved in 2023 (metadata); the text is identical, so all page citations hold for either.

Text was extracted with `pdftotext -layout`; tables and figures were read from page renders.

### 1.3 URLs tried and outcomes

| URL / route | Outcome |
|---|---|
| `curl https://www.nrc.gov/docs/ML1225/ML12254B065.pdf` (twice, with and without browser UA) | HTTP 403 "Access Denied" (CDN block). This is why the earlier review could not retrieve it. |
| WebFetch `https://www.nrc.gov/docs/ML1225/ML12254B065.pdf` | Succeeded (see above). Note for the maintainers: the WebFetch tool can fetch it where curl cannot. |
| `curl` NRC landing page `.../branch-technical-position-on-concentration-averaging-and-encapsulation` | HTTP 403. Not retrieved. A WebSearch summary confirms the page says Volume 1 (ML12254B065) is the guidance and Volume 2 (ML12326A611) holds comment responses and technical basis; I did not read the page itself. |
| `curl https://adams.nrc.gov/wba/ML12254B065` | DNS resolution failed in this sandbox (curl exit 6). The ADAMS API was not otherwise tried. |
| `https://web.archive.org/web/2023/<pdf url>` | HTTP 200 but an HTML wrapper page (8,982 bytes), not the PDF. |
| `https://web.archive.org/web/2020/<pdf url>` (non-id_ form) | HTTP 403. |
| Wayback availability API for the PDF URL | `{"archived_snapshots": {}}` (no snapshot reported, although the `2018id_` form returned the PDF). |
| `https://web.archive.org/web/2018id_/<pdf url>` | HTTP 200 PDF (success, above). |
| web.archive.org of the landing page (`2018id_`) and `llw-btp.html` (`2025id_`) | 404 / 403. |
| regulations.gov docket NRC-2011-0022 | Not tried (unnecessary once the primary text was in hand). |
| WebSearch | Returned the Federal Register notice (80 FR 10165, 2015-02-25), govinfo, the NRC landing page, and the 2012 draft (ML121170418, `https://www.nrc.gov/docs/ML1211/ML121170418.pdf`). Not used as a source beyond identification. |

No secondary sources (EPRI 3002008189, SECY papers, 80 FR 10165) were needed for the numbers below; everything is from the primary text. Secondary sources were not collected.

### 1.4 Terminology trap (V1 vs the CFR)

The BTP has its own "Table 1" to "Table 4". It also refers to "Tables 1 and 2 of 10 CFR 61.55". These are different tables. Below, **BTP Table n** and **CFR Table n** are always distinguished. BTP Table 1 = blending thresholds, BTP Table 2 = primary gamma activity limits, BTP Table 3 = other-nuclide activity limits, BTP Table 4 = volume/mass basis.

---

## 2. Scope and general averaging principles

**Scope and purpose.** V1 Sec. 1 p.5: "This Concentration Averaging (CA) Branch Technical Position (BTP) provides guidance on complying with 10 CFR 61.55(a)(8), 'Determination of concentrations in wastes,' as it applies to the classification of waste for disposal under 10 CFR Part 61."

V1 Sec. 1.5 p.14: "The scope of this revised CA BTP is limited to the averaging of concentrations of radionuclides for the purpose of determining the classification of waste being shipped for disposal, as provided for by 10 CFR 61.55(a)(8)."

**What (a)(8) says.** V1 p.7 fn.3: "10 CFR 61.55(a)(8) states that '…The concentration of a radionuclide may be averaged over the volume of the waste, or weight of the waste if the units are expressed as nanocuries per gram.'"

**What the BTP adds.** V1 Sec. 1.2 p.6-7: "The NRC does not specify in the regulations how radiological 'hot spots' are to be averaged, other than to specify in 10 CFR Part 61.55(a)(8) that averaging of waste concentrations is acceptable. The regulations do not define the conditions and constraints on such averaging." The BTP "is designed to answer questions such as: 'How much waste above the class limits is permissible in a waste mixture?' and 'How much more concentrated than the limit can a portion of this waste be?' and 'Over what volume are these concentrations to be measured?'" (p.7).

**Definition of averaging and the class step.** V1 Sec. 1.4 p.9: "Concentration averaging is the mathematical averaging of the radionuclide activities in waste over its volume or mass. ... Once the average radionuclide concentrations are determined, then the waste classification process described in 10 CFR 61.55 is used to determine the class of the waste."

**Hot spot / classification basis.** V1 p.11: "Hot Spot: A portion of the overall waste volume whose radionuclide concentrations are above the class limit for the entire container. ... Classification is based on the average radionuclide concentrations of waste in a container according to 10 CFR 61.55(a)(8), subject to certain constraints on hot spots."

**Volume or weight.** V1 Sec. 3.1.3 p.16: "Most concentrations of radionuclides in the 10 CFR 61.55 waste classification tables are based on radioactivity per unit volume. Weight may also be used and converted to equivalent volume for averaging purposes provided that a representative density is used, based on a representative distribution of materials." Fn.20 (p.16): "Transuranic radionuclide concentration limits are specified as radioactivity per unit of weight." V1 p.18 fn.28: "The volume of the waste can be calculated from the weight of the waste divided by its density. Licensees may use a representative density based on a representative distribution of materials as they occur in waste." V1 Sec. 3.5 p.33: "In general, waste volumes may be calculated from the mass of the waste divided by a representative density."

**Time basis.** V1 Sec. 3.5 p.33: "The volumes and masses listed are those at the time the waste is ready for shipment to a licensed disposal site, consistent with the Part 61 requirements for waste classification." Fn.46: "Waste is often processed before classification and shipment ... This table does not apply to in-process waste volumes." V2 p.22 (comment 1.d response): "The fill volumes apply to the waste disposal containers at the time of shipment to the disposal facility after processing, including compaction, has occurred."

**Two categories.** V1 Sec. 3.1 p.15: "For the purposes of this CA BTP, waste is divided into one of two categories, blendable waste or discrete items. Different averaging constraints apply to each category." V1 p.10 Figure 1 (flowchart): Begin -> "Blendable waste?" yes -> Sec. 3.2; no -> "Encapsulated item or items?" yes -> Figure 5; no -> "Single discrete item? (e.g., activated metal)" yes -> Sec. 3.3.1; no -> "Mixture of discrete items?" yes -> Figure 2; no -> "Use Alternative Approach (Section 3.8)".

**Discrete item definition.** V1 p.12 and p.15-16 and App. A p.44: "discrete items are items belonging to one of the following waste types: activated metals, sealed sources, cartridge filters, contaminated materials, and components incorporating radioactivity into their design." Contaminated materials (App. A p.44): "Components or metals on which radioactivity resides near the surface in a fixed or removable condition. This term does not include other materials, such as plastic, wood, or glass."

**Mixtures of waste types.** V1 Sec. 3.4 p.33: "For mixtures of discrete items of different waste types, the classification of the entire mixture of waste types should be the highest classification of any of the individual waste types in the mixture. Individual waste types should be averaged in accordance with the provisions in Section 3.3. If discrete items are mixed with blendable wastes, the averaging constraints in Section 3.3 should be applied to the discrete items." And p.33: "If one or more of the waste types in a mixture are blendable ... the licensee should document the physical and chemical compatibility of the waste types and make the documentation available for inspection". Fig. 2 (p.23) box: "Average concentrations within each waste type in accordance with Section 3.4, 'Classification of Mixtures of Different Waste Types'".

**Single-container only.** V1 Sec. 3.2.2 p.19: "Radionuclide concentrations should not be averaged over more than one container."

**What is not allowed / extreme measures.**
- V1 p.7: "The CA BTP states that extreme measures of adding non-radioactive material, diluting the waste to circumvent stricter disposal requirements, should not be used."
- V1 Sec. 3.5 p.33: "nonradioactive materials added to the mixture should have a purpose other than reducing the waste class, such as waste stabilization or process control. To be consistent with other provisions in this CA BTP, extreme measures to lower the waste classification should be avoided."
- Gaseous waste (V1 p.15): "not subject to concentration-averaging constraints because the radiological concentrations in gases are expected to be uniform." Liquid waste (p.15-16) "is not authorized for disposal in a licensed disposal facility. However, liquid waste may be solidified or absorbed". This BTP "only covers solid waste shipped for disposal" (p.16 fn.18).
- Generators shipping to a processor (V1 p.17): "they do not need to classify the waste, because classification is only required for the final waste form presented for disposal."

**Which nuclides/tables the constraints apply to.**
- V1 Sec. 3.3.2.2 p.22: "As used in this CA BTP, the primary gamma-emitting nuclides are cobalt-60 (60Co), niobium-94 (94Nb), and cesium-137 (137Cs). ... radionuclides other than primary gamma emitters are all radionuclides from Tables 1 and 2 of 10 CFR 61.55 other than 60Co, 94Nb, or 137Cs."
- V1 p.18 fn.27: "Most 137Cs decays to a metastable nuclear isomer, 137mBa. 137Cs is not a gamma emitter, but 137mBa is. When this BTP refers to Cs, it includes the 137mBa that is in equilibrium with it."
- V1 p.22 fn.34: "The NRC staff does not expect other gamma-emitting radionuclides in the 10 CFR Part 61 waste classification tables to cause a hazard to an intruder who handles the waste."
- "Radionuclides of Concern" (App. A p.45): "Any nuclides in the waste in concentrations greater than either 1 percent of the concentration of that nuclide listed in Table 1 in 10 CFR Part 61 or 1 percent of the applicable class-dependent concentration of that nuclide in Column 2 or 3 of Table 2 in 10 CFR Part 61."
- V1 p.17 (contaminated materials): "It is also permissible under the constraints for discrete items in Section 3.3 to average concentrations of the radionuclides listed in Tables 1 and 2 of 10 CFR 61.55 over the contents of the disposal container."

**Relation to site license and other guidance.** V1 Sec. 3.9 p.41: "Where there are conflicts with this guidance, the land disposal facility license conditions issued by the regulatory authority (currently, an Agreement State in all cases) must be met." Current licensees may keep using the 1995 BTP (p.41, 42).

---

## 3. Blendable waste (V1 Sec. 3.2, pp.17-21; BTP Table 1 p.20)

### 3.1 What is blendable (V1 p.17-18)

"a waste type is considered to have the properties needed for blending (i.e., a waste type is 'blendable') if: the waste can be physically mixed to create relatively uniform radionuclide concentrations or the waste is not expected to contain durable items with significant activity." Footnote 24 (p.17): "Radionuclide concentrations are 'relatively uniform' if an intruder who encounters the waste is unlikely to encounter waste more concentrated than the class limit by a factor of 10 or more." (Fn.11 p.12 says "by a factor of 10"; fn.48 p.44 likewise "by a factor of 10". The two forms differ by "or more".)

Examples (p.18): "Ion-exchange resins, filter media, evaporator bottom concentrates, soil, and ash are each an example of a blendable waste type ... Contaminated trash is considered to be blendable because it is not expected to contain durable items with significant activity."

Waste streams are distinct (V1 Sec. 3.1.1 p.15): "Waste streams are considered distinct (i.e., different from one another) if the concentrations of radionuclides of concern (see the Glossary) typically differ by more than a factor of 10 (e.g., primary and secondary resins from nuclear power plants are distinct waste streams)."

### 3.2 Removal of the old "Factor of 10" blending rule

V1 App. B item 3, p.46: "Removed the 'Factor of 10' rule for mixing blendable wastes to be consistent with the U.S. Nuclear Regulatory Commission (NRC) decision in SRM-SECY-10-0043, 'Blending of Low-Level Radioactive Waste' (NRC, 2010b). Replaced it with a demonstration of adequate blending when blendable waste might contain an unacceptable hot spot."

So for blendable waste, the old per-stream factor-of-10 limit is gone; it is replaced by BTP Table 1 volume thresholds plus an adequate-blending criterion. (The "Factor of 10" that remains in Rev. 1 is the discrete-item concentration constraint of Sec. 3.3.2.2; do not conflate them.)

### 3.3 Single blendable stream (V1 Sec. 3.2.1, p.18)

"If a waste package contains a single blendable waste stream, radionuclide concentrations for waste classification may be averaged over the waste in the package. That is, an average radionuclide concentration may be based on its total activity in the package divided by the volume or mass of the waste in the package. However, if waste fills 90 percent or more of a package, average radionuclide concentrations can be based on the entire interior volume of the container. Void spaces do not normally need to be accounted for in blendable waste."

Absorbed liquid (p.18): "Absorbed liquid ... may not be retained on the absorbent medium. Therefore, radionuclide concentrations should not be based on the volume or mass of the absorbent medium, but should be based on the original volume or mass of the liquid waste before absorption."

Small sources in trash (p.18): "The activity of small concentrated sources or gauges (less than 3.7 MBq) (100 µCi) that may be mixed with contaminated trash may be averaged over the trash volume."

V2 p.22 (comment 1.d response), on the 90 percent rule: "If the fill volume is less than 90 percent, generators and processors may average the concentration of waste over the volume of the waste." V2 p.59 fn.24: "Or the fill-volume of the container, if there is less than 10 percent void space."

### 3.4 Multiple blendable streams (V1 Sec. 3.2.2, p.19-21)

"In some cases, blendable waste streams of the same waste type (e.g., primary and secondary resins) may be combined in the same container without blending. If the CA BTP Table 1 thresholds are not exceeded, the waste does not need to be blended and the radionuclide concentrations can be averaged over the volume of the waste or container as a single waste stream (i.e., as in Section 3.2.1). If the thresholds in CA BTP Table 1 are exceeded, the waste must be blended and a demonstration of adequate blending is needed. These thresholds are based on the sum of fractions of the most concentrated influent waste stream, the volume of the mixture, and the classification of the final mixture. Waste is adequately blended if there is reasonable assurance that there are no hot spots of waste 0.2 m3 (7 cubic feet (ft3)) or larger in size that have a sum of fractions greater than 10 relative to the blended product." (p.19)

Procedure for the sum of fractions (p.19): "the first step in determining 'Sum of Fractions of the Most Concentrated Influent Waste Stream' ... is to determine the intended waste class of the blended product. Then the licensee should take the concentration of each nuclide in the most concentrated influent waste stream and divide it by the 10 CFR Part 61 concentration limit for that radionuclide, based on the waste class of the final blended product. The licensee should then sum the fractions over all the radionuclides in the influent waste stream."

Worked example (p.19): "if a licensee is blending waste to produce Class A waste, and the most concentrated influent waste stream has a 60Co concentration of 20,000 Ci/m3, the licensee would divide 20,000 Ci/m3 by the 10 CFR 61.55 Table 2 Column 1 (Class A) limit for 60Co of 700 Ci/m3. ... the 'Sum of Fractions of the Most Concentrated Influent Waste Stream' would be 20,000 Ci/m3 divided by 700 Ci/m3, which is 28. In this case, based on the CA BTP Table 1, the licensee would either need to limit the volume the waste is averaged over to less than 60 m3 (2,100 ft3) or provide a demonstration of adequate blending."

### 3.5 BTP Table 1 (p.20), reproduced in full (verified against a page render)

"Table 1. Thresholds for Demonstrating Adequate Blending" - columns: "Volume† of Mixture in m3 (ft3)"; row key "Characteristics of Most Concentrated Influent Waste Stream‡".

| Sum of fractions of most concentrated influent stream (‡ based on class of the blended product) | Class A mixture | Class B mixture | Class C mixture |
|---|---|---|---|
| less than 10 | No limit | No limit | No limit |
| between 10 and 20 | No limit | No limit | 50 (1800) |
| between 20 and 30 | 60 (2100) | No limit* | 20 (700) |
| between 30 and 50 | 20 (700) | No limit* | 6 (210) |
| between 50 and 100 | 6 (210) | 40 (1400)* | 2 (70) |

Footnotes as printed: "† Licensees using larger averaging volumes or more concentrated influent waste streams should demonstrate the waste has been adequately blended (see Section 3.2.3). ‡ Sum of fractions is based on the class of the blended product. * In the draft EIS for Part 61, class limits were derived based on a dose calculation. In the final EIS for Part 61, adjustments were made differently to the Class A, B, and C limits (see Volume 2, Section 4.6)."

Notes: (i) units are m3 with ft3 in parentheses; (ii) the table has no row above 100; the dagger footnote is the only statement for that case; (iii) the dagger footnote cites Sec. 3.2.3, but the blending demonstration text is in Sec. 3.2.2 (cross-reference slip in the source); (iv) the Class B "No limit*" and the larger Class B volume are explained in V2 p.88-89 (Class A 137Cs limit decay-adjusted exceeds Class B).

### 3.6 Demonstration, aggregation exception, and compatibility

- Demonstration (V1 p.19): "A demonstration that waste is adequately blended can be based on process knowledge, reasoned conclusions, calculations, or direct measurements." Alternative approaches may be justified (p.19, Sec. 3.8).
- Aggregation exception (V1 p.20): "If multiple waste streams of a single waste type generated at a licensee's facility are aggregated for the purposes of operational efficiency, occupational safety, or occupational dose reduction, the aggregated waste can be treated as a single waste stream for the purposes of this CA BTP (see Section 3.2.2). Table 1 of the CA BTP does not apply to this aggregated waste and no demonstration of adequate blending is needed. This reasoning does not apply to wastes that are blended after they have already been packaged separately for shipping (e.g., from a generator to a processor)-a demonstration of adequate blending may be necessary for such wastes." Fn.31: "This provision is not intended to apply to waste processors."
- Compatibility (V1 p.20-21): licensees combining blendable types with each other or with discrete types "should document the physical and chemical compatibility ... Wastes that could interact to cause undesirable chemical reactions (e.g., unacceptable amounts of hydrogen generation) should not be combined."
- If no practical way to demonstrate blending (p.21): "licensees should not exceed the threshold volumes in Table 1."

### 3.7 Solidified waste (V1 Sec. 3.2.3, p.21)

"if solidification is performed to create a physically uniform waste form in accordance with NRC guidance for solidification and industry standards, the average radionuclide concentrations in solidified waste may be based on the total volume or mass of the solidified waste form. ... nonradioactive materials used in solidification should have a purpose (e.g., stabilization of waste), other than lowering the waste class." Fn.33 (p.21): "Encapsulation is different from solidification in that a discrete item is placed in a non-radioactive matrix without creating a uniform concentration of radioactivity."

### 3.8 Technical basis for Table 1 (V2 Sec. 4.6, pp.85-90), for validation

V2 p.89-90: "the staff based the guidance on potential hot spots with a sum of fractions of 100." ... "a hot spot with a sum of fractions of 100 should be limited to 0.02 m3 (0.7 ft3) ... the BTP guidance limits hot spots with a sum of fractions of 10 to 0.2 m3 (7 ft3), which also limits a hot spot of 0.02 m3 (0.7 ft3) to a sum of fractions less than 100." V2 p.85: "if the radionuclide concentrations in the hot spot gave a sum of fractions of 10, the assumed hot spot volume was limited to 10 percent of the container volume. For a given hot spot concentration, meeting the class limit links the possible volume of the hot spot to the container size." Table 1 values rest on 137Cs (Class A, B) and 14C/241Pu/241Am (Class C) (V2 p.88).

---

## 4. Discrete items (V1 Sec. 3.3, pp.21-29)

### 4.1 Single discrete items (V1 Sec. 3.3.1, p.21-22)

"Individual discrete items may be classified based on the activity of their 10 CFR 61.55 radionuclides divided by the volume or weight of the item, as applicable. If an individual item is encapsulated, the concentration may be averaged over the volume or weight of the final waste form, including the encapsulating material, subject to the constraints in Section 3.3.4."

### 4.2 Volume basis for components (V1 Sec. 3.1.4 p.17, Sec. 3.5, BTP Table 4 p.34)

V1 p.17 (contaminated materials): "For individual items, classification may be determined by representative swipes or radiation survey measurements from which the total activity of radionuclides may be estimated through the use of scaling factors. In these cases, the volume or weight of the contaminated material should be the total mass or displaced volume of the item (i.e., major void volumes subtracted from envelope volume). For mixtures of these contaminated items, ... The volume or mass of the mixture should be the total mass or displaced volume of all the material contributing to the mixture." Fn.23: "For swipes or radiation survey measurements to be representative of the mixture, discrete items in the mixture should meet the Factors of 2 and 10 described in Section 3.3, as appropriate for the mixture."

BTP Table 4 p.34 rows (full table reproduced in Sec. 7 below): activated components, components containing radioactivity in their design, or contaminated materials: "Displaced volume (major void volumes subtracted from envelope volume) or mass of components."; cartridge filters treated as discrete: "Envelope volume or mass of filters. The envelope volume is the volume obtained using the outer dimensions of the filter (interstitial volume is included in the envelope volume)."

### 4.3 Screening criteria (V1 Sec. 3.3.2.1, p.22; Fig. 2 p.23)

"In classifying a mixture of discrete items of the same waste type, screening criteria may be used to simplify classification of the waste (Figure 2). These screening criteria are conservative, may provide efficiencies to licensees, and are optional. If these screening criteria are not used, the averaging constraints in Section 3.3.2.2 apply.
- If each item has an activity less than 37 MBq (1 mCi), the radionuclide activities may be divided over the volume or mass of the entire mixture.
- If any item has an activity greater than 37 MBq (1 mCi), the mixture may be conservatively classified according to the discrete item with the highest classification. For example, if a mixture ... includes a single item classified as Class C based on 10 CFR 61.55, and the remaining items are classified as Class A, the entire waste container may be conservatively classified as Class C LLW."

V2 p.63-64 (comment 12.h response): "the 37-MBq (1-mCi) constraint for primary gamma-emitting radionuclides is a simple and conservative screening criterion that licensees are not required to use ... If each item in a mixture has an activity level of 37 MBq (1 mCi) or less, the mixture can be concentration-averaged, without considerations of other criteria such as the Factor of 2 or 10 concentration limits. ... This activity constraint applies at the time of disposal".

### 4.4 Constraints for mixtures within a single waste type (V1 Sec. 3.3.2.2, pp.22-27; Figs. 3, Tables 2, 3)

**Which regime applies ("classification controlling").** V1 p.22 "If primary gamma-emitting radionuclides control the waste classification, more restrictive averaging constraints apply (see below). If radionuclides other than primary gamma-emitting radionuclides control the classification, less restrictive averaging constraints apply (see below)."

V1 p.25: "To determine whether the primary gamma emitters control the classification, the licensee should evaluate their relative significance as compared to the other radionuclides in the waste. The licensee should first determine the sums of fractions for 10 CFR 61.55 Tables 1 and 2. Then the licensee should determine which 10 CFR 61 table would result in a higher waste classification. This is the more restrictive table. If Tables 1 and 2 of 10 CFR 61.55 result in the same waste classification, the table that has the greater sum of fractions is the more restrictive table. Finally, the licensee should determine the fractional contribution that the primary gamma emitters make to the sum of fractions in the more restrictive table. If the primary gamma emitters contribute more than 50 percent of the sum of fractions in the more restrictive table, the primary gamma emitters are considered 'classification controlling.'" Fn.36 (p.25): "if the total sum of fractions for 10 CFR 61.55 Table 2 is 0.3, and 60Co and 137Cs together contribute 0.2 ... the primary gammas contribute more than half ... because 0.2/0.3 = 0.67 (67 percent)."

App. B item 2 (p.46): "Changed the definition of 'classification-controlling' to match the common-sense meaning of the term ..." and renamed the 1995 term "classification controlling radionuclides" to "radionuclides of concern".

**Choice of limit type for primary gammas.** V1 p.25: "For discrete items, the licensee has a choice of applying an activity limit or concentration limit. For primary gamma-emitting radionuclides, the activity limits are provided in Table 2. The appropriate concentration limit depends on whether the primary gamma-emitting radionuclides control the waste classification as follows:
- If the primary gamma-emitting radionuclides are classification-controlling, the appropriate concentration limit is the 'Factor of 2' concentration limit. The Factor of 2 concentration limit specifies that the concentration of each primary gamma-emitting nuclide in each item should be less than 2 times the classification limit for that nuclide.
- If the primary gamma-emitting radionuclides are not classification-controlling but are radionuclides of concern, the appropriate concentration limit is the 'Factor of 10' concentration limit. The Factor of 10 specifies that the concentration of each radionuclide of concern in each item should be less than 10 times the classification limit for that radionuclide."

App. B items 6, 7 (p.46): "Changed the 'Factor of 1.5' rule to a 'Factor of 2' rule for primary gamma emitters. In addition, both the Factor of 2 and Factor of 10 rules are now linked to the classification limit (Class A, B, or C) of the mixture rather than to the average concentration, making them more risk-informed." And: "Consolidated averaging constraints on discrete items, so that radionuclides in a discrete item must meet either the Table 2 activity constraint or the Factor of 2 or 10 concentration limit, as applicable, for primary gamma-emitting radionuclides, and either the Table 3 activity constraint or Factor of 10 concentration limit for other than primary gamma-emitting radionuclides."

(The old factor of 1.5 was relative to the package average; Rev. 1 uses a multiple of the class limit. V2 p.41: "The Factor of 2 is linked to the appropriate classification limit rather than being linked to the average of the mixture. In addition, the Factor of 2 is only applicable to the primary gamma emitters (i.e., 60Co, 137Cs, and 94Nb).")

### 4.5 BTP Table 2 (p.25) - primary gamma activity limits, reproduced in full (verified against a page render)

"Table 2. Recommended Activity Limits of Primary Gamma Emitters Potentially Requiring Piecemeal Consideration in Classification Determinations"

| Nuclide | Waste Classified as Class A | Class B | Class C |
|---|---|---|---|
| 60Co | 5.2 TBq (140 Ci) | No limit | No limit |
| 94Nb | 37 MBq (1 mCi) | 37 MBq (1 mCi) | 37 MBq (1 mCi) |
| 137Cs | 266 MBq (7.2 mCi) | 27 GBq (0.72 Ci) | 4.8 TBq (130 Ci) |

Basis (V2 p.81-82): small-item carry-away scenario, items "< 280 cc (0.01 ft3)", "pocketable"; 137Cs values "used directly ... without adjustment"; for 60Co Class A "engineering judgment was used to increase this value from 4.4 TBq to 5.2 TBq (120 Ci to 140 Ci)" because "it is the amount of 60Co that could be disposed in a 0.2-m3 (~55-gallon) drum at the Class A limit"; 60Co Class B/C: "the class limit alone was sufficient to protect an inadvertent intruder, and there was no need for an additional constraint in Table 2"; for 94Nb "the activity constraint at the time of disposal would be less than 37 MBq (1 mCi) for Class A (100 years), Class B (300 years), and Class C (500 years) disposal. However, the higher limit of 37 MBq (1 mCi) was selected for practical considerations."

### 4.6 BTP Table 3 (p.26) - non-primary-gamma activity limits, reproduced in full (verified against a page render)

"Table 3. Recommended Activity Limits of Radionuclides Other Than Primary Gamma Emitters Potentially Requiring Piecemeal Consideration in Classification Determinations"

| Nuclide* | For Waste Classified as Class A or B | For Waste Classified as Class C |
|---|---|---|
| 3H | 0.3 TBq (8 Ci) | No limit |
| 14C | 0.04 TBq (1 Ci) | 0.4 TBq (10 Ci) |
| 59Ni | 0.15 TBq (4 Ci) | 1.5 TBq (40 Ci) |
| 63Ni | 0.26 TBq (7 Ci) | 55 TBq (1500 Ci) |
| Alpha-emitting transuranic (TRU) waste with half-life greater than 5 years (excluding 241Pu and 242Cm) | 111 MBq (3 mCi) | 1.1 GBq (30 mCi) |

Footnote: "* Other nuclides listed in the tables in 10 CFR 61.55 are not expected to be important in determining waste classification."

Basis (V2 p.84-85): "The Table 3 activity limits reflect the maximum activity that would be allowed by the class limit if the radioactivity is averaged over a 0.2-m3 (~55-gallon) drum. The Table 3 activity limits for radionuclides that have mass-based activity limits are based on the assumption that the encapsulated waste has an average density of 1.5 g/cm3 (94 lbs/ft3)." (My arithmetic check, using 40 Ci/m3 as the CFR Class A limit for H-3 **[outside the BTP]**: 40 x 0.2 = 8 Ci, matching the 3H entry.)

### 4.7 How Tables 2 and 3 and the Factors are applied (V1 p.26)

"The Table 2 limits should be applied using a sum of fractions. The Factors of 2 and 10 are applied separately to each radionuclide (i.e., a sum of fractions is not applied). For comparison to Table 2 values, items larger than 280 cc (0.01 ft3) may be treated individually. However, items smaller than 280 cc (0.01 ft3) should be grouped together. That is, the sum of fractions should be based on the total inventory of each primary gamma-emitting radionuclide in the items smaller than 280 cc (0.01 ft3). The Factors of 2 and 10 are applied to each item individually, irrespective of size."

"In addition to applying concentration averaging constraints to primary gamma emitting radionuclides, licensees should also apply concentration averaging constraints to radionuclides of concern that are not primary gamma emitters. The licensee may choose to apply either the Table 3 values or the Factor of 10 concentration limit to radionuclides of concern other than primary gamma-emitting radionuclides. Table 3 is applied separately to each radionuclide (i.e., the sum of fractions is not applied). The Factor of 10 and Table 3 are applied to each item individually, irrespective of size."

"For radionuclides of concern that are not represented in Tables 2 or 3, the Factor of 10 concentration limit should be applied."

Fn.37 (p.25): "In general, the Table 2 limits are less restrictive for small items, and the Factor of 2 concentration limit is less restrictive for larger items. Because both limits are protective of an inadvertent intruder, the licensee may choose the less restrictive limit." Fn.38 (p.26): same for Table 3 and the Factor of 10. Fn.42 (p.31): "The size at which the Factor of 2 becomes less limiting is different for each radionuclide. ... Thus, a licensee may choose to apply the Table 2 limits to items larger than 280 cc (0.01 ft3)." V2 p.50: "Showing that either the Table 2 limit or the Factor of 2 has been met is sufficient. In choosing between these limits, the licensee is not restricted by the size of the item."

**Failing items (p.26):** "If an item in the mixture cannot meet the constraints described in this section, the item should be removed from the average and classified as an individual item in accordance with Section 3.3.1. The classification of the remaining mixture may be based on the volumetrically averaged or weight-averaged concentrations of the mixture." Fn.39: "If items smaller than 280 cc (0.01 ft3) collectively exceed the Table 2 limits, using a sum of fractions, and they do not individually meet the Factor of 2 or 10 (as applicable), their concentrations should not be averaged to meet the Factor of 2 or 10."

**Worked examples (p.27).** Example 1: "If 94Nb is classification-controlling in a Class A mixture of discrete items that are each larger than 280 cc (0.01 ft3), then each individual item could have an 94Nb activity of 37 MBq (1 mCi) (the Table 2 value) or a concentration up to 1.5 GBq/m3 (0.04 Ci/m3) (two times the Class A limit for 94Nb)." Example 2 (primary gammas not controlling): "...or a concentration up to 7.5 GBq/m3 (0.2 Ci/m3) (ten times the Class A limit for 94Nb)." These imply Class A 94Nb limit = 0.02 Ci/m3 in the CFR [outside the BTP: Table 2 Col. 1 for Nb-94 in activated metal is 0.02 Ci/m3].

**Flowchart (Fig. 3, p.24; text of the figure, read from render).** "Primary gamma emitters control classification?" Yes -> "Primary gamma emitters in each item* < Table 2 values or < 2X class limit?"; No -> "Primary gamma emitters in each item* < Table 2 values or < 10X class limit?". Then "Table 3 radionuclides of concern in each item < Table 3 value or < 10X class limit?" then "All remaining radionuclides of concern in each item < 10X class limit?" If all Yes: "Average activity over total waste volume or mass and classify in accordance with 10 CFR 61.55". Any No: "Manage item with elevated radionuclide concentrations in accordance with Figure 5 (Encapsulation) or using Alternative Approach (Section 3.8) and classify remaining mixture per this figure". Footnote: "* For comparison to Table 2 values, items larger than 280 cc (0.01 ft3) may be treated individually. Items smaller than 280 cc (0.01 ft3) should be grouped together".

**Basis of the Factor of 2 and 10 (V2 p.83-84).** Large-item scenario: a 2.55 m3 container, 1.7 m3 activated stainless steel; "Assuming that 50 percent of the items had an 94Nb concentration twice the Class C limit resulted in a projected dose of 5 mSv (500 mrem) ... individual pieces of activated metal should not exceed two times the Class C limit ... This rule is based on consideration of a Class C mixture, but would be protective if applied to a Class A or B mixture. This Factor of 2 concentration limit ... also places an absolute limit on the boundary between Class C and GTCC waste for discrete items in a mixture in which the primary gamma emitters control the classification." V2 p.84: "The Factor of 10 provides an absolute limit on the boundary between Class C and GTCC waste for discrete items."

### 4.8 Sectioned components (V1 Sec. 3.3.2.3, p.27; Fig. 4 p.28)

"If a component is sectioned for operational considerations (e.g., packaging for transportation), the activities of the pieces may be averaged over the volume (or mass, as appropriate) of the original larger component, provided that the individual pieces are all placed in the same container and the activities of radionuclides are less than the Table 2 and Table 3 values. For sectioning, meeting the Table 2 criteria means that no piece that is smaller than 280 cc (0.01 ft3) has primary gamma activity that exceeds the values shown in Table 2. Meeting the Table 3 criteria means that no individual piece of any size has activity that exceeds the values shown in Table 3. As for individual items, the Table 2 limits are applied using a sum of fractions and the Table 3 limits are applied to each radionuclide individually." Example 3: the four pieces "are assessed as a single component in determining whether the averaging and classification constraints for discrete items are fulfilled."

Fig. 4 (render): "All pieces in one package?" No -> "Classify pieces as part of mixtures of discrete items in each package (Figure 2)". Yes -> "Table 3 radionuclides in each piece < Table 3 values?" No -> classify offending pieces as individual items or mixture; remaining pieces as a single unsectioned component. Yes -> "All pieces larger than 280 cc?" Yes -> "Classify based on characteristics of original component (before sectioning)"; No -> "Primary gamma activity of pieces < 280 cc less than Table 2 values?" Yes -> classify on original component; No -> offending pieces individually/mixture, remaining as single unsectioned component.

### 4.9 Cartridge filters (V1 Sec. 3.3.3, p.29; also p.18, Table 4)

"Cartridge filters do not necessarily need to be treated as discrete items. Cartridge filters that do not contain significant amounts or concentrations of long-lived gamma radioactivity may be treated as blendable waste for the purpose of concentration averaging, with justification. A justification should be documented and made available for inspection." The justification should address "A history of the activity levels of primary gamma emitters in the filters covered by the analysis that is sufficient to demonstrate that their activity is within the Table 2 values in this CA BTP for the appropriate waste class and that the concentrations of non-primary gamma emitters are not greater than Class C"; the items covered; and physical characteristics (perforated housing; non-metallic degradable medium). "Individual cartridge filters that cannot be justified as blendable waste in accordance with the above are subject to the averaging constraints for discrete items". Reporting under 10 CFR 20 App. G I.C.10 and I.E.2 "continue to apply" (p.29; fn.26 p.18). Volume basis if blendable (Table 4 fn. a, p.34): "concentrations may be averaged over the total mass of the filters or their total envelope volume."

---

## 5. Encapsulation (V1 Sec. 3.3.4, pp.30-32; Fig. 5 p.32; Sec. 3.8.2 pp.37-38)

### 5.1 Definition and purpose (V1 p.30; p.12)

"Encapsulation is the process of surrounding discrete items of radioactive waste, such as sealed sources or cartridge filters, in a non-radioactive binding matrix, where the activity remains within the dimensions of the original item of waste. Encapsulation differs from waste solidification, in which radioactive waste is uniformly mixed into a binding matrix to create a physically uniform final waste form. ... However, the amount of credit allowed for encapsulation in the averaging of radionuclide concentrations to determine the classification of waste should be limited, so that extreme measures cannot be taken solely for the purposes of lowering the waste classification."

### 5.2 Volume / mass credit (V1 p.30)

"Containers up to 9.5 m3 (331 ft3) may be used for encapsulating waste provided that the minimum waste loading (volume of waste divided by the total volume, including encapsulating material) is at least 14 percent."

"For waste loadings less than 14 percent (such as a single sealed source), the maximum volume or mass used in determining the average radionuclide concentrations and waste class should be 0.2 m3 or 500 kg (approximately 55 gallons or 1,100 pounds). The volumes and masses may be larger than these values to allow for disposal of an item (such as a sealed source) in its shielded housing or source device or both, but no more than 0.2 m3 or 500 kg may be used in the calculation of waste class. The shape of the final encapsulated package does not have to be a cylinder."

"The minimum solid volume or mass used to encapsulate waste should be sufficient to make handling the radioactive waste by an inadvertent intruder prohibitively difficult. The size or weight of the encapsulated waste should be large enough to preclude movement without the assistance of mechanical equipment."

"Larger volumes for encapsulation (such as grouting of reactor internals into a reactor pressure vessel) may be proposed on a case-by-case basis in accordance with Section 3.8.4, 'Large Components.'"

BTP Table 4 (p.34) last row: "Encapsulated cartridge filters, sealed sources, or other wastes: The encapsulating medium may be included in the volume or mass, subject to certain constraints (see Section 3.3.4)."

**Number conflict (flag):** 9.5 m3 in V1 p.30 and V1 App. B item 11 ("container volumes up to 9.5 m3 (331 ft3)"); Fig. 5 (p.32) says "Is final waste volume ≤ 9.4 m3?"; V2 p.85 says "up to 9.4 m3 (331 cubic feet)" and V2 p.59 "smaller than 9.4 m3 (331 ft3)"; a commenter proposed "9.37 m3 (331 ft3)" (V2 p.58). 331 ft3 equals 9.37 m3. The parenthetical 331 ft3 is the only number common to all; 9.4 (or 9.37) is internally consistent with it, 9.5 is not. Also the threshold wording differs: "at least 14 percent" (V1 p.30), "≥ 14%" (Fig. 5), "> 14 percent" (V2 p.85).

### 5.3 Radioactivity constraints for encapsulated items (V1 p.30-31)

"The constraints on the amount of radioactivity or concentrations of individual encapsulated items are similar to those that apply to mixtures of discrete items addressed in Section 3.3.2. As in Section 3.3.2, Table 2 is applied using a sum of fractions. Because it has a different technical basis (see Volume 2, Section 4), Table 3 is applied to each item individually (i.e., a sum of fractions is not used). The Factors of 2 and 10 also are applied to each radionuclide individually. For comparison to Table 2 values, items larger than 280 cc (0.01 ft3) may be treated individually. However, items smaller than 280 cc (0.01 ft3) should be grouped together. That is, the sum of fractions should be based on the total inventory of each primary gamma-emitting radionuclide in the items smaller than 280 cc (0.01 ft3). The Factors of 2 and 10 are applied to each item individually, irrespective of size."

"For radionuclides other than primary gamma emitters, a licensee may choose either Table 3 or the Factor of 10 concentration limit for individual items. ... These constraints on non-primary gamma-emitting radionuclides are the same irrespective of which radionuclides control the waste classification."

"For the gamma emitters: If primary gamma-emitting radionuclides control the waste classification, the licensee may apply either the Factor of 2 concentration limit to each item; or the Table 2 activity limits. If non-primary gamma emitters control the waste classification, the licensee may apply either the Factor of 10 concentration limit to each item; or the Table 2 activity limits. A licensee may pick the less restrictive limits or may base the selection on other operational considerations. In choosing between these limits, the licensee is not restricted by the size of the item."

### 5.4 Total package cap and other conditions (V1 p.31-32)

"Multiple items may be encapsulated together, so long as the final encapsulated package meets the other criteria described herein."

"The maximum amount of any radioactive waste that should be encapsulated in a single disposal container is that which, when averaged over the waste and the encapsulating media, does not exceed the maximum concentration limits for Class C waste defined in Tables 1 and 2 of 10 CFR 61.55." Fn.43: "For mass-based concentrations (such as for transuranic radionuclides), it is generally acceptable to take credit for the actual density of the encapsulating material) if the density is less than 2.5 g/cc (156 lbs/ft3)." (V2 p.85: "up to 2.5 g/cm3 (156.1 lb/ft3)".)

"For encapsulating media that are described in an approved Topical Report, the terms and conditions associated with the Topical Report approval apply." Fn.44: list at ADAMS ML14063A124.

"In all cases in which one or more items are encapsulated, written procedures should be established to ensure that all such waste items are reasonably centered within the encapsulating media."

"For encapsulated materials, Class B and C waste must also meet the structural stability criteria of 10 CFR 61.56, 'Waste Characteristics,' and be segregated from disposal units with unstabilized Class A waste, as specified in 10 CFR 61.56(b)."

### 5.5 Fig. 5 (V1 p.32; read from render)

"From Figure 1" -> "Primary gammas emitters control classification?" Yes -> "Primary gamma emitters in each item^a < Table 2 values or < 2X class limit?"; No -> "... < Table 2 values or < 10X class limit?". Then "Each item < Table 3 or Factor of 10 constraint met for each item?" Then "Is waste loading^b ≥ 14%?" Yes -> "Is final waste volume ≤ 9.4 m3?" Yes -> "Classify based on encapsulation volume"; No -> Alternative Approach. Waste loading No -> "Classify based on the lesser of 0.2 m3 or encapsulation volume". Any other No -> "Use Alternative Approach (Section 3.8)". Footnote b: "Waste loading = (vol waste / total vol) x 100".

### 5.6 Sealed sources and alternatives (V1 Sec. 3.8.2, pp.37-38)

"The position on encapsulation in Section 3.3.4 is considered generally suitable for all LLW disposal facilities ... Other provisions may be used on a specific basis ... if ... there is reasonable assurance of compliance with the inadvertent intruder performance objective in 10 CFR 61.42." Example: "a 33 TBq (900 Ci) 137Cs sealed source in a shielded cask buried 12 m (40 ft) deep, in a 0.5-m3 (18-ft3) encapsulated waste form, a 10 CFR 61.58 authorization or an exemption is not necessary, because 33 TBq (900 Ci) of 137Cs in 0.5 m3 (18 ft3) is within the 10 CFR 61.55 Class C limits of 4,600 Ci/m3 (4.8 TBq/ft3) for 137Cs. Alternative proposals for encapsulating individual items that would exceed the 10 CFR 61.55 waste classification limits would require use of 10 CFR 61.58 or an exemption." (p.37). The BTP does not give a separate sealed-source activity table other than BTP Tables 2 and 3 above; sealed sources are subject to those. V2 p.48-49 states "the revised CA BTP does not distinguish between sealed sources and activated metal items in terms of averaging constraints."

### 5.6a Technical basis of encapsulation limits (V2 Sec. 4.5, pp.84-85)

"For low waste loadings (i.e., < 14 percent) the CA BTP constrains averaging by placing an upper limit on the volume or mass of encapsulating material over which radionuclide concentrations should be averaged and the activities that can be encapsulated. The volume and mass limits are 0.2 m3 or 500 kg ... and the activity limits are provided by Tables 2 and 3." Fn.36: "For encapsulation, the Table 2 limits apply, on a sum-of-fractions basis, to the sum of the activities of all of the items smaller than 280 cc (0.01 ft3), and individually to items larger than 280 cc (0.01 ft3). The Table 3 limits apply to each item individually and a sum of fractions is not required." V2 p.58: "The staff has previously approved a minimum waste loading of 14 percent in a topical report (NRC, 1999)".

---

## 6. Alternative approaches (V1 Sec. 3.7, 3.8, pp.35-41)

(The "Section 3.9"-type provision in the 1995/2012 numbering is Sec. 3.8 in this document; Sec. 3.9 here is "Implementation".)

- V1 Sec. 3.7 p.35: 10 CFR 61.58 alternatives; "The averaging constraints in this guidance were not developed for and do not have to be applied to 10 CFR 61.58 authorizations." Also: "These alternative provisions would not affect the generic waste classifications established in 10 CFR 61.55. Thus, the radionuclide concentrations in the waste define the class of the waste in accordance with the 10 CFR 61.55 waste classification tables."
- V1 Sec. 3.8 p.35-36: "Other averaging methods may be used, however ..." "Alternative approaches as described in this section or other averaging approaches as may be reviewed by the disposal facility's regulator may be acceptable methods to demonstrate compliance with the provisions to determine the concentration of waste under 10 CFR 61.55(a)(8), depending on site-specific conditions. Use of 10 CFR 61.58 or an exemption is not necessary to invoke the provisions of this section." And "NRC staff strongly urges licensees to submit proposals for alternative approaches to the regulator for the disposal facility ... before classifying waste using the alternative approach."
- 3.8.1 site-specific intruder assessments (p.36-37); 3.8.2 encapsulation (above); 3.8.3 likelihood of intrusion (p.38-39), ending: "This provision for alternative approaches in this CA BTP cannot be used, however, to justify or approve alternatives to the waste classification tables in 10 CFR 61.55."; 3.8.4 large components (p.40): "regulators have approved the disposal of a reactor vessel with highly radioactive internal hardware grouted into the vessel cavity using averaging approaches unique to the waste types involved. In 1998, the State of Washington authorized the Portland Gas and Electric Co. to dispose of the Trojan nuclear plant reactor vessel with components grouted into the vessel and averaged over the volume of the vessel"; 3.8.5 time of intrusion into blendable waste (p.40): intrusion at 500 years instead of 100/300 "if Class A or B waste is disposed of with a barrier that meets the requirements of 10 CFR 61.52(a)(2) for Class C waste"; 3.8.6 legacy wastes (p.40-41).
- V2 p.42 (comment 3.c response): "licensees could propose an alternative approach employing a Factor of 10 concentration limit for 94Nb in activated metal at a specific site, in accordance with the alternative approaches in Section 3.8."

These are site/regulator-specific. A generic software implementation of Secs. 3.2 to 3.5 should not claim any alternative approach; they require disposal-regulator review.

---

## 7. BTP Table 4 (V1 p.34), full reproduction

"Table 4. Volume and Mass for Determination of Concentration" (column headings: "Waste" | "Allowable Classification Volume or Mass")

| Waste | Allowable classification volume or mass |
|---|---|
| Single blendable waste stream | "If a waste container is more than 90% full of waste (by volume), the nominal interior volume ('fill volume') of the container may be used. For bulk waste, or for waste containers that are less than 90% full, the actual waste volume or mass should be used.^a" |
| Two or more blendable waste streams that have been combined (e.g., placed in a single container) but which have not been physically mixed together | "Table 1 volumes." |
| Blended waste (i.e., two or more blendable waste streams that have been physically mixed together) | "Table 1 volumes, or larger volumes if adequate blending is demonstrated." |
| Solidified waste (e.g., solidified liquid, solidified ion-exchange resins, or solidified shredded cartridge filters) | "Volume or mass of solidified waste form.^b" |
| Absorbed liquids | "Volume or mass of original liquid." |
| Cartridge filters treated as discrete items | "Envelope volume or mass of filters. The envelope volume is the volume obtained using the outer dimensions of the filter (interstitial volume is included in the envelope volume)." |
| Activated components, components containing radioactivity in their design, or contaminated materials | "Displaced volume (major void volumes subtracted from envelope volume) or mass of components." |
| Encapsulated cartridge filters, sealed sources, or other wastes | "The encapsulating medium may be included in the volume or mass, subject to certain constraints (see Section 3.3.4)." |

Footnotes: "a For ion-exchange resins, the volume and mass of the waste are the dewatered volume and mass. For cartridge filters treated as blendable waste, concentrations may be averaged over the total mass of the filters or their total envelope volume." "b Averaging over the solidified mass or volume is appropriate if the solidification creates a physically uniform waste form, in accord with relevant NRC guidance (NRC, 1991) and industry standards (e.g., ANSI, 1992) for solidified waste."

Note: "major void volumes" is not quantified anywhere in V1 or in the V2 sections read (I searched V2 for "void"; the hits concern container void space and 61.56(b)(3), not component voids). The BTP gives no numeric threshold for what counts as a "major" void.

---

## 8. Tables reproduced in this document

1. BTP Table 1 (V1 p.20), Sec. 3.5 above (blending thresholds).
2. BTP Table 2 (V1 p.25), Sec. 4.5 (primary gamma activity limits).
3. BTP Table 3 (V1 p.26), Sec. 4.6 (non-primary-gamma activity limits).
4. BTP Table 4 (V1 p.34), Sec. 7 (volume/mass basis).
There are no other numbered tables in Vol. 1. Figures 1 to 5 (flowcharts, pp.10, 23, 24, 28, 32) are summarized from page renders. There is no separate "sealed-source activity table"; sealed sources use BTP Tables 2 and 3.

---

## 9. Implementation-relevant rules (testable)

Each rule gives the quote it rests on. Where it needs interpretation, the interpretation is labelled.

R1. **Basis of concentration.** Average = total activity / volume (Ci/m3) or / mass (nCi/g for transuranics); volume may be mass/representative density. Quote: V1 p.16 "Weight may also be used and converted to equivalent volume for averaging purposes provided that a representative density is used"; fn.20 "Transuranic radionuclide concentration limits are specified as radioactivity per unit of weight."

R2. **Volume for an activated/contaminated component or mixture of them = displaced volume.** Quote: V1 Table 4 p.34 "Displaced volume (major void volumes subtracted from envelope volume) or mass of components"; V1 p.17 "The volume or mass of the mixture should be the total mass or displaced volume of all the material contributing to the mixture." (Consistent with the repo's current single-component rule.) For cartridge filters treated as discrete: envelope volume including interstitial volume.

R3. **Mixtures of different waste types: classify each type separately, package class = the highest.** Quote: V1 Sec. 3.4 p.33 "the classification of the entire mixture of waste types should be the highest classification of any of the individual waste types in the mixture. Individual waste types should be averaged in accordance with the provisions in Section 3.3. If discrete items are mixed with blendable wastes, the averaging constraints in Section 3.3 should be applied to the discrete items." Also no averaging across containers: V1 p.19 "Radionuclide concentrations should not be averaged over more than one container."

R4. **Optional screens for same-type discrete mixtures.** (a) every item activity < 37 MBq -> average over whole mixture volume/mass; (b) otherwise optionally classify the whole mixture as the highest class of any single item. Quote: V1 p.22 "If each item has an activity less than 37 MBq (1 mCi), the radionuclide activities may be divided over the volume or mass of the entire mixture. If any item has an activity greater than 37 MBq (1 mCi), the mixture may be conservatively classified according to the discrete item with the highest classification." Ambiguity: whether "activity" is total or primary-gamma-only (see Open Question Q4).

R5. **"Primary gammas are classification-controlling" test.** Compute sums of fractions for CFR Tables 1 and 2; the more restrictive table = the one giving the higher class, ties broken by larger sum of fractions; primary gammas (Co-60, Nb-94, Cs-137) control if they contribute strictly more than 50 percent of that table's sum of fractions. Quote: V1 p.25 "If the primary gamma emitters contribute more than 50 percent of the sum of fractions in the more restrictive table, the primary gamma emitters are considered 'classification controlling.'"

R6. **Per-item concentration constraints (Factor of 2 / Factor of 10).** If primary gammas control: each primary gamma nuclide in each item must have concentration < 2 x its class limit, or satisfy BTP Table 2 activity. If they do not control: each radionuclide of concern in each item < 10 x its class limit, or satisfy Table 2 (for primary gammas). Evaluated per nuclide with no sum of fractions, per item regardless of size, with the class limit being that of the mixture's class. Quotes: V1 p.25 ("should be less than 2 times the classification limit for that nuclide"; "less than 10 times the classification limit"); p.26 "The Factors of 2 and 10 are applied to each item individually, irrespective of size."; App. B item 6 "linked to the classification limit (Class A, B, or C) of the mixture rather than to the average concentration".

R7. **BTP Table 2 uses a sum of fractions and groups small items.** Items > 280 cc: each item's own sum of fractions over Co-60/Nb-94/Cs-137 against the Table 2 column for the mixture's class (<= 1). Items < 280 cc: pooled total inventory of each primary gamma nuclide over all such items, one sum of fractions. Quote: V1 p.26 "the sum of fractions should be based on the total inventory of each primary gamma-emitting radionuclide in the items smaller than 280 cc (0.01 ft3)." Table 2 values: Co-60 5.2 TBq (A), no limit (B, C); Nb-94 37 MBq (A, B, C); Cs-137 266 MBq (A), 27 GBq (B), 4.8 TBq (C).

R8. **Non-primary-gamma nuclides of concern.** Each item must satisfy, per nuclide and separately, either BTP Table 3 activity (H-3, C-14, Ni-59, Ni-63, alpha TRU > 5 y excluding Pu-241 and Cm-242; Class A/B vs Class C columns) or concentration < 10 x class limit; nuclides not in Tables 2 or 3 use the Factor of 10 only. Quote: V1 p.26 "The licensee may choose to apply either the Table 3 values or the Factor of 10 concentration limit ... Table 3 is applied separately to each radionuclide (i.e., the sum of fractions is not applied)."; "For radionuclides of concern that are not represented in Tables 2 or 3, the Factor of 10 concentration limit should be applied." The constraint set is "radionuclides of concern" (> 1 percent of the CFR limit; App. A p.45).

R9. **An item that fails is removed from the average.** It is classified on its own (Sec. 3.3.1) or sent to encapsulation/alternative; the rest are averaged volumetrically/gravimetrically. Small-item pool cannot rescue itself via the factors. Quote: V1 p.26 "the item should be removed from the average and classified as an individual item in accordance with Section 3.3.1. The classification of the remaining mixture may be based on the volumetrically averaged or weight-averaged concentrations of the mixture." Fn.39 for the pooled-small-items case.

R10. **Sectioned component.** Pieces in one package may be averaged over the original component's volume/mass only if no piece of any size exceeds Table 3 (each nuclide) and no piece < 280 cc has primary-gamma activity exceeding Table 2 (sum of fractions). Quote: V1 p.27 "meeting the Table 2 criteria means that no piece that is smaller than 280 cc (0.01 ft3) has primary gamma activity that exceeds the values shown in Table 2. Meeting the Table 3 criteria means that no individual piece of any size has activity that exceeds the values shown in Table 3."

R11. **Encapsulation credit.** If loading (waste volume / total volume including encapsulant) >= 14 percent and total volume <= 9.4 m3 (331 ft3; V1 text says 9.5), average over the encapsulated volume; if loading < 14 percent, average over at most 0.2 m3 or 500 kg (even if the physical package is larger); in both cases per-item Table 2/3 or Factor of 2/10 constraints apply and the average over waste + encapsulant must not exceed Class C limits. Quotes: V1 p.30 "Containers up to 9.5 m3 (331 ft3) may be used ... at least 14 percent"; "no more than 0.2 m3 or 500 kg may be used in the calculation of waste class"; V1 p.31 "does not exceed the maximum concentration limits for Class C waste defined in Tables 1 and 2 of 10 CFR 61.55"; Fig. 5 p.32 "Classify based on the lesser of 0.2 m3 or encapsulation volume".

R12. **Blendable waste volume rule.** Single blendable stream: container interior volume if waste >= 90 percent of container volume (strictly "more than 90%" in Table 4, "90 percent or more" in text), else actual waste volume or mass; absorbed liquid -> original liquid volume; solidified uniform form -> whole waste-form volume; two or more streams combined but not mixed -> BTP Table 1 volume thresholds on the sum of fractions of the most concentrated influent stream relative to the blended product class; above the threshold volume an adequate-blending demonstration is required (no hot spot >= 0.2 m3 with sum of fractions > 10). Quotes: V1 p.18, p.19, p.20 (Table 1), p.34 (Table 4).

---

## 10. Open questions and ambiguities in the source

Q1. **9.5 vs 9.4 m3** and **">=14%" vs "> 14%"** (see Sec. 5.2). The software needs one reading; 9.37 m3 = 331 ft3 would reconcile everything except the V1 text "9.5 m3". The parenthetical 331 ft3 appears everywhere.

Q2. **Meaning of "the classification limit" in the Factors of 2/10 for each class and each table.** The text says "less than 2 times the classification limit for that nuclide" (p.25) and App. B item 6 ties it to "the classification limit (Class A, B, or C) of the mixture". It does not state: (a) which CFR column applies for Table 1 nuclides (CFR Table 1 has one limit set; the BTP's own examples use Class A for Table 2 nuclides); (b) what the limit is for nuclides with no limit in the Class B or C column (e.g. Co-60 has none in CFR Table 2 Cols. 2 and 3 **[outside the BTP]**); BTP Table 2 also says "No limit" for Co-60 Class B/C. V2 p.83 says the factor-of-2 rule "is based on consideration of a Class C mixture". The BTP is silent on a Factor-of-2 test for a nuclide that has no class limit; an implementation must declare a rule (e.g. skip, or flag) and cannot cite the BTP for it.

Q3. **"Radionuclides of concern" column wording.** App. A p.45 says "1 percent of the applicable class-dependent concentration of that nuclide in Column 2 or 3 of Table 2". "Applicable" is not defined, and a Class A mixture would normally use Column 1. Also unaddressed: whether a primary gamma nuclide that is neither controlling nor "of concern" is subject to any item limit. V1 p.25 states the Factor of 10 only for those "not classification-controlling but ... radionuclides of concern".

Q4. **Total vs primary-gamma activity in the 37 MBq (1 mCi) screen.** V1 p.22 says "each item has an activity less than 37 MBq (1 mCi)"; V2 p.63 says "the 37-MBq (1-mCi) constraint for primary gamma-emitting radionuclides"; V1 uses "less than"/"greater than" (equality unaddressed), V2 uses "37 MBq (1 mCi) or less". Also the screening bullet says activities are "divided over the volume or mass of the entire mixture" without saying whether the average still has to be compared with Tables 1 and 2 of 10 CFR 61.55 (clearly intended).

Q5. **Boundary conditions.** "larger than 280 cc" vs "smaller than 280 cc" (an item of exactly 280 cc is in neither bucket; fn.42 treats "slightly larger" items as eligible for Table 2 individually); "less than 2 times" (strict) vs Table 2 "limits" (inclusive?); BTP Table 1 rows "less than 10", "between 10 and 20", "between 20 and 30" (shared endpoints at 10, 20, 30, 50; no row above 100 except the dagger footnote).

Q6. **Which volume for items in a "mixture" that includes both discrete and blendable types.** Sec. 3.4 says average each type per its own rules and take the highest class, and Fig. 2 says "Average concentrations within each waste type", but the text does not say whether the blendable portion's volume is its own waste volume or whether package fill/void rules interact (e.g. the 90 percent rule applies to a "single blendable waste stream" per Table 4, which a mixed package is not). Treat as unspecified.

Q7. **"Major void volumes" is not quantified.** See Sec. 7 note. Nor does the BTP say how voids inside an activated component's envelope are to be determined.

Q8. **Table 3's TRU row** is a group ("Alpha-emitting transuranic ... excluding 241Pu and 242Cm") but p.26 says Table 3 is "applied separately to each radionuclide (i.e., the sum of fractions is not applied)". It is not stated whether the 111 MBq (3 mCi) / 1.1 GBq (30 mCi) limit applies to each TRU nuclide or to their sum, per item. The mass-based nature of the TRU class limit (nCi/g) also means the Factor of 10 alternative is a mass concentration; the BTP does not say which mass (item mass) beyond Table 4 "mass of components".

Q9. **Waste loading definition.** "volume of waste divided by the total volume, including encapsulating material" (p.30). It does not say whether "volume of waste" for encapsulated discrete items is displaced volume, envelope volume, or source-device volume. "Where the activity remains within the dimensions of the original item" (p.30) suggests the original item.

Q10. **0.2 m3 vs 500 kg, which binds.** "the maximum volume or mass used in determining the average ... should be 0.2 m3 or 500 kg". Volume-based limits (Ci/m3) would naturally use 0.2 m3 and mass-based (nCi/g, TRU) 500 kg; the BTP does not say this explicitly **[inference]**. V2 p.85 notes Table 3's mass-based entries assume 1.5 g/cm3 density; the encapsulant density credit cap is 2.5 g/cc (p.31 fn.43).

Q11. **Figure vs text mismatches / typos in the source.** V1 Table 1's dagger footnote cites "Section 3.2.3" (blending demonstration is in Sec. 3.2.2); V1 App. B item 9 writes "0.02 m3 (55 gal.) drum" where the body says 0.2 m3; V2 p.41 cross-refs "Section 4.4" for the Factor of 2 basis, which is Sec. 4.3.2; V1 p.12 fn.11 vs p.17 fn.24 differ ("by a factor of 10" vs "by a factor of 10 or more").

Q12. **Blending of different types, discrete + blendable.** V1 p.18 says "Different waste types (e.g., resins and soils) can be blended if the licensee documents the physical and chemical compatibility", i.e. a documentation condition, not a numeric one; this cannot be tested numerically, only as a required attestation flag.

Q13. **Status.** The BTP is voluntary guidance and disposal-site license conditions override it (V1 p.41-42). Any software result is "consistent with the BTP" only where the site's license has adopted it "in whole or in part" (V1 p.7 fn.6: "In practice, current disposal facility licenses reference the CA BTP in whole or in part.").

Q14. **Not checked.** I did not cross-check the BTP's numeric examples against the current text of 10 CFR 61.55 tables (the CFR is not part of this task's text), apart from the two arithmetic sanity checks flagged **[outside the BTP]**. I did not read Vol. 2 Sec. 2, the rest of Sec. 3 (public comments on blending, scenario selection, Agreement State input), or the 2012 draft ML121170418.
