# P147 Amendment A — readings fixed during implementation, before the evidence

Registered 2026-10-17, after implementation and before the candidate commit and any evidence run. The protocol and its
fixed readings stand. This amendment fixes six points the protocol leaves open, and it discloses the development runs
made before it. No gate, tolerance or threshold is relaxed.

## 1. Sectioned components are merged per candidate class (item 3)

The protocol tests sectioned pieces against BTP Table 3, and against BTP Table 2 for pieces under 280 cm3, but it
does not say in which class column. The item tests of item 1 use the limits of the candidate class X. The merge
therefore uses X's columns as well, and it is decided again for each candidate. A piece passes if it is within
Table 3 at X, and, when it is under 280 cm3, its own Table 2 sum of fractions at X is at most 1. Two or more passing
pieces form one item named by the component's `section_of` id, which takes the place of its first passing piece.
Failing pieces are separate items.

The step reports the sections of the chosen candidate. When no candidate qualifies, it reports Class C's sections.
Under a screen the merge uses the class of the group's average. A group's `items` lists the original item ids. An item
test names a merged component by its component id.

## 2. A lone sectioned piece is refused (item 3, E5)

A `section_of` value carried by only one item in the package means the component's other pieces are in another
package. The command refuses the spec. This is E5's "sectioned pieces in different packages". The checker adds this
refusal to E5, so the synthetic battery no longer contains a single-piece section.

## 3. The alpha transuranic group is one entry (item 1)

The fixed reading sums the BTP Table 3 transuranic row per item. The CFR Table 1 limit of that row (100 nCi/g) also
applies to the sum of its members. The concern level (1 % of the class limit) and the Factor of 10 for the group
therefore use the members' summed concentration, not each member's alone.

## 4. An encapsulated container is one waste form (item 4)

Every item of an encapsulated package forms one group, whatever its BTP waste type, because the container is one waste
form. The item tests then run on the items' own concentrations. Each candidate's remainder is classified over the
credited volume and mass. When the items mix activated metal (`activated_metal`) with types evaluated under the
general rule rows, the group is evaluated under both rule-row sets, item tests and the Class C cap included. The
groups are labelled `encapsulated_activated_metal_rules` and `encapsulated_general_rules`, and the higher class is
kept. With one rule-row set, the group is labelled `encapsulated`. The step's sections are those of the first group
reported, which is general rules when present.

## 5. Screens are not available with encapsulation (items 4 and 5, E5)

BTP Figure 5 has no screening step, so a spec with an `encapsulation` block and a `screen` other than `none` is
refused. E5 includes this refusal. The battery's random generator sets the screen to `none` for an encapsulated
package. It draws the screen before the encapsulation, so the seeded sequence is otherwise unchanged.

## 6. Empty targets

A spec with an empty `targets` list is refused with its own message (E5 requires distinct messages).

## Development runs disclosed

Before this amendment, on uncommitted code in the main checkout, the checker selftest and gates E1 to E5 were run
several times to align the command with the checker. The last full development run, after the changes below, matched
62 of 62 battery packages and 6 of 6 activation packages in E1, passed every case of E2 to E5, and passed the Rust
unit tests and clippy. Every gate recorded `pass: false` only because the tree was not clean.

These runs found and fixed the following:

- In the command:
  - The table sums of encapsulated groups used the items' own volume rather than the credited geometry.
  - The transuranic concern test was per member (point 3).
  - The empty-targets refusal shared its message with another refusal.
- In the checker:
  - Sections were merged once, at the class of the whole component, rather than per candidate (point 1).
  - Groups listed merged ids.
  - The mixed-rule-set handling of point 4 was missing.
  - The battery had the single-piece section and the screened encapsulated package of points 2 and 5.

Since those runs, no evidence input has changed except those battery packages and the two E5 refusals added above.
