# ACTINV P102 Protocol — ALARA-format photon-source export (nucleide / PyNE R2S)

Opened 2026-10-02 under standing rules 1–7 at Connor's request ("go ahead and build the export and
the demo"). The hash of this file is recorded in `results/g0_p102_seals.json`; any change after G0 is
an append-only amendment.

## Goal

`nucleide` (nukehub-dev/nucleide, BSD-2, PyPI 0.16.0) and PyNE build rigorous two-step (R2S)
shutdown-dose workflows around ALARA. Both read ALARA's plain-text photon-source file and leave
activation solving to an external code: nucleide's roadmap states that "transport and activation
solving stay out of scope". Without an ALARA run, nucleide's `r2s` crate falls back to a uniform
split of each zone total across groups, which its own documentation calls a placeholder.

P102 adds `alara` to the pinned `export-source` surface (P57). ACTINV's banded
`actinv-r2s-source-1` document is then written as ALARA photon-source files, so nucleide's and
PyNE's existing readers take ACTINV output in place of ALARA output, with no change on their side.
This is the same zero-compute adapter lane as P57: no solver path is invoked.

## Reference format (what ALARA writes, what the consumers read)

- One row per (nuclide, decay time): `<nuclide>\t<time>\t<s_1>\t…\t<s_G>`. Fields are
  tab-separated; PyNE's `alara.photon_source_to_hdf5` splits on `\t`, and nucleide's
  `alara-io::photon` splits on whitespace.
- `<time>` is `shutdown` (0 s) or `<value> <unit>` with a space inside the field (`1 h`, `5000 s`).
- Each volume element's block ends with its `TOTAL` row(s). PyNE advances the element index when a
  non-`TOTAL` row follows a `TOTAL` row, and nucleide does not track element identity.
- Strengths are photon source **densities** (photons s⁻¹ cm⁻³) per group, in the ascending
  group order of the deck's `photon_source` energy bounds.
- The file carries no comment syntax that PyNE accepts: PyNE reads the first line to count groups.

## Decisions (pinned)

1. **One file per cell.** `OUT` is a directory. Each cell writes `OUT/<ordinal>_<sanitized-id>.photonSrc`
   containing exactly one row: `TOTAL\t<time>\t<densities…>`. One element per file is read correctly
   by both consumers. A multi-cell file of TOTAL-only rows would give every cell PyNE index 0, and
   nucleide cannot split it, so it is ruled out.
   - Sanitized id: ASCII `[A-Za-z0-9._-]`, any other byte replaced by `_`.
   - The ordinal prefix keeps names unique and sorted in document order.
2. **TOTAL rows only.** `actinv-r2s-source-1` carries per-nuclide photon totals but no per-nuclide
   group spectra. Per-nuclide rows would have to be invented, so none are emitted. This is a
   recorded limit.
3. **Densities.** Each group value is `photons_s_g / volume_cm3`. A cell with absent, non-finite or
   non-positive `volume_cm3` is refused by name, and the run fails closed with no partial
   directory.
4. **Group grid.** The grid is the union of `centroid_eV` values over every cell in the document,
   sorted ascending, with exact float equality defining "same group". Each cell writes the full
   grid with `0` where it has no group. Every file from one document therefore has the same group
   count G, and the grid is recorded in the index (decision 6) so the user can write matching
   ALARA/nucleide bounds. No group bounds are invented: the interchange document carries centroids
   only.
5. **Decay time.** A new required flag, `--shutdown-t-s T`, sets cooling = `step_t_s − T`.
   - Each cell's `step_t_s` must be finite and identical across cells; otherwise the run is refused.
   - Cooling < 0 is refused.
   - Cooling == 0 writes `shutdown`; otherwise `<cooling> s`, with the number in Rust `{}`
     formatting.
   - If the flag is absent, the run is refused with a message naming it. ACTINV does not guess
     where shutdown is.
6. **Provenance as a sidecar.** In-file comments would break PyNE, so `OUT/actinv-alara-index.json`
   carries:
   - emitter `actinv-source-adapter-1`, format `alara`, `input_sha256`, `step`, `step_t_s`,
     `shutdown_t_s`, `cooling_s`, the time token;
   - `units: "photons/s/cm3"`, `group_order: "ascending centroid_eV"`, `group_centroids_eV`;
   - one entry per cell: file name, id, ordinal, `bounds_cm`, `volume_cm3`, `photons_s`, both σ
     fields, the nonzero group count, and the file's sha256.

   The banded document remains the sole carrier of correlated uncertainty, as in P57.
7. **Zero-strength cells** write a row of zeros, not silence, so file counts reconcile with cells.
8. **CLI.** `actinv export-source alara R2S_SOURCE.ndjson OUT_DIR --shutdown-t-s T`. The
   OpenMC/MCNP/Serpent forms are unchanged. `OUT_DIR` must not exist or must be empty; otherwise
   the run is refused, so no file is overwritten.

## Deliverables

1. `crates/actinv-core/src/source_adapter.rs`: the `alara` writer reusing `parse_r2s`, extended to
   read `step_t_s` without changing behaviour for the three existing formats; CLI arm and usage
   line in `crates/actinv-cli/src/command.rs`.
2. `docs/INTERCHANGE_TRANSPORT.md`: an ALARA section stating decisions 1–8, plus how to write the
   matching ALARA `photon_source` bounds from the index.
3. `contrib/nucleide_r2s/`: `README.md`, `demo.py` and a test. The demo runs
   `actinv export-source alara` on an r2s source document, loads every file with nucleide's own
   photon-source reader, builds nucleide zone photon sources, tags voxels through nucleide's r2s
   API, and prints a conservation table: Σ_g density × volume against the cell's `photons_s`. The
   test is skipped with a stated reason when nucleide is not importable, following the
   `contrib/openmc_r2s` pattern.
4. Handbook: a short subsection in the R2S/workflows page of `docs/guide/` pointing to the demo.
   `CHANGELOG.md` Unreleased entry.
5. Ledger entry and `MANIFEST` refresh (`scripts/refresh_manifest.py --write --index`).

## Gates

- **G0 seal:** this file plus artifact hashes (`source_adapter.rs`, `command.rs`,
  `INTERCHANGE_TRANSPORT.md`, the P102 controls and checker) in `results/g0_p102_seals.json`.
- **G1 mechanics:** synthetic fixture with three cells (mixed group sets, one all-zero cell, one
  cell with a non-ASCII id) emits:
  - file count == cell count, one row per file, G identical across files;
  - index fields present;
  - refusals, each with a named message and no partial output:
    - missing `--shutdown-t-s`;
    - negative cooling;
    - unequal `step_t_s`;
    - missing, zero or negative volume;
    - non-empty `OUT_DIR`;
    - wrong schema;
    - missing footer.
- **G2 exactness:** an independent Python re-derivation of every token — the time token, each
  density, the union grid, file names and the index numbers — from the fixture bytes, at machine
  precision (same float formatting rule; `naive_sum` where sums are compared, per the P57 A2 note).
- **G3 consumer round-trip:** `results/p52_r2s_source.ndjson` → `alara`.
  - nucleide **0.16.0, pinned**, installed in a throwaway venv under `target/` parses every file
    with its own reader.
  - Per file: one row, nuclide `TOTAL`, `time_s` equals the declared cooling, G equals the grid
    length.
  - Σ_g strength × `volume_cm3` reproduces the cell's `photons_s` to ≤ 1e-12 relative (absolute
    0 for zero cells).
  - The nucleide r2s tagging call conserves the zone totals.
  - A tab-split parse following PyNE's `photon_source_to_hdf5` rules (G from line 1, idx stays 0)
    is applied by the checker. PyNE itself is not installed (PyTables/MOAB); that reason is
    recorded.
- **G4 determinism:** byte-identical directory contents across two runs.
- **G5 independent checker:** reparses the persisted G3 output with its own parser (no shared
  code), recomputes every number from the r2s document, and rejects planted mutations: a density
  scaled, a group swapped, the time token edited, an index sha edited, and a file removed.
- **G6 no regression:** on the P57 fixture and on `results/p52_r2s_source.ndjson`, the OpenMC, MCNP
  and Serpent emits are byte-identical before and after the change (hashes from master `24bfb1e`).
  The full `cargo test` passes for the touched crates.

**Verdict:** `P102-PASS` if G0–G6 pass. Any failing gate is a FAIL with its record and nothing is
merged.

## Limits

- TOTAL-only rows (decision 2); per-nuclide ALARA rows need per-nuclide group spectra in the
  interchange document, which is a separate protocol.
- Centroid grid, not bounds (decision 4): the user writes the ALARA deck bounds from the index.
- Verification is by the consumers' own parsers (nucleide) and a PyNE-rule parse, not by running
  ALARA or a transport code.
- nucleide is pre-alpha; the round-trip is pinned to 0.16.0. Later versions are a re-run, not a
  claim.

## Cost statement

Text emission over an NDJSON stream; the corpus document has 8 cells. No solver compute. The only
network step is `pip install nucleide==0.16.0` into a throwaway venv for G3.

## Amendments

- **A1 (post-G0, reference value; 2026-10-02):**
  - The G3/G5 corpus shutdown reference was `4000.0` s, an arbitrary value. The p52 corpus
    irradiates for `IRR_S = 300` s (`controls/p52_openmc_parity.py`), then cools 1 d, 30 d and 1 y,
    so its shutdown is at t = 300 s. G3 and G5 now use `300.0`, and the persisted
    `results/p102_alara/` files carry the physical cooling of step 4 (34214400 s after shutdown)
    instead of 34210700 s.
  - The gates were unaffected: G3 checks that the written time token equals the declared cooling,
    whatever its value.
  - `contrib/nucleide_r2s/demo.py` defaulted to `--shutdown-t-s 0` and now defaults to `300`, with
    the README stating why.
  - The synthetic G1/G2 fixture keeps its own `4000.0` (its `step_t_s` is synthetic).
  - G3, G4 and G5 were re-run and G0 resealed over the changed control artifacts. No Rust source
    changed, so no emitted byte changed for a given `--shutdown-t-s`.
  - G6 was additionally re-checked against the published v1.4.0 Linux release binary (SHA256SUMS
    verified; `source_adapter.rs` and `r2s.rs` are unchanged between v1.4.0 and `24bfb1e`). The
    OpenMC, MCNP and Serpent emits on the P57 fixture and the p52 corpus were byte-identical, 6 of
    6.
