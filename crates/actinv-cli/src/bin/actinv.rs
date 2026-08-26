//! ACTINV command-line entry point. Run and export commands consume the same result schema
//! used by the Python binding and validation harness.
use actinv_core::{
    flux::{import_fispact, import_mctal, import_meshtal, import_openmc, ImportSummary},
    mesh::{run_mesh, MeshSpec},
    photon::{export_mcnp, export_openmc, PhotonSourceOut},
    run::run,
    spec::Spec,
};
use std::collections::BTreeMap;

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

fn valued_options(args: &[String]) -> BTreeMap<&str, &str> {
    if !args.len().is_multiple_of(2) {
        die(format!("option '{}' has no value", args.last().unwrap()), 2);
    }
    let mut options = BTreeMap::new();
    for pair in args.chunks(2) {
        if !pair[0].starts_with("--") {
            die(format!("expected an option, got '{}'", pair[0]), 2);
        }
        if options.insert(pair[0].as_str(), pair[1].as_str()).is_some() {
            die(format!("duplicate option '{}'", pair[0]), 2);
        }
    }
    options
}

fn required_option<'a>(options: &'a BTreeMap<&str, &str>, name: &str) -> &'a str {
    options
        .get(name)
        .copied()
        .unwrap_or_else(|| die(format!("missing required option {name}"), 2))
}

fn parsed_option<T: std::str::FromStr>(options: &BTreeMap<&str, &str>, name: &str) -> Option<T> {
    options.get(name).map(|value| {
        value
            .parse()
            .unwrap_or_else(|_| die(format!("invalid value '{}' for {name}", value), 2))
    })
}

fn reject_unknown(options: &BTreeMap<&str, &str>, allowed: &[&str]) {
    if let Some(name) = options.keys().find(|name| !allowed.contains(name)) {
        die(format!("unknown option {name}"), 2);
    }
}

fn import_flux(args: &[String]) -> ImportSummary {
    if args.len() < 3 {
        die("import-flux needs FORMAT SOURCE OUT", 2);
    }
    let format = args[0].as_str();
    let source = &args[1];
    let output = &args[2];
    let options = valued_options(&args[3..]);
    match format {
        "openmc" => {
            reject_unknown(
                &options,
                &[
                    "--tally",
                    "--source-rate",
                    "--energy-floor-eV",
                    "--window-rows",
                ],
            );
            let tally = required_option(&options, "--tally")
                .parse()
                .unwrap_or_else(|_| die("--tally must be a positive integer", 2));
            let source_rate = required_option(&options, "--source-rate")
                .parse()
                .unwrap_or_else(|_| die("--source-rate must be numeric", 2));
            import_openmc(
                source,
                output,
                tally,
                source_rate,
                parsed_option(&options, "--energy-floor-eV"),
                parsed_option(&options, "--window-rows").unwrap_or(16_384),
            )
        }
        "meshtal" | "mctal" => {
            reject_unknown(&options, &["--tally", "--source-rate", "--energy-floor-eV"]);
            let tally = required_option(&options, "--tally")
                .parse()
                .unwrap_or_else(|_| die("--tally must be a positive integer", 2));
            let source_rate = required_option(&options, "--source-rate")
                .parse()
                .unwrap_or_else(|_| die("--source-rate must be numeric", 2));
            let energy_floor = parsed_option(&options, "--energy-floor-eV");
            if format == "meshtal" {
                import_meshtal(source, output, tally, source_rate, energy_floor)
            } else {
                import_mctal(source, output, tally, source_rate, energy_floor)
            }
        }
        "fispact" => {
            reject_unknown(&options, &["--groups"]);
            import_fispact(source, required_option(&options, "--groups"), output)
        }
        _ => Err(format!(
            "unsupported import-flux format '{format}'; expected openmc, meshtal, mctal or fispact"
        )),
    }
    .unwrap_or_else(|error| die(error, 1))
}

fn main() {
    let a: Vec<String> = std::env::args().collect();
    let usage = "usage: actinv run SPEC.json [OUT.json]\n\
                 actinv validate SPEC.json\n\
                 actinv import-flux openmc SOURCE.h5 OUT.ndjson --tally ID --source-rate RATE [--energy-floor-eV EV] [--window-rows N]\n\
                 actinv import-flux {meshtal|mctal} SOURCE OUT.ndjson --tally ID --source-rate RATE [--energy-floor-eV EV]\n\
                 actinv import-flux fispact FLUXES OUT.ndjson --groups GROUPS.json\n\
                 actinv mesh SPEC.json OUT.ndjson\n\
                 actinv export-openmc RESULT.json STEP OUT.py\n\
                 actinv export-mcnp RESULT.json STEP OUT.sdef";
    if a.len() < 2 {
        die(usage, 2);
    }
    match a[1].as_str() {
        "import-flux" => {
            let summary = import_flux(&a[2..]);
            println!(
                "{}",
                serde_json::to_string_pretty(&summary).expect("serialise import summary")
            );
        }
        "mesh" => {
            if a.len() != 4 {
                die(usage, 2);
            }
            let spec = MeshSpec::from_json(&read(&a[2])).unwrap_or_else(|error| die(error, 2));
            let summary = run_mesh(&spec, &a[3]).unwrap_or_else(|error| die(error, 1));
            println!(
                "{}",
                serde_json::to_string_pretty(&summary).expect("serialise mesh summary")
            );
        }
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
