# ACTINV-P21 protocol — large-scale execution

Status: **frozen** at the opening commit. Amendments land as separate files;
this file is never edited afterward.

## Opening context

Roadmap row (post-v1 program, frozen by CB1): "Reuse prepared networks across
compatible mesh cells, group common workloads, stream selectable outputs,
bound memory by chunk size and add interruption/checkpoint support; replace
extrapolation with an executed large case." Prerequisite: P15 (mesh).
P20 closed `P20-PASS`; P18b closed `P18b-FAIL`; P23 closed `P23-PASS`.
P21 reads no P18b evidence and changes no physics.

The P15 mesh runner already streams canonical flux chunks through a shared
`PreparedRun` on a bounded rayon pool and writes one NDJSON record per cell.
CB1 measured the surface up to 256 cells and recorded its million-cell
figures under `million_cell_linear_extrapolation_not_executed` with the
explicit warning that they "must not be quoted as an executed benchmark."
P21's job is to turn the mesh path into an honestly scaled execution surface:
reused, streamable, interruptible, memory-bounded — and *executed*, not
extrapolated.

## Design contract

### Prepared-state reuse and workload grouping

`PreparedRun` (library, decay chain, index, photon response, covariance) is
already immutable, `Send + Sync`, and shared across mesh workers. P21 audits
the remaining per-cell duplication and bounds it:

- per-cell work that is a pure function of the rebinned activation-group
  flux vector is grouped by signature: the SHA-256 of the rebinned
  `flux_per_group` f64 little-endian bytes keys a per-run memo; a cache hit
  emits the memoized result bytes under the cell's own ordinal, identity and
  rebin ledger. Reuse is recorded in the run: the footer carries
  `cells_served_from_reuse`;
- reuse never changes result bytes: every cell record in a grouped run is
  bit-identical to the same run with grouping disabled, and the footers
  agree modulo `cells_served_from_reuse` and the timing fields — which the
  control demonstrates;
- grouping is always on; an opt-out (`group_workloads: false`) exists for
  diagnostics and the identity control.

### Streaming and memory bounds

- The per-cell `result` object becomes selectable: `cell_result_fields`
  (optional list of `RunResult` top-level keys) keeps only the named fields
  per cell record. Absent field = current complete record, byte-identical.
- `memory_limit_bytes` (optional positive integer) is an honest post-hoc
  bound: after each completed chunk the process peak RSS (Linux
  `/proc/self/status` `VmHWM`) is compared against the limit; exceeding it
  aborts the run with a named error carrying both numbers. It is a guard,
  not an allocator cap; the protocol does not claim pre-emptive bounding.

### Interruption and checkpoint

The NDJSON output is its own checkpoint: every flushed cell record is a
completed-cell checkpoint. `resume: true` in the mesh spec switches output
from atomic temp+rename to direct write; on start it inspects the existing
output file:

- header must equal byte-for-byte the header a fresh run would emit, which
  binds the canonical flux hash, cell count, grids and a new
  `spec_fingerprint_sha256` header field — the canonical-JSON SHA-256 of
  the `MeshSpec` with `resume`, `threads`, `chunk_cells` and
  `memory_limit_bytes` removed (scheduling and the guard may change across
  a resume; content may not);
- complete, in-order cell records are accepted as done; a trailing partial
  line is truncated; a run whose footer is already present returns its
  summary without re-solving;
- remaining cells are re-executed in stream order and appended; the footer
  closes the file exactly as an uninterrupted run.

Resume identity is judged by the established convention: all bytes except
the footer's `wall_time_s` and `cells_per_s` are identical to an
uninterrupted run. Fresh (non-resume) output remains atomic: a failed fresh
run still leaves no partial output.

### Executed scale evidence

The qualification case is an *executed* large mesh, executed end-to-end in
this repository's bounded job environment:

- **20,000 cells**, canonical flux generated deterministically by the
  committed control (a rectilinear grid over the pinned TENDL-2025
  709-group boundaries with smoothly varying, non-repeating spectra scaled
  from the published FNS Fe example; every spectrum distinct so workload
  grouping cannot trivially collapse the case);
- pinned inputs: `actinv-data/v1.0.0/activation/tendl-2025-neutron-709g.npz`
  and `actinv-data/v1.0.0/decay/endf-b-viii-0_decay.dat`, Fe-100 material,
  the two-step 300 s irradiation + 60 s decay schedule;
- recorded: hardware description, OS, rustc and package versions, cgroup
  limits actually in force, wall time, per-size peak RSS, canonical-flux
  and output hashes, cell count, throughput.

Memory-scaling gates are measured, not asserted:

- at fixed `chunk_cells`, peak RSS at 1,000 / 5,000 / 20,000 cells must not
  grow with cell count beyond a 64 MB absolute slack;
- at fixed cell count, peak RSS must track `chunk_cells` — the 256-chunk
  run's peak must exceed the 16-chunk run's peak by at least 32 MB (the
  baseline probe measured ~86 MB), demonstrating the bound follows chunk
  size.

## Frozen evidence rules

- Mesh output equals independent per-cell ordinary runs bit-for-bit on the
  eight-cell control problem (existing P8 identity surface, unchanged).
- Thread-count invariance: identical output at 1 and 4 workers, by the
  established convention (all bytes except footer `wall_time_s` /
  `cells_per_s`).
- Resume after a mid-run kill produces output identical to an uninterrupted
  run by the same convention.
- `group_workloads` on/off outputs are byte-identical.
- `cell_result_fields` output contains only the named fields, footer closes,
  and the reduced run's cells match the full run's cells on their shared
  fields.
- `memory_limit_bytes` below the observed requirement fails loudly with the
  named error; no footer is emitted by an aborted run.
- No RNG anywhere; canonical flux and spectra are deterministic functions
  of their generator inputs.
- The executed case names its real size; no extrapolation is presented as
  an executed benchmark anywhere in the emitted evidence.

## Gates

- **G0** — protocol freeze, opening commit, prior-verdict map, four-surface
  identity baseline, baseline memory/throughput profile (256 cells and
  1,000 cells on the pinned library, recorded RSS and rate).
- **G1** — signature grouping + `cell_result_fields` + `memory_limit_bytes`
  in the runtime; absent-option byte identity preserved.
- **G2** — resume/checkpoint: interrupted and resumed output identical to
  uninterrupted by the established convention; fingerprint mismatch and
  complete-run resume behave as specified.
- **G3** — executed 20,000-cell case with the full hardware/limits record
  and both memory-scaling gates; grouping measurement on a
  repeated-spectrum case.
- **G4** — docs, examples, performance accounting; CB1's
  `million_cell_linear_extrapolation_not_executed` block is superseded by
  the executed record.
- **G5** — independent closure checker, verdict, session record.

## Explicit non-claims

- No unexecuted million-cell claim; scale evidence names its actual size
  (20,000 cells) and hardware.
- No distributed or cluster execution; parallelism stays in-process on the
  rayon pool.
- Checkpoint output is for resumption of the same spec only; it is not a
  stable interchange format.
- `memory_limit_bytes` is a post-hoc guard that aborts after a breach is
  observed; it does not prevent a single allocation from exceeding it.
- Grouping reuses bit-identical results only; it is not an approximation.
