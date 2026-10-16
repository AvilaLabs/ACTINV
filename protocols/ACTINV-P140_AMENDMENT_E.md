# P140 Amendment E — the unmodelled-mode ledger count is emitted only when nonzero

Registered 2026-10-15, after the controls reruns on `cand11` and before any change it names. Candidate `cand11`,
SHA-256 `179e96a0…4536`.

## Evidence

Amendment B made `unmodelled_decay_branches_to_leakage` an always-emitted ledger key. In the local run of the CI
control steps on `cand11`, three frozen baselines change only because of that key, each with the value 0, and each
recovers its recorded value exactly when the key is removed:

- `controls/g6_p10_projectile_runtime.py`: the legacy neutron normalized hash (f2342695… instead of e6157b59…);
- `results/p15_cache_integrity.json`: `normalized_result_sha256` (1dc1d8d9… instead of 9853ccc8…);
- `results/g1_p110_composition_solve.json`: the nine `output_sha256` values of `actinv waste composition solve`.

The P110 record cannot be re-seated in isolation. `controls/check_p110.py` is itself hash-frozen in the P110 G0
seal, and the P110 records are hash-chained into `results/p110_verdict.json` and the G0 seals of P111–P117, so a
re-seat would rewrite sealed records of closed phases.

## Change

The ledger key `unmodelled_decay_branches_to_leakage` is written only when the count is nonzero, as
`decay_branching_sums_off_unity` already is; its absence means zero. When nonzero, the key and the certificate
defect class of Amendment B are unchanged. With this change no baseline is re-seated: the edited P10 constant and
the regenerated P15 record are reverted to their committed values.

The H2 criterion `reference_compatible` of `controls/check_p140_matrix.py` reads an absent key as 0 (Amendment B
required an integer); everything else in the criterion is unchanged. Amendment D's criterion is read the same way.

## Gates for the changed candidate (`cand12`)

- The workspace test suite, fmt and clippy `-D warnings`, with the Amendment B unit test changed to assert that
  the key is absent at zero, and the RTYP 2.6 chain test unchanged (count 1).
- H2 on `cand12`: PASS as written in the protocol and Amendment B, with the absent-as-zero reading.
- H4 on `cand12`: PASS, since `docs/LEDGER.md` changes.
- The local CI control steps P10, P15, and P110 through P117 pass against their committed records, unchanged.
- H3 is not rerun: the importer is unchanged. The controls reruns on `cand11` (entry 170) are not repeated: the
  only behavioural difference from `cand11` is the omission of a zero-valued ledger key, which every rerun
  checker's G3 and later gates do not read and which Amendment D masks. If H2 shows any other difference between
  `cand11` and `cand12` results, this carry-over is void and the controls are rerun.
