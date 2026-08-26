//! ACTINV command-line entry point. Run and export commands consume the same result schema
//! used by the Python binding and validation harness.
use actinv_core::{
    photon::{export_mcnp, export_openmc, PhotonSourceOut},
    run::run,
    spec::Spec,
};

fn die(message: impl std::fmt::Display, code: i32) -> ! {
    eprintln!("{message}");
    std::process::exit(code);
}

fn read(path: &str) -> String {
    std::fs::read_to_string(path).unwrap_or_else(|e| die(format!("cannot read {path}: {e}"), 2))
}

fn selected_source(result_path: &str, requested_step: &str) -> PhotonSourceOut {
    let step: usize = requested_step.parse().unwrap_or_else(|_| {
        die(
            format!("STEP must be a positive integer, got '{requested_step}'"),
            2,
        )
    });
    if step == 0 {
        die("STEP is one-based and must be positive", 2);
    }
    let result: serde_json::Value = serde_json::from_str(&read(result_path))
        .unwrap_or_else(|e| die(format!("cannot parse result {result_path}: {e}"), 2));
    let steps = result["steps"]
        .as_array()
        .unwrap_or_else(|| die(format!("{result_path} has no result steps array"), 2));
    let selected = steps
        .iter()
        .find(|value| value["step"].as_u64() == Some(step as u64))
        .unwrap_or_else(|| die(format!("{result_path} has no step {step}"), 2));
    let value = selected.get("photon_source").unwrap_or_else(|| {
        die(
            format!("step {step} has no photon_source; request photons or dose in the run spec"),
            2,
        )
    });
    serde_json::from_value(value.clone()).unwrap_or_else(|e| {
        die(
            format!("cannot decode photon_source at step {step}: {e}"),
            2,
        )
    })
}

fn main() {
    let a: Vec<String> = std::env::args().collect();
    let usage = "usage: actinv run SPEC.json [OUT.json]\n\
                 actinv validate SPEC.json\n\
                 actinv export-openmc RESULT.json STEP OUT.py\n\
                 actinv export-mcnp RESULT.json STEP OUT.sdef";
    if a.len() < 2 {
        die(usage, 2);
    }
    match a[1].as_str() {
        "validate" | "run" => {
            if a.len() < 3 {
                die(usage, 2);
            }
            let spec = Spec::from_json(&read(&a[2])).unwrap_or_else(|e| die(e, 2));
            if a[1] == "validate" {
                println!(
                    "ok: {} — {} groups, {} steps",
                    spec.spec,
                    spec.spectrum.flux_per_group.len(),
                    spec.schedule.len()
                );
                return;
            }
            let r = run(&spec, "cli").unwrap_or_else(|e| die(e, 1));
            let js = serde_json::to_string_pretty(&r).expect("serialise result");
            if a.len() > 3 {
                std::fs::write(&a[3], js)
                    .unwrap_or_else(|e| die(format!("cannot write {}: {e}", a[3]), 1));
                eprintln!(
                    "{} steps, {} of {} states, {:.1} ms -> {}",
                    r.steps.len(),
                    r.pruned_states,
                    r.total_states,
                    r.ms,
                    a[3]
                );
            } else {
                println!("{js}");
            }
        }
        "export-openmc" | "export-mcnp" => {
            if a.len() != 5 {
                die(usage, 2);
            }
            let source = selected_source(&a[2], &a[3]);
            let fragment = if a[1] == "export-openmc" {
                export_openmc(&source)
            } else {
                export_mcnp(&source)
            }
            .unwrap_or_else(|e| die(e, 1));
            std::fs::write(&a[4], fragment)
                .unwrap_or_else(|e| die(format!("cannot write {}: {e}", a[4]), 1));
            eprintln!("step {} photon source -> {}", a[3], a[4]);
        }
        _ => die(usage, 2),
    }
}
