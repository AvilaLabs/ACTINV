# P141 Amendment B — the Z1 covariance identity uses the Fe-56 input alone

Registered 2026-10-16, after the Amendment A rerun and before any further run. No criterion changes.

## What happened

Under Amendment A (`results/failures/p141/z1_p11_attempt2.json`): the two-input P11 activation build exits 0 under
both binaries and the two `.npz` files are byte-identical (SHA-256 `5939355f…`); `n-Ni058.tendl` is skipped by
`--continue-on-error`, identically. `build-covariance` then refuses under both binaries before writing: "covariance
input has 2 files but activation index has 1 distinct sources". The covariance builder requires its input
directory to match the activation index, which the skipped Ni-58 file no longer does. Again a harness defect, not a
difference between the binaries.

## Change

The two-input activation-library identity above stands as the Z1 activation evidence for the P11 inputs. For the
covariance sidecar, the harness builds an activation library and then the sidecar from a directory holding only the
pinned `n-Fe056.tendl`, with each binary. Criterion unchanged: both builds exit 0 and the library and sidecar are each
byte-identical between the binaries.
