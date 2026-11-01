# ACTINV-P181 Amendment A — ACTINV's arm again, on the ENDF/B-VIII.1 library

## Why, and what has been seen

P181 (`protocols/ACTINV-P181_PROTOCOL.md`) passed G0–G4 as a measurement (ledger 328 and 329), but no sentence may be
used: ACTINV's dose with its shipped TENDL-2025 library was 0.79–0.89 of OpenMC's at every size where both completed,
outside the [0.90, 1.10] bound that P181 calls "an engineering bound that catches a broken pipeline, not a test of
data". The protocol allows one repair round, registered as an amendment first. This is it.

**Written after seeing the result.** Everything in P181's record has been seen: the times, memory and doses of both
arms at every size. In addition, an unregistered diagnostic (ledger 329) reran ACTINV's arm at 8 elements on the
ENDF/B-VIII.1 709-group library, the evaluation OpenMC's arm uses: the dose ratio was 1.035, and per material the
steel and tungsten photon sources above 0.5 MeV differ between the two evaluations. That diagnostic's outputs are
not part of this amendment's record. No other size has been run on ENDF/B-VIII.1.

The change is the one the diagnostic points to: compare like with like. Because the bound exists to catch a broken
pipeline, it should not trip on the difference between two evaluations; with the same evaluation on both sides it
tests the pipeline alone.

## What changes

- **ACTINV's arm is run again at all six sizes** (8 to 32,768 elements) with the ENDF/B-VIII.1 709-group library
  that P178 built (`endfb81_all_709g.npz`, SHA-256 `a964a44e…`, P180's pin) in place of TENDL-2025. Everything else
  in the arm is P181's: the released 1.7.1 binary (`bd0c21c9…`), the decay data, the model, transport, seeds,
  schedule, dose tally and two threads, through P181's unchanged harness (`controls/p181_harness.py`, whose `--child`
  run every run is), with the amendment's wrapper `controls/p181a_harness.py` narrowing the arm list to `actinv`
  and writing `results/p181a_scale.json`.
- **The adapter module is the one on master at registration**, not P181's: since P181's registration it gained
  `load_results` and an MPI guard (only rank 0 runs `actinv mesh`; 496acee). In a single-process run the solve is
  unchanged; step 2 now also writes the activation materials to `activation/materials.xml`, which is inside step 2's
  timed interval and counts against ACTINV.
- **OpenMC is not rerun.** Its runs, statuses, times, memory and the stop rule's skip at 32,768 elements are taken
  from P181's record as committed with its evidence (`e87a4ab`, SHA-256 `8c79bb8b…`). They were measured on the same
  machine, under the same limits, on 2026-11-01.
- **Limits, order, rules and quiet machine are P181's**: each run in its own scope with 10 GiB, no swap, 128 tasks and
  two CPUs, a 4-hour limit, the headroom wait, sizes ascending, the stop and projection rules, no other job.

## Gates (by `controls/check_p181a.py`)

- **G0:** P181's protocol and this amendment are in `protocols/protocol_hash.txt`.
- **G1:** P181's G1 on the amendment record, with the ENDF/B-VIII.1 library pin in place of TENDL-2025's and the
  adapter and `_defaults.py` equal to the checkout's; this amendment, its harness and P181's harness, protocol, demo
  and group files equal to the checkout's; only the `actinv` arm planned; P181's record equal to the committed one;
  the same CPU model in both records.
- **G2:** P181's G2 for the `actinv` arm.
- **G3:** P181's quiet-machine test on the amendment record.
- **G4:** CI green on the evidence commit.

P181A-PASS if G0–G4 hold, P181A-FAIL otherwise. There is no further repair round.

## Accuracy and sentences

P181's accuracy checks, summary, reach and sentence templates, computed with the amendment's ACTINV runs in place of
P181's and P181's OpenMC runs unchanged. The sentences name ACTINV's data: "ACTINV 1.7.1 with an ENDF/B-VIII.1
activation library took A s …" and "ACTINV (with an ENDF/B-VIII.1 activation library) completed meshes up to …". When
quoted, the sentences go with this disclosure: the ACTINV runs were made after P181's result was known, a few hours
after OpenMC's.

P181's TENDL-2025 runs stay in P181's record and are not quoted. Nothing is claimed about which evaluation is closer to
measurement.

## Not claimed

P181's list, and in addition: nothing about ACTINV's shipped TENDL-2025 data on this model.

## Commands (coordinator)

From a worktree at the registration commit, inside the AGENTS.md scope (200 % CPU, 6 GiB), with no other job running:

1. `~/micromamba/envs/openmc0153dd/bin/python controls/p181a_harness.py --actinv-bin
   /home/connoravila/Documents/actinv/target/p180-bin/actinv --library
   /home/connoravila/nuclear-data/endfb-viii.1-p178/endfb81_all_709g.npz --xs-archive-sha256
   b7ad59cb4a3d76d8a291326093a98507f8d24b6e6af629116d3f7dc85f83c4cb`
2. `python3 controls/check_p181a.py` (writes `results/p181a_verdict.json`).
