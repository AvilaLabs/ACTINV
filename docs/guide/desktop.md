# Use the desktop

The desktop edits a single-material problem, runs the shared ACTINV solver in the background, and displays inventories, activity, heat, photon spectra, and pathways.

Install it from the [download page](https://actinv.avilalabs.org/download/). Published preview packages and current source builds can expose different features; see [Versions and releases](releases.md).

## Learn without downloading nuclear data

In **Overview & data**, choose **Try offline results tutorial**. It shows a fictional one-hour decay example for learning the interface. Use the iron problem with verified data for an evaluated calculation.

## Run a problem

1. In **Overview & data**, keep the iron example or choose **Open problem**. To start from a FISPACT-II input instead, see [Import a FISPACT-II deck](#import-a-fispact-ii-deck).
2. Choose your installed activation and decay files, or download the standard neutron data and apply the installed paths.
3. Check **Input base**. Relative file paths use this folder. Opening a JSON problem sets the base to its directory; older repository examples may need the repository root.
4. Review **Material**, **Irradiation & cooling**, and **Spectrum**. On **Material**, density is optional; a step that uses W/cm3, or geometry self-shielding without its own density, needs it. Spectrum values are integrated group fluxes, with explicit ordering and normalization.
5. On **Outputs**, choose what to compute besides inventory, activity and decay heat, which every result includes. Apply or discard advanced JSON edits before returning to structured editing.
6. Choose **Validate**, then **Run**. The background task reports preparation and solving. In current builds, cancelling a calculation stops its isolated worker without publishing a partial result.

The current desktop uses a private prepared-data cache for each calculation, so setup time can recur between runs. CLI cache behavior is described in [Data setup](data.md#prepared-calculation-cache).

### Import a FISPACT-II deck

Choose **Import FISPACT-II deck…** in the toolbar (Ctrl/Cmd+I). Pick the deck, and optionally a fluxes file and a files file, then **Convert**. The dialog lists every keyword with its line and a status of mapped, approximated, noted or unsupported, and you can filter the list. **Strict** refuses the problem when any keyword is unsupported. **Download report** saves the same report as `actinv import-fispact`; see [Move a FISPACT-II deck to ACTINV](fispact-migration.md). **Use this problem** replaces the current problem, after asking about unsaved changes.

A deck that irradiates but comes without a fluxes file converts with a flat placeholder spectrum, flagged as a required input. Replace it on **Spectrum** before running. Under **Required additions**, each input the deck implies but cannot supply has a button to the page where you add it.

### Choose what drives each step

On **Irradiation & cooling**, each row has a **Drive**: a flux multiplier, a wall loading (MW/m2), a power density (W/cm3) or a specific power (W/g). A flux multiplier of zero is cooling. A wall-loading step is scaled against the reference loading set on **Spectrum**. Power steps need a kerma table pinned by its SHA-256; the page shows a **Kerma table** field with **Choose and pin**. Power steps in W/cm3 also need a density on **Material**. Sweeps over wall-loading or power steps are command-line only. See [Kerma heating and power-driven steps](results.md#kerma-heating-and-power-driven-steps) for the result side.

### Choose outputs

**Outputs** has one card for each optional calculation, with an enable box, its settings and a status that reports conflicts with other choices:

- **Step summary**, which feeds the dominant lists, element table and other [report tables](results.md#step-summary).
- **Decay photons and dose**.
- **Production pathways**, including **Full routes with closure**.
- **Regulatory tables**.
- **Geometry self-shielding**.
- **Uncertainty**, with its channels: MF=33 cross sections, half-lives, fission yields and flux tally statistics. The flux channel takes one relative error per flux group, pasted on the card; with flux alone no covariance file is needed.
- **Gas production (H and He)**, for neutron activation; it cannot be combined with uncertainty.

A result lacks an output you did not enable; the page that would show it says so and offers a link to **Outputs**.

## Explore and save results

The pages under **EXPLORE** read one result. A bar at the top of each has **Open result**, **Add comparison**, **Export JSON**, **Export inventory CSV** and the **Computed step** slider. The chosen step is shared, so every page shows the same step. Add another result for a comparison; check both files' physical times and normalizations.

- **Charts** plots activity, total heat or inventory against time. Click the plot to select the nearest step, and filter and select a nuclide to follow its history. Heat remains the whole-material response.
- **Tables** shows the tables that `actinv report` writes: nuclides (atoms and grams, or activity and heat), step totals, dominant nuclides, elements, depletion, dose, photons, regulatory tables and the time series. Filter a table, click a header to sort it, and choose **Export CSV** for this step or all steps. The CSV is byte-identical to the `actinv report` output. A table the result cannot build gives its reason; when the result has no `summary`, **Enable and re-run** adds `summary: {}` to the problem and runs it again.
- **Photons & dose** shows decay photon groups, dose rates, bremsstrahlung and dose against cooling time. **Export photon groups (CSV)** saves the groups of the selected step. A result with no photon source links to **Outputs**.
- **Pathways** shows how a nuclide is produced. **Full routes** lists the routes for each target with the part of the inventory they explain and the residual; **First products (trace)** ranks the first products and needs trace mode.
- **Ledger & certificate** shows incomplete inputs and calculation details.

Export the **full result JSON** to preserve the complete calculation. Inventory CSV contains the selected time step; saving the problem or exporting CSV does not save the full result.

## Other tools

**Sweep** solves a family of generated problems along one parameter. **Assimilation** folds a measured value into the uncertainty bands of the loaded result. **Advanced JSON** exposes the complete problem. Mesh runs, bulk library building, and transport-source exports use the [CLI](cli.md).

## Guided help

Choose **Help** or press **F1** for setup and result walkthroughs. Use Back, Next, or Exit, and press Escape to close a tour. Ctrl/Cmd+O opens a problem, Ctrl/Cmd+I imports a FISPACT-II deck, and Ctrl/Cmd+S saves the problem.

Read [Results](results.md) for interpretation and [Troubleshooting](troubleshooting.md) for input problems.
