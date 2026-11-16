# Correctness and validation audit: first pass

Base reviewed: `0b19b79d4d07fcd79586690d823682b17382a356` (1.7.2).
This is an internal code/coverage review and synthetic regression expansion,
not a new scientific qualification or a reinterpretation of historical verdicts.
Execution outcomes live in the verification runner's reports; a registered
experiment or a historical evidence record is not a fresh passed check.

## Scope and conclusions

Reviewed the sparse LU/refinement and CRAM recurrences, flux-collapse contracts
and front-door group checks, ENDF fixed-width handling, and GUI worker launch and
cancellation paths. Inspected the unfolding input/local-model boundaries and
catalogued the newer browser-pack and maintenance qualification authorities.
The full unfolding, transport, covariance, pack-deepening and maintenance
algorithms have not received a complete line-by-line review in this pass.

A spectrum-normalization defect was reproduced through a validated specification:
two finite weights of 1e308 with a declared total of 2 became two zero fluxes.
The initial independent extreme-value regression failed while the ordinary-case
binary-compatibility control passed. A rescaled fallback addresses overflow,
underflow and loss of precision in normalization intermediates. Solver algorithms,
data, tolerances and published qualification records remain unchanged. The
ordinary normalization path retains its original arithmetic.

## Findings

| ID | Priority / disposition | Evidence and action |
|---|---|---|
| A1 | High integration risk, addressed | The original verification worktree was about 250 commits behind current master. The newer worker has calculation, impurity-screen and job entry points. Integration preserves all three and puts the compile-time test-launch exclusion at their shared `spawn_input` boundary. The lockfile now follows the current 1.7.2 production graph. Earlier local results alone are not treated as testing this integration. |
| A2 | Coverage gap, addressed | The original generated LU cases were diagonally dominant in their supplied row order. New cases cyclically permute rows, assert that the first pivot actually moves, and apply a uniform binary scale from 2^-400 through 2^400. Row permutation and uniform scaling preserve conditioning; these are not tests demanding impossible accuracy from arbitrary ill-conditioned matrices. An independent dense product forms the RHS from a known complex solution. |
| A3 | Coverage gap, addressed | The original generated decay oracle covered one parent and one stable daughter. New cases cover two stable daughter branches, all six state permutations, two independent RHS columns, and the derivative with respect to log decay rate. Scalar, shared-factorization and tangent paths are each compared with analytic exp/expm1 expressions, rather than validated against one another. |
| A4 | Low-level contract gap, open | `cram::step` and `step_multi` zip public coefficient arrays without validating their lengths; an empty pair skips the recurrence. Raw CSC construction/solve and one-group helpers also assume valid shapes. Normal run preparation uses embedded matched CRAM tables and checks spectrum/library group counts, so this is not evidence of a normal CLI calculation returning a wrong result. Public Rust API hardening should document these preconditions or consistently reject malformed structures; it needs a separately scoped change and focused refusal regressions. |
| A5 | Integration coverage gap, open | Loom exercises the shared polling policy, and a process test terminates/reaps a separately built OS child. These do not establish end-to-end behavior of every GUI job under pipe backpressure, receiver drop, stale-result delivery or external filesystem stalls. Add bounded integration tests using a separately built application before claiming full worker-lifecycle coverage. |
| A6 | Proof coverage limit, open | The sparse 2x2 pattern claim and full-ASCII field-boundary claim have tractable harnesses. ENDF rejection experiments remain unproved after bounded timeouts. Ordinary tests reject all 66 short prefixes for each generated ASCII record and exercise all shared non-ASCII fixtures, including an accepted ASCII control with valid numeric tail fields. They are not relabelled as completed Kani proofs. |
| A7 | Medium correctness defect, repaired | `Spectrum::ascending_flux` used total/sum(weights), so an overflowing sum produced a zero multiplier, and tiny shapes could produce an infinite multiplier despite finite desired groups. A subnormal multiplier can also lose material relative precision. The fallback rescales the shape by its largest weight and uses a normal common multiplier where possible to retain representable traces. Independent equal/half-weight cases cover large and tiny shapes, zero groups, 709 groups, finite traces and tiny/large requested totals; a separate control checks ordinary-case bits. Generated equal-shape checks draw from every positive finite binary64 encoding and a wide range of requested totals. This is an input-arithmetic regression repair, not a new inventory-accuracy qualification. |

The earlier review also corrected two expectation gaps: the sparse assertions
now require dimensions and exact entry retention, and the non-ASCII tail control
has a complete valid tail rather than being rejected simply for truncation.

## Coverage map and next work

| Path | Independent check / existing authority | Remaining audit work |
|---|---|---|
| Quantity domains | Binary64-domain Kani constructors and conversion proof | Derived-operation overflow/refusal contracts |
| Sparse assembly and LU | Dense integer accumulation, known complex solutions, pivoting/scaling generation | Near-singular and cancellation-heavy families with specified accuracy/refusal expectations; malformed public API inputs |
| CRAM scalar, multi-RHS, tangents | Closed-form parent/branch/log-rate derivatives; existing P11/P17/P149 controls | Larger stiff branching/feed/removal combinations and reference precision checks under a registered scope |
| Flux and group collapse | Integer weighted-average oracle; `run.rs` checks initial and per-step library group counts | Rebin/interpolation/threshold/isomer semantics across combinations of supported options |
| ENDF / NPZ / specification input | Generated bounds and structured fuzz targets; existing P12/P94/P100 controls | Semantic activation/decay/covariance readers and cache/HDF5 paths; parser rejection proofs remain experimental |
| Uncertainty and unfolding | Existing P43/P55/P93/P195A authorities; inspected P195 input/covariance/local-model boundaries | Independent adversarial covariance and model-misspecification review; public dense-helper shape contracts |
| Browser pack execution | P196 failure and P196A successor evidence preserved | Trace-observable and option-combination review beyond the sealed populations |
| Maintenance planning | P197 closed-form and computed-component evidence | Broader model/geometry assumptions and integration review; no new qualification claimed here |
| Worker execution | Production polling policy, deterministic/Loom cases, child termination/reaping | Full-application lifecycle and pipe-failure tests (A5) |

Next, resolve the API contract choices in A4, then audit scientific input
semantics and difficult numerical cases against independent references. Include
negative controls that demonstrably exercise the intended refusal. Keep existing
qualified/conditional/failed scopes distinct. A whole-corpus E2 census remains a
separate protocol and does not substitute for these contract and semantic checks.

Before making additional suites required in CI, obtain a measured hosted run of
the manual verification workflow. Preserve explicit runtime budgets and the
distinction between routine passed proofs and inconclusive experiments.

## Local validation

On the current-master integration, application and verification formatting,
workspace check/clippy/tests with all targets and features, and verification
clippy passed. The extended property run passed 24,576 generated cases across
12 properties, plus deterministic parser/worker/seed controls. The independent
normalization regressions and ordinary-case compatibility controls passed.
The 19 default Kani harnesses and two Loom tests also passed on this integration;
the normalization repair does not change their included kernel sources.
The shipped-example control passed with a freshly built CLI, together with
15 preflight and 10 verification-runner safety/registration regressions.

Earlier bounded fuzzing, mutation and Miri runs are documented in
`VERIFICATION.md`; they are not fresh runs against every newer feature on this
base. In particular, the timed-out ENDF rejection experiments remain inconclusive.
This validation does not expand any scientific qualification scope.
