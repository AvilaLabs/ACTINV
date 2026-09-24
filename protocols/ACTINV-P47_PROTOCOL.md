# ACTINV-P47 protocol — dose-qualified spatial handoff

**Status:** FROZEN 2026-09-24 for G0 seal | **Parent:** roadmap draft
innovation extension P47 (`docs/ROADMAP.md`) | **Depends on:**
P32-CONDITIONAL (`results/verdict_p32.json`) — whose dose leg
(`results/p32_dose.json`) and tally-error propagation
(`results/p32_tally_error.json`) executed as post-close amendments on
2026-09-21 under the bounded cgroup. P47 qualifies those executed legs:
frozen bands, controls, propagated-band comparison, verdict.

This document freezes the comparison bands, job envelope, dose metrics
and verdict dispositions. Any change after the freeze is an amendment:
the protocol text is updated, a new sha-256 is taken, the seal
re-binds, and the verdict records that an amendment was used.

## Intent

Discharge P32's recorded conditions so the R2S chain's claim upgrades
from "flux proxy" to "computed dose on the executed geometry":

1. The dose leg executed: the same exported distributed sources
   (SHA-verified byte-identical at G0) replayed through OpenMC photon
   transport scoring E·μ_en,air directly, producing a dose rate with
   its Monte Carlo standard deviation — already executed as a P32
   amendment; P47 verifies artifact identity and qualifies the result.
2. Carry the neutron-tally statistical error into the activation
   comparison band — the per-cell tally relative std-dev enters the
   stated flux-normalization uncertainty; the comparison's agreement
   is reported inside a propagated band rather than as bare deviation.
3. Report the contact-proxy/transported-dose relationship for what it
   is: different models (uncollided semi-infinite slab vs transported
   geometry). Their ratio is *reported* with drivers named; it is not
   forced into agreement and no dose claim is made from the proxy.
4. External geometry: consume a benchmark geometry (SINBAD) **only
   if** a lawful copy arrives through the principal's channels before
   the freeze. None arrived — the self-produced condition stays named
   verbatim; it is not discharged by silence.

MCNP distributed-source emission remains a documented placeholder;
it cannot be verified without a licensed MCNP install.

## Frozen artifacts (re-hashed at G0, asserted byte-identical)

- `chain/mesh_result.ndjson` — 64-cell activation result (P32).
- `chain/flux.ndjson` — neutron mesh tally (mean + relative_error).
- `chain/source_step{2,3}.py` — exported distributed photon sources.
- `dose/dose_step{2,3}/statepoint.*.h5` — photon-dose statepoints.
- `tally_error/flux_pert{0..7}.ndjson` +
  `mesh_result_pert{0..7}.ndjson` — propagated-band solves.
- `chain/neutron/depletion_results.h5` — deplete comparison leg.
- `results/p32_dose.json`, `results/p32_tally_error.json` — the
  executed addendum records.

## Frozen metrics and bands

**Dose claim (the upgrade condition).** The dose table reports per
cooling step: `dose_Gy_h` and `mc_rel_std`. The upgrade to "dose on
the executed geometry" passes iff the G2 analytic control holds.

**Tally normalization (declared, verified 2026-09-24).** OpenMC
0.15.3's EnergyFunctionFilter flux tally returns a strength-weighted
track-length integral — `Σ_i strength_i · f(E_i) · L_i` — with **no
cell-volume division** (verified empirically on void, void+volume,
and material+volume cells; the `volume` XML attribute is ignored for
tally normalization). The physical dose rate is therefore

    dose_Gy_s = tally_mean / V_detector_cm3

with `V_gap = (4π/3)·10³ − 4³ = 4124.79 cm³` for the executed
geometry's detector cell. **This corrects the P32 amendment record**:
`results/p32_dose.json` read the raw tally as Gy/s directly, so its
published dose values are overstated by exactly V_gap (a ~4125×
normalization defect). P47 issues the corrected dose table and names
the defect; the P32 record itself is not rewritten.

**Analytic uncollided control (G2).** A 1 MeV monoenergetic isotropic
point source at the origin in an all-void geometry — spherical
detector cell r < 10 cm inside vacuum boundary r = 50 cm, unit source
strength — tallied with the identical EnergyFunctionFilter (E·μ_en,air,
same XCOM table sha). Every photon is born inside the detector and
streams exactly R = 10 cm through it; the exact uncollided
track-length tally is

    tally = S · f(1 MeV) · 10.0 cm     [Gy·cm³/s per unit strength]

Pass iff `|mc − analytic| / analytic ≤ max(3·mc_rel_std, 0.05)`.
Particles 20000, batches 100 — frozen.

**Propagated-band comparison.** Per cell, the tally-error record's
`relative_spread` (8-sample lognormal perturbation band, measured
~2.0%) is the propagated band half-width at the step. For each
top-50 compared (cell, nuclide) pair from the P32 activation
comparison, report `inside_band = rel_dev ≤ 2·relative_spread_cell`.
The claim: agreement is reported inside a propagated band — the
comparison never appears as bare deviation.

**Linearity control (G2).** A synthetic 2-cell mesh fixture: declared
per-bin rel_std σ₀ = 0.05; K = 16 lognormal perturbation samples at
σ₀ and at 2σ₀ through `actinv mesh`. Two assertions:
(a) *mechanism*: the std-dev of the written lognormal perturbation
factors scales as std(2σ₀)/std(σ₀) ∈ [1.7, 2.3];
(b) *response monotonicity*: the solved response spread increases,
spread(2σ₀) ≥ 1.2·spread(σ₀) — the response is not required to scale
linearly because second-order products carry a flux² term; only the
declared input uncertainty scales exactly.
Nominal central value unchanged (mean-shift < 0.5·spread(σ₀)).
(Amended 2026-09-24: the frozen K=8/band-[1.5,2.5] control carried
~±50% sampling noise on a stdev ratio and conflated the input-σ
scaling with nonlinear response physics.)

**Mutation control (G2).** Any single-byte mutation of a frozen
source file must fail the G0 re-hash assertion.

**Contact-proxy ratio (G3).** Report
`transported_detector_dose / Σ_cell contact_proxy` per step with named
drivers (uncollided-vs-transported, semi-infinite-slab-vs-detector
geometry, air μ_en convention shared). Reported, not judged.

## Job envelope

All OpenMC legs are cached-and-verified (statepoints exist and are
SHA-asserted); a replay happens only if a statepoint is missing or
corrupt. Controls add ≤ 10 min (analytic run ~2–5 min at 20k×100
particles, linearity fixture 16 two-cell mesh solves). Total P47
envelope 30 min under the bounded cgroup
(`MemoryMax=6G`, `CPUQuota=200%`), one job at a time.

OpenMC pinned: 0.15.3 (the P32 executor's version, env
`~/.local/share/mamba/envs/openmc`).

## Gates

- **G0** — seal: protocol sha, opening commit, every frozen artifact
  re-hashed and asserted byte-identical to its P32-sealed sha, OpenMC
  version pinned, envelope declared. Before any comparison is issued.
- **G1** — dose-table completeness: per-step dose + MC rel-std
  present, statepoint shas verified; completeness check passes.
- **G2** — controls: analytic uncollided dose inside frozen
  tolerance; linearity band-scaling; frozen-source mutation rejected.
- **G3** — report: propagated-band comparison issued per (cell,
  nuclide); contact-proxy ratio with drivers; geometry provenance
  verbatim.
- **G4** — independent closure: checker re-derives the dose table
  from the statepoint .h5 files (not from the record), re-verifies
  artifact identities, rejects planted mutations, emits verdict.

## Closure rule

PASS only if the dose leg's artifacts verify byte-identical, the
propagated-band comparison is issued, and all controls hold — with
the geometry provenance stated as whatever it actually was.
CONDITIONAL if an amendment was used or the external geometry
remained unavailable (condition named, not discharged). FAIL
otherwise.
