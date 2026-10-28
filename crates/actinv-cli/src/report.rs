//! `actinv report` — render an existing result JSON as FISPACT-style tables (P132b).
//!
//! The command-line half: option parsing, notes on standard error and file writing. The tables
//! themselves are `actinv_core::report`, a pure function of the result file (no solver, no
//! data files) that the GUI shares, so the screen, the exports and this command agree.

use actinv_core::report::{
    build, render, render_stream, Format, Outcome, Request as TableRequest, StepSelect, TableKind,
    ALL_TABLES, NAMED_TABLES,
};
use actinv_core::report_page;
use serde_json::Value;
use std::collections::BTreeMap;

pub const USAGE: &str = "usage: actinv report RESULT.json [--tables LIST] [--format csv|tsv|markdown|text|html] [--out DIR] [--step N|all] [--nuclides LIST] [--problem PROBLEM.json]";

/// Detail printed by `actinv report --help`.
pub const HELP: &str = "Render a result JSON from `actinv run` as FISPACT-style tables, or as a\n\
report page to read, share or print (--format html). Nothing is recomputed.\n\
--tables LIST    comma-separated: tab1 (atoms, grams), tab2 (activity, heat),\n\
                 tab3 (ingestion/inhalation dose), tab4 (photon groups), totals,\n\
                 dominant, elements, timeseries (default: all of these),\n\
                 depletion (per-nuclide depletion table) and regulatory (clearance,\n\
                 waste, A2 and activity-limit indices); the last two only when asked for\n\
--format F       csv (default), tsv, markdown or text; csv and tsv carry exact {:e} values;\n\
                 html writes one report page: the problem, headline numbers at round\n\
                 cooling times, charts, what dominates and the caveats\n\
--out DIR        write one file per table (tab1.csv, ...) instead of standard output;\n\
                 with html, report.html and one SVG file per chart\n\
--problem FILE   with html, the problem that produced the result, read into sentences\n\
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

#[derive(Debug, PartialEq)]
struct Request {
    path: String,
    format: Format,
    /// `--format html`: the report page instead of tables.
    page: bool,
    problem: Option<String>,
    out: Option<String>,
    select: TableRequest,
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
    const VALUED: [&str; 6] = [
        "--tables",
        "--format",
        "--out",
        "--step",
        "--nuclides",
        "--problem",
    ];
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
    let page = values.get("--format") == Some(&"html");
    if page {
        if let Some(option) = ["--tables", "--step", "--nuclides"]
            .into_iter()
            .find(|option| values.contains_key(option))
        {
            return Err(format!(
                "{option} selects tables; the html report page takes none"
            ));
        }
    } else if values.contains_key("--problem") {
        return Err("--problem is read only by --format html".into());
    }
    let format = match values.get("--format").copied() {
        None | Some("csv") | Some("html") => Format::Csv,
        Some("tsv") => Format::Tsv,
        Some("markdown") => Format::Markdown,
        Some("text") => Format::Text,
        Some(other) => {
            return Err(format!(
                "--format must be csv, tsv, markdown, text or html, got '{other}'"
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
        format,
        page,
        problem: values.get("--problem").map(|value| value.to_string()),
        out: values.get("--out").map(|value| value.to_string()),
        select: TableRequest {
            tables,
            step,
            nuclides,
        },
    })
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
    if request.page {
        return write_page(&request, &result);
    }
    let outcomes = build(&result, &request.select).map_err(ReportError::Failure)?;
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

/// `--format html`: the report page on standard output, or `report.html` and the chart SVGs in
/// `--out DIR`.
fn write_page(request: &Request, result: &Value) -> Result<(), ReportError> {
    let problem = match &request.problem {
        Some(path) => {
            let text = std::fs::read_to_string(path)
                .map_err(|error| ReportError::Failure(format!("cannot read {path}: {error}")))?;
            Some(serde_json::from_str::<Value>(&text).map_err(|error| {
                ReportError::Failure(format!("{path} is not valid JSON: {error}"))
            })?)
        }
        None => None,
    };
    let label = std::path::Path::new(&request.path)
        .file_name()
        .map(|name| name.to_string_lossy().into_owned());
    let page = report_page::html(
        result,
        &report_page::PageOptions {
            problem: problem.as_ref(),
            label,
            ..Default::default()
        },
    )
    .map_err(ReportError::Failure)?;
    let Some(dir) = &request.out else {
        print!("{page}");
        return Ok(());
    };
    std::fs::create_dir_all(dir)
        .map_err(|error| ReportError::Failure(format!("cannot create {dir}: {error}")))?;
    let mut files = vec![("report.html".to_owned(), page)];
    files.extend(
        report_page::charts(result)
            .into_iter()
            .map(|(stem, _, svg)| (format!("{stem}.svg"), svg)),
    );
    for (name, contents) in files {
        let path = std::path::Path::new(dir).join(name);
        std::fs::write(&path, contents).map_err(|error| {
            ReportError::Failure(format!("cannot write {}: {error}", path.display()))
        })?;
        eprintln!("wrote {}", path.display());
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

    /// One step of 10 s; with `summary`, the per-nuclide table and the absolute scale.
    /// The table builders have their own fixtures in `actinv_core::report`.
    fn result_json(summary: bool) -> Value {
        let mut step = json!({
            "step": 1,
            "t_s": 10.0,
            "inventory": [
                {"nuclide": "Fe56", "Z": 26, "A": 56, "LISO": 0, "atoms_per_g": 1.0e22},
                {"nuclide": "Co60", "Z": 27, "A": 60, "LISO": 0, "atoms_per_g": 2.0e15},
            ],
            "activity_Bq_per_g": {"Co60": 8.0e4},
            "heat_W_per_g": {"total": 0.75, "alpha": 0.0, "beta": 0.25, "gamma": 0.5},
        });
        if summary {
            step["summary"] = json!({
                "nuclides": [
                    {"nuclide": "Fe56", "atoms": 1.0e22, "grams": 0.5, "activity_Bq": 0.0,
                     "alpha_W": 0.0, "beta_W": 0.0, "gamma_W": 0.0, "half_life_s": null},
                    {"nuclide": "Co60", "atoms": 2.0e15, "grams": 2.5e-7,
                     "activity_Bq": 8.0e4, "alpha_W": 0.0, "beta_W": 0.25,
                     "gamma_W": 0.5, "half_life_s": 1.66e8},
                ],
                "totals": {"absolute": {"multiplier": 2.0}},
            });
        }
        json!({"spec_title": "fixture", "steps": [step]})
    }

    fn photon_json() -> Value {
        json!({
            "groups": [
                {"low_eV": 1.0e4, "high_eV": 1.0e5, "centroid_eV": 5.0e4,
                 "photons_s_g": 1.0e3, "photons_s": 2.0e3, "power_W_g": 2.5e-3,
                 "power_W": 5.0e-3},
            ],
        })
    }

    fn args(items: &[&str]) -> Vec<String> {
        items.iter().map(|item| item.to_string()).collect()
    }

    #[test]
    fn parses_defaults_and_options() {
        let defaults = parse_request(&args(&["r.json"])).unwrap();
        assert_eq!(defaults.path, "r.json");
        assert_eq!(defaults.select.tables, ALL_TABLES.to_vec());
        assert_eq!(defaults.format, Format::Csv);
        assert_eq!(defaults.select.step, StepSelect::All);
        assert_eq!(defaults.out, None);
        assert!(defaults.select.nuclides.is_empty());

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
        assert_eq!(full.select.tables, vec![TableKind::Tab4, TableKind::Totals]);
        assert_eq!(full.format, Format::Markdown);
        assert_eq!(full.select.step, StepSelect::One(3));
        assert_eq!(full.out.as_deref(), Some("dir"));
        assert_eq!(
            full.select.nuclides,
            vec!["Co60".to_string(), "Mn54".to_string()]
        );

        // the opt-in tables are accepted by name, in any case, and only then
        let names = args(&["--tables", "Depletion,regulatory", "r.json"]);
        let opt_in = parse_request(&names).unwrap();
        assert_eq!(
            opt_in.select.tables,
            vec![TableKind::Depletion, TableKind::Regulatory]
        );
    }

    #[test]
    fn html_asks_for_the_report_page() {
        let page = parse_request(&args(&[
            "r.json",
            "--format",
            "html",
            "--problem",
            "p.json",
        ]))
        .unwrap();
        assert!(page.page);
        assert_eq!(page.problem.as_deref(), Some("p.json"));
        assert!(!parse_request(&args(&["r.json"])).unwrap().page);
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
        rejected(
            &["a.json", "--format", "html", "--tables", "tab1"],
            "the html report page takes none",
        );
        rejected(&["a.json", "--problem", "p.json"], "only by --format html");
        rejected(&["a.json", "--step", "0"], "--step must be");
        rejected(&["a.json", "--step", "x"], "--step must be");
        rejected(&["a.json", "--tables", "tab9"], "unknown table 'tab9'");
        rejected(&["a.json", "--tables", "tab1,tab1"], "listed twice");
        rejected(&["a.json", "--tables", "tab1,"], "empty entry");
        rejected(&["a.json", "--out", "a", "--out", "b"], "more than once");
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
    fn regulatory_table_is_written_when_named() {
        let scratch = Scratch::new();
        let mut result = result_json(false);
        result["steps"][0]["radiological"] = json!({"responses": [{
            "id": "clr", "value": 4.0, "unit": "dimensionless",
            "covered_activity_Bq_per_g": 8.0e4, "missing_activity_Bq_per_g": 0.0,
            "activity_coverage_fraction": 1.0, "contributing_nuclide_count": 1,
            "missing_active_nuclides": [],
        }]});
        let input = scratch.file("result.json");
        std::fs::write(&input, serde_json::to_string(&result).unwrap()).unwrap();
        let out = scratch.file("tables");
        run(&args(&[&input, "--tables", "regulatory", "--out", &out])).unwrap();
        let written = std::fs::read_to_string(scratch.file("tables/regulatory.csv")).unwrap();
        assert!(
            written.starts_with("step,time_s,response,value,unit,"),
            "{written}"
        );
        assert!(
            written.contains("1,1e1,clr,4e0,dimensionless,8e4,0e0,1e0,1,,,"),
            "{written}"
        );
    }
}
