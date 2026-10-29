# Certify, optimize and compare designs

Three pages under **STUDIES** in the desktop app answer design questions by solving many calculations around the open problem:

| Page | Question | Command-line equivalent |
|---|---|---|
| **Certify** | Does the design meet its limits once the nuclear-data uncertainty is counted, and which measurement would narrow the answer most? | `actinv decide` |
| **Optimize** | Which design, within the ranges you give, does best on one quantity while meeting every limit? | `actinv optimize` |
| **Compare** | How do materials, spectra or irradiation lengths compare? | `actinv study run` |

Each page builds the input file the command reads from the open problem, runs the command in the background, and draws its result. Values are per gram of material. In the [browser workbench](browser.md#set-up-studies-in-the-browser) the pages set the study up and download the file instead of solving. The schemas and limits of the commands are in [Advanced workflows](workflows.md) and the [command line](cli.md#study-and-design-workflows).

Set up the material, the irradiation and the source first. The pages that need something from the problem say what is missing and offer a button to the page that sets it.

## Certify

**Certify** checks a list of limits against the uncertainty band of the nuclear data. A design passes a limit only if the edge of the band, not just the nominal value, is on the right side of it.

**What it needs.** The problem must have **Uncertainty** switched on under **What to compute**, with a cross-section covariance file chosen. The flux-only uncertainty mode cannot certify. Until both are set, the page shows a warning and an **Open What to compute** button. The schedule must also have at least one step.

**What to fill in.** Under **Limits**, each limit is a card:

- a name, which must be unique;
- the quantity: **Decay heat**, **Gamma heat**, **Beta heat**, **Alpha heat**, **Activity**, or **Activity of one nuclide** (type the nuclide, such as Co60);
- **at most** or **at least**, and the value, in W/g for heats and Bq/g for activities;
- the time, chosen from the schedule's step ends and named by what they mean, such as "at shutdown" or "1 y after shutdown";
- the edge it is **checked at**: **Conservative band edge**, **Normal band edge** or **Nominal value only**.

The conservative edge adds the declared unmodelled share to the band. The normal edge is the band at the problem's confidence level. Nominal ignores the uncertainty and is offered for comparison. Choose **+ Limit** for more cards, and set how many measurements to suggest per limit (1 to 20, default 5).

**Certify** solves the problem once with bands, then checks every limit. Progress shows in the status bar.

**What the answer shows.** The verdict is **Certified** or **Not certified**, with a line such as "2 of 3 limits hold at their band edges, with the nuclear-data uncertainty at 95% confidence". A warning counts limits that pass on the nominal value but fail with the uncertainty, which a calculation without bands would have certified. A second warning appears if the run is incomplete because some reaction or decay data were missing, in which case the band may be too narrow. The table has one row per limit: **Name**, **Quantity and time**, **Value at the band edge**, **Limit**, **Margin** (how far under or over the limit) and **Without uncertainty**. **What to measure next** ranks the cross sections, half-lives, fission yields or whole reactions whose measurement would narrow each band most, each as a share of the band's variance that a perfect measurement of that one quantity would remove.

## Optimize

**Optimize** searches within ranges you set for the design that makes one quantity as low or as high as possible while meeting the limits.

**What it needs.** A material with elements and a schedule with at least one step. Uncertainty is optional: without it, limits and goal are judged on the nominal value, and the page says so.

**What to fill in.**

- **What the search may change** lists up to 6 things, each with a range. Choose **+ Something to vary** to add one.
  - **Element content**: an element's weight percent, from one value to another. The other elements are scaled so the total stays 100 wt%, so at least one element must be left unvaried. The composition must be in weight percent.
  - **Flux**: the total flux as a multiple of the problem's, from a lower to a higher positive multiple. The source needs a total flux.
  - **Step length**: one schedule step's length, from one duration to a longer one, such as 30 d to 2 y.

  Each element or step can be varied once.
- **Goal** is **Lowest** or **Highest** of a quantity (the same list as Certify) at a time in the schedule, and, when the problem has uncertainty, judged at a band edge.
- **Limits** are optional and are the same cards as on Certify. A design counts only if it meets every limit.
- **Search** sets how many designs to **Try** spread over the ranges (default 12), and how many **more around the best so far** (default 8). The total is at most 64 designs; above that the page shows the count in red and **Search designs** stays disabled. Each design is one solve, or one solve with bands when the problem has uncertainty. The search is seeded, so the same inputs try the same designs.

**Search designs** runs the search; the status bar counts designs.

**What the answer shows.** **Best design found** gives the value of the goal and how many designs were tried. A table lists each varied thing with the **Best design** value beside **The problem now**. **Use this design** puts those values into the open problem; it takes effect like any edit, so run it from **Check & run**, and use **Undo edit** to go back. If no design meets every limit, the page says **No design meets every limit**: widen the ranges, loosen a limit or try more designs. **Every design tried** lists all designs ranked, and is open by default in that case.

## Compare

**Compare** solves every combination of the materials, spectra and irradiation lengths you choose and ranks the results side by side, each solved as **Run** solves it.

**What to fill in.** Three columns:

- **Materials**: **This problem's material**, plus any **library materials** added with **Add a library material**. The library is the one on the **Material** page.
- **Spectra**: **This problem's spectrum**, plus any of the four standard shapes (**D-T fusion (14.1 MeV)**, **Fission (Watt)**, **Thermal (Maxwellian)**, **Slowing down (1/E)**). The standard shapes take the problem's total flux.
- **Irradiation**: **This problem's irradiation**, plus further lengths typed after **Also irradiate at full flux for**, such as `30 d, 5 y`. Each irradiation is followed by this problem's cooling.

"This problem's" entries are unavailable, with the reason on hover, when they cannot be compared: a composition not in weight percent, an empty material, a spectrum without flux or one that is rebinned or set by wall loading, or an irradiation with power, wall-loading or library-switch steps. Each case is reported at shutdown and at the end of each cooling step. The page shows the number of combinations (materials times spectra times irradiations). **Compare** needs at least 2 and at most 200 combinations in the app; a study file run with `actinv study run` takes up to 1024.

**What the answer shows.** **Ranking** shows one quantity (**Activity** or **Decay heat**) at one time, per gram, lowest first. Only the columns that differ between cases are shown. Each row gives the value, **Times the lowest** and a bar for scale. A case that did not finish is listed last as "did not finish".

Compare answers "which is lower" for the combinations you pick. It does not search between them (use **Optimize**) and does not apply uncertainty bands (use **Certify**).
