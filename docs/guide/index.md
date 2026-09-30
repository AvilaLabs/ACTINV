# Welcome to ACTINV

ACTINV calculates which nuclides a material contains during irradiation and cooling. Supply a material composition, a particle-flux spectrum, a schedule, and evaluated nuclear data. ACTINV calculates inventories, activity, decay heat, and requested source terms and responses.

This handbook covers **ACTINV 1.3.1** and nuclear-data catalog **1.1.0**. The downloadable desktop preview has a separate release history; [check its solver version](releases.md) before using a feature described here.

## Choose how to work

| Interface | Use it for | Start here |
| --- | --- | --- |
| Desktop | Edit a single-material problem, run calculations, and explore plots | [Install the desktop](install.md#desktop) |
| Command line | Run JSON problems, import transport fluxes, and automate studies | [Your first calculation](quick-start.md) |
| Python | Construct problems and analyze results in scripts or notebooks | [Python guide](python.md) |
| Browser | Learn the interface, edit problems, and inspect existing results | [Browser workbench](browser.md) |

The browser prepares inputs and displays results. Run calculations in the desktop, CLI, or Python interface.

## Start with one material

[Install ACTINV](install.md), then follow [Your first calculation](quick-start.md) to run the supplied iron example. You do not need a source checkout. The example supplies a complete spectrum so you can concentrate on the material, schedule, and results.

Once it runs, use [Describe a problem](problems.md) to adapt it to your own inputs and [Read your results](results.md) to interpret the output. Keep the [specification reference](specification.md) nearby when enabling optional features.

## Understand the calculation boundary

ACTINV is an activation and inventory solver. A transport calculation or measurement supplies its particle spectrum; separate transport tools handle spatial shielding and dose transport. Missing evaluated data and incomplete responses appear in the result's ledger.

ACTINV is research-grade software. Its validation evidence does not approve a calculation for licensing, safety, waste classification, or regulatory use. Read [Scope and qualification](qualification.md) and [Known data limitations](data-limits.md) when assessing a calculation's applicability.

## Find help

Use the search button or press `/` to search this handbook. [Troubleshooting](troubleshooting.md) covers common setup and input errors. Report reproducible software problems through [GitHub issues](https://github.com/AvilaLabs/ACTINV/issues), including your version, command, and error message.
