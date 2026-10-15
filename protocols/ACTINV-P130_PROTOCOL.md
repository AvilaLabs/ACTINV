# P130 — All FISPACT-II group structures and a high-energy residual-production splice

Registered 2026-10-15, before any P130 result is produced, under the owner-approved FISPACT-II parity plan
(2026-10-15). Base commit: `2f3daa4`. Tracker rows: group structures, energies to 1 GeV, USESPALLATION.

## Change under test

1. The eleven FISPACT-II group structures ACTINV lacked (66, 69, 100, 172, 175, 211, 315, 351, 586, 616 and 1102) are
   vendored verbatim from FISPACT's `ebins_N` files (`~/nuclear-data/fispact-tendl2017/ebins.tar.bz2`), with
   per-file SHA-256 provenance. They are usable as spectrum structures, in library builds and with the `xN`
   refinement.
2. `build-library --spallation TABLE.json [--splice-energy-MeV E]`: a user-supplied residual-production table
   (`actinv-spallation-residuals-1`) replaces or adds reaction groups above the splice energy. The splice snaps up
   to a library boundary. Rows not named in the table and all groups below the splice are untouched, and the merge
   is recorded in the library index. No spallation data is shipped.

Default library builds and runs are unchanged. `builder_fingerprint()` changes because builder sources changed.

## Gates

Checker: `controls/check_p130.py`, run by the coordinator under the 6 GiB cgroup.

- **G0.** This protocol's SHA-256 is in `protocols/protocol_hash.txt` in a commit before any P130 evidence.
- **G1.** fmt, clippy with `-D warnings`, and the workspace tests, including the new `groups.rs`, `spallation.rs`,
  `spec.rs` and `builder.rs` unit tests.
- **G2, boundary fidelity.** For all 13 structures, the checker extracts `ebins_N` from the archive itself
  (verifying the archive SHA-256) and compares every boundary with the vendored JSON, bit for bit after the same
  decimal parse.
- **G3, unchanged by default.**
  - `examples/fns_fe_5min.json` gives byte-identical results (apart from `ms`) under reference and candidate.
  - Rebuilding a small TENDL-2025 709-group neutron library for one element, with and without the candidate,
    gives identical NPZ arrays; only the index's builder fingerprint may differ.
- **G4, splice.** Splicing a synthetic residual table into that small library leaves every row byte-identical below
  the effective splice. Above it, replaced groups equal the table values and added rows are zero below. The index
  records the requested and effective splice energies and the table SHA-256.
- **G5, new structures in use.** A spectrum on `fispact-1102` (an equal-lethargy test spectrum) validates and
  collapses. Its one-group rate against a library rebuilt on 1102 groups agrees with the 709-group rate for the
  same physical spectrum within a stated tolerance (report-only, with the tolerance justified in the result).
- **G6.** CI green on the pushed commit, with the committed run-output baselines unchanged.

A gate that fails is recorded as P130-FAIL with its evidence; repairs need an amendment registered first.

## Not claimed

No spallation evaluation is provided or validated. The TENDL neutron sublibrary stops at 200 MeV, and data above
it come only from the user's table.
