//! Export: one page to save what a result answers. A report page to read and print, the charts,
//! any of the FISPACT-style tables in a chosen format, and the result and problem files to
//! rerun or reuse. The view builds the files; the shell writes them (into a folder on the
//! desktop, as one zip download in the browser). The report and charts come from
//! `actinv_core::report_page` and the tables from `actinv_core::report`, so they match
//! `actinv report` file for file.
use crate::{model::ResultDocument, visuals::BLUE};
use actinv_core::{
    report::{self, Format, Request, StepSelect, TableKind, NAMED_TABLES},
    report_page::{self, PageOptions, Section},
};
use eframe::egui::{self, Color32, RichText};
use serde_json::Value;

/// The table formats offered, with what each is for.
const FORMATS: [(Format, &str); 4] = [
    (Format::Csv, "CSV, for spreadsheets"),
    (Format::Tsv, "TSV, tab-separated"),
    (Format::Markdown, "Markdown"),
    (Format::Text, "Text, fixed-width"),
];

/// Each table and whether the result can build it, with the reason when it cannot.
type Availability = Vec<(TableKind, Result<(), String>)>;

/// What the user chose to export. Everything is chosen until it is unticked.
pub struct ExportState {
    report: bool,
    include_problem: bool,
    charts: bool,
    tables: bool,
    format: Format,
    all_steps: bool,
    nuclides: String,
    /// Unticked tables; every buildable table is exported until listed here.
    skipped: Vec<TableKind>,
    result_json: bool,
    problem_json: bool,
    /// Which tables this result can build, and why not, for the result it was worked out for.
    available: Option<(String, Availability)>,
    pub message: Option<Result<String, String>>,
}

impl Default for ExportState {
    fn default() -> Self {
        Self {
            report: true,
            include_problem: true,
            charts: true,
            tables: true,
            format: Format::Csv,
            all_steps: true,
            nuclides: String::new(),
            skipped: Vec::new(),
            result_json: true,
            problem_json: true,
            available: None,
            message: None,
        }
    }
}

/// What the shell must do after the page was drawn.
pub enum ExportAction {
    None,
    /// Write these files: (path inside the export, bytes). `name` is a short name for the
    /// export, for its folder or zip file.
    Save {
        name: String,
        files: Vec<(String, Vec<u8>)>,
    },
}

/// What else the shell knows that belongs in the export.
pub struct Context<'a> {
    /// The problem as it is set up now; offered in the report and as problem.json.
    pub problem: Option<&'a Value>,
    /// A waste classification of this result, already put into rows.
    pub waste: Option<Section>,
    /// The step the result bar selects (zero-based); "This step" exports it.
    pub step: usize,
    /// How the destination is named on the button: "Save to folder…" or "Download .zip".
    pub save_label: &'static str,
}

fn table_request(state: &ExportState, step: usize, tables: Vec<TableKind>) -> Request {
    Request {
        tables,
        step: if state.all_steps {
            StepSelect::All
        } else {
            StepSelect::One(step as u64 + 1)
        },
        nuclides: state
            .nuclides
            .split(',')
            .map(str::trim)
            .filter(|n| !n.is_empty())
            .map(str::to_owned)
            .collect(),
    }
}

/// Which tables the result can build at all (every step, no nuclide filter).
fn availability(result: &ResultDocument) -> Availability {
    let request = Request {
        tables: NAMED_TABLES.to_vec(),
        step: StepSelect::All,
        nuclides: Vec::new(),
    };
    match report::build(&result.value, &request) {
        Ok(outcomes) => outcomes
            .into_iter()
            .map(|o| (o.kind, o.built.map(|_| ())))
            .collect(),
        Err(reason) => NAMED_TABLES
            .iter()
            .map(|kind| (*kind, Err(reason.clone())))
            .collect(),
    }
}

/// A file-system-safe short name: lower case, words joined by hyphens.
fn slug(text: &str) -> String {
    let words: Vec<String> = text
        .split(|c: char| !c.is_ascii_alphanumeric())
        .filter(|w| !w.is_empty())
        .take(6)
        .map(str::to_ascii_lowercase)
        .collect();
    if words.is_empty() {
        "actinv".into()
    } else {
        words.join("-")
    }
}

/// The export's short name: the result's file name without `.json`, else its title.
pub fn export_name(result: &ResultDocument) -> String {
    let label = result.label.trim_end_matches(".json");
    let base = if label.is_empty() || label.contains(' ') {
        slug(result.value["spec_title"].as_str().unwrap_or("actinv"))
    } else {
        slug(label)
    };
    format!("{base}-export")
}

/// Build every chosen file. `Err` names what could not be built.
pub fn files(
    result: &ResultDocument,
    state: &ExportState,
    context: &Context,
    generated: Option<String>,
) -> Result<Vec<(String, Vec<u8>)>, String> {
    let mut out: Vec<(String, Vec<u8>)> = Vec::new();
    let problem = context.problem.filter(|_| state.include_problem);
    if state.report {
        let page = report_page::html(
            &result.value,
            &PageOptions {
                problem,
                label: Some(result.label.clone()),
                generated,
                sections: context
                    .waste
                    .iter()
                    .map(|s| Section {
                        title: s.title.clone(),
                        note: s.note.clone(),
                        header: s.header.clone(),
                        rows: s.rows.clone(),
                    })
                    .collect(),
            },
        )?;
        out.push(("report.html".into(), page.into_bytes()));
    }
    if state.charts {
        for (stem, _, svg) in report_page::charts(&result.value) {
            out.push((format!("charts/{stem}.svg"), svg.into_bytes()));
        }
    }
    if state.tables {
        let chosen: Vec<TableKind> = buildable(state)
            .into_iter()
            .filter(|kind| !state.skipped.contains(kind))
            .collect();
        if !chosen.is_empty() {
            let outcomes =
                report::build(&result.value, &table_request(state, context.step, chosen))?;
            for outcome in outcomes {
                if let Ok(table) = &outcome.built {
                    out.push((
                        format!(
                            "tables/{}.{}",
                            outcome.kind.name(),
                            state.format.extension()
                        ),
                        format!("{}\n", report::render(table, state.format)).into_bytes(),
                    ));
                }
            }
        }
    }
    if state.result_json {
        let json = serde_json::to_vec_pretty(&result.value).map_err(|e| e.to_string())?;
        out.push(("result.json".into(), json));
    }
    if let Some(problem) = context.problem.filter(|_| state.problem_json) {
        let json = serde_json::to_vec_pretty(problem).map_err(|e| e.to_string())?;
        out.push(("problem.json".into(), json));
    }
    if out.is_empty() {
        return Err("Nothing is chosen to export.".into());
    }
    Ok(out)
}

/// The tables the result can build, in the order they are offered.
fn buildable(state: &ExportState) -> Vec<TableKind> {
    state
        .available
        .as_ref()
        .map(|(_, list)| {
            list.iter()
                .filter(|(_, ok)| ok.is_ok())
                .map(|(kind, _)| *kind)
                .collect()
        })
        .unwrap_or_default()
}

/// One choice: a checkbox with a title, the files it writes on the right and a sentence below.
fn choice(ui: &mut egui::Ui, on: &mut bool, title: &str, files: &str, text: &str) {
    ui.horizontal(|ui| {
        ui.checkbox(on, RichText::new(title).strong());
        ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
            ui.label(RichText::new(files).weak().monospace());
        });
    });
    ui.indent(title, |ui| {
        ui.label(text);
    });
}

/// Draw the page and return what the shell must do.
pub fn show(
    ui: &mut egui::Ui,
    result: &ResultDocument,
    state: &mut ExportState,
    context: &Context,
) -> ExportAction {
    if state
        .available
        .as_ref()
        .is_none_or(|(label, _)| *label != result.label)
    {
        state.available = Some((result.label.clone(), availability(result)));
        state.skipped.clear();
    }
    let mut action = ExportAction::None;
    let frame = |ui: &mut egui::Ui, add: &mut dyn FnMut(&mut egui::Ui)| {
        egui::Frame::group(ui.style())
            .inner_margin(egui::Margin::same(12))
            .show(ui, |ui| {
                ui.set_width(ui.available_width());
                add(ui);
            });
        ui.add_space(8.);
    };

    let charts = report_page::charts(&result.value);
    let count = files_count(state, &charts, context);
    ui.horizontal_wrapped(|ui| {
        let save = ui.add_enabled(
            count > 0,
            egui::Button::new(RichText::new(context.save_label).color(Color32::WHITE)).fill(BLUE),
        );
        let name = export_name(result);
        ui.label(
            RichText::new(match count {
                0 => "Choose something to export.".to_owned(),
                1 => format!("1 file, in {name}"),
                n => format!("{n} files, in {name}"),
            })
            .weak(),
        );
        if save.clicked() {
            match files(result, state, context, None) {
                Ok(files) => action = ExportAction::Save { name, files },
                Err(reason) => state.message = Some(Err(reason)),
            }
        }
    });
    match &state.message {
        Some(Ok(text)) => {
            ui.label(text);
        }
        Some(Err(text)) => {
            ui.colored_label(ui.visuals().error_fg_color, text);
        }
        None => {}
    }
    ui.add_space(12.);
    frame(ui, &mut |ui| {
        choice(
            ui,
            &mut state.report,
            "Report",
            "report.html",
            "One page to read or share: the problem, the numbers at round cooling times, charts, what dominates and the caveats. Opens in any browser; print it to PDF.",
        );
        if state.report {
            ui.indent("report-options", |ui| {
                ui.add_enabled_ui(context.problem.is_some(), |ui| {
                    ui.checkbox(
                        &mut state.include_problem,
                        "Describe the problem as it is set up now",
                    )
                    .on_hover_text(
                        "Untick this if you changed the setup after computing this result.",
                    );
                });
                if context.waste.is_some() {
                    ui.label(
                        RichText::new("Includes the waste classification made on Waste class.")
                            .weak(),
                    );
                }
            });
        }
    });

    frame(ui, &mut |ui| {
        let names: Vec<&str> = charts.iter().map(|c| c.0.as_str()).collect();
        let listed = if names.is_empty() {
            "none".to_owned()
        } else {
            format!("charts/ · {} SVG", names.len())
        };
        choice(
            ui,
            &mut state.charts,
            "Charts",
            &listed,
            "Each quantity as the material cools, and the decay photon spectrum at shutdown, as SVG: sharp at any size in documents and slides.",
        );
        if state.charts && names.is_empty() {
            ui.indent("charts-none", |ui| {
                ui.label(RichText::new("This result has no history to chart.").weak());
            });
        }
    });

    frame(ui, &mut |ui| {
        let ext = state.format.extension();
        choice(
            ui,
            &mut state.tables,
            "Tables",
            &format!("tables/ · .{ext}"),
            "The FISPACT-style tables, the same files `actinv report` writes.",
        );
        if !state.tables {
            return;
        }
        ui.indent("table-options", |ui| {
            egui::Grid::new("export-table-options")
                .num_columns(2)
                .spacing([12., 8.])
                .show(ui, |ui| {
                    ui.label("Format");
                    ui.horizontal_wrapped(|ui| {
                        for (format, label) in FORMATS {
                            ui.selectable_value(&mut state.format, format, label);
                        }
                    });
                    ui.end_row();
                    ui.label("Steps");
                    ui.horizontal(|ui| {
                        ui.selectable_value(&mut state.all_steps, true, "All steps");
                        ui.selectable_value(
                            &mut state.all_steps,
                            false,
                            format!("Only step {}", context.step + 1),
                        );
                    });
                    ui.end_row();
                    ui.label("Nuclides");
                    ui.add(
                        egui::TextEdit::singleline(&mut state.nuclides)
                            .hint_text("all, or e.g. Co60, Mn54")
                            .desired_width(240.),
                    )
                    .on_hover_text(
                        "Restricts the per-nuclide tables and adds these nuclides to the time series.",
                    );
                    ui.end_row();
                });
            ui.add_space(6.);
            let list = state.available.as_ref().map(|(_, l)| l.clone()).unwrap_or_default();
            egui::Grid::new("export-tables")
                .num_columns(2)
                .spacing([32., 4.])
                .show(ui, |ui| {
            for (i, (kind, ok)) in list.into_iter().enumerate() {
                let mut on = ok.is_ok() && !state.skipped.contains(&kind);
                let response = ui
                    .add_enabled_ui(ok.is_ok(), |ui| {
                        ui.horizontal(|ui| {
                            let changed = ui.checkbox(&mut on, kind.title()).changed();
                            ui.label(
                                RichText::new(format!("{}.{}", kind.name(), state.format.extension()))
                                    .weak()
                                    .monospace(),
                            );
                            changed
                        })
                        .inner
                    });
                if let Err(reason) = &ok {
                    response
                        .response
                        .on_disabled_hover_text(format!("Not in this result: {reason}"));
                } else if response.inner {
                    if on {
                        state.skipped.retain(|k| *k != kind);
                    } else {
                        state.skipped.push(kind);
                    }
                }
                if i % 2 == 1 {
                    ui.end_row();
                }
            }
                });
        });
    });

    frame(ui, &mut |ui| {
        ui.horizontal(|ui| {
            ui.label(RichText::new("Files to rerun or reuse").strong());
        });
        ui.indent("data-files", |ui| {
            ui.horizontal(|ui| {
                ui.checkbox(&mut state.result_json, "The complete result");
                ui.label(RichText::new("result.json").weak().monospace());
            });
            ui.label(
                RichText::new(
                    "Every step, the ledger and the certificate. ACTINV, `actinv report` and the Python reader open it.",
                )
                .weak(),
            );
            ui.add_enabled_ui(context.problem.is_some(), |ui| {
                ui.horizontal(|ui| {
                    ui.checkbox(&mut state.problem_json, "The problem");
                    ui.label(RichText::new("problem.json").weak().monospace());
                });
            });
            ui.label(
                RichText::new("As it is set up now; run it again with `actinv run problem.json`.")
                    .weak(),
            );
        });
    });

    action
}

/// How many files the current choice writes.
fn files_count(
    state: &ExportState,
    charts: &[(String, String, String)],
    context: &Context,
) -> usize {
    let tables = if state.tables {
        buildable(state)
            .iter()
            .filter(|kind| !state.skipped.contains(kind))
            .count()
    } else {
        0
    };
    usize::from(state.report)
        + if state.charts { charts.len() } else { 0 }
        + tables
        + usize::from(state.result_json)
        + usize::from(state.problem_json && context.problem.is_some())
}

/// A zip archive of the files, stored without compression: what the browser downloads.
#[cfg_attr(not(target_arch = "wasm32"), allow(dead_code))]
pub fn zip(files: &[(String, Vec<u8>)]) -> Vec<u8> {
    fn crc32(bytes: &[u8]) -> u32 {
        let mut crc = !0u32;
        for byte in bytes {
            crc ^= u32::from(*byte);
            for _ in 0..8 {
                crc = if crc & 1 != 0 {
                    (crc >> 1) ^ 0xEDB8_8320
                } else {
                    crc >> 1
                };
            }
        }
        !crc
    }
    let mut out = Vec::new();
    let mut central = Vec::new();
    for (name, data) in files {
        let offset = out.len() as u32;
        let crc = crc32(data);
        let size = data.len() as u32;
        let name = name.as_bytes();
        // local file header: version 2.0, no flags, stored, no time
        out.extend_from_slice(&0x0403_4b50u32.to_le_bytes());
        out.extend_from_slice(&[20, 0, 0, 0, 0, 0, 0, 0, 0x21, 0]);
        out.extend_from_slice(&crc.to_le_bytes());
        out.extend_from_slice(&size.to_le_bytes());
        out.extend_from_slice(&size.to_le_bytes());
        out.extend_from_slice(&(name.len() as u16).to_le_bytes());
        out.extend_from_slice(&0u16.to_le_bytes());
        out.extend_from_slice(name);
        out.extend_from_slice(data);
        // its central directory entry
        central.extend_from_slice(&0x0201_4b50u32.to_le_bytes());
        central.extend_from_slice(&[20, 0, 20, 0, 0, 0, 0, 0, 0, 0, 0x21, 0]);
        central.extend_from_slice(&crc.to_le_bytes());
        central.extend_from_slice(&size.to_le_bytes());
        central.extend_from_slice(&size.to_le_bytes());
        central.extend_from_slice(&(name.len() as u16).to_le_bytes());
        central.extend_from_slice(&[0; 12]);
        central.extend_from_slice(&offset.to_le_bytes());
        central.extend_from_slice(name);
    }
    let start = out.len() as u32;
    let entries = files.len() as u16;
    out.extend_from_slice(&central);
    out.extend_from_slice(&0x0605_4b50u32.to_le_bytes());
    out.extend_from_slice(&[0, 0, 0, 0]);
    out.extend_from_slice(&entries.to_le_bytes());
    out.extend_from_slice(&entries.to_le_bytes());
    out.extend_from_slice(&(central.len() as u32).to_le_bytes());
    out.extend_from_slice(&start.to_le_bytes());
    out.extend_from_slice(&0u16.to_le_bytes());
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn result() -> ResultDocument {
        crate::model::tutorial_result()
    }

    fn context(problem: Option<&Value>) -> Context<'_> {
        Context {
            problem,
            waste: None,
            step: 0,
            save_label: "Save to folder…",
        }
    }

    #[test]
    fn everything_chosen_writes_the_report_charts_tables_and_both_files() {
        let result = result();
        let state = ExportState {
            available: Some((result.label.clone(), availability(&result))),
            ..Default::default()
        };
        let problem = crate::model::example();
        let files = files(&result, &state, &context(Some(&problem)), None).unwrap();
        let names: Vec<&str> = files.iter().map(|f| f.0.as_str()).collect();
        assert_eq!(names.first(), Some(&"report.html"));
        assert!(names
            .iter()
            .any(|n| n.starts_with("charts/") && n.ends_with(".svg")));
        assert!(names
            .iter()
            .any(|n| n.starts_with("tables/") && n.ends_with(".csv")));
        assert!(names.contains(&"result.json"));
        assert!(names.contains(&"problem.json"));
        assert_eq!(
            files.len(),
            files_count(
                &state,
                &report_page::charts(&result.value),
                &context(Some(&problem))
            )
        );
    }

    #[test]
    fn tables_follow_the_chosen_format_and_nothing_chosen_is_refused() {
        let result = result();
        let mut state = ExportState {
            report: false,
            charts: false,
            result_json: false,
            format: Format::Markdown,
            ..Default::default()
        };
        state.available = Some((result.label.clone(), availability(&result)));
        let files = files(&result, &state, &context(None), None).unwrap();
        assert!(!files.is_empty());
        assert!(files.iter().all(|f| f.0.ends_with(".md")));
        state.tables = false;
        assert!(super::files(&result, &state, &context(None), None).is_err());
    }

    #[test]
    fn the_export_is_named_after_the_result_file() {
        let mut result = result();
        result.label = "fe_shield.json".into();
        assert_eq!(export_name(&result), "fe-shield-export");
        result.label = "UI smoke fixture".into();
        result.value["spec_title"] = "FNS Fe, 5-minute irradiation".into();
        assert_eq!(export_name(&result), "fns-fe-5-minute-irradiation-export");
    }

    #[test]
    fn the_zip_holds_each_file_stored_with_its_checksum() {
        let files = vec![
            ("a.txt".to_owned(), b"hello".to_vec()),
            ("dir/b.txt".to_owned(), Vec::new()),
        ];
        let zip = zip(&files);
        assert_eq!(&zip[..4], b"PK\x03\x04");
        // CRC-32 of "hello"
        assert_eq!(&zip[14..18], &0x3610_a686u32.to_le_bytes());
        let end = zip.len() - 22;
        assert_eq!(&zip[end..end + 4], b"PK\x05\x06");
        assert_eq!(u16::from_le_bytes([zip[end + 10], zip[end + 11]]), 2);
    }
}
