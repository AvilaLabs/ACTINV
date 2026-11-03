# ACTINV-P185 Amendment A — Nb-92m named as ACTINV names it

This is P185's one repair round. P185 failed G1 (ledger 343): both binaries exited 1 on problem B with "uncertainty
response 'activity:Nb92m' names a nuclide absent from the decay library". ACTINV names an isomer by its level
number (`Nb92m1`), and the protocol's response list used `Nb92m`.

## Disclosure

- Written after P185's run. Of that run, only these were read: exit statuses, error messages, build results and
  the checker's G0 and G1.
- Problem A's two outputs exist, but no value in them was read. They are deleted before the fresh run.
- Nothing about any band has been seen since P185's registration.

## Change

In **Problems**, problem B's last response is the activity of `Nb92m1` (Nb-92m), not `Nb92m`.

Everything else in P185 is unchanged: problems, binaries, pins, gates, tolerances and sentences.
- The candidate is built from a clean checkout of this amendment's registration commit. Its Rust sources are
  those of P185's registration commit `4ce2822`.
- The baseline is still `b7629fb`. Its worktree and build under `target/p185-work` are reused; the build step
  rebuilds it if anything changed.

## Procedure

`controls/p185a.py` runs P185's sealed harness and checker (`controls/p185_harness.py`, `controls/check_p185.py`)
with three changes:
- the response name above;
- the record and verdict written to `results/p185a_record.json` and `results/p185a_verdict.json`;
- G1's registration commit taken as this amendment's.

G0 requires the SHA-256 of this amendment, of P185's protocol, harness and checker, and of `controls/p185a.py` in
`protocols/protocol_hash.txt`. A fresh run of all four problems follows:
- `controls/p185a.py build`, `observe` and `assemble`;
- then `controls/p185a.py check`.

P185A-PASS if G0–G6 hold. No further repair round is allowed.
