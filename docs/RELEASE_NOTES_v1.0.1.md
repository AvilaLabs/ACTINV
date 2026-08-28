# ACTINV v1.0.1 — release notes

ACTINV 1.0.1 is a backward-compatible performance release. It changes how activation data is prepared and reused,
not the evaluated nuclear data, physics, solver order, public input/output schemas, certificate provenance, inventory
values, or ledger values. The separately versioned nuclear-data catalog remains `data-v1.0.0`.

Upgrade the Python package with:

```bash
python -m pip install --upgrade actinv
```

Rust CLI users can install this exact patch with:

```bash
cargo install --locked --force actinv-cli --version 1.0.1
```

## What users gain

The first calculation prepares deterministic, integrity-checked cache files bound to the exact activation library,
index, group boundaries, spectrum, schema, and algorithm. Later compatible calculations reuse those files
automatically. No specification change is required.

On the frozen public FNS iron example and recorded Intel Core i3-N305 host, the warm path changed from 3.075 seconds
to 1.185 seconds median process wall time and from 1.077 GB to 129 MB peak RSS: 2.595× faster and 8.326× lower peak
memory. The p95 changed from 3.190 seconds to 1.212 seconds, or 2.632× faster. These figures apply to that workload,
host, warm-cache state, and requested outputs; they are not a general comparison with another product. The optional
one-second warm-median stretch goal was missed, while every required performance gate passed.

The initial cache creation measured 5.583 seconds at 130 MB peak RSS and wrote about 282 MB. Deleting the cache is
safe and changes only preparation time. Set `ACTINV_CACHE_DIR` to choose its location; otherwise ACTINV follows the
platform cache-directory convention.

## Verification

- All 167,735 production rows, 710 group boundaries, 33,597,258 retained cross-section values, and 167,735 spectrum
  collapses matched the verified opening representation exactly.
- Twenty-three corruption, truncation, stale-source, schema, offset, count, and integrity plants failed before a
  result was published. Interrupted and concurrent creation produced only validated, byte-identical final files.
- CLI and Python returned the same normalized scientific result and retained the original source paths and SHA-256
  identities in their certificates. Python uses the same Rust cache rather than creating a second library copy.
- The exact Rust format, check, strict-Clippy, and test commands; Python-binding Rust gates; historical scientific
  controls; parser reliability; packaging; and clean-clone controls all passed locally and in GitHub Actions.

The complete machine-readable evidence is in `results/p15_*.json`, the independently derived verdict is
`results/verdict_p15.json`, and the closeout narrative is `docs/history/sessions/P15.md`.

## Compatibility and qualification

Existing `actinv-spec-1` problems and the `data-v1.0.0` files remain compatible. Generated cache files are local
derived data and are not part of a result certificate or release payload. An existing corrupt or incompatible cache
fails closed with a diagnostic instead of being trusted or silently overwritten.

ACTINV remains research-grade software. This patch is not approval for licensing, safety, waste-classification, or
regulatory use; the boundaries in `docs/QUALIFICATION.md` and the carried limitations in the v1.0.0 notes still apply.
