//! Runs a problem from a pack the way the browser does (`actinv_core::pack::run_from_pack`), so
//! a browser result can be checked against the same run natively.
//!
//!   pack_run SPEC.json PACKDIR REACTIONS OUT.json
//!
//! REACTIONS is `auto` (deepen round by round until the result settles, `pack::Deepening`, as the
//! browser does) or a fixed number of reactions. The library and decay data come from the pack
//! files each round's plan names; any other input the run reports missing is read from disk.

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
        let fixed: Option<usize> = match reactions.as_str() {
            "auto" => None,
            count => Some(
                count
                    .parse()
                    .map_err(|_| format!("bad reaction count {count}"))?,
            ),
        };
        let directory = std::path::Path::new(directory);
        let graph: PackGraph =
            serde_json::from_slice(&read(&directory.join("graph.json").to_string_lossy())?)
                .map_err(|e| e.to_string())?;
        let spec = Spec::from_json(&spec_text)?;
        let mut pack_files: HashMap<String, Vec<u8>> = HashMap::new();
        let mut inputs: HashMap<String, Vec<u8>> = HashMap::new();
        let mut deepening = pack::Deepening::new();
        let mut reactions = fixed.unwrap_or(pack::ROUNDS[0]);
        let started = std::time::Instant::now();
        let result = loop {
            let planned = std::time::Instant::now();
            let (reach, files) = pack::plan(&spec, &graph, reactions)?;
            let plan_seconds = planned.elapsed().as_secs_f64();
            for file in &files {
                if !pack_files.contains_key(file) {
                    let bytes = read(&directory.join(file).to_string_lossy())?;
                    pack_files.insert(file.clone(), bytes);
                }
            }
            eprintln!(
                "plan {plan_seconds:.2}s: {} targets, {} decay states, {} files, {} bytes",
                reach.targets.len(),
                reach.nuclides.len(),
                files.len(),
                files
                    .iter()
                    .map(|file| pack_files[file].len())
                    .sum::<usize>()
            );
            let solved = std::time::Instant::now();
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
            eprintln!(
                "round {reactions}: run {:.1}s",
                solved.elapsed().as_secs_f64()
            );
            if fixed.is_some() {
                break result;
            }
            match deepening.solved(&reach, result) {
                pack::Next::Run(deeper) => reactions = deeper,
                pack::Next::Done(result) => break *result,
            }
        };
        if fixed.is_none() {
            eprintln!(
                "rounds {}",
                serde_json::to_string(&deepening.rounds).map_err(|e| e.to_string())?
            );
        }
        eprintln!("run {:.1}s", started.elapsed().as_secs_f64());
        let text = serde_json::to_string(&result).map_err(|e| e.to_string())?;
        std::fs::write(out, text).map_err(|e| format!("{out}: {e}"))
    })();
    if let Err(error) = result {
        eprintln!("pack_run: {error}");
        std::process::exit(1);
    }
}
