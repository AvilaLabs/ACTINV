# Pinned-data CI recovery

## Observed cache recovery

The authorized [CI cache release](https://github.com/AvilaLabs/ACTINV/releases/tag/ci-data-cache-2026-10-04-v1)
is published. All 13 payloads were staged from the existing local copies,
then downloaded into a separate fresh directory and verified against the
unchanged original hashes and byte sizes. The separate release also contains
the pinned manifest and attribution notice. Nuclear payloads remain outside
Git.

Coordinator receipts under `results/quality/p117/` record zero exits for the
transfer fault tests, local 13-file verification, both CI layouts installed
with networking disabled, fresh release downloads, unchanged native catalog
fetch, both FNS source regression suites, the 20-measurement FNS campaign and
the unchanged heat reconstruction/second-campaign diagnosis. These establish
a working exact-byte transport. Successor twin controls, quality closure and
exact-commit GitHub CI remain required before P117 qualification.

## P117 authorized cache plan

P117 authorizes a separate, versioned exact-byte data release,
`ci-data-cache-2026-10-04-v1`, in `AvilaLabs/ACTINV`. The tracked authority is
[`scripts/ci_data_seed.json`](../../scripts/ci_data_seed.json); its 13 files
total 166,789,318 bytes (159.06 MiB), excluding notice/manifest/container
overhead. The matching source and attribution notice is
[`CI_DATA_CACHE_NOTICE.md`](CI_DATA_CACHE_NOTICE.md). Neither nuclear payloads
nor this cache should be committed to Git. Release publication and all
download/install evidence remain coordinator-owned and are not represented as
complete by this note.

| Seed path | Existing local source |
|---|---|
| `tendl/<10 files>` | `/home/connoravila/actinv-ci-data/tendl/` (also `/home/connoravila/nuclear-data/tendl-2023/ci_fe/`) |
| `decay/endf-b-viii-0_decay.dat` | `/home/connoravila/actinv-ci-data/decay/endf-b-viii-0_decay.dat` |
| `decay/jeff-3-3_decay.dat` | `/home/connoravila/Documents/actinv/actinv-data/v1.1.0/decay/jeff-3-3_decay.dat` |
| `fns/fns.zip` | `/home/connoravila/nuclear-data/conderc-fns/fns.zip` |

The release asset URL for each entry is formed by the manifest's versioned
URL and basename. `scripts/fetch_ci_data.sh` will consume the ten TENDL files
and ENDF tape beneath `$ACTINV_CI_DATA`; its seed-present path must validate
existing exact payloads and skip otherwise unnecessary TENDL ZIP downloads.
The FNS workflow will stage both decay tapes under
`target/fns-data/v1.1.0/decay/` before the unchanged `actinv data fetch`
command, which reuses valid catalog-pinned files and obtains the activation
library, index, and notice from the existing release URLs. It will stage the
source archive at `target/fns-iron/fns.zip`, the path used by both FNS
controls.

The seed notice preserves the original provider attributions and terms. IAEA
Terms of Use permit copying and dissemination with source credit, no implied
IAEA endorsement, and subject to restrictions specific to materials; they
also distinguish third-party content. The cache is byte-preserving and does
not assert one blanket license for ENDF/B, JEFF, TENDL-2023, or CoNDERC FNS
assets. The native v1.1.0 catalog and its historic notice are unchanged.

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

The local copies are the exact-byte source candidates for the authorized
P117 cache; no claim of release publication or CI accessibility is made until
the coordinator records those steps. Provider terms continue to apply as
recorded in `docs/DATA.md` and the cache notice. The [pinned Rust cache
cleanup](https://github.com/Swatinem/rust-cache/blob/6323deb102c322ba6fcbdcafc7e3dddab59af2b6/src/cleanup.ts)
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

The [IAEA Terms of Use](https://nucleus.iaea.org/Pages/Others/Terms-Of-Use.aspx)
permit copying and dissemination of IAEA site materials with acknowledgement
and no implied endorsement, subject to specific material restrictions. They
state that third-party materials may require rights from the identified
rights holder. P117's cache notice retains those qualifications and original
provider terms rather than applying a project-wide data license.

## Recovery acceptance

P117 acceptance requires all 13 versioned release assets to match the
manifest's exact URLs, bytes, and SHA-256 values, and the seed downloader to
fail closed on missing, corrupt, partial, or mismatched files. Preserve the
original FNS archive/member checks, atomic installation, and existing-file
verification. The cache is a transport location, not a replacement source of
scientific data or a new license. Download failures remain visible; never
treat an unexecuted data-dependent check as passing.

Alternatively, retry the unchanged workflows after official IAEA access
recovers. Before resuming feature work, observe all required workflows
green on the exact successor implementation and closure commits. The
owner's red-check rule in `AGENTS.md` remains in force.

## P117 evidence update — 2026-10-04

Eighteen of nineteen fresh local P117 quality gates have passed, including the
G0 seal/replay and G1/G2. The complete P116 science report was reproduced
exactly: 35 requests, 138 component targets and comparisons, 43 rejected
mutations, 50 refusals, and byte-identical repeats. Nine P117 source regressions
and seven verdict regressions also passed. The final full read-only replay is
still running; G3, the local verdict, the push, and all six required CI
workflows remain pending. These results are intermediate evidence, not a
terminal P117 PASS. P116's FAIL disposition remains immutable.

The final replay subsequently passed, completing all nineteen initial gates.
The quality collector passed, but its independent verdict failed only at
G3_local: the checker requested an absent top-level handbook map instead of
the already-qualified nested map. The complete actual failure is preserved
in initial checkpoint `24929f477ab56ed026eabee4260e39572c226428` and the
70-file archive plus discovery
`73f2cbec3a4a69b9dfe2b14301d283802ffa2fc00d71ce09c7eeab02c014232a`.
Registered Amendment A
`3b992672b5339f520beaff6450c227dd62d05516a70bc854b6df8dbe147b4025`
permits the one metadata repair, real-producer regression coverage, seven
repeated affected checks and explicitly adopted unchanged initial evidence.
It changes no data, transport, Rust or scientific criteria. No push or green
CI qualification exists yet.

2026-10-04 terminal update: P117 stopped after the consumed amendment. Four
amended source/seal gates completed zero, but a duplicate G0 replay recorder
returned actual one at the atomic no-overwrite log/receipt guard. The source
replay itself passed; original successful evidence was preserved. Remaining
amended G1/G2/full replay and G3 were not run. The unchanged independent
verdict derived terminal P117-FAIL with SHA
`11ea137a5362b5cc42c771137771cbad08b5cd7084939480138b80e656ee4e9b`.
The separate cache and its verified inputs remain intact. Preserve both P117
failure episodes, then register a successor that fixes the static CI dependency
on ignored target logs and requires a clean-worktree source-only verdict replay
before any push. No CI qualification or waste roadmap closure is claimed.
