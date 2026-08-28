# ACTINV P14 — performance anatomy and safe quick wins

Opened 2026-08-28 after CB1 closure at source commit
`d7f934dad677f128395443d10a57444c7b213472`. The maintainer prioritizes a smooth, resource-efficient product and
approved the P14--P22 post-v1 roadmap. P14 is the first and only open phase.

P14 measures the released end-to-end path and removes only demonstrated redundant work that can be changed without a
new nuclear-data representation, selective library loading, a prepared on-disk cache, a physics change, a numerical
tolerance change, or a public API/schema change. Those larger data-path changes remain P15. No result from P14 may be
used to alter evaluated data or tune the activation model.

## Frozen inputs and baseline

The production workload is `examples/fns_fe_5min.json` after substituting the exact local paths from the released
`data-v1.0.0` neutron bundle. Its identities remain:

- TENDL-2025 neutron activation NPZ:
  `ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44`;
- activation index: `8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb`;
- ENDF/B-VIII.0 decay payload:
  `6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb`;
- JEFF-3.3 fallback decay payload:
  `850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123`.

CB1 measured the warm-file-cache public example at `2.174 s` median, `2.304 s` p95 and `1,090,961,408` peak RSS bytes
on the recorded Intel Core i3-N305 host while hashing/parsing `237,928,911` input bytes. These values are historical
context, not silently reused as P14 measurements. P14 reruns the unchanged opening source and candidate binaries in
the same interleaved control on the same host.

The minimum development fixture is a compact generated activation/decay case from existing controls. It settles
instrumentation and semantic-identity gates without production data. The production example is run only after the
minimum fixture passes. Scalar measurements set `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS`,
`NUMEXPR_NUM_THREADS` and `RAYON_NUM_THREADS` to one. No P14 job is expected to exceed ten minutes; repetitions are
profiled and reduced before execution if that estimate is exceeded.

## Normative boundaries

1. P14 may add opt-in or control-only stage instrumentation. Ordinary result JSON and standard stdout/stderr remain
   compatible and do not expose host-dependent stage records.
2. P14 may remove redundant allocations, copies, scans, formatting or recomputation only after the frozen baseline
   attributes that work. It may not change iteration order merely for speed when doing so changes a scientific value.
3. Activation-library/decay formats, cache policy, reachable-target selection, memory mapping and persisted prepared
   data are P15 scope. P14 records evidence and a design input for P15 but does not implement them.
4. The candidate's normalized result must equal the opening result recursively after removing only `ms`, entry-point
   labels, explicit source/output paths and software-build identity fields already normalized by the frozen P12
   physics control. Certificate input hashes, library/decay inventories, ledger categories, state counts, steps,
   activities, heat, pathways, uncertainties and radiological values are never normalized away.
5. Bulk nuclear data, profilers' raw memory dumps and generated libraries remain outside Git. Compact stage samples,
   statistics, source identities, controls and reports may be committed.
6. No `unsafe`, new runtime dependency, concurrency architecture, public wire schema or solver/data-model change is
   authorized by this protocol.

## Gates

### G1 — reproducible opening baseline

An independent Python control verifies the four production input hashes, opening source commit, compiler and host
metadata, thread environment and executable hash. It measures opening and candidate binaries in alternating order
after five untimed warm-ups each. There are at least fifteen measured processes per binary; the control records raw
wall samples, minimum, median, p95, mean, sample deviation and one GNU-time peak-RSS process. It also records input and
output byte counts. A hash mismatch or changed workload fails closed.

### G2 — stage attribution

The Rust path exposes control-only timing checkpoints sufficient to distinguish at least input verification, activation
read/validation, index read/validation, decay read/parse/merge, chain construction, material/network preparation,
schedule solve/diagnostics and serialization. Stage names and boundaries are documented. At least fifteen instrumented
production runs report finite nonnegative values; the median accounted stages plus explicitly named uninstrumented
remainder reconcile to process wall time within 10%. Instrumentation-disabled output remains compatible.

Allocation/peak attribution may use OS counters and an external profiler rather than a new allocator. The report must
identify the dominant time stage and the live data structures that explain the peak; estimates are labeled as such.

### G3 — unchanged behavior and provenance

The opening and candidate binaries run the compact fixture and production example. The independent recursive control
applies only the frozen normalization boundary above and requires exact equality. It plants changes in a certificate
input hash, one inventory value and one ledger value and proves that each fails comparison. Prior v1.0/CB1 physics,
legacy projectile, public-example and certificate controls remain green.

### G4 — measured safe improvement

Every accepted source change names the G2 cost it removes. On the same interleaved sample set, the candidate must
reduce either warm median wall time or peak RSS by at least 10% relative to the opening binary. The other primary
metric may not regress by more than 5%; candidate p95 may not regress by more than 5%. Ratios and absolute differences
are reported without a cross-product performance claim. If this gate cannot be met without crossing a normative
boundary, P14 closes honestly without widening its scope and P15 retains the larger work.

### G5 — quality and close

`cargo fmt --all -- --check`, workspace/all-target/all-feature `cargo check`, Clippy with warnings denied, and the
complete workspace tests pass. The Python binding Rust gates, prior verdict audit, release-note audit, dependency
audit, public examples, self-contained clone and data-independent CI end-to-end controls also pass. A checker rehashes
the protocol and every committed P14 evidence file, rederives statistics/ratios/semantic comparisons and emits the
verdict. The session records the source/evidence commit and successful GitHub workflow. The repository manifest is
regenerated once at closure and a clean rerun leaves the tree unchanged.

## Closure interpretation

P14 passes only if G1--G5 pass. A measured improvement is a claim about the named host, workload, cache state and
outputs, not proof that ACTINV is faster than FISPACT or another unmatched product. Any selective-loading or prepared
format design discovered here is appended to `docs/PARKING.md` for P15 rather than implemented under P14.
