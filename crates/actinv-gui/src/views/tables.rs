//! Tables: the FISPACT-style report tables of one result, built by `actinv_core::report` so the
//! screen, the exported CSV and `actinv report` agree byte for byte.
//!
//! One table is built per (result, step, tab) and cached; moving the step slider builds the
//! next step only when it is first shown, and "All steps" is built only when its export is
//! asked for. The nuclide tab of the design is two tabs here, `TAB1` (atoms, grams) and `TAB2`
//! (activity, heat): the two tables have different row orders and the join would hide the
//! core's own columns and notes.
use crate::model::ResultDocument;
use actinv_core::report::{self, Cell, Format, ReportTable, Request, StepSelect, TableKind};
use eframe::egui::{self, RichText};
use egui_extras::{Column, TableBuilder};
use serde_json::Value;
use std::cmp::Ordering;
use std::collections::{BTreeMap, HashMap};
use std::sync::Arc;

/// Tabs in display order: the label shown and the report table behind it.
const TABS: [(&str, TableKind); 10] = [
    ("Nuclides (atoms, grams)", TableKind::Tab1),
    ("Nuclides (activity, heat)", TableKind::Tab2),
    ("Step totals", TableKind::Totals),
    ("Dominant", TableKind::Dominant),
    ("Elements", TableKind::Elements),
    ("Depletion", TableKind::Depletion),
    ("Dose (TAB3)", TableKind::Tab3),
    ("Photons (TAB4)", TableKind::Tab4),
    ("Regulatory", TableKind::Regulatory),
    ("Time series", TableKind::Timeseries),
];

/// Built tables kept at once. A result can hold hundreds of steps; scrubbing the slider must not
/// keep one table per step forever.
const MAX_CACHED: usize = 16;

/// Column names the filter box matches, first present wins. Other tables fall back to their
/// first text column.
const FILTER_COLUMNS: [&str; 5] = ["nuclide", "element", "response", "group", "quantity"];

/// What the shell must do after the page was drawn.
#[derive(Debug, PartialEq)]
pub enum TablesAction {
    None,
    /// The user asked to add `summary: {}` to the problem and run again.
    EnableSummary,
    /// Save `contents` (CSV text) under the suggested file name.
    Export {
        file_name: String,
        contents: String,
    },
}

/// Which result the cached tables belong to. The result carries no generation number, so the
/// identity is its label, its step count, the address of its JSON value and the time, total heat
/// and inventory length of its last step. The address alone could repeat when a re-run replaces
/// the result in place; the last-step fields tell such a result apart from the one it replaced.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
struct Identity {
    label: String,
    steps: usize,
    address: usize,
    last_t_bits: u64,
    last_heat_bits: u64,
    last_inventory: usize,
}

impl Identity {
    fn of(result: &ResultDocument) -> Self {
        let last = result.steps().last();
        Self {
            label: result.label.clone(),
            steps: result.steps().len(),
            address: std::ptr::from_ref(&result.value) as usize,
            last_t_bits: last.and_then(|s| s["t_s"].as_f64()).map_or(0, f64::to_bits),
            last_heat_bits: last
                .and_then(|s| s["heat_W_per_g"]["total"].as_f64())
                .map_or(0, f64::to_bits),
            last_inventory: last
                .and_then(|s| s["inventory"].as_array())
                .map_or(0, Vec::len),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
enum Scope {
    One(u64),
    All,
}

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
struct CacheKey {
    identity: Identity,
    scope: Scope,
    kind: &'static str,
}

impl CacheKey {
    fn new(identity: &Identity, kind: TableKind, step_number: u64) -> Self {
        Self {
            identity: identity.clone(),
            scope: scope_for(kind, step_number),
            kind: kind.name(),
        }
    }
}

/// The time series is a table across steps; every other table shows the selected step.
fn scope_for(kind: TableKind, step_number: u64) -> Scope {
    if kind == TableKind::Timeseries {
        Scope::All
    } else {
        Scope::One(step_number)
    }
}

/// The rows on screen: which table, which filter and sort produced them.
#[derive(Clone, PartialEq)]
struct OrderKey {
    cache: CacheKey,
    filter: String,
    sort: Option<(String, bool)>,
}

#[derive(Default)]
pub struct TablesState {
    tab: usize,
    identity: Option<Identity>,
    cache: HashMap<CacheKey, Arc<report::Built>>,
    /// Filter text and sort (column header, descending) per tab, by table name.
    filters: BTreeMap<&'static str, String>,
    sorts: BTreeMap<&'static str, (String, bool)>,
    order: Option<(OrderKey, Arc<Vec<usize>>)>,
    /// Export every step instead of the selected one.
    export_all: bool,
    message: Option<String>,
}

impl TablesState {
    /// Drop everything cached for another result.
    fn sync(&mut self, identity: &Identity) {
        if self.identity.as_ref() != Some(identity) {
            self.cache.clear();
            self.order = None;
            self.message = None;
            self.identity = Some(identity.clone());
        }
    }

    fn fetch(
        &mut self,
        value: &Value,
        key: &CacheKey,
        kind: TableKind,
        step_number: u64,
    ) -> Arc<report::Built> {
        if let Some(hit) = self.cache.get(key) {
            return hit.clone();
        }
        if self.cache.len() >= MAX_CACHED {
            self.cache.clear();
        }
        let built = Arc::new(build_table(value, kind, step_select(kind, step_number)));
        self.cache.insert(key.clone(), built.clone());
        built
    }

    fn rows(
        &mut self,
        key: &CacheKey,
        table: &ReportTable,
        filter: &str,
        sort: Option<&(String, bool)>,
    ) -> Arc<Vec<usize>> {
        let wanted = OrderKey {
            cache: key.clone(),
            filter: filter.to_string(),
            sort: sort.cloned(),
        };
        if let Some((have, rows)) = &self.order {
            if *have == wanted {
                return rows.clone();
            }
        }
        let rows = Arc::new(view_order(
            table,
            filter,
            sort.map(|(header, descending)| (header.as_str(), *descending)),
        ));
        self.order = Some((wanted, rows.clone()));
        rows
    }
}

fn step_select(kind: TableKind, step_number: u64) -> StepSelect {
    match scope_for(kind, step_number) {
        Scope::One(step) => StepSelect::One(step),
        Scope::All => StepSelect::All,
    }
}

/// The result's own number for the zero-based step (`step`, else its position plus one, as
/// `actinv report` reads it).
fn step_number(steps: &[Value], index: usize) -> u64 {
    steps[index]["step"].as_u64().unwrap_or(index as u64 + 1)
}

/// One table through the shared report logic. An unavailable table or a result the report cannot
/// read is the reason text.
fn build_table(value: &Value, kind: TableKind, step: StepSelect) -> report::Built {
    let request = Request {
        tables: vec![kind],
        step,
        nuclides: Vec::new(),
    };
    report::build(value, &request)?
        .pop()
        .map(|outcome| outcome.built)
        .unwrap_or_else(|| Err("the report built no table".into()))
}

/// CSV text and the suggested file name. The current step's table is rendered as is; `all`
/// builds every step (the time series always covers every step). The CSV is the core's, so it
/// is unfiltered and in the core's row order whatever the screen shows.
fn export_csv(
    value: &Value,
    kind: TableKind,
    step_number: u64,
    all: bool,
    current: &ReportTable,
) -> Result<(String, String), String> {
    let name = kind.name();
    if kind == TableKind::Timeseries {
        return Ok((
            format!("{name}_all.csv"),
            report::render(current, Format::Csv),
        ));
    }
    if all {
        let table = build_table(value, kind, StepSelect::All)?;
        return Ok((
            format!("{name}_all.csv"),
            report::render(&table, Format::Csv),
        ));
    }
    Ok((
        format!("{name}_step{step_number}.csv"),
        report::render(current, Format::Csv),
    ))
}

/// The column the filter box matches, if the table has one.
fn filter_column(table: &ReportTable) -> Option<usize> {
    FILTER_COLUMNS
        .iter()
        .find_map(|name| table.headers.iter().position(|header| header == name))
        .or_else(|| {
            (0..table.headers.len()).find(|&column| {
                table
                    .rows
                    .iter()
                    .any(|row| matches!(row.get(column), Some(Cell::Text(_))))
            })
        })
}

static EMPTY: Cell = Cell::Empty;

/// Total order on cells for sorting; empty cells are last whichever way the column is sorted,
/// numbers precede text.
fn compare_cells(a: &Cell, b: &Cell, descending: bool) -> Ordering {
    let ascending = match (a, b) {
        (Cell::Empty, Cell::Empty) => return Ordering::Equal,
        (Cell::Empty, _) => return Ordering::Greater,
        (_, Cell::Empty) => return Ordering::Less,
        (Cell::Num(x), Cell::Num(y)) => x.total_cmp(y),
        (Cell::Int(x), Cell::Int(y)) => x.cmp(y),
        (Cell::Int(x), Cell::Num(y)) => (*x as f64).total_cmp(y),
        (Cell::Num(x), Cell::Int(y)) => x.total_cmp(&(*y as f64)),
        (Cell::Text(x), Cell::Text(y)) => x
            .to_lowercase()
            .cmp(&y.to_lowercase())
            .then_with(|| x.cmp(y)),
        (Cell::Text(_), _) => Ordering::Greater,
        (_, Cell::Text(_)) => Ordering::Less,
    };
    if descending {
        ascending.reverse()
    } else {
        ascending
    }
}

/// Row indices to show: rows whose filter column contains the filter text (case-insensitive),
/// in the core's order or sorted by the named column. The sort is stable.
fn view_order(table: &ReportTable, filter: &str, sort: Option<(&str, bool)>) -> Vec<usize> {
    let needle = filter.trim().to_lowercase();
    let column = filter_column(table).filter(|_| !needle.is_empty());
    let mut rows: Vec<usize> = (0..table.rows.len())
        .filter(|&index| match column {
            Some(column) => table.rows[index]
                .get(column)
                .is_some_and(|cell| cell.human().to_lowercase().contains(&needle)),
            None => true,
        })
        .collect();
    if let Some((header, descending)) = sort {
        if let Some(column) = table.headers.iter().position(|name| name == header) {
            rows.sort_by(|&a, &b| {
                compare_cells(
                    table.rows[a].get(column).unwrap_or(&EMPTY),
                    table.rows[b].get(column).unwrap_or(&EMPTY),
                    descending,
                )
            });
        }
    }
    rows
}

/// A header click: a new column sorts ascending, the same column again descending, then off.
fn next_sort(current: Option<&(String, bool)>, header: &str) -> Option<(String, bool)> {
    match current {
        Some((name, false)) if name == header => Some((header.to_string(), true)),
        Some((name, true)) if name == header => None,
        _ => Some((header.to_string(), false)),
    }
}

fn mentions_summary(text: &str) -> bool {
    text.contains("summary")
}

fn column_width(header: &str) -> f32 {
    (header.chars().count() as f32 * 8. + 28.).clamp(90., 260.)
}

/// The Tables page body. `step` is the zero-based selected step.
pub fn show(
    ui: &mut egui::Ui,
    result: &ResultDocument,
    step: usize,
    state: &mut TablesState,
) -> TablesAction {
    let mut action = TablesAction::None;
    let steps = result.steps();
    if steps.is_empty() {
        ui.label("The result has no steps.");
        return action;
    }
    let step = step.min(steps.len() - 1);
    let step_no = step_number(steps, step);
    let has_summary = steps[step].get("summary").is_some();
    let identity = Identity::of(result);
    state.sync(&identity);
    state.tab = state.tab.min(TABS.len() - 1);

    ui.horizontal_wrapped(|ui| {
        for (index, (label, _)) in TABS.iter().enumerate() {
            ui.selectable_value(&mut state.tab, index, *label);
        }
    });
    let (_, kind) = TABS[state.tab];
    let name = kind.name();
    let scope_text = match scope_for(kind, step_no) {
        Scope::One(_) => format!("step {step_no} of {}", steps.len()),
        Scope::All => format!("all {} steps", steps.len()),
    };
    ui.label(
        RichText::new(format!("{} · {scope_text}", kind.title()))
            .small()
            .weak(),
    );

    let key = CacheKey::new(&identity, kind, step_no);
    let built = state.fetch(&result.value, &key, kind, step_no);
    let table = match &*built {
        Ok(table) => table,
        Err(reason) => {
            ui.add_space(8.);
            ui.label(format!("{} is not available: {reason}", kind.title()));
            if !has_summary && mentions_summary(reason) {
                action = enable_summary_button(ui, action);
            }
            return action;
        }
    };

    let filter_header = filter_column(table).map(|column| table.headers[column].clone());
    let mut sort_click: Option<String> = None;
    ui.horizontal(|ui| {
        let filter = state.filters.entry(name).or_default();
        ui.label("Filter");
        ui.add_enabled(
            filter_header.is_some(),
            egui::TextEdit::singleline(filter)
                .hint_text(match &filter_header {
                    Some(header) => format!("{header} contains…"),
                    None => "nothing to filter".to_string(),
                })
                .desired_width(180.),
        );
        if kind != TableKind::Timeseries {
            ui.label("Export scope");
            ui.selectable_value(&mut state.export_all, false, "This step");
            ui.selectable_value(&mut state.export_all, true, "All steps");
        }
        if ui.button("Export CSV").clicked() {
            match export_csv(&result.value, kind, step_no, state.export_all, table) {
                Ok((file_name, contents)) => {
                    state.message = None;
                    action = TablesAction::Export {
                        file_name,
                        contents,
                    };
                }
                Err(reason) => state.message = Some(format!("Export failed: {reason}")),
            }
        }
    });
    if let Some(message) = &state.message {
        ui.label(message);
    }

    let filter = state.filters.get(name).cloned().unwrap_or_default();
    let sort = state.sorts.get(name).cloned();
    let order = state.rows(&key, table, &filter, sort.as_ref());
    ui.label(
        RichText::new(format!(
            "{} of {} rows · click a column header to sort",
            order.len(),
            table.rows.len()
        ))
        .small()
        .weak(),
    );

    let height = (ui.available_height() - 4.).clamp(240., 700.);
    egui::ScrollArea::horizontal()
        .id_salt(format!("tables-scroll-{name}"))
        .show(ui, |ui| {
            let mut builder = TableBuilder::new(ui)
                .id_salt(format!("tables-{name}"))
                .striped(true)
                .resizable(true)
                .max_scroll_height(height);
            for header in &table.headers {
                builder = builder.column(
                    Column::initial(column_width(header))
                        .at_least(60.)
                        .clip(true),
                );
            }
            builder
                .header(28., |mut h| {
                    for header in &table.headers {
                        h.col(|ui| {
                            let arrow = match &sort {
                                Some((column, false)) if column == header => " ↑",
                                Some((column, true)) if column == header => " ↓",
                                _ => "",
                            };
                            let label = egui::Label::new(
                                RichText::new(format!("{header}{arrow}")).strong(),
                            )
                            .sense(egui::Sense::click());
                            if ui.add(label).clicked() {
                                sort_click = Some(header.clone());
                            }
                        });
                    }
                })
                .body(|body| {
                    body.rows(27., order.len(), |mut row| {
                        let cells = &table.rows[order[row.index()]];
                        for cell in cells {
                            row.col(|ui| match cell {
                                Cell::Text(text) => {
                                    ui.label(text);
                                }
                                other => {
                                    ui.monospace(other.human());
                                }
                            });
                        }
                    });
                });
        });
    if let Some(header) = sort_click {
        match next_sort(sort.as_ref(), &header) {
            Some(next) => {
                state.sorts.insert(name, next);
            }
            None => {
                state.sorts.remove(name);
            }
        }
    }

    if !table.notes.is_empty() {
        ui.add_space(6.);
        for note in &table.notes {
            ui.label(RichText::new(format!("Note: {note}")).small().weak());
        }
        if !has_summary && table.notes.iter().any(|note| mentions_summary(note)) {
            action = enable_summary_button(ui, action);
        }
    }
    action
}

/// The button that adds `summary: {}` to the problem; clicking it replaces `action`. The desktop
/// runs the problem again at once; the browser only edits it.
fn enable_summary_button(ui: &mut egui::Ui, action: TablesAction) -> TablesAction {
    #[cfg(not(target_arch = "wasm32"))]
    let (label, text) = (
        "Enable and re-run",
        "This result has no `summary` section. Enable and re-run adds `summary: {}` to the problem and runs it again.".to_string(),
    );
    #[cfg(target_arch = "wasm32")]
    let (label, text) = (
        "Add to problem",
        format!(
            "This result has no `summary` section. Add to problem adds `summary: {{}}` to the problem; then {}.",
            super::RUN_AGAIN
        ),
    );
    ui.label(text);
    if ui.button(label).clicked() {
        TablesAction::EnableSummary
    } else {
        action
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn step_json(step: u64, t_s: f64, k: f64, summary: bool) -> Value {
        let mut value = json!({
            "step": step,
            "t_s": t_s,
            "inventory": [
                {"nuclide": "Fe56", "Z": 26, "A": 56, "LISO": 0, "atoms_per_g": 1.0e22},
                {"nuclide": "Co60", "Z": 27, "A": 60, "LISO": 0, "atoms_per_g": 2.0e15 * k},
                {"nuclide": "Mn54", "Z": 25, "A": 54, "LISO": 0, "atoms_per_g": 5.0e14 * k},
            ],
            "activity_Bq_per_g": {"Co60": 8.0e4 * k, "Mn54": 2.0e4 * k},
            "heat_W_per_g": {"total": 0.75 * k, "alpha": 0.0, "beta": 0.25 * k, "gamma": 0.5 * k},
        });
        if summary {
            value["summary"] = json!({
                "nuclides": [
                    {"nuclide": "Fe56", "atoms": 1.0e22, "grams": 0.5, "activity_Bq": 0.0,
                     "alpha_W": 0.0, "beta_W": 0.0, "gamma_W": 0.0, "half_life_s": null,
                     "stable": true},
                    {"nuclide": "Co60", "atoms": 2.0e15 * k, "grams": 2.5e-7 * k,
                     "activity_Bq": 8.0e4 * k, "alpha_W": 0.0, "beta_W": 0.25 * k,
                     "gamma_W": 0.5 * k, "half_life_s": 1.66e8, "stable": false},
                    {"nuclide": "Mn54", "atoms": 5.0e14 * k, "grams": 5.0e-8 * k,
                     "activity_Bq": 2.0e4 * k, "alpha_W": 0.0, "beta_W": 0.0,
                     "gamma_W": 0.0, "half_life_s": 2.7e7, "stable": false},
                ],
                "totals": {"absolute": {"multiplier": 2.0, "activity_Bq": 2.0e5 * k,
                                        "heat_W": 1.5 * k}},
            });
        }
        value
    }

    /// Two steps at 10 s and 20 s; the second holds half the Co60 and Mn54 of the first.
    fn result(summary: bool) -> ResultDocument {
        ResultDocument::parse(
            json!({
                "spec_title": "fixture",
                "steps": [step_json(1, 10.0, 1.0, summary), step_json(2, 20.0, 0.5, summary)],
            }),
            "fixture".into(),
        )
        .expect("valid fixture")
    }

    fn core_csv(doc: &ResultDocument, kind: TableKind, step: StepSelect) -> String {
        let request = Request {
            tables: vec![kind],
            step,
            nuclides: Vec::new(),
        };
        let outcomes = report::build(&doc.value, &request).unwrap();
        report::render(outcomes[0].built.as_ref().unwrap(), Format::Csv)
    }

    #[test]
    fn exported_csv_is_the_core_render_of_the_same_request() {
        let doc = result(true);
        let current = build_table(&doc.value, TableKind::Tab1, StepSelect::One(2)).unwrap();
        let (file_name, contents) =
            export_csv(&doc.value, TableKind::Tab1, 2, false, &current).unwrap();
        assert_eq!(file_name, "tab1_step2.csv");
        assert_eq!(
            contents,
            core_csv(&doc, TableKind::Tab1, StepSelect::One(2))
        );
        let (file_name, contents) =
            export_csv(&doc.value, TableKind::Tab1, 2, true, &current).unwrap();
        assert_eq!(file_name, "tab1_all.csv");
        assert_eq!(contents, core_csv(&doc, TableKind::Tab1, StepSelect::All));
        // The all-steps CSV holds both steps' rows; the single-step CSV only one.
        assert!(
            contents.lines().count()
                > core_csv(&doc, TableKind::Tab1, StepSelect::One(2))
                    .lines()
                    .count()
        );
    }

    #[test]
    fn time_series_exports_every_step_whatever_the_scope() {
        let doc = result(true);
        let table = build_table(&doc.value, TableKind::Timeseries, StepSelect::All).unwrap();
        let (file_name, contents) =
            export_csv(&doc.value, TableKind::Timeseries, 1, false, &table).unwrap();
        assert_eq!(file_name, "timeseries_all.csv");
        assert_eq!(
            contents,
            core_csv(&doc, TableKind::Timeseries, StepSelect::All)
        );
        assert_eq!(contents.lines().count(), 3);
    }

    #[test]
    fn an_unavailable_table_keeps_the_core_reason() {
        let doc = result(false);
        let reason = build_table(&doc.value, TableKind::Dominant, StepSelect::One(1)).unwrap_err();
        assert!(reason.contains("summary"), "{reason}");
        assert!(mentions_summary(&reason));
        // Without a summary the nuclide tables still build from the inventory.
        let tab1 = build_table(&doc.value, TableKind::Tab1, StepSelect::One(1)).unwrap();
        assert!(tab1.notes.iter().any(|note| mentions_summary(note)));
    }

    #[test]
    fn an_unknown_step_is_the_core_error() {
        let doc = result(true);
        let reason = build_table(&doc.value, TableKind::Tab1, StepSelect::One(9)).unwrap_err();
        assert!(reason.contains("step 9"), "{reason}");
    }

    #[test]
    fn filter_matches_the_nuclide_column_case_insensitively() {
        let doc = result(true);
        let table = build_table(&doc.value, TableKind::Tab1, StepSelect::One(1)).unwrap();
        let column = filter_column(&table).unwrap();
        assert_eq!(table.headers[column], "nuclide");
        let names = |rows: &[usize]| -> Vec<String> {
            rows.iter()
                .map(|&r| table.rows[r][column].exact())
                .collect()
        };
        assert_eq!(view_order(&table, "", None).len(), 3);
        assert_eq!(names(&view_order(&table, "co", None)), ["Co60"]);
        assert_eq!(names(&view_order(&table, " FE5 ", None)), ["Fe56"]);
        assert!(view_order(&table, "zz", None).is_empty());
    }

    #[test]
    fn a_table_with_no_text_column_has_no_filter() {
        let doc = result(true);
        let table = build_table(&doc.value, TableKind::Totals, StepSelect::One(1)).unwrap();
        assert_eq!(filter_column(&table), None);
        assert_eq!(view_order(&table, "anything", None).len(), table.rows.len());
    }

    #[test]
    fn sorting_orders_by_value_either_way_and_is_stable() {
        let doc = result(true);
        let table = build_table(&doc.value, TableKind::Tab1, StepSelect::One(1)).unwrap();
        let column = table
            .headers
            .iter()
            .position(|h| h == "atoms_per_g")
            .unwrap();
        let atoms = |rows: &[usize]| -> Vec<f64> {
            rows.iter()
                .map(|&r| match table.rows[r][column] {
                    Cell::Num(v) => v,
                    _ => panic!("atoms are numbers"),
                })
                .collect()
        };
        let up = atoms(&view_order(&table, "", Some(("atoms_per_g", false))));
        let down = atoms(&view_order(&table, "", Some(("atoms_per_g", true))));
        assert!(up.windows(2).all(|w| w[0] <= w[1]));
        assert!(down.windows(2).all(|w| w[0] >= w[1]));
        assert_eq!(up.iter().rev().copied().collect::<Vec<_>>(), down);
        // An unknown column leaves the core's order.
        assert_eq!(
            view_order(&table, "", Some(("nope", false))),
            view_order(&table, "", None)
        );
        // Sorting composes with the filter.
        let both = view_order(&table, "5", Some(("atoms_per_g", true)));
        assert_eq!(both.len(), 2);
    }

    #[test]
    fn empty_cells_sort_last_in_both_directions() {
        let (empty, number) = (Cell::Empty, Cell::Num(1.));
        for descending in [false, true] {
            assert_eq!(
                compare_cells(&empty, &number, descending),
                Ordering::Greater
            );
            assert_eq!(compare_cells(&number, &empty, descending), Ordering::Less);
        }
        assert_eq!(
            compare_cells(&Cell::Int(2), &Cell::Num(10.), false),
            Ordering::Less
        );
        assert_eq!(
            compare_cells(&Cell::Text("a".into()), &Cell::Text("B".into()), false),
            Ordering::Less
        );
    }

    #[test]
    fn header_clicks_cycle_ascending_descending_off() {
        let first = next_sort(None, "atoms_per_g");
        assert_eq!(first, Some(("atoms_per_g".to_string(), false)));
        let second = next_sort(first.as_ref(), "atoms_per_g");
        assert_eq!(second, Some(("atoms_per_g".to_string(), true)));
        assert_eq!(next_sort(second.as_ref(), "atoms_per_g"), None);
        assert_eq!(
            next_sort(second.as_ref(), "nuclide"),
            Some(("nuclide".to_string(), false))
        );
    }

    #[test]
    fn cache_key_changes_with_step_tab_and_result_but_not_for_the_time_series() {
        let doc = result(true);
        let identity = Identity::of(&doc);
        let a = CacheKey::new(&identity, TableKind::Tab1, 1);
        assert_eq!(a, CacheKey::new(&identity, TableKind::Tab1, 1));
        assert_ne!(a, CacheKey::new(&identity, TableKind::Tab1, 2));
        assert_ne!(a, CacheKey::new(&identity, TableKind::Tab2, 1));
        assert_eq!(
            CacheKey::new(&identity, TableKind::Timeseries, 1),
            CacheKey::new(&identity, TableKind::Timeseries, 2)
        );
        let other = ResultDocument::parse(doc.value.clone(), "another".into()).unwrap();
        assert_ne!(a, CacheKey::new(&Identity::of(&other), TableKind::Tab1, 1));
        // The same label and step count with different content is a different result.
        let mut changed = doc.value.clone();
        changed["steps"][1]["t_s"] = json!(40.0);
        let changed = ResultDocument::parse(changed, "fixture".into()).unwrap();
        assert_ne!(identity, Identity::of(&changed));
    }

    #[test]
    fn the_cache_builds_each_step_once_and_clears_for_a_new_result() {
        let doc = result(true);
        let identity = Identity::of(&doc);
        let mut state = TablesState::default();
        state.sync(&identity);
        let key1 = CacheKey::new(&identity, TableKind::Tab1, 1);
        let key2 = CacheKey::new(&identity, TableKind::Tab1, 2);
        let first = state.fetch(&doc.value, &key1, TableKind::Tab1, 1);
        assert!(Arc::ptr_eq(
            &first,
            &state.fetch(&doc.value, &key1, TableKind::Tab1, 1)
        ));
        assert_eq!(state.cache.len(), 1, "other steps are not built eagerly");
        let second = state.fetch(&doc.value, &key2, TableKind::Tab1, 2);
        assert!(!Arc::ptr_eq(&first, &second));
        assert_eq!(state.cache.len(), 2);
        let other = ResultDocument::parse(doc.value.clone(), "another".into()).unwrap();
        state.sync(&Identity::of(&other));
        assert!(state.cache.is_empty());
    }

    #[test]
    fn the_cache_is_bounded() {
        let doc = result(true);
        let identity = Identity::of(&doc);
        let mut state = TablesState::default();
        for (index, (_, kind)) in TABS.iter().cycle().take(MAX_CACHED + 3).enumerate() {
            // Distinct keys: vary the nominal step number; unknown steps cache their reason.
            let key = CacheKey::new(&identity, *kind, 100 + index as u64);
            state.fetch(&doc.value, &key, *kind, 100 + index as u64);
        }
        assert!(state.cache.len() <= MAX_CACHED);
    }

    #[test]
    fn the_row_order_is_reused_until_filter_sort_or_table_changes() {
        let doc = result(true);
        let identity = Identity::of(&doc);
        let mut state = TablesState::default();
        let key = CacheKey::new(&identity, TableKind::Tab1, 1);
        let built = state.fetch(&doc.value, &key, TableKind::Tab1, 1);
        let table = (*built).as_ref().unwrap();
        let a = state.rows(&key, table, "", None);
        assert!(Arc::ptr_eq(&a, &state.rows(&key, table, "", None)));
        assert!(!Arc::ptr_eq(&a, &state.rows(&key, table, "co", None)));
    }

    fn render_all_tabs(doc: &ResultDocument, step: usize) -> Vec<TablesAction> {
        let ctx = egui::Context::default();
        let mut state = TablesState::default();
        let mut actions = Vec::new();
        for tab in 0..TABS.len() {
            state.tab = tab;
            for _ in 0..2 {
                let mut output = ctx.run_ui(Default::default(), |ui| {
                    actions.push(show(ui, doc, step, &mut state));
                });
                output.textures_delta.clear();
            }
        }
        actions
    }

    #[test]
    fn every_tab_renders_with_and_without_a_summary() {
        for summary in [true, false] {
            let doc = result(summary);
            for step in [0, 1, 99] {
                let actions = render_all_tabs(&doc, step);
                assert!(actions.iter().all(|a| *a == TablesAction::None));
            }
        }
    }

    #[test]
    fn the_unsummarised_page_offers_the_enable_button_only_when_the_reason_needs_it() {
        let with = result(true);
        let without = result(false);
        let summary_needed =
            build_table(&without.value, TableKind::Dominant, StepSelect::One(1)).unwrap_err();
        assert!(mentions_summary(&summary_needed));
        assert!(without.steps()[0].get("summary").is_none());
        assert!(with.steps()[0].get("summary").is_some());
        let dose = build_table(&without.value, TableKind::Tab3, StepSelect::One(1)).unwrap_err();
        assert!(!mentions_summary(&dose), "{dose}");
    }
}
