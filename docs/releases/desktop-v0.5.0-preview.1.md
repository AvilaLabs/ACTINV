# ACTINV Desktop 0.5.0-preview.1

This preview adds a Waste class page to the desktop and waste views to the browser workbench. The desktop uses
solver **1.6.1**, the same as the CLI and Python software release; see the
[1.6.0 release notes](../RELEASE_NOTES_v1.6.0.md) for the solver changes, including the new rate-pruning rule, and the
[1.6.1 release notes](../RELEASE_NOTES_v1.6.1.md) for the pathway fix. The
look is unchanged: the theme, colours, fonts and widget style stay as they were.

## Downloads

- Windows x86_64: per-user installer and portable ZIP.
- macOS: application bundle in a DMG, separately for Apple Silicon and Intel.
- Linux x86_64: AppImage and optional application-menu installation script/icon.

See [installation and first launch](../DESKTOP_INSTALL.md). Packages are unsigned by a trusted
publisher; macOS uses an ad-hoc integrity signature and is not notarized.

## What is new for desktop users

- Fourteen pages. **Waste class**, in the EXPLORE group, builds an ordinary `actinv-waste-spec-1` document from the
  loaded result and classifies it with the evaluator that `actinv waste` runs, against the bundled U.S. NRC
  10 CFR 61.55 tables (P144).
  - It describes one homogeneous component, the irradiated material. Choose the waste type and the displaced volume,
    or derive the volume from the material density; declare retained H-3 when it is not in the activation inventory.
  - **Load from decay data** fills each nuclide's Z, half-life and alpha emission from the open problem's decay data;
    **Override** replaces them with your own values, marked as overrides.
  - For each selected step the page shows the class, status, coverage, governing table and column with its sum and
    margin, and the binding nuclides, with a class-by-cooling-time strip and the earliest step within each class.
  - **Save spec…** writes the spec and the result it reads, so `actinv waste` reproduces the classification;
    **Save result…** writes the result exactly as the CLI does.
- The browser workbench opens waste results, intrusion-screen results and twin results with a waste classification,
  shows the same tables, and opens, edits and downloads waste specs. It never classifies and never runs the solver.
- Everything from 0.4.0-preview.1 is unchanged.

See the handbook pages for the [desktop](../guide/desktop.md#classify-a-result-as-waste), the
[browser workbench](../guide/browser.md) and [component waste classes](../guide/waste.md).

## Verification

Each platform's package is built and tested by the `desktop builds` workflow at the exact source
commit. The release lists the observed results per platform, including any platform where native
rendering was not demonstrated.

## Scope and limitations

Desktop versioning and `desktop-v…` tags are separate from the solver's version. All
[ACTINV qualification limits](../guide/qualification.md) apply. The waste classification is nominal screening, not
disposal acceptance. Nuclear-data inputs are downloaded separately and never bundled. No automatic update service is
included. The browser workbench does not run the solver.
