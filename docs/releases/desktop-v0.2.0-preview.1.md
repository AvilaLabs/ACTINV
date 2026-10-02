# ACTINV Desktop 0.2.0-preview.1

This preview packages the existing ACTINV native egui desktop on the **1.4.0** solver, replacing
`0.1.0-preview.1`, which used solver 1.0.1. The interface is unchanged. Every solver correction
and speed-up from 1.0.1 through 1.4.0 now reaches desktop users, including the 1.2.1 corrections
and the 1.4.0 trace-mode bulk-production fix. Packaging control: `protocols/desktop-packaging-v2.md`.

## Downloads

- Windows x86_64: per-user installer and portable ZIP.
- macOS: application bundle in a DMG, separately for Apple Silicon and Intel.
- Linux x86_64: AppImage and optional application-menu installation script/icon.

See [installation and first launch](../DESKTOP_INSTALL.md). Packages are unsigned by a trusted
publisher; macOS uses an ad-hoc integrity signature and is not notarized.

## What is new for desktop users

- Solver 1.4.0, with its correctness fixes and faster prepared runs.
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
