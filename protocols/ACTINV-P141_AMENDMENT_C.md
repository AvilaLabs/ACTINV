# P141 Amendment C — P13's pinned-dependency check names the new `zip` pin

Registered 2026-10-16, after the first Z4 local CI replay and before editing `controls/check_p13.py`.

## What happened

With `zip = "=9.0.4"` (P141 at `0dd6524`), the local replay of every `ci.yml` control step passed except two
(`results/failures/p141/z4_full_replay_attempt1.log`): "Native desktop build dependencies", which needs `sudo
apt-get` on the CI runner and is not a finding, and "P13 verified data-distribution contract". P13's source check
`pinned_network_dependencies` requires the literal `zip = { version = "=8.6.0"` (and `ureq = "=3.4.0"`) in
`crates/actinv-cli/Cargo.toml`. Its purpose is that network-facing dependencies stay exactly pinned; naming 8.6.0
makes it a guard on the present that forbids the move P141 makes. `check_p13.py` is not hash-pinned by any seal;
`check_p15.py` and `check_p16.py` invoke it.

## Change

In `controls/check_p13.py` the literal `=8.6.0` becomes `=9.0.4`. The check still demands an exact pin of both
dependencies; nothing else changes.

## Evidence

The P13, P15 and P16 CI steps exit 0 locally, then the full Z4 replay and CI on the pushed commit as P141 requires.
