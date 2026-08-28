# ACTINV P13 — verified data distribution and first-run setup

Opened 2026-08-28 after P12 closure, at the maintainer's request. P13 is a distribution and usability phase. It does
not change the activation builder, solver, physics models, numerical tolerances, nuclear-data values, or any prior
validation verdict.

## Scope

1. Ship a strict, versioned data catalog with the standalone `actinv` command.
2. Add `actinv data list`, `actinv data fetch`, `actinv data verify`, and `actinv data manifest` so a new user can
   install the recommended data with one command and receive ready-to-copy problem-spec paths.
3. Publish the exact P10 TENDL-2025 neutron/proton/deuteron/alpha activation libraries and P11 neutron covariance
   sidecar as immutable, SHA-256-pinned release assets, without committing them to Git.
4. Fetch the primary ENDF/B-VIII.0 and fallback JEFF-3.3 decay sublibraries from their official hosts. Their official
   archives and extracted payloads are both pinned and verified; ACTINV does not rehost those files.
5. Add a user-facing quick start, data notice, release preparation script, deterministic independent control, and CI
   coverage for the distribution contract.

PyPI/crates.io publication, a new physics benchmark, finite-dilution self-shielding, a FISPACT executable run, and
changes to any evaluated value remain outside P13.

## Normative distribution contract

1. The catalog schema is `actinv-data-catalog-1`. It is embedded in the binary and printed byte-for-byte by
   `actinv data manifest`; ordinary users do not acquire mutable catalog instructions from the network.
2. Catalog, bundle and file fields reject unknown values. Bundle IDs and destination paths are unique and bounded;
   absolute paths, parent traversal, empty components, platform prefixes and symlinked destination components fail.
3. Every installed payload has a positive declared byte count and lowercase SHA-256. Downloads stream to a sibling
   temporary file with an `expected + 1` byte ceiling. A destination is published atomically only after its size and
   hash match. Existing valid files are reused; an existing invalid file is never replaced without explicit
   `--force`, and even then replacement happens only after the new payload verifies.
4. Production download URLs are HTTPS. Direct release assets must have identical download and installed identities.
   An archive member requires two independent identities: archive bytes/hash and extracted bytes/hash. Only the exact
   declared regular member is read; whole-archive extraction and archive-provided destination paths are forbidden.
5. `tendl-2025-neutron` is the default bundle. Separate neutron-covariance, proton, deuteron and alpha bundles prevent
   users from downloading data they do not need. Shared verified decay and notice files are reused across bundles.
6. Activation and covariance index filenames remain adjacent to their NPZ payloads under the production naming rule.
   The catalog records their exact P10/P11 identities and the independent control matches those identities to the
   append-only evidence already in `results/g7_p10_builds.json` and `results/g6_p11_complete.json`.
7. TENDL-2025 source headers declare CC-BY-4.0. Release assets retain attribution, source URLs, source-archive hashes,
   ACTINV transformation details, the two recorded Pb-208 source repairs, builder identities and a link to the licence.
   ACTINV's MIT/Apache-2.0 software licence is explicitly not applied to third-party nuclear data.
8. Generated libraries, raw evaluations, decay payloads, covariance payloads, caches and credentials remain outside
   Git. The preparation script accepts explicit external paths, re-verifies every expected identity, and stages only
   the named release files, catalog and notice.

## Gates

**G1 — strict catalog.** Rust tests and an independent Python control reject duplicate IDs/roles/paths, unsafe paths,
bad schemas, unknown fields, non-HTTPS production URLs, malformed hashes, zero/oversized identities, inconsistent
direct downloads and invalid activation/covariance index naming. The embedded production catalog matches all P10/P11
evidence identities exactly.

**G2 — verified atomic fetch.** Reader-injected Rust regressions cover a valid direct payload, reuse of a valid local
file, truncation, excess bytes, bad hash, an existing invalid destination, explicit forced repair, and download failure.
No failing case publishes or damages a destination. A synthetic one-member ZIP independently exercises archive and
extracted-payload verification; an unexpected member or payload fails closed.

**G3 — public CLI contract.** `data list`, `data manifest`, `data fetch`, and `data verify` have regression coverage;
the default is neutron; output identifies the catalog version, installed root, exact files and a valid JSON problem
fragment. Bad commands/options/bundle IDs exit nonzero with bounded diagnostic text.

**G4 — release payload.** The staging script accepts the exact external P10/P11 files, produces the catalog-named
assets, and emits a sorted SHA-256/size inventory. Every staged asset re-matches both the embedded catalog and prior
evidence. No raw evaluation or unlisted file enters the stage.

**G5 — first-run documentation.** README leads with install, one-command data setup, a minimal calculation and the
meaning of the output before builder internals. `docs/DATA.md`, the release checklist, changelog and data notice explain
versions, storage, attribution, verification, offline/manual alternatives and the distinction between software and
data licences.

**G6 — repository regression.** The four required Rust gates pass with all features and targets, the Python binding
Rust gates pass, the independent P13 control reproduces its committed result, the repository remains self-contained,
and all existing data-independent CI controls remain green. The final session records commit, workflow and public
release identities separately; a release upload cannot retroactively determine a source-code gate.

## Closure

P13 closes only after G1–G6 are checker-derived, the protocol hash and any repair amendments are recorded, and the
source commit is pushed. Public release assets are an external distribution record and never substitute for committed
scientific evidence.
