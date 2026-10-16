# P141 Amendment A — the Z1 P11 builds run with `--continue-on-error true`

Registered 2026-10-16, after the first Z1/Z2 run and before it is repeated. No criterion changes.

## What happened

The first run (`results/failures/p141/z1_z2_attempt1.json`, log beside it) gave: Z1 proton full build byte-identical
between the reference (`zip` 8.6.0) and candidate (`zip` 9.0.4) binaries; Z2 reads of all three shipped libraries
byte-identical, and both runs identical apart from the `ms` timing field. The Z1 P11 builds exited 1 under **both**
binaries before any `.npz` was written: `build-library` without `--continue-on-error` fails closed on
`n-Ni058.tendl` (MT103/MF=10 emitted state sum exceeds the runtime total by 4.3 %, outside both envelopes), a check
added after P11 was sealed. The evidence harness omitted the flag that every full-library build, including Z1's
proton build and the shipped libraries, uses. This is a defect of the harness, not a difference between the binaries.

## Change

The two P11 `build-library` calls add `--continue-on-error true`. Z1's P11 criterion is unchanged: the activation
library and the covariance sidecar must each be byte-identical between the binaries, and both builds must exit 0.
Only the P11 part of Z1 is repeated; the recorded Z1 proton and Z2 results stand.
