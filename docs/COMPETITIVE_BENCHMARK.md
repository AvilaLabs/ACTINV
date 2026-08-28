# ACTINV competitive benchmark — CB1

*Initial scorecard: 2026-08-28 · ACTINV 1.0.0 · frozen protocol
`627990751a4730fe22e457ea2fa334fca25ae0eae7f463c8677e488e5dbb7398`*

## Bottom line

ACTINV 1.0.0 is a credible, usable activation product, but this benchmark does **not** support calling it the best
activation code overall. It does support a sharper position: ACTINV is strongest when a user wants an open,
easy-to-install, scriptable activation/R2S engine whose inputs and omissions remain reproducible and inspectable.

The initial evidence says:

- ACTINV agrees with independent OpenMC and dense matrix-exponential implementations at approximately machine
  precision on identical operators. On an identical processed-data pulse case, ACTINV and ALARA agree to
  `4.12e-8` relative in shutdown inventory, inside the precision of ALARA's text output.
- Against 132 FNS experiments, the comparison with the public FISPACT-II result is mixed. FISPACT-II/TENDL-2017 has
  the better typical point error and more experiments wholly within 30%; ACTINV/TENDL-2025 has a slightly better
  90th-percentile point error and a pooled bias closer to one. Because the nuclear-data releases differ, this is a
  product-plus-data comparison, **not** proof that either solver is more accurate.
- At the same Python-call boundary, ACTINV's sparse CRAM-48 kernel is 2.83–187 times faster than OpenMC's CRAM-48
  over the four tested operator sizes, with matching outputs. This is a kernel result, not a whole-product speed
  claim.
- ACTINV's clean PyPI install took `0.945 s`; its production data fetch took `5.35 s`; and its first public example
  result took `2.46 s` on the recorded host. The full public example's `1.09 GB` peak memory is the clearest measured
  efficiency weakness.
- FISPACT-II remains broader in documented incident particles, finite-dilution self-shielding, full-covariance
  uncertainty, and damage observables. OpenMC and SCALE/ORIGEN are stronger where transport coupling is the actual
  problem. ORIGEN and OpenMC have feed/removal models; ALARA has a reverse mode. Those are genuine gaps, although not
  all belong in ACTINV's intended niche.

There is deliberately no composite winner score. A single number would hide whether a difference came from the
solver, nuclear data, workflow, access model, or a capability irrelevant to the intended user.

## What was actually accessible

| product | fresh executable in CB1 | evidence used |
|---|---:|---|
| ACTINV 1.0.0 | yes | released CLI/Python module, source, full benchmark inputs |
| ALARA 2.9.2 | yes | official source commit `faa5b330…`, clean build, executable |
| OpenMC 0.15.3 | yes | pinned Python environment; depletion CRAM-48 exercised |
| FISPACT-II | no | public CoNDERC output from FISPACT-II 4.0/TENDL-2017; current 5.1 official documentation |
| SCALE/ORIGEN | no | current SCALE 6.3.3 distribution record and ORIGEN 6.3.2 official manual |

FISPACT-II and SCALE/ORIGEN are controlled distributions. CB1 did not have a licensed executable and did not
download, emulate, or redistribute either product. A fresh comparison is possible if a maintainer or collaborator
with lawful access runs the frozen inputs and returns the permitted outputs plus version/provenance records. Until
then, fresh executable cells remain `not-available`, not losses.

## Numerical and identical-data results

### Same transition operator

ACTINV CRAM-48, OpenMC CRAM-48, and SciPy's dense exponential received the same deterministic two-, eight-, and
32-state operators, initial vectors, and schedules.

| check | worst observed | CB1 bound | result |
|---|---:|---:|---:|
| relative error above the tolerance crossover | `4.18e-15` | `5e-12` | pass |
| absolute error / initial population 1-norm | `4.10e-15` | `5e-14` | pass |
| split versus merged equal-operator schedule | `2.45e-15` | `5e-14` | pass |

The largest relative difference over every merely nonzero tiny population is `8.45e-6`; those populations are far
below the absolute tolerance crossover, where the absolute criterion is the meaningful one. Reporting only that
relative number would misrepresent the result.

### Same processed activation data

ACTINV and the official ALARA 2.9.2 executable received the same 175-group FENDL-2 Fe-56(n,p)Mn-56 data, spectrum,
initial material, decay constant, and ten-pulse/nine-gap history.

| quantity | result |
|---|---:|
| collapsed reaction rate, both products | `5.78494e-10 s^-1` |
| reaction-rate relative difference | `0.0` |
| worst shutdown-inventory difference | `4.12e-8` relative |
| ACTINV versus analytic Bateman result | `2.94e-14` relative |
| ALARA versus analytic Bateman result | `4.11e-8` relative |

This is the strongest current code-to-code evidence because it removes the nuclear-library difference. It is a small
chain, however, not a substitute for a many-nuclide identical-data campaign.

## Measurement accuracy

### FNS 132-experiment family

The fresh ACTINV run scored all 132 public FNS experiments and 2,360 positive aligned calculation/measurement pairs.
No nuclide was removed after seeing the result and no measurement floor was introduced. The FISPACT values were
independently parsed from the frozen public archive.

| metric | ACTINV 1.0.0 / TENDL-2025 | FISPACT-II 4.0 / TENDL-2017 public result | preferred direction |
|---|---:|---:|---|
| pooled geometric-mean C/E | `1.0313` | `1.0636` | closer to `1` |
| median experiment geometric-mean C/E | `0.9933` | `1.0085` | closer to `1` |
| median point `abs(ln(C/E))` | `0.1392` | **`0.1053`** | lower |
| 90th-percentile point `abs(ln(C/E))` | **`0.6637`** | `0.6846` | lower |
| median experiment maximum `abs(ln(C/E))` | `0.2947` | **`0.2232`** | lower |
| experiments with every point within 30% | `59/132` (`44.7%`) | **`69/132` (`52.3%`)** | higher |
| RMS residual in reported measurement sigma | `4.54` | `76.0` | lower, but see note |

The median point errors correspond to multiplicative factors of about `1.15` for ACTINV and `1.11` for the public
FISPACT result. The 90th-percentile factors are about `1.94` and `1.98`, respectively. FISPACT's very large RMS-sigma
value is dominated by one extreme published indium-case residual; it should not be used alone to summarize typical
performance.

The honest reading is not “ACTINV beats FISPACT” or “ACTINV fails.” FISPACT leads the typical point, the median
worst point within an experiment, and the all-points-within-30% count. ACTINV leads pooled bias and the tail metric.
The compared evaluations are TENDL-2025 and TENDL-2017, so solver and data effects remain confounded. Historical
ACTINV runs reinforce that warning: EAF-2010 produced `62/132` experiments wholly within 30%, while TENDL-2023 and
TENDL-2025 each produced `59/132`.

### Other validation families

Prior hash-pinned ACTINV evidence, kept separate from the fresh FNS run, includes:

| family | points | geometric-mean C/E | maximum `abs(ln(C/E))` | RMS experimental sigma |
|---|---:|---:|---:|---:|
| Dickens U-235 thermal pulse, total heat | 32 | `1.00698` | `0.06683` | `0.993` |
| Yarnell U-235 thermal 20,000 s, total heat | 79 | `0.98451` | `0.08145` | `1.144` |

The FNG/ITER cell-620 evidence independently reproduces 120 reaction rates to `3.24e-16` relative and four
170-interval histories to roughly `3e-14`. It is an implementation/reference-record check, not an independent
activation measurement, so it is not pooled with the experiment statistics.

## Performance and resource cost

All scalar rows used one thread, five warm-ups, and 30 measured batches/processes on an Intel Core i3-N305 Linux host.
OS file caches were warm. These numbers characterize this host and workload; they are not universal hardware claims.

### Identical CRAM-48 operator at the Python boundary

| states | ACTINV median | OpenMC median | OpenMC / ACTINV | maximum matched-output relative difference |
|---:|---:|---:|---:|---:|
| 2 | `0.0155 ms` | `2.886 ms` | `186.8x` | `4.97e-16` |
| 32 | `0.1576 ms` | `3.196 ms` | `20.3x` | `6.72e-16` |
| 256 | `1.224 ms` | `4.853 ms` | `3.96x` | `1.18e-15` |
| 1,024 | `5.661 ms` | `16.039 ms` | `2.83x` | `6.72e-16` |

Both rows include their public Python-call and data-conversion boundary. This supports a concrete claim about the
tested sparse-kernel interface, not about complete OpenMC transport/depletion runs.

### Whole paths

| workload | median | p95 | peak RSS | interpretation |
|---|---:|---:|---:|---|
| ACTINV process startup/version | `2.30 ms` | `3.61 ms` | `3.76 MB` | warm file cache |
| ACTINV public Fe example | `2.174 s` | `2.304 s` | `1.091 GB` | hashes/parses `237.9 MB`; 3,873 states |
| ACTINV same-data Fe pulse, full diagnostics | `522 ms` | `548 ms` | `126 MB` | inventory, activity, heat, ledger, certificate |
| ALARA same-data Fe pulse, number density only | `3.34 ms` | `5.11 ms` | `5.30 MB` | preconverted library; narrow requested output |

The last two rows are intentionally **not** divided into a speed ratio. Their physics inputs and schedule match, but
the requested outputs and data-loading work do not. They reveal a product-level tradeoff and an ACTINV optimization
target; they do not establish a 156x equivalent-workload loss.

### ACTINV mesh path

| cells | threads | median wall time | median throughput | peak RSS |
|---:|---:|---:|---:|---:|
| 8 | 4 | `0.394 s` | `20.3 cells/s` | `128.7 MB` |
| 64 | 4 | `0.429 s` | `149 cells/s` | `128.7 MB` |
| 256 | 4 | `0.539 s` | `475 cells/s` | `128.8 MB` |

Four threads were `1.43x` faster than one at 256 cells. A linear fit projects one million cells at roughly `582 s`
and `13.6 GB` of output, but **that run was not executed**. Filesystem, serialization, allocator, and long-run scaling
could invalidate the extrapolation; it must never be quoted as a measured million-cell benchmark.

## First use and diagnostics

| exercise | ACTINV | ALARA |
|---|---:|---:|
| software install/build | PyPI wheel `0.945 s` | clean one-job build `39.22 s` after configure |
| released/software artifact | `3.20 MB` wheel | `10.1 MB` installed tree |
| production/default data setup | `145.5 MB` network, `5.35 s`, all hashes verified | not comparable; official sample is deliberately truncated |
| first supplied example result | `2.46 s` | conversion `0.027 s` + solve `0.319 s` on truncated sample |
| documented commands through first result | 3 | 4 build/install commands, excluding dependency setup |
| planted missing file, malformed input, inconsistent data | all failed nonzero and named the offending item | all failed nonzero and named the offending item |

FISPACT-II and SCALE/ORIGEN installation was not executed. Official prose is not relabeled as an install benchmark.

## Capability scorecard

`V` = verified complete for the named axis; `P` = meaningful partial support; `A` = confirmed absent; `?` = not
verified from sufficient current official evidence. “Unverified” is not a hidden “no.” The source-linked explanation
for every cell is in [`results/cb1_capabilities.json`](../results/cb1_capabilities.json).

| capability axis | ACTINV | ALARA | OpenMC | FISPACT-II | SCALE/ORIGEN |
|---|:---:|:---:|:---:|:---:|:---:|
| licence/access model established | V | V | V | V | V |
| install path established | V | V | V | V | V |
| projectiles and energy domain | P | P | P | V | P |
| finite-dilution self-shielding | P | ? | V | V | V |
| irradiation schedules | V | V | V | V | V |
| fission yields | V | ? | V | V | V |
| covariance/uncertainty | P | ? | P | V | P |
| activation responses | V | V | P | V | V |
| transport coupling | P | P | V | P | V |
| CLI and programmatic API | V | P | V | V | P |
| deterministic input provenance | V | ? | ? | ? | ? |
| spatial/mesh operation | V | V | V | ? | P |
| continuous feed/removal | A | ? | V | ? | V |
| reverse calculation | A | V | ? | ? | ? |
| damage observables | A | ? | V | V | ? |
| documented operating-system routes | V | P | V | V | V |
| compile-time physical unit types | A | ? | ? | ? | ? |

Important distinctions behind the compact table:

- FISPACT-II's official documentation establishes seven projectile libraries, probability-table self-shielding,
  full-covariance uncertainty, pathways, radiological outputs, and damage observables. Its research/commercial access
  is licensed rather than open distribution. See the [official overview](https://fispact.ukaea.uk/wiki/Main_Page),
  [data catalogue](https://fispact.ukaea.uk/wiki/Nuclear_data_downloads),
  [self-shielding method](https://fispact.ukaea.uk/wiki/Probability_table_self-shielding), and
  [licensing routes](https://fispact.ukaea.uk/wiki/User_licences).
- OpenMC's defining strength is transport-coupled depletion, including probability-table transport rates and
  material transfers. It is not represented here as a dedicated activation-reporting clone. See the
  [official depletion guide](https://docs.openmc.org/en/stable/usersguide/depletion.html).
- ORIGEN's strengths include mature neutron depletion/source terms and continuous feed/removal, with transport and
  self-shielding support supplied by the wider SCALE suite. Standalone ORIGEN is spatially zero-dimensional. See the
  [ORIGEN overview](https://scale-manual.ornl.gov/6.3.2/origen/index.html),
  [theory](https://scale-manual.ornl.gov/6.3.2/origen/origen-theory.html), and current
  [SCALE distribution record](https://rsicc.ornl.gov/codes/ccc/ccc8/ccc-860.html).
- ALARA remains a lightweight open activation code with arbitrary hierarchical schedules, multi-point solutions,
  activation trees, clearance/waste outputs, and reverse calculation. See its
  [official users' guide](https://svalinn.github.io/ALARA/usersguide/introtext.html).

## What ACTINV should try to be best at

The evidence supports a focused product promise:

1. **Open, reproducible activation and R2S.** A new user gets a wheel, verified production data, explicit input hashes,
   a certificate, and a missing-data ledger without negotiating a software licence.
2. **A fast, embeddable sparse inventory core.** The identical-operator kernel result is strong and directly
   measured. Rust CLI/Python interfaces make the same implementation usable in automated studies.
3. **Transparent fixed-spectrum and many-cell workflows.** ACTINV imports common transport outputs and exports photon
   sources without pretending to be a transport code; the streamed mesh path keeps measured memory nearly flat over
   the tested range.

ACTINV does not need to replace OpenMC's transport, SCALE's reactor-analysis suite, or every legacy FISPACT feature to
be the best version of that product. It does need to make its scientific accuracy and limitations unusually easy to
audit.

## Work indicated by CB1

Priorities are evidence-driven; none was implemented before this initial scorecard closed.

1. **Run a same-data many-nuclide FISPACT comparison.** This is the highest-value missing experiment. Obtain lawful
   access or a collaborator, feed both products the same processed library and schedule, compare initial rates first,
   then every inventory step. This isolates code from evaluation version.
2. **Diagnose the FNS accuracy losses without tuning to the test set.** Rank reactions/nuclides behind the gap in
   median point error and whole-experiment coverage, then validate candidate data or processing changes on independent
   held-out families.
3. **Add finite-dilution self-shielding where the intended users need it.** This is the largest activation-physics
   capability gap versus FISPACT and SCALE/OpenMC workflows.
4. **Reduce data-load memory and repeated parsing.** The `1.09 GB` public-example peak is acceptable on this host but
   too high for an otherwise lightweight tool. Memory mapping, a prepared immutable cache, or narrower decay loading
   should be evaluated without weakening hash verification.
5. **Broaden uncertainty evidence.** ACTINV has useful MF=33/local-response propagation, but not FISPACT's documented
   breadth. Independent covariance benchmarks and coverage studies should precede a broader claim.
6. **Make unit mistakes harder and extend metamorphic tests.** Public quantities are still `f64` values whose units
   live in field names and validation. Zero-cost domain types at internal boundaries deserve a scoped design study.
   Add broad linear-scaling, schedule-splitting, decay-only Bateman, group-rebin, and mass/atom conservation relations
   across the product path.
7. **Treat feature breadth as demand-led.** Gamma/triton/helion activation, feed/removal, reverse calculation, and
   damage observables are legitimate gaps. Implement them when they strengthen the reproducible activation/R2S niche,
   not simply to make every matrix cell green.

## Reproduction and limits

The frozen protocol is [`protocols/ACTINV-CB1_PROTOCOL.md`](../protocols/ACTINV-CB1_PROTOCOL.md). Machine-readable
evidence is in `results/cb1_*.json`; controls are in `controls/cb1_*.py`. Bulk nuclear data, licensed software, and
temporary benchmark caches are intentionally not committed.

The main limits are:

- one timing host and warm OS caches;
- no fresh FISPACT-II or SCALE/ORIGEN executable;
- FISPACT measurement output from 4.0/TENDL-2017 while capability documentation describes the current 5.1 family;
- no equivalent-output full-product timing row across products;
- a deliberately small identical-data ALARA chain; and
- no executed million-cell run.

CB1 is a reproducible first scorecard, not the end of competitive validation.
