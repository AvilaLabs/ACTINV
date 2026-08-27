# ACTINV P12 Amendment B — distributable crate boundary

**Date:** 2026-08-27. **Trigger:** The first P12-G5 `cargo package` verification compiled the workspace successfully
but failed while verifying `actinv-data-1.0.0.crate`. Three small tables embedded with `include_str!`/`include_bytes!`
were stored above the crate root, so Cargo correctly omitted them from the distributable archive.

This was a release-package defect, not a solver defect: ordinary workspace builds read the files successfully, and no
published crate existed. The failed candidate archive was not published or committed.

## Frozen repair

1. Move the FISPACT 709/162 group-boundary JSON and MT-product JSON into `crates/actinv-data/data/`, update production
   and independent-control paths, and keep every parsed numeric/table value unchanged. Two JSON files gain only a
   terminal newline so their generator and tracked representation agree.
2. Require the production crate package to contain all three embedded files and make `cargo package` compile the
   unpacked `actinv-data` archive successfully.
3. Give each workspace path dependency an exact `=1.0.0` registry version. Before the packages exist on crates.io,
   assemble `actinv-core` and `actinv-cli` archives without registry verification, unpack them, replace their registry
   dependencies only with the exact locally packaged archives, and compile those unpacked packages. Public publishing
   remains ordered `actinv-data`, `actinv-core`, then `actinv-cli`.
4. Require the Python wheel and source archive to include both licence texts and require the wheel to import with
   `__version__ == "1.0.0"` under the stable Python 3.9 ABI.
5. Re-run prior-verdict, dependency, self-contained-clone, CLI/Python end-to-end, P12 data-independent, and strict Rust
   controls before G5 closes.

No physics equation, input value, output tolerance, acceptance bound, or public-publishing authority changes. This
append-only repair is covered by the existing eventual **P12-CONDITIONAL** verdict.
