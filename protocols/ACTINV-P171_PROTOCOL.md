# ACTINV-P171 — decay heat after 14 MeV irradiation, ACTINV and OpenMC depletion against the JAEA FNS measurements

## Purpose

OpenMC's depletion module is the open-source code most users reach for when they need activation inventories and
decay heat after a transport run. ACTINV is an alternative. P163 registered the speed comparison. This protocol
registers the accuracy comparison on the same footing: every JAEA FNS 14 MeV decay-heat experiment of the CoNDERC
corpus (132 experiments), the measurement being the reference. The question, framed neutrally:

> How close is the decay heat calculated by ACTINV (shipped TENDL-2025 data, catalog 1.2.0) and by OpenMC 0.15.3
> depletion (OpenMC's published ENDF/B-VIII.1 depletion chain and its ENDF/B-VIII.1 cross sections) to the decay heat
> measured after 14 MeV irradiation?

A second arm, ACTINV run with the same ENDF/B-VIII.1 cross sections OpenMC uses, separates the effect of the data
from the effect of the solver. Two sensitivities vary only OpenMC's chain: the chain built for P32 and used in P163, and the published chain with isomer branching ratios added for the reaction types it lacks them for.
Accuracy is reported, not gated.

An exploratory version of this comparison exists (`results/FNS_ENDF8_HEADTOHEAD.md`, `results/openmc_fns_headtohead.json`,
`results/endf8_fns_headtohead.json`): 21 experiments, 424 points, OpenMC 0.16.0. It was never registered and is not
cited. This protocol replaces it.

## Disclosure of what has been seen

- The 21-experiment exploratory record above, in full: for OpenMC/ENDF-B-VIII.1 median |ln C/E| 0.181 (gm 0.685); ACTINV/
  ENDF-B-VIII.1 0.131; ACTINV/TENDL-2025 0.141. The 21 experiments were chosen by the earlier author as one experiment
  per material (5 min preferred), a convenience subset. Their results are part of this protocol's population and are
  not independent of it. The 111 remaining experiments have no OpenMC result.
- ACTINV values for all 132 experiments with TENDL-2025 (`results/cb2_fns.json`, actinv 1.1.2) and with ENDF/B-VIII.1
  (`results/fns_endfb8/`, produced by an older script) exist and were seen. This protocol re-runs both arms with the
  pinned module and one script.
- The exploratory write-up attributes OpenMC's large deviations (Nb, W, Y, Pb, Ni, Mn, S, Sn) to missing channels in its
  chain. A direct check of this protocol's author found that for Nb that is not so: ENDF/B-VIII.1's own Nb-93 file has
  (n,2n) to the Nb-92 ground state only, and ACTINV with the same file gets the same C/E (0.03) — a data property.
  The explanation of each deviation is therefore established by the sensitivity arm and the identical-data arm, not
  assumed.
- No parameter below was chosen after a 132-experiment OpenMC result.
- The pinned module was built and import-checked (it reported version 1.7.0). No arm of this protocol has been run.

## What is compared

Every experiment of `~/nuclear-data/conderc-fns/fns` (73 materials; 132 experiments; archive
`ba1dd6cb150a4aa3e0d81461054aec7d415ef19d946aba8b9886b31de218252d`). Each gives a material (element wt%), a 709-group
neutron flux with total, an irradiation time, cooling times and measured decay heat per gram with its uncertainty.
The scored points are those `controls/cb2_fns.py` aligns to the cooling schedule (positive measurement, positive time,
cooling step within 2%). All arms use these points.

### Arms

| arm | code | cross sections | chain / transmutation channels |
|---|---|---|---|
| `actinv_tendl2025` (ACTINV primary) | ACTINV Python module, `controls/cb2_fns.py` | shipped TENDL-2025 709-group library, catalog 1.2.0 | ACTINV: every library channel including isomer products; decay ENDF/B-VIII.0, JEFF-3.3 fallback |
| `actinv_endf8` (identical data) | same | ENDF/B-VIII.1, all 269 pristine tapes, 709 groups, built by ACTINV's builder (`--profile endfb8`) | same |
| `openmc` (OpenMC primary) | OpenMC 0.15.3: `MicroXS.from_multigroup_flux` (709-group flux, collapsed per chain nuclide and reaction) → `IndependentOperator` → `PredictorIntegrator`, source-rate normalization | OpenMC's official ENDF/B-VIII.1 HDF5 library | OpenMC's published ENDF/B-VIII.1 fast-spectrum chain, `chain_endfb81_fast.xml` (below) |
| `openmc_p32` (sensitivity 1) | as `openmc` | as `openmc` | the P32 chain: `Chain.from_endf` on ENDF/B-VIII.0 decay files and the TENDL-2025 neutron files for the reaction list; 3,820 nuclides, 2,500+ nuclides with each of (n,2n), (n,3n), (n,gamma), (n,p), (n,a), no isomer branching, no fission yields |
| `openmc_isomer` (sensitivity 2) | as `openmc` | as `openmc` | the published chain plus `Chain.set_branch_ratios` for (n,2n), (n,3n), (n,p), (n,a) from the ENDF/B-VIII.1 709-group library, flux-weighted per experiment flux; its (n,gamma) ratios stay the published ones. (n,n') isomer production cannot be expressed in an OpenMC chain and stays absent. |

### The published OpenMC chain

The primary OpenMC chain is the ENDF/B-VIII.1 fast-spectrum chain from https://openmc.org/depletion-chains/
("a complete depletion chain generated based on the incident neutron, decay, and neutron fission yield sublibraries
from ENDF/B-VIII.1"; thermal and fast variants differ only in the spectrum-averaged (n,gamma) isomer branching
ratios). It was downloaded on 2026-10-30 from
`https://anl.box.com/shared/static/n0pkqe66uotskoljr93szvjyvtvycgze.xml` (27,532,017 bytes) to
`~/nuclear-data/openmc-chains/chain_endfb81_fast.xml`, SHA-256
`28abc04814d1d4e58890617b4ddf0c7378fbc3d737b4d3de916321b752c4ad06`. The page gives links without file names; the name is
the one of OpenMC's repository convention (`chain_endfb71_fast.xml` in openmc-dev/data). The fast variant is chosen
because the FNS source is a 14 MeV fusion spectrum; OpenMC documents neither variant as the default. No ENDF/B-VIII.1
chain is committed to openmc-dev/data (only its generator `generate_endf81_chain.py`).

Facts, read from the file: 3,820 nuclides (738 isomers, as in the P32 chain); reaction counts (n,gamma) 655, (n,2n)
549, (n,p) 464, (n,a) 462, (n,3n) 404, (n,4n) 100, fission 88 (with fission yields). Isomer branching is present
only for (n,gamma): 202 reactions carry a spectrum-averaged branching ratio (Nb-93 to Nb-94 and Nb-94m, 0.31/0.69;
Y-89 to Y-90 and Y-90m 0.989/0.011). (n,2n), (n,3n), (n,p), (n,a) have a single ground-state target, so Nb-93(n,2n) feeds
Nb-92 only. It has no (n,n') channels. The reaction lists follow ENDF/B-VIII.1's own files, so some thresholds are
absent that the P32 chain has (Fe-56(n,3n), Ni-58(n,3n), Y-89(n,3n), Ta-181(n,4n)). Decay data are those of the P32
chain: of 3,820 nuclides 6 differ in decay energy, 2 by more than 5%.

Notes on the arms, stated so the reader can judge them:

- **The primary chain is OpenMC's published one.** The P32 chain is a sensitivity, so the result does not rest on a chain
  built for this project. The sentences name the published chain by file name.
- **Cross sections.** OpenMC's cross sections are the ENDF/B-VIII.1 evaluations at 293.6 K as shipped in its HDF5
  library, collapsed with the experiment flux per reaction (MT 16, 17, 102, 103, 107, 4-digit MTs). ACTINV's
  identical-data arm uses the same evaluations (pristine tapes), 0 K pointwise data processed to 293.6 K and collapsed
  to the 709 groups by ACTINV's builder. "Identical" means same evaluation files, not the same processing; ACTINV's
  builder normalized about 30 tapes (see `FNS_ENDF8_HEADTOHEAD.md`).
- **Decay data and the heat convention (settled from the code).** ACTINV's total decay heat is the sum, over inventory
  nuclides, of activity × (e_heavy + e_light + e_em) (`crates/actinv-core/src/run.rs`: the response snapshot at lines
  1093-1095 and the total `heat: [alpha + beta + gamma, ...]`), where `e_light`, `e_em` and `e_heavy` are entries 0, 2
  and 4 of the ENDF-6 MF8/MT457 mean-energy list (`crates/actinv-data/src/decay.rs`): mean light-particle,
  electromagnetic and heavy-particle (alpha, recoil) energies. The heavy component is included. Neutrino energy is not
  in that list's first three entries and is not counted. OpenMC's `decay_energy` is `light + electromagnetic + heavy`
  from the same three entries (`openmc/data/decay.py`, `Decay.decay_energy`; `openmc/deplete/chain.py` copies it into
  the chain). The conventions are the same; no harness correction is needed. The chain files carry decay energies
  equal to the ENDF/B-VIII.0 decay file ACTINV uses for all but 6 of 3,820 nuclides. ACTINV falls back to JEFF-3.3 for
  nuclides missing from it.
- **Decay heat in OpenMC** is not an OpenMC output here. It is sum(N × λ × E_decay) over chain nuclides from OpenMC's
  atom counts at each cooling boundary, the convention OpenMC's R2S uses. 1 cm³ at 1 g/cm³ makes atoms per cm³ equal
  atoms per gram; heat is in µW/g.
- **Isomer branching ratios** of the sensitivity arm use ACTINV's ENDF/B-VIII.1 library rows. Its state labels are the
  evaluation's final-state numbers mapped to isomer ordinals; for evaluations that do not give a physical isomer
  identity the ordinal can be wrong (the Ta-182 case in `FNS_ENDF8_HEADTOHEAD.md`). The arm is a sensitivity of the
  chain topology, not an independent OpenMC reference.

## Scoring

Per point, C/E = calculated / measured. The primary statistic is the median over points of |ln C/E|, pooled over the
points with a positive calculation in all three primary arms. Also reported, per arm: geometric-mean C/E, 90th
percentile and maximum |ln C/E|, points within 30% (|ln C/E| ≤ ln 1.3), experiments with all points within 30%, the
median over experiments of each experiment's median |ln C/E|, and a 95% interval for (OpenMC median − ACTINV median)
from 2,000 resamples of experiments with replacement (seed 171). The sensitivity arm is reported on its own point set
beside the primary arms on the same points. Points dropped for a non-positive calculation are listed; more than 5%
dropped fails G2.

Not weighted by measurement uncertainty: the comparison asks how close each calculation is to the measured value, as
the exploratory record did.

## Frozen inputs

| input | SHA-256 |
|---|---|
| OpenMC published chain `~/nuclear-data/openmc-chains/chain_endfb81_fast.xml` (URL and date above) | `28abc04814d1d4e58890617b4ddf0c7378fbc3d737b4d3de916321b752c4ad06` |
| P32 chain `~/nuclear-data/p32-work/chain/depletion/chain.xml` | `d3b829467cfdda9c624aaeb5709825df378b908ecc63c3f984ade51f8d0444a0` |
| OpenMC ENDF/B-VIII.1 archive `endfb81.tar.xz` | `b7ad59cb4a3d76d8a291326093a98507f8d24b6e6af629116d3f7dc85f83c4cb` |
| `~/nuclear-data/endfb-viii.1-hdf5/cross_sections.xml` | `b8ffb6104a946bf951dc0f3b9a1f57a60f7f57c4b308cd66df4fd3dd4bc557ce` |
| `actinv-data/v1.2.0/activation/tendl-2025-neutron-709g.npz` | `ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44` |
| its index | `8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb` |
| `endfb8_fns_709g_pristine.npz` | `0b8073ebaf6213e6adcaeed8b25568346c86f39a1e6cfbe78a715d5712e3631a` |
| its index | `31ff86d6407dffa01bd6fa9658fde4e75ef816c2e155402a4c1845232f3b0b11` |
| `endf-b-viii-0_decay.dat` | `6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb` |
| `jeff-3-3_decay.dat` | `850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123` |
| FNS archive `fns.zip` | `ba1dd6cb150a4aa3e0d81461054aec7d415ef19d946aba8b9886b31de218252d` |
| `results/tables/abundance_mass.json` | `285b38a823dcee398a1dcab798c3b5a17d6b7ae5817edc663ed502314367a87b` |
| `results/openmc_fns_headtohead.json` (the sealed 0.16.0 record, made with the P32 chain; used only as the P32 arm's reproduction check) | `2854484244b7198a7d3e5d7c8f0aafb2a311b7934a3c9152883eda000ba1d45d` |

The ACTINV Python module was built before registration, inside the bounded scope, from crates tree
`fd259e18ecdeaff326672fce811996610a599bc1` and python tree `77640a1d20c73fcc93128bea9cc5207380a2b839` (the
registration commit's). Its SHA-256 is `f33ea3eded6cfb8fd3734de692182ba4d793f75f026206e34a68871824dd7cc4`; it is
passed to the harness by `ACTINV_PYTHON_MODULE`. Both ACTINV arms must run with that one module, and the evidence
commit's crates tree must be the one above.
`controls/p171_openmc_fns.py`, `controls/openmc_fns_driver.py`, `controls/openmc_fns_headtohead.py` (imported for its
case builder and heat evaluator), `controls/cb2_fns.py` (imported for ACTINV) and `crates/actinv-data/data/fispact_709_groups.json`
are recorded by hash in the record. Tracked tree must be clean.

Material composition: the element weight percent of each `.i` file expanded to isotopes by the pinned abundance and
mass table, as in the exploratory run.

## Environment

- OpenMC 0.15.3 in `~/micromamba/envs/openmc0153dd` (as P163; the exploratory run used 0.16.0). Verified 0.15.3 and
  0.16.0 are both installed. Cross sections via `OPENMC_CROSS_SECTIONS`; no chain downloaded.
- Each mode runs inside the AGENTS.md systemd scope (`MemoryMax=6G`, no swap, 128 tasks, 200% CPU), and every OpenMC
  driver process is started in its own scope of the same limits, so a memory kill ends one chunk, not the harness.
  The cap is 6 GiB for every arm and is not raised by this protocol. OpenMC loads the cross sections of every reachable
  chain nuclide (P163 saw more than 6 GiB at 8 cells with about 500). The harness therefore runs one driver process per
  flux group (4 groups) and, if a process fails, splits it by material into halves, down to one material; a single
  material that still fails is recorded as `not_run` and fails G2. The number of cases that fit in 6 GiB is unknown
  until the smoke test; the record keeps each chunk's size, split depth and return code.
- One run per arm; depletion is deterministic.

## Reproduction control (P32 sensitivity only)

On the 21 experiments of the exploratory record (made with OpenMC 0.16.0 and the P32 chain) the `openmc_p32` heat must
equal the sealed heat within 0.01 in ln on every shared point (G2). This tests nothing about the primary arm and applies
only if the P32 sensitivity is run. If it fails, the cause is found and an amendment registers the repair; no number is
reported before.

## Registered summary and allowed sentences

`controls/check_p171.py` writes `results/p171_verdict.json`.

1. "Against {n} measured decay-heat points from {m} JAEA FNS experiments, ACTINV with its shipped TENDL-2025 data has a
   median |ln C/E| of {a}; OpenMC 0.15.3 depletion with its published ENDF/B-VIII.1 fast-spectrum depletion chain
   (chain_endfb81_fast.xml) and ENDF/B-VIII.1 has {b}."
2. "Given the same ENDF/B-VIII.1 cross sections as OpenMC, ACTINV has a median |ln C/E| of {c} on the same points."
3. (only if run) "With isomer branching ratios from the ENDF/B-VIII.1 library added to that chain, OpenMC has a median
   |ln C/E| of {d} on the {k} of those points it covers."
4. (only if run) "With a chain built from ENDF/B-VIII.0 decay data and TENDL-2025 reaction channels instead
   (Chain.from_endf, ground-state targets), OpenMC has a median |ln C/E| of {e} on the {k} of those points it covers."

The median is `statistics.median` of |ln C/E| everywhere.

Rounding: three decimals. No sentence may call a code or a library better, worse, more or less accurate, or close
than another; a sentence's numbers stand for themselves. Sentences 1 and 2 may only appear together. A speed claim
may cite P163 separately; it is not made here. The handbook states which experiments and reactions drive the
differences only if the per-experiment record in the verdict shows it.

## Gates

- **G0.** This protocol's SHA-256 (and any amendment's) is in `protocols/protocol_hash.txt`.
- **G1.** All pins above; the pinned module for both ACTINV arms; OpenMC 0.15.3; the bounded 6 GiB cgroup for every arm; clean tracked
  tree; the run's crates tree is the module's build tree above.
- **G2.** 132 experiments in every arm; ACTINV arms' own checks (`cb2_fns.py`: identities, certificate inputs,
  alignment) pass; the two ACTINV arms and OpenMC share identical measured points; every OpenMC case executed and every
  measurement time on a cooling boundary (within max(2%, 1 s)), also for each sensitivity run; at most 5% of points
  dropped; the P32 reproduction control above when that arm ran.
- **G3.** Summary and sentences recomputed from the record and written to (or, with `--no-write`, equal) the verdict.
- **G4.** CI green on the pushed evidence commit.

## Verdict

P171-PASS if G0–G4 hold, whatever the agreement. One repair round, registered as an amendment first.

## Not claimed

- That either code is more accurate than the other in general. The comparison is one measurement series (14 MeV
  neutrons, irradiations of 5 minutes to 7 hours, cooling to about a year) and the specific data and chain named.
- Anything about OpenMC's other chains (the thermal-spectrum variant of the same chain, the ENDF/B-VIII.0, JEFF or
  JENDL chains, the CASL chain), other OpenMC cross-section libraries, or OpenMC's transport. OpenMC documents no
  default; the fast ENDF/B-VIII.1 variant was chosen for the 14 MeV source and the other was not run.
- Anything about speed (P163 is the speed record), fission systems, or neutron spectra other than the FNS ones.
- That the ENDF/B-VIII.1 or TENDL-2025 evaluations are better or worse. Differences between the two ACTINV arms are
  differences of evaluations, and, because of processing, possibly of ACTINV's collapse.
- That the isomer sensitivity is what a careful OpenMC user would build. It shows how much of OpenMC's departure from
  the measurements follows from ground-state-only reaction targets in (n,2n), (n,3n), (n,p) and (n,a).

## Amendment slots (to fill before the evidence run)

1. The number of OpenMC cases that fit in 6 GiB per process, from the smoke test, if a split is needed by default.

## Commands (coordinator)

From the worktree, with `PYTHONDONTWRITEBYTECODE=1`, each inside
`systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 -p TasksMax=128 -p CPUQuota=200% -- env RAYON_NUM_THREADS=2`:

1. (Done before registration: the Python module, built with `CARGO_BUILD_JOBS=1`, digest above.)
2. `python3 controls/p171_openmc_fns.py actinv --arm tendl2025` (about 2.5 min)
3. `python3 controls/p171_openmc_fns.py actinv --arm endf8`
4. `python3 controls/p171_openmc_fns.py openmc` (published chain; starts the 0.15.3 interpreter itself; about 90
   minutes: 132 cases × ~35 s, plus the cross-section collapse per chunk). Smoke test first: temporarily run one
   material and note its peak memory.
5. `python3 controls/p171_openmc_fns.py openmc --chain-variant p32` and `... --chain-variant isomer` (about 90 and
   100 minutes).
6. `python3 controls/p171_openmc_fns.py assemble`, then `python3 controls/check_p171.py`.
