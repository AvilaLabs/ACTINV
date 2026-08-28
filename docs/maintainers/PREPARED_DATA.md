# Prepared activation-data cache

P15 introduces two disposable, content-bound cache schemas. They change data movement only: the NPZ activation
library and its index remain the certificate inputs, all original activation rows remain accounted for, and cache
deletion changes preparation time rather than a scientific result.

## Schemas

`actinv-prepared-library-1` (`ACTPLB01`, version 1) is a compact groupwise representation. Its 224-byte fixed-width
little-endian header records row/group/boundary counts, section offsets and lengths, the source-library and index
SHA-256 values, and the preparation-algorithm SHA-256. Each 40-byte row descriptor retains the original target index,
MT, ZAP, LFS, LMF and source order, then identifies one contiguous stored span. Positive-zero groups before and after
the span are implicit; every bit pattern inside the span—including internal zero and negative zero—is retained. Exact
binary64 group boundaries follow the span payload.

`actinv-collapsed-spectrum-1` (`ACTCOL01`, version 1) is the ordinary single-spectrum representation. Its 288-byte
header additionally binds the exact ascending flux-vector SHA-256 and the prepared artifact's integrity SHA-256. It
stores the exact boundaries and flux bits, every original 24-byte row descriptor, one opening-order binary64 collapse
per row, and the fission-average-energy value/presence records required by existing results.

Both formats end with the raw 32-byte SHA-256 of every preceding artifact byte. Readers independently validate magic,
version, reserved fields, algorithm identity, all counts and offsets, overflow, contiguous span accounting, dimensions,
source identities, payload values, integrity trailer, truncation and trailing bytes before returning an object. The
schemas are internal cache formats rather than stable public interchange APIs; an incompatible future schema uses a
new cache namespace and must fail closed against a file found at the old expected location.

## Lifecycle and concurrency

The default root follows the platform cache convention. `ACTINV_CACHE_DIR` overrides it only when it names an absolute
path. A prepared-library directory is keyed by the declared and independently verified source-library and index
hashes; collapsed filenames are keyed by the exact flux-bit hash. Cache paths never enter the result certificate.

Creation takes an exclusive `create_new` publication lock, writes and synchronizes a temporary sibling, renames it on
the same filesystem, then synchronizes the parent directory where supported. Another creator waits for publication
and validates the resulting final file. A partial temporary sibling is ignored. An existing corrupt, stale,
wrong-source or wrong-version final artifact is reported and is neither trusted nor overwritten. Deleting a valid
cache is the supported way to request deterministic recreation.

The implementation deliberately uses buffered reads and seeking rather than memory mapping, so this boundary contains
no `unsafe`. General data-crate entry points verify the NPZ SHA-256 themselves. The core uses explicitly named
`*_after_sha256_verification` entry points only immediately after its existing complete source-hash check, avoiding a
second full source read without weakening the trust boundary.

## Execution paths

- Ordinary single-spectrum runs use the collapsed artifact and preserve the opening summation order used to create it.
- Generic prepared and mesh runs use groupwise sparse data and retain original source row numbers.
- Indexed reads allocate only selected descriptors, spans and values, plus the shared group boundaries.
- Uncertainty runs retain the verified dense NPZ path until an exact covariance-to-original-row mapping is separately
  controlled.
- Python calls the same Rust preparation and solver path; no activation or decay bulk array crosses into Python.

The frozen contract and acceptance thresholds are in
[`protocols/ACTINV-P15_PROTOCOL.md`](../../protocols/ACTINV-P15_PROTOCOL.md). Independent representation, mutation,
interface and performance evidence is derived by `controls/check_p15.py` without importing the Rust writer.
