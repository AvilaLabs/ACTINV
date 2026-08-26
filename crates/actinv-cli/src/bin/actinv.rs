//! `actinv run SPEC.json [OUT.json]` — the command-line entry point to the same core the Python API and the harness use.
use actinv_core::{run::run, spec::Spec};

fn main() {
    let a: Vec<String> = std::env::args().collect();
    let usage = "usage: actinv run SPEC.json [OUT.json]\n       actinv validate SPEC.json";
    if a.len() < 3 { eprintln!("{usage}"); std::process::exit(2); }
    let text = match std::fs::read_to_string(&a[2]) { Ok(t) => t, Err(e) => { eprintln!("cannot read {}: {e}", a[2]); std::process::exit(2); } };
    let spec = match Spec::from_json(&text) { Ok(s) => s, Err(e) => { eprintln!("{e}"); std::process::exit(2); } };
    match a[1].as_str() {
        "validate" => println!("ok: {} — {} groups, {} steps", spec.spec, spec.spectrum.flux_per_group.len(), spec.schedule.len()),
        "run" => {
            let r = match run(&spec, "cli") { Ok(r) => r, Err(e) => { eprintln!("{e}"); std::process::exit(1); } };
            let js = serde_json::to_string_pretty(&r).expect("serialise result");
            if a.len() > 3 { std::fs::write(&a[3], js).expect("write result"); eprintln!("{} steps, {} of {} states, {:.1} ms -> {}", r.steps.len(), r.pruned_states, r.total_states, r.ms, a[3]); }
            else { println!("{js}"); }
        }
        _ => { eprintln!("{usage}"); std::process::exit(2); }
    }
}
