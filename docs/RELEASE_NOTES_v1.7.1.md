# ACTINV v1.7.1 — release notes

ACTINV 1.7.1 fixes resonance capture in the libraries and self-shielding tables you build from R-matrix-limited
evaluations. These include ENDF/B-VIII.1 and FENDL-3.2c tungsten, copper, Fe-54 and Fe-57.

It also fixes the cross-section sampler of `study.robustness` and adds:
- national clearance tables for Germany, the United Kingdom and Canada;
- a covariance export for the shutdown-dose source;
- options for building and applying self-shielding.

The bundled TENDL-2025 data and default results are unchanged. Everything else in the
[1.7.0 release notes](RELEASE_NOTES_v1.7.0.md) applies, including its known limitations.

Upgrade the Python package with:

```bash
python -m pip install --upgrade actinv
```

Rust CLI users can install this exact version with:

```bash
cargo install --locked --force actinv-cli --version 1.7.1
```

## Results to re-check

- **Libraries and shielding tables built from R-matrix-limited evaluations.**
  - **The defect:** from 1.5.0 to 1.7.0, `build-library` and `build-shielding` summed the eliminated capture of these
    evaluations coherently across resonance levels. Reich-Moore requires an incoherent sum.
  - **Affected ENDF/B-VIII.1 evaluations:** Ca-40, Cl-35, Cu-63, Cu-65, Fe-54, Fe-57, Pu-239, Rh-103, Si-28/29/30,
    Sr-88, V-51 and W-182/183/184/186. Thermal capture was off by factors from 0.09 (Ca-40) to 9.1 (W-183).
  - **Affected FENDL-3.2c evaluations:** Fe-54, Fe-57 and W-182/183/184/186.
  - **What to do:** rebuild any library or shielding table you built from these files with an affected release. Rerun
    any calculation that used it.
  - **Not affected:** TENDL-2025 has no evaluations in this format, so the data ACTINV downloads is unaffected.
- **`study.robustness` with `cross_section_mf33`.** The sampler now does three things differently:
  - it converges its Jacobi diagonalization per pair;
  - it moment-matches the log-normal covariance;
  - it repairs an indefinite block without discarding variances.

  Sampled bands can differ from 1.7.0, mostly where a covariance block held very large threshold-row variances or
  was not positive semi-definite.
- **Shielding tables you build.** `build-shielding` integrates the resolved range to a deeper quadrature. A table built
  with 1.7.1 can differ slightly from one built with 1.7.0 from the same files.

## What changes

- **`actinv clearance --jurisdiction de|uk|ca`** evaluates clearance against:
  - Germany's StrlSchV 2018 Anlage 4;
  - the UK's EPR 2016 Schedule 23 Part 3 Table 2;
  - Canada's NSRDR Schedule 2.

  `--route` and `--negligible` go with it. The IAEA RS-G-1.7 table stays the default. See
  [Component waste classes and impurity limits](guide/waste.md).
- **`actinv export-r2s-cov`** writes the shutdown-dose photon source with its covariance in a compact factored form.
  With it a transport code can propagate nuclear-data and flux-tally uncertainty to dose.
- **Self-shielding.**
  - `build-shielding` gains `--profile` and `--workers N`; N parallel workers give an identical table.
  - The geometry option gains `profile` (also a Profile choice in the app), so ENDF/B-VIII.1 resonance files such as
    Cu-63 are read.
- **Safer commands.** No command writes its result over one of its own inputs. `clearance --confidence` must lie in
  (0.5, 1). `optimize --resume` checks it is resuming the same problem.
- **Browser.** One toolbar for file actions and appearance, the setup steps in order in the sidebar, studies from a
  page of cards, and a Menu button on narrow windows.

## Desktop

Desktop `0.6.0-preview.1` stays on solver 1.7.0; the 1.7.1 desktop fixes reach it in the next desktop preview. See the
[desktop 0.6.0-preview.1 release](releases/desktop-v0.6.0-preview.1.md).

The [changelog](../CHANGELOG.md) has the full list of changes.
