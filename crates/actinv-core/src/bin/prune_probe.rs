//! P148 probe: run one spec through the ordinary path and write the pruning inputs, the bound graph, every
//! state's bounds and the outcome next to the result, for the independent checker.

use actinv_core::{prune, run::PreparedRun, spec::Spec};
use serde_json::{json, Value};

fn triplets(values: &[(usize, usize, f64)]) -> Value {
    Value::Array(values.iter().map(|(i, j, v)| json!([i, j, v])).collect())
}

/// Infinite bounds become the string "inf"; JSON has no infinity.
fn number(value: f64) -> Value {
    if value.is_finite() {
        json!(value)
    } else {
        json!("inf")
    }
}

fn numbers(values: &[f64]) -> Value {
    Value::Array(values.iter().map(|v| number(*v)).collect())
}

fn capture_json(capture: &prune::Capture) -> Value {
    json!({
        "n": capture.n,
        "decay": triplets(&capture.decay),
        "feed": capture.feed.iter().map(|t| triplets(t)).collect::<Vec<_>>(),
        "removal": capture.removal.iter().map(|t| triplets(t)).collect::<Vec<_>>(),
        "spectra": capture.spectra.iter().map(|t| triplets(t)).collect::<Vec<_>>(),
        "n0": capture.n0,
        "durations_s": capture.durations_s,
        "multipliers": capture.multipliers,
        "rate_mode": capture.rate_mode,
        "bmin": capture.bmin,
        "edges": capture.edges.iter().map(|e| json!([e.from, e.to, e.rate, number(e.share)])).collect::<Vec<_>>(),
        "throughput": numbers(&capture.bounds.throughput),
        "population": numbers(&capture.bounds.population),
        "inflow_rate": numbers(&capture.bounds.inflow_rate),
        "unbounded": capture.bounds.unbounded,
        "keep": capture.outcome.keep,
        "dropped": capture.outcome.dropped.iter()
            .map(|d| json!([d.index, number(d.atoms_bound), number(d.feed_bound)]))
            .collect::<Vec<_>>(),
        "kept_shortfall": capture.outcome.kept_shortfall.iter()
            .map(|(i, e)| json!([i, number(*e)]))
            .collect::<Vec<_>>(),
        "kept_unbounded": capture.outcome.unbounded,
        "energy_ev": capture.energy_ev,
        "names": capture.names,
    })
}

type Triplets = Vec<(usize, usize, f64)>;

fn triplet_list(value: &Value) -> Result<Triplets, String> {
    value
        .as_array()
        .ok_or("expected a triplet list")?
        .iter()
        .map(|t| {
            let i = t[0].as_u64().ok_or("bad row")? as usize;
            let j = t[1].as_u64().ok_or("bad column")? as usize;
            let v = t[2].as_f64().ok_or("bad value")?;
            Ok((i, j, v))
        })
        .collect()
}

fn lists(value: &Value) -> Result<Vec<Triplets>, String> {
    value
        .as_array()
        .ok_or("expected a list of triplet lists")?
        .iter()
        .map(triplet_list)
        .collect()
}

/// Synthetic battery: each case gives the raw problem; no spec, library or solver is involved.
fn synthetic(input: &str, out: &str) -> Result<(), String> {
    let battery: Value =
        serde_json::from_str(&std::fs::read_to_string(input).map_err(|error| error.to_string())?)
            .map_err(|error| error.to_string())?;
    let mut records = Vec::new();
    for case in battery["cases"].as_array().ok_or("expected cases")? {
        let decay = triplet_list(&case["decay"])?;
        let feed = lists(&case["feed"])?;
        let removal = lists(&case["removal"])?;
        let spectra = lists(&case["spectra"])?;
        let n0: Vec<f64> = case["n0"]
            .as_array()
            .ok_or("expected n0")?
            .iter()
            .map(|v| v.as_f64().ok_or("bad n0"))
            .collect::<Result<_, _>>()?;
        let sched: Vec<(f64, f64)> = case["schedule"]
            .as_array()
            .ok_or("expected schedule")?
            .iter()
            .map(|s| {
                Ok((
                    s[0].as_f64().ok_or("bad dt")?,
                    s[1].as_f64().ok_or("bad multiplier")?,
                ))
            })
            .collect::<Result<_, String>>()?;
        let problem = prune::PruneProblem {
            n: case["n"].as_u64().ok_or("expected n")? as usize,
            decay: &decay,
            feed: &feed,
            removal: &removal,
            spectra: &spectra,
            n0: &n0,
        };
        prune::capture::begin();
        let _ = prune::prune_schedule(
            &problem,
            &sched,
            case["rate_mode"].as_bool().unwrap_or(true),
            case["bmin"].as_f64().ok_or("expected bmin")?,
        );
        let capture = prune::capture::take();
        records.push(json!({
            "name": case["name"],
            "capture": capture_json(capture.first().ok_or("nothing captured")?),
        }));
    }
    let output = json!({"schema": "actinv-p148-prune-probe-synthetic-1", "records": records});
    std::fs::write(out, output.to_string()).map_err(|error| error.to_string())
}

fn main() {
    let arguments: Vec<String> = std::env::args().collect();
    let result = match arguments.as_slice() {
        [_, flag, input, out] if flag == "--synthetic" => synthetic(input, out),
        [_, spec, out] => from_spec(spec, out),
        _ => {
            eprintln!("usage: prune_probe SPEC.json OUT.json | prune_probe --synthetic BATTERY.json OUT.json");
            std::process::exit(2);
        }
    };
    if let Err(error) = result {
        eprintln!("{error}");
        std::process::exit(1);
    }
}

fn from_spec(spec_path: &str, out: &str) -> Result<(), String> {
    let text = std::fs::read_to_string(spec_path).map_err(|error| error.to_string())?;
    let spec = Spec::from_json(&text)?;
    let prepared = PreparedRun::prepare(&spec)?;
    prune::capture::begin();
    let run = prepared.run(&spec, "prune_probe");
    let captures = prune::capture::take();
    let run = run?;
    let output = json!({
        "schema": "actinv-p148-prune-probe-1",
        "captures": captures.iter().map(capture_json).collect::<Vec<_>>(),
        "result": serde_json::to_value(&run).map_err(|error| error.to_string())?,
    });
    std::fs::write(out, output.to_string()).map_err(|error| error.to_string())
}
