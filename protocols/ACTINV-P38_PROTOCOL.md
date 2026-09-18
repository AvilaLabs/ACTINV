# ACTINV P38 Protocol — lawful relaxation of the P37 strictness class

Frozen 2026-09-18 before implementation. P37 classified the twelve FENDL-3.2c
rejections: eight are `actinv_strictness` (legal ENDF-6 rejected by a
stricter-than-format contract), four are `true_defect`. This phase relaxes
exactly the two strictness mechanisms, rebuilds the FENDL-3.2c activation
artifact, and measures how much of the P26b identical-data arm becomes
executable. The four true-defect files remain rejected and are not touched.

## Scope

Two code changes, each mirroring an existing governed precedent:

1. **MF=10 self-comparator extension** (`crates/actinv-data/src/builder.rs`).
   The P25 Amendment B path for MT=18 uses the MF=10 partial sum as the
   runtime comparator, ledgered as internal completeness not anchored to an
   independent total. Extend that path to any non-fission MT that has MF=10
   products but no MF=3 section, with the identical caveat text pattern
   (`missing_total_self_comparator`). The independent-total path stays
   preferred whenever MF=3 exists; nothing about the emitted-row envelope
   check changes.

2. **LRF=7 negative-spin parity admission**
   (`crates/actinv-data/src/resonance.rs`). ENDF-6 encodes parity in the sign
   of the IA/IB spin fields; the validator currently rejects spin < 0.
   Admit signed spins (downstream statistics already use `.abs()`), keeping
   every other pair validation — mass, penetrability/shift flags, positive
   MT — unchanged.

Then rebuild `actinv_fendl32c_709` from the identical sealed source set and
re-score the P26b identical-data contract census.

## Acceptance gates

- G0: seal of the pre-change binary, the twelve failed files, the existing
  artifact, and the P26b contract.
- G1: both changes implemented with unit tests (signed-spin pair accepted;
  MF10-only non-fission section admitted and ledgered; inconsistent
  self-comparator still fails), full `cargo test`/`clippy`/`fmt` green,
  artifact rebuilt — expected 32/36 parents (the 8 strictness files now
  admitted; the 4 defects still rejected).
- G2: re-score the P26b identical-data leg census on the new artifact.
  Pre-measured expectation from the sealed contract's per-case `missing`
  sets: 912/1016 cases become executable (every case whose missing set is
  covered by the eight admitted isotopes); 104 remain contract_gap (100 on
  Ni-62, 4 each on W-182/W-184/W-186). Report the measured delta against
  this expectation and run the newly executable cases end-to-end.
- G3: negative controls — Ni62, W182, W184, W186 must STILL fail their
  original gates; a fabricated MF10-only section whose partials are
  internally inconsistent still fails; an RML pair violating any retained
  field still fails.
- G4: verdict records the exact coverage delta, names the unanchored-
  comparator caveat as a permanent limitation of the admitted rows, and
  restates that no defect file was admitted.

## Non-goals

No normalization or repair of the four defect files. No claim that the
identical-data arm is complete — Ni62, W182, W184, W186 remain excluded and
W/Ni-bearing contract cases stay gap-flagged. No relaxation of the
emitted-row envelope or any conservation check that has an independent
comparator.

## Dated amendment (execution findings, 2026-09-18)

Execution revealed scope changes recorded here rather than rewritten:

1. **Admit set is 7, not 8.** W-183 passed the two planned fixes
   (negative-spin parity, and a discovered third relaxation: zero-NRS spin
   groups carry a SAMMY 6-zero placeholder LIST row, tolerated only when
   NRS==0 and every value is exactly zero) but then failed a deeper gate —
   MT28/MF=10 states exceed the file's own MF=3 total by 2.109e-3. That is
   a data-level inconsistency, not a format question: W-183 reclassifies
   to `true_defect`. The P37 strictness class narrows to 7 files.
2. **Expected census unchanged**: 912/1016 executable; remaining 104
   blocked on Ni-62 (100) and W-182/183/184/186 (4 each).
3. **G2 result**: the leg now executes (40/40 sampled, zero arm failures)
   but the frozen tolerance fails comprehensively. The measured pattern —
   ALARA/ACTINV = 1.2x-200x at shutdown, 0.995-1.011 at 9y cooling —
   identifies the dominant residual divergence: lumped-channel MTs
   600-849 (discrete-state charged-particle production) are skipped by
   ACTINV's builder while FENDL and ALARA's REAC path carry them. The leg
   is therefore a coverage-gap measurement, not yet a solver comparison.
4. G3 confirmed all five defect files still fail their original gates.
