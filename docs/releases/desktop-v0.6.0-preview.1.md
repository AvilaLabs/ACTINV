# ACTINV Desktop 0.6.0-preview.1

This preview reorganises the desktop so that it reaches most of the command line: new problems from a material library, an Export page, and pages for impurity screens, certification, optimization, comparison, waste questions, transport meshes and measurements. The desktop uses solver **1.7.0**,
the same as the CLI and Python software release; see the [1.7.0 release notes](../RELEASE_NOTES_v1.7.0.md) for the
solver changes. The theme, colours, fonts and widget style are unchanged, and results are unchanged.

## Downloads

- Windows x86_64: per-user installer and portable ZIP.
- macOS: application bundle in a DMG, separately for Apple Silicon and Intel.
- Linux x86_64: AppImage and optional application-menu installation script/icon.

See [installation and first launch](../DESKTOP_INSTALL.md). Packages are unsigned by a trusted
publisher; macOS uses an ad-hoc integrity signature and is not notarized.

## What is new for desktop users

- A clearer path. The sidebar groups pages as **Start**, **Set up**, **Results**, **Studies** and **Tools**; Studies
  and Tools fold away. **Start** asks what you want to find out and opens the page that answers it. Five setup steps
  (**Material**, **Irradiation & cooling**, **Source & spectrum**, **What to compute**, **Check & run**) are ticked as
  they become ready, and the result pages open on **Summary**. Guided tours cover setup and results.
- **New problem** opens an empty problem, and three cards fill it: a library material (19 cited compositions), a
  standard spectrum at the flux you type (D-T fusion, Watt fission, thermal Maxwellian, 1/E), and a standard schedule.
- **Check & run** lists every setting changed from its default, each with **Edit**. **Validate** and **Run** show
  their outcome in a chip beside the buttons, and **Run** reads **Running… N s** while it works. The nuclear data move
  to a **Data** page under Tools, with **Verify the data**.
- An **Export** page saves, into a new folder, a report page `report.html` to read, share or print (the problem in
  sentences, activity, decay heat, contact dose and damage at shutdown and round cooling times, charts, what
  dominates, plain-language caveats, and the waste classes when classified), SVG charts, the FISPACT-II style tables of
  `actinv report` in CSV, TSV, Markdown or text for all steps or one, `result.json` and `problem.json`, and the decay
  photons as an OpenMC or MCNP source. The result bar is **Open result…**, **Compare with…**, **Save result…**,
  **Export…**; the inventory CSV button is replaced by Export.
- New pages under **Studies** run the command-line studies from the open problem:
  - **Impurities** screens the material's impurities, with optional nuclear-data bounds shown beside the nominal
    figures (`actinv impurities`).
  - **Certify** checks limits at the edge of the nuclear-data band and ranks what to measure next (`actinv decide`).
  - **Optimize** searches for the best design within the ranges you give, under limits (`actinv optimize`).
  - **Compare** ranks combinations of library materials, standard spectra and irradiation lengths (`actinv study run`).
  - **Mesh & transport** imports a transport flux tally, activates every cell, writes the shutdown-dose source for
    OpenMC, MCNP, Serpent or ALARA, and checks which cells clear their limits; any cell opens on the result pages.
  - **Measurements**, which replaces Assimilation, fits the flux to measured activities and puts it into the schedule,
    or narrows the bands with a measured value.
- **Waste class** asks which of four waste questions you have: this result's class (with the draft intrusion screen),
  a container of several items, activities known as ranges, or how sure the class is.
- **What to compute** gains **Unmodeled error**, with **Estimate from library disagreement**.
- Small windows: content stays within the window, the Start cards wrap, and the toolbar wraps instead of covering
  **Run**.
- The browser app has the same layout and exports one zip. It does not run the solver; it sets up every study and
  reads its results, and its Start page opens two results solved ahead of time.
- Solver 1.7.0 adds `actinv impurities`, a damage table in the data catalog, `actinv new --material`, and more
  FISPACT-II import; see [What's new in 1.7](../guide/whats-new-1.7.md).

See the handbook pages for the [desktop](../guide/desktop.md#export-a-result) and the
[browser workbench](../guide/browser.md).

## Verification

Each platform's package is built and tested by the `desktop builds` workflow at the exact source
commit. The release lists the observed results per platform, including any platform where native
rendering was not demonstrated.

## Scope and limitations

Desktop versioning and `desktop-v…` tags are separate from the solver's version. All
[ACTINV qualification limits](../guide/qualification.md) apply. Nuclear-data inputs are downloaded separately and
never bundled. No automatic update service is included. The browser workbench does not run the solver.
