# ACTINV P16 — typed physical boundaries and metamorphic assurance

Opened 2026-08-28 after the public v1.0.1 release close at repository commit
`0624133d3daa5d8440497e06c3d372c8a546a0ed`. Exact GitHub Actions run `33220183178` passed the complete release
control suite, including a clean self-contained clone. P15 is closed `P15-PASS`; P16 is the first and only open
phase.

P16 makes dimensional mistakes harder to express inside the Rust core and broadens controls that need no
competitor executable or known answer. It does not change evaluated nuclear data, solver order, physics models,
numerical tolerances, pruning, public result schemas or certificate provenance. Existing convenient JSON, Python and
Rust wire interfaces remain source-compatible. The new types sit behind those boundaries and must be exercised by
the production path rather than existing only as examples.

## Frozen baseline, inputs and minimum fixtures

The semantic and performance opening baseline is the exact signed `v1.0.1` source commit
`0332779401363d2f39722efe7a0b7218afcfb270`, rebuilt locally with the same recorded compiler and release profile as
the candidate. The production workload remains `examples/fns_fe_5min.json` with the released `data-v1.0.0` paths
substituted. Its identities are:

- TENDL-2025 neutron activation NPZ:
  `ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44`;
- activation index: `8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb`;
- ENDF/B-VIII.0 decay payload:
  `6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb`; and
- JEFF-3.3 fallback decay payload:
  `850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123`.

The minimum scientific fixture is the existing generated one-group P9 activation/decay network. It is deliberately
synthetic, compact and analytically visible; it is not evidence about evaluated nuclear data. A generated two-group
flux fixture adds an exact-grid and a split-grid mesh path. Compile-pass and compile-fail consumer crates are generated
in a temporary directory and depend on the local `actinv-core`; no deliberately failing target enters the workspace.
No planned P16 job exceeds ten minutes. Longer work must first be reduced or profiled under the standing execution
rules.

## Normative dimensional boundary

1. Public serde wire structs retain their current raw field names and Rust types. In particular, `mass_g`,
   `temperature_K`, `bmin_atoms_per_g`, energy fields, spectrum values, schedule multipliers and duration strings
   remain compatible inputs. Existing `parse_duration`, `flux_ascending`, `schedule_seconds`, `PreparedRun` and
   top-level `run` call shapes remain valid.
2. The core introduces private-field, `#[repr(transparent)]` scalar types for the physical dimensions it actually
   crosses in this phase: seconds, electronvolts, kelvin, grams, atoms per gram, particle flux, dimensionless flux
   multiplier, cross section in barns, rate per barn-second and rate per second. A typed group-flux wrapper may own
   the already-required vector without another copy. Every scalar type has `size_of` and `align_of` equal to `f64`;
   no heap allocation or dynamic dispatch is hidden in a scalar.
3. Raw values enter through named checked constructors that enforce the existing finite/sign rules. There is no
   blanket `From<f64>`, `Into<f64>`, generic arithmetic across dimensions, implicit unit conversion or silent clamp.
   Only physically meaningful operations are implemented—for example particle flux times duration and multiplier,
   and barns times rate per barn-second.
4. A validated physical-input view is built at the spec/core boundary. The ordinary top-level path builds it once
   and shares it with preparation and execution. The separately callable prepared APIs retain their legacy raw
   wrappers and convert once before entering typed implementations. This small ownership change is permitted to avoid
   duplicate parsing, allocation or cloning; it does not authorize a concurrency or solver redesign.
5. The barn-to-square-centimetre factor `1e-24` is applied in exactly one named typed rate constructor. Collapsed
   cross section and flux cannot be multiplied as unrelated `f64` values in production reaction assembly. Scalar
   extraction for legacy kernels occurs only at documented adapter methods named in the boundary inventory.
6. `docs/QUANTITIES.md` is the normative inventory. Each row names the user-facing field or kernel value, unit,
   Rust type, sole raw-to-typed constructor site, permitted typed operation and any sole typed-to-scalar adapter.
   An independent control reconciles this table with source and rejects an undocumented conversion or bypass in the
   covered production paths.
7. P16 adds no `unsafe`, runtime dependency, interior mutability, `Arc`, `Mutex`, or clone/allocation workaround.
   Existing allocations may be moved behind a wrapper but not duplicated. A structural ownership change beyond the
   shared validated view requires a pre-evidence protocol amendment explaining the constraint.

## Frozen comparison boundary

Release and candidate JSON are compared recursively after removing only top-level `ms`. Entry-point labels are
normalized only for a comparison between different public entry points, following the frozen P12 rule. Inventories,
activities, heat, photons, dose, pathways, uncertainties, radiological results, modes, state counts, every ledger
value and all certificate inputs remain compared. A new internal type name, conversion record or host detail does not
enter ordinary result JSON. Existing accepted inputs remain accepted and existing rejected unit/sign/nonfinite inputs
remain rejected with the same field context.

## Frozen metamorphic relations

All comparisons use binary64 values from the production Rust path unless explicitly identified as independent Python
arithmetic. Relative error is `abs(a-b)/max(abs(a),abs(b),1e-300)`.

1. **Scaling and fluence.** In trace mode, multiplying physical source flux by `2` and `0.5` scales activation-fed
   inventory, activity and heat by the same factor within `5e-12` wherever the reference is nonzero. Stable-product
   runs with reciprocal duration/multiplier changes at fixed fluence agree within `5e-12`. Per-gram inventory,
   activity and heat are bit-identical when only material grams change.
2. **Analytic decay.** A pure Mn-56 decay fixture at one, two and three synthetic half-lives agrees with
   `N(t)=N0 exp(-lambda t)` and its single daughter balance within `5e-11`, with total parent-plus-daughter atoms
   conserved within `5e-12`.
3. **Schedule splitting.** One 300-second constant segment and the partitions `100+200` and `100+100+100` have final
   parent/product inventories equal within `5e-11` for decay-only, trace-source and coupled-depletion fixtures.
   Intermediate output rows may differ because the requested observation schedule differs.
4. **Rebinning.** An exact energy grid copies flux bits exactly. Splitting source groups and rebinning to the original
   grid accounts for source total through destination, underflow and overflow to relative closure `1e-12`; scaling
   all source bins by `2` scales every accounted component within `5e-15`.
5. **Mode limit.** At burn-up optical depth `1e-8`, forced trace and coupled stable-product results differ by the
   analytic first-order depletion term. Their measured relative difference agrees with
   `1-(1-exp(-tau))/tau` to relative `0.25`, the coupled parent agrees with `N0 exp(-tau)` within `1e-10`, and the
   absolute product difference remains below the reported numerical floor where that floor dominates.
6. **Mesh identity.** Exact-grid and split-grid cells equal independently run ordinary cells under the frozen JSON
   normalization. One-thread and four-thread cell bytes are identical, and repeated identical cells scale footer
   totals linearly within `5e-12` without changing a cell result.
7. **Unit spelling.** `300 s`, `5 min`, `0.08333333333333333 h` and their accepted compact spellings map to the
   documented seconds value within one binary64 ulp where the decimal itself is not exact; unknown suffixes,
   negatives and nonfinite values remain rejected.

## Gates

### G1 — quantity representation and boundary inventory

The independent control enumerates all frozen types and proves their size/alignment, private representation, checked
construction and absence of blanket scalar conversions. It reconciles `docs/QUANTITIES.md` with the raw serde fields,
typed view, prepared wrappers, reaction-rate kernel and scalar adapters. It searches the covered source for the barn
factor and requires exactly one production occurrence. The workspace dependency graph and `unsafe` inventory equal
the opening baseline.

### G2 — compile-time dimensional rejection and legacy compilation

At least six isolated consumer fixtures try incompatible time/energy, mass/temperature, physical-flux/multiplier,
cross-section/rate, grams/atoms-per-gram and energy/rate operations. Every fixture must fail `cargo check` for a type
error; a planted same-type repair must compile. A positive consumer exercises all documented quantity constructors
and operations. A separate legacy consumer compiles the raw `Spec` fields and existing public helper and prepared-run
signatures. The control records compiler identity, command, exit status and diagnostic class rather than accepting an
arbitrary build failure.

### G3 — analytic and metamorphic scientific relations

An independent Python control generates the frozen compact data, invokes the release binary and candidate, computes
the analytic values itself and evaluates all seven relation families above. It plants one changed expectation in
each family and proves the comparator rejects it. Release behavior is reported even when a relation is newly guarded;
the candidate may not loosen a relation that already passes in the release.

### G4 — public compatibility and exact released result

The signed release and candidate run compact trace, coupled, charged-projectile, prepared, mesh, uncertainty and
radiological fixtures plus the production example. Normalized release/candidate results are exactly equal. CLI and
Python results remain equal under their established normalization; `actinv-spec-1` and `actinv-mesh-spec-1` JSON
round trips and a fixed accepted/rejected input corpus retain their outcomes. Public examples and the legacy Rust
consumer from G2 pass without edits.

### G5 — no material runtime or memory regression

Locally rebuilt release and candidate binaries run the unchanged warm-cache production workload in alternating order
after five untimed warm-ups each. At least fifteen measured processes per binary record raw wall values, median, p95,
mean and sample deviation; GNU time records peak RSS. Scalar thread variables are one, input hashes are reverified and
normalized output is exactly equal. Candidate median may be at most `1.10` times release median, candidate p95 at
most `1.15` times release p95, and candidate peak RSS at most the larger of `1.10` times release RSS or release RSS
plus 16 MiB. These are compatibility ceilings, not a performance-improvement claim.

### G6 — quality, reproducibility and close

The four exact Rust commands in `docs/maintainers/AGENTS.md` pass, as do Python-binding Rust gates, doctests,
prior-verdict, release-note, dependency, public-example, self-contained-clone, CI end-to-end, bounded parser,
P8 mesh, P9 mode and P10 legacy-projectile controls. A checker rehashes the protocol and committed evidence,
independently rederives every tolerance, source inventory, compile result, semantic comparison and performance limit,
and rejects planted evidence changes. No generated nuclear data, target tree, temporary consumer or bulk artifact
enters Git. The session records the source/evidence commit and successful GitHub workflow; the manifest is regenerated
once at closure and a clean rerun is byte-identical.

## Closure interpretation

`P16-PASS` requires G1--G6 without relaxing a type boundary, relation, normalization or runtime ceiling after results
are observed. One append-only documented repair round makes an otherwise passing close `P16-CONDITIONAL`; a second
repair need or an unmet required gate closes `P16-FAIL` and moves unfinished work to a separately frozen successor.
A passing P16 does not itself authorize a version bump or registry publication. P17 remains unopened until P16 has a
checker-derived verdict and green pushed workflow.
