# Pinned-data CI recovery

On 2026-10-04, controls run `37216443618` and fns-iron run `37216443631`
failed on implementation `2117f3b5df715ce832658f58250103789be845bb`
while fetching the pinned IAEA ENDF/B-VIII.0 decay ZIP. Both unchanged
second attempts failed with HTTP 403. A metadata-only request to the exact
URL returned `cf-mitigated: challenge`; the official NDS hostname alias
returned the same browser challenge. No benchmark failure is established
by these download failures.

The original run metadata, failed logs and header response are retained in
`results/failures/p116_ci/`. P116's unchanged checker derives terminal FAIL
under its consumed-repair rule. Its scientific and local quality evidence
remains passing. Keep that disposition immutable; a separately registered
successor must bind any retrieval/control changes and all required CI results.

## Exact inputs available locally

The existing local cache has all eleven payloads pinned by
`scripts/ci_data.sha256`: ten TENDL-2023 Fe evaluations and the decay tape.
Their relative names and SHA-256 values remain the manifest's authority.
The source ZIP is also present locally with its catalog-pinned identity:

| Input | Bytes | SHA-256 |
|---|---:|---|
| ENDF/B-VIII.0 source ZIP | 12,222,963 | `9f0254ea1314a233d957e507779eb53e6c7fafd17100fd5da23ce80ee80dd8ae` |
| ENDF/B-VIII.0 decay tape | 68,313,052 | `6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb` |
| JEFF-3.3 decay tape | 45,718,116 | `850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123` |
| CoNDERC FNS source ZIP | 14,839,792 | `ba1dd6cb150a4aa3e0d81461054aec7d415ef19d946aba8b9886b31de218252d` |

The native v1.1.0 default bundle's TENDL-2025 library, index, both decay
files and notice also have local copies matching the embedded catalog.
The library, index and notice already have project release URLs; the two
decay sources remain IAEA URLs. The FNS controls additionally fetch the
CoNDERC ZIP and verify its complete hash and selected member hashes.
Restoring only the first failed decay download may therefore expose later
IAEA download failures. Verify the whole required set before calling CI fixed.

The local copies are recovery candidates, not an authorization to publish
data. Do not commit nuclear payloads to Git. Provider terms continue to
apply as recorded in `docs/DATA.md` and the data notice. No approved
reachable data cache or mirror is configured in repository variables,
secrets or the workflows. The [pinned Rust cache cleanup](https://github.com/Swatinem/rust-cache/blob/6323deb102c322ba6fcbdcafc7e3dddab59af2b6/src/cleanup.ts)
recursively removes non-build files, so it does not supply this dataset cache.

## Official alternative research

[NNDC's official ENDF/B-VIII.0 download page](https://www.nndc.bnl.gov/endf-b8.0/download.html)
lists a decay archive with a different size and published identity. Its
availability does not establish equality to the pinned IAEA archive or
installed tape. Do not replace a catalog URL/hash, reconstruct data, or
substitute JEFF for the primary decay dataset under the existing seal.

[IAEA Open Benchmarks](https://github.com/IAEA-NDS/open-benchmarks)
licenses its checked-in benchmark data under CC-BY-4.0. Its inspected main
tree supplies neither the pinned FNS ZIP nor these decay/TENDL payloads;
that license is not a blanket redistribution grant for external archives.
The prior local JADE benchmark-hosting snapshot has verified source data
and adapter evidence, but no publication receipt or mirror URL.

## Recovery acceptance

An approved CI-accessible source must provide the exact existing inputs
with permitted use and a durable URL or controlled-transfer mechanism.
Preserve attribution, byte sizes, archive/member SHA checks, atomic
installation and existing-file verification. Download failures remain
visible; missing or mismatched cache data must fail closed. Any new
cache/transport implementation needs a frozen scope and fault controls
before execution. Never treat an unexecuted data-dependent check as passing.

Alternatively, retry the unchanged workflows after official IAEA access
recovers. Before resuming feature work, observe all required workflows
green on the exact successor implementation and closure commits. The
owner's red-check rule in `AGENTS.md` remains in force.
