# ACTINV P11 Amendment D — deterministic failure evidence

**Date:** 2026-08-27. **Parent protocol:** `ACTINV-P11_PROTOCOL.md`
(`fb9964d523e9bad8e2175ff24b5ca0e14982d9bfdf46962664a00a10925cc2d4`).

The final G5 rerun showed that two otherwise deterministic planted-failure diagnostics included the random temporary
directory which owned their control fixture. Return codes, publication checks, error meanings and every scientific or
provenance comparison were unchanged, but committing the literal directory would make the JSON evidence differ on
each execution and could retain a machine-specific path.

G5 now replaces only its own known temporary root with `<WORK>` when recording diagnostic tails. It still compares
the original return code and output path before normalization, and no production message, failure condition, physics
input, tolerance or scientific field changes. Repeated G5 runs now produce byte-identical evidence.

The successful phase verdict remains `P11-CONDITIONAL`.
