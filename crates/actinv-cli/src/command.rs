//! ACTINV command-line entry point. Run and export commands consume the same result schema
//! used by the Python binding and validation harness.
use crate::{embedded_catalog, embedded_catalog_json, fetch_bundle, verify_bundle};
use actinv_core::{
    flux::{import_fispact, import_mctal, import_meshtal, import_openmc, ImportSummary},
    mesh::{run_mesh, MeshSpec},
    photon::{
        export_mcnp, export_openmc, export_openmc_mesh, mesh_cell_from_source, MeshPhotonCell,
        PhotonSourceOut,
    },
    run::run,
    spec::Spec,
    study::{self, Study},
};
use actinv_data::{
    activation::Projectile,
    builder::{self, BuildOptions, LibraryFormat},
    covariance::{build_covariance as build_covariance_sidecar, CovarianceBuildOptions},
    groups::GroupStructure,
    normalize::NormalizeProfile,
    spallation::SpallationSplice,
};
use std::collections::BTreeMap;

const DATA_USAGE: &str = "usage: actinv data list\n\
                              actinv data fetch [BUNDLE] [--output DIR] [--force]\n\
                              actinv data verify [BUNDLE] [--output DIR]\n\
                              actinv data manifest";

fn die(message: impl std::fmt::Display, code: i32) -> ! {
    eprintln!("{message}");
    std::process::exit(code);
}

/// Exit when `out` is one of the command's input files: the result would
/// replace the input.
fn guard_out(out: Option<&str>, inputs: &[&str]) {
    if let Err(e) = crate::output_guard::refuse_optional(out, inputs) {
        die(e, 1);
    }
}

/// The text of an input file, or of stdin when the path is `-`.
fn read(path: &str) -> String {
    if path == "-" {
        let mut text = String::new();
        use std::io::Read;
        std::io::stdin()
            .read_to_string(&mut text)
            .unwrap_or_else(|e| die(format!("cannot read stdin: {e}"), 2));
        return text;
    }
    std::fs::read_to_string(path).unwrap_or_else(|e| die(format!("cannot read {path}: {e}"), 2))
}

/// The photon source of one step of a result document; `label` names the result in messages.
/// Every failure is a bad input (exit status 2 on the command line).
pub fn photon_source_at(
    result_text: &str,
    label: &str,
    step: usize,
) -> Result<PhotonSourceOut, String> {
    let result: serde_json::Value = serde_json::from_str(result_text)
        .map_err(|e| format!("cannot parse result {label}: {e}"))?;
    let steps = result["steps"]
        .as_array()
        .ok_or_else(|| format!("{label} has no result steps array"))?;
    let selected = steps
        .iter()
        .find(|value| value["step"].as_u64() == Some(step as u64))
        .ok_or_else(|| format!("{label} has no step {step}"))?;
    let value = selected.get("photon_source").ok_or_else(|| {
        format!("step {step} has no photon_source; request photons or dose in the run spec")
    })?;
    serde_json::from_value(value.clone())
        .map_err(|e| format!("cannot decode photon_source at step {step}: {e}"))
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
    photon_source_at(&read(result_path), result_path, step).unwrap_or_else(|e| die(e, 2))
}

/// A command failure: the message for stderr and the process exit status.
pub type Failure = (i32, String);

/// Stream an `actinv-mesh-result-1` file and collect every cell's
/// step-`step` photon source into a spatial-source fragment. Cells
/// without photons in the export group structure contribute no entry;
/// missing bounds, a missing step, or an unrequested photon source are
/// hard errors — the spatial contract never silently drops geometry.
pub fn mesh_photon_cells(path: &str, step: usize) -> Result<Vec<MeshPhotonCell>, Failure> {
    let file = std::fs::File::open(path)
        .map_err(|e| (2, format!("cannot open mesh result {path}: {e}")))?;
    let reader = std::io::BufReader::new(file);
    let mut cells = Vec::new();
    let mut seen_header = false;
    for (line_no, line) in std::io::BufRead::lines(reader).enumerate() {
        let line =
            line.map_err(|e| (2, format!("cannot read {path} line {}: {e}", line_no + 1)))?;
        if line.trim().is_empty() {
            continue;
        }
        let record: serde_json::Value = serde_json::from_str(&line)
            .map_err(|e| (2, format!("cannot parse {path} line {}: {e}", line_no + 1)))?;
        match record["record"].as_str() {
            Some("header") => {
                if record["schema"].as_str() != Some("actinv-mesh-result-1") {
                    return Err((2, format!("{path} is not an actinv-mesh-result-1 file")));
                }
                seen_header = true;
            }
            Some("cell") => {
                if !seen_header {
                    return Err((2, format!("{path} cell record precedes its header")));
                }
                let id = record["id"].as_str().unwrap_or("?").to_string();
                let bounds: [[f64; 2]; 3] = serde_json::from_value(record["bounds_cm"].clone())
                    .map_err(|_| {
                        (
                            1,
                            format!("cell '{id}' has no bounds_cm; cannot place its source"),
                        )
                    })?;
                let volume: f64 = serde_json::from_value(record["volume_cm3"].clone())
                    .map_err(|_| (1, format!("cell '{id}' has no volume_cm3")))?;
                let steps = record["result"]["steps"]
                    .as_array()
                    .ok_or_else(|| (1, format!("cell '{id}' result has no steps")))?;
                let selected = steps
                    .iter()
                    .find(|v| v["step"].as_u64() == Some(step as u64))
                    .ok_or_else(|| (1, format!("cell '{id}' has no step {step}")))?;
                let source: PhotonSourceOut = serde_json::from_value(
                    selected.get("photon_source").cloned().ok_or_else(|| {
                        (
                            1,
                            format!(
                                "cell '{id}' step {step} has no photon_source; \
                                         request photons in the mesh spec"
                            ),
                        )
                    })?,
                )
                .map_err(|e| (1, format!("cell '{id}' step {step} photon_source: {e}")))?;
                if let Some(cell) =
                    mesh_cell_from_source(id, bounds, volume, &source).map_err(|e| (1, e))?
                {
                    cells.push(cell);
                }
            }
            _ => {}
        }
    }
    if !seen_header {
        return Err((2, format!("{path} has no actinv-mesh-result-1 header")));
    }
    Ok(cells)
}

fn collect_mesh_photon_cells(path: &str, step: usize) -> Vec<MeshPhotonCell> {
    mesh_photon_cells(path, step).unwrap_or_else(|(code, message)| die(message, code))
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

fn human_bytes(bytes: u64) -> String {
    const UNITS: [&str; 5] = ["B", "KiB", "MiB", "GiB", "TiB"];
    let mut value = bytes as f64;
    let mut unit = 0;
    while value >= 1024.0 && unit + 1 < UNITS.len() {
        value /= 1024.0;
        unit += 1;
    }
    if unit == 0 {
        format!("{bytes} {}", UNITS[unit])
    } else {
        format!("{value:.1} {}", UNITS[unit])
    }
}

fn study_command(args: &[String]) {
    const STUDY_USAGE: &str = "usage: actinv study validate STUDY.json\n\
                              actinv study build STUDY.json [OUTDIR] [--revocations FILE]\n\
                              actinv study run STUDY.json [OUTDIR] [--revocations FILE]";
    if args.is_empty() {
        die(STUDY_USAGE, 2);
    }
    let sub = args[0].as_str();
    if !matches!(sub, "validate" | "build" | "run") || args.len() < 2 {
        die(STUDY_USAGE, 2);
    }
    let outdir_arg = args
        .get(2)
        .filter(|s| !s.starts_with("--"))
        .map(String::as_str);
    let opt_start = if outdir_arg.is_some() { 3 } else { 2 };
    let options = valued_options(&args[opt_start..]);
    reject_unknown(&options, &["--revocations"]);
    let study_path = std::path::Path::new(&args[1]);
    let revocations = options.get("--revocations").map(std::path::PathBuf::from);
    // schema/scope validation never touches the filesystem; catalog
    // resolution (which requires installed artifacts) applies to build/run
    let text = &read(&args[1]);
    let study = if sub == "validate" {
        Study::from_json(text)
    } else {
        Study::from_json(&crate::resolve_catalog_json(text).unwrap_or_else(|e| die(e, 2)))
    }
    .unwrap_or_else(|e| die(e, 2));
    match sub {
        "validate" => println!(
            "ok: {} — {} cases, {} responses",
            study.study_id,
            study.case_ids().len(),
            study.responses.len()
        ),
        "build" | "run" => {
            let outdir = study::outdir_for(study_path, outdir_arg);
            let result = if sub == "build" {
                study::build(&study, study_path, &outdir, revocations.as_deref())
            } else {
                study::execute(&study, study_path, &outdir, revocations.as_deref())
            }
            .unwrap_or_else(|e| die(e, 1));
            let n = result["cases"]
                .as_array()
                .map(Vec::len)
                .or_else(|| result["n_cases"].as_u64().map(|n| n as usize))
                .or_else(|| {
                    result["population"]["declared"]
                        .as_u64()
                        .map(|n| n as usize)
                })
                .unwrap_or(0);
            println!(
                "ok: {} — {} cases -> {}",
                study.study_id,
                n,
                outdir.display()
            );
        }
        _ => die(STUDY_USAGE, 2),
    }
}

/// Write the ALARA export of the R2S source `bytes` into `out_dir` and return its one-line
/// summary. The caller decides whether `out_dir` may already hold files.
pub fn write_alara_export(
    bytes: &[u8],
    shutdown_t_s: f64,
    out_dir: &std::path::Path,
) -> Result<String, Failure> {
    let export = actinv_core::source_adapter::export_source_alara(bytes, shutdown_t_s)
        .map_err(|e| (1, e))?;
    std::fs::create_dir_all(out_dir)
        .map_err(|e| (1, format!("cannot create {}: {e}", out_dir.display())))?;
    for file in &export.files {
        std::fs::write(out_dir.join(&file.name), &file.text).map_err(|e| {
            (
                1,
                format!("cannot write {}/{}: {e}", out_dir.display(), file.name),
            )
        })?;
    }
    let index_text = serde_json::to_string_pretty(&export.index)
        .map_err(|e| (1, format!("cannot serialize alara index: {e}")))?;
    std::fs::write(out_dir.join("actinv-alara-index.json"), index_text).map_err(|e| {
        (
            1,
            format!(
                "cannot write {}/actinv-alara-index.json: {e}",
                out_dir.display()
            ),
        )
    })?;
    Ok(format!(
        "step {} alara source: {} cells, cooling {} s -> {}",
        export.index["step"],
        export.files.len(),
        export.index["cooling_s"],
        out_dir.display()
    ))
}

fn data_command(args: &[String]) {
    if args.is_empty() {
        die(DATA_USAGE, 2);
    }
    match args[0].as_str() {
        "--help" | "-h" if args.len() == 1 => println!("{DATA_USAGE}"),
        "manifest" if args.len() == 1 => print!("{}", embedded_catalog_json()),
        "list" if args.len() == 1 => {
            let catalog = embedded_catalog().unwrap_or_else(|error| die(error, 1));
            println!(
                "ACTINV data catalog v{} (default: {})",
                catalog.catalog_version, catalog.default_bundle
            );
            for bundle in &catalog.bundles {
                let bytes = catalog
                    .source_download_bytes(bundle)
                    .unwrap_or_else(|error| die(error, 1));
                let marker = if bundle.id == catalog.default_bundle {
                    " [default]"
                } else {
                    ""
                };
                println!(
                    "  {}{} — {} download — {}",
                    bundle.id,
                    marker,
                    human_bytes(bytes),
                    bundle.description
                );
            }
            println!("release: {}", catalog.release_url);
        }
        "fetch" | "verify" => {
            let operation = args[0].as_str();
            let mut bundle = None;
            // the same directory a run reads: $ACTINV_DATA_DIR, else ./actinv-data
            let mut output = match std::env::var_os("ACTINV_DATA_DIR") {
                Some(value) if !value.is_empty() => std::path::PathBuf::from(value),
                _ => std::path::PathBuf::from("actinv-data"),
            };
            let mut output_seen = false;
            let mut force = false;
            let mut index = 1;
            while index < args.len() {
                match args[index].as_str() {
                    "--output" => {
                        if output_seen || index + 1 == args.len() {
                            die("--output must occur once and name a directory", 2);
                        }
                        output = std::path::PathBuf::from(&args[index + 1]);
                        output_seen = true;
                        index += 2;
                    }
                    "--force" if operation == "fetch" => {
                        if force {
                            die("duplicate option --force", 2);
                        }
                        force = true;
                        index += 1;
                    }
                    value if value.starts_with('-') => {
                        die(format!("unknown data option {value}"), 2)
                    }
                    value => {
                        if bundle.replace(value).is_some() {
                            die("data command accepts at most one BUNDLE", 2);
                        }
                        index += 1;
                    }
                }
            }
            let catalog = embedded_catalog().unwrap_or_else(|error| die(error, 1));
            let chosen = catalog.bundle(bundle).unwrap_or_else(|error| die(error, 2));
            if operation == "fetch" {
                let installed: u64 = chosen
                    .artifacts
                    .iter()
                    .filter_map(|id| catalog.artifact(id).ok())
                    .map(|artifact| artifact.bytes)
                    .sum();
                eprintln!(
                    "Installing {}: {} to download, {} on disk, into {}",
                    chosen.id,
                    human_bytes(
                        catalog
                            .source_download_bytes(chosen)
                            .unwrap_or_else(|error| die(error, 1))
                    ),
                    human_bytes(installed),
                    output
                        .join(format!("v{}", catalog.catalog_version))
                        .display()
                );
            }
            let summary = if operation == "fetch" {
                fetch_bundle(bundle, &output, force)
            } else {
                verify_bundle(bundle, &output)
            }
            .unwrap_or_else(|error| die(error, 1));
            if operation == "fetch" {
                eprintln!(
                    "Installed and verified {} files in {}.\nNext: actinv new problem.json",
                    summary.files.len(),
                    summary.root
                );
            } else {
                eprintln!(
                    "Verified {} files in {}.",
                    summary.files.len(),
                    summary.root
                );
            }
            println!(
                "{}",
                serde_json::to_string_pretty(&summary).expect("serialise data-operation summary")
            );
        }
        _ => die(DATA_USAGE, 2),
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

fn build_library(args: &[String]) {
    if args.len() < 2 {
        die("build-library needs INPUT OUTPUT.npz", 2);
    }
    let input = &args[0];
    let output = &args[1];
    let options = valued_options(&args[2..]);
    reject_unknown(
        &options,
        &[
            "--format",
            "--projectile",
            "--groups",
            "--temperature-K",
            "--workers",
            "--cache",
            "--grid-density",
            "--strict-states",
            "--profile",
            "--decay",
            "--decay-fallback",
            "--continue-on-error",
            "--spallation",
            "--splice-energy-MeV",
        ],
    );
    let format = LibraryFormat::parse(options.get("--format").copied().unwrap_or("auto"))
        .unwrap_or_else(|error| die(error, 2));
    let profile = NormalizeProfile::parse(options.get("--profile").copied().unwrap_or("none"))
        .unwrap_or_else(|error| die(error, 2));
    let projectile_value = options.get("--projectile").copied().unwrap_or("auto");
    let requested_projectile = if projectile_value == "auto" {
        None
    } else {
        Some(Projectile::parse(projectile_value).unwrap_or_else(|error| die(error, 2)))
    };
    let detected_projectile = requested_projectile.unwrap_or_else(|| {
        builder::inspect_projectile(input).unwrap_or_else(|error| die(error, 2))
    });
    let groups = match options.get("--groups").copied() {
        Some(spec) => GroupStructure::resolve(spec),
        None if detected_projectile == Projectile::Neutron => GroupStructure::fispact_709(),
        None => GroupStructure::fispact_162(),
    }
    .unwrap_or_else(|error| die(error, 2));
    let default_temperature = if detected_projectile == Projectile::Neutron {
        293.6
    } else {
        0.0
    };
    if options.contains_key("--splice-energy-MeV") && !options.contains_key("--spallation") {
        die("--splice-energy-MeV needs --spallation TABLE.json", 2);
    }
    let spallation = options.get("--spallation").map(|path| {
        // The splice energy is given in MeV, as FISPACT's USESPALLATION cutoff is.
        let splice_mev: f64 = parsed_option(&options, "--splice-energy-MeV")
            .unwrap_or(actinv_data::spallation::DEFAULT_SPLICE_EV / 1.0e6);
        SpallationSplice::from_file(path, splice_mev * 1.0e6).unwrap_or_else(|error| die(error, 2))
    });
    let build_options = BuildOptions {
        format,
        projectile: requested_projectile,
        groups,
        temperature_K: parsed_option(&options, "--temperature-K").unwrap_or(default_temperature),
        workers: parsed_option(&options, "--workers").unwrap_or(1),
        cache: options.get("--cache").map(std::path::PathBuf::from),
        grid_density: parsed_option(&options, "--grid-density").unwrap_or(1.0),
        strict_states: parsed_option(&options, "--strict-states").unwrap_or(false),
        decay_path: options.get("--decay").map(std::path::PathBuf::from),
        decay_fallback_path: options
            .get("--decay-fallback")
            .map(std::path::PathBuf::from),
        normalize_profile: profile,
        continue_on_error: parsed_option(&options, "--continue-on-error").unwrap_or(false),
        spallation,
    };
    let summary =
        builder::build_library(input, output, &build_options).unwrap_or_else(|error| die(error, 1));
    println!(
        "{} targets, {} rows, {} cache hits, {} {}, sha256 {}",
        summary.targets,
        summary.rows,
        summary.cache_hits,
        summary.projectile.name(),
        summary.output.display(),
        summary.sha256_npz
    );
    eprintln!("index -> {}", summary.index.display());
    if summary.spliced_rows_untouched_nonzero > 0 {
        eprintln!(
            "warning: the spallation splice left {} library row(s) untouched that are nonzero at or above the splice; see the index's spallation.untouched_rows_nonzero_in_spliced_groups",
            summary.spliced_rows_untouched_nonzero
        );
    }
}

fn build_damage(args: &[String]) {
    if args.len() < 2 {
        die("build-damage needs EVALUATION_DIR OUT.json", 2);
    }
    let input = &args[0];
    let output = &args[1];
    // --displacement-energy EL=eV may repeat (one per element), so it is peeled off before the
    // single-valued option parser, which rejects duplicates.
    let mut displacement_energies = BTreeMap::new();
    let mut remaining: Vec<String> = Vec::new();
    let mut rest = args[2..].iter();
    while let Some(arg) = rest.next() {
        if arg == "--displacement-energy" {
            let value = rest
                .next()
                .unwrap_or_else(|| die("option '--displacement-energy' has no value", 2));
            let (symbol, energy) = value.split_once('=').unwrap_or_else(|| {
                die(
                    format!("--displacement-energy expects EL=eV, got '{value}'"),
                    2,
                )
            });
            let energy: f64 = energy.parse().unwrap_or_else(|_| {
                die(
                    format!("invalid displacement energy '{energy}' for {symbol}"),
                    2,
                )
            });
            if !(energy.is_finite() && energy > 0.0) {
                die(
                    format!("displacement energy for {symbol} must be positive and finite"),
                    2,
                );
            }
            if displacement_energies
                .insert(symbol.to_string(), energy)
                .is_some()
            {
                die(format!("duplicate --displacement-energy for {symbol}"), 2);
            }
        } else {
            remaining.push(arg.clone());
        }
    }
    let options = valued_options(&remaining);
    reject_unknown(
        &options,
        &[
            "--projectile",
            "--groups",
            "--temperature-K",
            "--cache",
            "--method",
            "--capture-photons",
            "--continuum-recoil",
            "--grid-density",
            "--continue-on-error",
        ],
    );
    let kinematic = match options.get("--method").copied().unwrap_or("default") {
        "default" => false,
        "kinematic" => true,
        other => die(
            format!("unknown damage method '{other}'; expected default or kinematic"),
            2,
        ),
    };
    if !kinematic
        && (!displacement_energies.is_empty()
            || options.contains_key("--capture-photons")
            || options.contains_key("--continuum-recoil")
            || options.contains_key("--grid-density")
            || options.contains_key("--continue-on-error"))
    {
        die(
            "--displacement-energy, --capture-photons, --continuum-recoil, --grid-density and --continue-on-error need --method kinematic",
            2,
        );
    }
    let projectile_value = options.get("--projectile").copied().unwrap_or("auto");
    let requested_projectile = if projectile_value == "auto" {
        None
    } else {
        Some(Projectile::parse(projectile_value).unwrap_or_else(|error| die(error, 2)))
    };
    let detected_projectile = requested_projectile.unwrap_or_else(|| {
        builder::inspect_projectile(input).unwrap_or_else(|error| die(error, 2))
    });
    let groups = match options.get("--groups").copied() {
        Some(spec) => GroupStructure::resolve(spec),
        None if detected_projectile == Projectile::Neutron => GroupStructure::fispact_709(),
        None => GroupStructure::fispact_162(),
    }
    .unwrap_or_else(|error| die(error, 2));
    if kinematic {
        // The kinematic build reconstructs resonances at 0 K, so 0 K is its default.
        let kinematic_options = actinv_data::damage_kinematic::KinematicOptions {
            projectile: requested_projectile,
            groups,
            temperature_K: parsed_option(&options, "--temperature-K").unwrap_or(0.0),
            cache: options.get("--cache").map(std::path::PathBuf::from),
            displacement_energy_eV: displacement_energies,
            default_displacement_energy_eV:
                actinv_data::damage_kinematic::OTHERWISE_DISPLACEMENT_ENERGY_EV,
            capture_photons: actinv_data::damage_kinematic::CapturePhotons::parse(
                options
                    .get("--capture-photons")
                    .copied()
                    .unwrap_or("evaluated"),
            )
            .unwrap_or_else(|error| die(error, 2)),
            continuum_recoil: actinv_data::damage_kinematic::ContinuumRecoil::parse(
                options
                    .get("--continuum-recoil")
                    .copied()
                    .unwrap_or("kinematic"),
            )
            .unwrap_or_else(|error| die(error, 2)),
            grid_density: parsed_option(&options, "--grid-density").unwrap_or(1.0),
            continue_on_error: parsed_option(&options, "--continue-on-error").unwrap_or(false),
        };
        let summary = actinv_data::damage_kinematic::build_damage_kinematic(
            input,
            output,
            &kinematic_options,
        )
        .unwrap_or_else(|error| die(error, 1));
        // Without --continue-on-error a failure stops the build, so the count is shown only with it and the summary of
        // an ordinary build is unchanged.
        let failed = if kinematic_options.continue_on_error {
            format!("{} failed evaluations, ", summary.failed_evaluations)
        } else {
            String::new()
        };
        println!(
            "{} damage targets, {} evaluations without a covered elastic channel, {}{} cache hits, {} {}, sha256 {}",
            summary.targets,
            summary.uncovered_evaluations,
            failed,
            summary.cache_hits,
            summary.projectile.name(),
            summary.output.display(),
            summary.sha256
        );
        return;
    }
    let default_temperature = if detected_projectile == Projectile::Neutron {
        293.6
    } else {
        0.0
    };
    let build_options = builder::DamageBuildOptions {
        projectile: requested_projectile,
        groups,
        temperature_K: parsed_option(&options, "--temperature-K").unwrap_or(default_temperature),
        cache: options.get("--cache").map(std::path::PathBuf::from),
    };
    let summary =
        builder::build_damage(input, output, &build_options).unwrap_or_else(|error| die(error, 1));
    println!(
        "{} damage targets, {} evaluations without MT=444, {} cache hits, {} {}, sha256 {}",
        summary.targets,
        summary.uncovered_evaluations,
        summary.cache_hits,
        summary.projectile.name(),
        summary.output.display(),
        summary.sha256
    );
}

fn build_shielding(args: &[String]) {
    if args.len() < 2 {
        die("build-shielding needs EVALUATION_DIR OUT.json", 2);
    }
    let input = &args[0];
    let output = &args[1];
    let options = valued_options(&args[2..]);
    reject_unknown(&options, &["--projectile", "--groups", "--cache"]);
    let projectile_value = options.get("--projectile").copied().unwrap_or("auto");
    let requested_projectile = if projectile_value == "auto" {
        None
    } else {
        Some(Projectile::parse(projectile_value).unwrap_or_else(|error| die(error, 2)))
    };
    let detected_projectile = requested_projectile.unwrap_or_else(|| {
        builder::inspect_projectile(input).unwrap_or_else(|error| die(error, 2))
    });
    let groups = match options.get("--groups").copied() {
        Some(spec) => GroupStructure::resolve(spec),
        None if detected_projectile == Projectile::Neutron => GroupStructure::fispact_709(),
        None => GroupStructure::fispact_162(),
    }
    .unwrap_or_else(|error| die(error, 2));
    let build_options = builder::ShieldingBuildOptions {
        projectile: requested_projectile,
        groups,
        cache: options.get("--cache").map(std::path::PathBuf::from),
    };
    let summary = builder::build_shielding(input, output, &build_options)
        .unwrap_or_else(|error| die(error, 1));
    println!(
        "{} shielding targets, {} evaluations without unresolved data, {} cache hits, {} {}, sha256 {}",
        summary.targets,
        summary.uncovered_evaluations,
        summary.cache_hits,
        summary.projectile.name(),
        summary.output.display(),
        summary.sha256
    );
}

fn build_covariance(args: &[String]) {
    if args.len() < 3 {
        die(
            "build-covariance needs INPUT ACTIVATION.npz OUTPUT.cov.npz",
            2,
        );
    }
    let input = &args[0];
    let activation = &args[1];
    let output = &args[2];
    let options = valued_options(&args[3..]);
    reject_unknown(&options, &["--workers", "--cache"]);
    let build_options = CovarianceBuildOptions {
        workers: parsed_option(&options, "--workers").unwrap_or(1),
        cache: options.get("--cache").map(std::path::PathBuf::from),
    };
    let summary = build_covariance_sidecar(input, activation, output, &build_options)
        .unwrap_or_else(|error| die(error, 1));
    println!(
        "{} targets, {} MF=33 sections, {} components, {} cache hits, {} files with MF=33, sha256 {}",
        summary.targets,
        summary.sections,
        summary.components,
        summary.cache_hits,
        summary.files_with_mf33,
        summary.sha256_npz
    );
    eprintln!("sidecar -> {}", summary.output.display());
    eprintln!("index -> {}", summary.index.display());
}

/// Where the standard bundle installs under a data root, and which of its files are absent or
/// the wrong size there. Sizes only: `actinv data verify` checks the hashes.
struct StandardData {
    dir: std::path::PathBuf,
    files: usize,
    missing: Vec<String>,
}

fn standard_data(catalog: &crate::DataCatalog, root: &std::path::Path) -> StandardData {
    try_standard_data(catalog, root).unwrap_or_else(|error| die(error, 1))
}

fn try_standard_data(
    catalog: &crate::DataCatalog,
    root: &std::path::Path,
) -> Result<StandardData, String> {
    let dir = root.join(format!("v{}", catalog.catalog_version));
    let bundle = catalog.bundle(None).map_err(|error| error.to_string())?;
    let missing = bundle
        .artifacts
        .iter()
        .filter(|id| {
            catalog.artifact(id).map_or(true, |artifact| {
                std::fs::metadata(dir.join(&artifact.path))
                    .map(|m| m.len())
                    .ok()
                    != Some(artifact.bytes)
            })
        })
        .cloned()
        .collect();
    Ok(StandardData {
        dir,
        files: bundle.artifacts.len(),
        missing,
    })
}

/// The fetch command that installs the standard data where a run will look for it.
fn fetch_hint(data_dir: Option<&str>) -> String {
    match data_dir {
        Some(dir) => format!("actinv data fetch --output {dir}"),
        None => "actinv data fetch".to_string(),
    }
}

/// What `actinv doctor` found: the text for stdout, and the failure that ended the check, if any.
pub struct DoctorReport {
    pub text: String,
    pub failure: Option<Failure>,
}

/// `actinv doctor`: can ACTINV run here, and if not, the one thing to do next. `problem` reads
/// the problem document whose input files are to be checked; it runs after the header is written,
/// so a failure there still reports the header.
pub fn doctor_report(problem: Option<&dyn Fn() -> Result<String, Failure>>) -> DoctorReport {
    let mut text = String::new();
    let failure = doctor_into(&mut text, problem).err();
    DoctorReport { text, failure }
}

fn doctor_into(
    text: &mut String,
    problem: Option<&dyn Fn() -> Result<String, Failure>>,
) -> Result<(), Failure> {
    use std::fmt::Write;
    let _ = writeln!(
        text,
        "ACTINV {}\nWorking directory: {}\nCache override: {}",
        env!("CARGO_PKG_VERSION"),
        std::env::current_dir().unwrap_or_default().display(),
        match std::env::var_os("ACTINV_CACHE_DIR") {
            // Every cache user rejects an empty override; say so here too.
            Some(value) if value.is_empty() => "set but empty (invalid)".to_string(),
            Some(value) => value.to_string_lossy().into_owned(),
            None => "platform default".to_string(),
        }
    );
    match actinv_data::prepared::default_cache_root() {
        Ok(path) => {
            let _ = writeln!(text, "Cache directory: {}", path.display());
        }
        Err(error) => {
            let _ = writeln!(text, "Cache directory: {error}");
        }
    }
    let catalog = embedded_catalog().map_err(|error| (1, error.to_string()))?;
    let root = crate::data_root().map_err(|error| (1, error.to_string()))?;
    let from_env = std::env::var_os("ACTINV_DATA_DIR").is_some_and(|value| !value.is_empty());
    let _ = writeln!(
        text,
        "Data directory: {} ({})\nData catalog: {} (standard bundle {})",
        root.display(),
        if from_env {
            "from ACTINV_DATA_DIR"
        } else {
            "default; set ACTINV_DATA_DIR to use another"
        },
        catalog.catalog_version,
        catalog.default_bundle
    );
    if let Some(read_problem) = problem {
        let spec = Spec::from_json(
            &crate::resolve_catalog_json(&read_problem()?)
                .map_err(|error| (2, error.to_string()))?,
        )
        .map_err(|error| (2, error.to_string()))?;
        crate::workflow::check_files(&spec, false).map_err(|error| (1, error.to_string()))?;
        text.push_str("Input files are readable. Run `actinv validate SPEC.json --hashes` to check declared hashes.\n");
        return Ok(());
    }
    let status = try_standard_data(&catalog, &root).map_err(|error| (1, error))?;
    if status.missing.is_empty() {
        let _ = writeln!(
            text,
            "Standard data: installed ({} files in {}).\nReady. Next: actinv new problem.json",
            status.files,
            status.dir.display()
        );
        Ok(())
    } else {
        Err((
            1,
            format!(
                "Standard data: not installed in {} (missing {}).\nNext: actinv data fetch",
                status.dir.display(),
                status.missing.join(", ")
            ),
        ))
    }
}

fn doctor(problem: Option<&str>) {
    let report = match problem {
        Some(path) => doctor_report(Some(&|| Ok(read(path)))),
        None => doctor_report(None),
    };
    print!("{}", report.text);
    if let Some((code, message)) = report.failure {
        die(message, code);
    }
}

/// Run the command-line interface with an explicit argv vector.
///
/// The standalone binary supplies `std::env::args()`. Python console scripts must instead supply `sys.argv`, because
/// Python's process argv also contains the interpreter and wrapper path.
pub fn main_from(a: Vec<String>) {
    if a.len() < 2 {
        die(crate::help::LANDING, 2);
    }
    if a[1] == "help" {
        if a.len() > 3 {
            die(crate::help::usage("help"), 2);
        }
        match a.get(2) {
            None => println!("{}", crate::help::LANDING),
            Some(name) => match crate::help::page(name) {
                Some(text) => println!("{text}"),
                None => die(crate::help::unknown(name), 2),
            },
        }
        return;
    }
    // --help anywhere after the command shows that command's page; it is never a file name
    if a[2..]
        .iter()
        .any(|arg| matches!(arg.as_str(), "--help" | "-h"))
    {
        match crate::help::page(&a[1]) {
            Some(text) => println!("{text}"),
            None => die(crate::help::unknown(&a[1]), 2),
        }
        return;
    }
    match a[1].as_str() {
        "waste" => {
            if a.get(2).is_some_and(|arg| arg == "composition") {
                if a.get(3).is_some_and(|arg| arg == "solve") {
                    if a.len() < 5 || a.len() > 6 {
                        die(
                            "usage: actinv waste composition solve SPEC.json [OUT.json]",
                            2,
                        );
                    }
                    guard_out(a.get(5).map(String::as_str), &[a[4].as_str()]);
                    crate::waste_composition_solve::run(&a[4], a.get(5).map(String::as_str))
                        .unwrap_or_else(|e| die(e, 1));
                } else {
                    if a.len() < 4 || a.len() > 5 {
                        die(
                            "usage: actinv waste composition COMPOSITION.json [OUT.json]",
                            2,
                        );
                    }
                    guard_out(a.get(4).map(String::as_str), &[a[3].as_str()]);
                    crate::waste_composition::run(&a[3], a.get(4).map(String::as_str))
                        .unwrap_or_else(|e| die(e, 1));
                }
            } else if a.get(2).is_some_and(|arg| arg == "intrusion-screen") {
                if a.len() < 4 || a.len() > 5 {
                    die(
                        "usage: actinv waste intrusion-screen SPEC.json [OUT.json]",
                        2,
                    );
                }
                guard_out(a.get(4).map(String::as_str), &[a[3].as_str()]);
                crate::waste_intrusion::run(&a[3], a.get(4).map(String::as_str))
                    .unwrap_or_else(|e| die(e, 1));
            } else if a.get(2).is_some_and(|arg| arg == "uncertainty") {
                if a.len() < 4 || a.len() > 5 {
                    die("usage: actinv waste uncertainty SPEC.json [OUT.json]", 2);
                }
                guard_out(a.get(4).map(String::as_str), &[a[3].as_str()]);
                crate::waste_uncertainty::run(&a[3], a.get(4).map(String::as_str))
                    .unwrap_or_else(|e| die(e, 1));
            } else if a.get(2).is_some_and(|arg| arg == "package") {
                if a.len() < 4 || a.len() > 5 {
                    die("usage: actinv waste package SPEC.json [OUT.json]", 2);
                }
                guard_out(a.get(4).map(String::as_str), &[a[3].as_str()]);
                crate::waste_package::run(&a[3], a.get(4).map(String::as_str))
                    .unwrap_or_else(|e| die(e, 1));
            } else if a.get(2).is_some_and(|arg| arg == "bounds") {
                if a.len() < 4 || a.len() > 5 {
                    die("usage: actinv waste bounds BOUNDS.json [OUT.json]", 2);
                }
                guard_out(a.get(4).map(String::as_str), &[a[3].as_str()]);
                crate::waste_bounds::run(&a[3], a.get(4).map(String::as_str))
                    .unwrap_or_else(|e| die(e, 1));
            } else if a.get(2).is_some_and(|arg| arg == "budget")
                && a.get(3).is_some_and(|arg| arg == "uncertainty")
            {
                if a.len() < 5 || a.len() > 6 {
                    die(
                        "usage: actinv waste budget uncertainty SPEC.json [OUT.json]",
                        2,
                    );
                }
                guard_out(a.get(5).map(String::as_str), &[a[4].as_str()]);
                let doc = crate::waste_budget_uncertainty::run(&a[4], a.get(5).map(String::as_str))
                    .unwrap_or_else(|e| die(e, 1));
                let code = crate::waste_budget_uncertainty::exit_code(&doc);
                if code != 0 {
                    eprintln!("waste budget uncertainty: verification failed");
                    std::process::exit(code);
                }
            } else if a.get(2).is_some_and(|arg| arg == "budget") {
                if a.len() < 4 || a.len() > 5 {
                    die("usage: actinv waste budget BUDGET.json [OUT.json]", 2);
                }
                guard_out(a.get(4).map(String::as_str), &[a[3].as_str()]);
                let doc = crate::waste_budget::run(&a[3], a.get(4).map(String::as_str))
                    .unwrap_or_else(|e| die(e, 1));
                let code = crate::waste_budget::exit_code(&doc);
                if code != 0 {
                    eprintln!("waste budget: verification failed");
                    std::process::exit(code);
                }
            } else {
                if a.len() < 3 || a.len() > 4 {
                    die("usage: actinv waste WASTE.json [OUT.json]", 2);
                }
                guard_out(a.get(3).map(String::as_str), &[a[2].as_str()]);
                crate::waste::run(&a[2], a.get(3).map(String::as_str))
                    .unwrap_or_else(|e| die(e, 1));
            }
        }
        "inspect" => {
            // every other argument is a candidate input; the one after --out is the output
            if let Some(at) = a.iter().position(|arg| arg == "--out") {
                if let Some(out) = a.get(at + 1) {
                    let inputs: Vec<&str> = a[2..]
                        .iter()
                        .enumerate()
                        .filter(|(i, arg)| i + 2 != at + 1 && !arg.starts_with("--"))
                        .map(|(_, arg)| arg.as_str())
                        .collect();
                    guard_out(Some(out.as_str()), &inputs);
                }
            }
            match crate::inspect::run(&a[2..]) {
                Ok(()) => {}
                Err(crate::inspect::InspectError::Usage(message)) => die(message, 2),
                Err(crate::inspect::InspectError::Failure(message)) => die(message, 1),
            }
        }
        "report" => match crate::report::run(&a[2..]) {
            Ok(()) => {}
            Err(crate::report::ReportError::Usage(message)) => die(message, 2),
            Err(crate::report::ReportError::Failure(message)) => die(message, 1),
        },
        "new" => {
            let (out, data_dir, library) =
                match crate::workflow::parse_new_args(&a[2..]).unwrap_or_else(|e| die(e, 2)) {
                    crate::workflow::NewRequest::List => {
                        println!(
                            "{}",
                            crate::workflow::new_list().unwrap_or_else(|e| die(e, 1))
                        );
                        return;
                    }
                    crate::workflow::NewRequest::Problem {
                        out,
                        data_dir,
                        library,
                    } => (out, data_dir, library),
                };
            let root = data_dir.as_deref().unwrap_or("actinv-data");
            let spec = match &library {
                None => crate::workflow::new_example(std::path::Path::new(root)),
                Some(l) => crate::workflow::new_problem(
                    std::path::Path::new(root),
                    &l.material,
                    &l.source,
                    l.flux,
                    &l.irradiate,
                ),
            }
            .unwrap_or_else(|e| die(e, 2));
            let mut file = std::fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&out)
                .unwrap_or_else(|e| {
                    die(
                        format!("Cannot create {out}: {e}; choose a new filename"),
                        2,
                    )
                });
            use std::io::Write;
            writeln!(
                file,
                "{}",
                serde_json::to_string_pretty(&spec).expect("example serialization")
            )
            .unwrap_or_else(|e| die(e, 1));
            match &library {
                None => eprintln!(
                    "Wrote {out}: iron, 5 minutes at the FNS 14 MeV neutron source, then 20 cooling steps."
                ),
                Some(l) => {
                    let material = actinv_core::presets::material(&l.material)
                        .map(|m| m.name)
                        .unwrap_or_else(|_| l.material.clone());
                    let shape = actinv_core::presets::SpectrumShape::from_id(&l.source)
                        .map(|s| s.label())
                        .unwrap_or("");
                    eprintln!(
                        "Wrote {out}: {material}, {shape} at {:e} /cm\u{b2}/s for {}, then cooling to 10 y. Edit the schedule before running if it is not yours.",
                        l.flux,
                        l.irradiate.trim()
                    );
                }
            }
            let data = match data_dir.as_deref() {
                Some(dir) => Ok(std::path::PathBuf::from(dir)),
                None => crate::data_root(),
            };
            let catalog = embedded_catalog().unwrap_or_else(|e| die(e, 1));
            match data.map(|root| standard_data(&catalog, &root)) {
                Ok(status) if status.missing.is_empty() => eprintln!(
                    "Data: installed in {}.\nNext: actinv run {out} result.json",
                    status.dir.display(),
                ),
                Ok(status) => eprintln!(
                    "Data: not installed in {}.\nNext: {}, then actinv run {out} result.json",
                    status.dir.display(),
                    fetch_hint(data_dir.as_deref()),
                ),
                Err(e) => eprintln!("Data: {e}"),
            }
        }
        "doctor" => {
            if a.len() > 3 {
                die(crate::help::usage("doctor"), 2);
            }
            doctor(a.get(2).map(String::as_str));
        }
        "--version" | "-V" if a.len() == 2 => println!("actinv {}", env!("CARGO_PKG_VERSION")),
        "--help" | "-h" if a.len() == 2 => println!("{}", crate::help::LANDING),
        "build-covariance" => build_covariance(&a[2..]),
        "build-damage" => build_damage(&a[2..]),
        "build-library" => build_library(&a[2..]),
        "build-shielding" => build_shielding(&a[2..]),
        "data" => data_command(&a[2..]),
        "import-fispact" => {
            let cli = crate::fispact_import::parse_cli(&a[2..]).unwrap_or_else(|e| die(e, 2));
            println!(
                "{}",
                crate::fispact_import::run(&cli).unwrap_or_else(|e| die(e, 1))
            );
        }
        "import-flux" => {
            let summary = import_flux(&a[2..]);
            println!(
                "{}",
                serde_json::to_string_pretty(&summary).expect("serialise import summary")
            );
        }
        "reverse" => {
            let mut args = a[2..].iter().filter(|arg| arg.as_str() != "--segments");
            let (Some(problem), Some(measurements)) = (args.next(), args.next()) else {
                die(
                    "usage: actinv reverse PROBLEM.json MEASUREMENTS.json [OUT.json] [--segments]",
                    2,
                );
            };
            let out = args.next();
            if args.next().is_some() {
                die(
                    "usage: actinv reverse PROBLEM.json MEASUREMENTS.json [OUT.json] [--segments]",
                    2,
                );
            }
            let segments = a[2..].iter().any(|arg| arg == "--segments");
            guard_out(
                out.map(String::as_str),
                &[problem.as_str(), measurements.as_str()],
            );
            let problem_text =
                crate::resolve_catalog_json(&read(problem)).unwrap_or_else(|e| die(e, 2));
            let spec = Spec::from_json(&problem_text).unwrap_or_else(|e| die(e, 2));
            let measurements_text = read(measurements);
            let result =
                actinv_core::reverse::solve(&spec, &problem_text, &measurements_text, segments)
                    .unwrap_or_else(|e| die(e, 1));
            let text = serde_json::to_string_pretty(&result).expect("serialise reverse result");
            match out {
                Some(path) => {
                    std::fs::write(path, format!("{text}\n"))
                        .unwrap_or_else(|e| die(format!("Cannot write {path}: {e}"), 1));
                    println!("wrote {path}");
                }
                None => println!("{text}"),
            }
        }
        "reverse-qualified" => {
            const RQ_USAGE: &str =
                "usage: actinv reverse-qualified PROBLEM.json MEASUREMENTS.json OUT.ndjson";
            let positional: Vec<&String> = a[2..].iter().filter(|x| !x.starts_with("--")).collect();
            if positional.len() != 3 || a.len() - 2 != positional.len() {
                die(RQ_USAGE, 2);
            }
            guard_out(
                Some(positional[2].as_str()),
                &[positional[0].as_str(), positional[1].as_str()],
            );
            let problem_text =
                crate::resolve_catalog_json(&read(positional[0])).unwrap_or_else(|e| die(e, 2));
            let spec = Spec::from_json(&problem_text).unwrap_or_else(|e| die(e, 2));
            let measurements_text = read(positional[1]);
            let (doc, summary) =
                actinv_core::reverse::solve_qualified(&spec, &problem_text, &measurements_text)
                    .unwrap_or_else(|e| die(e, 1));
            std::fs::write(positional[2], doc)
                .unwrap_or_else(|e| die(format!("Cannot write {}: {e}", positional[2]), 1));
            eprintln!(
                "qualified inverse: {} segments, {} measurements, {} forward runs -> {}",
                summary["segments"],
                summary["measurements"],
                summary["forward_runs"],
                positional[2]
            );
        }
        "optimize" => {
            const OPT_USAGE: &str = "usage: actinv optimize OPTSPEC.json [OUTDIR] [--resume]";
            let resume = a.iter().any(|x| x == "--resume");
            let positional: Vec<&String> =
                a[2..].iter().filter(|x| x.as_str() != "--resume").collect();
            if positional.iter().any(|x| x.starts_with("--"))
                || positional.is_empty()
                || positional.len() > 2
            {
                die(OPT_USAGE, 2);
            }
            let summary = crate::optimize::run_optimize(
                positional[0],
                positional.get(1).map(|s| s.as_str()),
                resume,
            )
            .unwrap_or_else(|e| die(e, 1));
            println!(
                "optimize: {} evals, best {} ({}), {:.1} s -> {}",
                summary.n_evals,
                if summary.infeasible {
                    "none (infeasible box)".to_string()
                } else {
                    format!("eval {}", summary.best_feasible.unwrap_or(0))
                },
                summary.out_dir.join("optimize_result.json").display(),
                summary.wall_s,
                summary.out_dir.display(),
            );
        }
        "assimilate" => {
            if a.len() < 3 {
                die(
                    "usage: actinv assimilate --result RUN.json --assay ASSAY.json [--out OUT.json] [--emit-result UPDATED.json]\nFold an assay into certified response bands (log-Gaussian update with an emitted Kalman gain and a consistency verdict). The assay may carry scalar fields (response/value/standard_uncertainty), `entries: [{response,value,standard_uncertainty}]` — a multi-nuclide count fusing every line at its shared time_s — or `mixture: [{response,coefficient}]` with a scalar `value` — a linear-combination measurement (dose-rate, gross activity) fused jointly via a Kalman H-row update with the induced correlation matrix emitted.\n--emit-result writes the full run result with the fused bands replaced by posteriors — chainable into decide/clearance/another assimilate.",
                    2,
                );
            }
            let options = valued_options(&a[2..]);
            reject_unknown(&options, &["--result", "--assay", "--out", "--emit-result"]);
            let result = required_option(&options, "--result");
            let assay = required_option(&options, "--assay");
            let out = options.get("--out").copied();
            let emit = options.get("--emit-result").copied();
            guard_out(out, &[result, assay]);
            guard_out(emit, &[result, assay]);
            if let (Some(o), Some(e)) = (out, emit) {
                guard_out(Some(e), &[o]);
            }
            let summary = crate::assimilate::run(result, assay, out, emit)
                .unwrap_or_else(|error| die(error, 1));
            if out.is_none() {
                println!(
                    "{}",
                    serde_json::to_string_pretty(&summary).expect("serialise assimilate summary")
                );
            } else {
                println!("{}", serde_json::to_string(&summary).unwrap());
            }
        }
        "eval-spread" => {
            if a.len() < 3 || a.len() > 4 {
                die(
                    "usage: actinv eval-spread SPEC.json [OUT.json]\nSolve the spec under both decay primacies and report the per-quantity spread in log space.\nRequires decay.fallback naming the alternate evaluation; emits actinv-eval-spread-1.",
                    2,
                );
            }
            let summary = crate::evalspread::run(&a[2], a.get(3).map(String::as_str))
                .unwrap_or_else(|e| die(e, 1));
            println!("{}", serde_json::to_string(&summary).unwrap());
        }
        "surrogate" => {
            const SUR_USAGE: &str = "usage: actinv surrogate fit SURSPEC.json [OUTDIR]\n       \
                 actinv surrogate eval ARTIFACT.json X.json [OUT.json]";
            let positional: Vec<&String> = a[2..].iter().filter(|x| !x.starts_with("--")).collect();
            if positional.is_empty() || positional.len() != a.len() - 2 {
                die(SUR_USAGE, 2);
            }
            match positional[0].as_str() {
                "fit" if positional.len() == 2 || positional.len() == 3 => {
                    // fit writes OUTDIR/surrogate.json (OUTDIR defaults to the cwd)
                    let artifact =
                        std::path::Path::new(positional.get(2).map_or(".", |s| s.as_str()))
                            .join("surrogate.json");
                    guard_out(artifact.to_str(), &[positional[1].as_str()]);
                    let summary = crate::surrogate::run_fit(
                        positional[1],
                        positional.get(2).map(|s| s.as_str()),
                    )
                    .unwrap_or_else(|e| die(e, 1));
                    println!(
                        "surrogate fit: {} nodes, r_max [{}] -> {}",
                        summary["n_nodes"],
                        summary["r_max"]
                            .as_array()
                            .map(|a| a
                                .iter()
                                .map(|v| format!("{:.3e}", v.as_f64().unwrap_or(0.0)))
                                .collect::<Vec<_>>()
                                .join(", "))
                            .unwrap_or_default(),
                        summary["out"].as_str().unwrap_or("?"),
                    );
                }
                "eval" if positional.len() == 3 || positional.len() == 4 => {
                    guard_out(
                        positional.get(3).map(|s| s.as_str()),
                        &[positional[1].as_str(), positional[2].as_str()],
                    );
                    let out = crate::surrogate::run_eval(
                        positional[1],
                        positional[2],
                        positional.get(3).map(|s| s.as_str()),
                    )
                    .unwrap_or_else(|e| die(e, 1));
                    if positional.get(3).is_none() {
                        println!("{}", serde_json::to_string_pretty(&out).unwrap());
                    } else {
                        println!("surrogate eval -> {}", positional[3]);
                    }
                }
                _ => die(SUR_USAGE, 2),
            }
        }
        "budget" => {
            const BUDGET_USAGE: &str = "usage: actinv budget BUDGET.json [OUT.json] [--no-verify]";
            let mut verify = true;
            let mut positional = Vec::new();
            for x in &a[2..] {
                match x.as_str() {
                    "--no-verify" => verify = false,
                    f if f.starts_with("--") => die(format!("unknown budget flag {f}"), 2),
                    p => positional.push(p),
                }
            }
            if positional.is_empty() || positional.len() > 2 {
                die(BUDGET_USAGE, 2);
            }
            guard_out(positional.get(1).copied(), &[positional[0]]);
            let doc = crate::budget::run_budget(positional[0], positional.get(1).copied(), verify)
                .unwrap_or_else(|e| die(e, 1));
            if let Some(out) = positional.get(1) {
                eprintln!(
                    "budget: {} element solves, {} targets, verification {} (max rel dev {}) -> {}",
                    doc["element_solves"].as_array().map_or(0, Vec::len),
                    doc["targets"].as_array().map_or(0, Vec::len),
                    if doc["verification"]["skipped"] == true {
                        "skipped"
                    } else if doc["verification"]["verified"] == true {
                        "passed"
                    } else {
                        "FAILED"
                    },
                    doc["verification"]["max_rel_dev"],
                    out
                );
            }
            if doc["verification"]["skipped"] != true && doc["verification"]["verified"] != true {
                std::process::exit(3);
            }
        }
        "impurities" => {
            const IMPURITIES_USAGE: &str =
                "usage: actinv impurities SPEC.json [OUT.json] [--no-verify]";
            let mut verify = true;
            let mut positional = Vec::new();
            for x in &a[2..] {
                match x.as_str() {
                    "--no-verify" => verify = false,
                    f if f.starts_with("--") => die(format!("unknown impurities flag {f}"), 2),
                    p => positional.push(p),
                }
            }
            if positional.is_empty() || positional.len() > 2 {
                die(IMPURITIES_USAGE, 2);
            }
            guard_out(positional.get(1).copied(), &[positional[0]]);
            let doc = crate::impurities::run_impurities(
                positional[0],
                positional.get(1).copied(),
                verify,
            )
            .unwrap_or_else(|e| die(e, 1));
            if let Some(out) = positional.get(1) {
                eprintln!("{}", crate::impurities::summary_line(&doc, out));
            }
            if doc["verification"]["skipped"] != true && doc["verification"]["verified"] != true {
                std::process::exit(3);
            }
        }
        "decide" => {
            const DECIDE_USAGE: &str = "usage: actinv decide DECISION.json [OUT.json]";
            let positional: Vec<&String> = a[2..].iter().filter(|x| !x.starts_with("--")).collect();
            if positional.is_empty() || positional.len() > 2 || positional.len() != a.len() - 2 {
                die(DECIDE_USAGE, 2);
            }
            guard_out(
                positional.get(1).map(|s| s.as_str()),
                &[positional[0].as_str()],
            );
            let doc =
                crate::decide::run_decide(positional[0], positional.get(1).map(|s| s.as_str()))
                    .unwrap_or_else(|e| die(e, 1));
            if let Some(out) = positional.get(1) {
                let certified = doc["verdict"]["certified"].as_bool().unwrap_or(false);
                eprintln!(
                    "decide: {} — {} constraints, {} binding -> {}",
                    if certified {
                        "certified"
                    } else {
                        "not certified"
                    },
                    doc["constraints"].as_array().map_or(0, Vec::len),
                    doc["verdict"]["binding"].as_array().map_or(0, Vec::len),
                    out
                );
            }
        }
        "mesh" => {
            if a.len() != 4 {
                die(crate::help::usage(&a[1]), 2);
            }
            let spec = MeshSpec::from_json(
                &crate::resolve_catalog_json(&read(&a[2])).unwrap_or_else(|e| die(e, 2)),
            )
            .unwrap_or_else(|error| die(error, 2));
            let summary = run_mesh(&spec, &a[3]).unwrap_or_else(|error| die(error, 1));
            println!(
                "{}",
                serde_json::to_string_pretty(&summary).expect("serialise mesh summary")
            );
        }
        "twin" => {
            if a.len() < 3 || a.len() > 4 {
                die(
                    "usage: actinv twin TWINSPEC.json [OUT.json]\nEvaluate declared clearance limits against per-cell certified bands from an `actinv mesh` output.",
                    2,
                );
            }
            let summary = crate::twin::run(&a[2], if a.len() == 4 { Some(&a[3]) } else { None })
                .unwrap_or_else(|error| die(error, 1));
            if a.len() == 3 {
                println!(
                    "{}",
                    serde_json::to_string_pretty(&summary).expect("serialise twin summary")
                );
            } else {
                println!("{}", serde_json::to_string(&summary).unwrap());
            }
        }
        "study" => {
            study_command(&a[2..]);
        }
        "worker" => {
            if a.len() > 2 {
                die(
                    "usage: actinv worker\nServe actinv-worker-request-1 JSON lines on stdin; one actinv-worker-response-1 line per request on stdout.",
                    2,
                );
            }
            std::process::exit(crate::worker::serve());
        }
        "validate" | "run" => {
            if a.len() < 3 {
                die(crate::help::usage(&a[1]), 2);
            }
            if let Some(option) = a
                .get(3)
                .filter(|arg| a[1] == "run" && arg.starts_with('-') && arg.as_str() != "-")
            {
                die(
                    format!("unknown run option {option}\n{}", crate::help::usage("run")),
                    2,
                );
            }
            let profile = std::env::var_os("ACTINV_P14_PROFILE").is_some();
            let command_started = profile.then(std::time::Instant::now);
            let spec_started = profile.then(std::time::Instant::now);
            let spec = Spec::from_json(
                &crate::resolve_catalog_json(&read(&a[2])).unwrap_or_else(|e| die(e, 2)),
            )
            .unwrap_or_else(|e| die(e, 2));
            let spec_read_parse_ms = spec_started
                .map(|started| started.elapsed().as_secs_f64() * 1e3)
                .unwrap_or(0.0);
            if a[1] == "validate" {
                let level = a.get(3).map(String::as_str).unwrap_or("--schema");
                if a.len() > 4 || !matches!(level, "--schema" | "--files" | "--hashes") {
                    die(crate::help::usage("validate"), 2);
                }
                if level != "--schema" {
                    crate::workflow::check_files(&spec, level == "--hashes")
                        .unwrap_or_else(|e| die(e, 1));
                }
                println!(
                    "ok ({level}): {} — {} groups, {} steps",
                    spec.spec,
                    spec.spectrum.flux_per_group.len(),
                    spec.schedule.len()
                );
                return;
            }
            if a.len() > 4 {
                die(crate::help::usage("run"), 2);
            }
            guard_out(a.get(3).map(String::as_str), &[a[2].as_str()]);
            // a person at a terminal is told what the silence is; scripts and pipes are not
            let attended = {
                use std::io::IsTerminal;
                std::io::stderr().is_terminal()
            };
            if attended {
                eprintln!(
                    "Solving {} steps. A first run with new data also prepares a cache, so it takes longer.",
                    spec.schedule.len()
                );
            }
            let r = run(&spec, "cli").unwrap_or_else(|e| die(e, 1));
            // the result stays as computed; an unconverged power step is flagged on stderr only
            for step in &r.steps {
                if let Some(power) = step.power.as_ref().filter(|power| !power.converged) {
                    eprintln!(
                        "warning: power-driven step {} did not converge: power drift {:.3e} \
                         exceeds the tolerance {:.3e} at the cap of {} sub-intervals; raise \
                         kerma.max_substeps or loosen kerma.power_tolerance (power.converged is \
                         false in the result)",
                        step.step, power.max_power_drift, power.tolerance, power.max_substeps
                    );
                }
            }
            let serialization_started = profile.then(std::time::Instant::now);
            let js = serde_json::to_string_pretty(&r).expect("serialise result");
            let serialization_ms = serialization_started
                .map(|started| started.elapsed().as_secs_f64() * 1e3)
                .unwrap_or(0.0);
            let output_started = profile.then(std::time::Instant::now);
            if a.len() > 3 && a[3] != "-" {
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
                if attended {
                    eprintln!("Next: actinv report {}", a[3]);
                }
            } else {
                println!("{js}");
            }
            if profile {
                let output_write_ms = output_started
                    .expect("profiled output has a start time")
                    .elapsed()
                    .as_secs_f64()
                    * 1e3;
                let total_cli_ms = command_started
                    .expect("profiled command has a start time")
                    .elapsed()
                    .as_secs_f64()
                    * 1e3;
                eprintln!(
                    "ACTINV_P14_CLI_PROFILE {}",
                    serde_json::json!({
                        "schema": "actinv-p14-cli-profile-1",
                        "spec_read_parse_ms": spec_read_parse_ms,
                        "serialization_ms": serialization_ms,
                        "output_write_ms": output_write_ms,
                        "total_cli_ms": total_cli_ms,
                    })
                );
            }
        }
        "export-openmc" | "export-mcnp" => {
            if a.len() != 5 {
                die(crate::help::usage(&a[1]), 2);
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
        "export-openmc-mesh" => {
            if a.len() != 5 {
                die(crate::help::usage(&a[1]), 2);
            }
            let step: usize = a[3]
                .parse()
                .unwrap_or_else(|_| die("export-openmc-mesh STEP must be a positive integer", 2));
            if step == 0 {
                die("STEP is one-based and must be positive", 2);
            }
            let cells = collect_mesh_photon_cells(&a[2], step);
            let fragment = export_openmc_mesh(&cells).unwrap_or_else(|e| die(e, 1));
            std::fs::write(&a[4], fragment)
                .unwrap_or_else(|e| die(format!("cannot write {}: {e}", a[4]), 1));
            eprintln!(
                "step {} spatial photon source: {} cells -> {}",
                step,
                cells.len(),
                a[4]
            );
        }
        "export-r2s" => {
            if a.len() != 5 {
                die(crate::help::usage(&a[1]), 2);
            }
            let step: usize = a[3]
                .parse()
                .unwrap_or_else(|_| die("export-r2s STEP must be a positive integer", 2));
            if step == 0 {
                die("STEP is one-based and must be positive", 2);
            }
            let bytes = std::fs::read(&a[2])
                .unwrap_or_else(|e| die(format!("cannot read {}: {e}", a[2]), 2));
            let (doc, summary) =
                actinv_core::r2s::emit_r2s_source(&bytes, step).unwrap_or_else(|e| die(e, 1));
            std::fs::write(&a[4], doc)
                .unwrap_or_else(|e| die(format!("cannot write {}: {e}", a[4]), 1));
            eprintln!(
                "step {} banded r2s source: {} cells, {} partially unbanded -> {}",
                step, summary["cells"], summary["cells_partially_unbanded"], a[4]
            );
        }
        "export-r2s-joint" => {
            if a.len() != 6 {
                die(crate::help::usage(&a[1]), 2);
            }
            let step: usize = a[4]
                .parse()
                .unwrap_or_else(|_| die("export-r2s-joint STEP must be a positive integer", 2));
            if step == 0 {
                die("STEP is one-based and must be positive", 2);
            }
            let mesh_bytes = std::fs::read(&a[2])
                .unwrap_or_else(|e| die(format!("cannot read {}: {e}", a[2]), 2));
            let spec_bytes =
                crate::resolve_catalog_json(&read(&a[3])).unwrap_or_else(|e| die(e, 2));
            let (doc, summary) =
                actinv_core::r2s::emit_r2s_joint(&mesh_bytes, spec_bytes.as_bytes(), step)
                    .unwrap_or_else(|e| die(e, 1));
            std::fs::write(&a[5], doc)
                .unwrap_or_else(|e| die(format!("cannot write {}: {e}", a[5]), 1));
            eprintln!(
                "step {} joint r2s source: {} cells, sigma_correlated {} -> {}",
                step, summary["cells"], summary["sigma_total_correlated"], a[5]
            );
        }
        "export-r2s-cov" => {
            if a.len() != 6 {
                die(crate::help::usage(&a[1]), 2);
            }
            let step: usize = a[4]
                .parse()
                .unwrap_or_else(|_| die("export-r2s-cov STEP must be a positive integer", 2));
            if step == 0 {
                die("STEP is one-based and must be positive", 2);
            }
            let mesh_bytes = std::fs::read(&a[2])
                .unwrap_or_else(|e| die(format!("cannot read {}: {e}", a[2]), 2));
            let spec_bytes =
                crate::resolve_catalog_json(&read(&a[3])).unwrap_or_else(|e| die(e, 2));
            let (doc, summary) =
                actinv_core::r2s::emit_r2s_cov(&mesh_bytes, spec_bytes.as_bytes(), step)
                    .unwrap_or_else(|e| die(e, 1));
            std::fs::write(&a[5], doc)
                .unwrap_or_else(|e| die(format!("cannot write {}: {e}", a[5]), 1));
            eprintln!(
                "step {} source covariance: {} regions x {} groups, {} shared columns -> {}",
                step, summary["regions"], summary["groups"], summary["shared_columns"], a[5]
            );
        }
        "export-source" if a.len() >= 3 && a[2] == "alara" => {
            // actinv export-source alara R2S_SOURCE.ndjson OUT_DIR --shutdown-t-s T
            if a.len() < 5 {
                die(crate::help::usage(&a[1]), 2);
            }
            let out_dir = a[4].clone();
            let mut shutdown_t_s: Option<f64> = None;
            let mut i = 5;
            while i < a.len() {
                match a[i].as_str() {
                    "--shutdown-t-s" => {
                        shutdown_t_s = Some(
                            a.get(i + 1)
                                .and_then(|s| s.parse().ok())
                                .unwrap_or_else(|| die("--shutdown-t-s requires a number", 2)),
                        );
                        i += 2;
                    }
                    flag => die(format!("unknown export-source alara argument '{flag}'"), 2),
                }
            }
            let shutdown_t_s = shutdown_t_s
                .unwrap_or_else(|| die("export-source alara requires --shutdown-t-s T", 2));
            let out_path = std::path::Path::new(&out_dir);
            if out_path.exists() {
                let empty = out_path.is_dir()
                    && std::fs::read_dir(out_path)
                        .map(|mut rd| rd.next().is_none())
                        .unwrap_or(false);
                if !empty {
                    die(
                        format!("export-source alara: {out_dir} exists and is not empty"),
                        1,
                    );
                }
            }
            let bytes = std::fs::read(&a[3])
                .unwrap_or_else(|e| die(format!("cannot read {}: {e}", a[3]), 2));
            let line = write_alara_export(&bytes, shutdown_t_s, out_path)
                .unwrap_or_else(|(code, message)| die(message, code));
            eprintln!("{line}");
        }
        "export-source" => {
            if a.len() != 5 {
                die(crate::help::usage(&a[1]), 2);
            }
            let bytes = std::fs::read(&a[3])
                .unwrap_or_else(|e| die(format!("cannot read {}: {e}", a[3]), 2));
            let (doc, summary) = actinv_core::source_adapter::export_source(&a[2], &bytes)
                .unwrap_or_else(|e| die(e, 1));
            std::fs::write(&a[4], doc)
                .unwrap_or_else(|e| die(format!("cannot write {}: {e}", a[4]), 1));
            eprintln!(
                "step {} {} source: {} cells, {} photons/s -> {}",
                summary["step"],
                summary["format"],
                summary["cells"],
                summary["total_photons_s"],
                a[4]
            );
        }
        "clearance" => {
            // actinv clearance INPUT STEP [--limits PATH] [--confidence T] OUT.ndjson
            if a.len() < 4 {
                die(crate::help::usage(&a[1]), 2);
            }
            let step: u64 = a[3]
                .parse()
                .unwrap_or_else(|_| die("clearance STEP must be a positive integer", 2));
            if step == 0 {
                die("STEP is one-based and must be positive", 2);
            }
            let mut limits_path: Option<String> = None;
            let mut confidence = 0.95f64;
            let mut out_path: Option<String> = None;
            let mut i = 4;
            while i < a.len() {
                match a[i].as_str() {
                    "--limits" => {
                        limits_path = Some(
                            a.get(i + 1)
                                .cloned()
                                .unwrap_or_else(|| die("--limits requires a path", 2)),
                        );
                        i += 2;
                    }
                    "--confidence" => {
                        let usage = "--confidence requires a number in the open interval (0.5, 1)";
                        confidence = a
                            .get(i + 1)
                            .and_then(|s| s.parse().ok())
                            .unwrap_or_else(|| die(usage, 2));
                        i += 2;
                    }
                    flag if flag.starts_with("--") => {
                        die(format!("unknown clearance flag {flag}"), 2)
                    }
                    positional => {
                        if out_path.is_some() {
                            die("clearance takes exactly one output path", 2);
                        }
                        out_path = Some(positional.to_string());
                        i += 1;
                    }
                }
            }
            let out = out_path.unwrap_or_else(|| die(crate::help::usage(&a[1]), 2));
            let bytes = std::fs::read(&a[2])
                .unwrap_or_else(|e| die(format!("cannot read {}: {e}", a[2]), 2));
            let doc = actinv_core::clearance::emit_clearance(
                &bytes,
                step,
                limits_path.as_deref(),
                confidence,
            )
            .unwrap_or_else(|e| die(e, 1));
            std::fs::write(&out, doc)
                .unwrap_or_else(|e| die(format!("cannot write {out}: {e}"), 1));
            eprintln!("step {step} clearance evaluation -> {out}");
        }
        _ => die(crate::help::unknown(&a[1]), 2),
    }
}
