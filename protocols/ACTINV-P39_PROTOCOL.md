# ACTINV-P39: lumped-channel MT (600-849) coverage for FENDL artifacts

## Context

P38 executed the first identical-data cases and measured a channel-content
divergence: ACTINV's FENDL artifact lacks charged-particle production that
FENDL encodes in lumped-channel MTs (600-849, the ENDF-6 discrete-state +
continuum convention). ALARA's REAC conversion reads them. The gap is the
named next blocker for a valid identical-data comparison.

## Measured facts (sealed evidence: p38-run + verdict_p38.json)

- FENDL Fe-56 carries (n,p) only as MF3 MT600-609+649; ACTINV emits no
  (n,p) row. Fe-54 carries MT103 AND lumped 600-649; MT103 equals the
  lumped sum exactly (verified at 4 energies).
- Mn-55 carries (n,alpha) only as lumped 800-831; V-52 production absent.
- W-180's (n,p) is already emitted via MF10 MT103 partials (row 73180)
  under the P38 self-comparator path — lumped MF3 must not double-count.
- Lumped MTs exist only in MF3 (no MF8/9/10 counterparts) across all 31
  built parents.
- ENDF-6 family map: 600-649=(n,p) sum->MT103 residual(Z-1,A);
  650-699=(n,d)->104(Z-1,A-1); 700-749=(n,t)->105(Z-1,A-2);
  750-799=(n,3He)->106(Z-2,A-2); 800-849=(n,alpha)->107(Z-2,A-3).

## Change (bounded)

In `builder.rs`, after the main MT loop: for each lumped family present in
MF3, if the summary MT has NO coverage in `processed`, `evaluation.mf3`,
or `evaluation.mf10`, sum the family's collapsed MF3 tables and emit one
row as `(summary_mt, residual_zap, lfs=0)` with a ledgered
`lumped_channel_synthesis` note (excitation-level branching to residual
isomers not attributed — matches REAC lumped semantics). When summary
coverage exists, ledger the lumped sections as redundant and skip them.

Not changing: skip_mt's exclusion of the range in the main loop,
descriptor validation, envelopes, decay semantics.

## Expected outcomes (pre-registered)

- Artifact: Fe-56 emits (103, 25056), Mn-55 emits (107, 23052),
  Fe-54 unchanged (MT103 governs, lumped skipped), W-180 unchanged
  (MF10 MT103 governs).
- Identical-data leg: early-time ALARA/ACTINV ratios move toward ~1;
  the frozen 5e-4 tolerance still fails where other divergences live —
  the leg remains a coverage audit, not a solver claim.
- All previously rejected files still reject.

## Gates

- G0: seal commit, contract, inputs, expected row changes.
- G1: implementation + full test/clippy/fmt; artifact rebuild; per-target
  row diff vs p38 artifact recorded.
- G2: re-execute the 40-case identical-data sample on the new artifact;
  report the distribution change honestly.
- G3: negative controls — summary-MT double-count guard exercised
  (Fe-54/W-180 rows unchanged), a file with lumped-only channels emits
  synthesized rows, previous defect files still reject.
- G4: scoped verdict; roadmap update; commit.
