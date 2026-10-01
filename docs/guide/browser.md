# Use the browser workbench

Open [ACTINV in your browser](https://actinv.avilalabs.org/) to learn the interface, edit a problem, or inspect result files without installation. A laptop or desktop with WebGL enabled works best.

## Explore results

Choose **Try the results tutorial** for a fictional one-hour decay example. Switch between activity, heat, and inventory and select a nuclide to follow its history.

For a real calculation, open or drop a result JSON produced by ACTINV. Use **Compare result** to overlay a second file. Plots use each result's recorded time and normalization; the browser does not rescale results to make them comparable.

Photon sources and diagnostics appear under **Result record** when the file contains them. An inventory CSV export contains the selected time step. A result JSON download retains the complete imported result.

## Edit a problem

Open **Problem**, edit the iron example or load your own JSON, then download the problem. Open that file in the desktop or run it with the CLI/Python interface.

The browser checks the specification. It does not run the activation solver, download nuclear-data libraries, or import transport tallies. Local data paths are preserved for the installed application to resolve.

## Keep your edits

Files are read on your device and are not uploaded. Download edits before closing or reloading the tab; the workbench does not retain them between visits. Open one file at a time, up to 32 MiB.

Continue with [Your first calculation](quick-start.md) when you are ready to run the solver.
