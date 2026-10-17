# P147 — Mixed packages of discrete items and encapsulation (NRC concentration-averaging BTP)

Registered 2026-10-17, before any source change for this phase. Owner direction 2026-10-17: implement discrete-item
mixtures and encapsulation; homogeneous blending is out of scope. The source is the NRC Concentration Averaging and
Encapsulation Branch Technical Position, Revision 1, Volume 1, February 2015 (ADAMS ML12254B065), with Volume 2
(ML12326A611) for the technical basis. Both were read in full and frozen as
`docs/maintainers/WASTE_BTP_SOURCE_REVIEW.md` before this protocol (G0). Below, "BTP Table n" and "CFR Table n"
(10 CFR 61.55) are always distinguished, and section and page citations refer to that review.

## Change under test

A new command `actinv waste package SPEC [OUT]` (spec schema `actinv-waste-package-spec-1`, result schema
`actinv-waste-package-1`) and its Python twin `actinv.waste.package`. One spec describes one disposal container: the
rule pack and nuclide properties as in `actinv waste classify`, the target steps, and a list of items. Each item has
an id, a BTP waste type (`activated_metal`, `contaminated_material`, `design_radioactivity_component`,
`sealed_source`, `cartridge_filter`), a mass, a displaced volume (an envelope volume for cartridge filters; BTP
Table 4), optionally the id of the original component it was sectioned from, whether it is encapsulated, and its
activities: either from an ACTINV run result (per-gram activities at each target step times the item mass) or
declared per nuclide and step (for sealed sources and measured items). An optional `encapsulation` block gives the
total encapsulated volume, the encapsulant mass and density, and the waste volume. When it is present, every item of
the package is encapsulated. An optional `screen` selects the
BTP §3.3.2.1 screens (`none`, the default; `activity_37mbq`; `highest_item`).

For each target step the command:

1. groups the items by waste type (BTP §3.4) and, for each group, applies BTP §3.3.2. It determines the more
   restrictive CFR table and whether the primary gamma emitters (Co-60, Nb-94, Cs-137) are classification-controlling
   (more than 50 % of that table's sum of fractions, BTP p.25). It then tests every item: primary gammas against BTP
   Table 2 (sum of fractions; items of 280 cc or more individually, smaller items pooled) or the Factor of 2 or 10
   (per nuclide, per item, against the class limit of the mixture's class); other radionuclides of concern against
   BTP Table 3 (per nuclide) or the Factor of 10, and those absent from Tables 2 and 3 against the Factor of 10 only.
   The licensee's choice of the less restrictive limit (BTP fn.37, fn.38) is applied per item and nuclide group, and
   the result states which limit each item met;
2. removes failing items from the average and classifies each alone (BTP §3.3.1), and classifies the remaining
   items on their volume- or mass-averaged concentrations (BTP p.26). The mixture's class enters the item tests, so
   for each candidate class X from A to C the items are tested against X's limits, the failing items are removed, and
   the remainder's averaged class Y is computed. The mixture class is the lowest X with Y ≤ X, reported as X (the
   items were tested at X's limits); if no X qualifies, the group is above Class C;
3. treats sectioned pieces of one component in the same package as that component when no piece exceeds BTP Table 3
   and no piece under 280 cc exceeds BTP Table 2 (BTP §3.3.2.3);
4. for encapsulated items, applies the same item tests, then the volume credit of BTP §3.3.4: the encapsulated
   volume when the waste loading is at least 14 % and the total volume is at most 331 ft3 (9.373 m3); otherwise at
   most 0.2 m3 (volume-based limits) or 500 kg (mass-based limits), and never more than the encapsulated volume or
   mass; the encapsulant mass credit is capped at 2.5 g/cm3; and the package average over waste and encapsulant must
   not exceed Class C;
5. reports the package class as the highest class of any waste-type group and of any item classified alone (BTP §3.4,
   conservative for failing items left in the package), with every intermediate quantity: per-group sums of fractions
   for both CFR tables, the restrictive table, the primary-gamma fraction, per-item test results and the limit met,
   the small-item pool, removed items, averaged concentrations, the encapsulation loading and credited volume or mass,
   and the Class C cap;
6. states coverage and what is not claimed: blending of homogeneous wastes (BTP §3.2), alternative approaches
   (BTP §3.8), 10 CFR 61.58 authorizations, site licence conditions (which override the BTP, BTP §3.9), structural
   stability under 10 CFR 61.56, centring procedures, compatibility documentation, and the encapsulation topical
   reports.

## Readings fixed before implementation (the BTP is ambiguous or silent)

- **Encapsulation volume.** BTP V1 says 9.5 m3 (331 ft3); Figure 5 and V2 say 9.4 m3. 331 ft3 = 9.373 m3 is common
  to every statement, and it is the most restrictive, so it is used. The loading threshold is "at least 14 %"
  (V1 text, Figure 5), i.e. ≥ 0.14.
- **Class limit in the Factors of 2 and 10.** This is the limit of the class being tested for the mixture (BTP App. B
  item 6). For a CFR Table 1 nuclide it is 0.1 × the Table 1 value for Class A and B and the Table 1 value for Class C
  (§61.55(a)(3)). The BTP's Nb-94 examples, 0.04 and 0.2 Ci/m3 in a Class A mixture, confirm this against the
  bundled rule pack. For a CFR Table 2 nuclide it is that class's column. When the column has no limit (Co-60, H-3
  and the under-5-year group in Classes B and C), the factor test does not apply to that nuclide in that class. The
  result says so, and the BTP is not cited for it.
- **Radionuclides of concern.** A nuclide in an item whose concentration exceeds 1 % of its class limit (as above)
  for the class being tested.
- **37 MBq screen.** The total activity of the item (V1 text; more restrictive than V2's primary-gamma wording),
  strictly less than 37 MBq.
- **280 cc.** Items of 280 cm3 or more are treated individually for BTP Table 2, and smaller items are pooled. An item
  of exactly 280 cm3 is individual (fn.42 allows Table 2 for larger items).
- **Inequalities.** A Factor test passes when the concentration is strictly less than the factor times the limit
  (BTP "less than"). A BTP Table 2 sum of fractions passes at ≤ 1, and a BTP Table 3 activity at ≤ the value
  ("limits"). The class determination uses the existing evaluator's CFR conventions unchanged.
- **BTP Table 3 TRU row.** Applied to the sum, per item, of the alpha-emitting transuranics with half-life above
  5 years (excluding Pu-241 and Cm-242). This is the conservative reading.
- **Waste loading.** The sum of the encapsulated items' displaced (or envelope) volumes, divided by the total
  encapsulated volume, unless the spec declares the waste volume.
- **0.2 m3 or 500 kg.** 0.2 m3 for volume-based (Ci/m3) limits and 500 kg for mass-based (nCi/g) limits, each
  capped at the actual encapsulated volume or credited mass.

## Required evidence

Only the coordinator runs executable jobs, serially, under the required systemd cgroup, with disk-backed `TMPDIR`.
Inputs are committed with the candidate, before any evidence run. No `.npz`, `.zip` or `.endf` files are tracked.

- **E1, independent recomputation.** An independent Python checker, given the spec, the bundled rule pack and the
  BTP data, recomputes every reported quantity of item 5 for:
  - a deterministic synthetic battery of at least 60 packages that reaches every branch of items 1 to 5 (the checker
    reports branch coverage, and each branch must be hit at least once);
  - at least three activation packages built from ACTINV run results: activated steel pieces of different sizes, some
    under 280 cm3, at two or more cooling steps, one with an encapsulated sealed source.

  Classes, statuses, choices and counts must be equal. Concentrations and sums must agree within 1e-12 relative.
- **E2, source fidelity.**
  - The bundled BTP Tables 2, 3 and 4 values and every threshold equal the frozen source review.
  - BTP Examples 1 and 2 are reproduced: Nb-94 at 37 MBq, or at 0.04 and 0.2 Ci/m3, in a Class A mixture of items
    over 280 cc.
  - The footnote-36 controlling case (0.2 of 0.3 = 67 %) is reproduced.
  - BTP Table 3 is consistent with the bundled class limits times 0.2 m3 (H-3, Ni-63) and at 1.5 g/cm3 (alpha TRU),
    as V2 states, within the BTP's rounding.
  - The §3.8.2 example of 33 TBq Cs-137 in 0.5 m3 falls inside the Class C cap.
- **E3, boundaries.** For every threshold (37 MBq, 280 cm3, Factors of 2 and 10, the 50 % controlling share, the 1 %
  concern level, 14 % loading, 9.373 m3, 0.2 m3, 500 kg, 2.5 g/cm3, BTP Table 2 sum of fractions 1, BTP Table 3
  values, the Class C cap), cases just below, at and just above it fall on the side the readings above fix.
- **E4, reductions.**
  - A package of one item, without encapsulation, reproduces `actinv waste classify` of the same component (class and
    sums) exactly.
  - Splitting an item into identical pieces that all pass leaves the averaged class unchanged.
  - A package whose items all pass gives the class of the averaged concentrations.
- **E5, refusals and determinism.**
  - Each malformed input is refused with its own message and writes nothing: unknown waste type; missing volume or
    mass; non-positive volume; encapsulation without encapsulated items, or encapsulated and unencapsulated items in one package; loading above 1; sectioned pieces in
    different packages; duplicate ids.
  - A repeated run is byte-identical.
- **E6.**
  - The P103 and P105 waste controls stay as committed: `actinv waste classify` and `actinv waste budget` outputs
    are byte-identical.
  - The `check_p145.py` U2 recomputation on the P145 committed result is unchanged.
  - Workspace tests, fmt, clippy `-D warnings` native and wasm, Python binding checks, module tests, handbook build,
    the P13 data-distribution step and the checker selftest, all run after the last edit.
  - CI green on the pushed commit for every triggered workflow.

## Verdict

P147-PASS if E1–E6 hold. A failing gate is preserved under `results/failures/p147/`; one repair round, registered as
an amendment first.

## Not claimed

Blending (BTP §3.2, BTP Table 1), cartridge filters justified as blendable (BTP §3.3.3 justification), alternative
approaches (BTP §3.8), 10 CFR 61.58 or exemptions, site licence conditions, 10 CFR 61.56 stability, centring and
compatibility documentation. The result is consistent with the BTP only under the readings above, and only where
the disposal site's licence adopts the BTP. It is not a package classification for shipment or a disposal
acceptance.
