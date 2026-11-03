# ACTINV-P186 — State-resolved (MF=10) coverage in the MF=33 band, qualified again

## Purpose

P186 qualifies again the change P185 registered: a state-resolved (MF=10) product row takes its reaction's MF=33
covariance, fully correlated with the reaction total, and the band reports partial coverage. The change has been on
master since `4ce2822`. P185 failed twice, both times on errors in the protocol, not in the change:
- **P185 (ledger 343):** the response list named Nb-92m `Nb92m`, which ACTINV calls `Nb92m1`.
- **P185 Amendment A (ledger 345):** G4 required Fe-53 to have a state-resolved parameter at every step. Fe-53's
  half-life is 8.5 min, so at 1 day after shutdown its activity is 0 and it has no sensitivity at all.

P185's purpose, data, problems and other gates are kept. Like P185, P186 does not show that the band is right.
Agreement with brute-force sampling is a later protocol (P187).

## Disclosure of what has been seen

Written after P185 Amendment A's full record (`results/p185a_verdict.json`) was seen:
- G1–G3 passed. Every band matched the checker to 1.2e-15 relative, and nothing else changed.
- Every relative standard deviation was seen at every step, baseline and candidate. Examples at 66 s after shutdown:
  Fe-53 0 → 40.1%, Al-26 0 → 28.2%, Nb-92m 0 → 24.9%, Co-58 0 → 5.04%, Co-60 0.21% → 17.3%, W-185 0.17% → 5.86%.
- Fe-53's entry at the last step: activity 0, no nonzero sensitivity, coverage `complete` with 0 parameters.

The change to G4 below was chosen knowing that it passes on those outputs. P186 runs everything again; the outputs
above are deleted first.

## What P186 changes in P185

- **Problem B:** the last response is the activity of `Nb92m1` (Nb-92m), as in P185 Amendment A.
- **G4's last condition:** "Fe-53 in A and Co-58 in B have a state-resolved parameter at every step where their
  activity is nonzero, and their activity is nonzero at one step at least." The rest of G4 is unchanged.
- **Sentence 2:** given for each response that has a state-resolved parameter at every step where its activity is
  nonzero.
- **Binaries:** the candidate is built from a clean checkout of P186's registration commit, whose Rust sources are
  `4ce2822`'s. The baseline is still `b7629fb`; its worktree and build under `target/p185-work` are reused.

Everything else is P185's: pins, problems, the bounded scope, G0–G3, G5, G6, tolerances and sentence 1.

## Procedure

`controls/p186.py` runs P185's sealed harness and checker (`controls/p185_harness.py`, `controls/check_p185.py`)
with the changes above. It writes `results/p186_record.json` and `results/p186_verdict.json`.
- G1's registration commit is P186's.
- G0 requires the SHA-256s of this protocol, of P185's protocol, harness and checker, and of `controls/p186.py` in
  `protocols/protocol_hash.txt`.

Commands, inside the bounded scope: `controls/p186.py build`, `observe` and `assemble`, then
`controls/p186.py check`.

P186-PASS if G0–G6 hold. One repair round (an amendment with a fresh run) is allowed. After the verdict the outputs
are deleted, along with the baseline worktree and its build.
