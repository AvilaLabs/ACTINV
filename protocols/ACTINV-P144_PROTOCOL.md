# P144 — Waste classification in the desktop and the browser workbench

Registered 2026-10-16, before any source change for this phase. Owner direction 2026-10-16: finish the waste
program; the roadmap's accepted item "Waste workbench integration" is this phase. The owner's standing rule for the
interface applies: new pages are welcome, but the look (theme, colours, fonts, widget style) stays exactly as it is.

## Change under test

**Desktop: a Waste page** in the EXPLORE group, after "Ledger & certificate". From the current run it builds an
ordinary `actinv-waste-spec-1` document and classifies it with the CLI's own evaluator, in process:

- One component, the irradiated material: mass from the problem; displaced volume entered, or derived from a declared
  density; waste type `general` or `activated_metal`; external H-3 `not_applicable` or a declared activity per step.
- Targets: the steps the user selects, all steps by default.
- Nuclide properties (Z, half-life, alpha emission) for every nuclide with positive activity at a selected step,
  filled by the existing `actinv_cli::waste_composition_verify::derive_properties` from the run's decay data (the
  function P110 qualified for `waste composition solve`). A nuclide without a decay record stays absent, so the
  evaluator reports unknown coverage, as the CLI does. Properties are shown and cannot be silently edited; an override
  is an explicit, marked entry.
- Display per target step: class, status, governing table and column, sum of fractions and margin, binding nuclides,
  coverage; a class-by-cooling-time strip and the earliest selected step at which the component is within each class.
  These are read from the result; nothing new is computed.
- Save the spec and the result. The saved result is the exact text the CLI writes for that spec.

**Browser workbench: a waste result viewer.** It opens an `actinv-waste-result-1`, an intrusion-screen result or a
twin result with `waste_classification`, and shows the same tables, and it opens, edits and downloads waste specs.
The browser does not classify: the evaluator is not built for the web, and the browser's stated scope (no solver
execution, files stay on the device) is unchanged.

No change to waste rules, evaluators, schemas, the CLI or the solver. No change to `visuals.rs`, the theme, colours,
fonts or shared widget styling.

## Required evidence

Only the coordinator runs executable jobs, serially, under the required systemd cgroup, with disk-backed `TMPDIR`.

- **V1, CLI parity.** For `examples/fns_fe_5min.json` and one second material (chosen and recorded before the run,
  with an activated-metal type and a declared external H-3), the page's saved spec run through
  `actinv waste SPEC OUT` gives an `OUT` byte-identical to the result the page saved.
- **V2, properties.** For the nuclides the page filled in V1, Z, half-life and alpha emission equal an independent
  reading of the same ENDF-6 decay file by a separate Python reader (MF=8 MT=457: half-life, and alpha emission as
  any positive branch whose RTYP digits include 4).
- **V3, browser.** In the existing Chromium smoke harness, the viewer opens one result of each schema from V1 and the
  P105/P112/P116 controls and shows its classes; a malformed file shows an error message and no panic.
- **V4, look.** `git diff` of `crates/actinv-gui/src/visuals.rs` and of every theme/font asset is empty; desktop
  smoke captures of the new page are kept and reviewed against the existing pages' palette.
- **V5.** Workspace tests, fmt, clippy `-D warnings` (native and wasm), and CI green on the pushed commit for every
  triggered workflow, with `desktop builds` and `Build browser workbench` among them.

## Verdict

P144-PASS if V1–V5 hold. A failing gate is preserved under `results/failures/p144/`; one repair round, registered as
an amendment first.

## Not claimed

The page classifies one homogeneous component. Packages, encapsulation, uncertainty and other jurisdictions are later
phases. Classification is nominal screening, not disposal acceptance or legal compliance.
