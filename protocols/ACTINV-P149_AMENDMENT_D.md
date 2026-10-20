# P149 Amendment D — S0 Table 3 inputs on disk; S6 records a failing default-output run

Registered after evidence attempt 3 and before S0 and S6 are rerun. No study is weakened, and no source changes. S0
still regenerates every Table 3 control on the release binary. S6 still times every row. A run that fails in the
release is reported as failed, with its message, and is not left out. S1–S5 are unaffected.

## D1. What happened

Attempt 3 ran S0 under Amendment C, with the interpreter that carries `openmc` and `h5py` and with CMake 4.2.3
restored. `g4_p9_pulses_openmc`, `g3_p11_sensitivity` and `g4_p11_propagation` completed. Two controls again
returned 1, so their values in the S0 record are the committed ones.

S6 completed every timed row for Fe, SS316 and W. Its separate run of W at `prune: none` with the default outputs
then stopped: `actinv run` panicked with `index out of bounds: the len is 3871 but the index is 3871` at
`crates/actinv-core/src/run.rs:6472`. The driver stopped there, so natural uranium did not run.

The S0 and S6 records, their logs and the checker report are preserved under `results/failures/p149/attempt3/`.

## D2. Causes

**`g4_p12_fng`.** The control reads the extracted Zenodo research data (record 10660030). It reads the location from
`ACTINV_P12_FNG_ROOT`, or else from a single `/tmp/actinv-p12-fng.*` directory. That directory had been cleared by a
restart, and the S0 driver sets only `ACTINV_P12_FNG_ARCHIVE`. Amendment C did not identify this cause; attempt 1 had
stopped earlier on the missing `h5py`.

**`g5_p9_alara`.** The control writes ALARA input with an absolute `dump_file` path under its work directory. ALARA
2.9.2 reads input tokens into a 64-byte buffer (`char token[64]`, `src/Input.C`). Under the required disk-backed
`TMPDIR` the path is longer than 63 characters, and ALARA reports `Error #100: Invalid token in input file`.
Under `/tmp` the path is 57 characters. This limit belongs to ALARA, not to ACTINV.

**S6, W.** This is a defect in the release, not in the driver. The pathway calculation runs when `pathways` is among the
outputs, as it is by default. It labels each source by its product row with `ch.keys[row]`. When a bulk nuclide's reaction
leads to a product with no decay data, the product row is the leakage state. That state's index equals the number
of nuclides (`chain.rs`, `new_leak = old_n_nuc + missing.len()`), so the lookup runs one past the end. The same W
case completed with rate and with reachability pruning. SS316 completed in all three modes. Why pruning avoids the
failure was not investigated. The timed rows request no pathways, so they are unaffected. P149 changes no
source, so the defect is recorded for the next release.

## D3. Changes

`controls/p149_s6_scaling.py`:
- A default-outputs run that fails is recorded with `failed: true`, its exit status and its message.
  - The message is the panic line and the line after it, or else the last line of standard error.
  - The thread id is removed from the message, because it varies between runs.
- The case and the remaining cases then continue.
- A failing timed row still stops the study.

`controls/check_p149.py`:
- S6 requires all four cases. The attempt-3 record shows that a partial S6 record passed.
- S6 requires each default-outputs run to have either a wall time or a failure with exit status and message.
- Selftest plants with a missing case and with a failure without a message must be caught. The selftest passes.

The S0 environment adds:
- `ACTINV_P12_FNG_ROOT`: a disk directory holding the six archive members the control reads, extracted from
  the cached archive. The control checks the archive and every member against its pinned SHA-256 values before
  use.
- `ACTINV_P9_WORK`: a fresh disk directory per run whose `dump_file` path stays under 64 characters (a short
  directory under the home directory).

## D4. Rerun and repeat

On the same binaries (`8845e93e…`):
1. **Attempt 4:** S0 and S6 as evidence, then the checker.
2. **Repeat:** S0 and S6 again into the repeat output directory, with fresh work directories, compared as in
   Amendment C, C4.

The repeats of S1–S4 and S5 SS316 under Amendment C, C4 are not affected. The Amendment C repeat of S6 runs the
unamended driver, so it stops at the same point. It is kept as a log only and is not the S6 repeat.
