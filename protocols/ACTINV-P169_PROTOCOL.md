# ACTINV-P169 — does different decay data close ACTINV's short-time fission-product decay-heat bias?

## Purpose

P166 scored ACTINV's fission-product decay heat against the CoNDERC tables (Gauld, 2019) with ENDF/B-VIII.0 independent
yields and ENDF/B-VIII.0 decay data. ACTINV and ORIGEN were both low at short cooling times; ORIGEN's printed values
make that visible on the same points. The question here:

> If ACTINV is given other open decay data, is the short-time bias reduced, and does the reduction hold on
> measurements the choice was not made on?

The comparison is a data swap, not a code change: nothing in `crates/` changes. Candidates are chosen on the P166
tables (train) by a rule fixed below and the chosen candidate is scored once on the P9 Dickens (Oak Ridge) and Yarnell
(Los Alamos) U-235 measurements (test). The outcome is reported whichever way it falls; a null result (no candidate
meets the rule) is a result.

## Disclosure (what has been seen before registration)

- The P166 record (`results/p166_conderc.json`) and its verdict have been seen. They are the train data and the
  baseline; no train-set choice below used anything else.
- An unregistered diagnosis was seen: ACTINV and ORIGEN are both 6-8% low below 100 s, about 4% low at 100-1000 s and
  about 0 beyond 1000 s, ACTINV/ORIGEN about 0.997 at all times; the ENDF/B-VIII.0 and JEFF-3.3 mean beta and gamma
  energies differ most for Rb-92, Rb-94, Rb-95, Br-87, Br-88, Br-89, Sb-134, Cs-142, I-138 and Y-95, while the nuclides
  with TAGS measurements in Algora (2010) already agree. Candidate D's nuclide list comes from that diagnosis, which is
  why D is a train-set candidate and not a test-set one. Candidate B was suggested by the same diagnosis.
- The P9 baseline values for Dickens and Yarnell have been seen (`results/g6_p9_conderc.json`: Dickens total
  geometric-mean C/E 1.00698, Yarnell total 0.98451). No candidate other than A has been run on either set. No ACTINV
  run of B or D has been made on any data before this registration.
- One parse check of candidate B's inputs: a decay-only problem (Rb-92 and Co-60, 10 s of cooling, no fission
  yields, no measured data) with JEFF-3.3 as primary and ENDF/B-VIII.0 as fallback ran (3,873 states) and its
  certificate recorded the pinned JEFF-3.3 hash. Only the exit status, the state count and the certificate were read.
- The consequence: the baseline on the test sets is not blind, and the candidate list was shaped by the train-set
  diagnosis. The held-out score of the selected candidate is the only number this protocol treats as a test result.

## Frozen inputs (SHA-256 pins; no data file is committed or redistributed)

| Input | Pin |
|---|---|
| ENDF/B-VIII.0 decay (`endfb-viii.0-decay/bulk/endf-b-viii-0_decay.dat`; P9, P166 pin) | `6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb` |
| JEFF-3.3 decay (`jeff-3.3-decay/bulk/jeff-3-3_decay.dat`, from IAEA-NDS `jeff-3-3_decay.sublib.zip`, whose own SHA-256 is `73d52f142a284e2f463a8afcac5a9422c00de7bbbdee7e33196d825bea5edd6d`) | `850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123` |
| Candidate D override document, written by `p169_decay_swap.py overrides` from the two files above | `d5458572e9dec58a90e3576d46ffc7c2f4c6b33521e0bc50cd25b7a8bbd827a1` |
| P166 table CSV | `0160b82fedf141e71b81f7ff9ad1e26783ef77c999a22ff0b6d8db79759a0cb9` |
| P166 record `results/p166_conderc.json` (the baseline to reproduce) | `2f080d9e84d47ee1e7cedc9b97ee064026412252da23ce0ccf00c0b5f60cb6bf` |
| Dickens_pulse.csv, 32 rows | `2a0442af2c232a86b07c198680461f5acfd3530bcf78bb3d23967f34a018f47b` |
| Yarnell_20000.csv, 79 rows | `3d35f4bb7bcf167a5ec2dae8371093b65bea9d6a687156a0be552be23a3aad0c` |
| U-233, U-235, Pu-239 yield files and the NFPY archive | the P166 pins, unchanged |
| Binary | ACTINV 1.7.0 release build from d6f516b, `186cc2587bb61766b3b3c426c61cd44f00ed472401026dea2ca3dd595c8d1194`; the `crates/` tree must equal d6f516b's |

The Dickens and Yarnell archive files carry the P9-recorded header anomalies (Yarnell is labelled "Akiyama / Pulse" in
the file; the filename, the paired FISPACT input and the UKAEA report define it as the Yarnell 20,000 s irradiation,
and Dickens values are time-weighted); P169 follows P9's reading of both, unchanged.

Licences. JEFF-3.3 is an OECD/NEA Data Bank evaluation distributed openly by the IAEA-NDS and the NEA. The files are
used as inputs on the maintainer's machine; this protocol commits and redistributes no data file, only the SHA-256 pins,
the override document's pin and result records. If the override document (ten numbers per nuclide taken from JEFF-3.3)
is ever shipped, the JEFF-3.3 attribution and the NEA's terms of use for that library apply and must be checked then.
ENDF/B-VIII.0 is a public-domain US evaluation. ORIGEN values in the sentences are quoted from Gauld's open report.

## Model (the P166 model, unchanged)

`controls/p169_decay_swap.py` imports `controls/p166_harness.py` and calls its `per_fission` for every run: one-group
library, trace mode, ENDF/B-VIII.0 independent yields at the fixed energy 0.0253 eV, constant fission rate, a second
flux-off run subtracted channel by channel, the P166 flux and parent atoms. The harness replaces only the `decay` block of
each specification. Normalization follows P166 and P9: Dickens is a 1 microsecond pulse normalized by integrated
fissions, with the archive's time-weighted values divided by cooling time; Yarnell is 20,000 s at a constant fission
rate. (P9 ran these at a flux of 10^22 and 10^14 with the fission cross section of ENDF/B-VII.1 U-235; P166's flux and
nominal cross section are used instead because trace mode is linear in them. Candidate A's reproduction of P9's recorded
geometric means is a gate.)

## Candidates (frozen; one decay change each)

ACTINV selects decay data through the specification's `decay` object: `primary` (required, an ENDF-6 decay sublibrary
file), `fallback` (optional second file that fills nuclides absent from the primary) and `overrides` (optional
`actinv-decay-overrides-1` document with per-nuclide half-life, mean energies and decay modes, source and reason
mandatory), `crates/actinv-core/src/spec.rs:626-635`; merge and override order in `crates/actinv-core/src/run.rs:3084-3126`.
Heat per nuclide uses the mean light-particle (beta) and electromagnetic (gamma) energies from the decay file,
`run.rs:1094-1095`.

| Name | `decay` block | Meaning |
|---|---|---|
| A | `primary` = ENDF/B-VIII.0 | Baseline; the P166 and P9 configuration. |
| B | `primary` = JEFF-3.3, `fallback` = ENDF/B-VIII.0 | Whole JEFF-3.3 library; ENDF/B-VIII.0 supplies any product JEFF-3.3 lacks (count recorded). |
| D | `primary` = ENDF/B-VIII.0, `overrides` = the pinned document | Mean beta, gamma and heavy-particle energies of Rb-92, Rb-94, Rb-95, Br-87, Br-88, Br-89, Sb-134, Cs-142, I-138, Y-95 (ground states) replaced by JEFF-3.3's; half-lives, branching and spectra stay ENDF/B-VIII.0. |

Candidate C (ENDF/B-VIII.1 decay data) is not run. The locally held ENDF/B-VIII.1 package is the OpenMC HDF5 library
(`endfb81.tar.xz`, SHA-256 `b7ad59cb4a3d76d8a291326093a98507f8d24b6e6af629116d3f7dc85f83c4cb`, extracted to `endfb-viii.1-hdf5`), which has neutron,
photon and thermal-scattering data and no decay sublibrary; the directory prepared for VIII.1 decay data is empty.
Obtaining it would need a new download, outside this protocol. It can be added by an amendment registered before any
test-set run.

## Selection rule (train: the P166 primary points)

Train points are the 692 total-heat points of P166's primary view. For each candidate compute the median |ln C/E| over
the points with cooling time below 1000 s ("short", 391 points) and at or above 1000 s ("long", 301 points). For
reference, A's values from P166 are 0.0572 (short) and 0.0275 (long). A candidate X other than A is eligible if

1. long median(X) <= long median(A) + 0.005, and
2. short median(X) <= short median(A) - 0.005.

The selected candidate is the eligible one with the lowest short median (ties by name); if none is eligible, A. The
thresholds are absolute differences in median |ln C/E| (about 0.5% of heat), set before any B or D run; a floating-point
slack of 1e-12 is allowed at either edge. The rule is implemented by `p169_decay_swap.select` and recomputed by the
checker from the train points.

Train stage: A, B and D on all twelve P166 datasets (`p169_decay_swap.py train`, writing `results/p169_train.json`,
which records the selection).

## Held-out scoring (test: once)

`p169_decay_swap.py test` runs A and the selected candidate on Dickens (32 points x beta, gamma, total) and Yarnell
(79 total points), reads the selection from the train record, recomputes it, and refuses to overwrite an existing test
record. If A is selected, only A runs. The test record is not regenerated whatever it shows. The held-out headline is
the total-heat view (111 points: 32 + 79; 23 + 39 = 62 below 1000 s, 49 at or above), the selected candidate against A,
with the paired fraction of points whose |ln C/E| is smaller than A's. Dickens beta and gamma are reported as context.

## Registered summary and allowed sentences

Per candidate on train: geometric-mean C/E, median, 90th-percentile and maximum |ln C/E|, within-1-sigma and 2-sigma
fractions for the short and long windows; ORIGEN as printed by Gauld on the same points (train only). On test: the same
for all total points, the short and long windows, and per dataset and channel. The verdict file carries sentences filled
from the computed numbers:

1. One per candidate: "On the {n} P166 train points with cooling time below 1000 s, {candidate} gives a median |ln C/E|
   of {m}; at 1000 s and above ({n} points) it is {m}."
2. "By the registered rule the selected decay data is {candidate}."
3. If a candidate was selected: "On the held-out Dickens and Yarnell U-235 total decay heat ({n} points), {candidate}
   gives a median |ln C/E| of {m} and a geometric-mean C/E of {g}, against {m_A} and {g_A} for ENDF/B-VIII.0 decay
   data. Below 1000 s ({k} points) the medians are {m} and {m_A}." If none: "No candidate met the rule, so
   ENDF/B-VIII.0 decay data was kept; ... its median |ln C/E| is {m} and its geometric-mean C/E is {g}."
4. "On the same train points, ORIGEN as printed by Gauld (2019) has a median |ln C/E| of {m} below 1000 s and {m} at
   1000 s and above." This is a train-set sentence only and compares data sets, not codes.

Numbers are rounded to three decimals. No sentence may say that a decay library is better in general, that any code is
more accurate than another, or that the train result predicts anything not shown by the held-out sets.

## Gates

- **G0.** This protocol's SHA-256 (and any amendment's) is in `protocols/protocol_hash.txt`.
- **G1.** Both records carry the pinned hashes above, the pinned binary, d6f516b's `crates/` tree, a clean tracked tree
  and a cgroup of at most 6 GiB, no swap, 128 tasks and 200% CPU; the test record names the train record it was
  selected from; the train record is a full run, not a smoke run.
- **G2.** P166's per-run fidelity checks (trace mode, one 0.0253 eV yield selection for the right parent, effective yield
  sum within 1e-6 of 2, rate closure <= 1e-9, step times to 1e-9, parent background <= 1e-6) on every candidate and
  dataset; each candidate's certificate names exactly its registered decay files; D applied all ten overrides; no other
  candidate applied any; A reproduces P166's recorded C/E on every train point to 1e-9 relative and P9's recorded
  geometric-mean total C/E (Dickens 1.0069797, Yarnell 0.9845114) to 1e-4 relative.
- **G3.** Every P166 CSV row is scored once per candidate with a finite positive C/E and positive sigma; held-out counts
  are Dickens 32 x 3 and Yarnell 79; the test record holds A and the selected candidate only.
- **G4.** The selection is recomputed from the train points and equals the recorded one and the test record's; the
  summary and sentences are recomputed and equal `results/p169_verdict.json`.
- **G5.** CI green on the pushed evidence commit.

Reported but not gated: the fraction of atoms lost to products missing from the decay library per run (max leakage), and
the number of nuclides B took from the fallback.

## Verdict

P169-PASS if G0-G5 hold, whatever the selection and whether or not the held-out score improves. One repair round,
registered as an amendment first. No candidate may be added, dropped or re-tuned after the first B or D run except by an
amendment registered before the test run.

## Not claimed

- That any decay library is more accurate than another in general; only that on these points, this model and this
  yield set, a swap does or does not change the C/E.
- Anything about the cause of the short-time bias beyond what the swap shows: yields, delayed gamma and beta spectra,
  pandemonium bias and the measurement itself are not separated here.
- Fast fission, other fissile nuclides, long irradiations with transmutation, or activation cross sections.
- That the Dickens and Yarnell baselines are blind (see Disclosure), or that the candidate list was chosen without the
  diagnosis.
- ENDF/B-VIII.1 decay data (candidate C), UKDD-2020 or any other library.
