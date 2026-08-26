# ACTINV P9 Amendment A — G5/G6 repair-round record

**Date:** 2026-08-26. **Trigger:** the first staging execution of the external-code and CoNDERC controls did not
complete the P9 gate set. This amendment records the one repair pass allowed by the standing rules. It does not alter
the P9 physics, scope, data pins, reference histories, tolerances, or the rule that CoNDERC accuracy is reported rather
than post-hoc gated.

The repair pass covered these control and reporting defects before the complete gate set was rerun:

1. The official ALARA sample returned success, emitted its ten-pulse/five-second-delay schedule and produced its zone
   results, but this out-of-tree build did not print the verbose phrase `Solved problem.`. It also omitted element
   symbols in text rows (`-56` rather than `mn-56`/`fe-56`). G5 now requires the actual solver output section plus the
   two schedule markers, and distinguishes the two mass-56 rows by their unambiguous radioactive/stable half-lives.
   The ALARA rate, inventory, source, schedule and acceptance tolerance are unchanged.
2. The first G6 reader treated every whitespace token in the official FISPACT `fluxes_therm` file as a group value.
   The file contains 709 group values followed by a scalar normalization and the title `Thermal neutron`, so the
   reader stopped at the title before publishing a gate result. It now reads exactly 709 groups and separately records
   the normalization/title.
3. Inspection of the initially reported Dickens C/E exposed a source-unit premise hidden by the CSV header. The CSV
   says `MeV/f/s`, while UKAEA-R(18)003 defines the plotted pulse quantity as cooling time multiplied by
   power/fission. For the protocol's explicit `MeV s^-1 fission^-1` comparison, G6 now divides each Dickens value and
   uncertainty by its cooling time. The ACTINV calculation remains power divided by independently integrated
   fissions. The 20,000 s finite-irradiation values remain power divided by fission rate (`MeV/fission`). No measured
   value is changed or dropped; both archive and converted values are retained in every C/E row.
4. Strict Clippy under Rust 1.98 added two findings in new control probes: a constant-size `chunks_exact(2)` loop and a
   test module placed before a later implementation block. The mechanical `as_chunks::<2>()` conversion and test
   relocation change no production physics or wire value.

The repaired controls must still satisfy the original P9 protocol verbatim. Because named gate controls were executed
and repaired, a successful close is **P9-CONDITIONAL**, not P9-PASS.
