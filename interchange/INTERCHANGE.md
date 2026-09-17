# ACTINV ↔ Avila Core interchange contract (P27)

This document fixes how an `actinv-study-1` build crosses into an Avila Core
case package and how Core's process evidence maps back into a study record.
The ACTINV side owns the study schema, deterministic expansion, population
accounting and verdict logic; Core owns staged execution, receipts, claim
binding and campaign evaluation. Users never author Core JSON — the adapter
(`interchange/actinv_core_adapter.py`) produces it.

## Pinned interface

- Semantic profile: `avila.core/semantic/0.2-draft` (frozen in the G0 seal;
  a package or receipt reporting any other profile is refused).
- Supported Core range pinned at G0: checkout `844daa2`, workspace 0.1.0.
- Consumed schema versions (digests frozen at G0): `case-package/v0.1-draft`,
  `evidence-contract/v0.2-draft`, `registry-snapshot/v0.2-draft`,
  `execution-receipt/v0.1-draft`, `evidence-claims/v0.2-draft`,
  `campaign-report/v0.2-draft`, `qualification/v0.1-draft`.

## Outbound: study → case package

`emit_package(study, manifest_dir, outdir)` writes a Core case directory:

- `contract.json` — `avila.core/evidence-contract/v0.2-draft`. One
  `contract_input` per data artifact the study binds (activation library +
  index, decay primary/fallback, the actinv executable, each emitted spec).
  `workflow`: one step per case id, capability_type `actinv.run@1`, inputs
  bound to the case's spec + the shared data inputs. Requirements: per case,
  `case_executed` (categorical: the step produced its output artifact) — the
  comparative ACT-COMPARE-01 rules are evaluated ACTINV-side and recorded as
  study-record comparison verdicts, never delegated to Core.
- `registry.json` — `avila.core/registry-snapshot/v0.2-draft`: kinds
  (`core.dimensionless-ratio`), purposes (`avila-labs.core.internal-integration`),
  roles (`actinv.problem`, `actinv.run-output`, `actinv.executable`,
  `actinv.activation-library`, `actinv.decay-data`), capability types
  (`actinv.run`).
- `package.json` — `avila.core/case-package/v0.1-draft`: documents (contract,
  registry, claims, the adapter definition), artifacts digest-pinned under
  named source_roots (`actinv` = checkout data, `actinv-build` = emitted
  specs/manifest, `actinv-release` = the executable), capabilities binding the
  actinv binary by sha256, executions: one step per case through adapter
  `avila-labs.actinv/run-case@1`.
- `adapter.json` — `avila.core/external-checker-adapter/v0.1-draft` defining
  `avila-labs.actinv/run-case@1`: invokes the bound `actinv` capability as
  `actinv run <spec> <out>` with a declared timeout; outputs: `result`
  (the case `out.json`, media type `application/vnd.actinv.result+json`) and
  claims extracting per-time responses by JSON pointer.
- `claims.json` — `avila.core/evidence-claims/v0.2-draft` claim spec for each
  output slot.

The emitted spec files staged as inputs carry study-relative paths verbatim;
the adapter stages data files at the workspace paths the spec strings name
(`workspace_path` in the receipt inputs), so the staged spec bytes stay
byte-identical to the manifest digests.

## Inbound: receipt → study record

`interpret_receipt(receipt)` maps an `execution-receipt/v0.1-draft` to a case
row update:

| receipt field | case row field |
|---|---|
| `capability.executable_sha256` | `tool.binary_sha256` (must equal the record's) |
| `inputs[].sha256` | `spec_sha256` (the `spec` slot digest must equal the manifest's) |
| `status`, `process` | `status` mapping below |
| `outputs[result].sha256` | `out_sha256` |

## Failure classes → case status

| condition | class | case status |
|---|---|---|
| executable absent/digest mismatch at staging | `capability_mismatch` | `failed` (run refused; no execution claimed) |
| process exits nonzero, stdout/stderr bound | `nonzero_exit` | `failed` |
| process killed at `timeout_ms` | `timeout` | `failed` |
| exit 0 but `result` output missing/undigested | `missing_output` | `failed` |
| `result` not parseable `actinv` output JSON | `malformed_output` | `failed` |
| spec/schema/scope refusal before solve (`family_not_qualified`, bad version, unknown field, `study_too_large`, `template_revoked`, missing data files) | `contract_gap` | `contract_gap` |
| receipt `status` ≠ `executed` for any reason not above | `process_error` | `failed` |

A `contract_gap` row records the refusal string; nothing else runs for that
case. Failures never coerce to zero-valued metrics.

## Evidence kinds

Every study-record case row and every interchange claim carries one of:

- `attestation` — a recorded claim/registry entry; bytes not re-verified now
- `verified` — artifact bytes re-hashed and matched now
- `reused` — a committed Core receipt reused under Core's committed-receipt
  rules (inputs+executable+invocation identical, outputs still verify)
- `fresh` — executed during this run

## Qualification boundary

Receipts and claims establish process facts only (what ran, on which bytes,
what it produced). They do not establish scientific correctness, reviewer
sign-off or regulatory suitability. Study records on this path carry
`qualification: unqualified`; Core's own unqualified-evidence warning is not
an ACTINV qualification.
