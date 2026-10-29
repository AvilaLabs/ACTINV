# Handoff: 1.7 app parity and setup flow

Owner-approved plan, 2026-10-29. Release 1.7.0 is held until this is done. A session that picks this up should read
this file, `AGENTS.md`, `docs/maintainers/AGENTS.md`, and the status table at the bottom, then continue from the first
item not marked done. Keep the status table current: update it in the same commit as the work.

## Why

The desktop app lags the CLI: many commands have no page. The owner also found that checking a problem means flipping
through many pages, and that there is no way to start a problem from scratch (only the iron example, open, or a
FISPACT-II deck). The owner asked for every relevant CLI feature in the app for 1.7, a single place to review a
problem, and a "New problem" path.

## Standing rules (owner)

- Keep the app's visual look (theme, colours, fonts, widget style). New pages and navigation are fine.
- UI design and page implementation are done by the coordinating session, never delegated to subagents. Subagents
  (Sonnet only) may do plumbing, CLI fixes, examples, tests and research, and may not run builds or tests.
- UI/CLI changes need regression tests; physics or data changes need a protocol phase (P162 below).
- Screenshot every changed page in Light and Dark, top and scrolled, before the owner reviews
  (`ACTINV_GUI_CAPTURE_DIR`, `_THEME`, `_RESULT`, `_SCREEN`, `_SCROLL`).
- Releases, website and handbook deploys need the owner's OK. Pushes to GitHub are authorized.
- The desktop app runs calculations. The browser sets up and reads every page but runs nothing, and says where to get
  the desktop app or CLI.

## Navigation

Sidebar groups, in order:

- **Start**
- **Set up**: 1 Material, 2 Irradiation & cooling, 3 Source & spectrum, 4 What to compute, 5 Check & run
- **Results**: Summary, Time history, Nuclides, Photons & dose, Pathways, Evidence, Export
- **Studies**: Impurities, Optimize, Certify, Waste, Measurements, Sweep & study, Mesh & transport
- **Tools**: Data, Advanced JSON

Set up and Results are always open. Studies and Tools collapse, and open when the current page is in them. The Start
page asks "What do you want to find out?" and links each question to its page.

## Stage 1: setting a problem up correctly

1. **Complete review on Check & run.**
   - It lists every setting that differs from its default as a plain sentence. Each sentence has Edit, which opens the
     page that sets it.
   - Changes are found by parsing the document with `Spec::from_json` and comparing it, serialized, with a baseline
     spec that has the same material, schedule, spectrum, projectile and data. Every differing leaf is listed, so
     nothing is hidden. Unknown keys fall back to "Section · Key: value (default X)".
   - The rest of the problem is one line: "All other settings at their defaults".
   - Module: `crates/actinv-gui/src/views/review.rs`.
2. **Settings move off Check & run.** Nuclear data and data files go to the new **Data** page (Tools). Solver mode and
   temperature go to an "Advanced settings" section on What to compute. Collapsed sections show "· N changed".
3. **Data page.**
   - Shows which data are installed, with a download option, and runs Verify (`data verify` and `doctor` through the
     job layer).
   - Shows which library and decay files the problem uses, and lets the user pick their own.
   - Lists the CLI commands for building data. The `build-*` commands stay CLI-only.
4. **New problem.** A Start card placed before the example.
   - Material, source and irradiation start missing, not prefilled.
   - Solver, data and outputs start at their defaults.
5. **Material library** on the Material step: pick a material, or type a composition, or "Save to my materials".
6. **Starting spectra** on the Source step: 14.1 MeV line, fission (Watt), thermal (Maxwellian) and 1/E, each with a
   total flux and labelled as a generic shape.
7. **Irradiation quick picks**: irradiate for a time, then cool to 1 h, 1 d, 1 month, 1 year and 10 years (editable).
8. **CLI**: `actinv new problem.json --material ID --source SHAPE` uses the same library and spectra. Bare
   `actinv new` still writes the iron example.
9. **P162 protocol** for the library compositions and the spectrum shapes:
   - each composition cites its source and sums to 100 wt%;
   - group integrals of each shape match the closed-form CDF on the 709-group structure;
   - the screens themselves are not part of the phase.

   Compositions are researched into the scratchpad file `materials_research.json`. Prefer PNNL-15870 Rev. 1 and the
   defining specifications for fusion materials. Trace elements matter (Co, Nb, Ag, Eu, Ta, N).

## Stage 2: design questions

- **Impurities** already covers `budget` and `waste budget`, with or without uncertainty. The page says so; there is
  no separate page.
- **Optimize** (`optimize`):
  - Inputs: axes taken from the open problem (element fraction, flux scale, step length), objective(s), and
    constraints with an edge.
  - Runs as a job with progress from `optimize_ledger.jsonl`, plus Cancel and Resume.
  - Results: best feasible design, a ranked table, and a Pareto chart when there are two objectives.
  - Sweep gets an "Optimize this" button.
- **Certify** (`decide`):
  - Uses the open problem, which must have uncertainty. Constraints are entered as a table.
  - Results: certified or not, the binding constraints, where nominal would over-certify, and what to measure next.
- **Sweep & study** (`study`):
  - Builds a grid of materials × spectra × schedules from the open problem, or opens a study file.
  - Runs as a resumable job.
  - Results: population, rule verdicts and a case table. Sweep stays as the quick one-parameter tool.

## Stage 3: Waste page

The page starts by asking which question:

1. **Classify this result**: today's page, plus an optional draft intrusion screen (`waste intrusion-screen`).
2. **A container of several items** (`waste package`): items from result files or declared Bq, with screen and
   encapsulation.
3. **From measured activity ranges** (`waste bounds`): a form only, no solve.
4. **How sure is the class?** (`waste uncertainty`): a sampling job.

`waste composition solve` is left out because of P157's round-off/scale defect; Impurities gives the class over
ranges instead.

## Stage 4: Mesh & transport

- **Export page** gains "Photon source for a transport code": `export-openmc` and `export-mcnp` for a chosen step.
  Only steps with a photon source are offered.
- **Mesh & transport page:**
  1. `import-flux` from openmc, meshtal, mctal or fispact;
  2. `mesh` run as a resumable job;
  3. cell results;
  4. exports: `export-openmc-mesh`, `export-r2s`, `export-r2s-joint`, and `export-source` for
     openmc, mcnp, serpent or alara.
- **Facility check** (`twin`): a section after a banded mesh run. It shows cells that clear, the binding cell, and the
  components' waste class.

## Stage 5: Measurements and leftovers

- **Measurements** page: Assimilation, plus "Flux from measurements" (`reverse`) with a `reverse-qualified` checkbox.
- **`eval-spread`**: a button in the uncertainty settings, "Estimate from library disagreement", which fills in
  `uncertainty.unmodeled_relative`.
- Left out: `surrogate` (an optimizer internal) and `build-*` (CLI-only).

## Stage 6: browser and finish

- The browser opens on example results computed ahead of time (iron, impurities). They are served next to the wasm
  and fetched on click.
- Check small screens: laptop and narrow widths.
- Do a full screenshot pass.
- Update CHANGELOG, `docs/RELEASE_NOTES_v1.7.0.md`, `docs/guide/whats-new-1.7.md`,
  `docs/releases/desktop-v0.6.0-preview.1.md` and the handbook pages.
- Rebase `release-1.7.0`, rerun the release gates, then get the owner's OK for the atomic tag push.

## After the app work: adoption roadmap (owner agreed 2026-10-29)

Adoption over new physics. The owner said some of it may go in 1.7.

- **1.7, after Stage 6:** OpenMC flux-mode speed comparison, as its own protocol. The recorded 19 s vs 850 s
  (`results/openmc_r2s_demo.json`) used OpenMC's default per-nuclide reaction-rate tallies, not its flux-only mode.
  Run a scaled comparison against flux mode before any public speed claim; the release notes quote whatever it
  measures.
- **1.8:** `pip install actinv[openmc]` packaging (ACTINV as the activation step in an OpenMC R2S script), then an
  upstream conversation about a backend. Also an ACTINV-labelled export that pypact can load, scoped from what
  pypact's `Output` reads. This is not a copy of FISPACT's `.out` format.
- **1.9:** the whole-plant flagship, built with one or two fusion design partners. CAD and an operating scenario go
  in; per-component waste class, dose maps and decay heat come out. It builds on `actinv twin` and `twin_waste`.
  New work: a compact scenario format, per-component histories and replacements, CAD-tag mapping, one command and
  one report.

## Plumbing (subagents, reviewed before any build)

- **Job layer** `crates/actinv-gui/src/jobs.rs`. Branch `app17-jobs`, worktree `target/app17-jobs-wt`.
  - The envelope `actinv-gui-job-1` is {command, document, out_dir, args}. The result `actinv-gui-job-result-1` is
    {exit 0|3, primary, summary, files, message}.
  - It runs in the worker child (`worker::spawn_job`), or on a thread for post-processing commands.
  - Nothing it calls may print to stdout or exit.
  - It reports progress for optimize, study and mesh.
  - The spawn, cancel and reap code in `worker.rs` stays unchanged.
- **CLI fixes**. Branch `app17-cli`, worktree `target/app17-cli-wt`.
  - `waste budget` writes its result and exits 3 on a verification miss; today it exits 1 with no output.
  - `waste budget uncertainty` exits 3 when `verification_pass` is false.
  - Help text: `export-r2s-joint` takes MESH_SPEC; the `export-source openmc` output format is fixed.
  - The study schema description matches the code, and the exit code when a `study run` case fails is settled.
- **Examples** for every new page, under `examples/<command>/` with a README. Branch `app17-ex`, worktree
  `target/app17-ex-wt`. Each must pass `controls/check_public_examples.py`.

Main GUI work: branch `app17`, worktree `target/app17-wt`. Merge the plumbing branches into it after review and a
bounded build.

## Command facts that matter for the pages

- Exit codes are 0 done, 1 failed, 2 usage or input, and 3 a check failed with output written (budget, impurities,
  and the waste-budget pair after the fix).
- Several outcomes are exit 0 and must be read from the output: decide "not certified", optimize `infeasible`, and
  `verification` fields.
- `optimize` takes `base_spec` as a path only, writes a folder (`optimize_result.json`, `optimize_ledger.jsonl`,
  `candidates/`), allows at most 64 evaluations, and can take hours with bands.
- `decide` needs an `uncertainty` block. `run_spec` can be embedded.
- `study` has a key `study`, not `schema`. Case ids are `{material}__{spectrum}__{schedule}`, with at most 1024 cases.
- `mesh` and `twin` use the key `spec`. Mesh flux comes from `import-flux` and is pinned by sha256.
- `waste uncertainty` needs at least 72 samples to establish a class at 0.95, takes exactly one component, and
  `problem` is a path.
- `waste package` items reference result files or give declared Bq per step.
- `reverse` takes a problem and `actinv-reverse-input-1` measurements (step, nuclide, activity_Bq_per_g, sigma), and
  works only in the linear trace regime.
- `eval-spread` needs `decay.fallback` and runs exactly 2 solves.

## Status

Working branch: `app17` (worktree `target/app17-wt`), not yet on master except through P162. The release branch
`release-1.7.0` (worktree `target/rel17-wt`) is rebased at the end.

| Item | State | Commit |
|---|---|---|
| Plan approved by owner | done | `149584e` |
| Job layer: `actinv-gui-job-1`, 22 commands, worker for solves | done | `6ebc67f` |
| CLI fixes: waste budget exit 3, decide `catalog:` names, help pages | done | `4a49ebd` |
| Examples for the new pages (none run yet) | done, unrun | `6ee7062`, `c887a87` |
| P162 material library and spectra | P162-PASS, on master (ledger 270) | `4e52a7f` ... `14eab3d` |
| Stage 1: review on Check & run, Data page with Verify, solver settings card, New problem, standard schedule, folding Studies/Tools (desktop and browser), library and spectrum cards | done, screenshots checked | `d4e8634`, `dd4358f`, `91c14e2` |
| Stage 1: Start page "What do you want to find out?" | moved to Stage 2, so it can route to Certify/Optimize | |
| Stage 2: Certify page (decide): limits at band edges, verdict, margins, what to measure next | done, screenshots checked, on master | `dfce05a` |
| Stage 2: Optimize page: what to vary, goal, limits, status-bar progress, best design, Use this design | done, screenshots checked | `4a7cd6e` |
| Stage 2: Compare page (study run), Start page routing, outcome chip | done, screenshots checked, on master | `d8b6e68`, `28a20bd`, `4983ae7` |
| P164 singular covariance blocks in the measurement design | P164-PASS, on master (ledger 273) | `c8d9514` ... `d6f7d35` |
| Stage 3: Waste page asks which question (class with intrusion screen, container, ranges, how sure) | done, screenshots checked, on master, CI green | `a935c4e` |
| Stage 4: Export gains the transport photon source (OpenMC, MCNP; one step or every step) | done, screenshots checked, on master | `91171ab` |
| Stage 4: Mesh & transport page: flux import, mesh run with resume, cell table, banded/per-cell source for OpenMC/MCNP/Serpent/ALARA, facility check | done, screenshots checked, on master; components' waste class stays CLI (`twin` `waste` block) | `91171ab` |
| Stage 4: `examples/export_r2s` flux had no cell bounds, so its `export-source` commands failed; it now has `mesh_flux_boxes.ndjson` and the whole chain was run | done | `91171ab` |
| Stage 4 CI: desktop smoke still expected 39 screenshots (red on Linux); fixed, and a GUI test now ties the smoke count to the page list | fixed | `c236422`, `965d210` |
| Stage 5: Measurements page (replaces Assimilation): fit the flux to measured activities (`reverse`, or `reverse-qualified` with intervals, identifiability and agreement; Use this flux), and narrow the bands with a measured value (assimilate preview/apply) | done, screenshots checked | this commit |
| Stage 5: Unmodeled error on the Uncertainty card, with Estimate from library disagreement (`eval-spread`). The app uses the largest spread among the banded responses, not the CLI's `suggested_unmodeled_relative` (the largest over every nuclide: 155 % from Ti51 at 30 d on the example, against 0.75 % for total heat) | done, screenshots checked | this commit |
| Stage 5 CI | green, on master | `9cdc359` |
| Stage 6: browser Start page opens two results solved ahead of time (iron example, RA-steel impurity screen), fetched from `examples/` beside the app; `scripts/web_examples.py` recomputes them and `web/examples/manifest.json` pins version and SHA-256 (the packaging test fails when the workspace version moves on, so the release recomputes them) | done, checked in Chromium | this commit |
| Stage 6: small screens. Browser: content was wider than a window under 1100 px (`set_max_width` widened it) and the Start cards overflowed. Desktop at 900 points: toolbar's right group covered Run, Start cards did not wrap (groups do not wrap), wrapped text in `ui.columns` was letter-justified. All fixed; captures zoom with `ACTINV_GUI_CAPTURE_WIDTH` | done, screenshots checked | this commit |
| Stage 6: full screenshot pass, release notes, rebase `release-1.7.0` (recompute browser examples with 1.7.0), owner OK for the tag | not started | |

Capture notes: `ACTINV_GUI_CAPTURE_PROBLEM=FILE` loads a problem before the screenshots; capture closes after its
planned shots (40 with 23 pages) (it used to run until killed when the problem counted as unsaved). Screenshot files are named `00.png`. `ACTINV_GUI_CAPTURE_MESH=DIR/mesh.ndjson` loads a mesh result and, beside it, `flux.ndjson`, `mesh_spec.json`, `r2s.ndjson` and `twin.json`; `ACTINV_GUI_CAPTURE_EXPORT=transport` ticks the transport source.
