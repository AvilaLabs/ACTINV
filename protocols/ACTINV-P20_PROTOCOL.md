# ACTINV-P20 protocol — practical uncertainty

Status: **frozen** at the opening commit. Amendments land as separate files;
this file is never edited afterward.

## Opening context

Roadmap row (post-v1 program, frozen by CB1): "Audit P11 covariance coverage;
propagate relevant correlations into usable observable bands; compare linear
propagation with deterministic correlated sampling; distinguish
cross-section, decay, yield and uncovered model uncertainty." Prerequisite:
P16. P19 closed `P19-PASS`; P18b closed `P18b-FAIL`. P20 reads no P18b
evidence and touches no P18b surface.

P11 shipped first-order local propagation of spectrum-collapsed ENDF-6 MF=33
covariance into response bands, with explicit covered/total parameter counts
and named uncovered coverage. The collapse already emits a dense within-MT and
cross-MT covariance matrix and the run already propagates the full quadratic
form `s^T C s` including signed off-diagonal terms. P20 widens the claim
honestly: a complete MF=33 census, PSD/asymmetry defect exclusion with named
reasons, an independent deterministic sampling check of the linear
approximation, and additional decay-constant and fission-yield uncertainty
channels — each with its own coverage fraction.

## Design contract

### Coverage census

Before any new propagation claim, a complete census of MF=33 covariance in
the frozen TENDL-2025 neutron library: per (ZA, LISO, MT) block presence,
block shape (component kinds and NC sub-subsection counts), energy overlap
with the activation rows, and the fraction of the reference workload's
sensitivity mass that is covered. The census is a committed control;
uncovered parameter mass is always named in output.

### Correlation propagation

Propagation uses the full within- and cross-MT covariance matrix where the
evaluation provides it:

- propagated variance uses the full block `s^T C s`, not only diagonal terms;
- off-diagonal sign is carried (covariance may be negative where lawful);
- asymmetric or non-positive-semidefinite blocks are diagnosed, counted, and
  excluded from propagation with a named reason — never silently clipped to
  PSD, never silently symmetrized beyond the protocol's declared tolerance;
- response bands retain the P11 reporting shape plus a per-channel breakdown:
  cross-section (MF=33), decay constants, fission yields, and a named
  uncovered/model remainder.

### Frozen PSD and asymmetry rules

For each collapsed covariance block used in propagation:

- the symmetric part `S = (B + B^T)/2` is formed only for diagnosis; the
  stored block itself is used for propagation exactly as emitted;
- **asymmetry**: a block is `asymmetric_block` when
  `max|B_ij - B_ji| > 1e-9 * max|B_ij|` and `max|B_ij - B_ji| > 0`;
- **PSD**: a block is `non_positive_semidefinite` when its symmetric part's
  minimum eigenvalue satisfies `lambda_min < -1e-10 * lambda_max` with
  `lambda_max > 0`, or `lambda_min < -1e-30` barn^2 when `lambda_max <= 0`;
- excluded blocks contribute nothing to the propagated variance; each
  exclusion is recorded with `(target, mt, mt1, reason, measured defect)` and
  counted in the run ledger and the report's `excluded_blocks` field;
- when no exclusions occur the report records `excluded_blocks: []`.

### Deterministic correlated sampling check

An independent control samples the correlated parameter vector
deterministically — stratified quantiles through the eigendecomposition of
the collapsed covariance's symmetric part, same no-RNG discipline as P19 —
and propagates each sample through the same observable.

- **Stratification**: K = 9 equiprobable standard-normal strata per
  eigen-axis; the axis-j stratum-i sample is `x = q_i * sqrt(lambda_j) * v_j`
  with `q_i = normal_quantile((i + 0.5) / 9)`, `i = 0..8`. Sample count per
  block = `9 * d+ + 1` where `d+` is the number of eigenaxes with
  `lambda_j > 0`, plus the nominal `x = 0`.
- **Observable**: each sample perturbs the collapsed one-group parameters;
  the response is re-evaluated by scaling every covered activation row's
  group rate by the perturbed-to-nominal parameter ratio and re-solving the
  network — not through the linear model.
- **Shared regime**: a case is in the shared regime when every covered
  parameter's sensitivity sign is constant over the sampled set and every
  sampled response stays finite and positive.
- **Agreement tolerance**: in the shared regime the sampled central 90%
  interval half-width must agree with the linear `1.645 * sigma` band
  half-width within 5% relative. Outside the shared regime the difference is
  reported, not normalized away.

### Uncertainty channels

Three channels are distinguished everywhere in output:

1. cross-section (MF=33, as P11 plus PSD/asymmetry exclusion);
2. decay constants — `lambda = ln2 / T_half`, relative uncertainty
   `d_lambda/lambda = dThalf/Thalf` taken from the MF=8/MT=457 LIST
   half-life-uncertainty field of the evaluated decay files; nuclides whose
   files carry no such field are named as uncovered;
3. fission yields — independent-yield relative uncertainties `dy/y` taken
   from the MF=8/MT=454 `DY` field of the evaluated yield file at the case's
   incident energy; parent/product/state entries without yield data are
   named as uncovered.

Within each channel, parameters are treated as uncorrelated unless the file
provides correlation; decay and yield evaluations carry none, so those
channels propagate diagonal variances. Each channel reports its own coverage
fraction; a response band is labeled `partial` unless every channel is
complete. No output field is ever named "total uncertainty" without total
coverage.

### Decay and yield file pinning

Decay-channel extraction reads only these two hash-pinned files (the same
pair the P18b scorer uses):

- `endfb_viii_0`: `endf-b-viii-0_decay.dat`,
  sha256 `6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb`
- `jeff_3_3`: `jeff-3-3_decay.dat`,
  sha256 `850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123`

The yield channel reads the evaluation already vendored for the run's
fission parents through the existing `fission.rs` path; the control records
the resolved file sha256 per parent.

### Combination with self-shielding

`uncertainty` combined with `self_shielding` remains rejected (P19 G2 leg);
P20 does not relax that. A covariance-aware shielded run is out of scope.

## Evidence and scoring rules

- Analytic synthetic covariance cases (known eigenvalues, known
  sensitivities) verified to machine precision at both propagation paths.
- Fixed-input reproduction: identical spec + identical sidecars give
  byte-identical uncertainty output.
- Partial coverage must be demonstrable on a real workload with named
  uncovered parameters.
- The deterministic sampling control must pass on a synthetic PSD block
  before it is applied to the real case.
- No RNG anywhere; the seed structure is the frozen stratification itself.

## Gates

- **G0** — protocol freeze, identity baseline, MF=33 census inventory, oracle
  plan, Core case opened.
- **G1** — coverage census control committed and independently reproduced.
- **G2** — PSD/asymmetry exclusion + per-channel band schema in the runtime;
  absent-section byte identity preserved.
- **G3** — deterministic sampling oracle; linear-vs-sampled agreement in the
  shared regime on synthetic and real cases; documented divergence outside.
- **G4** — decay and yield channels, docs, examples, performance.
- **G5** — independent closure checker, verdict, session record.

## Explicit non-claims

- No claim of total uncertainty unless every channel is complete; uncovered
  parameter mass is always named.
- No MF=34/35 (resonance/secondary) covariance; no model-defect terms; no
  covariance between different nuclides unless the file provides it.
- Correlated sampling is a verification control, not a shipped feature.
- No decay-yield or decay-cross-section cross-channel covariance; channels
  sum as independent variances.

## Closure interpretation

`P20-PASS` means ACTINV propagates full within-MT covariance into observable
bands with honest per-channel coverage, defective blocks are excluded with
named reasons, the linear and sampled paths agree in their shared regime,
and every emitted interval names what it does not cover.
