//! Runs a problem from a pack the way the browser does (`actinv_core::pack::run_from_pack`), so
//! a browser result can be checked against the same run natively.
//!
//!   pack_run SPEC.json PACKDIR REACTIONS OUT.json
//!
//! The library and decay data come from the pack files the plan names; any other input the run
//! reports missing is read from disk.

use actinv_core::pack::{self, PackGraph};
use actinv_core::spec::Spec;
use std::collections::HashMap;

fn read(path: &str) -> Result<Vec<u8>, String> {
    std::fs::read(path).map_err(|e| format!("{path}: {e}"))
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let [spec_path, directory, reactions, out] = args.as_slice() else {
        eprintln!("usage: pack_run SPEC.json PACKDIR REACTIONS OUT.json");
        std::process::exit(2);
    };
    let result = (|| -> Result<(), String> {
        let spec_text = String::from_utf8(read(spec_path)?).map_err(|e| e.to_string())?;
        let reactions: usize = reactions
            .parse()
            .map_err(|_| format!("bad reaction count {reactions}"))?;
        let directory = std::path::Path::new(directory);
        let graph: PackGraph =
            serde_json::from_slice(&read(&directory.join("graph.json").to_string_lossy())?)
                .map_err(|e| e.to_string())?;
        let planned = std::time::Instant::now();
        let (reach, files) = pack::plan(&Spec::from_json(&spec_text)?, &graph, reactions)?;
        let plan_seconds = planned.elapsed().as_secs_f64();
        let mut pack_files = HashMap::new();
        for file in files {
            let bytes = read(&directory.join(&file).to_string_lossy())?;
            pack_files.insert(file, bytes);
        }
        eprintln!(
            "plan {plan_seconds:.2}s: {} targets, {} decay states, {} files, {} bytes",
            reach.targets.len(),
            reach.nuclides.len(),
            pack_files.len(),
            pack_files.values().map(Vec::len).sum::<usize>()
        );
        let parts: Vec<(usize, Vec<u8>)> = reach
            .targets
            .iter()
            .map(|&target| (target, pack_files[&graph.targets[target].file].clone()))
            .collect();
        let assembled = std::time::Instant::now();
        pack::assemble(&graph, &parts)?;
        eprintln!("assemble {:.2}s", assembled.elapsed().as_secs_f64());
        let joined = std::time::Instant::now();
        pack::decay_text(&graph, &reach.nuclides, &pack_files)?;
        eprintln!("decay text {:.2}s", joined.elapsed().as_secs_f64());
        let mut inputs: HashMap<String, Vec<u8>> = HashMap::new();
        let started = std::time::Instant::now();
        let result = loop {
            match pack::run_from_pack(
                &spec_text,
                "pack-run",
                &graph,
                reactions,
                &pack_files,
                inputs.clone(),
            ) {
                Ok(result) => break result,
                Err(error) => {
                    let Some(missing) =
                        error.strip_suffix(" was not supplied with the run's inputs")
                    else {
                        return Err(error);
                    };
                    if inputs.contains_key(missing) {
                        return Err(format!("asked twice for {missing}"));
                    }
                    inputs.insert(missing.to_owned(), read(missing)?);
                }
            }
        };
        eprintln!("run {:.1}s", started.elapsed().as_secs_f64());
        let text = serde_json::to_string(&result).map_err(|e| e.to_string())?;
        std::fs::write(out, text).map_err(|e| format!("{out}: {e}"))
    })();
    if let Err(error) = result {
        eprintln!("pack_run: {error}");
        std::process::exit(1);
    }
}
