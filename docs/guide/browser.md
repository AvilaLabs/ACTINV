# Use the browser workbench

Open [ACTINV in your browser](https://actinv.avilalabs.org/) to learn the interface, edit a problem, import a FISPACT-II deck, or inspect result files without installation. A laptop or desktop with WebGL enabled works best.

The browser has the same pages and controls as the [desktop app](desktop.md), under the same names, laid out the same way: **Start**, the numbered setup steps ending in **Check & run**, and the results, which open on **Summary**. It leaves out what runs or downloads things: the **Sweep**, **Mesh & transport** and **Measurements** studies, the run button, the nuclear-data download and **Verify the data**. The **Data** page only records the library and decay paths. On **Check & run** the browser hands the problem over instead: **Download problem**, run it with the desktop app or `actinv run`, then open the result here. Where the site serves the data for it, the browser can also [run the problem itself](#run-a-problem-in-the-browser). The pages that differ only in how they choose a file are described below.

## Explore results

On **Start**, **Iron example's result** opens what ACTINV computes for the iron example the Start page loads as a problem, and **Impurity screen of a steel** opens an impurity screen on the **Impurities** page. Both were computed ahead of time with the desktop solver and are fetched when you click. Under **Results**, **Try the results tutorial** opens a fictional one-hour decay example instead. A result opens on **Summary**: activity, decay heat, contact dose and the dominant nuclides at the selected time. On **Time history**, switch between activity, heat and inventory, click the plot to select a step, and select a nuclide to follow its history.

For a real calculation, open or drop a result JSON produced by ACTINV. Use **Compare with…** to overlay a second file. Plots use each result's recorded time and normalization; the browser does not rescale results to make them comparable. The bar at the top of each results page has the file actions and the **Computed step** slider, and the chosen step is shared by every page.

- **Nuclides** shows the tables that `actinv report` writes. Filter and sort a table, then choose **Export CSV** to download it for this step or all steps; the file is the text `actinv report` writes. When the result has no `summary`, **Add to problem** adds `summary: {}` to the problem. The browser does not run the solver, so download the problem and run it with the desktop app or the CLI, then open the new result.
- **Photons & dose** shows decay photon groups, dose rates, bremsstrahlung and dose against cooling time, and downloads the groups of the selected step as CSV. A result with no photon source links to **What to compute**.
- **Pathways** shows first products and full production routes, when the result recorded them.
- **Evidence** shows the ledger of missing data and approximations, and the certificate of inputs and data.
- **Waste class** reads waste results, edits waste specs and sets up the container, ranges and how-sure questions; see [Read waste results and edit waste specs](#read-waste-results-and-edit-waste-specs).
- **Impurities** sets up an impurity screen of the open problem and reads screen results. The browser does not screen: **Save screen…** downloads `impurities.json`, which you run with the desktop app or `actinv impurities`, and **Open result…** shows the result; see [Screen a material's impurities](impurities.md#in-the-desktop-app-and-the-browser).

**Download result** saves the complete result JSON. **Export…** opens the **Export** page, which downloads a report page, charts, tables, a transport photon source and the result and problem files as one zip; it offers the same choices as the [desktop Export page](desktop.md#export-a-result) except the waste classification.

### Read waste results and edit waste specs

**Waste class** opens an `actinv-waste-result-1` file, an intrusion-screen result, or a twin result that has `waste_classification`, and shows what the desktop page shows: for each selected step the class, status, coverage, governing table and column with its sum and margin, and the binding nuclides, a class strip against cooling time, and the earliest selected step within Class A, B and C. All of it is read from the file. The browser does not classify: the evaluator is not built for the web, so run a spec with the desktop app or `actinv waste` and open the result here. A file that is not one of these three, or is not valid JSON, shows an error message and changes nothing.

The same page opens an `actinv-waste-spec-1` file (or starts from a skeleton), lets you edit it as JSON, checks its structure and downloads it as `waste-spec.json`. The `input` member names the run result the spec reads, so keep that result next to the spec when you run it. Dropping a waste file on the window opens it. The other three waste questions of the [desktop page](desktop.md#classify-a-result-as-waste) are set up here too: a container of items, activity ranges, and how sure the class is. The browser has no decay data, so enter each nuclide's properties in the table. Each form downloads its spec (`package-spec.json`, `bounds-spec.json`, or `uncertainty.json` with the `problem.json` it samples) to run with the CLI, and **Open its result…** shows the answer. See [Component waste classes](waste.md) for the document formats.

## Edit a problem

The numbered steps **Material**, **Irradiation & cooling**, **Source & spectrum** and **What to compute** edit the problem, and **Check & run** reads it back with a **Fix** button for anything missing. Start from the iron example, **Open problem…**, or [import a deck](#import-a-fispact-ii-deck), edit it, then choose **Download problem** on **Check & run**. Open that file in the desktop app or run it with the CLI or Python interface. **Advanced JSON** exposes every option.

The browser checks the specification. It does not run the activation solver, download nuclear-data libraries, or import transport tallies. Local data paths are preserved for the installed application to resolve.

### Files you pin by name

Some options point at a data file with its SHA-256: the kerma table, the photon response, a radiological response table, the covariance sidecar and resonance files. **Choose and pin** opens your browser's file chooser, reads the file on your device and writes its SHA-256 into the problem. A browser does not reveal where a file lives, so the recorded path is the file name only. Keep each file where you run ACTINV so that name resolves against the input base folder, or edit the path before you run.

## Run a problem in the browser

Where the site serves the browser's nuclear data, **Check & run** has a **Run it here** panel with a **Run in browser** button. ACTINV then solves the problem in the page, in the background, with the standard TENDL-2025 library (709 groups) and the ENDF/B-VIII.0 and JEFF-3.3 decay data. The problem and its result stay on your computer. The result opens on **Summary**, and **Download result** keeps it. **Cancel** stops the run.

The browser does not download the whole library. It fetches the reactions the material can reach in two steps, with the decays between them, and the decay data they lead to, and solves. Then it solves again with more reactions (three, four, six and so on), fetching only what each round adds, until two rounds in a row give the same answer. Meanwhile the progress line says **Checking with more reactions**. In the tests below, most problems settled at four reactions after about 34 MB of data. A material burnt for years in a strong thermal flux needs most of the library: tungsten at 1e15 n/cm²/s for ten years fetched about 200 MB and took about three minutes.

The browser keeps the files it fetches, so running the problem again, or another one with the same materials, fetches only what it has not kept, even after a restart. A kept file is used only if it still matches the library's record of it, and is otherwise fetched again. Clearing the site's data in your browser's settings removes them. Pages served without HTTPS (other than from your own computer) cannot keep files and fetch everything each run.

Some problems run only on your computer, and the panel says why:

- a library or decay data other than the standard ones;
- fission yields;
- displacement damage;
- self-shielding;
- a schedule whose steps change library.

Download those and run them with the desktop app or `actinv run`.

How close browser runs come to the desktop, from the registered test P196A:

> In the browser, ACTINV solves a problem with the standard TENDL-2025 library, fetching only the reactions its material can reach and the decay data they lead to, and following more reactions, round by round, until the result no longer changes.
>
> On 175 problems (19 materials under four standard spectra at 1e14 n/cm²/s for a year and at 1e15 for ten years, under a thermal spectrum at 3e14 for three years, and 4 of the examples), its total activity, decay heat and photon emission at every step were within 9e-07 of the whole library's (of 1e-10 of its largest value, where it had fallen below that), and every nuclide carrying 0.1% of the activity within 5e-15.
>
> Run in Chromium, the browser's result was identical to the native one on all 15 problems tried; a problem fetched a median of 34 MB of data (at most 209 MB).

Agreement outside those problems is not measured, and that the rounds settle does not prove a deeper chain could not matter elsewhere. For a result you will rely on, run the problem with the desktop app or the CLI.

## Import a FISPACT-II deck

Choose **Import deck…** on **Start**. The dialog is the one the desktop shows: it lists every keyword of the deck with its line and a status of mapped, approximated, noted or unsupported, and **Strict** refuses a deck with any unsupported keyword. **Download report** saves the report that `actinv import-fispact` writes (see [Move a FISPACT-II deck to ACTINV](fispact-migration.md)). **Use this problem** replaces the current problem, after asking about unsaved changes.

Each file is chosen on its own: the deck, an optional fluxes file and an optional files file. Dropping a file on the window while the dialog is open takes it as the deck. A deck that irradiates but has no fluxes file converts with a flat placeholder spectrum and a required-input note; a decay-only deck needs none. Each file is read on your device, up to 32 MiB, and non-UTF-8 bytes are replaced rather than refused. The files file is read only for the names it lists, and the browser cannot follow those names to other files, so choose the fluxes file yourself.

## Set up studies in the browser

**Certify**, **Optimize** and **Compare** (see [Certify, optimize and compare designs](studies.md)) have the same inputs and checks as in the desktop, but the browser does not solve them. Their main button becomes a download, and the page notes the command that runs it:

- **Certify**: **Download the decision file** gives `decision.json`; run `actinv decide decision.json verdict.json` where the nuclear data and the covariance file are installed.
- **Optimize**: **Download the search file** gives `search.json` and `base_spec.json`. Keep them in one folder and run `actinv optimize search.json out`. **Use this design** is a desktop button.
- **Compare**: **Download the study file** gives `compare.json`; run `actinv study run compare.json out`.

**Maintenance** plans in the browser itself, as in the [desktop](desktop.md#plan-maintenance-after-shutdown): the run results and dose-rate tables it adds are read on your device, and **Save result…** downloads `maintenance-result.json`.

The starting cards on **Material**, **Source & spectrum** and **Irradiation & cooling** work in the browser, as does **New problem** on **Start**; see [Start a new problem](desktop.md#start-a-new-problem).

## Keep your edits

Files are read on your device and are not uploaded. Download edits before closing or reloading the tab; the browser asks first while a problem has unsaved edits, and the workbench does not retain them between visits. Open one file at a time, up to 32 MiB.

Continue with [Your first calculation](quick-start.md) when you are ready to run the solver.
