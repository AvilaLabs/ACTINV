# Use the desktop

The desktop edits a single-material problem, runs the shared ACTINV solver in the background, and displays inventories, activity, heat, photon spectra, and pathways.

Install it from the [download page](https://actinv.avilalabs.org/download/). Published preview packages and current source builds can expose different features; see [Versions and releases](releases.md).

## How the app is organized

The sidebar follows a calculation from start to answer:

- **Start** is the home page. Begin a **New problem**, load the iron example, open a problem, or import a FISPACT-II deck. Under **What do you want to find out?** each question opens the page that answers it, such as **Certify** for whether a design meets its limits. Start also shows the current problem and result, and whether the standard nuclear data are installed.
- **SET UP** holds the numbered steps: **1 Material**, **2 Irradiation & cooling**, **3 Source & spectrum**, **4 What to compute** and **5 Check & run**. A tick beside a step means its part of the problem is filled in. Each step ends with **Back** and **Continue**.
- **RESULTS** opens on **Summary**, then **Time history**, **Nuclides**, **Photons & dose**, **Pathways**, **Waste class**, **Evidence** and **Export**. These pages are dimmed until a result is loaded.
- **STUDIES** holds **Impurities**, **Certify**, **Optimize**, **Sweep**, **Mesh & transport**, **Compare** and **Assimilation**, which run many calculations. The group is folded away until you open it or one of its pages is current. [Certify, optimize and compare designs](studies.md) describes **Certify**, **Optimize** and **Compare**.
- **TOOLS** holds **Data** and **Advanced JSON**, folded the same way. See [Check the nuclear data](#check-the-nuclear-data).

## Learn without downloading nuclear data

On **Start**, choose **Try the results tutorial**. It shows a fictional one-hour decay example for learning the interface. Use the iron problem with verified data for an evaluated calculation.

## Run a problem

1. On **Start**, choose **New problem**, **Load iron example**, **Open problem…** or **Import deck…**. If the standard data are not installed, **Download standard data…** installs them, or run `actinv data fetch` in a terminal.
2. Walk the numbered steps. On **Material**, density is optional; a step that uses W/cm3, or geometry self-shielding without its own density, needs it. On **Source & spectrum**, choose the projectile; spectrum values are integrated group fluxes, with explicit ordering and normalization. On **What to compute**, add what to compute besides inventory, activity and decay heat, which every result includes.
3. **Check & run** reads the problem back in one sentence per step, such as "5 min irradiation, then 20 cooling steps to 57 min after shutdown". Anything missing is marked with a **Fix** button that opens the step. Under **Settings you changed** it lists every setting that differs from its default, each with an **Edit** button that opens the page that sets it. Under **Nuclear data** it says whether the data the problem names are installed; **Edit** opens the **Data** page.
4. Choose **Run calculation**. The background task reports preparation and solving. Cancelling a calculation stops its isolated worker without publishing a partial result. The result opens on **Summary** at shutdown.

The toolbar keeps **Open problem**, **Import FISPACT-II deck…**, **Save**, **Validate** and **Run** on every page. Apply or discard advanced JSON edits before returning to structured editing.

### Start a new problem

**New problem** on **Start** opens a blank problem on **Material**. The material, the schedule and the source are empty and nothing is filled in for you: each step is ticked once its part is complete, and **Check & run** marks what is still missing. The three starting cards below fill the empty parts quickly. Each card is open while its part is empty and folds under the page's own controls once it is filled.

**Material** has **Start from a library material**. Choose a material from the list, grouped by category, then **Use this material**. The library holds 19 cited compositions of common reactor and fusion materials: steels (EUROFER97 at its specification maxima, EUROFER97-3 as one measured heat, and stainless 304, 304L and 316), Inconel 718, Zircaloy-4, aluminium 6061, ordinary concrete, water, reactor graphite, tungsten, tantalum, niobium, titanium grade 2, copper, beryllium S-65, V-4Cr-4Ti and Pb-15.7Li. Below the list the page shows the largest elements, what kind of composition it is, the density and the citation. The kinds differ:

- **Nominal composition**: generic values, not a real heat.
- **Specification maxima**: trace elements at their allowed maximum, the main element the balance. Trace activation is overstated by design.
- **Measured heat**: a published analysis of one batch, not the specification.

The entry sets the composition in weight percent and sets the density from the same source, or removes the density when the source gives none. It names an untitled problem after the material. On a filled table the card is the folded **Library materials** heading, and its button reads **Replace the composition with this one**. Check a composition against your own material certificate before relying on it; each entry's note names the trace elements it leaves out. `actinv new` does the same from a terminal; see [Command line](cli.md#create-check-and-solve).

**Source & spectrum** has **Start from a standard spectrum**. Pick a shape, type the **Total flux (n/cm²/s)** and choose **Use this spectrum**. The shapes are generic textbook shapes on the 709-group structure, not a facility's spectrum; for a real facility import its group values from transport. The card appears for neutron problems only. Once the spectrum has flux, it is the folded **Standard spectra** heading and its button reads **Replace the group values with this spectrum**.

- **D-T fusion (14.1 MeV)**: all flux in the group that contains 14.1 MeV.
- **Fission (Watt)**: a Watt spectrum for U-235 thermal fission (a = 0.988 MeV, b = 2.249 per MeV).
- **Thermal (Maxwellian)**: a Maxwellian at kT = 0.0253 eV.
- **Slowing down (1/E)**: flux per unit energy proportional to 1/E from 0.5 eV to 1 MeV, zero outside.

**Irradiation & cooling** has **Start with a standard schedule**. Type the length in **Irradiate for** (30 d or 2 y; it starts at 1 y) and choose **Use this schedule**. It irradiates at the source's full flux for that time, then cools with results at 1 h, 1 d, 30 d, 1 y and 10 y after shutdown. Afterwards the card is the folded **Standard schedule** heading, and its button, **Replace the steps with this schedule**, overwrites every step. Edit the steps below it as usual.

### Read the outcome beside Validate and Run

**Validate** and **Run** show their outcome in a small chip beside the buttons, so you need not look at the status bar. **Validate** gives **Valid** or **Not valid**. While a calculation runs, the **Run** button is replaced by a disabled **Running… N s** counting seconds; other background jobs read **Working… N s**. When a job ends the chip reads **Finished in** and the time, or **Failed**. A cancelled calculation shows **Cancelled**. **Validate only** on **Check & run** shows the same chip. The chip fades after about 4 seconds, or 8 seconds for a failure; hover over it for the full message, which also stays in the status bar.

### Check the nuclear data

**Data**, under **TOOLS**, is where the nuclear data are set. **This problem's data** reports whether the files the problem names are installed. If some are not found, it lists them; when the standard data are installed, **Use the installed standard data** points the problem at them, which fixes a problem written for an older data release. **Data files and input folder** holds the library, decay and fission-yield paths, with a SHA-256 pin for the library; relative paths use the input folder, which opening a problem sets to its directory. If the standard data are missing, **Download standard neutron data** installs them with their size shown, or run `actinv data fetch`.

**Check the installed data** has **Verify the data**. It reads every installed standard file and compares it with the SHA-256 the catalog records, then names any file that was changed or cut short. It reads and hashes every file, so it takes a while. If it fails, `actinv data fetch --force` replaces the changed file. The command-line equivalent is `actinv data verify`; see [Choose and install nuclear data](data.md). **Build your own data** lists the `actinv build-*` commands, which libraries, damage tables, shielding factors and covariance files still need.

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
- **Waste class** answers four waste questions: the class of the loaded result, of a container of items, of activity ranges, and how sure the class is; see [Classify a result as waste](#classify-a-result-as-waste).
- **Export** saves what the result answers in one new folder; see [Export a result](#export-a-result).

**Save result…** keeps the complete result JSON: every step, the ledger and the certificate. Saving the problem does not save the result.

### Export a result

**Export** writes the files you tick into a new folder inside the folder you choose; it never writes over an existing folder. **Save to folder…** at the top of the page names how many files it will write.

- **Report** (`report.html`): one page to read, share or print to PDF, with the problem in sentences, activity, decay heat, contact dose and damage at shutdown and at round cooling times, charts, the nuclides that dominate and the caveats. When **Waste class** has classified this result, its classes are included. Untick **Describe the problem as it is set up now** if you changed the setup after the run.
- **Charts** (`charts/*.svg`): each quantity as the material cools, and the decay photon spectrum at shutdown.
- **Tables** (`tables/`): the FISPACT-II style tables of `actinv report`, as CSV, TSV, Markdown or fixed-width text, for all steps or only the selected one, optionally restricted to named nuclides. A table the result cannot build is greyed, with the reason on hover.
- **Transport source** (`transport/`, not ticked by default): the decay photons of the selected step, or of every step that has them, as the source of a photon transport run: `photon_source_openmc.py` (an `openmc.IndependentSource`) and `photon_source_mcnp.txt` (an SDEF card). The source sits at the origin; replace it with your geometry's spatial distribution. A step without decay photons gives its reason; ask for photons or dose under **What to compute**.
- **Files to rerun or reuse**: `result.json` and `problem.json`.

The report, charts and tables are the files `actinv report` writes, and the transport sources the files `actinv export-openmc` and `actinv export-mcnp` write; see [the CLI reference](cli.md).

### Classify a result as waste

**Waste class** opens by asking which waste question you have. Each answer comes from the evaluator the `actinv waste` commands run, against the bundled U.S. NRC 10 CFR 61.55 tables, and each form has **Save spec…** and **Save result…** so the command line reproduces the answer.

- **What class is this result?** classifies the loaded result (`actinv waste`).
- **What class is a container of several items?** averages plugs, rod sections, sealed sources and filters in one container (`actinv waste package`).
- **Which classes can it reach if activities are ranges?** takes lower and upper activities and lists every class inside them (`actinv waste bounds`). It needs no run.
- **How sure is the class?** samples the nuclear-data uncertainty, solving the problem once per sample (`actinv waste uncertainty`).

All four need the nuclide properties (Z, half-life, alpha emission) of every nuclide with activity. Press **Load from decay data** to read them from the problem's decay data; the page asks only for nuclides it has not looked up, and looks them up again after the decay data change. A nuclide without a decay record stays blank and leaves its class unknown, as the CLI does. **Enter** replaces a nuclide's properties with your own entry, marked as yours.

**This result** describes one homogeneous component, the irradiated material, so the component mass is the material mass of the problem. Choose the waste type (activated metal or general) and the displaced volume, or derive the volume from the material density when the problem declares one. Select the steps to classify; all are selected at first, and **Last only** keeps the last. If retained H-3 is not in the activation inventory, declare it with its source and a whole-component activity in Bq for each selected step. Tick **Also screen the whole container against the draft intrusion table** to add the February 2026 draft NUREG-1556 Vol. 22 screen (`actinv waste intrusion-screen`); activated metal is screened as metal, and general waste takes the waste form you choose. Site acceptance limits and reportable quantities are added from the command line.

**Classify** runs in the background. For each selected step the page shows the class, status, coverage, the governing table and column with its sum and margin, and the binding nuclides, a strip of the classes against cooling time, and the earliest selected step within Class A, B and C; with the screen, it adds each step's indicator and the draft rows that apply. **Save spec…** writes the spec and the `result.json` it reads into a folder you choose (it will not replace existing files). The classification is nominal screening, not disposal acceptance; see [Component waste classes](waste.md).

**A container** lists its items: a name, waste type, mass, volume (an envelope volume for a cartridge filter), and, for pieces of one sectioned component, a shared **piece of** name. Each item's activity is this result's activity per gram times its mass, another saved result's (**Choose…**), or becquerels you declare at each step. Choose a screen, or tick **The items are encapsulated** and give the total volume and the encapsulant. The answer shows the container's class at each step, how each waste type averaged and which items were classed on their own, and every item's own class.

**Activity ranges** describe one component by name, mass, waste type and volume, then its whole activity at one or more times as a lower and upper value per nuclide. **Fill from this result** puts a step of the loaded result times the mass into the ranges, widened by the percentage you give. Say whether the nuclides are the whole inventory, where the ranges come from and what they assume. The answer lists every class the component can reach, and the range of each table sum.

**How sure** solves the problem in the background once per sample, with the inputs you tick redrawn each time: cross sections (from the TENDL-2025 covariance data, which `actinv data fetch tendl-2025-neutron-covariance` installs), decay constants, and a flux spread. The problem's own uncertainty block is left out, because the sampler draws its own. At least 72 samples are needed to establish a class at 95 % confidence. The answer gives the established class at each step and the share of samples in each class with its confidence interval.

## Studies and tools

Three studies compare designs or check limits: **Certify**, **Optimize** and **Compare**. They are described in [Certify, optimize and compare designs](studies.md).

**Impurities** screens the trace elements of the open problem's material: which drive activity, decay heat, contact dose, a nuclide's activity, the clearance index or the waste class, the worst case over their specified ranges, and how far each may rise before a limit is reached. It runs `actinv impurities` in the background; see [Screen a material's impurities](impurities.md#in-the-desktop-app-and-the-browser).

**Sweep** solves a family of generated problems along one parameter. **Assimilation** folds a measured value into the uncertainty bands of the loaded result. **Advanced JSON** exposes the complete problem. Bulk library building uses the [CLI](cli.md).

### Activate a transport mesh

**Mesh & transport** takes the flux of a transport run cell by cell and walks four steps; each opens once the one before it is done.

1. **The flux on the mesh** imports an OpenMC statepoint, an MCNP meshtal or mctal tally, or FISPACT-II fluxes (`actinv import-flux`). A tally needs its ID and the source rate, in source particles per second, that makes the per-particle tally a flux. An ACTINV flux file that `import-flux` wrote already is read directly. The flux is pinned by its SHA-256.
2. **Activate every cell** solves the problem as it is set up now in every cell (`actinv mesh`): the material, the irradiation and cooling, the library and what to compute, with each cell's flux in place of the spectrum. The schedule's flux multipliers scale it. A step with its own spectrum is refused; pathways, KERMA and named overrides are left out. **Uncertainty bands** bands the activity, the heat and the nuclides that carry the decay photons in the open result; the banded source and the facility check need them, and they need the TENDL-2025 covariance from **Data**. A run cancelled from the toolbar keeps the cells it finished; **Resume** solves the rest. The table lists each cell's activity, heat and contact dose at a chosen step, highest activity first; **Open** loads one cell's result on the result pages.
3. **The shutdown-dose source** writes the decay photons of every cell at one step for the photon transport run. The banded source (`actinv export-r2s`) carries each cell's independent and fully correlated sigma, and is converted for OpenMC (XML), MCNP (SDEF), Serpent or ALARA (`actinv export-source`). **Correlate the bands between cells** adds the cell-to-cell correlated sigma (`actinv export-r2s-joint`) and takes about as long as the mesh run. **One OpenMC source per cell** (`actinv export-openmc-mesh`) needs no bands. Every source needs the cell bounds a flux mesh with geometry carries. **Save to folder…** copies the files out.
4. **Facility check** applies clearance limits to the band of every cell (`actinv twin`): a cell clears only when its whole band does. Each limit names a banded response, at most or at least a value, and **When** picks the step it applies at, or every step. **Components** name groups of cells for one verdict each. The answer lists what binds each limit and the cells that do not clear.

The browser leaves this page out: it reads and writes files on your disk.

## Guided help

Choose **Help** or press **F1** for setup and result walkthroughs. Use Back, Next, or Exit, and press Escape to close a tour. Ctrl/Cmd+O opens a problem, Ctrl/Cmd+I imports a FISPACT-II deck, and Ctrl/Cmd+S saves the problem.

Read [Results](results.md) for interpretation and [Troubleshooting](troubleshooting.md) for input problems.
