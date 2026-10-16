//! `actinv report` — render an existing result JSON as FISPACT-style tables (P132b).
//!
//! The FISPACT-II `TAB1`-`TAB4`, `GRAPH`, `NUCGRAPH` and DEPLETION analog. It is a pure function of the
//! result file: no solver, no data files. Each table is built from fields the result carries
//! (the P122 `summary` and its depletion table, `inventory`, `activity_Bq_per_g`, `heat_W_per_g`, `photon_source`,
//! `radiological`); a column whose source is absent is left out and named in a note, never
//! filled in. Every quantity keeps the result's own basis: `_per_g` columns are per gram of
//! initial material, the unsuffixed absolute columns scale them by `summary` `material.mass_g`.
//! `csv` and `tsv` carry every value as a shortest round-trip `{:e}` string; `text` and
//! `markdown` use six significant digits.

use crate::inspect::{render_table, Table};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};

pub const USAGE: &str = "usage: actinv report RESULT.json [--tables LIST] [--format csv|tsv|markdown|text] [--out DIR] [--step N|all] [--nuclides LIST]";

/// Detail printed by `actinv report --help`.
pub const HELP: &str = "Render a result JSON from `actinv run` as FISPACT-style tables.\n\
Nothing is recomputed.\n\
--tables LIST    comma-separated: tab1 (atoms, grams), tab2 (activity, heat),\n\
                 tab3 (ingestion/inhalation dose), tab4 (photon groups), totals,\n\
                 dominant, elements, timeseries (default: all of these) and\n\
                 depletion (per-nuclide depletion table; only when asked for)\n\
--format F       csv (default), tsv, markdown or text; csv and tsv carry exact {:e} values\n\
--out DIR        write one file per table (tab1.csv, ...) instead of standard output\n\
--step N|all     one-based step number, or every step (default)\n\
--nuclides LIST  comma-separated, e.g. Co60,Mn54: restrict tab1/tab2/depletion and add\n\
                 timeseries columns\n\
Per-gram columns (`_per_g`) are per gram of initial material; absolute columns need\n\
the result's `summary` section. A column the result cannot supply is omitted and\n\
named on standard error.";

#[derive(Debug)]
pub enum ReportError {
    /// Malformed command line (exit status 2).
    Usage(String),
    /// A well-formed request that could not be answered (exit status 1).
    Failure(String),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum TableKind {
    Tab1,
    Tab2,
    Tab3,
    Tab4,
    Totals,
    Dominant,
    Elements,
    Timeseries,
    Depletion,
}

/// The tables a report without `--tables` builds.
const ALL_TABLES: [TableKind; 8] = [
    TableKind::Tab1,
    TableKind::Tab2,
    TableKind::Tab3,
    TableKind::Tab4,
    TableKind::Totals,
    TableKind::Dominant,
    TableKind::Elements,
    TableKind::Timeseries,
];

/// Every table `--tables` accepts. The depletion table is left out of the default set: it exists
/// only for runs that asked for `summary.depletion`, and the default output stays as it was.
const NAMED_TABLES: [TableKind; 9] = [
    TableKind::Tab1,
    TableKind::Tab2,
    TableKind::Tab3,
    TableKind::Tab4,
    TableKind::Totals,
    TableKind::Dominant,
    TableKind::Elements,
    TableKind::Timeseries,
    TableKind::Depletion,
];

impl TableKind {
    fn name(self) -> &'static str {
        match self {
            Self::Tab1 => "tab1",
            Self::Tab2 => "tab2",
            Self::Tab3 => "tab3",
            Self::Tab4 => "tab4",
            Self::Totals => "totals",
            Self::Dominant => "dominant",
            Self::Elements => "elements",
            Self::Timeseries => "timeseries",
            Self::Depletion => "depletion",
        }
    }

    fn title(self) -> &'static str {
        match self {
            Self::Tab1 => "TAB1 atoms and mass per nuclide",
            Self::Tab2 => "TAB2 activity and heat per nuclide",
            Self::Tab3 => "TAB3 ingestion and inhalation dose",
            Self::Tab4 => "TAB4 photon source per energy group",
            Self::Totals => "Step totals",
            Self::Dominant => "Dominant nuclides",
            Self::Elements => "Elements",
            Self::Timeseries => "Time series (GRAPH, NUCGRAPH)",
            Self::Depletion => "Depletion of the initial nuclides",
        }
    }

    fn from_name(name: &str) -> Option<Self> {
        NAMED_TABLES.into_iter().find(|kind| kind.name() == name)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Format {
    Csv,
    Tsv,
    Markdown,
    Text,
}

impl Format {
    fn extension(self) -> &'static str {
        match self {
            Self::Csv => "csv",
            Self::Tsv => "tsv",
            Self::Markdown => "md",
            Self::Text => "txt",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum StepSelect {
    All,
    One(u64),
}

#[derive(Debug, PartialEq)]
struct Request {
    path: String,
    tables: Vec<TableKind>,
    format: Format,
    out: Option<String>,
    step: StepSelect,
    nuclides: Vec<String>,
}

/// One table cell. `Num` is a floating-point value; `Int` is a count or index.
#[derive(Debug, Clone, PartialEq)]
enum Cell {
    Text(String),
    Int(u64),
    Num(f64),
    Empty,
}

impl Cell {
    /// The exact form: `{:e}` is the shortest string that parses back to the same `f64`.
    fn exact(&self) -> String {
        match self {
            Self::Text(text) => text.clone(),
            Self::Int(value) => value.to_string(),
            Self::Num(value) => format!("{value:e}"),
            Self::Empty => String::new(),
        }
    }

    fn human(&self) -> String {
        match self {
            // `{:.5e}`: one digit before the point and five after, six significant digits.
            Self::Num(value) => format!("{value:.5e}"),
            other => other.exact(),
        }
    }
}

#[derive(Debug)]
struct ReportTable {
    kind: TableKind,
    headers: Vec<String>,
    rows: Vec<Vec<Cell>>,
    /// Columns left out or rows trimmed, with the reason.
    notes: Vec<String>,
}

/// A built table, or the reason it cannot be built from this result.
type Built = Result<ReportTable, String>;

struct Outcome {
    kind: TableKind,
    built: Built,
}

struct StepView<'a> {
    step: u64,
    time_s: f64,
    value: &'a Value,
}

// ---- command line ----

fn parse_names(text: &str, what: &str) -> Result<Vec<String>, String> {
    let mut names: Vec<String> = Vec::new();
    for part in text.split(',') {
        let name = part.trim();
        if name.is_empty() {
            return Err(format!("{what} list '{text}' has an empty entry"));
        }
        if names.iter().any(|existing| existing == name) {
            return Err(format!("{what} '{name}' is listed twice"));
        }
        names.push(name.to_string());
    }
    Ok(names)
}

fn parse_request(args: &[String]) -> Result<Request, String> {
    const VALUED: [&str; 5] = ["--tables", "--format", "--out", "--step", "--nuclides"];
    let mut positional: Vec<&str> = Vec::new();
    let mut values: BTreeMap<&str, &str> = BTreeMap::new();
    let mut index = 0;
    while index < args.len() {
        let arg = args[index].as_str();
        if arg.starts_with("--") {
            if !VALUED.contains(&arg) {
                return Err(format!("unknown report option {arg}"));
            }
            index += 1;
            let value = args
                .get(index)
                .ok_or_else(|| format!("{arg} needs a value"))?;
            if values.insert(arg, value.as_str()).is_some() {
                return Err(format!("{arg} given more than once"));
            }
        } else {
            positional.push(arg);
        }
        index += 1;
    }
    if positional.len() != 1 {
        return Err(format!(
            "expected exactly one RESULT.json, got {}",
            positional.len()
        ));
    }
    let tables = match values.get("--tables") {
        Some(text) => {
            let mut kinds = Vec::new();
            for name in parse_names(text, "table")? {
                let kind = TableKind::from_name(&name.to_ascii_lowercase()).ok_or_else(|| {
                    let known: Vec<&str> = NAMED_TABLES.iter().map(|kind| kind.name()).collect();
                    format!("unknown table '{name}' (expected {})", known.join(", "))
                })?;
                kinds.push(kind);
            }
            kinds
        }
        None => ALL_TABLES.to_vec(),
    };
    let format = match values.get("--format").copied() {
        None | Some("csv") => Format::Csv,
        Some("tsv") => Format::Tsv,
        Some("markdown") => Format::Markdown,
        Some("text") => Format::Text,
        Some(other) => {
            return Err(format!(
                "--format must be csv, tsv, markdown or text, got '{other}'"
            ))
        }
    };
    let step = match values.get("--step").copied() {
        None | Some("all") => StepSelect::All,
        Some(text) => match text.parse::<u64>() {
            Ok(number) if number >= 1 => StepSelect::One(number),
            _ => {
                return Err(format!(
                    "--step must be a positive integer or 'all', got '{text}'"
                ))
            }
        },
    };
    let nuclides = match values.get("--nuclides") {
        Some(text) => parse_names(text, "nuclide")?,
        None => Vec::new(),
    };
    Ok(Request {
        path: positional[0].to_string(),
        tables,
        format,
        out: values.get("--out").map(|value| value.to_string()),
        step,
        nuclides,
    })
}

// ---- JSON access ----

fn ptr_num(value: &Value, pointer: &str) -> Option<f64> {
    value.pointer(pointer).and_then(Value::as_f64)
}

fn num(value: &Value, key: &str, context: &str) -> Result<f64, String> {
    value
        .get(key)
        .and_then(Value::as_f64)
        .ok_or_else(|| format!("{context} lacks a numeric '{key}'"))
}

fn text<'a>(value: &'a Value, key: &str, context: &str) -> Result<&'a str, String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| format!("{context} lacks a string '{key}'"))
}

fn select_steps(result: &Value, select: StepSelect) -> Result<Vec<StepView<'_>>, String> {
    let steps = result
        .get("steps")
        .and_then(Value::as_array)
        .ok_or("the file has no `steps` array; it is not an `actinv run` result")?;
    if steps.is_empty() {
        return Err("the result has no steps".into());
    }
    let mut views = Vec::with_capacity(steps.len());
    for (index, value) in steps.iter().enumerate() {
        let step = value
            .get("step")
            .and_then(Value::as_u64)
            .unwrap_or(index as u64 + 1);
        let time_s = value
            .get("t_s")
            .and_then(Value::as_f64)
            .ok_or_else(|| format!("step {step} lacks a numeric 't_s'"))?;
        views.push(StepView {
            step,
            time_s,
            value,
        });
    }
    match select {
        StepSelect::All => Ok(views),
        StepSelect::One(wanted) => {
            views.retain(|view| view.step == wanted);
            if views.is_empty() {
                Err(format!(
                    "step {wanted} is not in the result (steps 1 to {})",
                    steps.len()
                ))
            } else {
                Ok(views)
            }
        }
    }
}

fn names_of_inventory<'a>(step: &StepView<'a>) -> Vec<&'a str> {
    step.value
        .get("inventory")
        .and_then(Value::as_array)
        .map(|entries| {
            entries
                .iter()
                .filter_map(|entry| entry.get("nuclide").and_then(Value::as_str))
                .collect()
        })
        .unwrap_or_default()
}

/// Every selected step carries the summary's per-nuclide table.
fn has_summary_nuclides(steps: &[StepView]) -> bool {
    steps.iter().all(|step| {
        step.value
            .pointer("/summary/nuclides")
            .is_some_and(Value::is_array)
    })
}

/// `material.mass_g` per step, when every selected step carries the summary's absolute scale.
fn mass_multipliers(steps: &[StepView]) -> Option<Vec<f64>> {
    steps
        .iter()
        .map(|step| ptr_num(step.value, "/summary/totals/absolute/multiplier"))
        .collect()
}

fn omitted_note(columns: &[&str], hint: &str) -> Option<String> {
    (!columns.is_empty()).then(|| {
        format!(
            "columns omitted because the result does not carry them ({hint}): {}",
            columns.join(", ")
        )
    })
}

/// Names in the step's `summary.nuclides`.
fn summary_names<'a>(step: &StepView<'a>) -> BTreeSet<&'a str> {
    step.value
        .pointer("/summary/nuclides")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|entry| entry.get("nuclide").and_then(Value::as_str))
        .collect()
}

/// Requested nuclides the result carries (inventory or activity) but `summary.nuclides` lacks:
/// the summary trimmed them (`min_atoms` or `include_stable`), so the summary tables have no row.
fn trimmed_requested(steps: &[StepView], nuclides: &[String]) -> Vec<String> {
    let mut names = BTreeSet::new();
    for step in steps {
        let listed = summary_names(step);
        for nuclide in nuclides {
            let carried = names_of_inventory(step).contains(&nuclide.as_str())
                || step
                    .value
                    .get("activity_Bq_per_g")
                    .is_some_and(|map| map.get(nuclide.as_str()).is_some());
            if carried && !listed.contains(nuclide.as_str()) {
                names.insert(nuclide.clone());
            }
        }
    }
    names.into_iter().collect()
}

fn trimmed_requested_note(names: &[String]) -> Option<String> {
    (!names.is_empty()).then(|| {
        format!(
            "requested nuclides in the result's inventory but not in summary.nuclides \
             (min_atoms or include_stable trimmed them), so they have no rows: {}",
            names.join(", ")
        )
    })
}

fn by_atoms_then_name<T>(rows: &mut [T], key: impl Fn(&T) -> (f64, &str)) {
    rows.sort_by(|a, b| {
        let (atoms_a, name_a) = key(a);
        let (atoms_b, name_b) = key(b);
        atoms_b.total_cmp(&atoms_a).then_with(|| name_a.cmp(name_b))
    });
}

fn head(step: &StepView) -> Vec<Cell> {
    vec![Cell::Int(step.step), Cell::Num(step.time_s)]
}

fn headers(first: &[&str]) -> Vec<String> {
    first.iter().map(|name| name.to_string()).collect()
}

const SUMMARY_HINT: &str = "add a `summary` section to the spec";

// ---- TAB1 and TAB2 ----

struct AtomsRow {
    name: String,
    atoms: f64,
    grams: Option<f64>,
}

fn tab1(steps: &[StepView], nuclides: &[String]) -> Result<Built, String> {
    let summary = has_summary_nuclides(steps);
    let multipliers = mass_multipliers(steps);
    let mut trimmed = 0usize;
    let mut table_rows = Vec::new();
    for (index, step) in steps.iter().enumerate() {
        let mut rows: Vec<AtomsRow> = Vec::new();
        if summary {
            let context = format!("step {} summary.nuclides entry", step.step);
            for entry in step
                .value
                .pointer("/summary/nuclides")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
            {
                rows.push(AtomsRow {
                    name: text(entry, "nuclide", &context)?.to_string(),
                    atoms: num(entry, "atoms", &context)?,
                    grams: Some(num(entry, "grams", &context)?),
                });
            }
            let listed: BTreeSet<&str> = rows.iter().map(|row| row.name.as_str()).collect();
            trimmed += names_of_inventory(step)
                .into_iter()
                .filter(|name| !listed.contains(name))
                .count();
        } else {
            let Some(entries) = step.value.get("inventory").and_then(Value::as_array) else {
                return Ok(Err(format!("step {} has no `inventory`", step.step)));
            };
            let context = format!("step {} inventory entry", step.step);
            for entry in entries {
                rows.push(AtomsRow {
                    name: text(entry, "nuclide", &context)?.to_string(),
                    atoms: num(entry, "atoms_per_g", &context)?,
                    grams: None,
                });
            }
        }
        if !nuclides.is_empty() {
            rows.retain(|row| nuclides.contains(&row.name));
        }
        by_atoms_then_name(&mut rows, |row| (row.atoms, row.name.as_str()));
        for row in rows {
            let mut cells = head(step);
            cells.push(Cell::Text(row.name));
            cells.push(Cell::Num(row.atoms));
            if let Some(grams) = row.grams {
                cells.push(Cell::Num(grams));
            }
            if let Some(multipliers) = &multipliers {
                cells.push(Cell::Num(row.atoms * multipliers[index]));
                if let Some(grams) = row.grams {
                    cells.push(Cell::Num(grams * multipliers[index]));
                }
            }
            table_rows.push(cells);
        }
    }
    let mut header = headers(&["step", "time_s", "nuclide", "atoms_per_g"]);
    let mut omitted = Vec::new();
    if summary {
        header.push("mass_g_per_g".into());
    } else {
        omitted.push("mass_g_per_g");
    }
    if multipliers.is_some() {
        header.push("atoms".into());
    } else {
        omitted.push("atoms");
    }
    if multipliers.is_some() && summary {
        header.push("mass_g".into());
    } else {
        omitted.push("mass_g");
    }
    let mut notes: Vec<String> = omitted_note(&omitted, SUMMARY_HINT).into_iter().collect();
    if trimmed > 0 {
        notes.push(format!(
            "{trimmed} inventory entries are not in summary.nuclides (min_atoms or \
             include_stable); the table lists the summary rows"
        ));
    }
    if summary {
        notes.extend(trimmed_requested_note(&trimmed_requested(steps, nuclides)));
    }
    Ok(Ok(ReportTable {
        kind: TableKind::Tab1,
        headers: header,
        rows: table_rows,
        notes,
    }))
}

struct ActivityRow {
    name: String,
    activity: f64,
    /// alpha, beta, gamma W per gram.
    heat: Option<[f64; 3]>,
    half_life_s: Option<f64>,
}

fn tab2(steps: &[StepView], nuclides: &[String]) -> Result<Built, String> {
    let summary = has_summary_nuclides(steps);
    let multipliers = mass_multipliers(steps);
    let mut trimmed = 0usize;
    let mut table_rows = Vec::new();
    for (index, step) in steps.iter().enumerate() {
        let mut rows: Vec<ActivityRow> = Vec::new();
        if summary {
            let listed = summary_names(step);
            trimmed += step
                .value
                .get("activity_Bq_per_g")
                .and_then(Value::as_object)
                .into_iter()
                .flatten()
                .filter(|(name, value)| {
                    value.as_f64().is_some_and(|activity| activity > 0.0)
                        && !listed.contains(name.as_str())
                })
                .count();
            let context = format!("step {} summary.nuclides entry", step.step);
            for entry in step
                .value
                .pointer("/summary/nuclides")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
            {
                let activity = num(entry, "activity_Bq", &context)?;
                if activity > 0.0 {
                    rows.push(ActivityRow {
                        name: text(entry, "nuclide", &context)?.to_string(),
                        activity,
                        heat: Some([
                            num(entry, "alpha_W", &context)?,
                            num(entry, "beta_W", &context)?,
                            num(entry, "gamma_W", &context)?,
                        ]),
                        half_life_s: entry.get("half_life_s").and_then(Value::as_f64),
                    });
                }
            }
        } else {
            let Some(map) = step
                .value
                .get("activity_Bq_per_g")
                .and_then(Value::as_object)
            else {
                return Ok(Err(format!(
                    "step {} has no `activity_Bq_per_g`",
                    step.step
                )));
            };
            for (name, value) in map {
                let activity = value.as_f64().ok_or_else(|| {
                    format!("step {} activity for '{name}' is not a number", step.step)
                })?;
                if activity > 0.0 {
                    rows.push(ActivityRow {
                        name: name.clone(),
                        activity,
                        heat: None,
                        half_life_s: None,
                    });
                }
            }
        }
        if !nuclides.is_empty() {
            rows.retain(|row| nuclides.contains(&row.name));
        }
        by_atoms_then_name(&mut rows, |row| (row.activity, row.name.as_str()));
        for row in rows {
            let mut cells = head(step);
            cells.push(Cell::Text(row.name));
            cells.push(Cell::Num(row.activity));
            if let Some([alpha, beta, gamma]) = row.heat {
                cells.push(Cell::Num(alpha + beta + gamma));
                cells.push(Cell::Num(alpha));
                cells.push(Cell::Num(beta));
                cells.push(Cell::Num(gamma));
                cells.push(row.half_life_s.map_or(Cell::Empty, Cell::Num));
            }
            if let Some(multipliers) = &multipliers {
                cells.push(Cell::Num(row.activity * multipliers[index]));
                if let Some([alpha, beta, gamma]) = row.heat {
                    cells.push(Cell::Num((alpha + beta + gamma) * multipliers[index]));
                }
            }
            table_rows.push(cells);
        }
    }
    let mut header = headers(&["step", "time_s", "nuclide", "activity_Bq_per_g"]);
    let mut omitted = Vec::new();
    if summary {
        for name in [
            "heat_W_per_g",
            "alpha_W_per_g",
            "beta_W_per_g",
            "gamma_W_per_g",
            "half_life_s",
        ] {
            header.push(name.into());
        }
    } else {
        omitted.extend([
            "heat_W_per_g",
            "alpha_W_per_g",
            "beta_W_per_g",
            "gamma_W_per_g",
            "half_life_s",
        ]);
    }
    if multipliers.is_some() {
        header.push("activity_Bq".into());
    } else {
        omitted.push("activity_Bq");
    }
    if multipliers.is_some() && summary {
        header.push("heat_W".into());
    } else {
        omitted.push("heat_W");
    }
    let mut notes: Vec<String> = omitted_note(&omitted, SUMMARY_HINT).into_iter().collect();
    if trimmed > 0 {
        notes.push(format!(
            "{trimmed} nuclides with activity are not in summary.nuclides (min_atoms or \
             include_stable); the table lists the summary rows"
        ));
    }
    if summary {
        notes.extend(trimmed_requested_note(&trimmed_requested(steps, nuclides)));
    }
    Ok(Ok(ReportTable {
        kind: TableKind::Tab2,
        headers: header,
        rows: table_rows,
        notes,
    }))
}

// ---- TAB3 and TAB4 ----

fn tab3(steps: &[StepView]) -> Result<Built, String> {
    let mut rows = Vec::new();
    let mut other_units = BTreeSet::new();
    let mut any = false;
    for step in steps {
        let Some(responses) = step
            .value
            .pointer("/radiological/responses")
            .and_then(Value::as_array)
        else {
            continue;
        };
        any = true;
        let context = format!("step {} radiological response", step.step);
        for response in responses {
            let unit = text(response, "unit", &context)?;
            if !unit.starts_with("Sv") {
                other_units.insert(unit.to_string());
                continue;
            }
            let missing: Vec<&str> = response
                .get("missing_active_nuclides")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .collect();
            let mut cells = head(step);
            cells.push(Cell::Text(text(response, "id", &context)?.to_string()));
            cells.push(Cell::Num(num(response, "value", &context)?));
            cells.push(Cell::Text(unit.to_string()));
            for key in [
                "covered_activity_Bq_per_g",
                "missing_activity_Bq_per_g",
                "activity_coverage_fraction",
            ] {
                cells.push(Cell::Num(num(response, key, &context)?));
            }
            let count = response
                .get("contributing_nuclide_count")
                .and_then(Value::as_u64)
                .ok_or_else(|| format!("{context} lacks 'contributing_nuclide_count'"))?;
            cells.push(Cell::Int(count));
            cells.push(Cell::Text(missing.join(";")));
            rows.push(cells);
        }
    }
    if !any {
        return Ok(Err(
            "the result has no `radiological` responses (declare `radiological` in the spec)"
                .into(),
        ));
    }
    if rows.is_empty() {
        let units: Vec<String> = other_units.into_iter().collect();
        return Ok(Err(format!(
            "the result has radiological responses but none is an ingestion or inhalation dose \
             (units: {})",
            units.join(", ")
        )));
    }
    Ok(Ok(ReportTable {
        kind: TableKind::Tab3,
        headers: headers(&[
            "step",
            "time_s",
            "response",
            "value",
            "unit",
            "covered_activity_Bq_per_g",
            "missing_activity_Bq_per_g",
            "activity_coverage_fraction",
            "contributing_nuclides",
            "missing_active_nuclides",
        ]),
        rows,
        notes: vec![
            "each value is the sum over nuclides; a per-nuclide dose needs the coefficient table, \
             which the result does not carry"
                .into(),
        ],
    }))
}

fn tab4(steps: &[StepView]) -> Result<Built, String> {
    let mut rows = Vec::new();
    let mut without = Vec::new();
    for step in steps {
        let Some(groups) = step
            .value
            .pointer("/photon_source/groups")
            .and_then(Value::as_array)
        else {
            without.push(step.step.to_string());
            continue;
        };
        let context = format!("step {} photon group", step.step);
        for (index, group) in groups.iter().enumerate() {
            let mut cells = head(step);
            cells.push(Cell::Int(index as u64 + 1));
            for key in [
                "low_eV",
                "high_eV",
                "centroid_eV",
                "photons_s_g",
                "power_W_g",
                "photons_s",
                "power_W",
            ] {
                cells.push(Cell::Num(num(group, key, &context)?));
            }
            rows.push(cells);
        }
    }
    if without.len() == steps.len() {
        return Ok(Err(
            "the result has no `photon_source` groups (the spec must request photon output)".into(),
        ));
    }
    let mut notes = Vec::new();
    if !without.is_empty() {
        notes.push(format!(
            "steps without a photon source: {}",
            without.join(", ")
        ));
    }
    Ok(Ok(ReportTable {
        kind: TableKind::Tab4,
        headers: headers(&[
            "step",
            "time_s",
            "group",
            "low_eV",
            "high_eV",
            "centroid_eV",
            "photons_s_g",
            "power_W_g",
            "photons_s",
            "power_W",
        ]),
        rows,
        notes,
    }))
}

// ---- per-step tables: totals, timeseries ----

struct Column {
    header: String,
    cells: Vec<Cell>,
}

fn pointer_column(steps: &[StepView], header: &str, pointer: &str, int: bool) -> Option<Column> {
    let cells: Option<Vec<Cell>> = steps
        .iter()
        .map(|step| {
            let value = step.value.pointer(pointer)?;
            if int {
                value.as_u64().map(Cell::Int)
            } else {
                value.as_f64().map(Cell::Num)
            }
        })
        .collect();
    cells.map(|cells| Column {
        header: header.to_string(),
        cells,
    })
}

/// Total activity, the sum over the result's activity map in its (sorted) key order.
fn activity_total_column(steps: &[StepView]) -> Option<Column> {
    let cells: Option<Vec<Cell>> = steps
        .iter()
        .map(|step| {
            let map = step.value.get("activity_Bq_per_g")?.as_object()?;
            let values: Option<Vec<f64>> = map.values().map(Value::as_f64).collect();
            Some(Cell::Num(values?.iter().sum()))
        })
        .collect();
    cells.map(|cells| Column {
        header: "activity_Bq_per_g".into(),
        cells,
    })
}

const BASE_HEAT: [(&str, &str); 4] = [
    ("heat_total_W_per_g", "/heat_W_per_g/total"),
    ("heat_alpha_W_per_g", "/heat_W_per_g/alpha"),
    ("heat_beta_W_per_g", "/heat_W_per_g/beta"),
    ("heat_gamma_W_per_g", "/heat_W_per_g/gamma"),
];

/// Summary-derived total columns: (header, pointer, integer).
const SUMMARY_TOTALS: [(&str, &str, bool); 18] = [
    ("activity_Ci_per_g", "/summary/totals/activity_Ci", false),
    ("alpha_activity_Bq_per_g", "/summary/totals/alpha_Bq", false),
    ("beta_activity_Bq_per_g", "/summary/totals/beta_Bq", false),
    ("gamma_activity_Bq_per_g", "/summary/totals/gamma_Bq", false),
    (
        "activity_excl_H3_Bq_per_g",
        "/summary/totals/activity_excl_H3_Bq",
        false,
    ),
    (
        "heat_excl_H3_W_per_g",
        "/summary/totals/heat_excl_H3_W",
        false,
    ),
    ("mass_g_per_g", "/summary/totals/mass_g", false),
    ("n_nuclides", "/summary/totals/n_nuclides", true),
    (
        "mode_beta_minus_Bq_per_g",
        "/summary/totals/activity_by_mode/beta_minus_Bq",
        false,
    ),
    (
        "mode_ec_beta_plus_Bq_per_g",
        "/summary/totals/activity_by_mode/ec_beta_plus_Bq",
        false,
    ),
    (
        "mode_isomeric_transition_Bq_per_g",
        "/summary/totals/activity_by_mode/isomeric_transition_Bq",
        false,
    ),
    (
        "mode_alpha_Bq_per_g",
        "/summary/totals/activity_by_mode/alpha_Bq",
        false,
    ),
    (
        "mode_neutron_Bq_per_g",
        "/summary/totals/activity_by_mode/neutron_Bq",
        false,
    ),
    (
        "mode_spontaneous_fission_Bq_per_g",
        "/summary/totals/activity_by_mode/spontaneous_fission_Bq",
        false,
    ),
    (
        "mode_proton_Bq_per_g",
        "/summary/totals/activity_by_mode/proton_Bq",
        false,
    ),
    (
        "mode_other_Bq_per_g",
        "/summary/totals/activity_by_mode/other_Bq",
        false,
    ),
    (
        "material_mass_g",
        "/summary/totals/absolute/multiplier",
        false,
    ),
    ("activity_Bq", "/summary/totals/absolute/activity_Bq", false),
];

/// Absolute and per-cm3 columns that follow `SUMMARY_TOTALS`.
const SUMMARY_SCALED: [(&str, &str); 4] = [
    ("heat_W", "/summary/totals/absolute/heat_W"),
    ("density_g_cm3", "/summary/totals/per_cm3/multiplier"),
    ("activity_Bq_per_cm3", "/summary/totals/per_cm3/activity_Bq"),
    ("heat_W_per_cm3", "/summary/totals/per_cm3/heat_W"),
];

fn per_step_table(
    kind: TableKind,
    steps: &[StepView],
    columns: Vec<Column>,
    notes: Vec<String>,
) -> ReportTable {
    let mut header = headers(&["step", "time_s"]);
    header.extend(columns.iter().map(|column| column.header.clone()));
    let rows = steps
        .iter()
        .enumerate()
        .map(|(index, step)| {
            let mut cells = head(step);
            cells.extend(columns.iter().map(|column| column.cells[index].clone()));
            cells
        })
        .collect();
    ReportTable {
        kind,
        headers: header,
        rows,
        notes,
    }
}

/// Activity total and the four heat totals, the base fields of every result.
fn base_total_columns(
    steps: &[StepView],
    columns: &mut Vec<Column>,
    omitted: &mut Vec<&'static str>,
) {
    match activity_total_column(steps) {
        Some(column) => columns.push(column),
        None => omitted.push("activity_Bq_per_g"),
    }
    for (header, pointer) in BASE_HEAT {
        match pointer_column(steps, header, pointer, false) {
            Some(column) => columns.push(column),
            None => omitted.push(header),
        }
    }
}

fn totals(steps: &[StepView]) -> Built {
    let mut columns = Vec::new();
    let mut omitted: Vec<&'static str> = Vec::new();
    base_total_columns(steps, &mut columns, &mut omitted);
    let scaled = SUMMARY_SCALED.map(|(header, pointer)| (header, pointer, false));
    for (header, pointer, int) in SUMMARY_TOTALS.into_iter().chain(scaled) {
        match pointer_column(steps, header, pointer, int) {
            Some(column) => columns.push(column),
            None => omitted.push(header),
        }
    }
    // Per-cm3 columns exist only when the material has a density; do not call that a missing
    // summary.
    let summary_present = steps
        .iter()
        .all(|step| step.value.pointer("/summary/totals").is_some());
    if summary_present {
        omitted.retain(|header| {
            !matches!(
                *header,
                "density_g_cm3" | "activity_Bq_per_cm3" | "heat_W_per_cm3"
            )
        });
    }
    let hint = if summary_present {
        "the result's `summary` lacks these fields"
    } else {
        SUMMARY_HINT
    };
    let notes = omitted_note(&omitted, hint).into_iter().collect();
    Ok(per_step_table(TableKind::Totals, steps, columns, notes))
}

fn timeseries(steps: &[StepView], nuclides: &[String]) -> Built {
    let mut columns = Vec::new();
    let mut omitted: Vec<&'static str> = Vec::new();
    base_total_columns(steps, &mut columns, &mut omitted);
    // Dose-rate columns appear when any step has the value; a step without it stays empty.
    for (header, pointer) in [
        (
            "contact_gamma_air_dose_proxy_Gy_h",
            "/photon_source/contact_gamma_air_dose_proxy_Gy_h",
        ),
        (
            "point_source_gamma_air_dose_Sv_h",
            "/photon_source/point_source_dose/gamma_air_dose_Sv_h",
        ),
    ] {
        let cells: Vec<Cell> = steps
            .iter()
            .map(|step| ptr_num(step.value, pointer).map_or(Cell::Empty, Cell::Num))
            .collect();
        if cells.iter().any(|cell| *cell != Cell::Empty) {
            columns.push(Column {
                header: header.to_string(),
                cells,
            });
        }
    }
    let mut responses: Vec<(String, String)> = Vec::new();
    for step in steps {
        for response in step
            .value
            .pointer("/radiological/responses")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            if let (Some(id), Some(unit)) = (
                response.get("id").and_then(Value::as_str),
                response.get("unit").and_then(Value::as_str),
            ) {
                if !responses.iter().any(|(known, _)| known == id) {
                    responses.push((id.to_string(), unit.to_string()));
                }
            }
        }
    }
    for (id, unit) in responses {
        let cells = steps
            .iter()
            .map(|step| {
                step.value
                    .pointer("/radiological/responses")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                    .find(|response| {
                        response.get("id").and_then(Value::as_str) == Some(id.as_str())
                    })
                    .and_then(|response| response.get("value").and_then(Value::as_f64))
                    .map_or(Cell::Empty, Cell::Num)
            })
            .collect();
        columns.push(Column {
            header: format!("radiological.{id}[{unit}]"),
            cells,
        });
    }
    for nuclide in nuclides {
        let atoms = steps.iter().map(|step| {
            let entries = step.value.get("inventory").and_then(Value::as_array);
            let found = entries
                .into_iter()
                .flatten()
                .find(|entry| {
                    entry.get("nuclide").and_then(Value::as_str) == Some(nuclide.as_str())
                })
                .and_then(|entry| entry.get("atoms_per_g").and_then(Value::as_f64));
            Cell::Num(found.unwrap_or(0.0))
        });
        let activity = steps.iter().map(|step| {
            let found = step
                .value
                .get("activity_Bq_per_g")
                .and_then(|map| map.get(nuclide.as_str()))
                .and_then(Value::as_f64);
            Cell::Num(found.unwrap_or(0.0))
        });
        columns.push(Column {
            header: format!("{nuclide}_atoms_per_g"),
            cells: atoms.collect(),
        });
        columns.push(Column {
            header: format!("{nuclide}_activity_Bq_per_g"),
            cells: activity.collect(),
        });
    }
    let notes = omitted_note(&omitted, "the spec's `outputs` may have dropped them")
        .into_iter()
        .collect();
    Ok(per_step_table(TableKind::Timeseries, steps, columns, notes))
}

// ---- dominant and elements ----

const DOMINANT_LISTS: [(&str, &str); 5] = [
    ("activity", "Bq_per_g"),
    ("heat", "W_per_g"),
    ("alpha_heat", "W_per_g"),
    ("beta_heat", "W_per_g"),
    ("gamma_heat", "W_per_g"),
];

fn dominant(steps: &[StepView]) -> Result<Built, String> {
    let mut rows = Vec::new();
    let mut without = Vec::new();
    for step in steps {
        let Some(block) = step.value.pointer("/summary/dominant") else {
            without.push(step.step.to_string());
            continue;
        };
        for (quantity, unit) in DOMINANT_LISTS {
            let context = format!("step {} summary.dominant.{quantity}", step.step);
            let list = block
                .get(quantity)
                .ok_or_else(|| format!("{context} is missing"))?;
            let total = num(list, "total", &context)?;
            let count = |key: &str| {
                list.get(key)
                    .and_then(Value::as_u64)
                    .ok_or_else(|| format!("{context} lacks '{key}'"))
            };
            let (n90, n99) = (count("n_for_90_percent")?, count("n_for_99_percent")?);
            let top = list
                .get("top")
                .and_then(Value::as_array)
                .ok_or_else(|| format!("{context} lacks 'top'"))?;
            for (rank, entry) in top.iter().enumerate() {
                let mut cells = head(step);
                cells.push(Cell::Text(quantity.to_string()));
                cells.push(Cell::Int(rank as u64 + 1));
                cells.push(Cell::Text(text(entry, "nuclide", &context)?.to_string()));
                cells.push(Cell::Num(num(entry, "value", &context)?));
                cells.push(Cell::Text(unit.to_string()));
                cells.push(Cell::Num(num(entry, "percent", &context)?));
                cells.push(Cell::Num(num(entry, "cumulative_percent", &context)?));
                cells.push(Cell::Num(total));
                cells.push(Cell::Int(n90));
                cells.push(Cell::Int(n99));
                rows.push(cells);
            }
        }
    }
    if without.len() == steps.len() {
        return Ok(Err(
            "the result has no `summary.dominant` lists (add a `summary` section to the spec)"
                .into(),
        ));
    }
    let mut notes = Vec::new();
    if !without.is_empty() {
        notes.push(format!("steps without a summary: {}", without.join(", ")));
    }
    Ok(Ok(ReportTable {
        kind: TableKind::Dominant,
        headers: headers(&[
            "step",
            "time_s",
            "quantity",
            "rank",
            "nuclide",
            "value",
            "unit",
            "percent",
            "cumulative_percent",
            "list_total",
            "n_for_90_percent",
            "n_for_99_percent",
        ]),
        rows,
        notes,
    }))
}

fn elements(steps: &[StepView]) -> Result<Built, String> {
    let mut rows = Vec::new();
    let mut without = Vec::new();
    for step in steps {
        let Some(entries) = step
            .value
            .pointer("/summary/elements")
            .and_then(Value::as_array)
        else {
            without.push(step.step.to_string());
            continue;
        };
        let context = format!("step {} summary.elements entry", step.step);
        for entry in entries {
            let z = entry
                .get("Z")
                .and_then(Value::as_u64)
                .ok_or_else(|| format!("{context} lacks 'Z'"))?;
            let mut cells = head(step);
            cells.push(Cell::Int(z));
            cells.push(Cell::Text(text(entry, "symbol", &context)?.to_string()));
            for key in [
                "atoms",
                "grams",
                "activity_Bq",
                "alpha_W",
                "beta_W",
                "gamma_W",
            ] {
                cells.push(Cell::Num(num(entry, key, &context)?));
            }
            rows.push(cells);
        }
    }
    if without.len() == steps.len() {
        return Ok(Err(
            "the result has no `summary.elements` table (add a `summary` section to the spec)"
                .into(),
        ));
    }
    let mut notes = Vec::new();
    if !without.is_empty() {
        notes.push(format!(
            "steps without an element table: {}",
            without.join(", ")
        ));
    }
    Ok(Ok(ReportTable {
        kind: TableKind::Elements,
        headers: headers(&[
            "step",
            "time_s",
            "Z",
            "element",
            "atoms_per_g",
            "mass_g_per_g",
            "activity_Bq_per_g",
            "alpha_W_per_g",
            "beta_W_per_g",
            "gamma_W_per_g",
        ]),
        rows,
        notes,
    }))
}

// ---- assembly ----

// ---- depletion ----

/// One row per initial nuclide per step from `summary.depletion`, with the removal channels
/// ranked as the result lists them ("MT102 60%; decay 40%").
fn depletion(steps: &[StepView], nuclides: &[String]) -> Result<Built, String> {
    let mut rows = Vec::new();
    let mut without = Vec::new();
    let mut uncertainty = false;
    let mut listed: BTreeSet<String> = BTreeSet::new();
    for step in steps {
        let Some(block) = step.value.pointer("/summary/depletion") else {
            without.push(step.step.to_string());
            continue;
        };
        let context = format!("step {} summary.depletion", step.step);
        let model = text(block, "model", &context)?.to_string();
        let entries = block
            .get("nuclides")
            .and_then(Value::as_array)
            .ok_or_else(|| format!("{context} lacks 'nuclides'"))?;
        for entry in entries {
            let context = format!("step {} summary.depletion nuclide", step.step);
            let name = text(entry, "nuclide", &context)?.to_string();
            listed.insert(name.clone());
            if !nuclides.is_empty() && !nuclides.contains(&name) {
                continue;
            }
            let relative = entry
                .get("removal_rate_relative_standard_uncertainty")
                .and_then(Value::as_f64);
            uncertainty |= relative.is_some();
            let channels = entry
                .get("channels")
                .and_then(Value::as_array)
                .ok_or_else(|| format!("{context} '{name}' lacks 'channels'"))?
                .iter()
                .map(|channel| {
                    let label = text(channel, "channel", &context)?;
                    let share = num(channel, "share", &context)?;
                    Ok(format!("{label} {:.4}%", share * 100.0))
                })
                .collect::<Result<Vec<_>, String>>()?
                .join("; ");
            let mut cells = head(step);
            cells.push(Cell::Text(name));
            cells.push(Cell::Text(model.clone()));
            for key in [
                "initial_atoms_per_g",
                "atoms_start_per_g",
                "atoms_end_per_g",
                "depleted_atoms_per_g",
                "depleted_fraction",
                "cumulative_depleted_atoms_per_g",
                "cumulative_depleted_fraction",
                "removal_rate_per_s",
            ] {
                cells.push(Cell::Num(num(entry, key, &context)?));
            }
            cells.push(relative.map_or(Cell::Empty, Cell::Num));
            cells.push(Cell::Text(channels));
            rows.push(cells);
        }
    }
    if without.len() == steps.len() {
        return Ok(Err(
            "the result has no `summary.depletion` table (set `summary.depletion` to true in the spec)"
                .into(),
        ));
    }
    let mut header = headers(&[
        "step",
        "time_s",
        "nuclide",
        "model",
        "initial_atoms_per_g",
        "atoms_start_per_g",
        "atoms_end_per_g",
        "depleted_atoms_per_g",
        "depleted_fraction",
        "cumulative_depleted_atoms_per_g",
        "cumulative_depleted_fraction",
        "removal_rate_per_s",
        "removal_rate_relative_standard_uncertainty",
        "channels",
    ]);
    let mut notes = Vec::new();
    if !uncertainty {
        // the column is empty throughout: leave it out
        let position = header
            .iter()
            .position(|name| name == "removal_rate_relative_standard_uncertainty")
            .expect("header has the column");
        header.remove(position);
        for row in &mut rows {
            row.remove(position);
        }
    }
    if !without.is_empty() {
        notes.push(format!(
            "steps without a depletion table: {}",
            without.join(", ")
        ));
    }
    let absent: Vec<&str> = nuclides
        .iter()
        .filter(|name| !listed.contains(name.as_str()))
        .map(String::as_str)
        .collect();
    if !absent.is_empty() {
        notes.push(format!(
            "not in the depletion table (it follows the initial-material nuclides only): {}",
            absent.join(", ")
        ));
    }
    Ok(Ok(ReportTable {
        kind: TableKind::Depletion,
        headers: header,
        rows,
        notes,
    }))
}

fn check_nuclides(steps: &[StepView], nuclides: &[String]) -> Result<(), String> {
    for nuclide in nuclides {
        let present = steps.iter().any(|step| {
            names_of_inventory(step).contains(&nuclide.as_str())
                || step
                    .value
                    .get("activity_Bq_per_g")
                    .is_some_and(|map| map.get(nuclide.as_str()).is_some())
        });
        if !present {
            return Err(format!(
                "nuclide '{nuclide}' is in no selected step's inventory or activity; \
                 names are written as in the result, for example Co60 or Ta182m1"
            ));
        }
    }
    Ok(())
}

fn build_tables(result: &Value, request: &Request) -> Result<Vec<Outcome>, String> {
    let steps = select_steps(result, request.step)?;
    check_nuclides(&steps, &request.nuclides)?;
    let mut outcomes = Vec::new();
    for &kind in &request.tables {
        let built = match kind {
            TableKind::Tab1 => tab1(&steps, &request.nuclides)?,
            TableKind::Tab2 => tab2(&steps, &request.nuclides)?,
            TableKind::Tab3 => tab3(&steps)?,
            TableKind::Tab4 => tab4(&steps)?,
            TableKind::Totals => totals(&steps),
            TableKind::Dominant => dominant(&steps)?,
            TableKind::Elements => elements(&steps)?,
            TableKind::Timeseries => timeseries(&steps, &request.nuclides),
            TableKind::Depletion => depletion(&steps, &request.nuclides)?,
        };
        outcomes.push(Outcome { kind, built });
    }
    Ok(outcomes)
}

// ---- rendering ----

fn delimited_cell(cell: &str, format: Format) -> String {
    match format {
        Format::Csv if cell.contains([',', '"', '\n', '\r']) => {
            format!("\"{}\"", cell.replace('"', "\"\""))
        }
        Format::Tsv => cell.replace(['\t', '\n', '\r'], " "),
        _ => cell.to_string(),
    }
}

fn delimited(table: &ReportTable, format: Format) -> String {
    let separator = if format == Format::Tsv { "\t" } else { "," };
    let line = |cells: Vec<String>| -> String {
        cells
            .iter()
            .map(|cell| delimited_cell(cell, format))
            .collect::<Vec<_>>()
            .join(separator)
    };
    let mut lines = vec![line(table.headers.clone())];
    for row in &table.rows {
        lines.push(line(row.iter().map(Cell::exact).collect()));
    }
    lines.join("\n")
}

fn markdown(table: &ReportTable) -> String {
    let escape = |cell: String| cell.replace('|', "\\|");
    let line = |cells: Vec<String>| -> String {
        format!(
            "| {} |",
            cells
                .into_iter()
                .map(escape)
                .collect::<Vec<_>>()
                .join(" | ")
        )
    };
    let mut out = format!("### {}\n\n", table.kind.title());
    out.push_str(&line(table.headers.clone()));
    out.push('\n');
    out.push_str(&line(
        table.headers.iter().map(|_| "---".to_string()).collect(),
    ));
    for row in &table.rows {
        out.push('\n');
        out.push_str(&line(row.iter().map(Cell::human).collect()));
    }
    for note in &table.notes {
        out.push_str(&format!("\n\nNote: {note}"));
    }
    out
}

fn plain(table: &ReportTable) -> String {
    let mut out = render_table(&Table {
        title: table.kind.title().to_string(),
        headers: table.headers.clone(),
        rows: table
            .rows
            .iter()
            .map(|row| row.iter().map(Cell::human).collect())
            .collect(),
    });
    for note in &table.notes {
        out.push_str(&format!("\nNote: {note}"));
    }
    out
}

fn render(table: &ReportTable, format: Format) -> String {
    match format {
        Format::Csv | Format::Tsv => delimited(table, format),
        Format::Markdown => markdown(table),
        Format::Text => plain(table),
    }
}

fn render_unavailable(kind: TableKind, reason: &str, format: Format) -> String {
    match format {
        Format::Markdown => format!("### {}\n\nUnavailable: {reason}", kind.title()),
        _ => format!("{}\n  unavailable: {reason}", kind.title()),
    }
}

/// The whole report as one stream. Delimited formats name each table in a `# name` line when
/// more than one is present, and omit unavailable tables (their reasons go to standard error).
fn render_stream(outcomes: &[Outcome], format: Format) -> String {
    let delimited_format = matches!(format, Format::Csv | Format::Tsv);
    let available = outcomes
        .iter()
        .filter(|outcome| outcome.built.is_ok())
        .count();
    let mut parts = Vec::new();
    for outcome in outcomes {
        match &outcome.built {
            Ok(table) if delimited_format && available > 1 => parts.push(format!(
                "# {}\n{}",
                outcome.kind.name(),
                render(table, format)
            )),
            Ok(table) => parts.push(render(table, format)),
            Err(reason) if !delimited_format => {
                parts.push(render_unavailable(outcome.kind, reason, format))
            }
            Err(_) => {}
        }
    }
    parts.join("\n\n")
}

fn print_notes(outcomes: &[Outcome]) {
    for outcome in outcomes {
        match &outcome.built {
            Ok(table) => {
                for note in &table.notes {
                    eprintln!("note: {}: {note}", outcome.kind.name());
                }
            }
            Err(reason) => eprintln!("note: {}: unavailable: {reason}", outcome.kind.name()),
        }
    }
}

pub fn run(args: &[String]) -> Result<(), ReportError> {
    let request =
        parse_request(args).map_err(|error| ReportError::Usage(format!("{error}\n{USAGE}")))?;
    let source = std::fs::read_to_string(&request.path)
        .map_err(|error| ReportError::Failure(format!("cannot read {}: {error}", request.path)))?;
    let result: Value = serde_json::from_str(&source).map_err(|error| {
        ReportError::Failure(format!("{} is not valid JSON: {error}", request.path))
    })?;
    let outcomes = build_tables(&result, &request).map_err(ReportError::Failure)?;
    print_notes(&outcomes);
    if outcomes.iter().all(|outcome| outcome.built.is_err()) {
        return Err(ReportError::Failure(
            "none of the requested tables can be built from this result".into(),
        ));
    }
    match &request.out {
        Some(dir) => {
            std::fs::create_dir_all(dir)
                .map_err(|error| ReportError::Failure(format!("cannot create {dir}: {error}")))?;
            for outcome in &outcomes {
                let Ok(table) = &outcome.built else { continue };
                let path = std::path::Path::new(dir).join(format!(
                    "{}.{}",
                    outcome.kind.name(),
                    request.format.extension()
                ));
                std::fs::write(&path, format!("{}\n", render(table, request.format))).map_err(
                    |error| {
                        ReportError::Failure(format!("cannot write {}: {error}", path.display()))
                    },
                )?;
                eprintln!("wrote {}", path.display());
            }
        }
        None => println!("{}", render_stream(&outcomes, request.format)),
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicUsize, Ordering};

    struct Scratch(PathBuf);

    impl Scratch {
        fn new() -> Self {
            static NEXT: AtomicUsize = AtomicUsize::new(0);
            let path = std::env::temp_dir().join(format!(
                "actinv-report-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            std::fs::create_dir_all(&path).unwrap();
            Self(path)
        }

        fn file(&self, name: &str) -> String {
            self.0.join(name).to_str().unwrap().to_string()
        }
    }

    impl Drop for Scratch {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn dominant_list(total: f64, top: Vec<Value>, n90: u64, n99: u64) -> Value {
        json!({
            "total": total,
            "top": top,
            "n_for_90_percent": n90,
            "n_for_99_percent": n99,
        })
    }

    fn ranked(nuclide: &str, value: f64, percent: f64, cumulative: f64) -> Value {
        json!({
            "nuclide": nuclide,
            "value": value,
            "percent": percent,
            "cumulative_percent": cumulative,
        })
    }

    /// A summary block whose Co60 and Mn54 quantities scale by `k` (a power of two, so exact).
    fn summary_json(k: f64) -> Value {
        json!({
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
            "totals": {
                "activity_Bq": 1.0e5 * k,
                "activity_Ci": 2.5e-6 * k,
                "alpha_W": 0.0,
                "beta_W": 0.25 * k,
                "gamma_W": 0.5 * k,
                "heat_W": 0.75 * k,
                "activity_by_mode": {
                    "beta_minus_Bq": 1.0e5 * k,
                    "ec_beta_plus_Bq": 0.0,
                    "isomeric_transition_Bq": 0.0,
                    "alpha_Bq": 0.0,
                    "neutron_Bq": 0.0,
                    "spontaneous_fission_Bq": 0.0,
                    "proton_Bq": 0.0,
                    "other_Bq": 0.0,
                },
                "alpha_Bq": 0.0,
                "beta_Bq": 1.0e5 * k,
                "gamma_Bq": 8.0e4 * k,
                "activity_excl_H3_Bq": 1.0e5 * k,
                "heat_excl_H3_W": 0.75 * k,
                "mass_g": 1.0,
                "leakage_atoms": 0.0,
                "n_nuclides": 3,
                "absolute": {"multiplier": 2.0, "activity_Bq": 2.0e5 * k, "heat_W": 1.5 * k},
                "per_cm3": {"multiplier": 4.0, "activity_Bq": 4.0e5 * k, "heat_W": 3.0 * k},
            },
            "elements": [
                {"Z": 25, "symbol": "Mn", "atoms": 5.0e14 * k, "grams": 5.0e-8 * k,
                 "activity_Bq": 2.0e4 * k, "alpha_W": 0.0, "beta_W": 0.0, "gamma_W": 0.0},
                {"Z": 26, "symbol": "Fe", "atoms": 1.0e22, "grams": 0.5,
                 "activity_Bq": 0.0, "alpha_W": 0.0, "beta_W": 0.0, "gamma_W": 0.0},
                {"Z": 27, "symbol": "Co", "atoms": 2.0e15 * k, "grams": 2.5e-7 * k,
                 "activity_Bq": 8.0e4 * k, "alpha_W": 0.0, "beta_W": 0.25 * k,
                 "gamma_W": 0.5 * k},
            ],
            "dominant": {
                "activity": dominant_list(
                    1.0e5 * k,
                    vec![
                        ranked("Co60", 8.0e4 * k, 80.0, 80.0),
                        ranked("Mn54", 2.0e4 * k, 20.0, 100.0),
                    ],
                    2,
                    2,
                ),
                "heat": dominant_list(
                    0.75 * k,
                    vec![ranked("Co60", 0.75 * k, 100.0, 100.0)],
                    1,
                    1,
                ),
                "alpha_heat": dominant_list(0.0, vec![], 0, 0),
                "beta_heat": dominant_list(
                    0.25 * k,
                    vec![ranked("Co60", 0.25 * k, 100.0, 100.0)],
                    1,
                    1,
                ),
                "gamma_heat": dominant_list(
                    0.5 * k,
                    vec![ranked("Co60", 0.5 * k, 100.0, 100.0)],
                    1,
                    1,
                ),
            },
        })
    }

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
            "heat_W_per_g": {
                "total": 0.75 * k, "alpha": 0.0, "beta": 0.25 * k, "gamma": 0.5 * k
            },
        });
        if summary {
            value["summary"] = summary_json(k);
        }
        value
    }

    /// Two steps at 10 s and 20 s; the second holds half the Co60 and Mn54 of the first.
    fn result_json(summary: bool) -> Value {
        json!({
            "spec_title": "fixture",
            "steps": [step_json(1, 10.0, 1.0, summary), step_json(2, 20.0, 0.5, summary)],
        })
    }

    fn photon_json() -> Value {
        json!({
            "groups": [
                {"low_eV": 1.0e4, "high_eV": 1.0e5, "centroid_eV": 5.0e4,
                 "photons_s_g": 1.0e3, "photons_s": 2.0e3, "power_W_g": 2.5e-3,
                 "power_W": 5.0e-3},
                {"low_eV": 1.0e5, "high_eV": 1.0e6, "centroid_eV": 5.0e5,
                 "photons_s_g": 4.0e2, "photons_s": 8.0e2, "power_W_g": 1.0e-3,
                 "power_W": 2.0e-3},
            ],
            "contact_gamma_air_dose_proxy_Gy_h": 2.5e-6,
            "point_source_dose": {"gamma_air_dose_Sv_h": 4.0e-6},
        })
    }

    fn response_json(
        id: &str,
        value: f64,
        unit: &str,
        coverage: (f64, f64, f64),
        count: u64,
        names: &[&str],
    ) -> Value {
        json!({
            "id": id,
            "value": value,
            "unit": unit,
            "covered_activity_Bq_per_g": coverage.0,
            "missing_activity_Bq_per_g": coverage.1,
            "activity_coverage_fraction": coverage.2,
            "contributing_nuclide_count": count,
            "missing_active_nuclides": names,
        })
    }

    fn radiological_json() -> Value {
        let full = (1.0e5, 0.0, 1.0);
        json!({"responses": [
            response_json("ing", 2.5e-3, "Sv/g_material_intake", full, 2, &[]),
            response_json("clr", 4.0, "dimensionless", full, 2, &[]),
            response_json(
                "inh",
                0.125,
                "Sv/g_material_intake",
                (8.0e4, 2.0e4, 0.8),
                1,
                &["Mn54"],
            ),
        ]})
    }

    fn request(
        tables: &[TableKind],
        format: Format,
        step: StepSelect,
        nuclides: &[&str],
    ) -> Request {
        Request {
            path: String::new(),
            tables: tables.to_vec(),
            format,
            out: None,
            step,
            nuclides: nuclides.iter().map(|name| name.to_string()).collect(),
        }
    }

    fn table(result: &Value, kind: TableKind, step: StepSelect, nuclides: &[&str]) -> Built {
        let request = request(&[kind], Format::Csv, step, nuclides);
        build_tables(result, &request).unwrap().remove(0).built
    }

    const FIRST: StepSelect = StepSelect::One(1);

    fn csv(result: &Value, kind: TableKind) -> String {
        render(&table(result, kind, FIRST, &[]).unwrap(), Format::Csv)
    }

    fn args(items: &[&str]) -> Vec<String> {
        items.iter().map(|item| item.to_string()).collect()
    }

    #[test]
    fn parses_defaults_and_options() {
        let defaults = parse_request(&args(&["r.json"])).unwrap();
        assert_eq!(defaults.path, "r.json");
        assert_eq!(defaults.tables, ALL_TABLES.to_vec());
        assert_eq!(defaults.format, Format::Csv);
        assert_eq!(defaults.step, StepSelect::All);
        assert_eq!(defaults.out, None);
        assert!(defaults.nuclides.is_empty());

        let full = parse_request(&args(&[
            "--tables",
            "TAB4,totals",
            "r.json",
            "--format",
            "markdown",
            "--step",
            "3",
            "--out",
            "dir",
            "--nuclides",
            "Co60, Mn54",
        ]))
        .unwrap();
        assert_eq!(full.tables, vec![TableKind::Tab4, TableKind::Totals]);
        assert_eq!(full.format, Format::Markdown);
        assert_eq!(full.step, StepSelect::One(3));
        assert_eq!(full.out.as_deref(), Some("dir"));
        assert_eq!(full.nuclides, vec!["Co60".to_string(), "Mn54".to_string()]);
    }

    #[test]
    fn rejects_malformed_command_lines() {
        let rejected = |items: &[&str], needle: &str| {
            let error = parse_request(&args(items)).unwrap_err();
            assert!(error.contains(needle), "{error}");
        };
        rejected(&[], "exactly one RESULT.json");
        rejected(&["a.json", "b.json"], "exactly one RESULT.json");
        rejected(&["a.json", "--table"], "unknown report option --table");
        rejected(&["a.json", "--format"], "--format needs a value");
        rejected(&["a.json", "--format", "xml"], "--format must be");
        rejected(&["a.json", "--step", "0"], "--step must be");
        rejected(&["a.json", "--step", "x"], "--step must be");
        rejected(&["a.json", "--tables", "tab9"], "unknown table 'tab9'");
        rejected(&["a.json", "--tables", "tab1,tab1"], "listed twice");
        rejected(&["a.json", "--tables", "tab1,"], "empty entry");
        rejected(&["a.json", "--out", "a", "--out", "b"], "more than once");
    }

    #[test]
    fn base_fields_alone_give_atoms_and_activity_and_name_the_missing_columns() {
        let result = result_json(false);
        let tab1 = table(&result, TableKind::Tab1, FIRST, &[]).unwrap();
        assert_eq!(
            render(&tab1, Format::Csv),
            "step,time_s,nuclide,atoms_per_g\n\
             1,1e1,Fe56,1e22\n\
             1,1e1,Co60,2e15\n\
             1,1e1,Mn54,5e14"
        );
        assert_eq!(
            tab1.notes,
            vec![
                "columns omitted because the result does not carry them (add a `summary` \
                 section to the spec): mass_g_per_g, atoms, mass_g"
                    .to_string()
            ]
        );
        let tab2 = table(&result, TableKind::Tab2, FIRST, &[]).unwrap();
        assert_eq!(
            render(&tab2, Format::Csv),
            "step,time_s,nuclide,activity_Bq_per_g\n\
             1,1e1,Co60,8e4\n\
             1,1e1,Mn54,2e4"
        );
        assert_eq!(
            tab2.notes,
            vec![
                "columns omitted because the result does not carry them (add a `summary` \
                 section to the spec): heat_W_per_g, alpha_W_per_g, beta_W_per_g, \
                 gamma_W_per_g, half_life_s, activity_Bq, heat_W"
                    .to_string()
            ]
        );
    }

    #[test]
    fn summary_tab1_in_every_format() {
        let result = result_json(true);
        let tab1 = table(&result, TableKind::Tab1, FIRST, &[]).unwrap();
        assert!(tab1.notes.is_empty());
        assert_eq!(
            render(&tab1, Format::Csv),
            "step,time_s,nuclide,atoms_per_g,mass_g_per_g,atoms,mass_g\n\
             1,1e1,Fe56,1e22,5e-1,2e22,1e0\n\
             1,1e1,Co60,2e15,2.5e-7,4e15,5e-7\n\
             1,1e1,Mn54,5e14,5e-8,1e15,1e-7"
        );
        assert_eq!(
            render(&tab1, Format::Tsv),
            "step\ttime_s\tnuclide\tatoms_per_g\tmass_g_per_g\tatoms\tmass_g\n\
             1\t1e1\tFe56\t1e22\t5e-1\t2e22\t1e0\n\
             1\t1e1\tCo60\t2e15\t2.5e-7\t4e15\t5e-7\n\
             1\t1e1\tMn54\t5e14\t5e-8\t1e15\t1e-7"
        );
        assert_eq!(
            render(&tab1, Format::Markdown),
            "### TAB1 atoms and mass per nuclide\n\n\
             | step | time_s | nuclide | atoms_per_g | mass_g_per_g | atoms | mass_g |\n\
             | --- | --- | --- | --- | --- | --- | --- |\n\
             | 1 | 1.00000e1 | Fe56 | 1.00000e22 | 5.00000e-1 | 2.00000e22 | 1.00000e0 |\n\
             | 1 | 1.00000e1 | Co60 | 2.00000e15 | 2.50000e-7 | 4.00000e15 | 5.00000e-7 |\n\
             | 1 | 1.00000e1 | Mn54 | 5.00000e14 | 5.00000e-8 | 1.00000e15 | 1.00000e-7 |"
        );
        assert_eq!(
            render(&tab1, Format::Text),
            "TAB1 atoms and mass per nuclide\n\
             step  time_s     nuclide  atoms_per_g  mass_g_per_g  atoms       mass_g\n\
             ----  ---------  -------  -----------  ------------  ----------  ----------\n\
             1     1.00000e1  Fe56     1.00000e22   5.00000e-1    2.00000e22  1.00000e0\n\
             1     1.00000e1  Co60     2.00000e15   2.50000e-7    4.00000e15  5.00000e-7\n\
             1     1.00000e1  Mn54     5.00000e14   5.00000e-8    1.00000e15  1.00000e-7"
        );
    }

    #[test]
    fn csv_values_round_trip_exactly() {
        let mut result = result_json(false);
        let value = 0.1 + 0.2;
        result["steps"][0]["activity_Bq_per_g"]["Co60"] = json!(value);
        let text = csv(&result, TableKind::Tab2);
        let cell = text.lines().nth(2).unwrap().rsplit(',').next().unwrap();
        assert_eq!(cell.parse::<f64>().unwrap(), value);
        assert_eq!(cell, "3.0000000000000004e-1");
    }

    #[test]
    fn summary_tab2_has_heat_split_half_life_and_absolute_columns() {
        let result = result_json(true);
        assert_eq!(
            csv(&result, TableKind::Tab2),
            "step,time_s,nuclide,activity_Bq_per_g,heat_W_per_g,alpha_W_per_g,beta_W_per_g,\
             gamma_W_per_g,half_life_s,activity_Bq,heat_W\n\
             1,1e1,Co60,8e4,7.5e-1,0e0,2.5e-1,5e-1,1.66e8,1.6e5,1.5e0\n\
             1,1e1,Mn54,2e4,0e0,0e0,0e0,0e0,2.7e7,4e4,0e0"
        );
    }

    #[test]
    fn totals_report_base_and_summary_columns() {
        let header = [
            "step",
            "time_s",
            "activity_Bq_per_g",
            "heat_total_W_per_g",
            "heat_alpha_W_per_g",
            "heat_beta_W_per_g",
            "heat_gamma_W_per_g",
            "activity_Ci_per_g",
            "alpha_activity_Bq_per_g",
            "beta_activity_Bq_per_g",
            "gamma_activity_Bq_per_g",
            "activity_excl_H3_Bq_per_g",
            "heat_excl_H3_W_per_g",
            "mass_g_per_g",
            "n_nuclides",
            "mode_beta_minus_Bq_per_g",
            "mode_ec_beta_plus_Bq_per_g",
            "mode_isomeric_transition_Bq_per_g",
            "mode_alpha_Bq_per_g",
            "mode_neutron_Bq_per_g",
            "mode_spontaneous_fission_Bq_per_g",
            "mode_proton_Bq_per_g",
            "mode_other_Bq_per_g",
            "material_mass_g",
            "activity_Bq",
            "heat_W",
            "density_g_cm3",
            "activity_Bq_per_cm3",
            "heat_W_per_cm3",
        ]
        .join(",");
        let row = [
            "1", "1e1", "1e5", "7.5e-1", "0e0", "2.5e-1", "5e-1", "2.5e-6", "0e0", "1e5", "8e4",
            "1e5", "7.5e-1", "1e0", "3", "1e5", "0e0", "0e0", "0e0", "0e0", "0e0", "0e0", "0e0",
            "2e0", "2e5", "1.5e0", "4e0", "4e5", "3e0",
        ]
        .join(",");
        assert_eq!(
            csv(&result_json(true), TableKind::Totals),
            format!("{header}\n{row}")
        );

        let base = table(&result_json(false), TableKind::Totals, FIRST, &[]).unwrap();
        assert_eq!(
            render(&base, Format::Csv),
            "step,time_s,activity_Bq_per_g,heat_total_W_per_g,heat_alpha_W_per_g,\
             heat_beta_W_per_g,heat_gamma_W_per_g\n\
             1,1e1,1e5,7.5e-1,0e0,2.5e-1,5e-1"
        );
        assert_eq!(base.notes.len(), 1);
        assert!(base.notes[0].starts_with("columns omitted"));
        assert!(base.notes[0].contains("activity_Ci_per_g"));
        assert!(base.notes[0].contains("heat_W_per_cm3"));
    }

    #[test]
    fn missing_density_is_not_reported_as_a_missing_summary() {
        let mut result = result_json(true);
        result["steps"][0]["summary"]["totals"]
            .as_object_mut()
            .unwrap()
            .remove("per_cm3");
        let totals = table(&result, TableKind::Totals, FIRST, &[]).unwrap();
        assert!(totals.notes.is_empty());
        assert!(!totals.headers.iter().any(|name| name.contains("cm3")));
    }

    #[test]
    fn elements_and_dominant_come_from_the_summary() {
        let result = result_json(true);
        assert_eq!(
            csv(&result, TableKind::Elements),
            "step,time_s,Z,element,atoms_per_g,mass_g_per_g,activity_Bq_per_g,alpha_W_per_g,\
             beta_W_per_g,gamma_W_per_g\n\
             1,1e1,25,Mn,5e14,5e-8,2e4,0e0,0e0,0e0\n\
             1,1e1,26,Fe,1e22,5e-1,0e0,0e0,0e0,0e0\n\
             1,1e1,27,Co,2e15,2.5e-7,8e4,0e0,2.5e-1,5e-1"
        );
        assert_eq!(
            csv(&result, TableKind::Dominant),
            "step,time_s,quantity,rank,nuclide,value,unit,percent,cumulative_percent,\
             list_total,n_for_90_percent,n_for_99_percent\n\
             1,1e1,activity,1,Co60,8e4,Bq_per_g,8e1,8e1,1e5,2,2\n\
             1,1e1,activity,2,Mn54,2e4,Bq_per_g,2e1,1e2,1e5,2,2\n\
             1,1e1,heat,1,Co60,7.5e-1,W_per_g,1e2,1e2,7.5e-1,1,1\n\
             1,1e1,beta_heat,1,Co60,2.5e-1,W_per_g,1e2,1e2,2.5e-1,1,1\n\
             1,1e1,gamma_heat,1,Co60,5e-1,W_per_g,1e2,1e2,5e-1,1,1"
        );
    }

    #[test]
    fn summary_only_tables_are_unavailable_without_a_summary() {
        let result = result_json(false);
        for (kind, needle) in [
            (TableKind::Dominant, "summary.dominant"),
            (TableKind::Elements, "summary.elements"),
        ] {
            let reason = table(&result, kind, StepSelect::All, &[]).unwrap_err();
            assert!(reason.contains(needle), "{reason}");
        }
    }

    #[test]
    fn partial_summaries_degrade_to_the_base_columns() {
        let mut result = result_json(true);
        result["steps"][1]
            .as_object_mut()
            .unwrap()
            .remove("summary");
        let tab1 = table(&result, TableKind::Tab1, StepSelect::All, &[]).unwrap();
        assert_eq!(
            tab1.headers,
            headers(&["step", "time_s", "nuclide", "atoms_per_g"])
        );
        assert_eq!(tab1.rows.len(), 6);
        let elements = table(&result, TableKind::Elements, StepSelect::All, &[]).unwrap();
        assert_eq!(elements.rows.len(), 3);
        assert_eq!(
            elements.notes,
            vec!["steps without an element table: 2".to_string()]
        );
    }

    #[test]
    fn summary_trimming_is_reported() {
        let mut result = result_json(true);
        result["steps"][0]["summary"]["nuclides"]
            .as_array_mut()
            .unwrap()
            .pop();
        let tab1 = table(&result, TableKind::Tab1, FIRST, &[]).unwrap();
        assert_eq!(tab1.rows.len(), 2);
        assert_eq!(
            tab1.notes,
            vec![
                "1 inventory entries are not in summary.nuclides (min_atoms or include_stable); \
                 the table lists the summary rows"
                    .to_string()
            ]
        );
    }

    #[test]
    fn tab3_lists_dose_responses_only() {
        let mut result = result_json(true);
        result["steps"][0]["radiological"] = radiological_json();
        let tab3 = table(&result, TableKind::Tab3, StepSelect::All, &[]).unwrap();
        assert_eq!(
            render(&tab3, Format::Csv),
            "step,time_s,response,value,unit,covered_activity_Bq_per_g,\
             missing_activity_Bq_per_g,activity_coverage_fraction,contributing_nuclides,\
             missing_active_nuclides\n\
             1,1e1,ing,2.5e-3,Sv/g_material_intake,1e5,0e0,1e0,2,\n\
             1,1e1,inh,1.25e-1,Sv/g_material_intake,8e4,2e4,8e-1,1,Mn54"
        );
        assert_eq!(tab3.notes.len(), 1);
        assert!(tab3.notes[0].contains("per-nuclide dose"));
    }

    #[test]
    fn tab3_says_why_it_is_unavailable() {
        let result = result_json(true);
        let reason = table(&result, TableKind::Tab3, StepSelect::All, &[]).unwrap_err();
        assert_eq!(
            reason,
            "the result has no `radiological` responses (declare `radiological` in the spec)"
        );
        let full = (1.0e5, 0.0, 1.0);
        let mut index_only = result_json(true);
        index_only["steps"][0]["radiological"] = json!({"responses": [
            response_json("clr", 4.0, "dimensionless", full, 2, &[]),
            response_json("a2", 0.5, "A2_per_g_material", full, 2, &[]),
        ]});
        let reason = table(&index_only, TableKind::Tab3, StepSelect::All, &[]).unwrap_err();
        assert_eq!(
            reason,
            "the result has radiological responses but none is an ingestion or inhalation dose \
             (units: A2_per_g_material, dimensionless)"
        );
    }

    #[test]
    fn tab4_lists_photon_groups_and_names_steps_without_a_source() {
        let mut result = result_json(false);
        result["steps"][0]["photon_source"] = photon_json();
        let tab4 = table(&result, TableKind::Tab4, StepSelect::All, &[]).unwrap();
        assert_eq!(
            render(&tab4, Format::Csv),
            "step,time_s,group,low_eV,high_eV,centroid_eV,photons_s_g,power_W_g,photons_s,\
             power_W\n\
             1,1e1,1,1e4,1e5,5e4,1e3,2.5e-3,2e3,5e-3\n\
             1,1e1,2,1e5,1e6,5e5,4e2,1e-3,8e2,2e-3"
        );
        assert_eq!(
            tab4.notes,
            vec!["steps without a photon source: 2".to_string()]
        );
        let reason = table(&result_json(false), TableKind::Tab4, StepSelect::All, &[]);
        assert!(reason.unwrap_err().contains("no `photon_source` groups"));
    }

    #[test]
    fn timeseries_has_totals_dose_rates_responses_and_chosen_nuclides() {
        let mut result = result_json(true);
        result["steps"][0]["photon_source"] = photon_json();
        result["steps"][0]["radiological"] = radiological_json();
        let series = table(
            &result,
            TableKind::Timeseries,
            StepSelect::All,
            &["Co60", "Mn54"],
        )
        .unwrap();
        assert!(series.notes.is_empty());
        let header = [
            "step",
            "time_s",
            "activity_Bq_per_g",
            "heat_total_W_per_g",
            "heat_alpha_W_per_g",
            "heat_beta_W_per_g",
            "heat_gamma_W_per_g",
            "contact_gamma_air_dose_proxy_Gy_h",
            "point_source_gamma_air_dose_Sv_h",
            "radiological.ing[Sv/g_material_intake]",
            "radiological.clr[dimensionless]",
            "radiological.inh[Sv/g_material_intake]",
            "Co60_atoms_per_g",
            "Co60_activity_Bq_per_g",
            "Mn54_atoms_per_g",
            "Mn54_activity_Bq_per_g",
        ]
        .join(",");
        let first = [
            "1", "1e1", "1e5", "7.5e-1", "0e0", "2.5e-1", "5e-1", "2.5e-6", "4e-6", "2.5e-3",
            "4e0", "1.25e-1", "2e15", "8e4", "5e14", "2e4",
        ]
        .join(",");
        let second = [
            "2", "2e1", "5e4", "3.75e-1", "0e0", "1.25e-1", "2.5e-1", "", "", "", "", "", "1e15",
            "4e4", "2.5e14", "1e4",
        ]
        .join(",");
        assert_eq!(
            render(&series, Format::Csv),
            format!("{header}\n{first}\n{second}")
        );
    }

    #[test]
    fn nuclide_filter_restricts_tab1_and_tab2() {
        let result = result_json(true);
        let tab1 = table(&result, TableKind::Tab1, FIRST, &["Mn54"]).unwrap();
        assert_eq!(
            render(&tab1, Format::Csv),
            "step,time_s,nuclide,atoms_per_g,mass_g_per_g,atoms,mass_g\n\
             1,1e1,Mn54,5e14,5e-8,1e15,1e-7"
        );
        let tab2 = table(&result_json(false), TableKind::Tab2, FIRST, &["Co60"]).unwrap();
        assert_eq!(
            render(&tab2, Format::Csv),
            "step,time_s,nuclide,activity_Bq_per_g\n1,1e1,Co60,8e4"
        );
    }

    #[test]
    fn unknown_nuclide_and_step_are_refused() {
        let result = result_json(false);
        let unknown = request(&[TableKind::Tab1], Format::Csv, FIRST, &["U238"]);
        let error = build_tables(&result, &unknown).err().unwrap();
        assert!(error.contains("nuclide 'U238'"), "{error}");
        let missing = request(&[TableKind::Tab1], Format::Csv, StepSelect::One(3), &[]);
        let error = build_tables(&result, &missing).err().unwrap();
        assert_eq!(error, "step 3 is not in the result (steps 1 to 2)");
        let error = build_tables(&json!({"title": "x"}), &missing)
            .err()
            .unwrap();
        assert!(error.contains("no `steps` array"), "{error}");
        let error = build_tables(&json!({"steps": []}), &missing).err().unwrap();
        assert_eq!(error, "the result has no steps");
    }

    #[test]
    fn a_single_step_is_selected_by_number() {
        let result = result_json(false);
        let second = table(&result, TableKind::Tab2, StepSelect::One(2), &[]).unwrap();
        assert_eq!(
            render(&second, Format::Csv),
            "step,time_s,nuclide,activity_Bq_per_g\n\
             2,2e1,Co60,4e4\n\
             2,2e1,Mn54,1e4"
        );
        let all = table(&result, TableKind::Tab2, StepSelect::All, &[]).unwrap();
        assert_eq!(all.rows.len(), 4);
    }

    #[test]
    fn stream_names_tables_when_there_are_several() {
        let result = result_json(false);
        let kinds = [TableKind::Tab1, TableKind::Tab2, TableKind::Tab3];
        let outcomes = build_tables(&result, &request(&kinds, Format::Csv, FIRST, &[])).unwrap();
        assert_eq!(
            render_stream(&outcomes, Format::Csv),
            "# tab1\nstep,time_s,nuclide,atoms_per_g\n\
             1,1e1,Fe56,1e22\n1,1e1,Co60,2e15\n1,1e1,Mn54,5e14\n\n\
             # tab2\nstep,time_s,nuclide,activity_Bq_per_g\n\
             1,1e1,Co60,8e4\n1,1e1,Mn54,2e4"
        );
        let only = build_tables(&result, &request(&kinds[..1], Format::Csv, FIRST, &[])).unwrap();
        assert!(render_stream(&only, Format::Csv).starts_with("step,time_s"));
        let unavailable = request(&kinds[2..], Format::Markdown, FIRST, &[]);
        let outcomes = build_tables(&result, &unavailable).unwrap();
        assert_eq!(
            render_stream(&outcomes, Format::Markdown),
            "### TAB3 ingestion and inhalation dose\n\n\
             Unavailable: the result has no `radiological` responses \
             (declare `radiological` in the spec)"
        );
    }

    #[test]
    fn text_and_markdown_carry_notes() {
        let result = result_json(false);
        let tab2 = table(&result, TableKind::Tab2, FIRST, &[]).unwrap();
        let note = format!("Note: {}", tab2.notes[0]);
        assert!(render(&tab2, Format::Text).ends_with(&format!("\n{note}")));
        assert!(render(&tab2, Format::Markdown).ends_with(&format!("\n\n{note}")));
        assert!(!render(&tab2, Format::Csv).contains("Note"));
    }

    #[test]
    fn delimited_cells_are_escaped() {
        assert_eq!(delimited_cell("a,b\"c", Format::Csv), "\"a,b\"\"c\"");
        assert_eq!(delimited_cell("plain", Format::Csv), "plain");
        assert_eq!(delimited_cell("a\tb\nc", Format::Tsv), "a b c");
    }

    #[test]
    fn run_writes_one_file_per_table() {
        let scratch = Scratch::new();
        let mut result = result_json(true);
        result["steps"][0]["photon_source"] = photon_json();
        let input = scratch.file("result.json");
        std::fs::write(&input, serde_json::to_string(&result).unwrap()).unwrap();
        let out = scratch.file("tables");
        run(&args(&[
            &input,
            "--tables",
            "tab1,tab3,tab4",
            "--format",
            "tsv",
            "--step",
            "1",
            "--out",
            &out,
        ]))
        .unwrap();
        let tab1 = std::fs::read_to_string(scratch.file("tables/tab1.tsv")).unwrap();
        assert!(tab1.starts_with("step\ttime_s\tnuclide\tatoms_per_g\tmass_g_per_g\tatoms"));
        assert!(tab1.ends_with('\n'));
        let tab4 = std::fs::read_to_string(scratch.file("tables/tab4.tsv")).unwrap();
        assert!(tab4.starts_with("step\ttime_s\tgroup\tlow_eV\thigh_eV"));
        // The unavailable table is skipped, not written as an empty file.
        assert!(!std::path::Path::new(&scratch.file("tables/tab3.tsv")).exists());
    }

    #[test]
    fn run_reports_unusable_requests() {
        let scratch = Scratch::new();
        let input = scratch.file("result.json");
        std::fs::write(&input, serde_json::to_string(&result_json(false)).unwrap()).unwrap();
        match run(&args(&[&input, "--tables", "tab3"])) {
            Err(ReportError::Failure(message)) => assert!(message.contains("none of"), "{message}"),
            other => panic!("expected a failure, got {other:?}"),
        }
        match run(&args(&[&input, "--format", "xml"])) {
            Err(ReportError::Usage(message)) => assert!(message.contains("usage: actinv report")),
            other => panic!("expected a usage error, got {other:?}"),
        }
        match run(&args(&[&scratch.file("absent.json")])) {
            Err(ReportError::Failure(message)) => assert!(message.contains("cannot read")),
            other => panic!("expected a failure, got {other:?}"),
        }
        let broken = scratch.file("broken.json");
        std::fs::write(&broken, "{").unwrap();
        match run(&args(&[&broken])) {
            Err(ReportError::Failure(message)) => assert!(message.contains("not valid JSON")),
            other => panic!("expected a failure, got {other:?}"),
        }
    }

    // ---- P140 H1 regression tests (hardening pass) ----

    /// `summary.depletion` of one step: Fe56 loses 10 % through (n,g) and (n,2n).
    fn depletion_json() -> Value {
        json!({
            "model": "coupled",
            "nuclides": [{
                "nuclide": "Fe56", "Z": 26, "A": 56, "LISO": 0,
                "initial_atoms_per_g": 1.0e22,
                "atoms_start_per_g": 1.0e22,
                "atoms_end_per_g": 9.0e21,
                "depleted_atoms_per_g": 1.0e21,
                "depleted_fraction": 0.1,
                "cumulative_depleted_atoms_per_g": 1.0e21,
                "cumulative_depleted_fraction": 0.1,
                "removal_rate_per_s": 0.01,
                "channels": [
                    {"channel": "MT102", "kind": "reaction", "mt": 102, "reaction": "(n,g)",
                     "rate_per_s": 0.006, "share": 0.6},
                    {"channel": "MT16", "kind": "reaction", "mt": 16, "reaction": "(n,2n)",
                     "rate_per_s": 0.004, "share": 0.4},
                ],
            }],
        })
    }

    #[test]
    fn p140_report_f10_depletion_table_is_rendered() {
        let scratch = Scratch::new();
        let mut result = result_json(true);
        for step in result["steps"].as_array_mut().unwrap() {
            step["summary"]["depletion"] = depletion_json();
        }
        let input = scratch.file("result.json");
        std::fs::write(&input, serde_json::to_string(&result).unwrap()).unwrap();
        let out = scratch.file("tables");
        run(&args(&[
            &input,
            "--tables",
            "depletion",
            "--format",
            "csv",
            "--step",
            "1",
            "--out",
            &out,
        ]))
        .expect("`depletion` is a report table");
        let written = std::fs::read_to_string(scratch.file("tables/depletion.csv")).unwrap();
        let header = written.lines().next().unwrap();
        assert!(header.contains("nuclide"), "{header}");
        assert!(header.contains("depleted_fraction"), "{header}");
        assert!(written.contains("Fe56"), "{written}");
        // the fraction (exact {:e} text) and both channels appear
        assert!(written.contains("1e-1"), "{written}");
        assert!(written.contains("MT102"), "{written}");
        assert!(written.contains("MT16"), "{written}");
    }

    #[test]
    fn p140_report_f10_tab2_says_when_summary_rows_are_trimmed() {
        let mut result = result_json(true);
        // Mn54 is in the inventory and the activity map but not in summary.nuclides
        result["steps"][0]["summary"]["nuclides"]
            .as_array_mut()
            .unwrap()
            .pop();
        let tab2 = table(&result, TableKind::Tab2, FIRST, &[]).unwrap();
        assert_eq!(tab2.rows.len(), 1);
        assert!(
            tab2.notes
                .iter()
                .any(|note| note.contains("not in summary.nuclides")),
            "TAB2 trims silently: {:?}",
            tab2.notes
        );
        // nothing trimmed, nothing to say
        let untrimmed = table(&result_json(true), TableKind::Tab2, FIRST, &[]).unwrap();
        assert!(untrimmed.notes.is_empty(), "{:?}", untrimmed.notes);
    }

    #[test]
    fn p140_report_f10_nuclide_trimmed_from_the_summary_is_named() {
        let mut result = result_json(true);
        result["steps"][0]["summary"]["nuclides"]
            .as_array_mut()
            .unwrap()
            .pop();
        for kind in [TableKind::Tab1, TableKind::Tab2] {
            let asked = request(&[kind], Format::Csv, FIRST, &["Mn54"]);
            // Either a refusal or a note, but it must name the nuclide and summary.nuclides.
            let message = match build_tables(&result, &asked) {
                Err(error) => error,
                Ok(mut outcomes) => match outcomes.remove(0).built {
                    Ok(built) => built.notes.join("\n"),
                    Err(reason) => reason,
                },
            };
            assert!(
                message.contains("Mn54") && message.contains("summary.nuclides"),
                "{}: an empty table for a trimmed nuclide without explanation: '{message}'",
                kind.name()
            );
        }
    }
}
