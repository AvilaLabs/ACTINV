# ACTINV-P182 Amendment A — the pruning totals tested against the reported bound

P182's one repair round. P182 failed G3 (ledger 334): all 268 problems were unchanged outside `ledger.rate_pruning`
and kept the same nuclides, but on four ENDF/B-VIII.1 FNS problems (F 1996 and 2000, Gd 2000, Mn 2000, 5-min
irradiations) the candidate's `kept_activity_Bq_per_g_bound` and `kept_heat_W_per_g_bound` exceeded the baseline's
by more than `1 + 1e-6` (ratios 1.0000069 to 1.0019, on values of 1e-47 to 3e-24 in Bq/g and W/g).

## Disclosure

Written after P182's whole record was seen: the comparisons, the four failures above and the recorded times. The
change below was chosen knowing that it passes those four problems. No output is rerun.

## Why the registered test was wrong

`ledger.rate_pruning` reports an overall bound on the activity and the heat that pruning can miss
(`activity_Bq_per_g_bound`, `heat_W_per_g_bound`) and splits each into a part from dropped nuclides and a part from
kept nuclides whose feeds were dropped (`dropped_…`, `kept_…`). The kept part comes from the bounds on dropped
nuclides, which the change makes looser where they fall below `1e-6 × bmin`. P182 held each of the six totals to
`1 + 1e-6` of its own baseline value. For a part 30 orders of magnitude below the overall bound, that asks for a
relative agreement no one reads: on the four problems the overall bounds moved by at most 1.4e-10 relative.

## Change

In **Pruning record**, the totals bullet becomes:

- the two overall totals (`activity_Bq_per_g_bound`, `heat_W_per_g_bound`) are at least the baseline's (within
  1e-12) and at most `1 + 1e-6` times it, as registered;
- each of the four parts (`dropped_activity_Bq_per_g_bound`, `dropped_heat_W_per_g_bound`,
  `kept_activity_Bq_per_g_bound`, `kept_heat_W_per_g_bound`) is at least the baseline's (within 1e-12), and exceeds
  it by at most `1e-6` times the baseline's overall total for the same quantity (activity or heat). Where that
  overall total is zero or absent, the part must equal the baseline's within 1e-12.

Every other comparison, gate, pin and sentence of P182 is unchanged.

## Procedure

`controls/check_p182a.py` reads P182's committed record (SHA-256 pinned), checks that every output it names is still
on disk with the recorded SHA-256, recompares every pair with P182's checker under the change above, and writes
`results/p182a_record.json` and `results/p182a_verdict.json`. G0 also requires this amendment's SHA-256 in
`protocols/protocol_hash.txt`. G1, G2 and G4 are P182's, evaluated on its record. G5: CI green on the evidence
commit. The outputs are deleted after the verdict.

P182A-PASS if G0–G5 hold. When quoted, P182's sentences go with this disclosure: the pruning-total test was amended
after the result was known; the results themselves were identical on all 268 problems under both tests.
