# ACTINV P25b — Amendment 1: repair proposal and coverage-floor freeze

Frozen at G3 per the protocol's repair-proposal gate, before any repaired build is scored.
This is the phase's single permitted append-only amendment.

## Sole permitted repair

**R3 bound revision.** The G1 construction bound of 90 s per target was a bounded-build limit,
not a demonstrated defect: the decimal oracle found no genuine source inconsistency on four of the
six timeout files and could not parse the remaining two (recorded `oracle_error`, itself a bounded
outcome). The six `bound_limited_timeout` rows may be rebuilt once each under a 600 s bound:

- `tendl_2023`: Ta-181, Th-232, Np-237
- `fendl_32c`: Th-232, U-235, U-238

No code, data, definition or grammar changes are authorized by this amendment. A row that still
fails at 600 s keeps its `construction_error` class with the new message recorded verbatim.

## Coverage floors (frozen)

| candidate | floor (met at G1) | projected post-repair |
|---|---|---|
| eaf_2010 | 47 / 47 | 47 |
| tendl_2023 | 41 / 47 | ≤ 44 |
| fendl_32c | 34 / 47 | ≤ 37 |

A candidate whose post-repair `ok` count falls below its floor fails the coverage gate; projections
above the floor are ceiling estimates, not commitments.

## Explicitly non-repaired classes

- `builder_capability:mf6_law_-5` — implementing a new MF=6 law is feature work, not a bounded
  repair. TENDL-2023 U-235, U-238, Pu-239 stay `construction_failed:capability`.
- `builder_interp` — FENDL La-139's log-log-on-nonpositive limitation; the file's data carries no
  oracle defect. Stays `construction_failed:interp`.
- `product_state_conflict:mf8_mf9` — FENDL Al-27's MF=8/MF=9 product conflict is a source-data
  inconsistency; no data repair is permitted mid-phase.
- `format_misdetect_marker` — FENDL H-3, He-4, O-18 carry stray `EAF-2010` text and sit outside the
  eligible population; recorded `format_unsupported` permanently.
- `corpus_incomplete` — absent files cannot be repaired; coverage fact only.

## Status

Protocol-mandated freeze, not a post-failure repair round. Any *further* amendment after G4 work
begins counts against the phase as a repair need: a second amendment fails the phase.
