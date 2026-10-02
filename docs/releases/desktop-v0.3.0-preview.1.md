# ACTINV Desktop 0.3.0-preview.1

This preview adds an optional Avila Labs account sign-in to the ACTINV desktop and to the browser
workbench, together with a tool launcher and a new application icon. The solver is unchanged: the
desktop still uses solver **1.4.0**, the same as the CLI and Python software release.

## Downloads

- Windows x86_64: per-user installer and portable ZIP.
- macOS: application bundle in a DMG, separately for Apple Silicon and Intel.
- Linux x86_64: AppImage and optional application-menu installation script/icon.

See [installation and first launch](../DESKTOP_INSTALL.md). Packages are unsigned by a trusted
publisher; macOS uses an ad-hoc integrity signature and is not notarized.

## What is new for desktop users

- Optional Avila Labs account sign-in. In the browser workbench, use the **Sign in** button or the
  first-visit prompt. In the desktop, sign-in is a device sign-in that you approve in your web
  browser. One sign-in covers ACTINV, Converra and OpenBNCT.
- Signing in is optional and every ACTINV feature works without an account. Nothing from your work
  is sent: no problem inputs, results or files. The desktop stores its sign-in token in
  `~/.config/avila/credentials.json`, and signing out revokes it.
- A tool launcher for moving between the Avila Labs tools.
- A new application icon and tool marks, including the window icon, favicon and installer icons.
- Solver 1.4.0 and all other features are unchanged from 0.2.0-preview.1.

See the handbook page [Avila Labs account (optional)](../guide/account.md). The account service's
[privacy notice](https://api.avilalabs.org/privacy) and [terms](https://api.avilalabs.org/terms)
apply to the account.

## Verification

Each platform's package is built and tested by the `desktop builds` workflow at the exact source
commit. The release lists the observed results per platform, including any platform where native
rendering was not demonstrated.

## Scope and limitations

Desktop versioning and `desktop-v…` tags are separate from the solver's version. All
[ACTINV qualification limits](../guide/qualification.md) apply. Nuclear-data inputs are downloaded
separately and never bundled. No automatic update service is included.
