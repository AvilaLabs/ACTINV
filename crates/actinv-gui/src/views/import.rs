//! Import FISPACT-II deck: the body of the import dialog. The shell reads the files; this view
//! converts their text with `actinv_core::fispact_import`, shows the per-keyword report and hands
//! the converted problem or the report back to the shell.
use super::Target;
use actinv_core::fispact_import::{
    convert, lex, load_flux_text, parse_files_file, Conversion, Counts, ImportContext, LoadedFlux,
    Status, IRRADIATES_WITHOUT_FLUXES,
};
use eframe::egui::{self, RichText};
use egui_extras::{Column, TableBuilder};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

/// The three files the dialog takes.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum ImportSlot {
    Deck,
    Fluxes,
    Files,
}

/// What the shell must do after a frame of the dialog.
pub enum ImportAction {
    None,
    /// Ask the shell to let the user choose the file for this slot, then call `set_file`.
    Choose(ImportSlot),
    /// Replace the problem with this spec document (the shell asks to save unsaved changes first).
    Use(Value),
    /// Save the keyword report (JSON text) under this file name.
    SaveReport {
        file_name: String,
        contents: String,
    },
    Open(Target),
    Close,
}

/// A file the shell has read: its name and its text.
#[derive(Clone, Debug, PartialEq)]
pub struct Slot {
    pub name: String,
    pub text: String,
}

/// One line of the keyword report.
#[derive(Clone, Debug, PartialEq)]
pub struct Row {
    pub keyword: String,
    pub line: usize,
    pub status: Status,
    pub detail: String,
}

/// An input the converted problem still lacks, and the page that supplies it.
#[derive(Clone, Debug, PartialEq)]
pub struct Required {
    pub field: String,
    pub keyword: Option<String>,
    pub line: Option<usize>,
    pub reason: String,
    pub target: Target,
}

/// A finished conversion: the problem, the report and what the dialog shows of it.
pub struct Converted {
    pub spec: Value,
    /// the `actinv-fispact-import-report-1` document as `actinv import-fispact` writes it, less
    /// the `strict` flag, which `report_text` adds
    pub report: Value,
    pub counts: Counts,
    pub unsupported: Vec<(String, usize)>,
    pub rows: Vec<Row>,
    pub required: Vec<Required>,
    /// the deck irradiates and no fluxes file was supplied: the spectrum is a flat stand-in
    pub placeholder: bool,
    /// the deck text carries replacement characters, so its bytes were not all UTF-8
    pub lossy: bool,
    /// a `fluxes` entry of the files file that no supplied fluxes file answers
    pub unresolved_fluxes: Option<String>,
}

impl Converted {
    /// The report as `actinv import-fispact` writes it: pretty JSON with a closing newline.
    pub fn report_text(&self, strict: bool) -> String {
        let mut report = self.report.clone();
        if let Value::Object(map) = &mut report {
            map.insert("strict".into(), json!(strict));
        }
        let text = serde_json::to_string_pretty(&report).unwrap_or_default();
        text + "\n"
    }

    /// `--strict` refuses a problem with any unsupported keyword.
    pub fn blocked_by_strict(&self, strict: bool) -> bool {
        strict && self.counts.unsupported > 0
    }
}

/// The dialog's state: the files the shell supplied, the switch and the last conversion.
#[derive(Default)]
pub struct ImportState {
    deck: Option<Slot>,
    fluxes: Option<Slot>,
    files: Option<Slot>,
    strict: bool,
    outcome: Option<Result<Converted, String>>,
    shown: [bool; 4],
    filter: String,
}

impl ImportState {
    /// The shell read a file the user chose for a slot.
    pub fn set_file(&mut self, slot: ImportSlot, name: String, text: String) {
        let file = Some(Slot { name, text });
        match slot {
            ImportSlot::Deck => self.deck = file,
            ImportSlot::Fluxes => self.fluxes = file,
            ImportSlot::Files => self.files = file,
        }
        // the report describes the files it was made from
        self.outcome = None;
    }
}

// ---------------------------------------------------------------------------------------------
// Conversion (pure; the CLI's `run` without the file system)
// ---------------------------------------------------------------------------------------------

/// Written into the report beside the files file's entries, as the CLI does.
const FILES_FILE_NOTE: &str = "xs_endf, dk_endf and the other FISPACT-II data entries are listed for reference and never read; the library and decay data come from the spec";

fn status_index(status: Status) -> usize {
    match status {
        Status::Mapped => 0,
        Status::Approximated => 1,
        Status::Noted => 2,
        Status::Unsupported => 3,
    }
}

fn parse_status(word: &str) -> Status {
    match word {
        "mapped" => Status::Mapped,
        "approximated" => Status::Approximated,
        "noted" => Status::Noted,
        // an unknown status is never shown as harmless
        _ => Status::Unsupported,
    }
}

/// The report's `entries[]`.
pub fn report_rows(report: &Value) -> Vec<Row> {
    report["entries"]
        .as_array()
        .map(|entries| {
            entries
                .iter()
                .map(|entry| Row {
                    keyword: entry["keyword"].as_str().unwrap_or("").to_string(),
                    line: entry["line"].as_u64().unwrap_or(0) as usize,
                    status: parse_status(entry["status"].as_str().unwrap_or("")),
                    detail: entry["detail"].as_str().unwrap_or("").to_string(),
                })
                .collect()
        })
        .unwrap_or_default()
}

/// How many rows carry each status.
pub fn status_counts(rows: &[Row]) -> Counts {
    let mut counts = Counts::default();
    for row in rows {
        match row.status {
            Status::Mapped => counts.mapped += 1,
            Status::Approximated => counts.approximated += 1,
            Status::Noted => counts.noted += 1,
            Status::Unsupported => counts.unsupported += 1,
        }
    }
    counts
}

/// Indices of the rows to show: those of a selected status (all, when none is selected) whose
/// line, keyword or message contains the filter text, ignoring case.
pub fn filtered_rows(rows: &[Row], shown: &[bool; 4], text: &str) -> Vec<usize> {
    let any = shown.iter().any(|on| *on);
    let needle = text.trim().to_lowercase();
    rows.iter()
        .enumerate()
        .filter(|(_, row)| !any || shown[status_index(row.status)])
        .filter(|(_, row)| {
            needle.is_empty()
                || row.keyword.to_lowercase().contains(&needle)
                || row.detail.to_lowercase().contains(&needle)
                || row.line.to_string().contains(&needle)
        })
        .map(|(index, _)| index)
        .collect()
}

/// The setup page that supplies a spec field.
pub fn target_for_field(field: &str) -> Target {
    let head = field.split(['.', '[']).next().unwrap_or("");
    match head {
        "spectrum" => Target::Spectrum,
        "material" => Target::Material,
        // power-driven steps pin their kerma table on the step
        "schedule" | "kerma" => Target::Schedule,
        "options" | "summary" | "outputs" | "photons" | "pathways" => Target::Outputs,
        "library" | "decay" | "fission_yields" => Target::Data,
        _ => Target::CheckRun,
    }
}

/// The report's `required_additions[]`.
pub fn required_additions(report: &Value) -> Vec<Required> {
    report["required_additions"]
        .as_array()
        .map(|items| {
            items
                .iter()
                .map(|item| {
                    let field = item["field"].as_str().unwrap_or("").to_string();
                    Required {
                        target: target_for_field(&field),
                        field,
                        keyword: item["keyword"].as_str().map(str::to_string),
                        line: item["line"].as_u64().map(|line| line as usize),
                        reason: item["reason"].as_str().unwrap_or("").to_string(),
                    }
                })
                .collect()
        })
        .unwrap_or_default()
}

/// The `fluxes` entry of the files file when no fluxes file was supplied to answer it. A fluxes
/// entry that does not resolve matters only to a deck that irradiates.
pub fn unresolved_fluxes(fluxes: Option<&Slot>, files: Option<&Slot>) -> Option<String> {
    if fluxes.is_some() {
        return None;
    }
    let entries = parse_files_file(&files?.text);
    let (_, value) = entries.iter().rev().find(|(key, _)| key == "fluxes")?;
    Some(format!(
        "the fluxes file '{value}' named by the files file was not supplied; choose it as the fluxes file"
    ))
}

/// A flat spectrum of `groups` equal groups, so that the deck's FLUX amplitude still scales it.
fn placeholder_flux(groups: usize) -> LoadedFlux {
    LoadedFlux {
        path: "placeholder".into(),
        values: vec![1.0; groups],
        wall_loading_mw_m2: 1.0,
        title: "placeholder spectrum".into(),
    }
}

/// Groups of the spectrum structure the deck's projectile reads: 709 for neutrons (the default),
/// 162 for the charged-particle and photon projectiles. The last PROJECTILE keyword applies.
fn placeholder_groups(deck: &str) -> usize {
    let tokens = lex(deck).tokens;
    let mut neutron = true;
    for pair in tokens.windows(2) {
        if !pair[0].star && pair[0].text.to_ascii_uppercase().starts_with("PROJ") {
            if let Ok(code) = pair[1].text.parse::<u32>() {
                neutron = code == 1;
            }
        }
    }
    if neutron {
        709
    } else {
        162
    }
}

/// Convert an irradiating deck that came with no fluxes file, on a flat stand-in spectrum of the
/// structure its projectile reads.
fn convert_with_placeholder(deck: &str) -> Result<Conversion, String> {
    let ctx = ImportContext {
        flux: Some(placeholder_flux(placeholder_groups(deck))),
        ..ImportContext::default()
    };
    convert(deck, &ctx)
}

/// Convert a deck the way `actinv import-fispact` does for the same inputs: the fluxes file when
/// supplied, the catalog's library and decay data, and the same report keys. A deck that
/// irradiates with no fluxes file (the CLI refuses it) converts on a placeholder spectrum and
/// lists the spectrum under `required_additions`.
pub fn convert_deck(
    deck: &Slot,
    fluxes: Option<&Slot>,
    files: Option<&Slot>,
) -> Result<Converted, String> {
    let lossy = deck.text.contains('\u{FFFD}');
    let files_entries = files
        .map(|slot| parse_files_file(&slot.text))
        .unwrap_or_default();
    let unresolved = unresolved_fluxes(fluxes, files);
    let flux = fluxes
        .map(|slot| load_flux_text(&slot.name, &slot.text))
        .transpose()?;
    let supplied = flux.is_some();
    let ctx = ImportContext {
        flux,
        ..ImportContext::default()
    };
    let mut placeholder = false;
    let conversion = match convert(&deck.text, &ctx) {
        Ok(conversion) => conversion,
        Err(error) if !supplied && error.starts_with(IRRADIATES_WITHOUT_FLUXES) => {
            match convert_with_placeholder(&deck.text) {
                Ok(conversion) => {
                    placeholder = true;
                    conversion
                }
                Err(_) => {
                    return Err(match &unresolved {
                        Some(note) => format!("{error}; {note}"),
                        None => format!("{error}; choose a fluxes file"),
                    });
                }
            }
        }
        Err(error) => return Err(error),
    };
    let Conversion {
        spec,
        mut report,
        counts,
        unsupported,
    } = conversion;
    let rows = report_rows(&report);
    if placeholder {
        let source = rows
            .iter()
            .find(|row| {
                ["FLUX", "POWER", "WALL"]
                    .iter()
                    .any(|word| row.keyword.eq_ignore_ascii_case(word))
            })
            .map(|row| (row.keyword.clone(), row.line));
        report["spectrum"]["source"] = Value::Null;
        report["spectrum"]["title"] = Value::Null;
        report["spectrum"]["wall_loading_mw_m2"] = Value::Null;
        report["spectrum"]["placeholder"] = json!(true);
        if let Some(additions) = report["required_additions"].as_array_mut() {
            additions.push(json!({
                "field": "spectrum",
                "keyword": source.as_ref().map(|(keyword, _)| keyword.clone()),
                "line": source.as_ref().map(|(_, line)| *line),
                "reason": "the deck irradiates but no fluxes file was supplied: the spectrum is a flat placeholder (every group equal) scaled to the deck's flux; replace it with the real spectrum before running",
            }));
        }
    }
    if let Value::Object(map) = &mut report {
        map.insert(
            "deck".into(),
            json!({
                "path": deck.name,
                "sha256": format!("{:x}", Sha256::digest(deck.text.as_bytes())),
                "lossy_utf8": lossy,
            }),
        );
        map.insert("fluxes_file_unresolved".into(), json!(unresolved));
        map.insert(
            "files_file".into(),
            match files {
                Some(slot) => json!({
                    "path": slot.name,
                    "entries": files_entries
                        .iter()
                        .map(|(key, value)| json!({"key": key, "path": value}))
                        .collect::<Vec<_>>(),
                    "note": FILES_FILE_NOTE,
                }),
                None => Value::Null,
            },
        );
    }
    let required = required_additions(&report);
    Ok(Converted {
        spec,
        report,
        counts,
        unsupported,
        rows,
        required,
        placeholder,
        lossy,
        unresolved_fluxes: unresolved,
    })
}

/// `OUT.import.json` for a deck: its file name less the extension.
pub fn report_file_name(deck_name: &str) -> String {
    let name = deck_name.rsplit(['/', '\\']).next().unwrap_or(deck_name);
    let stem = match name.rfind('.') {
        Some(dot) if dot > 0 => &name[..dot],
        _ => name,
    };
    if stem.is_empty() {
        "deck.import.json".to_string()
    } else {
        format!("{stem}.import.json")
    }
}

fn target_label(target: Target) -> &'static str {
    match target {
        Target::CheckRun => "Check & run",
        Target::Data => "Data",
        Target::Advanced => "Advanced JSON",
        Target::Material => "Material",
        Target::Schedule => "Irradiation & cooling",
        Target::Spectrum => "Source & spectrum",
        Target::Outputs => "What to compute",
    }
}

// ---------------------------------------------------------------------------------------------
// View
// ---------------------------------------------------------------------------------------------

const STATUS_NAMES: [&str; 4] = ["mapped", "approximated", "noted", "unsupported"];

fn slot_row(
    ui: &mut egui::Ui,
    label: &str,
    slot: Option<&Slot>,
    note: &str,
    action: &mut ImportAction,
    which: ImportSlot,
) {
    ui.label(label);
    match slot {
        Some(file) => {
            ui.monospace(&file.name);
        }
        None => {
            ui.weak("none");
        }
    }
    ui.horizontal(|ui| {
        if ui.button("Choose…").clicked() {
            *action = ImportAction::Choose(which);
        }
        ui.weak(note);
    });
    ui.end_row();
}

/// The body of the import dialog.
pub fn show(ui: &mut egui::Ui, state: &mut ImportState) -> ImportAction {
    let mut action = ImportAction::None;
    let ImportState {
        deck,
        fluxes,
        files,
        strict,
        outcome,
        shown,
        filter,
    } = state;
    egui::ScrollArea::vertical()
        .auto_shrink([false, true])
        .show(ui, |ui| {
            ui.label("Convert a FISPACT-II input deck to an ACTINV problem. Every keyword is reported; nothing is dropped silently.");
            ui.add_space(8.);
            let unresolved = unresolved_fluxes(fluxes.as_ref(), files.as_ref());
            egui::Grid::new("import-slots")
                .num_columns(3)
                .show(ui, |ui| {
                    slot_row(
                        ui,
                        "Deck (.i)",
                        deck.as_ref(),
                        "required",
                        &mut action,
                        ImportSlot::Deck,
                    );
                    slot_row(
                        ui,
                        "Fluxes file",
                        fluxes.as_ref(),
                        "optional; a decay-only deck needs none",
                        &mut action,
                        ImportSlot::Fluxes,
                    );
                    slot_row(
                        ui,
                        "Files file",
                        files.as_ref(),
                        "optional; names the fluxes file",
                        &mut action,
                        ImportSlot::Files,
                    );
                });
            if let Some(note) = &unresolved {
                ui.colored_label(ui.visuals().warn_fg_color, note);
                if ui.button("Choose fluxes file...").clicked() {
                    action = ImportAction::Choose(ImportSlot::Fluxes);
                }
            }
            ui.add_space(8.);
            let mut convert_now = false;
            ui.horizontal(|ui| {
                ui.checkbox(
                    strict,
                    "Strict: refuse the problem if any keyword is unsupported",
                );
                if ui
                    .add_enabled(deck.is_some(), egui::Button::new("Convert"))
                    .clicked()
                {
                    convert_now = true;
                }
            });
            if deck.is_none() {
                ui.weak("Choose the deck first.");
            }
            if convert_now {
                if let Some(deck) = deck.as_ref() {
                    *outcome = Some(convert_deck(deck, fluxes.as_ref(), files.as_ref()));
                }
            }
            ui.add_space(8.);
            match &*outcome {
                None => {}
                Some(Err(error)) => {
                    ui.colored_label(ui.visuals().error_fg_color, error.as_str());
                    if error.starts_with(IRRADIATES_WITHOUT_FLUXES)
                        && ui.button("Choose fluxes file...").clicked()
                    {
                        action = ImportAction::Choose(ImportSlot::Fluxes);
                    }
                }
                Some(Ok(done)) => {
                    report_section(ui, done, *strict, shown, filter, &mut action);
                }
            }
            ui.add_space(8.);
            ui.separator();
            let ready = matches!(&*outcome, Some(Ok(done)) if !done.blocked_by_strict(*strict));
            let has_report = matches!(&*outcome, Some(Ok(_)));
            ui.horizontal(|ui| {
                if ui
                    .add_enabled(ready, egui::Button::new("Use this problem"))
                    .clicked()
                {
                    if let Some(Ok(done)) = &*outcome {
                        action = ImportAction::Use(done.spec.clone());
                    }
                }
                if ui
                    .add_enabled(has_report, egui::Button::new("Download report"))
                    .clicked()
                {
                    if let (Some(Ok(done)), Some(deck)) = (&*outcome, deck.as_ref()) {
                        action = ImportAction::SaveReport {
                            file_name: report_file_name(&deck.name),
                            contents: done.report_text(*strict),
                        };
                    }
                }
                if ui.button("Close").clicked() {
                    action = ImportAction::Close;
                }
            });
        });
    action
}

/// Warnings, status counts, the filterable keyword table, the spectrum line and the required
/// additions of a finished conversion.
fn report_section(
    ui: &mut egui::Ui,
    done: &Converted,
    strict: bool,
    shown: &mut [bool; 4],
    filter: &mut String,
    action: &mut ImportAction,
) {
    let warn = ui.visuals().warn_fg_color;
    let error = ui.visuals().error_fg_color;
    if done.lossy {
        ui.colored_label(
            warn,
            "The deck is not valid UTF-8: undecodable bytes were replaced. Keyword lines are unaffected unless they contained such bytes.",
        );
    }
    if done.placeholder {
        ui.colored_label(
            warn,
            "Required input: this deck irradiates but no fluxes file was supplied. The converted problem carries a flat placeholder spectrum; replace it on the Spectrum page before running.",
        );
    }
    if done.blocked_by_strict(strict) {
        let list: Vec<String> = done
            .unsupported
            .iter()
            .map(|(keyword, line)| format!("{keyword} (line {line})"))
            .collect();
        ui.colored_label(
            error,
            format!(
                "Strict: {} unsupported keyword(s): {}. Switch Strict off to use the problem.",
                done.counts.unsupported,
                list.join(", ")
            ),
        );
    }
    let counts = status_counts(&done.rows);
    let numbers = [
        counts.mapped,
        counts.approximated,
        counts.noted,
        counts.unsupported,
    ];
    ui.horizontal_wrapped(|ui| {
        for (index, (name, count)) in STATUS_NAMES.iter().zip(numbers).enumerate() {
            if ui
                .selectable_label(shown[index], format!("{count} {name}"))
                .clicked()
            {
                shown[index] = !shown[index];
            }
        }
        ui.label("Filter");
        ui.add(
            egui::TextEdit::singleline(filter)
                .desired_width(160.)
                .hint_text("keyword or message"),
        );
    });
    let visible = filtered_rows(&done.rows, shown, filter);
    TableBuilder::new(ui)
        .id_salt("import-report")
        .striped(true)
        .resizable(true)
        .column(Column::initial(50.).at_least(40.))
        .column(Column::initial(110.).at_least(70.))
        .column(Column::initial(105.).at_least(80.))
        .column(Column::remainder().at_least(180.))
        .max_scroll_height(260.)
        .header(28., |mut header| {
            for label in ["Line", "Keyword", "Status", "Message"] {
                header.col(|ui| {
                    ui.strong(label);
                });
            }
        })
        .body(|body| {
            body.rows(27., visible.len(), |mut row| {
                let item = &done.rows[visible[row.index()]];
                row.col(|ui| {
                    ui.monospace(item.line.to_string());
                });
                row.col(|ui| {
                    ui.monospace(&item.keyword);
                });
                row.col(|ui| {
                    let name = item.status.as_str();
                    if item.status == Status::Unsupported {
                        ui.label(RichText::new(name).color(error));
                    } else {
                        ui.label(name);
                    }
                });
                row.col(|ui| {
                    ui.add(egui::Label::new(&item.detail).truncate())
                        .on_hover_text(&item.detail);
                });
            });
        });
    if visible.len() != done.rows.len() {
        ui.weak(format!(
            "Showing {} of {} keywords.",
            visible.len(),
            done.rows.len()
        ));
    }
    ui.add_space(6.);
    ui.label(spectrum_line(&done.report));
    if let Some(note) = &done.unresolved_fluxes {
        if !done.placeholder {
            ui.weak(note);
        }
    }
    if !done.required.is_empty() {
        ui.add_space(6.);
        ui.strong("Required additions");
        ui.label("The converted problem does not validate until these are added.");
        for item in &done.required {
            ui.horizontal_wrapped(|ui| {
                ui.colored_label(warn, format!("! {}", item.field));
                ui.label(&item.reason);
                if ui
                    .small_button(format!("Go to {}", target_label(item.target)))
                    .clicked()
                {
                    *action = ImportAction::Open(item.target);
                }
            });
        }
    }
}

/// The one-line spectrum and schedule summary from the report.
pub fn spectrum_line(report: &Value) -> String {
    let spectrum = &report["spectrum"];
    let mut line = format!(
        "Spectrum: {} groups",
        spectrum["groups"].as_u64().unwrap_or(0)
    );
    if let Some(total) = spectrum["total_cm2_s"].as_f64() {
        line += &format!(", total {total:.3e} particles/cm²/s");
    }
    if let Some(wall) = spectrum["wall_loading_mw_m2"].as_f64() {
        line += &format!(", wall loading {wall} MW/m2");
    }
    if spectrum["placeholder"] == json!(true) {
        line += " (placeholder)";
    }
    line += &format!("   Steps: {}", report["steps"].as_u64().unwrap_or(0));
    line
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A deck with no irradiation: it needs no fluxes file.
    const COOLING_ONLY: &str =
        "FISPACT\n* t\nMASS 1E-3 1\n FE 100\nFLUX 0\nTIME 1 DAYS ATOMS\nEND\n";
    const IRRADIATING: &str =
        "FISPACT\n* t\nMASS 1E-3 1\n FE 100\nFLUX 1E10\nTIME 1 HOURS ATOMS\nEND\n";
    const WITH_UNSUPPORTED: &str =
        "FISPACT\n* t\nMASS 1E-3 1\n FE 100\nFROBNICATE\nFLUX 0\nTIME 1 DAYS ATOMS\nEND\n";
    const POWER_DECK: &str =
        "FISPACT\n* t\nMASS 1E-3 1\n FE 100\nPOWER 150.0 1 301\nTIME 1 HOURS ATOMS\nEND\n";

    fn slot(name: &str, text: &str) -> Slot {
        Slot {
            name: name.into(),
            text: text.into(),
        }
    }

    /// A 709-group fluxes file: one value per line, the wall loading, a title.
    fn fluxes_709() -> Slot {
        let mut text = String::new();
        for group in 0..709 {
            text += &format!(" {}\n", if group >= 400 { "1.0E6" } else { "0.0" });
        }
        text += " 1.000\nFlat test spectrum\n";
        slot("fluxes", &text)
    }

    /// What `actinv_cli::fispact_import::run` does with a deck, a fluxes file and a files file,
    /// less the file system: the conversion and the report keys it adds.
    fn cli_way(
        deck: &str,
        deck_name: &str,
        fluxes: Option<&Slot>,
        files: Option<&Slot>,
        strict: bool,
    ) -> (Conversion, Value) {
        let flux = fluxes.map(|f| load_flux_text(&f.name, &f.text).unwrap());
        let ctx = ImportContext {
            flux,
            library: None,
            decay: None,
            covariance: None,
            ..ImportContext::default()
        };
        let conversion = convert(deck, &ctx).unwrap();
        let mut report = conversion.report.clone();
        let entries = files.map(|f| parse_files_file(&f.text)).unwrap_or_default();
        let unresolved: Option<String> = None;
        if let Value::Object(map) = &mut report {
            map.insert(
                "deck".into(),
                json!({
                    "path": deck_name,
                    "sha256": format!("{:x}", Sha256::digest(deck.as_bytes())),
                    "lossy_utf8": false,
                }),
            );
            map.insert("strict".into(), json!(strict));
            map.insert("fluxes_file_unresolved".into(), json!(unresolved));
            map.insert(
                "files_file".into(),
                match files {
                    Some(f) => json!({
                        "path": f.name,
                        "entries": entries
                            .iter()
                            .map(|(key, value)| json!({"key": key, "path": value}))
                            .collect::<Vec<_>>(),
                        "note": FILES_FILE_NOTE,
                    }),
                    None => Value::Null,
                },
            );
        }
        (conversion, report)
    }

    #[test]
    fn a_decay_only_deck_converts_like_the_command_line_without_fluxes() {
        let done = convert_deck(&slot("cool.i", COOLING_ONLY), None, None).unwrap();
        let (conversion, report) = cli_way(COOLING_ONLY, "cool.i", None, None, false);
        assert_eq!(done.spec, conversion.spec);
        assert_eq!(done.counts, conversion.counts);
        assert_eq!(done.unsupported, conversion.unsupported);
        assert_eq!(
            serde_json::from_str::<Value>(&done.report_text(false)).unwrap(),
            report
        );
        assert!(!done.placeholder && !done.lossy);
        assert!(done.required.is_empty());
    }

    #[test]
    fn an_irradiating_deck_converts_like_the_command_line_with_a_fluxes_and_files_file() {
        let fluxes = fluxes_709();
        let files = slot("files", "# comment\nfluxes fluxes\nxs_endf /data/gxs-709\n");
        let done = convert_deck(&slot("iron.i", IRRADIATING), Some(&fluxes), Some(&files)).unwrap();
        let (conversion, report) =
            cli_way(IRRADIATING, "iron.i", Some(&fluxes), Some(&files), true);
        assert_eq!(done.spec, conversion.spec);
        assert_eq!(done.counts, conversion.counts);
        assert_eq!(
            serde_json::from_str::<Value>(&done.report_text(true)).unwrap(),
            report
        );
        assert!(!done.placeholder);
        assert_eq!(done.report["spectrum"]["source"], "fluxes");
        assert_eq!(done.report["spectrum"]["groups"], 709);
    }

    #[test]
    fn a_deck_that_irradiates_without_fluxes_gets_a_placeholder_and_a_spectrum_requirement() {
        // the command line refuses it
        let error = convert(IRRADIATING, &ImportContext::default())
            .err()
            .unwrap();
        assert!(error.starts_with(IRRADIATES_WITHOUT_FLUXES), "{error}");
        let done = convert_deck(&slot("iron.i", IRRADIATING), None, None).unwrap();
        assert!(done.placeholder);
        assert_eq!(done.report["spectrum"]["placeholder"], true);
        assert!(done.report["spectrum"]["source"].is_null());
        let spectrum: Vec<&Required> = done
            .required
            .iter()
            .filter(|item| item.field == "spectrum")
            .collect();
        assert_eq!(spectrum.len(), 1);
        assert_eq!(spectrum[0].target, Target::Spectrum);
        assert_eq!(spectrum[0].keyword.as_deref(), Some("FLUX"));
        assert_eq!(spectrum[0].line, Some(5));
        assert!(spectrum_line(&done.report).contains("placeholder"));
        // the converted problem is a valid spec
        actinv_core::spec::Spec::from_json(&done.spec.to_string()).unwrap();
    }

    #[test]
    fn a_files_file_naming_an_unsupplied_fluxes_file_is_noted() {
        let files = slot("files", "FLUXES my_fluxes\n");
        let note = unresolved_fluxes(None, Some(&files)).unwrap();
        assert!(note.contains("'my_fluxes'"), "{note}");
        assert!(unresolved_fluxes(Some(&slot("f", "")), Some(&files)).is_none());
        assert!(unresolved_fluxes(None, Some(&slot("files", "xs_endf x\n"))).is_none());
        assert!(unresolved_fluxes(None, None).is_none());
        // a decay-only deck still converts; the note rides in the report
        let done = convert_deck(&slot("cool.i", COOLING_ONLY), None, Some(&files)).unwrap();
        assert_eq!(done.unresolved_fluxes.as_deref(), Some(note.as_str()));
        assert_eq!(done.report["fluxes_file_unresolved"], json!(note));
        // an irradiating deck falls back on the placeholder
        let done = convert_deck(&slot("iron.i", IRRADIATING), None, Some(&files)).unwrap();
        assert!(done.placeholder);
    }

    #[test]
    fn a_bad_fluxes_file_and_a_bad_deck_are_errors() {
        let error = convert_deck(
            &slot("iron.i", IRRADIATING),
            Some(&slot("bad", "1.0 x\ntitle\n")),
            None,
        )
        .err()
        .unwrap();
        assert!(error.starts_with("bad: non-numeric word 'x'"), "{error}");
        // a flux of the wrong group count is the importer's error, not a placeholder
        let error = convert_deck(
            &slot("iron.i", IRRADIATING),
            Some(&slot("f", " 1.0 2.0 3.0\n 0.5\nPosition\n")),
            None,
        )
        .err()
        .unwrap();
        assert!(error.starts_with("f has 3 groups"), "{error}");
    }

    #[test]
    fn status_counts_match_the_importers_and_the_table_rows() {
        let done = convert_deck(&slot("d.i", WITH_UNSUPPORTED), None, None).unwrap();
        assert_eq!(status_counts(&done.rows), done.counts);
        assert_eq!(
            done.counts.mapped
                + done.counts.approximated
                + done.counts.noted
                + done.counts.unsupported,
            done.rows.len()
        );
        assert_eq!(done.counts.unsupported, 1);
        assert_eq!(done.unsupported, vec![("FROBNICATE".to_string(), 5)]);
        let row = done
            .rows
            .iter()
            .find(|row| row.keyword == "FROBNICATE")
            .unwrap();
        assert_eq!((row.line, row.status), (5, Status::Unsupported));
        assert!(done.blocked_by_strict(true));
        assert!(!done.blocked_by_strict(false));
        let clean = convert_deck(&slot("d.i", COOLING_ONLY), None, None).unwrap();
        assert!(!clean.blocked_by_strict(true));
    }

    #[test]
    fn the_report_text_carries_the_strict_flag_and_a_closing_newline() {
        let done = convert_deck(&slot("d.i", COOLING_ONLY), None, None).unwrap();
        let strict = done.report_text(true);
        assert!(strict.ends_with("}\n"));
        let parsed: Value = serde_json::from_str(&strict).unwrap();
        assert_eq!(parsed["strict"], true);
        assert_eq!(parsed["schema"], "actinv-fispact-import-report-1");
        assert_eq!(parsed["deck"]["path"], "d.i");
        assert_eq!(parsed["deck"]["lossy_utf8"], false);
        let loose: Value = serde_json::from_str(&done.report_text(false)).unwrap();
        assert_eq!(loose["strict"], false);
    }

    #[test]
    fn lossy_decks_are_flagged() {
        let text = String::from_utf8_lossy(b"<< dose in \xb5Sv/h >>\nFISPACT\n* t\nMASS 1E-3 1\n FE 100\nFLUX 0\nTIME 1 DAYS ATOMS\nEND\n").into_owned();
        let done = convert_deck(&slot("d.i", &text), None, None).unwrap();
        assert!(done.lossy);
        assert_eq!(done.report["deck"]["lossy_utf8"], true);
    }

    #[test]
    fn required_additions_point_at_the_page_that_fixes_them() {
        assert_eq!(target_for_field("spectrum"), Target::Spectrum);
        assert_eq!(
            target_for_field("spectrum.flux_per_group"),
            Target::Spectrum
        );
        assert_eq!(target_for_field("material.density_g_cm3"), Target::Material);
        assert_eq!(target_for_field("kerma"), Target::Schedule);
        assert_eq!(target_for_field("schedule[0].flux"), Target::Schedule);
        assert_eq!(target_for_field("options.outputs"), Target::Outputs);
        assert_eq!(target_for_field("summary.min_atoms"), Target::Outputs);
        assert_eq!(target_for_field("library.path"), Target::Data);
        assert_eq!(target_for_field(""), Target::CheckRun);
        // the importer's own report for a POWER deck with no DENSITY
        let done = convert_deck(&slot("p.i", POWER_DECK), Some(&fluxes_709()), None).unwrap();
        let fields: Vec<(&str, Target)> = done
            .required
            .iter()
            .map(|item| (item.field.as_str(), item.target))
            .collect();
        assert_eq!(
            fields,
            vec![
                ("material.density_g_cm3", Target::Material),
                ("kerma", Target::Schedule)
            ]
        );
        assert!(done
            .required
            .iter()
            .all(|item| item.keyword.as_deref() == Some("POWER")));
        assert!(done.required.iter().all(|item| !item.reason.is_empty()));
    }

    fn rows() -> Vec<Row> {
        let row = |keyword: &str, line, status, detail: &str| Row {
            keyword: keyword.into(),
            line,
            status,
            detail: detail.into(),
        };
        vec![
            row("MASS", 3, Status::Mapped, "material.mass_g"),
            row("GRPCONVERT", 7, Status::Approximated, "spectrum.rebin"),
            row("HALF", 9, Status::Noted, "output selector"),
            row("COLLAPSE", 12, Status::Unsupported, "no ACTINV equivalent"),
        ]
    }

    #[test]
    fn the_filter_selects_by_status_and_by_text() {
        let rows = rows();
        assert_eq!(filtered_rows(&rows, &[false; 4], ""), vec![0, 1, 2, 3]);
        assert_eq!(
            filtered_rows(&rows, &[true, false, false, true], ""),
            vec![0, 3]
        );
        assert_eq!(filtered_rows(&rows, &[false; 4], "grp"), vec![1]);
        assert_eq!(filtered_rows(&rows, &[false; 4], "  EQUIVALENT "), vec![3]);
        assert_eq!(filtered_rows(&rows, &[false; 4], "12"), vec![3]);
        assert_eq!(
            filtered_rows(&rows, &[false, true, false, false], "mass"),
            Vec::<usize>::new()
        );
        assert_eq!(status_counts(&rows).mapped, 1);
        assert_eq!(status_counts(&rows).unsupported, 1);
    }

    #[test]
    fn report_file_names_follow_the_command_line_rule() {
        assert_eq!(report_file_name("iron_5min.i"), "iron_5min.import.json");
        assert_eq!(
            report_file_name("/home/me/decks/iron.i"),
            "iron.import.json"
        );
        assert_eq!(report_file_name("C:\\decks\\iron.inp"), "iron.import.json");
        assert_eq!(report_file_name("deck"), "deck.import.json");
        assert_eq!(report_file_name(".i"), ".i.import.json");
        assert_eq!(report_file_name(""), "deck.import.json");
    }

    #[test]
    fn the_state_keeps_files_and_forgets_the_report_when_a_file_changes() {
        let mut state = ImportState::default();
        state.set_file(ImportSlot::Deck, "d.i".into(), COOLING_ONLY.into());
        state.outcome = Some(convert_deck(state.deck.as_ref().unwrap(), None, None));
        assert!(matches!(state.outcome, Some(Ok(_))));
        state.set_file(ImportSlot::Fluxes, "f".into(), "x".into());
        assert!(state.outcome.is_none());
        assert_eq!(state.fluxes.as_ref().unwrap().name, "f");
        state.set_file(ImportSlot::Files, "files".into(), "".into());
        assert!(state.files.is_some());
        state = ImportState::default();
        assert!(state.deck.is_none() && state.fluxes.is_none() && state.files.is_none());
    }

    fn render(state: &mut ImportState) -> ImportAction {
        let ctx = egui::Context::default();
        ctx.memory_mut(|m| m.set_everything_is_visible(true));
        let mut action = ImportAction::None;
        let mut output = ctx.run_ui(Default::default(), |ui| {
            action = show(ui, state);
        });
        output.textures_delta.clear();
        action
    }

    #[test]
    fn the_dialog_renders_empty_converted_and_failed() {
        let mut state = ImportState::default();
        assert!(matches!(render(&mut state), ImportAction::None));
        state.set_file(ImportSlot::Deck, "d.i".into(), WITH_UNSUPPORTED.into());
        state.strict = true;
        state.outcome = Some(convert_deck(state.deck.as_ref().unwrap(), None, None));
        assert!(matches!(render(&mut state), ImportAction::None));
        state.shown[3] = true;
        state.filter = "frob".into();
        assert!(matches!(render(&mut state), ImportAction::None));
        state.set_file(ImportSlot::Deck, "p.i".into(), IRRADIATING.into());
        state.set_file(ImportSlot::Files, "files".into(), "fluxes f\n".into());
        state.outcome = Some(convert_deck(
            state.deck.as_ref().unwrap(),
            None,
            state.files.as_ref(),
        ));
        assert!(matches!(render(&mut state), ImportAction::None));
        state.outcome = Some(Err(format!("{IRRADIATES_WITHOUT_FLUXES}; try again")));
        assert!(matches!(render(&mut state), ImportAction::None));
    }
}
