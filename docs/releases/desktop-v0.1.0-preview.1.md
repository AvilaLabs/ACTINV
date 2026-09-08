# ACTINV Desktop 0.1.0-preview.1

This is the first packaged preview of the ACTINV native egui desktop, using the
existing ACTINV 1.0.1 solver. It adds clickable desktop distribution without a
new scientific qualification claim. The GUI keeps the Avila Labs branding and
interface shown in the README.

## Downloads

- Windows x86_64: per-user installer and portable ZIP.
- macOS: application bundle in a DMG, separately for Apple Silicon and Intel.
- Linux x86_64: AppImage and optional application-menu installation script/icon.

See [installation and first launch](../DESKTOP_INSTALL.md). These preview packages
are unsigned by a trusted publisher. macOS uses an ad-hoc integrity signature and
is not notarized. Expect platform-specific first-launch notices; managed systems
may block installation. Rust and Python are not required to run the application.

## Capabilities

Prepare a material, irradiation/cooling schedule, and spectrum; download verified
data; validate and run a calculation in the background; explore linked plots and
inventory tables, comparisons, spectra, pathways, and provenance; export JSON/CSV.
Help offers interactive walkthroughs that dim the screen and highlight controls.
The Windows release executable does not open a terminal window, and platform
launchers use the Avila Labs logo.

## Verification

Candidate build and verification files accompany every platform's downloads.
They record the exact source commit, package and binary SHA-256 hashes, and the
outcome of model operations and native rendering. The shipped executables are
tested outside the checkout with a temporary synthetic P11 fixture. Windows
installation/uninstallation and macOS DMG extraction are checked on disposable
runners; Linux checks include the user-local application-menu entry.

Read `verification-*.json` for each platform. `native_rendering: false` means that
native rendering was not demonstrated on that runner, even when model execution
passed. Automated captures are not a manual test of every native dialog or
download-warning flow. The release draft will state the observed platform results.

## Scope and limitations

This is a desktop preview, not an update to the published CLI/Python packages.
Desktop versioning and `desktop-v…` tags are separate from the solver's version.
Historical P16/P17/P18 verdicts and the existing release boundary remain intact.
All [ACTINV qualification limits](../QUALIFICATION.md) and existing v1.0.1
scientific limitations still apply.

Mesh execution, transport import, bulk library building, and specialized source
exports remain CLI workflows. Nuclear-data inputs are downloaded separately and
are never bundled into these application downloads. No automatic update service
or trusted publisher signing is included in this preview.
