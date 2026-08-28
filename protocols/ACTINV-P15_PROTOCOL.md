# ACTINV P15 — deterministic prepared and selective activation data

Opened 2026-08-28 after the honest P14 close at repository commit
`5f7289a44c2686505d0e1b40f4b00ef5c8e4a9ab`. P14 retained exact-result safe wins but missed its 10% gate. Its
measured anatomy identifies the next boundary: the public run inflates a 951,392,920-byte dense cross-section array
whose exact production nonzero spans occupy 268,778,064 bytes. P15 is the first and only open phase.

P15 adds a deterministic, schema-versioned, content-bound prepared representation; safe automatic reuse; indexed
target access; and a spectrum-bound collapsed representation for the ordinary one-spectrum path. It changes data
movement, not evaluated values, physics, numerical tolerances, solver order, public result schemas or certificate
provenance. No result may be removed or normalized merely because selective loading makes it inconvenient to
reproduce. In particular, every activation row and existing ledger contribution remains accounted for.

## Frozen inputs, opening implementation and minimum work

The production workload is `examples/fns_fe_5min.json` with the exact released `data-v1.0.0` paths substituted. The
input identities are unchanged from P14:

- TENDL-2025 neutron activation NPZ:
  `ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44`;
- activation index: `8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb`;
- ENDF/B-VIII.0 decay payload:
  `6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb`;
- JEFF-3.3 fallback decay payload:
  `850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123`.

The opening binary is rebuilt from exact commit `5f7289a44c2686505d0e1b40f4b00ef5c8e4a9ab` with the recorded compiler.
Historical P14 timings are context only; P15 measures opening and candidate binaries again, interleaved on the same
host. The minimum development input is a generated multi-target library containing leading, trailing and internal
zero groups. It settles wire-format, mutation, atomic-publication and exact-collapse controls before production data
is read. No planned P15 job exceeds ten minutes; any newly discovered longer job must first be reduced and profiled
under standing rule 7.

## Normative representation and lifecycle

1. The prepared-library schema is `actinv-prepared-library-1`. It contains fixed-width little-endian metadata,
   original row order and target identity, exact group-boundary bits, per-row nonzero span descriptors and exact
   binary64 span values. Internal zero groups may remain inside a span; no nonzero value is quantized or omitted.
2. The collapsed schema is `actinv-collapsed-spectrum-1`. It binds the prepared schema, original activation-library
   SHA-256, activation-index SHA-256, exact ascending flux-vector bits, group boundaries, row metadata and every
   row's spectrum collapse. Any auxiliary fission-energy quantity required by the existing result is bound too.
3. Both artifacts are self-validating: magic, schema version, declared lengths/counts, source identities, offsets,
   dimensions and a SHA-256 over all preceding artifact bytes are checked before use. Arithmetic is overflow-checked;
   overlaps, gaps where forbidden, truncation and trailing bytes fail without a result.
4. Default reuse is automatic and requires no spec edit. `ACTINV_CACHE_DIR` may select the cache root for controls and
   advanced deployments; otherwise ACTINV follows the platform cache directory convention. Cache paths never replace
   source identities in a result or certificate.
5. Publication uses a completed temporary sibling followed by same-filesystem rename. Concurrent creators may race
   only by producing independently validated identical bytes; a partial file is never accepted. An existing corrupt,
   stale, wrong-source or cross-schema artifact produces a diagnostic and is not silently trusted or overwritten.
   Deleting the cache is supported and changes only preparation cost.
6. The ordinary single-spectrum path may reuse the collapsed artifact. Generic prepared and mesh paths may retain
   groupwise sparse data. Uncertainty may deliberately retain the verified dense path until an exact original-row
   mapping is independently controlled; it must not receive an approximate or renumbered substitute.
7. Indexed target reads return original row metadata and cross-section bits in original row order. Target selection is
   conservative and may load extra targets, but it may never exclude a row needed for an existing result, ledger,
   fission-yield selection, uncertainty or provenance field.
8. Memory mapping is evaluated but is not an excuse for `unsafe`. P15 introduces no `unsafe` block. Buffered seeking,
   bounded streaming or a dependency with a completely safe public boundary is preferred when it meets the gate.
9. The Python API calls the same Rust cache and solver path. No activation or decay bulk array crosses the Python
   boundary, and no Python-side full-library copy/cache is added.

## Frozen comparison and normalization boundary

Opening, candidate-with-empty-cache, candidate-warm-cache and candidate-after-cache-deletion run the same compact and
production specifications. Recursive equality removes only top-level `ms`. Entry-point labels are normalized only
when comparing different public entry points, as in the frozen P12 controls. Certificate hashes, paths, schemas,
inventories, state counts, every scientific scalar, pathways, uncertainty/radiological records, ledger keys and
values—including `assembly.n_library_rows`—are never normalized away. A cache hit is not added to result JSON.

## Gates

### G1 — opening baseline and representation contract

An independent control verifies the opening commit, compiler/binary identity, host, thread environment and all four
production input hashes. It records NPZ member sizes, shape, row/target counts, exact nonzero count and the dense and
prepared byte totals. The prepared production payload, including metadata and integrity trailer, must be no more than
35% of the original dense `sig` payload. Generated fixtures prove leading/trailing/internal zeros and zero-only rows
round-trip exactly.

### G2 — deterministic preparation and indexed identity

Two fresh preparations from the same source produce byte-identical prepared artifacts. Every one of the 167,735
production row descriptors, all 710 boundary bits and every retained cross-section bit agree with the source NPZ.
The prepared reader re-derives all 167,735 production spectrum collapses with exact binary64 identity to the opening
algorithm. Indexed reads over the public Fe targets, a fixed noncontiguous target sample, an empty selection and all
targets agree exactly with the verified NPZ reader; selected allocation is bounded by selected payload plus 16 MiB.

### G3 — cache integrity, invalidation and atomicity

At minimum, independent plants alter magic, schema version, source-library hash, source-index hash, flux hash, one
row descriptor, one selected value, an offset, a declared count, the integrity trailer, truncation and trailing data.
Every applicable plant fails before a result is published and names the artifact or field class. Deletion followed by
recreation produces the original bytes and result. A stale/cross-version file at the expected location fails rather
than falling back silently. Interrupted publication leaves no accepted final file. Two concurrent preparations either
publish identical valid bytes or one validates and reuses the other's identical final artifact.

### G4 — complete semantic, provenance and interface identity

Compact trace/coupled/charged fixtures and the production example satisfy the frozen recursive comparison for empty,
warm and deleted-cache paths. Certificate-, inventory- and ledger-value plants prove the comparator rejects changes.
The original activation and decay paths/hashes remain the certificate inputs; no cache identity is substituted.
Existing CLI, Python, prepared, mesh, projectile, uncertainty, radiological and public-example controls remain green.
The Python warm path is measured and may add interpreter overhead, but it must not allocate another full prepared or
dense activation payload.

### G5 — material user-visible improvement

Opening and warm candidate processes run in alternating order after five untimed warm-ups per binary. At least fifteen
measured processes per binary record raw wall values, median, p95, mean and sample deviation; GNU time records peak
RSS. Scalar thread variables are one. The candidate cache is already complete for warm samples; empty-cache creation
time, bytes written and peak RSS are reported separately and are never hidden inside a warm claim.

For the production workload, all of the following are required:

- warm candidate median wall time is at most two thirds of opening median (at least 1.5x faster);
- warm candidate p95 wall time is at most two thirds of opening p95;
- warm candidate peak RSS is at most one half of opening peak RSS (at least 2x lower);
- empty-cache peak RSS is at most 512 MiB and empty-cache wall time is at most twice opening median; and
- cache deletion/recreation does not change the normalized result or recreated artifact bytes.

The stretch goals are warm median at or below 1.0 second and peak RSS at or below 512 MiB. A stretch miss does not
fail P15; a required ratio miss does. Claims name this host, workload, cache state and requested outputs and are not
generalized to competitors or other machines.

### G6 — quality, reproducibility and close

The four exact Rust commands in `docs/maintainers/AGENTS.md` pass, as do the Python-binding Rust gates, prior-verdict,
release-note, dependency, public-example, self-contained-clone, CI end-to-end, parser-smoke and P10 legacy-projectile
controls. A checker independently rehashes the protocol and evidence, parses both artifact schemas without importing
the production writer, rederives byte/count/ratio/equality claims and rejects planted evidence changes. No bulk data
or generated cache enters Git. The session records the source/evidence commit and successful GitHub workflow; the
manifest is regenerated once at closure and a clean rerun is byte-identical.

## Closure interpretation

`P15-PASS` requires G1--G6. One documented repair round makes an otherwise passing close `P15-CONDITIONAL`; a second
repair need or any unmet required gate closes `P15-FAIL` and moves remaining work to a separately frozen successor.
Thresholds, normalization and cache-integrity behavior are not relaxed after evidence is observed. A passing P15 is
eligible for a backward-compatible performance patch, but tagging or publishing that patch remains a separate act.
