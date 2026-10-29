# waste intrusion-screen: does the 2026 draft intrusion table indicate a review?

`actinv waste intrusion-screen` classifies one 1 kg activated-metal container nominally under Part 61 and, separately,
screens it against the February 2026 draft NUREG-1556 Volume 22 Table 8-5 (not a dose calculation or an acceptance
decision). All values are artificial, including the site WAC (C-14, Nb-94) and the DOT reportable quantities (Co-60,
C-14).

`component-result.json` is a small hand-written stand-in for an `actinv run` result (only `entry_point`, `mode` and
`steps[].step`, `t_s`, `activity_Bq_per_g`) at two steps (10 y and 30 y), so that every nuclide has the Z and
half-life that `intrusion.json` supplies. A real result works the same way, but `nuclide_properties` must then list
every nuclide with positive activity at the target steps (an activation run has dozens), or the class is `unknown`.
To use a run result, produce it with `actinv run PROBLEM.json run-result.json`, point `waste_spec.input` at it and
set `targets` to its cooling steps.

```sh
actinv waste intrusion-screen examples/intrusion_screen/intrusion.json intrusion-result.json
```

Expected runtime: well under a second; no data files are read.

In `intrusion-result.json` look at `classification` (the nominal Part 61 class, as `actinv waste` reports it) and
`draft_intrusion_screen.targets[]`: `review_indicated`, `indeterminate` or `not_indicated_by_implemented_checks`, with
the rows that decide it. `disposal_acceptance` is always `not_assessed`.
