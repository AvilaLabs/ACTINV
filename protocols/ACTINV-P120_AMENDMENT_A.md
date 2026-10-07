# P120 Amendment A — correction of P116's recorded disposition

Registered 2026-10-07, after the E1 baseline run and before any verifier edit. No method, evidence or verdict rule
of P120 changes.

The registered protocol's "Not claimed" section says "P116's PASS and P117/P118's FAIL dispositions". That is wrong.
P116's preserved disposition is **P116-FAIL**: its local scientific gates passed and its terminal failure was in CI
(`controls/check_p116_history.py` re-derives "P116-FAIL" with failed workflows `controls` and `fns-iron`). Read the
sentence as "P116's, P117's and P118's FAIL dispositions". Found while reading `check_p116_history.py` to make the
P120 edits.

Two further facts found while reading, recorded here so the implementation does not depend on unstated choices:

1. The sealed `results/g0_p116_twin_waste.json` control map (99 files) equals the Git objects at the P116
   implementation commit `2117f3b5df715ce832658f58250103789be845bb` (0 mismatches; 8 against `cb786f7`, 15 against
   `6db45f7`). Item 3 therefore reads control files from `2117f3b`, and production Rust from the sealed G0 map
   (`check_p116._g0_base(verify_current_sources=False)`, which already exists and is what `check_p116_verdict` uses
   for its independent derivation).
2. Field meanings after the change (item 5): `current_source_matches_except_p119_edits` (P118) and
   `current_source_matches_except_ci` (P117) remain `true` and now mean "the pinned commit's Git objects match the
   frozen map, and `ci.yml` matches its registered transition"; `all_100_rust_sources_match_git` (P116) now means
   the 100 Rust Git objects at `2117f3b` equal the sealed production map. The P120 result record lists these.
