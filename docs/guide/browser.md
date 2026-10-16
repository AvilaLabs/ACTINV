# Use the browser workbench

Open [ACTINV in your browser](https://actinv.avilalabs.org/) to learn the interface, edit a problem, import a FISPACT-II deck, or inspect result files without installation. A laptop or desktop with WebGL enabled works best.

The browser has the same pages and controls as the [desktop app](desktop.md), under the same names. It leaves out the pages that run or download things: **Sweep** and **Assimilation**, the run button and the nuclear-data download. The pages that differ only in how they choose a file are described below.

## Explore results

Choose **Try the results tutorial** for a fictional one-hour decay example. Switch between activity, heat, and inventory on **Charts**, click the plot to select a step, and select a nuclide to follow its history.

For a real calculation, open or drop a result JSON produced by ACTINV. Use **Compare result** to overlay a second file. Plots use each result's recorded time and normalization; the browser does not rescale results to make them comparable. The bar at the top of each results page has the file actions and the **Computed step** slider, and the chosen step is shared by every page.

- **Tables** shows the tables that `actinv report` writes. Filter and sort a table, then choose **Export CSV** to download it for this step or all steps; the file is the text `actinv report` writes. When the result has no `summary`, **Enable and re-run** adds `summary: {}` to the problem. The browser does not run the solver, so download the problem and run it with the desktop app or the CLI, then open the new result.
- **Photons & dose** shows decay photon groups, dose rates, bremsstrahlung and dose against cooling time, and downloads the groups of the selected step as CSV. A result with no photon source links to **Outputs**.
- **Pathways** shows first products and full production routes, when the result recorded them.
- **Ledger & certificate** shows missing data, diagnostics and the calculation details.

An inventory CSV download contains the selected time step. A result JSON download retains the complete imported result.

## Edit a problem

**Overview & data**, **Material**, **Irradiation & cooling**, **Spectrum** and **Outputs** edit the problem. Start from the iron example, **Open problem**, or [import a deck](#import-a-fispact-ii-deck), edit it, then choose **Download problem**. Open that file in the desktop app or run it with the CLI or Python interface. **Advanced JSON** exposes every option.

The browser checks the specification. It does not run the activation solver, download nuclear-data libraries, or import transport tallies. Local data paths are preserved for the installed application to resolve.

### Files you pin by name

Some options point at a data file with its SHA-256: the kerma table, the photon response, a radiological response table, the covariance sidecar and resonance files. **Choose and pin** opens your browser's file chooser, reads the file on your device and writes its SHA-256 into the problem. A browser does not reveal where a file lives, so the recorded path is the file name only. Keep each file where you run ACTINV so that name resolves against the input base folder, or edit the path before you run.

## Import a FISPACT-II deck

Choose **Import FISPACT-II deck…** on **Overview & data**. The dialog is the one the desktop shows: it lists every keyword of the deck with its line and a status of mapped, approximated, noted or unsupported, and **Strict** refuses a deck with any unsupported keyword. **Download report** saves the report that `actinv import-fispact` writes (see [Move a FISPACT-II deck to ACTINV](fispact-migration.md)). **Use this problem** replaces the current problem, after asking about unsaved changes.

Each file is chosen on its own: the deck, an optional fluxes file and an optional files file. Dropping a file on the window while the dialog is open takes it as the deck. A deck that irradiates but has no fluxes file converts with a flat placeholder spectrum and a required-input note; a decay-only deck needs none. Each file is read on your device, up to 32 MiB, and non-UTF-8 bytes are replaced rather than refused. The files file is read only for the names it lists, and the browser cannot follow those names to other files, so choose the fluxes file yourself.

## Keep your edits

Files are read on your device and are not uploaded. Download edits before closing or reloading the tab; the workbench does not retain them between visits. Open one file at a time, up to 32 MiB.

Continue with [Your first calculation](quick-start.md) when you are ready to run the solver.
