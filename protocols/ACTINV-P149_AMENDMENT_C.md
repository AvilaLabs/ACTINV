# P149 Amendment C — S6 repeat comparison without the timing field; S0 rerun in the controls' environment

Registered after evidence attempt 2 and before S0 and S6 are rerun. No study is weakened: S6 still runs 10 timed
repeats per row and requires them to be identical apart from timing fields, and S0 still regenerates every Table 3
control on the release binary. S1–S5 are unaffected.

## C1. What happened

Evidence attempt 2 ran S5 (Amendment B), S6 and the checker on the same binaries as attempt 1. S5 completed all
three cases. S6 stopped on its first row (`fe rate 16: repeats differ`) after 9 s, so no S6 record exists. The
checker then failed S0 and reported S6 missing. The attempt is preserved under `results/failures/p149/attempt2/`:
- the attempt-1 and attempt-2 run scripts and step summaries;
- the S6 driver log;
- the checker's statuses and errors;
- the attempt-1 S0 record;
- the binary hashes and the commit.

## C2. Causes

**S6.** The driver compared repeats by the SHA-256 of the whole `actinv run` output. That output carries the solve's
core time `ms`, so two runs never hash alike. Two further runs of the Fe `rate`/16 row differ from the stored output
in `ms` only. This contradicts the protocol, which requires repeats to be identical apart from timing fields. The
Amendment A development run used one repeat, so it could not show the defect.

**S0.** Attempt 1 started the S0 driver with the system interpreter, and the Table 3 controls inherit it. Three
controls could not start:
- `g4_p9_pulses_openmc` needs `openmc`, which that interpreter lacks;
- `g4_p12_fng` needs `h5py`, which that interpreter lacks;
- `g5_p9_alara` stopped when `ninja` tried to regenerate the pinned ALARA build. The build was configured with CMake
  4.2.3 installed under `/tmp/actinv-p9-cmake`, which a restart had cleared.

Each control left its committed record in place, so their Table 3 values in the attempt-1 S0 record are the
committed values, not regenerated ones. The checker caught this (`returncode 1, pass True`). The driver is not at
fault: the run script chose the interpreter.

**Binary identity.** The binary that ran attempts 1 and 2 is the workspace release build of `b07a763`: `actinv`
SHA-256 `8845e93e5a83ceebf68ad26ec676f925e3ccdbd9b814b5d42325dbd134fdd36b`, as recorded in both attempts'
`binaries.sha256`. Amendment B and ledger entry 218 give `7b76596b…`, which is wrong. The distributed v1.6.0
Linux binary is a separate CI build of the same commit; its hash differs.

## C3. Changes

`controls/p149_s6_scaling.py`:
- Each timed run hashes its result with the top-level `ms` removed (canonical JSON, sorted keys).
- Each row records `distinct_result_sha256_without_ms`, the sorted distinct hashes over its repeats. The driver
  still stops when there is more than one.
- Nothing else changes: cases, modes, orders, repeats, timings and memory.

`controls/check_p149.py`:
- S6 requires exactly one distinct hash per row.
- A selftest plant with two hashes must be caught. The selftest passes.

**S0 environment.** No driver change.
- The S0 driver runs with the interpreter that carries `openmc` 0.15.3 and `h5py`, the one S2–S4 already use.
- CMake 4.2.3 is restored at the configured path, from a disk-backed install linked there. `ninja` then
  regenerated the build files and had no work to do. The ALARA binary's SHA-256 was the same before and after:
  `7ef1d6324a611976bd19e24abf1472a1e0ca07d33e9b8760ae1c0673247877b0`.

## C4. Rerun and repeat

On the same binaries (`8845e93e…`), from fresh disk-backed work directories:
1. **Attempt 3:** S0 and S6, then the checker. The S1–S5 records of attempts 1 and 2 stand.
2. **Repeat:** S0–S4, S6 and S5 SS316 again, into a separate output directory with fresh work directories. Each
   record is compared with its evidence record, ignoring timing fields only.
   - S5 records the SHA-256 of the whole first-order `actinv run` output (`first_order.result_sha256`). That output
     carries `ms`, so the hash is timing-dependent. It is not compared. Instead, the two outputs it identifies are
     compared with `ms` removed.
   - For S5 Fe and Fe+Co, attempt 2 is the repeat of the attempt-1 partial record, which used the same seed and
     binary. Every first-order and sampling value is compared. The fields excluded are those Amendment B changed:
     the study file and its hash, the built-in comparison's `local_vs_nonlinear` and `tool_first_order` blocks,
     and the added `first_order_std` and `sampled_over_first_order`. The output hash is also excluded, because
     attempt 1's work directory was not kept. In this comparison every other field agrees.
   - A study whose repeat differs is reported as such, with the differing fields. It does not pass.

## C5. Development runs

Two `actinv run` executions of the Fe `rate`/16 S6 specification, on the release binary, inside the required cgroup.
Each differed from the attempt-2 output in `ms` only. These runs are not evidence.
