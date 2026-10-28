# What's new in 1.7

ACTINV 1.7.0 screens an alloy's impurities, ships a damage table so that dpa needs no setup, starts a problem from a cited material library, and reorganises the desktop and browser app so that it reaches most of the command line. This page is a tour by task. [Versions and releases](releases.md) has the version table, and the [1.7.0 release notes](https://github.com/AvilaLabs/ACTINV/blob/master/docs/RELEASE_NOTES_v1.7.0.md) have the full list.

The additions are new commands and opt-in options, except the pathway closure and measurement-design changes collected in [Results that may change](#results-that-may-change). The nuclear-data catalog is 1.2.0. The desktop is 0.6.0-preview.1, and the [desktop release page](https://github.com/AvilaLabs/ACTINV/blob/master/docs/releases/desktop-v0.6.0-preview.1.md) describes it.

## Screen a material's impurities

`actinv impurities SPEC [OUT]` tells you which impurities of an alloy matter, at which cooling times and for which concern, and how much of each you can tolerate. It solves each element once and forms the activity, heat, clearance-index and waste-class table sums of any composition from those runs, then re-solves every reported composition in full. Add a `sampling` block and every value gets an upper bound and every limit a lower bound over the nuclear-data uncertainty, with the probability of meeting the waste class; see [Add nuclear-data uncertainty](impurities.md#add-nuclear-data-uncertainty). In the app, the **Impurities** page under Studies sets up the same screen from the open problem, runs it on the desktop and shows the result with its bounds. See [Screen a material's impurities](impurities.md).

## Get dpa with no setup

The data catalog 1.2.0 includes a neutron damage table. A problem that names it gets dpa, exact NRT dpa, arc-dpa and He/dpa without a table to build. `actinv new` writes the `damage` section for you, and `actinv import-fispact` uses the table for decks that give `ALLDISPEN` or `ATDISPEN`. `actinv build-damage --method kinematic` builds a table from an evaluation for other energies or targets. See [Nuclear data](data.md).

## Import and run more of a FISPACT-II deck

`actinv import-fispact` takes fission-yield, self-shielding, resonance and damage files and maps the fission, uncertainty, self-shielding, photon and damage keywords. New opt-in options estimate missing photon spectra (`photon.missing_spectra: "estimate"`), remove X-rays, mix fission yields by each parent's fission rate and select which parents feed products. See [Coming from FISPACT-II](fispact-migration.md#coming-from-fispact-ii).

## Start a new problem from a library

`actinv new problem.json --material ss316 --source fission --flux 1e13` writes 1 g of a library material under a standard spectrum, irradiated for a year and cooled to 1 h, 1 d, 30 d, 1 y and 10 y; `actinv new --list` shows the choices. The library has 19 cited compositions of common reactor and fusion materials, each marked as a nominal composition, specification maxima or a measured heat, with its density and a note naming the trace elements it leaves out. The four spectra are D-T fusion, Watt fission, a thermal Maxwellian and 1/E, on the 709-group structure. See [Command line](cli.md#create-check-and-solve).

In the app, **New problem** on **Start** opens an empty problem, and three cards fill it in one click each: a library material on **Material**, a standard spectrum at the flux you type on **Source & spectrum**, and a standard schedule on **Irradiation & cooling**. See [Start a new problem](desktop.md#start-a-new-problem).

## Find your way in the app

The sidebar groups pages as **Start**, **Set up**, **Results**, **Studies** and **Tools**; Studies and Tools fold away until opened. **Start** asks what you want to find out and opens the page that answers it. The five setup steps are ticked as they become ready and end in **Check & run**, which lists every setting you changed from its default with an **Edit** button. **Validate** and **Run** show their outcome in a chip beside the buttons, and **Run** reads **Running… N s** while it works. The nuclear data moved to a **Data** page under Tools, with **Verify the data**. Result pages open on Summary, and guided tours cover setup and results. See [How the app is organized](desktop.md#how-the-app-is-organized).

## Export a report

The **Export** page saves a report page you can read, share or print, SVG charts, the tables of `actinv report` and the result and problem files, into a new folder on the desktop and one zip in the browser. `actinv report RESULT.json --format html` writes the same report page from the command line. See [Export a result](desktop.md#export-a-result) and the [browser guide](browser.md).

## Run studies from the app

The pages under **Studies** run the command-line studies from the open problem; the browser sets them up and downloads the input for the command:

- **Certify** checks limits at the edge of the nuclear-data band and ranks which measurements would narrow the answer (`actinv decide`).
- **Optimize** searches up to 6 varied things, within 64 designs, for the best value of one quantity under limits (`actinv optimize`).
- **Compare** ranks up to 200 combinations of materials, spectra and irradiation lengths (`actinv study run`, which takes up to 1024 from a file).
- **Mesh & transport** takes a transport run's flux cell by cell: import the tally from OpenMC, MCNP or FISPACT-II, activate every cell (a cancelled run resumes), write the shutdown-dose source for OpenMC, MCNP, Serpent or ALARA, and check which cells clear their limits. See [Activate a transport mesh](desktop.md#activate-a-transport-mesh).
- **Measurements** fits the irradiation flux to measured activities (`actinv reverse`, or `reverse-qualified` with the nuclear-data uncertainty), and **Use this flux** puts the answer into the schedule. It also narrows the loaded result's bands with a measured value. See [Bring in measurements](desktop.md#bring-in-measurements).

See [Certify, optimize and compare designs](studies.md). **Waste class** now asks which waste question you have: the class of this result with the draft intrusion screen, the class of a container of several items, every class a component can reach when its activities are ranges, and how sure the class is under nuclear-data uncertainty. See [Classify a result as waste](desktop.md#classify-a-result-as-waste). On **What to compute**, the Uncertainty card gains **Unmodeled error**, which **Estimate from library disagreement** fills from `actinv eval-spread`.

## Look at results in the browser

The browser's **Start** page opens two results solved ahead of time, so a first visit shows real output without installing anything: the iron example's result, and an impurity screen of a reduced-activation steel. See [Use the browser workbench](browser.md#explore-results).

## Results that may change

`pathway_closure` now counts only populations above 1e-12 of the step's largest. The quick start (`examples/fns_fe_5min.json`) reported 4.117 because of Mn-58 at 2.7e-28 atoms/g; it now reports 3.9e-15, and no other result field changes. Damage tables you build with `build-damage --method kinematic` use NJOY2016 HEATR's per-element displacement energies by default instead of a flat 25 eV; tables built before 1.7.0 are unchanged. In `actinv decide`, `design.top_reactions` now computes covariance blocks that are only positive semi-definite, which it used to report as `ill_conditioned`, and lists computed blocks first; on the iron decision example Fe-56 and Fe-54 now head the list instead of Ca-43 to Ca-47.
