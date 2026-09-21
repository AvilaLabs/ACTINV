# Leadership levers — analysis and scoping (2026-09-20)

Where ACTINV stands after the CB3 identical-data campaign, and what it
would take to go from competitive to best-in-class. Written after the
decay-state-resolution work (commit `8ac697f` and the fallback/loose
extension in progress); numbers quoted are the post-fix CB3 values.

## Why the identical-data margin is capped

CB3 compares ACTINV and FISPACT-II on the same TENDL-2017 data. With the
same cross sections in, the C/E is dominated by XS evaluation quality and
measurement uncertainty, not the solver. Measured ceiling: 130/132
experiments already agree within 30% between the two codes; median pooled
|log C/E| is 0.1040 vs 0.1053. The remaining differences are state-handling
defects, and four classes were found and fixed (isomer-product mapping,
coverage scope, product-only isomers, cross-library state numbering). A
*substantial* accuracy lead on identical inputs would require the incumbent
to have large systematic defects — the well is nearly dry.

A durable "best bar none" position therefore has to come from axes where the
architectures genuinely differ: qualified uncertainty campaigns, current
evaluated data, validation breadth, complete spatial workflows, and
provenance/diagnostics.

## Lever 1 — Qualified uncertainty at campaign scale (P30-CONDITIONAL)

**Current machinery** (`crates/actinv-core/src/uncertainty.rs`): hashed
covariance input, first-order propagated variance, per-channel decay and
fission-yield sensitivities, response bands with per-channel coverage
accounting, plus a sampling verification control. `build-covariance`
produces collapsed covariances.

**Conditions recorded at closure** (`results/verdict_p30.json`), with
implementation status as of this session:

- ~~Collapsed covariance required a nonzero diagonal ridge for correlated
  sampling, and a substantial fraction of draws clamped nonpositive.~~
  **Implemented:** the ridge ladder and Cholesky/diagonal fallback are
  replaced by a cyclic-Jacobi eigendecomposition of the *relative*
  covariance, eigen-clipped to the nearest PSD; draws are mean-preserving
  lognormal factors `exp(F·z − ½·diag(FFᵀ))` — never nonpositive, never
  uncorrelated. The clipped eigenvalue mass is ledgered as
  `negative_eigenvalue_mass_clipped_relative`. Flux and composition
  channels use the same lognormal convention.
- ~~Local-vs-nonlinear comparison rests on a root-sum-square
  approximation~~ **Implemented:** `activity.total` is now a first-class
  uncertainty response propagated as sᵀΣs over the full covariance (spec,
  snapshot, tangent, and first_order_std all updated); the per-nuclide RSS
  combination is gone.
- MF=33 covariance coverage is partial — uncovered active rows carry no
  declared uncertainty. *Data-bound:* already ledgered per-band
  (`uncovered_library_rows`, `uncovered_decay_constants`,
  `uncovered_yield_products`); a coverage summary is emitted per step.
- Flux and composition channels exist in the robustness sampling layer.
  `self_shielding` + `uncertainty` is now supported: the MF=33 collapse
  weights each row's flux by the same per-group shield factors the depletion
  fold applies (`collapse_weighted`; nominal cross sections are checked
  bitwise against the shielded depletion convention), and the study
  robustness path folds the same plan into its sampled collapse.
- No blind experimental validation — the FNS suite is the closest
  available blind-ish set; a per-experiment uncertainty-coverage score
  (fraction of measurements inside the propagated band) is locally
  computable once channels exist.

**Campaign layer** (P31-CONDITIONAL exists): prepared-run amortization,
streaming, resume-by-digest are measured. The remaining step toward
"qualified campaign" is wiring a sampling driver over the robustness spec
knobs with complete population accounting and the per-channel coverage
table above.

## Lever 2 — Current evaluated data (in flight)

TENDL-2017 rebuild with `--decay --decay-fallback` is running; TENDL-2025
follows (2850 files, ~10 h). The fallback/loose-resolution work recovers
six previously homeless isomer states (Sb-120m, Cu-68m, Cs-135m, Dy-147m,
Hf-178n, Au-189m) — 363 synthetic emissions collapse to ~9 genuinely
homeless states. If TENDL-2025 evaluations are better, ACTINV-on-current-
data legitimately beats everything frozen on TENDL-2017 — a *product*
claim about shipped data quality, not a solver claim.

## Lever 3 — Validation breadth

The repo already holds **two** measured families: FNS decay-heat (132
experiments, CB3) and the IRDFF-II SACS foil-activation corpus scored in
P24/P25 (`g4_p25c_irdff_score.json`: 17 comparable rows, median |ln|
0.065, 100% within 30% on TENDL-2025 — retrospective, consumed
evidence). Re-scoring the IRDFF arm against the rebuilt artifacts is
locally repeatable.

What's genuinely absent and why:

- **SINBAD** — license-gated (NEA GitLab, member-country licensees);
  public abstract pages exist but the data files 404. External blocker:
  needs a SINBAD license, also the natural source of an external R2S
  geometry for Lever 4.
- **JAERI FNS data compilations** (JAERI-Data-Code-98-021 et al.) —
  public PDFs on JOPSS, but scanned images with no text layer; OCR of
  1990s numeric tables is not trustworthy for a validation set.
- **Activity time-series** — the FNS `.exp` files are 3-column
  decay-heat records only; no fetchable public activity-family found.
- `conderc-fission` — two U-235 FISPACT inputs only.

Net: breadth means SINBAD (blocked on a license) or a curated extraction
from scanned JAERI tables (blocked on transcription risk). Documented,
not claimed.

## Lever 4 — Spatial R2S handoff (P32-CONDITIONAL)

The chain is *executed* (`controls/g1_p32_chain.py`, `results/g1_p32.json`:
neutron tally → flux import → 64-cell mesh activation → distributed
`openmc.stats.Box` sources → OpenMC photon transport → `openmc.deplete`
comparator at 2.14e-7 median deviation). Recorded conditions and their
local discharge paths:

- *Self-produced geometry only* — needs an external benchmark geometry;
  SINBAD is the natural source.
- *Photon leg is a flux proxy, not dose* — **implemented**:
  `controls/p32_dose.py` replays the frozen exported sources (SHA-re-verified)
  through OpenMC photon transport with an `EnergyFunctionFilter` scoring
  E·μ_en,air directly — a dose rate with MC std-dev, additive evidence that
  leaves the frozen artifacts untouched. Awaiting the job slot.
- *Tally statistical error not propagated* — carry the neutron tally
  std-dev into the activation comparison band; engineering only.
- *MCNP export remains point-at-origin* — MCNP SDEF distributed-source
  emission is implementable but unverifiable without an MCNP install;
  keep documented as placeholder.

## Lever 5 — Residual same-data gaps (in flight)

The Sb-2000 uniform gap resolved as a *sixth defect class*: cross-library
level-scheme disagreement (ENDF calls Sb-120m 151 keV/LIS 4; JEFF and
TENDL call it 200 keV/LIS 6). Fixed via fallback-decay resolution plus a
sole-candidate loose-ELIS tier. Remaining tail is dominated by shared
hard cases (In, Tb, Rh, Bi, Os, Na) where both codes miss — those are
nuclear-data or measurement limits, not solver defects.

## Lever 6 — Kernel-throughput crossover — RESOLVED 2026-09-20

CB2 measures the identical-operator CRAM-48 kernel ratio vs
OpenMC 0.15.3's Python `CRAM48`, both pinned to one thread:
**182× / 13.8× / 2.64× / 1.28× / 0.98× / 1.83×** at
2/32/256/1024/2048/4096 states (the two largest sizes added to cover
the ~1700-state operators the TENDL-2025 library produces — 2048 reads
parity under host contention, 4096 confirms no second cliff). The
1024-state crossover is gone — ACTINV leads or matches at every size
*while still running the compensated-residual verification OpenMC does
not perform*.

Diagnosis (instrumentation: `refinement_stats` + `cram_probe` phase
mode): the regression was NOT the dynamic-range gate. At 1024 states all
24 poles flagged `backward_ok` — on rows whose entire scale is
subnormal (denominators ~1e-318, below `f64::MIN_POSITIVE`), where a
relative backward-error bound is unrepresentable and its scaled floor
underflows. SuperLU's unrefined solve fails the identical bound on the
identical rows — the bound is un-satisfiable at subnormal scale, and
OpenMC simply never checks. The same rows also drove refinement to 2–5
iterations because the correction floor (1e-14·denom) underflows below
denormal deltas — non-convergent by construction, exiting only at the
5-iteration cap or on bit-stability.

Fix (all semantics preserved):

- `backward_ok` and the correction floor now exempt rows at subnormal
  scale — a row whose total magnitude is below `f64::MIN_POSITIVE`
  cannot hide a material component and cannot satisfy a relative bound.
- `x_min_nonzero` counts only normal-scale components.
- `scale_shift` builds the shifted matrix directly in CSC form instead
  of a triplet round-trip (~6× faster: 2.0–8.4 ms → 0.35 ms).

A "material-and-hidden" narrowing of the range check was tried and
rejected: the trace-daughter test proved the contract is stronger than
phantom protection — refinement *achieves* forward accuracy on
sub-scale components, and visibility to a row's residual check does not
guarantee it. The RANGE_TOL flag stays; at 1024 states all 24 poles
still flag it (ref_range), each now converging in exactly one
iteration.

Verification: 78/78 actinv-core tests including all phantom-parent and
trace-daughter regressions; CB1 numerical output bit-identical to the
pre-change record; CB2 re-run under the same cgroup/host.

Not taken: pole parallelism (no benefit under the benchmark's 1-thread
pin) and factorization reuse across identical (A, dt) steps (bounded
benefit — FNS-style cooling varies dt).

## Capability matrix (external landscape, 2026-09)

Verified against public sources — FISPACT-II manuals/pricing, OpenMC
0.15.3 release notes (2025-11-22), published ARC-class and FNG studies.

| axis | ACTINV 1.1.2 | FISPACT-II 5.x | OpenMC 0.15.3 | verdict |
|---|---|---|---|---|
| Same-data FNS accuracy | 71/132 ≤30%, median 0.1030 | 69/132, 0.1053 | n/a | **ACTINV (narrow; inside decay-library sensitivity — JEFF-primary erases to parity)** |
| Provenance/audit | full SHA chain + decision ledger + defect eviction | licensed binaries, condensed libs, JEFF pipelines non-public | open source, no data ledger | internal QA, not a market axis |
| Isomer/state resolution | auto ELIS/LIS + fallback + loose tiers, audited | manual condensed-library convention | incomplete activation chains (ARC paper) | **ACTINV** |
| Solve verification | compensated-residual refinement gate | none documented | none (raw spsolve) | **ACTINV** |
| UQ channels | XS + decay + yield + flux + composition | XS + decay + TMC/GEF yields; pathways + MC sensitivity | none for depletion | ~parity (FISPACT deeper on yields) |
| UQ sampling mechanics | eigen-PSD lognormal, no ridge/clamps | covariance collapse + MC | n/a | **ACTINV (mechanics)** |
| R2S | mesh + distributed sources, executed vs deplete | MCR2S workflow | R2SManager, FNG-published | parity on capability |
| Kernel speed | 182×/13.8×/2.64×/1.28×/0.98×/1.83× @2–4096 states | proprietary solver | Python CRAM (C++ port in progress) | **ACTINV (≥parity at scale)** |
| Self-shielding × UQ | shield factors folded through MF=33 collapse, bitwise-checked vs depletion | CALENDF prob tables through collapse+UQ | n/a | parity |
| Per-step spectra | schedule steps carry distinct spectra (union prune, per-step fluence); UQ combination fail-closed | multi-spectrum pulse sequences | n/a in depletion | parity |
| Projectiles | n/p/d/α | n/p/d/α/γ | n via transport | FISPACT (γ) |
| Access | source-available | £15k–35k or NEA/RSICC restricted | open source | ACTINV/OpenMC |

**Net:** excluding external validation, ACTINV's competitive leads are
kernel speed at parity-or-better to 4096 states (182×/13.8×/2.64×/1.28×/
0.98×/1.83×) and isomer-resolution mechanics; identical-data accuracy is
at parity with FISPACT inside decay-library sensitivity (ENDF-primary
71/132 vs 69/132; JEFF-primary erases the margin). Per-step spectra
closed the schedule-depth gap on 2026-09-21 (UQ+multi-spectrum remains
fail-closed); it still trails FISPACT on TMC yields and γ projectiles.
Audit/provenance infrastructure remains internal QA, not a selling axis
(owner direction 2026-09-21). OpenMC's C++ CRAM port may still contest
the kernel lead at scale.

## Ranking

1. **Lever 2** is running — cheapest real gain, watch it land.
2. **Lever 1** has the highest ceiling: it converts the measured speed
   advantage into a *capability* competitors don't offer at campaign
   scale, and most of its conditions are locally dischargeable.
3. **Lever 4** conditions are mostly local engineering on an executed
   chain.
4. **Lever 3** is real but needs external data fetches.
5. **Lever 5** converges — each fixed class shrinks the tail further.

Honesty boundary: none of this produces a blanket "better than FISPACT"
claim. What it can produce, all locally: same-or-better identical-data
accuracy, a qualified-uncertainty workflow at campaign scale, an executed
distributed R2S path with dose and error propagation, and a data pipeline
that ledgers every defect class found.
