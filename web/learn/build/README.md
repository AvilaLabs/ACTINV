# Building the course

`web/learn` is the interactive course served at `/learn/` (`scripts/prepare_web.py` copies it into the web
bundle, without this folder). The pages are static HTML, CSS and JavaScript modules; nothing is compiled.
Fonts are the two the ACTINV app uses, egui's defaults from `epaint_default_fonts` 0.36.2: Ubuntu Light (Ubuntu Font Licence) and Hack (its own licence). The page copies the workbench look: its colours, flat buttons, 1 px frames and side panel of numbered steps.

## Chapter 1: Your first irradiation

`make_bracket.py` writes the problem the learner downloads (`first-irradiation/files/bracket.json`) and the
results the page plots (`first-irradiation/data/bracket-data.json`): eight ACTINV runs of the bracket, one per
cobalt level. From the repository root, with `actinv-data/` installed and a release build:

```sh
python3 web/learn/build/make_bracket.py --actinv target/release/actinv --work target/learn-work
```

Run it inside the bounded scope from `AGENTS.md`. `scripts/test_prepare_web.py` fails when the data no longer
belongs to the problem file.

The page replays three recorded outputs, all printed by ACTINV 1.7.2 for this problem:

- the practice terminal (`chapter.js`, `OUT` and `FETCH`): `actinv`, `actinv help run`, `actinv data fetch`
  in an empty folder, `actinv validate`, `actinv run`, `actinv report result.json --tables dominant --format
  text --step 14`, and the error messages for a missing file, an unknown command and missing data;
- the Python tab: `first.py` and `study.py` in this folder, run with the `actinv` 1.7.2 package from PyPI in
  the folder holding `bracket.json` and `photon-response.json`.

After changing the problem or ACTINV's output, rerun these and update the recorded text.

## The neutron field

The bracket's spectrum is illustrative: 25% of the flux in the 14.1 MeV line, 55% in the 1/E shape and 20% in
the thermal shape of `actinv new --source`, 1e10 n/cm2/s in total. X-rays are excluded from the photon source
(`photon.exclude_xrays`), because the steel absorbs them; without that, Fe-55's X-rays dominate a point-source
dose with no attenuation in the part.

## Chapters 2 to 5

Each chapter has a `make_*.py` that writes the files the learner downloads (`<chapter>/files/`) and the numbers the
page shows (`<chapter>/data/`), pinning each file's SHA-256 so `scripts/test_prepare_web.py` notices when they drift.
Run them from the repository root inside the bounded scope, as for chapter 1:

| Chapter | Script | Takes |
|---|---|---|
| 2 `where-from/` | `make_routes.py --work target/learn-work/routes` | under a minute |
| 3 `impurities/` | `make_impurities.py --work target/learn-work/impurities` | about 4 minutes |
| 4 `maintenance/` | `make_maintenance.py --work target/learn-work/maintenance` | under a minute |
| 5 `waste/` | `make_waste.py --work target/learn-work/waste-ch` | about 2 minutes |

The pages share `kit.js` (start-up, tabs, quiz, the file explorer and a practice terminal driven by recorded output).
Each `chapter.js` holds its recorded output next to the command that printed it: the practice terminal's `script`
entries and the Python tab's replay. The Python tabs are `routes.py`, `impurities.py`, `maintenance.py` and
`waste.py` in this folder, run in the chapter's work folder with the `actinv` package on `PATH`. Chapter 4's
`actinv maintenance` was recorded with a build of master, ahead of its release; the others with ACTINV 1.7.2.

The impurity sliders of chapter 3 and the niobium slider of chapter 5 add each impurity's reported gradient to the
impurity-free value, which is exact for activity, the clearance index and the waste-class sums and close for the
contact dose proxy over these ranges (the page says so). Chapter 4's slider integrates the recorded dose-rate
curves the way `actinv maintenance` does; at the tabulated waits it agrees with the plan within a percent.
