# ACTINV-P45 protocol — executed complete-campaign benchmark

**Status:** FROZEN — sealed at G0 | **Frozen:** 2026-09-24 |
**Parent:** roadmap draft innovation extension P45 (`docs/ROADMAP.md`) |
**Depends on:** P43 (robustness machinery); P26b/P38/P40 comparator
lineage (identical-data contracts, ALARA driver); P32 (OpenMC
depletion chain); P21 (mesh execution)

## Intent

Replace the kernel-ratio headline with an executed whole-workload
claim. P45 runs frozen complete campaigns end-to-end on the release
candidate and on every lawfully executable equivalent-output
comparator, with the comparator's documented amortization enabled, and
reports preparation, solve, sampling, output and evidence time
separately. The P26 lesson binds this phase: an undetermined target is
a failure mode — every figure here is an executed measurement, a
measured loss is published as one, and an inaccessible comparator is
`unmeasured`, never a silent win.

## Scope

- Two frozen workloads, defined below with exact populations.
- Comparator arms, pinned below: ALARA 2.9.2 (converted-library
  amortized path) and OpenMC 0.16 `deplete` (IndependentOperator,
  micros from a supplied multigroup flux — documented amortized
  pattern). FISPACT-II and SCALE/ORIGEN stay `unmeasured`: no lawful
  executable exists on this host.
- Output parity contract per arm, frozen below before timing.
- Time accounting: per-case wall times, per-arm stage splits where the
  tool exposes them, cold and warm states, preparation amortization.
- Failure accounting: failed cases counted on both sides; a case a
  comparator cannot express is `contract_gap`, not a timing exclusion.

## Arms (pinned)

| arm | tool | data identity | parity class |
|-----|------|---------------|--------------|
| `actinv_tendl2025_patched` | `target/release/actinv` (sealed at G0) | `actinv-data/v1.1.0/activation/tendl-2025-patched-neutron-709g.npz` + ENDF-B-VIII.0 decay (primary) + JEFF-3.3 decay (fallback) | production arm |
| `actinv_fendl32c_709` | same binary | `~/nuclear-data/p26b-work/g1-run/actinv_fendl32c_709.npz` + same decay pair | identical-data leg vs `alara` |
| `alara_fendl32c_709` | ALARA 2.9.2 `~/nuclear-data/alara-2.9.2-build/src/alara` | converted `~/nuclear-data/p26b-work/g1-run/fendl32c_709.lib` + `alara.dmp` (P26b amortized converted-library path — the only lawful ALARA execution mode) | identical-data leg |
| `openmc_endfb81` | OpenMC 0.16.0 `/home/connoravila/micromamba/envs/openmc016/bin/python`, `PredictorIntegrator` + `IndependentOperator`, micros via `openmc.deplete.MicroXS.from_multigroup_flux` inside one `openmc.lib.TemporarySession` per batch (documented amortized pattern) | `~/nuclear-data/p32-work/chain/depletion/chain.xml` (3,820-nuclide chain built from TENDL-2025 neutron files + ENDF decay) + `~/nuclear-data/endfb-viii.1-hdf5` microscopic XS | **data-mismatch arm** — declared, timed, outputs compared and reported with the data-provenance caveat; differences are evaluation spread, not contract violations |
| `actinv_robustness` | same binary, study runner | `actinv_tendl2025_patched` + `~/nuclear-data/p43-work/p43.cov.npz` | capability-asymmetry leg — uncertainty sampling no comparator admits; timed separately, never folded into a speed ratio |

## Workload 1 — variant campaign (28 cases, frozen)

Stratified subset of the P26b W-CAMPAIGN `product_plus_data`-executable
population (`results/g2_p26b_leg_contract.json`, sha recorded at G0):
every case is a material × irradiation-time point on the `fns_709`
spectrum (709-group, descending, total 11166502554.2 n/cm²/s,
sha256 `a2ba307d…` per the contract).

```
fe_{dopant}{level}wppm__fns_709__{irr}s
  dopant ∈ {co, cr, mn, v}, level ∈ {10, 1000, 100000},
  irr ∈ {60, 86400}                                  → 24 cases
fe__fns_709__pulse_5min, fe__fns_709__cont_2y,
fe_co100wppm__fns_709__pulse_5min,
fe_co100wppm__fns_709__cont_2y                       → +4 = 28 cases
```

`pulse_5min` = 300 s irradiation; `cont_2y` = 63072000 s. Cooling
times at which responses are recorded: `[0, 86400, 2592000,
31536000, 315360000]` s (the contract's set). Composition,
isotope expansion and required-isotope lists are the contract's.

## Workload 2 — distinct-spectrum mesh (16 cells, frozen)

One material (pure Fe, natural isotopic expansion) × 16 distinct
709-group spectra. Cell `i` spectrum:

```
phi_i(g) = phi_fns(g) * (1 + 0.5 * sin(2*pi*i*g/709 + i*0.7)),
           g = 0..708, then rescaled so sum(phi_i) = 11166502554.2
```

(the multiplier is bounded in [0.5, 1.5] — strictly positive).
Schedule: 300 s irradiation at the cell flux + the same five cooling
times. Arms: `actinv mesh` (one streaming process, `chunk_cells=4`,
`threads=2`), ALARA one invocation per cell (16 subprocesses — its
only mode), OpenMC one `IndependentOperator` with 16 depletable
materials each carrying its own flux (one process — its amortized
mode). Per-cell wall time reported for every arm.

## Robustness add-on (capability asymmetry leg)

`actinv_robustness` runs the 24-case Cartesian core of the variant
campaign (12 dopant materials × {60 s, 86400 s}; the four
schedule-shape extras are excluded — they add no information to the
capability-cost measurement) as one study with
`robustness { samples: 16, seed: 20260924, channels:
{cross_section_mf33, decay_constants} (fission_yields, flux,
composition off), first_order_comparison: false }` — MF=33 + decay
sampling, the P44 band definition minus flux/composition/yield (none of
the corpus materials are fissile; flux/composition are measurement-side
inputs). Timed end-to-end; reported as ACTINV capability cost, never
converted into a comparator ratio. Comparators are marked
`capability_asymmetry`, not failed.

## Parity contract (frozen before timing)

- **Response set**: per-nuclide atom densities (atoms per gram) and
  total specific activity (Bq/g) at each cooling time. Activity is
  compared as reported natively by each tool; for OpenMC, activity is
  derived from output atoms × ENDF-B-VIII.0 decay constants — the same
  evaluation ACTINV uses — applied identically to both arms.
- **`alara` ↔ `actinv_fendl32c_709`** (identical data): relative
  difference `(alara − actinv)/actinv` per response per case per time.
  Contract tolerance (the P26b/P38 precedent): |rel| ≤ 0.10 on
  `total_activity_bq_per_g` at every cooling time and on each of the
  top-5 product nuclides' `specific_activity_bq_per_g` at t=0 (top-5 by
  ACTINV activity — the union is checked against ALARA's reported
  values); larger deviations are `parity_divergence`, ledgered per
  case — they do not stop timing but must appear in the report.
- **`openmc` ↔ `actinv_tendl2025_patched`** (declared data mismatch):
  same response comparison, reported descriptively (median/max rel
  per response per time); no tolerance enforced — differences are the
  declared evaluation gap, recorded, never hidden.
- A case where an arm fails to produce output is `arm_failure` (with
  reason); a case the arm cannot express is `contract_gap`. Both count
  in the ledger and appear in the verdict.

## Timing methodology (frozen)

- All runs sequential inside the enforced cgroup
  (`CPUQuota=200% MemoryMax=6G TasksMax=128`), `RAYON_NUM_THREADS=2`,
  one solver job at a time — the same bound every arm sees.
- **Warm**: each case timed on the second and third invocation of an
  identical workload unit inside the same arm batch process where the
  arm supports amortization (OpenMC TemporarySession batch; ACTINV
  campaign runner; ALARA has no amortization — every case is a fresh
  subprocess and that is its documented mode); reported as
  median-of-repeats per case. **Cold**: first invocation of each arm
  batch in a fresh working directory — reported separately.
- ACTINV per-case internal `ms` (solve+output wall inside the binary)
  recorded alongside subprocess wall — the stage split (library load
  vs solve) is attributable from the delta.
- Preparation: ACTINV's NPZ + decay parse happens once per campaign
  process (amortized); ALARA loads its converted binary dump per
  invocation (its mode); OpenMC loads the chain + HDF5 XS once per
  TemporarySession (amortized). Preparation costs reported separately
  from per-case costs.
- Timing unit: wall-clock seconds from `time.monotonic()` around each
  subprocess/driver call; the timing harness itself is sealed at G0.

## Envelope

G3 total across both workloads and all measured arms: **240 minutes**
wall-clock (fresh, complete pass). Per-case subprocess timeout 300 s.
Resumable at case granularity; resumed runs report status honestly and
the envelope applies only to a fresh complete pass.

## Out of scope

- Extrapolated figures, kernel-only claims, transport-coupled
  workloads, any metric on mismatched outputs, and optimizing ACTINV
  for the benchmark (candidate changes need a separately-frozen
  amendment + re-verified parity).
- The OpenMC arm's output comparison is not a pass/fail gate (declared
  data mismatch); it is reported, not enforced.

## Gates

- **G0** — seal: protocol hash, opening commit, candidate artifact
  identity, comparator identities (ALARA binary, OpenMC env python,
  chain.xml, HDF5 library manifest), the campaign/mesh documents, the
  timing harness, host record (uname -a, CPU model, cgroup limits),
  prior verdict re-verification (P43, P44 read).
- **G1** — feasibility and parity: one representative unit per arm per
  workload (one campaign case + one mesh cell), the output-parity
  contract verified executable, OpenMC per-case cost measured to size
  the campaign honestly; an arm that cannot execute the parity
  contract is `unmeasured` here.
- **G2** — controls: parity holds on a frozen 3-case pre-timed subset
  (ALARA identical-data tolerance enforced); timing methodology checks
  (repeat spread reported; a planted output mismatch is detected
  before timing; warm-state digests verify).
- **G3** — execution: both workloads run to completion on every
  measured arm inside the envelope; per-case and per-stage records
  complete; failures ledgered.
- **G4** — independent closure: checker re-derives every ratio and
  total from raw per-case records, verifies parity preconditions held
  during timed runs, rejects planted mutations, emits the verdict.

## Closure rule

PASS only if both workloads executed on at least one comparator arm
with the parity contract held and complete accounting. CONDITIONAL if
amendments were used, a headline arm is `unmeasured`, or a leg is
declared data-mismatch only. FAIL otherwise. The published claim
names the exact executed workloads, comparator versions, resource
limits and host — nothing else is claimed.

## Honest limitations (declared at freeze)

- The OpenMC arm is data-mismatched by construction (ENDF/B-VIII.1 XS
  vs TENDL-2025/FENDL activation data): its timing is measured and
  reported but its output comparison is descriptive only.
- The identical-data leg (ALARA↔ACTINV) runs FENDL-3.2c, not the
  shipped TENDL-2025-patched artifact — the timing claim is about the
  solver+orchestration cost on equivalent inputs, not about one
  specific data file's parse cost.
- The campaign is Fe-family dopant materials (the lawful identical-data
  population); it does not claim coverage of the full material space.
- ALARA's isotope-name display defect means its per-nuclide parity
  relies on the KZA-alignment recovery machinery (P26b), which is
  verified by cross-checks, not by ALARA's printed names.
- **Known conversion-coverage gap (declared at freeze, root-caused
  during feasibility):** the converted `actinv_fendl32c_709` NPZ
  carries 24 of the 36 subset PENDF targets — 12 failed ENDF parsing
  (Cr50/52/53/54, Fe57, Mn55, Ni62, W180/182/183/184/186), and Fe56's
  MT=103 (n,p) row is absent though ALARA's `.lib` retains it. The
  identical-data leg therefore diverges exactly where those channels
  dominate (short-lived charged-particle products at early cooling
  times). Consequences, all declared: (a) `parity_divergence` outcomes
  on affected cases are ledgered and published, not treated as
  failures; (b) the timing comparison on that leg biases *toward*
  ACTINV — fewer channels means a sparser chain and a cheaper solve —
  the report bounds this by the per-case missing-channel count;
  (c) 12 of the 28 campaign cases lose their dopant targets outright —
  every fe_cr* case (all natural-Cr isotopes failed) and every fe_mn*
  case (Mn55 failed) — so on the identical-data leg those cases are
  expected `parity_divergence` driven by absent dopant activation; the
  fe / fe_co / fe_v cases (16/28) carry their full target sets and are
  the leg's parity-bearing population. The conversion repair is
  parked, not performed, in this phase.
