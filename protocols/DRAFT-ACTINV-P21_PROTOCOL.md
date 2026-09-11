# DRAFT ACTINV-P21 protocol — large-scale execution

Status: **draft — not frozen**. Working draft for maintainer review; freezes
only on explicit direction as `ACTINV-P21_PROTOCOL.md`.

## Opening context

Roadmap row (post-v1 program, frozen by CB1): "Reuse prepared networks across
compatible mesh cells, group common workloads, stream selectable outputs,
bound memory by chunk size and add interruption/checkpoint support; replace
extrapolation with an executed large case." Prerequisite: P15 (mesh).

The CB1 measurement recorded a 1.09 GB peak for the single-cell public
example and the mesh path keeps measured memory nearly flat on the tested
range. P21's job is to turn the mesh path into an honestly-scaled execution
surface: executed, not extrapolated.

## Design contract (draft)

### Prepared-network reuse

A prepared run's immutable state (library, decay chain, index, photon
response) is already `Send + Sync` and shared across mesh workers without
cloning. P21 audits and then bounds the remaining per-cell duplication:
network assembly per unique (material, schedule, spectrum) signature is
reused across cells that share it, and the reuse is recorded in the ledger.

### Streaming and memory bounds

- Per-cell outputs are streamable (selectable fields to disk) so peak RSS is
  bounded by chunk size, not total cell count.
- The run-level JSON remains complete; streaming files are additive.
- A memory ceiling knob (`memory_limit`) fails loudly rather than swapping
  or OOM-killing.

### Interruption and checkpoint

A mesh run writes a deterministic checkpoint per completed cell; a resumed
run re-executes only incomplete cells and produces byte-identical output to
an uninterrupted run.

### Executed scale evidence

The qualification case is an *executed* large mesh (size fixed at freeze)
with recorded hardware, wall time, peak RSS, and cache state — replacing any
extrapolated million-cell claim.

## Frozen evidence rules (draft)

- Mesh output equals independent per-cell runs bit-for-bit.
- Thread-count invariance: identical results at 1 and N workers.
- Memory scales with chunk size, not cell count (measured, not asserted).
- Resume produces byte-identical output.

## Gates (draft)

- **G0** — protocol freeze, baseline memory/throughput profile, Core case.
- **G1** — prepared-state reuse + streaming output schema.
- **G2** — interruption/checkpoint + resume identity.
- **G3** — executed large-case evidence with hardware record.
- **G4** — docs, examples, performance bounds.
- **G5** — independent closure.

## Explicit non-claims

- No unexecuted million-cell claim; scale evidence names its actual size.
- No distributed/cluster execution; parallelism stays in-process.
- Checkpoint files are for resumption, not a stable interchange format.
