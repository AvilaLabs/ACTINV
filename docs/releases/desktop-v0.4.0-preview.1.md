# ACTINV Desktop 0.4.0-preview.1

This preview replaces the desktop's page layout with a thirteen-page interface that exposes the
FISPACT-II parity features of the 1.5.0 solver, and gives the browser workbench the same pages. The
desktop uses solver **1.5.0**, the same as the CLI and Python software release; see the
[1.5.0 release notes](../RELEASE_NOTES_v1.5.0.md) for the solver changes. The look is unchanged: the
theme, colours, fonts and widget style stay as they were.

## Downloads

- Windows x86_64: per-user installer and portable ZIP.
- macOS: application bundle in a DMG, separately for Apple Silicon and Intel.
- Linux x86_64: AppImage and optional application-menu installation script/icon.

See [installation and first launch](../DESKTOP_INSTALL.md). Packages are unsigned by a trusted
publisher; macOS uses an ad-hoc integrity signature and is not notarized.

## What is new for desktop users

- Thirteen pages in three groups.
  - **CALCULATION:** Overview & data, Material, Irradiation & cooling, Spectrum and **Outputs**. Outputs
    is one place for everything a run computes: step summary and depletion, decay photons and dose,
    production pathways and routes, regulatory tables, geometry self-shielding and uncertainty. It
    shows the solver's own message when two choices conflict.
  - **EXPLORE:** **Charts** (the former Results page), **Tables**, **Photons & dose**, **Pathways**
    (first products and full routes, with closure) and Ledger & certificate, with one shared result
    bar.
  - **TOOLS:** Sweep, Assimilation and Advanced JSON.
- **Outputs** also has controls for the 1.4 options that needed JSON editing before: gas production
  and each uncertainty channel (MF=33 cross sections, half-lives, fission yields and flux tally
  statistics, with the per-group relative errors pasted on the card). The gamma projectile is on
  Overview & data.
- **Tables** shows the `actinv report` tables for each step. Its CSV export is identical to the
  command-line output.
- **Import FISPACT-II deck** in the toolbar (Ctrl/Cmd+I) converts an input deck and shows the importer's
  per-keyword report, with a Strict option and the required additions linked to the page that fixes
  them. An irradiating deck without a fluxes file gets a flat placeholder spectrum, flagged as a
  required input; the command-line importer refuses such a deck instead.
- **Irradiation & cooling** gains a Drive for each step: a flux multiplier, a wall loading, or a power
  in W/cm³ or W/g. The kerma-table and density requirements of the power and wall-loading drives are
  shown next to the controls. **Material** gains an optional density.
- Fixed: with no `options.outputs` the output switches showed three outputs while the solver computed
  five, and the first click turned photons and pathways off. Wall-loading and power steps were labelled
  Cooling and could not be edited.
- The browser workbench has the same pages and controls. It still never runs the solver. Files pinned in
  the browser are recorded by file name and SHA-256.
- Optional account sign-in, the tool launcher and the application icon are as in 0.3.0-preview.1.

See the handbook pages for the [desktop](../guide/desktop.md), the [browser workbench](../guide/browser.md)
and [migrating from FISPACT-II](../guide/fispact-migration.md).

## Verification

Each platform's package is built and tested by the `desktop builds` workflow at the exact source
commit. The release lists the observed results per platform, including any platform where native
rendering was not demonstrated.

## Scope and limitations

Desktop versioning and `desktop-v…` tags are separate from the solver's version. All
[ACTINV qualification limits](../guide/qualification.md) apply. Nuclear-data inputs are downloaded
separately and never bundled. No automatic update service is included. The browser workbench does not run
the solver.
