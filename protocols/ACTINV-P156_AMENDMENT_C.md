# P156 Amendment C — Pb-208 from the library's own working corpus

Registered after development run 2 (disclosed in the ledger) and before any evidence run. Only the source of one
file in the item-6 build directory changes.

## Finding that requires it

Development run 2, the item-6 command with Amendment B's `--continue-on-error true` on candidate `71f717b`, built 388
of the 389 selected targets in 32 min 6 s (peak RSS 464 MB, 48.9 MB table). Fe-54's 0 K elastic was clipped on 36
points between 3,491 and 3,517 eV, with the most negative value −1e-4 b. The one failure is `n-Pb208.tendl`:
`MAT=8237/MF=3/MT=1: nonfinite ENDF number 'NaN'`. The distributed TENDL-2025 Pb-208 file (SHA-256
`32249bf7…c4dc4137`) carries a literal `NaN`. The shipped activation library does not use that file. It is built from
the neutron working corpus, whose only difference from the official extraction is the two fail-closed Pb-208 numeric
field repairs frozen in P10 Amendments D/E (`docs/DATA.md`). The library index records that file's SHA-256 as
`86788a14563ecdb844628a6a455864874ba5f5bca9b8142c10a59ea00df87c72`. Natural lead is 52 % Pb-208, so without it a Pb or
PbLi material would have about half its atoms uncovered.

## C1. Build directory

`DIR` holds `n-Pb208.tendl` with SHA-256 `86788a14…f5bca9b8142c10a59ea00df87c72`, the P10 working-corpus file the
library index records, instead of the distributed file. Every other file is unchanged. G5's selection is unchanged;
its `files` check accepts this one SHA-256 for Pb-208 and requires the distributed hash for every other file. The
substitution is stated in the data notice and the release notes.
