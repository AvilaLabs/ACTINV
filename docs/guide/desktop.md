# Use the desktop

The desktop edits a single-material problem, runs the shared ACTINV solver in the background, and displays inventories, activity, heat, photon spectra, and pathways.

Install it from the [download page](https://actinv.avilalabs.org/download/). Published preview packages and current source builds can expose different features; see [Versions and releases](releases.md).

## How the app is organized

The sidebar follows a calculation from start to answer:

- **Start** is the home page. Begin from the iron example, open a problem, or import a FISPACT-II deck. It also shows the current problem and result, and whether the standard nuclear data are installed.
- **SET UP** holds the numbered steps: **1 Material**, **2 Irradiation & cooling**, **3 Source & spectrum**, **4 What to compute** and **5 Check & run**. A tick beside a step means its part of the problem is filled in. Each step ends with **Back** and **Continue**.
- **RESULTS** opens on **Summary**, then **Time history**, **Nuclides**, **Photons & dose**, **Pathways**, **Waste class** and **Evidence**.
- **STUDIES** holds **Impurities**, **Sweep** and **Assimilation**, which run many calculations.
- **TOOLS** holds **Advanced JSON**.

## Learn without downloading nuclear data

On **Start**, choose **Try the results tutorial**. It shows a fictional one-hour decay example for learning the interface. Use the iron problem with verified data for an evaluated calculation.

## Run a problem

1. On **Start**, choose **Load iron example**, **Open problem…** or **Import deck…**. If the standard data are not installed, **Download standard data…** installs them, or run `actinv data fetch` in a terminal.
2. Walk the numbered steps. On **Material**, density is optional; a step that uses W/cm3, or geometry self-shielding without its own density, needs it. On **Source & spectrum**, choose the projectile; spectrum values are integrated group fluxes, with explicit ordering and normalization. On **What to compute**, add what to compute besides inventory, activity and decay heat, which every result includes.
3. **Check & run** reads the problem back in one sentence per step, such as "5 min irradiation, then 20 cooling steps to 57 min after shutdown". Anything missing is marked with a **Fix** button that opens the step. Under **Nuclear data**, a problem whose data files are not found, such as one written for an older data release, can switch to the installed standard data with **Use the installed standard data**. **Data files and input folder** holds the file paths; relative paths use the input folder, which opening a problem sets to its directory.
4. Choose **Run calculation**. The background task reports preparation and solving. Cancelling a calculation stops its isolated worker without publishing a partial result. The result opens on **Summary** at shutdown.

The toolbar keeps **Open problem**, **Import FISPACT-II deck…**, **Save**, **Validate** and **Run** on every page. Apply or discard advanced JSON edits before returning to structured editing.

The current desktop uses a private prepared-data cache for each calculation, so setup time can recur between runs. CLI cache behavior is described in [Data setup](data.md#prepared-calculation-cache).

### Import a FISPACT-II deck

Choose **Import FISPACT-II deck…** in the toolbar (Ctrl/Cmd+I). Pick the deck, and optionally a fluxes file and a files file, then **Convert**. The dialog lists every keyword with its line and a status of mapped, approximated, noted or unsupported, and you can filter the list. **Strict** refuses the problem when any keyword is unsupported. **Download report** saves the same report as `actinv import-fispact`; see [Move a FISPACT-II deck to ACTINV](fispact-migration.md). **Use this problem** replaces the current problem, after asking about unsaved changes.

A deck that irradiates but comes without a fluxes file converts with a flat placeholder spectrum, flagged as a required input. Replace it on **Source & spectrum** before running. Under **Required additions**, each input the deck implies but cannot supply has a button to the page where you add it.

### Choose what drives each step

On **Irradiation & cooling**, each row has a **Drive**: a flux multiplier, a wall loading (MW/m2), a power density (W/cm3) or a specific power (W/g). A flux multiplier of zero is cooling. A wall-loading step is scaled against the reference loading, `spectrum.wall_loading_MW_m2` in **Advanced JSON**. Power steps need a kerma table pinned by its SHA-256; the page shows a **Kerma table** field with **Choose and pin**. Power steps in W/cm3 also need a density on **Material**. Sweeps over wall-loading or power steps are command-line only. See [Kerma heating and power-driven steps](results.md#kerma-heating-and-power-driven-steps) for the result side.

### Choose what to compute

**What to compute** has one card for each optional calculation, with an enable box, its settings and a status that reports conflicts with other choices:

- **Step summary**, which feeds the dominant lists, element table and other [report tables](results.md#step-summary).
- **Decay photons and dose**.
- **Production pathways**, including **Full routes with closure**.
- **Regulatory tables**.
- **Geometry self-shielding**.
- **Uncertainty**, with its channels: MF=33 cross sections, half-lives, fission yields and flux tally statistics. The flux channel takes one relative error per flux group, pasted on the card; with flux alone no covariance file is needed.
- **Gas production (H and He)**, for neutron activation; it cannot be combined with uncertainty.

A result lacks an output you did not enable; the page that would show it says so and offers a link to **What to compute**.

## Explore and save results

The pages under **RESULTS** read one result. A bar at the top of each has **Open result…**, **Compare with…**, **Save result…**, **Export…** and the **Computed step** slider. The chosen step is shared, so every page shows the same step. Add another result for a comparison; check both files' physical times and normalizations.

- **Summary** is where a result opens. It gives activity, decay heat, contact dose and, when computed, damage at the selected time, each with a small curve of how it evolves, and the nuclides that dominate activity and heat. **Jump to** selects shutdown or the step nearest 1 h, 1 d, 1 y and so on after it. With a comparison loaded, each value shows its change against the comparison at the same time. Select a nuclide to follow it on **Time history**.
- **Time history** plots activity, total heat or inventory against time. Click the plot to select the nearest step, and filter and select a nuclide to follow its history. Heat remains the whole-material response.
- **Nuclides** shows the tables that `actinv report` writes: nuclides (atoms and grams, or activity and heat), step totals, dominant nuclides, elements, depletion, dose, photons, regulatory tables and the time series. Filter a table, click a header to sort it, and choose **Export CSV** for this step or all steps. The CSV is byte-identical to the `actinv report` output. A table the result cannot build gives its reason; when the result has no `summary`, **Enable and re-run** adds `summary: {}` to the problem and runs it again.
- **Photons & dose** shows decay photon groups, dose rates, bremsstrahlung and dose against cooling time. **Export photon groups (CSV)** saves the groups of the selected step. A result with no photon source links to **What to compute**.
- **Pathways** shows how a nuclide is produced. **Full routes** lists the routes for each target with the part of the inventory they explain and the residual; **First products (trace)** ranks the first products and needs trace mode.
- **Evidence** shows the ledger of incomplete inputs and approximations, and the certificate of inputs and data.
- **Waste class** classifies the loaded result as waste; see [Classify a result as waste](#classify-a-result-as-waste).
- **Export** saves what the result answers in one new folder; see [Export a result](#export-a-result).

**Save result…** keeps the complete result JSON: every step, the ledger and the certificate. Saving the problem does not save the result.

### Export a result

**Export** writes the files you tick into a new folder inside the folder you choose; it never writes over an existing folder. **Save to folder…** at the top of the page names how many files it will write.

- **Report** (`report.html`): one page to read, share or print to PDF, with the problem in sentences, activity, decay heat, contact dose and damage at shutdown and at round cooling times, charts, the nuclides that dominate and the caveats. When **Waste class** has classified this result, its classes are included. Untick **Describe the problem as it is set up now** if you changed the setup after the run.
- **Charts** (`charts/*.svg`): each quantity as the material cools, and the decay photon spectrum at shutdown.
- **Tables** (`tables/`): the FISPACT-II style tables of `actinv report`, as CSV, TSV, Markdown or fixed-width text, for all steps or only the selected one, optionally restricted to named nuclides. A table the result cannot build is greyed, with the reason on hover.
- **Files to rerun or reuse**: `result.json` and `problem.json`.

The report, charts and tables are the files `actinv report` writes; see [the CLI reference](cli.md).

### Classify a result as waste

**Waste class** builds an ordinary `actinv-waste-spec-1` document from the loaded result and classifies it with the evaluator that `actinv waste` runs, against the bundled U.S. NRC 10 CFR 61.55 tables. It describes one homogeneous component, the irradiated material, so the component mass is the material mass of the problem. Choose the waste type (general or activated metal) and the displaced volume, or, when the problem declares a material density, derive the volume from it. Select the steps to classify; all are selected at first. If retained H-3 is not in the activation inventory, declare it with its source and a whole-component activity in Bq for each selected step; otherwise it is marked not applicable.

Press **Load from decay data** to fill the table of nuclide properties (Z, half-life, alpha emission) for every nuclide with positive activity at a selected step, and for H-3 when external H-3 is declared. The values come from the decay data of the problem that is open, and load again after changing the selection, the external H-3 declaration or the problem. A nuclide without a decay record stays blank and leaves its class unknown, as the CLI does. **Override** replaces a nuclide's properties with your own entry, which the table marks as an override; the saved spec then holds only the entered values.

**Classify** runs in the background. For each selected step the page shows the class, status, coverage, the governing table and column with its sum and margin, and the binding nuclides, a strip of the classes against cooling time, and the earliest selected step within Class A, B and C. These are read from the result; the page computes nothing else. **Save spec…** writes `waste-spec.json` and the `result.json` it reads into a folder you choose (it will not replace existing files), so `actinv waste waste-spec.json` there writes the same result. **Save result…** writes the evaluator's result exactly as the CLI writes it. The classification is nominal screening, not disposal acceptance; see [Component waste classes](waste.md).

## Studies and tools

**Impurities** screens the trace elements of the open problem's material: which drive activity, decay heat, contact dose, a nuclide's activity, the clearance index or the waste class, the worst case over their specified ranges, and how far each may rise before a limit is reached. It runs `actinv impurities` in the background; see [Screen a material's impurities](impurities.md#in-the-desktop-app-and-the-browser).

**Sweep** solves a family of generated problems along one parameter. **Assimilation** folds a measured value into the uncertainty bands of the loaded result. **Advanced JSON** exposes the complete problem. Mesh runs, bulk library building, and transport-source exports use the [CLI](cli.md).

## Guided help

Choose **Help** or press **F1** for setup and result walkthroughs. Use Back, Next, or Exit, and press Escape to close a tour. Ctrl/Cmd+O opens a problem, Ctrl/Cmd+I imports a FISPACT-II deck, and Ctrl/Cmd+S saves the problem.

Read [Results](results.md) for interpretation and [Troubleshooting](troubleshooting.md) for input problems.
