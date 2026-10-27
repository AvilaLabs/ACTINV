# P155 Amendment B — corpus entries outside the phase's keywords; RESULT

Registered after development run 1 of the checker on the candidate (disclosed in the ledger) and before any evidence
run. No mapping gate is loosened.

## B1. G5: unsupported entries the phase does not own

G5 asks that the pypact decks import "with no unsupported keyword except those item 7 keeps". The pypact
`reference/test.i` deck gives two `TIME` cards in a row with no `ATOMS` between them. Under the importer's rule since
P140, a `TIME` runs at the next `ATOMS`, `STEP` or `SPECTRUM`, so the first interval is reported `unsupported` as
never run. The v1.6.1 reference reports the same entry. It concerns schedule translation, not a keyword of items 2–7.
G5 is read as: on every corpus deck, no entry for a keyword of items 2–7 is `unsupported` except those item 7 keeps,
and every other `unsupported` entry is also `unsupported` in the reference import of the same deck, so the phase adds
none. The remaining entries are listed in the record.

## B2. `RESULT`

Item 7 makes `RESULT` noted when the deck defines its inventory itself, and unsupported otherwise. The importer
refuses a deck with no material or no schedule step, so every converted deck defines and runs its own inventory.
`RESULT` is therefore always noted, wherever it stands in the deck. The "otherwise" case cannot arise in a converted
deck and has no G3 or G6 case.
