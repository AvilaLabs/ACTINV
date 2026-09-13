# ACTINV competitive research and proposed extension

Research date: 2026-09-13. Status: advisory research and an unfrozen proposal. The canonical scope remains
[ROADMAP.md](ROADMAP.md). This document opens no phase and changes no frozen protocol or prior evidence.
Local observations began at commit `77dfaef8c1cb4067c8cc8e1b7c4a686123161ea7`; P25 development was active in the
shared checkout. Its future results are not assumed here. The planning assumption is balanced emphasis on scientific
confidence, campaign performance and analyst workflow.

**Recommendation.** Plan another extension, with explicit competitive outcomes. Complete P25 and the required
P24/release qualification on their own terms. Use the research below to design a successor around a small set of
complete studies that ACTINV should handle exceptionally well. Research and proposal preparation can proceed during
the repairs; production phase execution must retain the repository's one-open-phase discipline.

The proposed product promise is: **an open activation environment in which an analyst can run a substantial study,
understand what controls the answer, and give a colleague a reproducible explanation of the result.** This is an
aspiration to test. Current evidence does not establish overall superiority over FISPACT-II, SCALE, OpenMC or ALARA.

**Evidence conventions.** External capabilities below are documented by their developers, not newly executed in
this review. ACTINV measurements are identified as historical repository evidence. Proposed advantages and priorities
are planning judgments. Missing documentation means unverified, never absent. Software capabilities, supplied-data
coverage and experimentally demonstrated accuracy are separate questions. No builds, executable tests, benchmarks,
solver jobs or competitor installations were run for this research.

**What the competitors actually establish**

| Comparator and documentary baseline | Relevant documented capability | Consequence for ACTINV's strategy |
|---|---|---|
| FISPACT-II; official release history lists 5.1 | UKAEA documents activation and transmutation, damage responses, self-shielding, temperature treatment, pathways, an API, covariance propagation and Monte Carlo sensitivity/uncertainty. | Adding these labels cannot establish a lead. Compare the supported combinations, data coverage and user effort. [UKAEA overview](https://www.ukaea.org/service/fispact/), [release history](https://fispact.github.io/#release-history) |
| FISPACT-II data preparation | Official documentation describes compressed binary nuclear data, reuse of a collapse for a common spectrum, repeated collapses without rereading cross sections, and reduced nuclide indexes, including pathway-directed selection. | Prepared data and reduced networks are valuable but have established counterparts. Any campaign comparison must enable appropriate competitor preparation and amortize its cost fairly. [Data I/O documentation](https://fispact.ukaea.uk/wiki/Nuclear_data_I/O_optimisation) |
| SCALE 6.3.3 / ORIGEN | ORIGEN documents feed/removal, binary transition libraries, restartable inventory records and adjoint sensitivities for reaction, decay, yield and branching data. | A fast sparse solver, sensitivities and restart are comparison baselines. Standalone ORIGEN and the wider SCALE workflow must be distinguished. [ORIGEN theory](https://scale-manual.ornl.gov/6.3.3/origen/origen-theory.html) |
| SCALE 6.3.3 / Sampler | Sampler treats multigroup nuclear data, self-shielding data, depletion data and model inputs, including composition and temperature. It includes consistent shielding perturbations, parameter studies and reaction-importance ranking. Its documented nuclear-data sampling has restrictions, including exclusion of CE Monte Carlo cross-section sampling. | Uncertainty spanning multiple model components and identifying influential reactions already exists in a competitor suite. ACTINV must qualify its own combinations and measure usability or cost advantages. [Sampler](https://scale-manual.ornl.gov/6.3.3/sampler.html) |
| SCALE 6.3.3 / ORIGAMI | ORIGAMI distributes spatial depletion cases through MPI and consolidates inventories. | A spatial capability comparison cannot classify the entire SCALE suite by standalone ORIGEN's zero-dimensional equations. Cluster breadth is not an automatic ACTINV priority. [ORIGAMI parallel execution](https://scale-manual.ornl.gov/6.3.3/origen/origami.html#parallel-execution-on-linux-clusters) |
| OpenMC; current stable documentation includes 0.16.0, while CB1 exercised 0.15.3 | `IndependentOperator` runs depletion from supplied multigroup fluxes and cross sections. The guide documents transfers between materials and constant external feed/removal. The fixed microscopic cross sections in the independent operator impose an applicability limit. | OpenMC is a direct fixed-spectrum inventory comparator as well as a transport partner. Pin an actual release, chain and normalization before new measurements. [IndependentOperator](https://docs.openmc.org/en/stable/pythonapi/generated/openmc.deplete.IndependentOperator.html), [depletion guide](https://docs.openmc.org/en/stable/usersguide/depletion.html), [0.16.0 release](https://github.com/openmc-dev/openmc/releases/tag/v0.16.0) |
| OpenMC R2S | `R2SManager` manages mesh/cell activation regions and decay-photon sources across neutron transport, activation and photon transport. Version 0.16.0 adds multiple-mesh and decay-spectrum improvements. | Convenient open R2S is already an active competitor capability. ACTINV needs to demonstrate a specific benefit and should reuse transport integrations where appropriate. [R2SManager](https://docs.openmc.org/en/stable/pythonapi/generated/openmc.deplete.R2SManager.html), [0.16.0 notes](https://docs.openmc.org/en/stable/releasenotes/0.16.0.html) |
| ALARA; historical CB1 executable 2.9.2, developer guide last updated 2019 | The guide documents spatial activation, hierarchical pulse histories, activation trees, clearance/waste outputs and reverse mode. Its input reference specifies target isotopes for reverse calculations. | Retain ALARA as an accessible activation comparator. Do not assume its reverse mode is equivalent to ACTINV's flux-estimation inverse problem. Performance superlatives in its guide are developer claims, not results of this review. [Features](https://svalinn.github.io/ALARA/usersguide/introtext.html), [input reference](https://svalinn.github.io/ALARA/usersguide/inputtext.html) |
| PyNE / ALARA / DAGMC-MCNP R2S ecosystem; PyNE 0.7.1 documentation | The guide describes CAD/material discretization, Cartesian and tetrahedral activation meshes, photon-source sampling and a complete transport/activation sequence; it cites an FNG validation. The documented route requires its external tools, including MCNP. | Compare usable workflows and their dependencies. Open orchestration does not imply unrestricted access to every executable. A point-source export does not supply equivalent spatial functionality. [PyNE R2S](https://pyne.io/usersguide/r2s.html) |

Some legacy FISPACT wiki/manual URLs redirect to a general UKAEA page. The indexed I/O documentation supports the
specific preparation claims above, but executable comparison must pin the corresponding shipped manual and options.
Current documentation is a discovery baseline, not a frozen executable specification. ALARA's older documentation
also requires reconciliation with its chosen source revision. No competitor's run-time provenance, diagnostics or
uncertainty are declared inferior because their equivalence was not established here.

This research concentrates on the four existing named competitors and the R2S ecosystem. It is not an exhaustive
survey of every depletion/transport code. Serpent, other transport suites and specialist activation workflows can be
added if a selected use case makes them a necessary comparator.

**Where ACTINV stands**

The strong foundations are real: an open shared CLI/Python/desktop core, verified data setup, explicit omissions,
independent controls and a measured efficient prepared path. P15 recorded 2.595x lower warm wall time and 8.326x lower
peak RSS against its opening implementation, at identical normalized results. Those are improvements over ACTINV's
own baseline, not comparative victories over a currently optimized competitor.
[P15 evidence](../results/p15_performance.json)

The present scorecard needs an additive update. P22's process-performance leg deliberately uses a rebuilt v1.0.0 CLI
to satisfy CB1's version guard; its Python kernel leg uses the candidate. Consequently, its approximately 1.1 GB CLI
example is not a measurement of the current prepared path. Preserve the old report and add a current-candidate
comparison with unambiguous software identities. [P22 implementation](../controls/g2_p22_exercises.py)

| Complete user need | Local evidence and remaining gap |
|---|---|
| Shielded activation with useful uncertainty | The specification and production validation reject `self_shielding` with `uncertainty`. Current shielding is limited to the documented treatment; uncertainty omits flux, composition and other named sources. [Specification](SPEC.md), [validation](../crates/actinv-core/src/spec.rs) |
| A practical uncertainty campaign | P20 provides additional local uncertainty channels, but its correlated sampling remains a verification control. Integration, sampling applicability and user-facing campaign execution need their own qualification. [P20 protocol](../protocols/ACTINV-P20_PROTOCOL.md) |
| Efficient, distinct spatial cases | P21 executed 20,000 distinct spectra with bounded measured memory. It does not establish faster equivalent-workload execution than the alternatives. Exact-repeat memoization alone cannot supply the next gain. [P21 protocol](../protocols/ACTINV-P21_PROTOCOL.md), [execution evidence](../results/g3_p21_executed.json) |
| Complete spatial photon-source handoff | Ordinary exporters use a point at the origin; the qualification document assigns distributed source construction to the user. Verify current adapter code before freezing a repair. [Specification](SPEC.md), [qualification boundary](QUALIFICATION.md) |
| Qualified accuracy across the intended domain | P18b failed and its consumed evidence is diagnostic for P25. Successful processing repairs must not automatically become claims of better predictions. Fresh measurement qualification and coverage accounting remain separate. [P25 protocol](../protocols/ACTINV-P25_PROTOCOL.md) |

**Proposed extension: six decision stages**

These are candidate stages, not allocated phase numbers or implementation commitments. Number and freeze them in
ROADMAP.md only after the opening feasibility work establishes the real workload and interfaces. Every stage should
state the user outcome, comparison baseline, independent evidence, dependencies and a stop condition. An implementation
pass and a competitive-lead verdict must be reported separately.

**E0 — establish the competitive contract.** Prepare a new scorecard on the repaired candidate and current selected
competitor versions. Before candidate optimization, define three representative studies: (A) a resonance-sensitive
activation study with uncertainty; (B) repeated material/history variants and distinct spatial spectra; (C) transport
flux to spatial photon source to reproduced study. Proposed examples include impurity-sensitive alloys and tungsten
activation; these are workload choices to validate, not assertions about demand. Record supported materials,
energies, schedules, responses and data coverage. Keep measurement values sealed where future blind qualification
requires it.

Opening deliverables are executable feasibility, an outcome-level capability matrix, a comparison harness plan,
baseline identities and stage estimates. Choose cases before inspecting competitive results. FISPACT/SCALE execution
can come from a lawful collaborator; it is not a dependency for development. Where access is unavailable, mark
performance and agreement unmeasured and restrict eventual claims to the named executed comparator set. A documented
capability can set a design requirement without supplying comparative timing evidence.

**E1 — qualify the physics combinations needed by the selected studies.** Determine which resolved/unresolved
shielding and rate-processing regimes the studies actually need. Expand treatment only where existing support is
insufficient. Establish state identity, production/loss consistency and data applicability through source-level
controls, independent processing and physically appropriate limits. A finite-dilution treatment must not be presented
as a substitute for missing geometry-dependent transport.

Exit requires a documented coverage map and independent rate/observable agreement for the whole chosen regime,
including its boundaries. Preserve failures and identify unsupported cases before scoring. Stop or narrow the
proposed domain if suitable inputs or independent controls are unavailable; do not broaden tolerances to compensate.
This stage earns scientific capability, not an accuracy-superiority claim by itself.

**E2 — deliver uncertainty that works with the qualified physics.** Build a user-facing campaign path that combines
the E1 treatment with declared nuclear-data and selected input uncertainties. Start with the inputs material to the
chosen studies, potentially flux normalization and impurity composition, and explicitly preserve their correlations
and constraints. Compare local sensitivities with converged nonlinear sampling. If efficient adjoints are warranted
by measured cost, treat them as an implementation option with independent controls; ORIGEN already documents that
method, so it is not a novelty claim.

Shielding must respond consistently to perturbed cross sections/composition where the selected model requires it.
Simply attaching an unshielded covariance band to a shielded mean is not qualification. Every reported interval must
name its included uncertainties, approximation and uncovered remainder. A spread across evaluations is sensitivity
to data choice unless a statistical interpretation is independently justified. Keep all failed samples accounted.
Require constraint preservation, sampling convergence, calibrated synthetic controls, and experimental checks where
independent evidence supports them. Freeze tolerances and missing-data handling before acceptance scoring.

**E3 — make the complete campaigns materially cheaper.** Optimize the measured bottlenecks in E0/E2, including
preparation, collapse, network construction, sensitivities, solve and output. Exercise both repeated and distinct
spectra/compositions; enable appropriate competitor caches and reusable APIs. Any approximate reuse needs explicit
error control against exact calculations and visible provenance. Preserve exact paths when approximation is
unnecessary. Multi-node execution is optional only if the selected workloads justify its complexity.

Draft ambition: at least 3x lower end-to-end wall time on two independently selected campaign workloads against the
fastest accessible equivalent-output comparator, at matched resource limits and qualified accuracy. This is an
unfrozen target to assess during E0, not an observed result. A plausible scale pair is 1,000 material/history variants
and the existing 20,000 distinct-spectrum case, with output requirements fixed first. Freeze numerical tolerances,
memory budgets and success thresholds after feasibility but before implementation optimization. If the target is
missed, publish the result and decide whether the gain merits shipping without a leadership claim.

**E4 — deliver a reproducible study and spatial handoff.** Package specifications, data identities, method choices,
results, applicability notes and verification instructions into a relocatable study. Resolve licensed or bulky data
through lawful references rather than assuming redistribution. An intentional change of library, composition or
schedule should yield a traceable comparison of rates, inventories and responses, with interactions and unattributed
differences retained. Initial implementation may use explicit controlled reruns rather than promise automatic unique
causal attribution.

Qualify at least one complete open transport integration against OpenMC's native R2S path: spatial material mapping,
cell volumes, flux normalization, emitted photon rates and source sampling all require independent checks. Add MCNP
or other adapters only when there is a concrete user and lawful validation route. Do not add an internal transport
solver. Include a newcomer handoff exercise: reproduce a supplied study, diagnose a planted material/normalization
mistake and explain a changed result. Draft workflow ambition is at least 50% lower median hands-on completion time
than the chosen existing workflow with no increase in undetected errors; E0 must define tasks, participants,
order/carryover controls and evidence sufficiency. If participants are unavailable, report an internal demonstration
and leave the usability claim unqualified.

**E5 — independently decide what leads and what can ship.** Re-run the entire selected workflow battery on the exact
release candidate, with qualified data versions and compatible combinations enabled. Compare rate construction,
inventories and requested observables, not only a CRAM kernel. Keep independent implementation checks separate from
fresh predictive validation. An accuracy-superiority claim requires suitable independent measurements, adequate
family/stratum coverage and predeclared comparisons; it is not implied by tighter solver agreement or faster runs.
Report confidence/variation for noisy performance and user-study results. Repeat installation, study handoff and
resource checks for the release artifacts. Allow a useful qualified release with honestly missed competitive goals;
do not convert phase completion into a claim of leadership.

Suggested dependency order is E0, E1, E2, E3, E4, E5, with research refining E4's design during earlier stages. Retain
the existing phase execution discipline. E3's workloads must include E2's real costs. E4's final controls must include
the combinations users will actually run. Scope and estimates should be revisited at each unopened-stage boundary,
without modifying a frozen acceptance rule after seeing its result.

**Rules that prevent another gap between phase completion and product ambition**

1. Express every major gate as a complete analyst task; include feature interactions in the accepted configuration.
2. Separate numerical agreement, processing correctness, supplied-data coverage and experimental predictive accuracy.
3. Use both identical-data comparisons and product-plus-data comparisons, labeled separately. Different-library
   output disagreement alone cannot identify the solver responsible.
4. Freeze all denominators: eligible cases, family counts, failed constructions, zero predictions, failed samples
   and unavailable outputs. No comparative gain may come from silently losing difficult cases.
5. Record full work, preparation amortization, selected outputs, thread/resource limits, versions and hardware.
   Preserve CB1/P22 records and write additive successor evidence.
6. Keep the strongest existing competitor workflow in the comparison; document its configuration and dependencies.
   APIs, binary data, sensitivities, reverse modes and R2S are not equivalent merely because their labels match.
7. Require independent derivation of physical semantics as well as independent arithmetic. Two implementations can
   reproduce the same mistaken assumption.
8. Preserve consumed measurement sets as regression/diagnostic evidence. A new phase number cannot restore blindness.
9. Execute future local builds, tests and solver jobs only under the repository's enforced cgroup limits, one job at
   a time through the coordinating agent. Benchmarking must accommodate those limits, with other hardware recorded
   separately when needed. No unlimited million-cell experiment is implied by this proposal.

**What should remain demand-led**

Additional incident projectiles, an internal transport solver, general fuel-cycle flowsheets, elaborate distributed
infrastructure and broad nonlinear inverse modeling are not automatic scope. Neither is a user interface rewrite.
Promote one only when a selected study exposes it as the highest-value missing capability. A productive first
extension can improve confidence and cost within a well-qualified neutron-activation domain while preserving and
repairing existing charged-particle support.

The first concrete planning deliverable should be E0's short competitive contract, with a complete combination
matrix and three reproducible workload definitions. This research supports that investment. It does not yet support
a fixed completion date, a promise to outperform inaccessible executables, or a new public superiority claim.
