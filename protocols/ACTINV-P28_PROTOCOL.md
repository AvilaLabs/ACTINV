# ACTINV P28 — qualified physics combinations

Opened 2026-09-17 after P27 closed `P27-PASS` at commit `ff9344b`. P28 qualifies the
physics combinations the remaining investigation needs, starting from P25's recorded
disposition: P25-FAIL (coverage floors failed on all four projectiles), P25b-FAIL,
P25c-PASS (the shipped `tendl-2025-patched` artifact is a qualified derived candidate with
a verbatim defect ledger: 53 files carry other defect classes, the dosimetry-critical set
{Ni-58, Nb-93, Ag-109, In-113, Au-197} remains unrecovered, isomeric identity is qualified
only to liso {0,1} product states, and no blind experimental evidence exists). P18/P18b
isomeric-identity verdicts remain FAIL. These limitations are inherited, not waived.

Prior verdicts are preserved byte-for-byte and never rewritten: `P17-FAIL`, `P18-FAIL`,
`P18b-FAIL`, `P24-CONDITIONAL`, `P25-FAIL`, `P25b-FAIL`, `P25c-PASS`, `P26-FAIL`,
`P26b-CONDITIONAL`, `P27-PASS` stand exactly as recorded.

## Why this phase exists

Every downstream phase (P29 numerical control, P30 uncertainty, P31 campaign efficiency)
measures and optimizes inside whatever physics envelope P28 establishes. If the envelope
is wrong — if a combination that cannot be qualified is silently treated as qualified —
all later evidence inherits the error. P28's output is an explicit applicability map: for
the selected studies, which (element, projectile, spectrum structure, shielding mode,
temperature, response) combinations are qualified, which are contract gaps, and why —
with independent controls at the gates the roadmap names.

## Frozen scope

P28 may:

- add controls, machine-readable evidence, checkers, protocols and documentation under
  `controls/`, `results/`, `protocols/`, `schemas/` and `docs/`;
- run the released/current `actinv` binary and the pinned ALARA build under the
  workstation cgroup rule for the regime population and controls;
- produce the applicability map artifact (`results/g*_p28_applicability.json` /
  `docs/APPLICABILITY.md`) recording qualified, gap and excluded combinations;
- add narrowly-scoped production code only if a control requires a missing observation
  hook (e.g., an emitted per-case rate trace); any such change carries tests and the
  workspace quality gates, and is enumerated in the G0 amendment record if it lands
  after G0.

P28 may not:

- change solver physics, numerics or data — no new reaction model, no relaxation of a
  validation rule, no patch to the shipped artifact;
- qualify projectile families other than neutron (P25 floors failed for p/d/alpha —
  they remain unqualified and appear as recorded gaps);
- claim isomeric identity beyond liso {0,1} product states (P18/P18b stand FAIL);
- claim experimental validation — processing correctness and experimental prediction
  stay separated; every comparison against ALARA/FENDL is a data-evaluation effect,
  never a solver or physics verdict;
- claim that the finite-dilution approximation replaces geometry-dependent transport —
  transport remains an external input responsibility inherited by P32;
- qualify a narrower easy subset under a broad claim: if required coverage cannot be
  established for a frozen combination, it is recorded `gap` with its missing coverage
  counted, and downstream scope is revised accordingly;
- run more than one repair amendment; a second failure closes FAIL;
- modify a frozen gate record after its gate except by the declared amendment path.

## Frozen execution rules

- This protocol's SHA-256 is registered in `protocols/protocol_hash.txt` at opening; the
  G0 seal binds the hash, the opening commit and every prior verdict verbatim.
- The validation population freezes at G0 before construction: the selected study
  (P27 smoke: Fe, Fe+Co-100wppm; FNS-709 and IRDFF-709 spectra; 300 s pulse and 1 d
  continuous schedules; 0 s and 1 d cooling; the five qualified responses) plus the
  regime-boundary cases (temperature limits, dilution modes, flux-scale extremes,
  single-group spectra, artifact-coverage edge elements, non-neutron projectile and
  out-of-coverage element gap probes). Unsupported cases are counted, never dropped.
- The `p28_qualifying` partition seals at G0: population outputs measured under it are
  consumed once, by the verdict gate; iteration uses the diagnostic partition.
- All builds, tests and solver jobs run under the workstation cgroup rule with
  disk-backed temporary artifacts.
- Every measured number is published with hardware, input, cache and tool-identity
  context.
- The applicability map is exhaustive over the frozen regime axes: every combination is
  `qualified`, `gap` (named reason) or `excluded` (named phase/policy), and the counts
  reconcile to the Cartesian product size.

## Minimum gates (roadmap)

1. Independent source-to-rate traces: for the frozen parent set, collapsed one-group
   effective rates are recomputed independently from the artifact bytes
   (sigma_g x flux_g summed over the declared group structure) and compared to the
   solver's used rates recovered through single-group probe spectra; every traced
   parent/channel either matches within the frozen tolerance or is named.
2. Analytic limits: zero-flux production is exactly zero; a single-group spectrum
   produces exactly the single channel's sigma x phi rate; pure decay of a unit
   inventory follows the Bateman decay term; total production/loss accounting on an
   executed case closes within the frozen tolerance.
3. Independently processed reference: ALARA/FENDL-3.2c collapsed rates on the same
   spectra for the executable subset — reported as data-evaluation effects with the
   P26b accounting discipline (executability and contract_gap counted per case).
4. Complete inventory/response comparisons: every ordinary and boundary case of the
   frozen population executes or records a named gap; per-case response metrics are
   independently re-formed from raw artifacts.
5. Family applicability and ledger categories updated across all interfaces.

## Gates

### G0 — opening seal and scope freeze

Publish `results/g0_p28_seals.json` and `results/g0_p28_check.json`: protocol hash,
opening commit, prior verdicts verbatim, the `actinv` binary identity at the opening
commit, the shipped artifact + index + decay pins, the ALARA pin and corpus pins from
P26b, the frozen selected-regime axes, the frozen validation population, the tolerance
set and the sealed `p28_qualifying` partition. Independent checker verifies identity
resolution and rejects planted mutations.

### G1 — source-to-rate traces and analytic limits

Publish `results/g1_p28_rates.json`: independent collapse recomputation vs
single-group probe-spectra recovery for the frozen parent set, and the analytic-limit
results (zero flux, single group, pure decay, production/loss closure). Independent
checker re-derives a sample of traces from raw artifact bytes and rejects planted
mutations.

### G2 — regime population execution and applicability map

Publish `results/g2_p28_population.json` and `docs/APPLICABILITY.md`: the frozen
population executed through the P27 study machinery (ordinary + boundary cases,
qualified/gap/excluded counted), the independent ALARA reference on the executable
subset, and the complete applicability map reconciling to the Cartesian product.
Independent checker re-forms response metrics from raw artifacts and rejects planted
mutations.

### G3 — verdict and closure

Publish `results/verdict_p28.json` and `controls/check_g4_p28.py`: prior verdicts
verbatim, gate ancestry, population accounting, applicability-map completeness, and the
verdict string re-derived under the closure rule — PASS only if every frozen gate holds
with all minimum-gate controls executed; CONDITIONAL if a repair amendment was used or
a frozen combination is gap-recorded with unopened downstream scope revised; FAIL
otherwise.
