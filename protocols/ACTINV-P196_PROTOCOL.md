# ACTINV-P196 — Activation calculations in the browser: library packs and the WebAssembly engine

## Purpose

The browser workbench sets up problems and explores results, but until now could not solve one: the activation
library is 124 MB and the decay data 114 MB, too much to fetch for one calculation. Commits `31ba6f1` and `f6b0264` (the
candidate) add a way to run in the browser:

- **Library packs** (`actinv_core::pack`). The library is split into one NPZ per target and the decay data into one
  record per nuclide state. A small graph names, for each target, the states its reactions produce, and for each
  unstable state its decay daughters.
- **Planning.** From the material's nuclides (and any fed nuclides), `pack::plan` follows at most two reactions,
  with decays between them followed freely. It names the targets reached and the decay records that their reactions
  and the decays after them can need. Only those files are fetched. They are joined into one library (an
  uncompressed NPZ and its index) and one decay text per sublibrary, and the problem runs on them through the same
  `run_with_inputs` as any run held in memory.
- **The engine.** `crates/actinv-web-engine` is a web worker. It fetches the planned files, reports progress and
  solves without blocking the page. The workbench's Check & run page offers "Run in browser" where the site serves
  the pack.

Leaving a target out removes the reactions of nuclides that can only be reached in three or more reactions. This
is the only difference from a whole-library run. P196 measures that difference on a fixed suite, checks that the
WebAssembly engine in a browser reproduces the native result, and checks that problems a pack cannot reproduce are
refused.

## Disclosure of what has been seen

During development, the following were run and their agreement read.

**Packed runs against the whole library** (`pack_compare`, 1, 2, 3 and 4 reactions):

- Four 709-group problems: Eurofer and CuCrZr at 1e15 n/cm²/s for 2 years, tungsten at 1e15 for 5 years, and SS316
  at 1e14 for 5 years, each cooled 1 d, 100 d, 10 y and 90 y.
- At two reactions, the largest relative change of a total was 8.9e-9 (Eurofer activity). The largest change of a
  nuclide carrying 0.1% of the activity was 1.2e-6 (Eurofer Be8). At one reaction, tungsten's Os185 was missing
  entirely.
- Eurofer was also run at 60 reactions (2626 targets), where it was identical to the whole library.
- Trimming the decay data to the planned states left every one of these comparisons unchanged.

**SS316 at 1e14 for a year** (`actinv new`, damage removed, fusion-14mev and thermal sources):

- Run with `pack_run` against `actinv run`: largest change 9.3e-9 (thermal) and 1.2e-16 (fusion).
- These two problems are in the suite below.

**Browser and WebAssembly runs:**

- The tungsten problem ran under Node.js from a WebAssembly build, identical to native.
- The SS316 thermal problem ran in headless Chromium through the worker, identical to native.
- The iron example was run through the page's Run in browser button.

**A rehearsal of the harness** (`controls/p196.py rehearse`, into `target/p196-rehearsal`):

- It covered `example-fns_fe_5min`, `al6061-thermal-1e14-1y` and `niobium-fusion-14mev-1e15-10y`, one browser run
  (the iron example) and the five refusal cases.
- Only the following were read: exit statuses; that each pack run printed its plan; the browser run's identity
  with the native pack run (G3: identical); and the refusal messages.
- The damage case first failed at spec validation (its table had no SHA-256). It now pins the catalog damage table.
- No agreement value of the two suite problems was read.

No other suite problem has been run with a pack.

## The suite (registered)

Built by `controls/p196.py`, run in this order:

- **4 examples:** `fns_fe_5min`, `pulsed_demo`, `feed_removal_demo` and `shielding_demo`. They are copied
  unchanged. `shielding_demo`'s self-shielding table is read from disk by `pack_run` like any input outside the
  pack.
- **19 × 6 problems from `actinv new`,** with each problem's damage block removed (packs refuse damage; G4):
  - materials: every library material, i.e. `eurofer97`, `eurofer97-3-measured`, `ss304`, `ss304l`, `ss316`,
    `inconel718`, `zircaloy4`, `al6061`, `concrete-ordinary`, `water`, `graphite-reactor`, `tungsten`, `tantalum`,
    `niobium`, `titanium-grade2`, `copper`, `beryllium-s65`, `v-4cr-4ti` and `pb-15.7li`;
  - under the four standard sources (`fusion-14mev`, `fission`, `thermal`, `one-over-e`) at 1e14 n/cm²/s for 1 y;
  - and under `fusion-14mev` and `thermal` at 1e15 for 10 y;
  - each cooled to 1 h, 1 d, 30 d, 1 y and 10 y (the `actinv new` schedule).

That makes 118 problems. Each runs once with `actinv run` on the catalog library (TENDL-2025, 709 groups, with the
ENDF/B-VIII.0 and JEFF-3.3 decay data) and once with `pack_run` at two reactions. The pack is built by
`pack_build` from the same catalog files, which are pinned by SHA-256.

**Browser problems:** `example-fns_fe_5min`, `example-pulsed_demo`, `example-feed_removal_demo`,
`eurofer97-fusion-14mev-1e15-10y`, `ss316-thermal-1e15-10y`, `tungsten-fusion-14mev-1e15-10y`,
`zircaloy4-thermal-1e15-10y`, `concrete-ordinary-thermal-1e14-1y`, `copper-fission-1e14-1y`,
`inconel718-one-over-e-1e14-1y`, `pb-15.7li-fusion-14mev-1e14-1y` and `v-4cr-4ti-fission-1e14-1y`.

**Refusal cases:** the iron example with:

- the library replaced by another file;
- the library pinned to another SHA-256;
- the decay files in the other order;
- fission-yield files added;
- a damage table added.

## Gates

- **G0.** The SHA-256 of this protocol, `controls/p196.py`, `controls/p196_browser.mjs` and
  `controls/check_p196.py` are in `protocols/protocol_hash.txt` before the run.
- **G1. Provenance.** The record must be the registered run, not a rehearsal:
  - from a clean tracked tree;
  - on the suite, browser problems and refusal cases above, at two reactions;
  - the pack graph names the catalog library and decay data and carries the pinned library SHA-256;
  - every output named in the record matches its SHA-256.
- **G2. Agreement (gate).** Every suite problem must finish both runs with the same number of steps. At every step:
  - the total activity, total decay heat and total photon emission from the pack must be within **1e-4**
    (relative) of the whole library's;
  - every nuclide carrying at least 0.1% of the step's activity in the whole-library run must be within **1e-3**.

  The checker recomputes these from the outputs.
- **G3. The browser (gate).** On every browser problem, the result from the engine worker must be identical to the
  `pack_run` result apart from wall times and the entry point. The worker is built by `trunk build` at the
  candidate commit and runs in headless Chromium (Playwright). The bundle and the pack are served by a local web
  server.
- **G4. Refusals (gate).** Every refusal case must exit non-zero without writing a result, and its message must name
  its cause.
- **G5. Size and time (reported, never a gate).** Reported:
  - the median and largest data a problem fetches (bytes and files);
  - the browser's median and largest time;
  - the native pack run's median time.

  The browser time is measured against a local single-threaded Python web server, not a hosted site.
- **G6.** CI green on the evidence commit.

**P196-PASS** if G0–G4 and G6 hold. One repair round, registered as an amendment before its run.

## Allowed sentences

On P196-PASS, filled by `controls/check_p196.py` from the record and quoted together:

1. "In the browser, ACTINV solves a problem with the standard TENDL-2025 library, fetching only the reactions its
   material can reach in two steps and the decay data they lead to."
2. "On {n} problems (19 materials under four standard spectra at 1e14 n/cm²/s for a year, two of them at 1e15 for
   ten years, and 4 of the examples), its total activity, decay heat and photon emission at every step were within
   {totals} of the whole library's, and every nuclide carrying 0.1% of the activity within {nuclide}."
3. "Run in Chromium, the browser's result was identical to the native one on all {m} problems tried; a problem
   fetched a median of {median} MB of data (at most {max} MB)."

No sentence may say the browser results are FISPACT-II's or the desktop app's to all digits. Sentence 2 bounds the
difference on this suite only.

## Not claimed

- **Fission.** Problems with fission yields, damage tables or step-wise libraries are refused, not qualified.
  Fission products are not followed by a pack, and their own reactions would be missing.
- **Untested conditions.** Agreement on fluences, materials or spectra outside the suite is not measured. A long
  chain of three or more reactions that dominates some response is possible in principle. Eurofer's Be8 at 1.2e-6
  is the largest such effect seen.
- **Speed in use.** Browser speed on a user's computer and network is not measured. The pack's hosting is the
  owner's decision and is not part of P196.

## Commands (coordinator)

Inside the AGENTS.md scope, from a worktree at the evidence candidate with the catalog data installed:

- release builds of `actinv`, `pack_run` and `pack_build`;
- `trunk build --config crates/actinv-gui/Trunk.toml --release --locked --public-url ./`;
- `python3 controls/p196.py run --actinv-bin … --pack-run-bin … --pack-build-bin … --web-dist dist/web`;
- `python3 controls/check_p196.py`.
