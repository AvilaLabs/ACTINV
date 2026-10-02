# ACTINV Desktop 0.2.0-preview.1

This preview packages the ACTINV native egui desktop on the **1.4.0** solver, replacing
`0.1.0-preview.1`, which used solver 1.0.1. Every solver correction and speed-up from 1.0.1
through 1.4.0 now reaches desktop users, including the 1.2.1 corrections and the 1.4.0
trace-mode bulk-production fix, together with the desktop features added since 0.1.0. Packaging control: `protocols/desktop-packaging-v2.md`.

## Downloads

- Windows x86_64: per-user installer and portable ZIP.
- macOS: application bundle in a DMG, separately for Apple Silicon and Intel.
- Linux x86_64: AppImage and optional application-menu installation script/icon.

See [installation and first launch](../DESKTOP_INSTALL.md). Packages are unsigned by a trusted
publisher; macOS uses an ad-hoc integrity signature and is not notarized.

## What is new for desktop users

- Solver 1.4.0, with its correctness fixes and faster prepared runs.
- Desktop features added since 0.1.0: the interactive parameter sweep (P48) with certified
  screening (P68), the live certified flux slider (P69, with every position solved since P78),
  the live Pareto view (P71), the assay panel, persistent themes and scientific views,
  cancellable background jobs, and first-use setup and validation improvements.
- The automated package checks exercise the eight pages, the walkthroughs and a model solve. They
  do not drive the sweep, slider, Pareto or assay panels, which were verified by their own
  protocols at source level.
- The 1.4.0 options (gas production, the `flux` uncertainty channel, the gamma projectile) have no
  dedicated editor controls yet. They can be set through advanced JSON editing. Gamma runs also
  need a gamma library built with the CLI.

## Verification

Each platform's package was built and tested by the `desktop builds` workflow at the exact source
commit. The release lists the observed results per platform, including any platform where native
rendering was not demonstrated.

## Scope and limitations

Desktop versioning and `desktop-v…` tags are separate from the solver's version. All
[ACTINV qualification limits](../guide/qualification.md) apply. Mesh execution, transport import,
bulk library building and specialized source exports remain CLI workflows. Nuclear-data inputs are
downloaded separately and never bundled. No automatic update service is included.
