# P149 Amendment F — the revision release becomes 1.6.1; every study is regenerated on it, with Table 7 as S7

Registered after the P149 verdict on 1.6.0 (ledger 222) and before any run on 1.6.1. No study is weakened. Every
study is rerun on the new release and repeated on the same binary. The pruning comparison of Table 7 is added as
study S7, so that it also comes from the release binary.

## F1. What happened

S6 found a defect in 1.6.0: a trace-mode run with pathway output stopped with an index error on tungsten, 7 h,
without pruning (Amendment D, D2). 1.6.0 is already on PyPI, and a published version cannot be replaced. On
2026-10-21 the owner directed that the fix be released as 1.6.1 and that 1.6.1 be the revision release the paper
describes. The protocol requires every number in the revised paper to be regenerated on that release.

## F2. The release

1.6.1 changes one function: the pathway decomposition leaves out a source whose first product is the leakage state
(`attribute_pathway_column`, with a unit test). The leakage state is a sink and never a pathway target, so such a
source carries no chain. The solve is unchanged. The other changes are version numbers and release documents.

The evidence runs on the workspace release build of the 1.6.1 release commit `a36e5d3`, from a clean tracked tree.
Its commit and the SHA-256 of `actinv`, `prune_probe` and `libactinv.so` are recorded with the evidence. The ALARA
binary is unchanged (`7ef1d632…`).

## F3. S7, rate pruning (Table 7)

Table 7 came from P148's R1–R3 batch (`results/p148/runs/runs.json`). That batch ran on candidate `c2beaa0`, whose
source matches the 1.6.0 release apart from version numbers, but it was not the release binary. S7 reruns that batch on
the release `prune_probe`:
- driver `controls/p148_runs.py`, unchanged;
- the 132 FNS experiments and the two one-year iron histories;
- thresholds 1e-12 to 1e-2 atoms/g;
- every rate run recomputed independently and checked against unpruned CRAM-48 and CRAM-16 references at every step.

The driver's `runs.json` is stored as `results/p149/s7_pruning_runs.json`.

`controls/check_p149.py` adds `check_s7`, which requires:
- the six thresholds;
- all 132 experiments and a one-year history at each threshold;
- no R1 or R2 problem in any row, and both pass flags true;
- each threshold's summary equal to the summary recomputed from the rows.

Selftest plants a changed summary, a dominance violation and a missing experiment; all must be caught. The selftest
passes, and the check passes P148's record.

## F4. Runs

1. **Attempt 6 (evidence).** S0–S7 on the 1.6.1 binaries, from fresh disk-backed work directories, each study with
   the interpreter its 1.6.0 evidence used:
   - S0, S2, S3, S4 and S6: the interpreter with `openmc` and `h5py`;
   - S1 and S5: the system interpreter.
   The S0 environment is that of Amendment D. Then the checker.
2. **Repeat.** S0–S7 again on the same binaries into a separate directory, with fresh work directories. Each record
   is compared with its evidence record as in Amendment C, C4. Paths under the home directory are normalised, because
   evidence records are committed with them as `<HOME>`.
3. **Comparison with 1.6.0, for information.** Each 1.6.1 record is compared with its 1.6.0 counterpart, ignoring
   timing fields, release identity fields and hashes of outputs that name the release. The comparison does not
   decide any study. Expected differences:
   - the W, 7 h default-outputs run without pruning now completes;
   - nothing else should change.
   Any other difference is reported.

The 1.6.0 evidence, its repeat comparison and its verdict move to `results/p149_v1.6.0/` and
`results/p149_v1.6.0_verdict.json`. The 1.6.1 records take their places in `results/p149/` and
`results/p149_verdict.json`.

## F5. Development runs

None on 1.6.1 before registration. The new unit test and the existing trace-pathway tests ran in the local gates of
the release commit.
