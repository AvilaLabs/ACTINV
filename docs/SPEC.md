# ACTINV problem specification

The current field reference is maintained in the [ACTINV Handbook — Problem specification](guide/specification.md).

## Independent mesh fields

The [complete mesh reference](guide/specification.md#independent-mesh-specification-actinv-mesh-spec-1) defines the input and output contract. These fields remain indexed here for existing documentation controls and repository links:

| Field | Purpose |
| --- | --- |
| `group_workloads` | Reuse results for identical compatible cell spectra; set to `false` for the identity comparison |
| `cell_result_fields` | Select the top-level result fields, or dotted `steps.<field>[.<key>...]` paths, retained in each cell record |
| `memory_limit_bytes` | Check process peak memory after each completed chunk and abort on a breach; it is a post-hoc guard |
| `resume` | Continue a compatible checkpoint using complete, ordered cell records |
| `spec_fingerprint_sha256` | Header identity for calculation content; scheduling, resume, and memory-guard settings are excluded |
| `cells_served_from_reuse` | Footer count of cells served from the workload-reuse memo |

## Calculation options

The [uncertainty reference](guide/specification.md#mf33-uncertainty) documents `uncertainty.covariance` and feature-specific `require_complete` coverage checks. The [options reference](guide/specification.md#options-and-result) documents `options.cram_order`, the selected numerical approximation order.

Current master also documents transport-tally statistics through `spectrum.relative_error` and `uncertainty.channels: ["flux"]`, and [hydrogen/helium gas production](guide/specification.md#gas-production-h-and-he-isotopes) through `options.gas`. These are [unreleased additions](guide/releases.md#current-master); the handbook retains their complete scope and data requirements.

Start with [Your first calculation](guide/quick-start.md) for a complete runnable example, or [Describe a problem](guide/problems.md) for material, spectrum, schedule, and path conventions.
