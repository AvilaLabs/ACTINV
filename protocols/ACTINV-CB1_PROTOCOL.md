# ACTINV CB1 — public competitive benchmark

Opened 2026-08-28 after the v1.0.0 release, at source commit
`19afc18d1f65d696512d52d848ec0a145e67534e`. CB1 measures ACTINV against independently implemented solvers,
published experiments, and the documented capabilities of established activation/depletion products. It is a
post-release evidence campaign, not a numbered product phase.

The initial scorecard is frozen before new results are generated. No numerical, physics, data-processing, or product
change may be made to improve an initial CB1 result. Findings become separately reviewed follow-up work after the
initial report closes. The portable public example repair in the opening commit predates this protocol and changes no
scientific result.

## Questions and scope

CB1 answers five separate questions; it does not collapse them into a single “winner” score.

1. **Numerical correctness:** given the same transition operator, initial vector, and schedule, does ACTINV agree
   with independent matrix-exponential implementations?
2. **Identical-data behavior:** given the same processed nuclear data and irradiation history, does ACTINV agree with
   another activation product?
3. **Predictive accuracy:** how closely do complete product-and-data combinations reproduce public measurements?
4. **Cost and first use:** what wall time, peak memory, setup work, and failure diagnostics does a user actually see?
5. **Capability:** which user-relevant features are verified present, partial, absent, or unverified in each product?

The compared products are ACTINV 1.0.0, ALARA 2.9.2, OpenMC 0.15.3 depletion, FISPACT-II, and SCALE/ORIGEN. OpenMC is
included as an independent depletion/numerical anchor rather than represented as a dedicated activation-code clone.
No competitor is assigned a zero or a loss merely because its executable or licensed data are unavailable.

## Access and evidence rules

Every cell and result carries exactly one access class:

- `executed`: the named executable was run in CB1 with recorded version, inputs, command, and environment;
- `published-reference`: a named public output or publication was independently parsed, but its executable was not
  run in CB1;
- `documented-only`: a capability is supported only by current official documentation;
- `not-available`: CB1 has neither lawful executable access nor a suitable public result;
- `not-applicable`: the comparison would not answer the stated question.

ACTINV, ALARA, and OpenMC are available for execution. No FISPACT-II or SCALE/ORIGEN executable was found at the scope
freeze. Their fresh-execution cells therefore remain `not-available` unless the maintainer later supplies lawful
access under a separately recorded amendment. Public CoNDERC FISPACT-II outputs may be `published-reference`. CB1
will not download, redistribute, emulate, or substitute for licensed software or data without permission.

Claims are layered so code and nuclear-data effects are not confused:

1. same operator, initial vector, and schedule: exponential solver only;
2. same processed reaction/decay data and schedule: chain construction plus solver;
3. same raw evaluation and spectrum: processing plus chain plus solver;
4. each product with its named data against experiment: complete product-and-data performance.

Cross-layer rankings are forbidden. In particular, ACTINV/TENDL-2025 against a FISPACT-II/TENDL-2017 public result
is useful product context but not a differential solver test.

## Frozen inputs and environment

- ACTINV source: `19afc18d1f65d696512d52d848ec0a145e67534e`.
- TENDL-2025 neutron library:
  `ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44`; index
  `8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb`.
- ENDF/B-VIII.0 decay payload:
  `6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb`; JEFF-3.3 fallback:
  `850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123`.
- CoNDERC FNS archive:
  `ba1dd6cb150a4aa3e0d81461054aec7d415ef19d946aba8b9886b31de218252d`.
- CoNDERC fission archive:
  `30756fef88c0f3637246bf8ad8ef1fc5397a3f784e5408f2861bc474993e74a5`.
- ALARA official 2.9.2 source commit:
  `faa5b330460fe865e38fc788f1b792ea33d13d1b`; OpenMC `0.15.3`, NumPy `2.5.2`, and SciPy `1.18.0`.

The control records CPU, core count, memory, operating system, compiler/interpreter versions, thread environment, and
executable hashes. Bulk libraries, raw evaluations, licensed artifacts, and benchmark caches remain outside Git.
Compact derived JSON, protocols, controls, and the report are committed.

## Gates

### G0 — provenance and access inventory

An independent control verifies every available input identity and executable/version pin before any scientific or
timing result is accepted. It records all five products' access classes and explains every non-executed cell. A hash,
version, or source mismatch fails closed; it is not silently relabeled.

### G1 — identical-operator numerical parity

ACTINV CRAM-48, OpenMC CRAM-48, and SciPy's dense matrix exponential receive byte-identical two-state and deterministic
sparse-chain operators, initial vectors, and constant/pulsed schedules. Populations above
`max(1e-24 * initial_atoms, 1e-30)` must agree with the dense control within `5e-12` relative or `5e-14` of the
initial-vector 1-norm in absolute terms. All values must be finite and nonnegative within that absolute tolerance;
split/merged equal-operator schedules must meet the same bound. The report prints actual worst errors and does not
describe a tolerance pass as performance superiority.

### G2 — identical processed-data activation case

The official ALARA 2.9.2 executable and ACTINV receive the same independently extracted FENDL-2 Fe-56(n,p)Mn-56
group data, source spectrum, initial atoms, ten pulses, nine gaps, and decay constant used by the frozen P9 control.
Collapsed reaction rates must be identical to `1e-12` relative. Shutdown Fe-56 and Mn-56 inventories must agree
within `5e-4` relative, the frozen precision bound of ALARA's text output. Analytic two-member Bateman values are
reported alongside both. New CB1 evidence must rerun the executables; copied P9 numbers alone do not close G2.

### G3 — public measurement accuracy

The complete 132-experiment CoNDERC FNS family is rerun through the current Rust-owned ACTINV path with the frozen
TENDL-2025 and decay payloads. Measurements and public FISPACT-II 4.0/TENDL-2017 outputs are parsed independently from
the frozen archive. Historical ACTINV EAF-2010 and TENDL-2023 results remain clearly labeled historical.

For each experiment and product/data combination, only aligned positive measurement/calculation pairs are scored.
The report includes pair count, geometric-mean C/E, median and 90th-percentile `abs(ln(C/E))`, median experiment-level
maximum `abs(ln(C/E))`, and the fraction of experiments for which every scored point lies within 30%. It also reports
the unscored/zero/misaligned points and the archive-provided measurement uncertainties without inventing replacement
uncertainties. No after-the-fact measurement floor or nuclide exclusion is permitted.

The frozen P9 CoNDERC fission cases and P12 FNG/ITER activation case are independently rederived or explicitly marked
as prior evidence with their exact source commit. They are reported as separate validation families, not pooled with
FNS. G3 is a measurement report: disappointing accuracy does not make the control fail unless inputs, alignment, or
arithmetic are invalid.

### G4 — performance and resource cost

Timing controls are sequential and bounded by the repository memory guard. Scalar numerical kernels use one thread;
mesh results name the exact thread count. Every timed process receives five warm-ups and thirty measured repetitions,
except a control may use more repetitions for sub-millisecond kernels and fewer for a run whose profiled total would
exceed ten minutes; the deviation and profile must be recorded before the full run. Report minimum, median, p95,
arithmetic mean, sample standard deviation, and peak resident memory where the platform exposes it.

Separate tables cover: matrix-exponential kernel cost on identical operators; cold process/startup cost; data-load
plus end-to-end calculation; and ACTINV mesh throughput/scaling. Products are compared in one row only when workload,
state count, requested outputs, precision, and thread count match. Otherwise the values remain useful standalone
measurements with the mismatch named. Cache state and all thread-control environment variables are recorded.

### G5 — first-use and diagnostic exercise

From a clean temporary environment, the control records the documented commands, elapsed time, downloaded bytes,
installed bytes, and successful minimal run for ACTINV's PyPI plus `actinv data fetch` path. ALARA is measured from its
pinned public source and existing documented build dependencies in a clean build directory. Licensed products remain
`not-available`; published installation prose is not presented as an executed install.

Each executable path that is available is also given planted missing-file, malformed-input, and inconsistent-data
cases applicable to that interface. The report records exit status and whether the diagnostic names the offending
field/file. Command counts and observations remain separate; there is no subjective ease-of-use total.

### G6 — dated capability matrix

Current official documentation and executable probes populate a source-linked matrix using only `verified`,
`partial`, `absent`, `unverified`, and `not-applicable`. Axes include licence/access, install path, supported projectiles
and energy domains, self-shielding, schedules, fission yields, covariance/uncertainty, response quantities, transport
coupling, CLI/API, determinism/provenance, mesh operation, feed/removal, reverse calculation, damage observables, and
supported operating systems. Every non-ACTINV claim names product version, retrieval date, and official source. A
missing documentation statement is `unverified`, never `absent`.

### G7 — reproducible scorecard and close

`docs/COMPETITIVE_BENCHMARK.md` leads with the intended-user conclusions, then shows all losses and access limitations
beside strengths. Checker-derived JSON contains the exact table values. Controls reproduce every committed arithmetic
result from hash-pinned external inputs without committing those inputs. Required repository checks pass, the protocol
hash is append-only, the manifest is regenerated once at closure, and the source commit is pushed before CB1 closes.

## Interpretation

CB1 has no aggregate pass/fail winner. G0, G1, G2, and G7 are reproducibility/correctness gates; G3–G6 are measured
scorecard sections whose value is honest visibility. ACTINV's intended niche is a reproducible, scriptable,
easy-to-install activation and R2S engine with strong provenance and a Rust core. The conclusion may call ACTINV best
at a narrower property only when CB1 contains a directly comparable measurement or verified capability supporting
that wording; otherwise it states the evidence without a superlative.
