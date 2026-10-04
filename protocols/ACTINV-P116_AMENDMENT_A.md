# ACTINV P116 Amendment A — numeric representation and strict tritium keys

Register before repairing the observed first G1 failure. Original protocol
`cf5841dafbd2470604b36d75fdafa2e2fc730e33abb26f6e44a4068e6388a8aa`
and every previous phase remain immutable. This is P116's one repair round.

## Observed failure and exact original evidence

Initial G0 seal and full read-only replay each exited zero. All 25 executed
quality gates have actual integer zero exits, including 484 passed workspace
tests, zero failed and two ignored. The first CLI G1 campaign exited integer
one. It executed all 35 requests and 138 component-target comparisons with
byte-identical repeats. Its 186 comparison diagnostics all concern
`row_fractions[*].limit`: the independent pack contains integer JSON literals,
while production correctly emits the same values through an f64 field.
The checker mistakenly treats these scientific limits as structural integers.
The integer-count mutation is rejected by the comparison but its change guard
uses Python dictionary equality, which equates `4` and `4.0`. The original
`unknown_external_field` refusal also fails: serde ignores extra keys on the
internally tagged unit `not_applicable` variant despite enum-level
`deny_unknown_fields`. `required` has the same unit shape.

Preserve original source and evidence in local Git checkpoint
`cb786f75acc456cb0157c6d5f38d9b6308140d85`. The exact discovery
`results/failures/p116_initial/discovery.json` has SHA-256
`15f73cb664ff13a4ca75d7edcf098d2ee2ce557ff00f9d95cb23c366b612e441`.
Its source map contains exactly 200 original source/protocol/metadata files,
including the complete 100-file Rust population. Its retained map contains
exactly 88 evidence files; physical membership is those files plus discovery.
Require every source and retained blob to match that checkpoint and its map,
with safe regular nonsymlink paths and no missing, changed or extra entries.

The original G0 SHA is
`2b1b8cbaad1073be4b16e5d21f284d41702ad2c0b53a36e6ee7b7f4e3e32af93`;
the failed G1 SHA is
`75e94554ac5d9d909e5eac59bd676c649db685eb52ffbba8a2340d5603251c21`;
the failed G1 log SHA is
`a836211e55cbd461718935edf88e91b07f9ecd572007f9eadd0028623a15cde2`.
Verify all 25 actual original receipt/log identities and zero exits. G0/G1
were invoked directly, so their separately observed exits are explicitly
recorded by discovery; do not invent gate-recorder receipts for them.
No initial G2, G3, implementation record or CI qualification exists. Preserve
the complete failed G1 report, its exact diagnostics, unsuccessful mutation
and refusal, sentinel-overwriting output and all original source bytes.
Original G0 remains a round-zero pass; original G1 remains a failure.

## Sole permitted correction

Keep `controls/fixtures/p113/cases.json`, the independent arithmetic and
legacy oracles, all expected values and the complete request population
byte-identical. Compare only scientific `row_fractions[*].limit` values as
finite numeric quantities using the existing `rel_tol=1e-12`,
`abs_tol=1e-12`; JSON integer versus float representation carries no scientific
meaning for that f64 limit. Keep exact integer type/value comparisons for
counts, steps, tables, columns and contributor counts, exact booleans and
categorical values, and reject booleans/nonfinite values as numeric quantities.
Use canonical JSON bytes for the mutation's changed-document guard so a
count's integer-to-float change is observed and must still be rejected.
Add focused regressions for an integer-valued numeric limit, unequal limits,
boolean/nonfinite limits and unchanged strict structural integer checks.

Permit production edits only in `crates/actinv-cli/src/twin_waste.rs` to
enforce exact raw `external_tritium` object keys before typed deserialization:
`not_applicable` and `required` accept only `status`; `declared` requires
exactly `status`, `source`, `excludes_activation`, `activity_bq`. Existing
typed validation still checks values and field types. Keep original refusal
fixtures unchanged; do not replace the unit-variant refusal with a different
case. Add regressions for extra keys on both unit variants and on declared,
valid declarations, missing fields and preservation of the existing input
policy. No classification math or valid-request output changes are permitted.
The other 99 Rust files remain byte-identical to the original checkpoint.
Related parser discoveries in other entry points remain separately scoped.

## Registered round one and fresh qualification

Update only live P116 control/verdict/regression metadata needed to validate
this registered amendment and original evidence. Derive strict integer round
one from the exact amendment registry and pinned discovery/source checkpoint.
Bind complete original source/evidence maps, original G0/G1/log identities,
all 25 observed receipts and their exact physical archive membership in new
G0. Reject missing/changed/extra archive entries and altered Git identities.
Keep P115's historical round one separate from P116's current round one.

The new seal retains the complete original inherited 100-file Rust map and
separately binds all 100 current production Rust files. Report the original
population equality honestly; require the same 100 paths, precisely the
permitted twin file change, and byte equality for the other 99. G3 and source
verification bind the current production map to the new sealed G0 and exact
implementation Git tree. Current G3 round must equal the new sealed G0 round.

After verifying the archive, clear only the original active P116 receipt/log,
G0/G1 and resource-inspection destinations that have exact retained copies.
Execute all 32 quality gates afresh; adopt no initial observation. Rebuild
release before science. Seal and fully replay new G0 before fresh G1/G2,
then complete the unchanged full read-only replay. Preserve all 35 requests,
138 comparisons, minimum mutation/refusal counts, sentinels, assay/legacy
checks, raw byte repeats, caps and workstation limits. All six exact-commit
implementation workflows and every closure-push workflow must be green.

A further failed gate after this repair is terminal P116-FAIL and requires a
separately frozen successor. Never overwrite original failure evidence or
claim an unexecuted or unobserved-exit check passed.
