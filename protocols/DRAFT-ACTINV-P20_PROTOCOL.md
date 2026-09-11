# DRAFT ACTINV-P20 protocol — practical uncertainty

Status: **draft — not frozen**. This document is a working protocol draft for
maintainer review. It freezes at its opening commit only after explicit
direction; the frozen file is then `ACTINV-P20_PROTOCOL.md` and is never
edited afterward. Amendments land as separate files.

## Opening context

Roadmap row (post-v1 program, frozen by CB1): "Audit P11 covariance coverage;
propagate relevant correlations into usable observable bands; compare linear
propagation with deterministic correlated sampling; distinguish
cross-section, decay, yield and uncovered model uncertainty." Prerequisite:
P16. P19 closed `P19-PASS`; P18b may still be open — P20 reads no P18b
evidence and touches no P18b surface.

P11 shipped first-order local propagation of spectrum-collapsed ENDF-6 MF=33
covariance into response bands, with explicit covered/total parameter counts
and named uncovered coverage. P20 widens the claim honestly: full block
correlation propagation, additional uncertainty channels, and an independent
sampling check of the linear approximation.

## Design contract

### Coverage audit

Before any new propagation, a complete census of MF=33 covariance in the
frozen TENDL-2025 neutron library: per (ZA, LISO, MT) block presence, block
shape, energy overlap with the activation rows, and the fraction of the
reference workload's sensitivity mass that is covered. The census is a
committed control; uncovered parameter mass is always named in output.

### Correlation propagation

Extend the propagated quantity from diagonal/local blocks to the full
within-MT covariance matrix where the evaluation provides it:

- propagated variance uses the full block `s^T C s`, not only diagonal terms;
- off-diagonal sign is carried (covariance may be negative where lawful);
- asymmetric or non-positive-semidefinite blocks are diagnosed, counted, and
  excluded with a named reason — never silently clipped to PSD;
- response bands retain the P11 reporting shape plus a per-channel breakdown:
  cross-section (MF=33), decay constants, fission yields, and a named
  uncovered/model remainder.

### Deterministic correlated sampling check

An independent control samples the correlated parameter vector
deterministically — stratified quantiles through the Cholesky (or
eigendecomposition) factor, same no-RNG discipline as P19 — and propagates
each sample through the same observable. In the jointly-Gaussian shared
regime the sampled central interval must agree with the linear first-order
interval within a frozen tolerance; outside it (order-of-magnitude
parameters, sign-changing sensitivities) the difference is reported, not
normalized away.

### Uncertainty channels

Three channels are distinguished everywhere in output:

1. cross-section (MF=33, as P11 plus correlations);
2. decay constants — propagated from evaluated half-life uncertainties where
   the decay files carry them, marked estimated where they do not;
3. fission yields — propagated from evaluated yield uncertainties where
   carried.

Each channel reports its own coverage fraction; a response band is labeled
`partial` unless every channel is complete. No output field is ever named
"total uncertainty" without total coverage.

### Combination with self-shielding

`uncertainty` combined with `self_shielding` remains rejected (P19 G2 leg);
P20 does not relax that. A covariance-aware shielded run is out of scope.

## Frozen numerical definitions (to fix at freeze)

- Sample count per correlated block and the stratification rule.
- Shared-regime agreement tolerance between linear and sampled intervals.
- PSD-defect diagnosis thresholds and the exclusion record schema.
- The decay/yield uncertainty extraction rules and their file-source hashes.
- Deterministic seed structure for reproducibility: none — no RNG is used.

## Evidence and scoring rules (to fix at freeze)

- Analytic synthetic covariance cases (known eigenvalues, known
  sensitivities) verified to machine precision at both propagation paths.
- Fixed-input reproduction: identical spec + identical sidecars give
  byte-identical uncertainty output.
- Partial coverage must be demonstrable on a real workload with named
  uncovered parameters.

## Gates (draft)

- **G0** — protocol freeze, identity baseline, MF=33 census inventory, oracle
  plan, Core case opened.
- **G1** — coverage census control committed and reproduced.
- **G2** — full-block propagation runtime with per-channel bands and named
  coverage; absent-section byte identity preserved.
- **G3** — deterministic sampling oracle; linear-vs-sampled agreement in the
  shared regime; documented divergence outside it.
- **G4** — decay and yield channels, docs, examples, performance.
- **G5** — independent closure checker, verdict, session record.

## Explicit non-claims

- No claim of total uncertainty unless every channel is complete; uncovered
  parameter mass is always named.
- No MF=34/35 (resonance/secondary) covariance; no model-defect terms; no
  covariance between different nuclides unless the file provides it.
- Correlated sampling is a verification control, not a shipped feature.

## Closure interpretation

`P20-PASS` means ACTINV propagates full within-MT covariance into observable
bands with honest per-channel coverage, the linear and sampled paths agree in
their shared regime, and every emitted interval names what it does not cover.
