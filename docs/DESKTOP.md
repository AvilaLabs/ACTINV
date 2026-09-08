# ACTINV desktop

The desktop application adds a native egui interface to ACTINV's existing JSON specifications and Rust solver.

## Implementation plan and component survey

The first workflow is open/create a problem, edit, validate, run in the background, inspect linked results, and export. Scientific outputs and their ledger/certificate remain those of the existing core. Existing CLI and Python workflows remain available.

Reviewed the current egui demo catalog (panels, widget gallery, sliders, tables, drag and drop, scenes, painting, text editing, tooltips, modals, undo/redo, extra viewports), egui_extras tables and syntax highlighting, egui_plot plots, and egui_tiles layouts. Sources: https://github.com/emilk/egui/tree/main/crates/egui_demo_lib/src/demo, https://docs.rs/egui_extras/, https://docs.rs/egui_plot/, https://github.com/rerun-io/egui_tiles.

Component choices: resizable navigation/inspector panels; structured material and schedule editing; numeric drag controls; schedule timeline; interactive plots; virtualized inventory tables; expandable JSON inspectors; contextual tooltips; native file dialogs; modal unsaved-change protection; spotlight walkthroughs; custom pathway drawing. Docking and extra OS windows are optional future enhancements rather than prerequisites for learning the application.

## Branding

The embedded mark is copied from Avila Labs' official local website asset `website/assets/avila-labs-logo-256.png`. Accent cobalt `#1800AD` comes from that website's brand styles. The mark identifies Avila Labs and is not a new ACTINV logo.

## Verification plan

Regression coverage will check specification preservation, relative input paths, result loading, linked data selection, and guided-tour state. Run repository formatting, check, Clippy, and test gates; visually inspect native rendering where available. No nuclear-data inputs are included in the desktop package.

## Launch

With Rust 1.95 or newer:

```sh
cargo run --release -p actinv-gui
```

GitHub Actions **desktop builds** produces candidate Linux AppImages, macOS disk images (Apple Silicon and Intel),
and Windows installer/portable downloads on default-branch pushes, GUI pull requests, or manual runs. These are
unsigned preview candidates; macOS bundles use an ad-hoc signature without notarization.

Linux needs a working Wayland or X11 desktop and graphics driver. The app uses eframe's OpenGL backend and native file dialogs. Building the GUI is optional; `cargo install actinv-cli` continues to install only the CLI.

## A first calculation

1. Open **Overview & data**. Keep the bundled iron example or choose **Open problem**.
2. Choose installed activation and decay files, or use **Download standard neutron data**. Downloads run in the background through the existing verified data installer. Apply the installed paths explicitly when the download completes.
3. Check **Input base**: relative data paths are interpreted relative to this folder. Opening a JSON sets it to that JSON's directory. Repository examples often need the repository root instead. Saving to a different directory resolves input references so their meaning is preserved.
4. Edit **Material**, **Irradiation & cooling**, and **Spectrum**. The composition basis names match the core schema. A zero schedule multiplier means cooling. Reorder rows by their handle or Up/Down buttons.
5. Choose optional outputs under **Calculation options & requested outputs**. Advanced JSON exposes the complete schema, including covariance and radiological tables. Apply or discard JSON edits before continuing in the structured editors.
6. Click **Validate**, then **Run**. Validation checks the specification and file locations; the solver checks hashes and evaluated data when running. Background work shows elapsed time; it does not invent a completion percentage.
7. In **Results**, choose a metric and computed step, or click the plot to select the nearest step. Filter and select a nuclide to follow its activity/inventory. Heat is always whole-material heat. Add a comparison to overlay a second result on physical time.
8. Inspect **Spectra & pathways** and **Ledger & certificate**, then export full JSON or the selected inventory as CSV.

**Help** (or F1) offers setup and result walkthroughs. Tours dim the surrounding workspace, highlight the relevant control, and show an explanation with Back/Next/Exit. The highlighted control stays usable. Escape dismisses a tour. Ctrl/Cmd+O opens a problem and Ctrl/Cmd+S saves it.

## Scientific presentation

Results retain the complete solver JSON. Optional missing outputs are explained instead of replaced with demonstration numbers. MF=33 shading uses the recorded normal interval for the exact selected response, only when present at every step; aggregate activity bands are not synthesized from individual nuclide uncertainties. Detailed confidence and coverage remain available in the result inspector.

The pan-and-zoom pathway diagrams show the recorded source, first product, and selected nuclide, with ranked contributions. The result schema does not enumerate intermediate chain members. Photon plots show per-group source strength, not spectral density. Comparison values use each result's own normalization; the desktop does not rescale them.

The first release focuses on single-material runs and result inspection. Mesh execution, transport-flux import, library construction, and transport-source export remain available through the CLI. Layout docking, extra OS windows, logarithmic plot axes, and cooperative cancellation are not implemented in this release. Closing during a run requires an explicit decision and stops that process.

## Reproduce verification

```sh
cargo fmt --all -- --check
cargo check --workspace --all-targets --all-features
cargo clippy --workspace --all-targets --all-features -- -D warnings
cargo test --workspace --all-targets --all-features
python3 controls/check_desktop.py
```

The independent control requires NumPy (as do the existing P11 fixtures). It generates temporary nuclear data and compares the desktop worker against the CLI, including scientific arrays, the ledger, and input certificates.

For native visual checks, set `ACTINV_GUI_CAPTURE_DIR` to a temporary directory when launching the GUI. Optionally set `ACTINV_GUI_CAPTURE_RESULT` to a real ACTINV result JSON. This opt-in harness captures eight pages and all thirteen setup/result tour steps, then closes. It never loads a fixture in ordinary use.

Local implementation evidence is recorded in [scientific parity](../results/desktop-interface-v1.json) and
[desktop verification](../results/desktop-ui-verification-v1.json). The latter records 126 passing workspace tests,
release-build and native visual checks, and hashes for 21 captured pages/tour steps. macOS and Windows builds are
configured in Actions; their runtime behavior has not been verified locally.
## Clickable preview packages

See [Desktop installation](DESKTOP_INSTALL.md) for Windows installers and portable
downloads, macOS application bundles, and Linux AppImages. Desktop preview version
0.1.0-preview.1 is separate from solver version 1.0.1.
