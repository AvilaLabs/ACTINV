# ACTINV P12 Amendment C — nested clean-clone target ownership

**Date:** 2026-08-27. **Trigger:** The first complete P12-G5 run passed its outer clean-clone compilation, package,
wheel, source-archive, standalone, end-to-end and P12 subset stages, then failed when the self-contained control tried
to execute its unit probe.

The release gate intentionally supplies an outer-clone `CARGO_TARGET_DIR`. The nested self-contained control inherited
that directory for its successful build but looked for the probe below its own clone. The executable was therefore
built in one controlled temporary directory and sought in another. No compiler, test, package, interface or scientific
result failed.

## Frozen repair

1. The self-contained control sets `CARGO_TARGET_DIR` to its own temporary clone before running any build or probe.
   Its redirected `HOME`, shared read-only toolchain/cache locations and external-data exception are unchanged.
2. P12-G5 prints each bounded subprocess before running it so long clean-clone executions expose progress. Captured
   stdout/stderr, timeouts, return-code handling and acceptance criteria are unchanged.
3. Re-run the self-contained control through the complete P12-G5 clean-clone path. It must build and execute the probe
   from the nested clone and leave that clone clean after every deterministic regeneration step.

This is a control-composition and observability repair. It changes no product source, data value, package content,
tolerance, scientific criterion or public-release authority. The eventual P12 verdict remains **P12-CONDITIONAL**.
