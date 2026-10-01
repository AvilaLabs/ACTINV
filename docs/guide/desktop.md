# Use the desktop

The desktop edits a single-material problem, runs the shared ACTINV solver in the background, and displays inventories, activity, heat, photon spectra, and pathways.

Install it from the [download page](https://actinv.avilalabs.org/download/). Published preview packages and current source builds can expose different features; see [Versions and releases](releases.md).

## Learn without downloading nuclear data

In **Overview & data**, choose **Try offline results tutorial**. It shows a fictional one-hour decay example for learning the interface. Use the iron problem with verified data for an evaluated calculation.

## Run a problem

1. In **Overview & data**, keep the iron example or choose **Open problem**.
2. Choose your installed activation and decay files, or download the standard neutron data and apply the installed paths.
3. Check **Input base**. Relative file paths use this folder. Opening a JSON problem sets the base to its directory; older repository examples may need the repository root.
4. Review **Material**, **Irradiation & cooling**, and **Spectrum**. A zero schedule multiplier means cooling. Spectrum values are integrated group fluxes, with explicit ordering and normalization.
5. Choose optional outputs in **Calculation options & requested outputs**. Apply or discard advanced JSON edits before returning to structured editing.
6. Choose **Validate**, then **Run**. The background task reports preparation and solving. In current builds, cancelling a calculation stops its isolated worker without publishing a partial result.

The current desktop uses a private prepared-data cache for each calculation, so setup time can recur between runs. CLI cache behavior is described in [Data setup](data.md#prepared-calculation-cache).

## Explore and save results

In **Results**, choose activity, heat, or inventory, then select a computed time step. Filter and select a nuclide to follow its history. Heat remains the whole-material response. Add another result for a comparison; check both files' physical times and normalizations.

Open **Spectra & pathways** for optional source information and **Ledger & certificate** for incomplete inputs and calculation details. Export the **full result JSON** to preserve the complete calculation. Inventory CSV contains the selected time step; saving the problem or exporting CSV does not save the full result.

## Guided help

Choose **Help** or press **F1** for setup and result walkthroughs. Use Back, Next, or Exit, and press Escape to close a tour. Ctrl/Cmd+O opens a problem and Ctrl/Cmd+S saves it.

Mesh runs, bulk library building, and transport-source exports use the [CLI](cli.md). Read [Results](results.md) for interpretation and [Troubleshooting](troubleshooting.md) for input problems.
