# Use the browser workbench

Open [ACTINV in your browser](https://actinv.avilalabs.org/) to learn the interface, edit a problem, import a FISPACT-II deck, or inspect result files without installation. A laptop or desktop with WebGL enabled works best.

The browser has the same pages and controls as the [desktop app](desktop.md), under the same names, laid out the same way: **Start**, the numbered setup steps ending in **Check & run**, and the results, which open on **Summary**. It leaves out what runs or downloads things: the **Sweep** and **Assimilation** studies, the run button and the nuclear-data download. On **Check & run** the browser hands the problem over instead: **Download problem**, run it with the desktop app or `actinv run`, then open the result here. The pages that differ only in how they choose a file are described below.

## Explore results

On **Start**, choose **Try the results tutorial** for a fictional one-hour decay example. A result opens on **Summary**: activity, decay heat, contact dose and the dominant nuclides at the selected time. On **Time history**, switch between activity, heat and inventory, click the plot to select a step, and select a nuclide to follow its history.

For a real calculation, open or drop a result JSON produced by ACTINV. Use **Compare with…** to overlay a second file. Plots use each result's recorded time and normalization; the browser does not rescale results to make them comparable. The bar at the top of each results page has the file actions and the **Computed step** slider, and the chosen step is shared by every page.

- **Nuclides** shows the tables that `actinv report` writes. Filter and sort a table, then choose **Export CSV** to download it for this step or all steps; the file is the text `actinv report` writes. When the result has no `summary`, **Add to problem** adds `summary: {}` to the problem. The browser does not run the solver, so download the problem and run it with the desktop app or the CLI, then open the new result.
- **Photons & dose** shows decay photon groups, dose rates, bremsstrahlung and dose against cooling time, and downloads the groups of the selected step as CSV. A result with no photon source links to **What to compute**.
- **Pathways** shows first products and full production routes, when the result recorded them.
- **Evidence** shows the ledger of missing data and approximations, and the certificate of inputs and data.
- **Waste class** reads waste results and edits waste specs; see [Read waste results and edit waste specs](#read-waste-results-and-edit-waste-specs).
- **Impurities** sets up an impurity screen of the open problem and reads screen results. The browser does not screen: **Save screen…** downloads `impurities.json`, which you run with the desktop app or `actinv impurities`, and **Open result…** shows the result; see [Screen a material's impurities](impurities.md#in-the-desktop-app-and-the-browser).

**Download result** saves the complete result JSON. **Export…** opens the **Export** page, which downloads a report page, charts, tables and the result and problem files as one zip; it offers the same choices as the [desktop Export page](desktop.md#export-a-result) except the waste classification.

### Read waste results and edit waste specs

**Waste class** opens an `actinv-waste-result-1` file, an intrusion-screen result, or a twin result that has `waste_classification`, and shows what the desktop page shows: for each selected step the class, status, coverage, governing table and column with its sum and margin, and the binding nuclides, a class strip against cooling time, and the earliest selected step within Class A, B and C. All of it is read from the file. The browser does not classify: the evaluator is not built for the web, so run a spec with the desktop app or `actinv waste` and open the result here. A file that is not one of these three, or is not valid JSON, shows an error message and changes nothing.

The same page opens an `actinv-waste-spec-1` file (or starts from a skeleton), lets you edit it as JSON, checks its structure and downloads it as `waste-spec.json`. The `input` member names the run result the spec reads, so keep that result next to the spec when you run it. Dropping a waste file on the window opens it. See [Component waste classes](waste.md) for the document formats.

## Edit a problem

The numbered steps **Material**, **Irradiation & cooling**, **Source & spectrum** and **What to compute** edit the problem, and **Check & run** reads it back with a **Fix** button for anything missing. Start from the iron example, **Open problem…**, or [import a deck](#import-a-fispact-ii-deck), edit it, then choose **Download problem** on **Check & run**. Open that file in the desktop app or run it with the CLI or Python interface. **Advanced JSON** exposes every option.

The browser checks the specification. It does not run the activation solver, download nuclear-data libraries, or import transport tallies. Local data paths are preserved for the installed application to resolve.

### Files you pin by name

Some options point at a data file with its SHA-256: the kerma table, the photon response, a radiological response table, the covariance sidecar and resonance files. **Choose and pin** opens your browser's file chooser, reads the file on your device and writes its SHA-256 into the problem. A browser does not reveal where a file lives, so the recorded path is the file name only. Keep each file where you run ACTINV so that name resolves against the input base folder, or edit the path before you run.

## Import a FISPACT-II deck

Choose **Import deck…** on **Start**. The dialog is the one the desktop shows: it lists every keyword of the deck with its line and a status of mapped, approximated, noted or unsupported, and **Strict** refuses a deck with any unsupported keyword. **Download report** saves the report that `actinv import-fispact` writes (see [Move a FISPACT-II deck to ACTINV](fispact-migration.md)). **Use this problem** replaces the current problem, after asking about unsaved changes.

Each file is chosen on its own: the deck, an optional fluxes file and an optional files file. Dropping a file on the window while the dialog is open takes it as the deck. A deck that irradiates but has no fluxes file converts with a flat placeholder spectrum and a required-input note; a decay-only deck needs none. Each file is read on your device, up to 32 MiB, and non-UTF-8 bytes are replaced rather than refused. The files file is read only for the names it lists, and the browser cannot follow those names to other files, so choose the fluxes file yourself.

## Keep your edits

Files are read on your device and are not uploaded. Download edits before closing or reloading the tab; the browser asks first while a problem has unsaved edits, and the workbench does not retain them between visits. Open one file at a time, up to 32 MiB.

Continue with [Your first calculation](quick-start.md) when you are ready to run the solver.
