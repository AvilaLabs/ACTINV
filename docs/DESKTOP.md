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
