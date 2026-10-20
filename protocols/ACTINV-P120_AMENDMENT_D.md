# P120 Amendment D — a third live guard in P110: the P148 ledger block in G1

Registered 2026-10-19, after the red `controls` run on the P148 evidence commit and before the restored P110 records
are committed. P120's rule applies unchanged: keep every check on the archived records; drop only the requirement
that the live code never change. Amendment B asks that any further live guard get its own amendment before it is
edited; this is that amendment. The owner approved the approach on 2026-10-19 ("sure tahts fine").

## What happened

The P148 evidence commit `c50fce2` (ledger entry 212) committed regenerated P110 G1 and G2 records as changed
baselines. `controls` run 39360320526 failed in the step "P110 native composition basis and witness verification":
the persisted P110 verdict, which pins the G1 and G2 record hashes, no longer matched its derivation. The other five
workflows on `c50fce2` passed. The records are in `results/failures/p148/r6_ci_attempt1/`.

Regenerating the verdict is not a repair: `results/p110_verdict.json` is frozen by the P113, P114, P115 and P116
history verifiers, and P111 and P112 derive the P110 verdict and compare it with the frozen file. With the sealed G1
and G2 records restored, those all pass, and the only remaining failure is the G1 replay: the nine
`case_evidence[].output_sha256` values differ.

The cause is the P148 ledger. G1 hashes the raw output of `actinv waste composition solve`, which embeds the run
ledger's `rate_pruning` block 9 to 15 times per output. P148 replaced the block's one bound field,
`removed_heat_W_per_g_bound`, by the certified activity and heat bounds and `unbounded_components`. In all nine P110
cases pruning is off (`threshold_atoms_per_g` 0, nothing dropped), so every one of the 120 blocks is the same
all-zero block. Outside those blocks the candidate's outputs are byte-identical to the pre-P148 outputs (P148 R5 raw
outputs from the binaries of `364a73e` and `c2beaa0`). Mapping each unpruned P148 block to the pre-P148 block makes
each candidate output byte-identical to its pre-P148 output, and each of the nine digests (after the Amendment C
version map) equal to its sealed value: `results/failures/p148/r6_ci_attempt1/p110_ledger_binding.json`.

## The change

Only `controls/check_p110.py` changes, plus a new regression file `controls/test_p120_amendment_d.py`, this
registration, its `protocol_hash.txt` line, a ledger entry, `MANIFEST.sha256` and the preserved failure records.
`results/g1_p110_composition_solve.json` and `results/g2_p110_composition_solve.json` return to their sealed bytes
(`f31e0f1`), reversing that part of `c50fce2`. `.github/workflows/ci.yml` and every other committed result are unchanged.

1. **G1.** Before `output_sha256` is computed, and before the Amendment C version map, each `rate_pruning` block
   that is exactly the unpruned P148 block (the nine fields in the solver's order, every bound `0.0`, `dropped` and
   `unbounded_components` empty, threshold `0.0`, one indentation throughout) is replaced by the pre-P148 block
   (`dropped` empty, `removed_heat_W_per_g_bound` `-0.0`, threshold `0.0`, same indentation). Nothing else is
   normalized. A block with any drop, nonzero bound or threshold, unbounded component, or any other field or byte is
   left as it is, so it changes the digest and fails G1. The repeat-run comparison, oracle validation, mutation and
   refusal checks and every other field use the raw output.
2. Unchanged: G0, the sealed G0, G1 and G2 records, the P110 verdict, its history, and the P111–P118 checks.

## Required evidence

Only the coordinator runs executable jobs, serially, under the required systemd cgroup, with disk-backed `TMPDIR`.

- **D1.** The preserved records above.
- **D2, regressions** in `controls/test_p120_amendment_d.py`: the unpruned block maps to the pre-P148 block and
  nothing else changes; output without the block is unchanged; a block with a dropped nuclide, a nonzero bound, a
  nonzero threshold, an unbounded component, a missing or extra field, or mixed indentation is not mapped; any other
  byte difference survives the map; G1's digest applies both registered maps. The Amendment C regressions still pass.
- **D3.** With the P148 candidate release binary, the CI steps P103 through P118 exit 0 exactly as `ci.yml` lists
  them, and `check_p110.py --no-write` reproduces the sealed G0, G1 and G2 records byte for byte.
- **D4.** CI green on the pushed commit for every triggered workflow.

## Not claimed

No preserved verdict changes. P110's records stay as sealed; they now also replay under the P148 ledger when no
pruning occurs, with the unpruned block and the version token the only bytes mapped. P110 does not exercise pruning,
so it neither tests nor depends on the P148 bound.
