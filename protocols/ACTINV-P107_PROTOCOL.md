# ACTINV P107 — activity-box seal replay successor

Opened 2026-10-03 after P106 closed FAIL. The owner requested all open roadmap
work, with waste first, and authorized pushes. This phase carries forward the
entire declared-activity-box product contract in
`protocols/ACTINV-P106_PROTOCOL.md` and its exact-boundary construction repair in
`protocols/ACTINV-P106_AMENDMENT_A.md`. Neither historical document nor its
evidence may be rewritten.

## Failure and narrow correction

P106's first G0 attempt failed before sealing because `frac` forwarded the
wrong applicability selector. Amendment A consumed its sole repair. The
amended seal passed, but mandatory read-only G0 replay then failed before any
CLI case: the independently derived expected label records contained Python
tuples while persisted JSON contained arrays. The checker compared these
different in-memory representations directly. Retain the complete failed log
and parsed report, and derive P106-FAIL. G1/G2 were not executed. The Rust
workspace run passed 442 tests with two ignored; that is implementation evidence,
not a P106 class-control pass.

Create a new `controls/check_p107.py` and verdict checker. Before comparing the
stable G0 identity, convert its report to the same JSON-compatible representation
used on disk. Compare the whole stable report before adding replay diagnostics.
Add a regression that exercises an actual persisted seal round trip and rejects
a changed stable field. P106's frozen checker/helper/tests stay unchanged.

No production Rust, rule values, classifications, acceptance tolerances,
denominators, vector population or declared-bounds semantics change. P107 may
reuse P106's frozen interval helper and its independent P105 point oracle.

## Inherited scientific contract and fixed population

The authoritative inherited contract is P106 protocol SHA-256
`163a7a265363583c42b8d27a28ec11664b87d393a0b2565fcc76ec3904d606ee`,
with Amendment A SHA-256
`a9b8568fb5e7fde35b8325b525646fbc441e6989c4292fa6ef8735ce84c17f97`.
The bundled pack remains `us-nrc-10cfr61.55-v1`, SHA-256
`890268af81a53815b8e11c238e4a1694eabb79664a1ca087fd7fe22b9fabe5d7`.
The 126 inherited P103 vectors remain byte-for-byte frozen, SHA-256
`bfefb655b2df52da7ccb7a93cfd7c22bdc18762917e900e828ebd97d58b2bb42`.
The P106 generated artificial case fixture remains frozen, SHA-256
`6041be8d54015e604a6aec0578d05fcc6f1cf4aed82322cb03459d108d293ff4`.

There are exactly 146 components, 150 target records, and 300 endpoints: all
126 point cases represented as zero-width whole-component total-Bq intervals,
plus the same 20 named artificial interval/coverage cases and mathematical
inputs in P106. Derive the expected 20 labels independently and require their
exact equality. Do not add nuclear data or claim predictive validation.

The CLI remains `actinv waste bounds BOUNDS.json [OUT.json]`, input/output
schemas `actinv-waste-bounds-spec-1` / `actinv-waste-bounds-result-1`, method
`declared_activity_box`. Fixed geometry, waste type and nuclide properties;
simultaneous nonnegative rectangular absolute-Bq intervals at each target;
explicit complete/incomplete inventory and nonempty source/assumptions; separate
required/not-applicable/bounded external H-3 excluding activation; strict
duplicate/alias/refusal rules; and validate-all-before-write remain mandatory.

Lower and upper point arithmetic preserves P105's inclusive single-contributor
and strict multi-contributor predicates. Endpoint-specific contributor counts
use positive lower and positive upper activities respectively, without epsilon.
Keep each table/column separate, all six constraints and signed margins, aligned
row ranges (zero for absent endpoint rows, null for null limits), contributors,
bindings and unlisted inventory. Numeric comparisons use relative `1e-6` /
absolute `1e-12`; labels at exact boundaries never use a tolerance.

Complete endpoint classes bound an ordered conservative superset of A/B/C/
above-C; intervening classes need not be attainable. Agreement qualifies only
stability within a physically valid caller-declared box. Incomplete inventory,
required H-3 or any absent upper-positive property forces both endpoint
regulatory classes unknown while preserving known-subset calculated-only
arithmetic, explicit reasons and unknown names. Never infer physical coverage
from an empty map. This phase supplies no confidence level, covariance,
automatic uncertainty propagation, composition solver, uncertainty budget,
draft intrusion screen, full BTP interpretation or disposal acceptance.

## Gates and failure discipline

- G0: register this protocol before successor edits/evidence. Bind the terminal
  P106 failure, P105 terminal PASS, the frozen P106 protocol/amendment, pack,
  inherited vectors and case fixture. Seal successor checker/regression and
  unchanged inherited control/helper/test hashes before production CLI cases.
  Run the persisted-identity regression and require full read-only seal replay.
  The generated population and 20 independent labels must match frozen inputs.
- G1: all 146 artificial component cases round-trip through the freshly built
  CLI and compare independently, including activity/concentration conversion,
  every endpoint row/constraint/class, class envelope, stability, geometry,
  source/assumptions, coverage, properties, target identity and external-H-3
  declarations. Reuse every inherited planted mutation and refusal. All plants
  must pass the independent report validator and be rejected; refusals must
  leave preexisting output bytes intact. Repeat at separate output paths and
  require byte identity. Persist the complete result and replay before adding
  diagnostics.
- G2: bind the exact successful G1 artifact hash and byte-identical output result.
  Full read-only G0/G1/G2 replay must pass without rewriting frozen evidence.
- G3: serial enforced systemd scopes on this workstation: MemoryMax=6G,
  MemorySwapMax=0, TasksMax=128, CPUQuota=200%, CARGO_BUILD_JOBS=1,
  RUST_TEST_THREADS=1, RAYON_NUM_THREADS=2 and disk TMPDIR at
  `target/preflight-tmp`. Inspect limits read-only; never fall back unlimited.
  Run workspace fmt/check/clippy/test with all targets/features, inherited four
  child timeout/cancellation lifecycle tests, P106 helper regressions and the
  new seal regression, handbook build/link/Chromium checks. Review spawning;
  waits are bounded with termination/kill/reaping. No test current_exe launch.
  Individual jobs have at most 20-minute timeouts; controls at most five minutes.
  Coordinator alone executes one job at a time; Luna agents may inspect/edit.
  Reuse the unchanged release binary built for P106; rebuild if production code
  changes. Existing Rust evidence may be retained with exact source binding,
  but required completion checks must cover the final source.
- CI: stage code before indexed manifest refresh, check fmt and test compilation
  locally, push a plain owner-authored commit, and verify every scheduled
  workflow on the exact SHA completed successfully. Preserve the API record.
  Do not start later feature work while any pushed check is red.

The verdict checker derives P107-LOCAL-PASS only after every local gate; final
P107-PASS additionally requires all scheduled implementation workflows green,
including controls, desktop builds, handbook and browser workbench. Retain
P103/P104/P106 FAIL and P105 PASS. One repair round is available; preserve any
failure and freeze an amendment before repair. A further failure closes FAIL
and requires a new protocol. Close with session, append-only ledger, manifest,
push and verified CI. All wider roadmap conditions stay visible.
