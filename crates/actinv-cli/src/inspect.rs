//! `actinv inspect` — query the data files a run consumes (P131).
//!
//! The FISPACT-II PRINTLIB/EXTRACTXS analog, but query-oriented: each subcommand reads the same
//! loaders the solver uses (decay, activation library, fission yields, group structures) and
//! reports JSON by default, or a fixed-width table with `--table`. Nothing here alters data or
//! re-derives physics; the one-group collapse is the library's own `ReactionLibrary::one_group`.

use actinv_core::chain::{rtyp_digits, step_of};
use actinv_core::spec::Spectrum;
use actinv_data::composition::{material_key, symbol_of, MaterialKey};
use actinv_data::decay::{self, Nuclide};
use actinv_data::fission::{self, FissionYields, NuclideKey};
use actinv_data::groups::GroupStructure;
use actinv_data::library::{self, Library, ReactionLibrary, Row};
use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, BTreeSet, HashMap};

pub const USAGE: &str = "usage: actinv inspect decay DECAY_PATH [NUCLIDE...] [--table] [--out OUT]\n\
                         actinv inspect xs LIBRARY.npz [--target NUCLIDE] [--mt MT] [--spectrum SPEC.json] [--table] [--out OUT]\n\
                         actinv inspect yields YIELD_PATH... [--parent NUCLIDE] [--energy EV] [--cumulative] [--table] [--out OUT]\n\
                         actinv inspect library LIBRARY.npz [--table] [--out OUT]\n\
                         actinv inspect groups NAME [--table] [--out OUT]";

/// Detail printed by `actinv inspect --help`.
pub const HELP: &str = "Query decay data, activation libraries, fission yields and group structures.\n\
Output is JSON on standard output (or OUT); --table prints a fixed-width table instead.\n\
decay: half-life, decay modes with branching ratios and daughters, mean beta/gamma/alpha energies, discrete gamma lines.\n\
xs: reactions of a library (MT, product, groups, energy structure) and group cross sections for --target;\n\
    with --spectrum (a spectrum object or a run spec) also the collapsed one-group cross section and reaction rate.\n\
yields: independent (or --cumulative MT=459) fission yields, interpolated at --energy EV when given.\n\
library: targets, reactions, group structure, projectile and index provenance fields.\n\
groups: boundaries of a named group structure (fispact-709, fispact-162, an xN refinement, or a group JSON path).";

#[derive(Debug)]
pub enum InspectError {
    /// Malformed command line (exit status 2).
    Usage(String),
    /// A well-formed request that could not be answered (exit status 1).
    Failure(String),
}

#[derive(Debug, PartialEq)]
enum Command {
    Decay {
        path: String,
        nuclides: Vec<String>,
    },
    Xs {
        path: String,
        target: Option<String>,
        mt: Option<i32>,
        spectrum: Option<String>,
    },
    Yields {
        paths: Vec<String>,
        parent: Option<String>,
        energy: Option<f64>,
        cumulative: bool,
    },
    Library {
        path: String,
    },
    Groups {
        name: String,
    },
}

#[derive(Debug, PartialEq)]
struct Request {
    command: Command,
    table: bool,
    out: Option<String>,
}

/// A fixed-width table: left-aligned columns separated by two spaces.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Table {
    pub(crate) title: String,
    pub(crate) headers: Vec<String>,
    pub(crate) rows: Vec<Vec<String>>,
}

struct Output {
    json: Value,
    tables: Vec<Table>,
}

#[derive(Default)]
struct Options {
    positional: Vec<String>,
    values: BTreeMap<String, String>,
    flags: BTreeSet<String>,
}

fn parse_options(args: &[String], valued: &[&str], boolean: &[&str]) -> Result<Options, String> {
    let mut options = Options::default();
    let mut index = 0;
    while index < args.len() {
        let arg = args[index].as_str();
        if arg.starts_with("--") {
            if boolean.contains(&arg) {
                if !options.flags.insert(arg.to_string()) {
                    return Err(format!("{arg} given more than once"));
                }
            } else if valued.contains(&arg) {
                index += 1;
                let value = args
                    .get(index)
                    .ok_or_else(|| format!("{arg} needs a value"))?;
                if options
                    .values
                    .insert(arg.to_string(), value.clone())
                    .is_some()
                {
                    return Err(format!("{arg} given more than once"));
                }
            } else {
                return Err(format!("unknown inspect option {arg}"));
            }
        } else {
            options.positional.push(arg.to_string());
        }
        index += 1;
    }
    Ok(options)
}

fn one_positional(options: &mut Options, what: &str) -> Result<String, String> {
    if options.positional.len() != 1 {
        return Err(format!(
            "expected exactly one {what}, got {}",
            options.positional.len()
        ));
    }
    Ok(options.positional.remove(0))
}

fn parse_request(args: &[String]) -> Result<Request, String> {
    let Some(subcommand) = args.first() else {
        return Err("inspect needs a subcommand".into());
    };
    let rest = &args[1..];
    let (options, command) = match subcommand.as_str() {
        "decay" => {
            let mut options = parse_options(rest, &["--out"], &["--table"])?;
            if options.positional.is_empty() {
                return Err("inspect decay needs DECAY_PATH".into());
            }
            let path = options.positional.remove(0);
            let nuclides = std::mem::take(&mut options.positional);
            (options, Command::Decay { path, nuclides })
        }
        "xs" => {
            let mut options = parse_options(
                rest,
                &["--target", "--mt", "--spectrum", "--out"],
                &["--table"],
            )?;
            let path = one_positional(&mut options, "LIBRARY.npz")?;
            let mt = match options.values.get("--mt") {
                Some(text) => Some(
                    text.parse::<i32>()
                        .map_err(|_| format!("--mt must be an integer, got '{text}'"))?,
                ),
                None => None,
            };
            let command = Command::Xs {
                path,
                target: options.values.get("--target").cloned(),
                mt,
                spectrum: options.values.get("--spectrum").cloned(),
            };
            (options, command)
        }
        "yields" => {
            let mut options = parse_options(
                rest,
                &["--parent", "--energy", "--out"],
                &["--table", "--cumulative"],
            )?;
            if options.positional.is_empty() {
                return Err("inspect yields needs YIELD_PATH".into());
            }
            let energy = match options.values.get("--energy") {
                Some(text) => {
                    let value = text
                        .parse::<f64>()
                        .map_err(|_| format!("--energy must be a number in eV, got '{text}'"))?;
                    if !value.is_finite() || value < 0.0 {
                        return Err(format!(
                            "--energy must be finite and nonnegative, got '{text}'"
                        ));
                    }
                    Some(value)
                }
                None => None,
            };
            let command = Command::Yields {
                paths: std::mem::take(&mut options.positional),
                parent: options.values.get("--parent").cloned(),
                energy,
                cumulative: options.flags.contains("--cumulative"),
            };
            (options, command)
        }
        "library" => {
            let mut options = parse_options(rest, &["--out"], &["--table"])?;
            let path = one_positional(&mut options, "LIBRARY.npz")?;
            (options, Command::Library { path })
        }
        "groups" => {
            let mut options = parse_options(rest, &["--out"], &["--table"])?;
            let name = one_positional(&mut options, "group-structure NAME")?;
            (options, Command::Groups { name })
        }
        other => return Err(format!("unknown inspect subcommand '{other}'")),
    };
    Ok(Request {
        command,
        table: options.flags.contains("--table"),
        out: options.values.get("--out").cloned(),
    })
}

/// Run `actinv inspect` over the arguments that follow the word `inspect`.
pub fn run(args: &[String]) -> Result<(), InspectError> {
    let request =
        parse_request(args).map_err(|error| InspectError::Usage(format!("{error}\n{USAGE}")))?;
    let output = execute(&request.command).map_err(InspectError::Failure)?;
    let text = if request.table {
        render_tables(&output.tables)
    } else {
        serde_json::to_string_pretty(&output.json)
            .map_err(|error| InspectError::Failure(format!("serialise inspect output: {error}")))?
    };
    match &request.out {
        Some(path) => {
            std::fs::write(path, format!("{text}\n"))
                .map_err(|error| InspectError::Failure(format!("cannot write {path}: {error}")))?;
            eprintln!("wrote {path}");
        }
        None => println!("{text}"),
    }
    Ok(())
}

fn execute(command: &Command) -> Result<Output, String> {
    match command {
        Command::Decay { path, nuclides } => {
            let table = decay::parse_file(path)
                .map_err(|error| format!("cannot read decay data {path}: {error}"))?;
            decay_report(&table, nuclides, path)
        }
        Command::Xs {
            path,
            target,
            mt,
            spectrum,
        } => {
            let index = load_index(path)?;
            let target_index = match target {
                Some(name) => Some(resolve_target(
                    name,
                    index.as_ref().map(|index| index.targets.as_slice()),
                )?),
                None => None,
            };
            let loaded = match target_index {
                Some(target) => library::read_npz_target(path, target),
                None => library::read_npz(path),
            }
            .map_err(|error| format!("{path}: {error}"))?;
            let spectrum = spectrum.as_deref().map(load_spectrum).transpose()?;
            let selection = XsSelection {
                target: target_index,
                target_label: target.clone(),
                mt: *mt,
            };
            xs_report(&loaded, index.as_ref(), path, &selection, spectrum.as_ref())
        }
        Command::Yields {
            paths,
            parent,
            energy,
            cumulative,
        } => {
            let mut parsed = Vec::with_capacity(paths.len());
            for path in paths {
                parsed.push((path.clone(), fission::parse_file(path)?));
            }
            let parent = parent.as_deref().map(parse_nuclide).transpose()?;
            yields_report(&parsed, parent, *energy, *cumulative)
        }
        Command::Library { path } => {
            let structure =
                library::read_npz_structure(path).map_err(|error| format!("{path}: {error}"))?;
            let index = load_index(path)?;
            Ok(library_report(
                &structure.rows,
                structure.ngroups,
                &structure.bounds,
                index.as_ref(),
                path,
            ))
        }
        Command::Groups { name } => {
            let structure = GroupStructure::resolve(name)?;
            Ok(groups_report(&structure))
        }
    }
}

// ---- formatting ----

pub(crate) fn render_table(table: &Table) -> String {
    let mut widths: Vec<usize> = table
        .headers
        .iter()
        .map(|header| header.chars().count())
        .collect();
    for row in &table.rows {
        for (column, cell) in row.iter().enumerate().take(widths.len()) {
            widths[column] = widths[column].max(cell.chars().count());
        }
    }
    let line = |cells: &[String]| -> String {
        let mut text = String::new();
        for (column, &width) in widths.iter().enumerate() {
            if column > 0 {
                text.push_str("  ");
            }
            let cell = cells.get(column).map(String::as_str).unwrap_or("");
            text.push_str(&format!("{cell:<width$}"));
        }
        text.trim_end().to_string()
    };
    let mut out = String::new();
    if !table.title.is_empty() {
        out.push_str(&table.title);
        out.push('\n');
    }
    out.push_str(&line(&table.headers));
    out.push('\n');
    let rule: Vec<String> = widths.iter().map(|&width| "-".repeat(width)).collect();
    out.push_str(&line(&rule));
    for row in &table.rows {
        out.push('\n');
        out.push_str(&line(row));
    }
    out
}

fn render_tables(tables: &[Table]) -> String {
    tables
        .iter()
        .map(render_table)
        .collect::<Vec<_>>()
        .join("\n\n")
}

pub(crate) fn sci(value: f64) -> String {
    format!("{value:.6e}")
}

fn nuclide_name(za: i32, liso: i32) -> String {
    let symbol = symbol_of(za / 1000);
    if liso > 0 {
        format!("{symbol}{}m{liso}", za % 1000)
    } else {
        format!("{symbol}{}", za % 1000)
    }
}

fn parse_nuclide(name: &str) -> Result<(i32, i32), String> {
    match material_key(name)? {
        MaterialKey::Nuclide { za, liso, .. } => Ok((za, liso)),
        MaterialKey::Element(_) => Err(format!(
            "'{name}' must be an explicit nuclide such as Co60 or Ta182m1"
        )),
    }
}

// ---- decay ----

fn mode_label(rtyp: f64) -> String {
    let names: Vec<&str> = rtyp_digits(rtyp)
        .into_iter()
        .map(|digit| match digit {
            1 => "beta-",
            2 => "beta+/EC",
            3 => "IT",
            4 => "alpha",
            5 => "n",
            6 => "SF",
            7 => "p",
            _ => "?",
        })
        .collect();
    if names.is_empty() {
        "?".to_string()
    } else {
        names.join(" + ")
    }
}

/// Daughter state of one decay mode, as the decay network resolves it; `None` for spontaneous
/// fission and for modes the network does not model.
fn mode_daughter(za: i32, rtyp: f64, rfs: f64) -> Option<(i32, i32)> {
    let (mut z, mut a) = (za / 1000, za % 1000);
    for digit in rtyp_digits(rtyp) {
        let (delta_z, delta_a) = step_of(digit)?;
        z += delta_z;
        a += delta_a;
    }
    (z >= 1 && a >= 1).then(|| (z * 1000 + a, rfs.round() as i32))
}

fn spectrum_label(styp: f64) -> &'static str {
    match styp.round() as i32 {
        0 => "gamma",
        1 => "beta-",
        2 => "beta+/EC",
        3 => "internal-transition",
        4 => "alpha",
        5 => "neutron",
        6 => "spontaneous-fission",
        7 => "proton",
        8 => "discrete-electron",
        9 => "x-ray/annihilation",
        _ => "other",
    }
}

/// Discrete line count and total energy per decay (eV) over the spectra of one ENDF STYP.
/// A line's yield per decay is the spectrum's discrete normalisation FD times its intensity RI.
fn line_summary(nuclide: &Nuclide, styp: i32) -> (usize, f64) {
    let mut lines = 0;
    let mut energy = 0.0;
    for spectrum in nuclide
        .spectra
        .iter()
        .filter(|spectrum| spectrum.styp.round() as i32 == styp)
    {
        lines += spectrum.discrete.len();
        for line in &spectrum.discrete {
            energy += spectrum.fd * line.intensity * line.energy;
        }
    }
    (lines, energy)
}

fn decay_entry(nuclide: &Nuclide) -> Value {
    let stable = nuclide.nst == 1 || nuclide.half_life <= 0.0;
    let modes: Vec<Value> = nuclide
        .modes
        .iter()
        .map(|mode| {
            let daughter = mode_daughter(nuclide.za, mode.rtyp, mode.rfs);
            json!({
                "rtyp": mode.rtyp,
                "type": mode_label(mode.rtyp),
                "branching_ratio": mode.br,
                "branching_ratio_uncertainty": mode.dbr,
                "q_eV": mode.q,
                "daughter": daughter.map(|(za, liso)| nuclide_name(za, liso)),
                "daughter_za": daughter.map(|(za, _)| za),
                "daughter_liso": daughter.map(|(_, liso)| liso),
            })
        })
        .collect();
    let (gamma_lines, gamma_energy) = line_summary(nuclide, 0);
    let spectra: Vec<Value> = nuclide
        .spectra
        .iter()
        .map(|spectrum| {
            json!({
                "type": spectrum_label(spectrum.styp),
                "styp": spectrum.styp,
                "discrete_lines": spectrum.discrete.len(),
                "has_continuum": spectrum.continuous.is_some(),
                "average_energy_eV": spectrum.average_energy,
            })
        })
        .collect();
    json!({
        "nuclide": nuclide_name(nuclide.za, nuclide.liso),
        "za": nuclide.za,
        "liso": nuclide.liso,
        "stable": stable,
        "half_life_s": if stable { Value::Null } else { json!(nuclide.half_life) },
        "half_life_uncertainty_s": if stable { Value::Null } else { json!(nuclide.d_half_life) },
        "decay_constant_per_s": nuclide.lambda(),
        "mean_energy_eV": {
            "beta": nuclide.e_light(),
            "gamma": nuclide.e_em(),
            "alpha": nuclide.e_heavy(),
        },
        "modes": modes,
        "gamma": {
            "discrete_lines": gamma_lines,
            "total_energy_eV_per_decay": gamma_energy,
        },
        "spectra": spectra,
    })
}

fn decay_row(nuclide: &Nuclide) -> Vec<String> {
    let stable = nuclide.nst == 1 || nuclide.half_life <= 0.0;
    let modes = nuclide
        .modes
        .iter()
        .map(|mode| format!("{} {:.4}%", mode_label(mode.rtyp), mode.br * 100.0))
        .collect::<Vec<_>>()
        .join("; ");
    let (gamma_lines, gamma_energy) = line_summary(nuclide, 0);
    vec![
        nuclide_name(nuclide.za, nuclide.liso),
        if stable {
            "stable".to_string()
        } else {
            sci(nuclide.half_life)
        },
        modes,
        sci(nuclide.e_light()),
        sci(nuclide.e_em()),
        sci(nuclide.e_heavy()),
        gamma_lines.to_string(),
        sci(gamma_energy),
    ]
}

fn decay_report(
    table: &HashMap<(i32, i32), Nuclide>,
    requested: &[String],
    source: &str,
) -> Result<Output, String> {
    let mut keys: Vec<(i32, i32)> = if requested.is_empty() {
        table.keys().copied().collect()
    } else {
        requested
            .iter()
            .map(|name| parse_nuclide(name))
            .collect::<Result<Vec<_>, String>>()?
    };
    keys.sort_unstable();
    keys.dedup();
    let mut entries = Vec::with_capacity(keys.len());
    let mut rows = Vec::with_capacity(keys.len());
    for &(za, liso) in &keys {
        let nuclide = table.get(&(za, liso)).ok_or_else(|| {
            format!(
                "{} (ZA={za} LISO={liso}) is not in {source}",
                nuclide_name(za, liso)
            )
        })?;
        entries.push(decay_entry(nuclide));
        rows.push(decay_row(nuclide));
    }
    let json = json!({
        "schema": "actinv-inspect-decay-1",
        "source": source,
        "count": entries.len(),
        "nuclides": entries,
    });
    let headers = [
        "Nuclide",
        "Half-life (s)",
        "Modes",
        "E beta (eV)",
        "E gamma (eV)",
        "E alpha (eV)",
        "Gamma lines",
        "Gamma E/decay (eV)",
    ];
    Ok(Output {
        json,
        tables: vec![Table {
            title: format!("Decay data: {source}"),
            headers: headers.iter().map(|header| header.to_string()).collect(),
            rows,
        }],
    })
}

// ---- activation library ----

/// The `<stem>_index.json` sidecar of an activation library.
struct LibraryIndex {
    doc: Value,
    targets: Vec<(i32, i32)>,
}

impl LibraryIndex {
    fn from_value(doc: Value) -> Self {
        let targets: Vec<(i32, i32)> = doc
            .get("targets")
            .and_then(Value::as_array)
            .map(|targets| {
                targets
                    .iter()
                    .map(|target| {
                        (
                            target["za"].as_i64().unwrap_or(0) as i32,
                            target["liso"].as_i64().unwrap_or(0) as i32,
                        )
                    })
                    .collect()
            })
            .unwrap_or_default();
        Self { doc, targets }
    }

    fn text(&self, field: &str) -> Option<&str> {
        self.doc.get(field).and_then(Value::as_str)
    }
}

fn index_path(library_path: &str) -> String {
    match library_path.strip_suffix(".npz") {
        Some(stem) => format!("{stem}_index.json"),
        None => format!("{library_path}_index.json"),
    }
}

fn load_index(library_path: &str) -> Result<Option<LibraryIndex>, String> {
    let path = index_path(library_path);
    if !std::path::Path::new(&path).exists() {
        return Ok(None);
    }
    let text = std::fs::read_to_string(&path)
        .map_err(|error| format!("cannot read library index {path}: {error}"))?;
    let doc: Value = serde_json::from_str(&text)
        .map_err(|error| format!("cannot parse library index {path}: {error}"))?;
    Ok(Some(LibraryIndex::from_value(doc)))
}

/// A target given as a nuclide name (needs the library index) or as a raw target index.
fn resolve_target(spec: &str, targets: Option<&[(i32, i32)]>) -> Result<usize, String> {
    if let Ok(index) = spec.parse::<usize>() {
        if let Some(targets) = targets {
            if index >= targets.len() {
                return Err(format!(
                    "target index {index} is out of range for {} targets",
                    targets.len()
                ));
            }
        }
        return Ok(index);
    }
    let key = parse_nuclide(spec)?;
    let targets = targets.ok_or_else(|| {
        format!(
            "target '{spec}' needs the library index file (<library>_index.json); pass a target index instead"
        )
    })?;
    targets
        .iter()
        .position(|candidate| *candidate == key)
        .ok_or_else(|| format!("target '{spec}' is not in the library"))
}

/// The energy-structure name: the index's `groups`, else a named structure matched bitwise on the
/// boundaries, else `custom`.
fn structure_name(ngroups: usize, bounds: &[f64], index: Option<&LibraryIndex>) -> String {
    if let Some(name) = index.and_then(|index| index.text("groups")) {
        return name.to_string();
    }
    for (groups, name) in [(709, "fispact-709"), (162, "fispact-162")] {
        if ngroups != groups {
            continue;
        }
        if let Ok(Some(named)) = GroupStructure::resolve_named(name) {
            if named.boundaries_ev.len() == bounds.len()
                && named
                    .boundaries_ev
                    .iter()
                    .zip(bounds)
                    .all(|(left, right)| left.to_bits() == right.to_bits())
            {
                return name.to_string();
            }
        }
    }
    "custom".to_string()
}

fn mt_label(mt: i32) -> Option<&'static str> {
    Some(match mt {
        2 => "elastic",
        4 => "inelastic",
        16 => "(x,2n)",
        17 => "(x,3n)",
        18 => "fission",
        22 => "(x,na)",
        28 => "(x,np)",
        37 => "(x,4n)",
        51..=90 => "inelastic level",
        91 => "inelastic continuum",
        102 => "(x,g)",
        103 => "(x,p)",
        104 => "(x,d)",
        105 => "(x,t)",
        106 => "(x,3He)",
        107 => "(x,a)",
        111 => "(x,2p)",
        _ => return None,
    })
}

/// The residual nuclide a row produces; `None` when the row has no residual (ZAP below 1000).
fn product_name(row: &Row) -> Option<String> {
    (row.zap >= 1000).then(|| nuclide_name(row.zap, row.lfs.max(0)))
}

struct XsSelection {
    target: Option<usize>,
    target_label: Option<String>,
    mt: Option<i32>,
}

fn load_spectrum(path: &str) -> Result<Spectrum, String> {
    let text = std::fs::read_to_string(path)
        .map_err(|error| format!("cannot read spectrum {path}: {error}"))?;
    let doc: Value = serde_json::from_str(&text)
        .map_err(|error| format!("cannot parse spectrum {path}: {error}"))?;
    let node = doc.get("spectrum").cloned().unwrap_or(doc);
    serde_json::from_value(node)
        .map_err(|error| format!("{path} is neither a spectrum nor a run spec: {error}"))
}

/// Ascending group fluxes of `spectrum`, checked against the library's group structure.
fn spectrum_flux(spectrum: &Spectrum, library: &Library) -> Result<Vec<f64>, String> {
    if spectrum.flux_per_group.len() != library.ngroups {
        return Err(format!(
            "spectrum has {} group fluxes; the library has {} groups",
            spectrum.flux_per_group.len(),
            library.ngroups
        ));
    }
    if spectrum
        .flux_per_group
        .iter()
        .any(|flux| !flux.is_finite() || *flux < 0.0)
    {
        return Err("spectrum group fluxes must be finite and nonnegative".into());
    }
    if spectrum.structure == "custom" {
        let bounds = spectrum
            .boundaries_eV
            .as_ref()
            .ok_or("custom spectrum needs boundaries_eV")?;
        if bounds.len() != library.bounds.len()
            || bounds
                .iter()
                .zip(&library.bounds)
                .any(|(left, right)| (left - right).abs() > 1e-9 * right.abs())
        {
            return Err("spectrum boundaries_eV differ from the library group boundaries".into());
        }
    }
    Ok(spectrum.ascending_flux())
}

fn xs_report(
    library: &Library,
    index: Option<&LibraryIndex>,
    source: &str,
    selection: &XsSelection,
    spectrum: Option<&Spectrum>,
) -> Result<Output, String> {
    let structure = structure_name(library.ngroups, &library.bounds, index);
    let flux = spectrum
        .map(|spectrum| spectrum_flux(spectrum, library))
        .transpose()?;
    let total_flux = flux.as_ref().map(|flux| flux.iter().sum::<f64>());
    let selected: Vec<usize> = library
        .rows
        .iter()
        .enumerate()
        .filter(|(_, row)| {
            selection.target.is_none_or(|target| row.target == target)
                && selection.mt.is_none_or(|mt| row.mt == mt)
        })
        .map(|(position, _)| position)
        .collect();
    if selected.is_empty() {
        return Err(format!(
            "no reactions match target {} and MT {} in {source}",
            selection.target_label.as_deref().unwrap_or("(any)"),
            selection
                .mt
                .map_or("(any)".to_string(), |mt| mt.to_string())
        ));
    }
    // Group-resolved values are only listed for one chosen target; a whole library would be huge.
    let include_groups = selection.target.is_some();
    let target_name = |target: usize| -> Option<String> {
        index
            .and_then(|index| index.targets.get(target))
            .map(|&(za, liso)| nuclide_name(za, liso))
    };
    let mut reactions = Vec::with_capacity(selected.len());
    let mut summary_rows = Vec::with_capacity(selected.len());
    let mut last_one_group: Option<(f64, f64)> = None;
    for &position in &selected {
        let row = &library.rows[position];
        let sigma = library.sigma(position);
        let nonzero = sigma.iter().filter(|value| **value > 0.0).count();
        let peak = sigma.iter().copied().fold(0.0_f64, f64::max);
        let product = product_name(row);
        let mut entry = json!({
            "target_index": row.target,
            "target": target_name(row.target),
            "mt": row.mt,
            "label": mt_label(row.mt),
            "product": product,
            "product_za": row.zap,
            "product_lfs": row.lfs,
            "lmf": row.lmf,
            "group_count": library.ngroups,
            "energy_structure": structure,
            "nonzero_groups": nonzero,
            "max_cross_section_b": peak,
        });
        if include_groups {
            entry["cross_section_b"] = json!(sigma.to_vec());
        }
        let mut one_group = None;
        if let (Some(flux), Some(total)) = (flux.as_ref(), total_flux) {
            let xs = <Library as ReactionLibrary>::one_group(library, position, flux);
            let rate = xs * 1e-24 * total;
            entry["one_group_xs_b"] = json!(xs);
            entry["reaction_rate_per_target_atom_per_s"] = json!(rate);
            one_group = Some((xs, rate));
            last_one_group = one_group;
        }
        let mut cells = vec![
            target_name(row.target).unwrap_or_else(|| row.target.to_string()),
            row.mt.to_string(),
            mt_label(row.mt).unwrap_or("").to_string(),
            product.unwrap_or_default(),
            library.ngroups.to_string(),
            nonzero.to_string(),
            sci(peak),
        ];
        if let Some((xs, rate)) = one_group {
            cells.push(sci(xs));
            cells.push(sci(rate));
        }
        summary_rows.push(cells);
        reactions.push(entry);
    }

    let mut json = json!({
        "schema": "actinv-inspect-xs-1",
        "source": source,
        "projectile": index.and_then(|index| index.text("projectile")),
        "energy_structure": structure,
        "group_count": library.ngroups,
        "target": selection.target_label,
        "mt": selection.mt,
        "reaction_count": reactions.len(),
        "group_cross_sections_included": include_groups,
        "reactions": reactions,
    });
    if include_groups {
        json["group_bounds_eV"] = json!(library.bounds);
    }
    if let (Some(spectrum), Some(total)) = (spectrum, total_flux) {
        json["spectrum"] = json!({
            "structure": spectrum.structure,
            "groups": library.ngroups,
            "total_flux_cm2_s": total,
        });
    }

    let tables = if let [position] = selected.as_slice() {
        // One reaction: print its group cross sections (and the flux when a spectrum is given).
        let sigma = library.sigma(*position);
        let mut headers = vec!["Group", "E low (eV)", "E high (eV)", "Sigma (b)"];
        if flux.is_some() {
            headers.push("Flux");
        }
        let rows = (0..library.ngroups)
            .map(|group| {
                let mut cells = vec![
                    group.to_string(),
                    sci(library.bounds[group]),
                    sci(library.bounds[group + 1]),
                    sci(sigma[group]),
                ];
                if let Some(flux) = &flux {
                    cells.push(sci(flux[group]));
                }
                cells
            })
            .collect();
        let row = &library.rows[*position];
        let mut tables = vec![Table {
            title: format!(
                "Group cross sections: {} MT={} ({} groups, {structure})",
                target_name(row.target).unwrap_or_else(|| row.target.to_string()),
                row.mt,
                library.ngroups
            ),
            headers: headers.iter().map(|header| header.to_string()).collect(),
            rows,
        }];
        if let Some((xs, rate)) = last_one_group {
            tables.push(Table {
                title: String::new(),
                headers: vec![
                    "One-group xs (b)".to_string(),
                    "Rate (1/atom/s)".to_string(),
                ],
                rows: vec![vec![sci(xs), sci(rate)]],
            });
        }
        tables
    } else {
        let mut headers = vec![
            "Target",
            "MT",
            "Reaction",
            "Product",
            "Groups",
            "Nonzero",
            "Max xs (b)",
        ];
        if flux.is_some() {
            headers.push("One-group xs (b)");
            headers.push("Rate (1/atom/s)");
        }
        vec![Table {
            title: format!("Reactions: {source} ({structure})"),
            headers: headers.iter().map(|header| header.to_string()).collect(),
            rows: summary_rows,
        }]
    };
    Ok(Output { json, tables })
}

fn library_report(
    rows: &[Row],
    ngroups: usize,
    bounds: &[f64],
    index: Option<&LibraryIndex>,
    source: &str,
) -> Output {
    let structure = structure_name(ngroups, bounds, index);
    let mut targets_in_rows = BTreeSet::new();
    let mut by_mt: BTreeMap<i32, usize> = BTreeMap::new();
    for row in rows {
        targets_in_rows.insert(row.target);
        *by_mt.entry(row.mt).or_default() += 1;
    }
    let mut scalars = Map::new();
    let mut arrays = Map::new();
    if let Some(doc) = index.and_then(|index| index.doc.as_object()) {
        for (key, value) in doc {
            match value {
                Value::Array(items) => {
                    arrays.insert(key.clone(), json!(items.len()));
                }
                Value::Object(_) => {}
                other => {
                    scalars.insert(key.clone(), other.clone());
                }
            }
        }
    }
    let target_names: Option<Vec<String>> = index.map(|index| {
        index
            .targets
            .iter()
            .map(|&(za, liso)| nuclide_name(za, liso))
            .collect()
    });
    let target_count = index.map_or(targets_in_rows.len(), |index| index.targets.len());
    let projectile = index.and_then(|index| index.text("projectile"));
    let reactions_by_mt: Map<String, Value> = by_mt
        .iter()
        .map(|(mt, count)| (mt.to_string(), json!(count)))
        .collect();
    let json = json!({
        "schema": "actinv-inspect-library-1",
        "source": source,
        "index_file": index.map(|_| index_path(source)),
        "targets": target_count,
        "targets_with_reactions": targets_in_rows.len(),
        "reactions": rows.len(),
        "reactions_by_mt": reactions_by_mt,
        "group_count": ngroups,
        "energy_structure": structure,
        "energy_range_eV": [bounds.first(), bounds.last()],
        "projectile": projectile.unwrap_or("neutron"),
        "projectile_declared": projectile.is_some(),
        "index": scalars,
        "index_array_lengths": arrays,
        "target_names": target_names,
    });
    let mut table_rows: Vec<Vec<String>> = vec![
        vec!["source".into(), source.to_string()],
        vec!["targets".into(), target_count.to_string()],
        vec![
            "targets with reactions".into(),
            targets_in_rows.len().to_string(),
        ],
        vec!["reactions".into(), rows.len().to_string()],
        vec!["groups".into(), ngroups.to_string()],
        vec!["energy structure".into(), structure.clone()],
        vec![
            "energy range (eV)".into(),
            format!(
                "{} .. {}",
                bounds.first().map_or(String::new(), |value| sci(*value)),
                bounds.last().map_or(String::new(), |value| sci(*value))
            ),
        ],
        vec![
            "projectile".into(),
            match projectile {
                Some(name) => name.to_string(),
                None => "neutron (no index)".to_string(),
            },
        ],
    ];
    for (mt, count) in &by_mt {
        table_rows.push(vec![
            format!("MT {mt} {}", mt_label(*mt).unwrap_or(""))
                .trim_end()
                .to_string(),
            format!("{count} reactions"),
        ]);
    }
    for (key, value) in &scalars {
        table_rows.push(vec![
            format!("index.{key}"),
            match value {
                Value::String(text) => text.clone(),
                other => other.to_string(),
            },
        ]);
    }
    Output {
        json,
        tables: vec![Table {
            title: "Activation library".to_string(),
            headers: vec!["Field".to_string(), "Value".to_string()],
            rows: table_rows,
        }],
    }
}

// ---- fission yields ----

fn product_entry(key: NuclideKey, value: f64, uncertainty: f64) -> Value {
    json!({
        "nuclide": nuclide_name(key.0, key.1),
        "za": key.0,
        "liso": key.1,
        "yield": value,
        "uncertainty": uncertainty,
    })
}

fn yields_report(
    parsed: &[(String, FissionYields)],
    parent: Option<(i32, i32)>,
    energy: Option<f64>,
    cumulative: bool,
) -> Result<Output, String> {
    let kind = if cumulative {
        "cumulative"
    } else {
        "independent"
    };
    let selected: Vec<&(String, FissionYields)> = parsed
        .iter()
        .filter(|(_, yields)| parent.is_none_or(|wanted| yields.parent == wanted))
        .collect();
    if selected.is_empty() {
        let found: Vec<String> = parsed
            .iter()
            .map(|(_, yields)| nuclide_name(yields.parent.0, yields.parent.1))
            .collect();
        return Err(format!(
            "no fission-yield file has parent {}; found: {}",
            parent.map_or("(any)".to_string(), |(za, liso)| nuclide_name(za, liso)),
            found.join(", ")
        ));
    }
    let mut parents = Vec::with_capacity(selected.len());
    let mut tables = Vec::new();
    for (source, yields) in selected {
        let name = nuclide_name(yields.parent.0, yields.parent.1);
        let tabulated = if cumulative {
            &yields.cumulative
        } else {
            &yields.independent
        };
        if tabulated.is_empty() {
            return Err(format!(
                "{source}: evaluation has no {kind} yields{}",
                if cumulative { " (MT=459)" } else { "" }
            ));
        }
        let mut entry = json!({
            "source": source,
            "parent": name,
            "parent_za": yields.parent.0,
            "parent_liso": yields.parent.1,
            "awr": yields.awr,
        });
        match energy {
            Some(energy_ev) => {
                // Cumulative tables share the independent tables' energy grid and interpolation rule.
                let view = FissionYields {
                    parent: yields.parent,
                    awr: yields.awr,
                    independent: tabulated.clone(),
                    cumulative: Vec::new(),
                };
                let effective = view.effective(energy_ev)?;
                let products: Vec<Value> = effective
                    .products
                    .iter()
                    .map(|(&key, &value)| {
                        let uncertainty = effective.uncertainties.get(&key).copied().unwrap_or(0.0);
                        product_entry(key, value, uncertainty)
                    })
                    .collect();
                entry["requested_energy_eV"] = json!(effective.requested_energy_ev);
                entry["lower_energy_eV"] = json!(effective.lower_energy_ev);
                entry["upper_energy_eV"] = json!(effective.upper_energy_ev);
                entry["upper_weight"] = json!(effective.upper_weight);
                entry["clamped"] = json!(effective.clamped);
                entry["product_count"] = json!(effective.products.len());
                entry["sum"] = json!(effective.sum);
                entry["products"] = json!(products);
                tables.push(Table {
                    title: format!(
                        "{name} {kind} fission yields at {} eV (tabulated {} .. {} eV, weight {:.6}{})",
                        sci(energy_ev),
                        sci(effective.lower_energy_ev),
                        sci(effective.upper_energy_ev),
                        effective.upper_weight,
                        if effective.clamped { ", clamped" } else { "" }
                    ),
                    headers: ["Product", "Yield", "Uncertainty"]
                        .iter()
                        .map(|header| header.to_string())
                        .collect(),
                    rows: effective
                        .products
                        .iter()
                        .map(|(&key, &value)| {
                            let uncertainty =
                                effective.uncertainties.get(&key).copied().unwrap_or(0.0);
                            vec![nuclide_name(key.0, key.1), sci(value), sci(uncertainty)]
                        })
                        .collect(),
                });
            }
            None => {
                let energies: Vec<Value> = tabulated
                    .iter()
                    .map(|table| {
                        let products: Vec<Value> = table
                            .products
                            .iter()
                            .map(|(&key, value)| product_entry(key, value.value, value.uncertainty))
                            .collect();
                        json!({
                            "energy_eV": table.energy_ev,
                            "product_count": table.products.len(),
                            "sum": table.sum,
                            "products": products,
                        })
                    })
                    .collect();
                entry["energies"] = json!(energies);
                // One table, one yield column per tabulated incident energy.
                let keys: BTreeSet<NuclideKey> = tabulated
                    .iter()
                    .flat_map(|table| table.products.keys().copied())
                    .collect();
                let mut headers = vec!["Product".to_string()];
                headers.extend(
                    tabulated
                        .iter()
                        .map(|table| format!("Y @ {} eV", sci(table.energy_ev))),
                );
                let mut rows: Vec<Vec<String>> = keys
                    .iter()
                    .map(|key| {
                        let mut cells = vec![nuclide_name(key.0, key.1)];
                        cells.extend(tabulated.iter().map(|table| {
                            table
                                .products
                                .get(key)
                                .map_or("-".to_string(), |value| sci(value.value))
                        }));
                        cells
                    })
                    .collect();
                let mut sums = vec!["sum".to_string()];
                sums.extend(tabulated.iter().map(|table| sci(table.sum)));
                rows.push(sums);
                tables.push(Table {
                    title: format!("{name} {kind} fission yields"),
                    headers,
                    rows,
                });
            }
        }
        parents.push(entry);
    }
    Ok(Output {
        json: json!({
            "schema": "actinv-inspect-yields-1",
            "kind": kind,
            "parents": parents,
        }),
        tables,
    })
}

// ---- group structures ----

fn groups_report(structure: &GroupStructure) -> Output {
    let json = json!({
        "schema": "actinv-inspect-groups-1",
        "name": structure.name,
        "group_count": structure.groups(),
        "boundary_sha256": structure.hash(),
        "boundaries_eV": structure.boundaries_ev,
    });
    let rows = structure
        .boundaries_ev
        .windows(2)
        .enumerate()
        .map(|(group, pair)| vec![group.to_string(), sci(pair[0]), sci(pair[1])])
        .collect();
    Output {
        json,
        tables: vec![Table {
            title: format!(
                "{}: {} groups, ascending energy",
                structure.name,
                structure.groups()
            ),
            headers: ["Group", "E low (eV)", "E high (eV)"]
                .iter()
                .map(|header| header.to_string())
                .collect(),
            rows,
        }],
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use actinv_data::decay::{DiscreteRadiation, Mode, Spectrum as DecaySpectrum};
    use actinv_data::fission::{YieldTable, YieldValue};
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicUsize, Ordering};

    struct Scratch(PathBuf);

    impl Scratch {
        fn new() -> Self {
            static NEXT: AtomicUsize = AtomicUsize::new(0);
            let path = std::env::temp_dir().join(format!(
                "actinv-inspect-{}-{}",
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

    fn args(items: &[&str]) -> Vec<String> {
        items.iter().map(|item| item.to_string()).collect()
    }

    fn line(energy: f64, intensity: f64) -> DiscreteRadiation {
        DiscreteRadiation {
            energy,
            d_energy: 0.0,
            rtyp: 0.0,
            transition_type: 0.0,
            intensity,
            d_intensity: 0.0,
            pair_intensity: 0.0,
            d_pair_intensity: 0.0,
            conversion_total: 0.0,
            d_conversion_total: 0.0,
            conversion_k: 0.0,
            d_conversion_k: 0.0,
            conversion_l: 0.0,
            d_conversion_l: 0.0,
        }
    }

    fn mode(rtyp: f64, rfs: f64, br: f64) -> Mode {
        Mode {
            rtyp,
            rfs,
            q: 2.8e6,
            dq: 0.0,
            br,
            dbr: 0.0,
        }
    }

    fn co60() -> Nuclide {
        Nuclide {
            mat: 2725,
            za: 27060,
            awr: 59.0,
            liso: 0,
            nst: 0,
            half_life: 1.66e8,
            d_half_life: 1.0e4,
            energies: vec![9.6e4, 0.0, 2.5e6, 0.0, 0.0, 0.0],
            modes: vec![mode(1.0, 0.0, 1.0)],
            spectra: vec![DecaySpectrum {
                styp: 0.0,
                lcon: 0,
                lcov: 0,
                fd: 1.0,
                d_fd: 0.0,
                average_energy: 2.5e6,
                d_average_energy: 0.0,
                fc: 0.0,
                d_fc: 0.0,
                discrete: vec![line(1.1732e6, 0.9985), line(1.3325e6, 0.9998)],
                continuous: None,
            }],
        }
    }

    fn u238() -> Nuclide {
        Nuclide {
            mat: 9237,
            za: 92238,
            awr: 236.0,
            liso: 0,
            nst: 0,
            half_life: 1.41e17,
            d_half_life: 0.0,
            energies: vec![0.0, 0.0, 1.0e3, 0.0, 4.2e6, 0.0],
            modes: vec![mode(4.0, 0.0, 1.0), mode(6.0, 0.0, 5.0e-7)],
            spectra: Vec::new(),
        }
    }

    fn fe57_stable() -> Nuclide {
        Nuclide {
            mat: 2631,
            za: 26057,
            awr: 56.0,
            liso: 0,
            nst: 1,
            half_life: 0.0,
            d_half_life: 0.0,
            energies: Vec::new(),
            modes: Vec::new(),
            spectra: Vec::new(),
        }
    }

    fn decay_table() -> HashMap<(i32, i32), Nuclide> {
        [co60(), u238(), fe57_stable()]
            .into_iter()
            .map(|nuclide| ((nuclide.za, nuclide.liso), nuclide))
            .collect()
    }

    #[test]
    fn request_parsing_accepts_each_subcommand() {
        let request = parse_request(&args(&[
            "decay", "d.endf", "Co60", "U238", "--table", "--out", "o.json",
        ]))
        .unwrap();
        assert_eq!(
            request.command,
            Command::Decay {
                path: "d.endf".into(),
                nuclides: vec!["Co60".into(), "U238".into()],
            }
        );
        assert!(request.table);
        assert_eq!(request.out.as_deref(), Some("o.json"));

        let request = parse_request(&args(&[
            "xs",
            "lib.npz",
            "--target",
            "Fe56",
            "--mt",
            "102",
            "--spectrum",
            "s.json",
        ]))
        .unwrap();
        assert_eq!(
            request.command,
            Command::Xs {
                path: "lib.npz".into(),
                target: Some("Fe56".into()),
                mt: Some(102),
                spectrum: Some("s.json".into()),
            }
        );
        assert!(!request.table);

        let request = parse_request(&args(&[
            "yields",
            "u235.endf",
            "--energy",
            "1.4e7",
            "--cumulative",
        ]))
        .unwrap();
        assert_eq!(
            request.command,
            Command::Yields {
                paths: vec!["u235.endf".into()],
                parent: None,
                energy: Some(1.4e7),
                cumulative: true,
            }
        );
        assert_eq!(
            parse_request(&args(&["groups", "fispact-709"]))
                .unwrap()
                .command,
            Command::Groups {
                name: "fispact-709".into()
            }
        );
        assert_eq!(
            parse_request(&args(&["library", "lib.npz"]))
                .unwrap()
                .command,
            Command::Library {
                path: "lib.npz".into()
            }
        );
    }

    #[test]
    fn request_parsing_rejects_malformed_command_lines() {
        for bad in [
            vec![],
            vec!["bogus"],
            vec!["decay"],
            vec!["decay", "d.endf", "--bogus"],
            vec!["xs"],
            vec!["xs", "a.npz", "b.npz"],
            vec!["xs", "a.npz", "--mt", "x"],
            vec!["xs", "a.npz", "--mt"],
            vec!["xs", "a.npz", "--mt", "1", "--mt", "2"],
            vec!["yields", "y.endf", "--energy", "-1"],
            vec!["yields", "y.endf", "--energy", "nan"],
            vec!["yields"],
            vec!["groups"],
            vec!["library", "a.npz", "b.npz"],
        ] {
            assert!(parse_request(&args(&bad)).is_err(), "{bad:?}");
        }
    }

    #[test]
    fn table_rendering_is_fixed_width() {
        let table = Table {
            title: "T".into(),
            headers: vec!["Name".into(), "Value".into()],
            rows: vec![
                vec!["a".into(), "1".into()],
                vec!["longer".into(), "22".into()],
            ],
        };
        assert_eq!(
            render_table(&table),
            "T\nName    Value\n------  -----\na       1\nlonger  22"
        );
        let two = render_tables(&[table.clone(), table]);
        assert_eq!(two.matches("Name").count(), 2);
        assert!(two.contains("22\n\nT\n"));
    }

    #[test]
    fn decay_report_has_modes_daughters_and_gamma_totals() {
        let output = decay_report(&decay_table(), &args(&["Co60"]), "decay.endf").unwrap();
        let json = &output.json;
        assert_eq!(json["schema"], "actinv-inspect-decay-1");
        assert_eq!(json["count"], 1);
        let co = &json["nuclides"][0];
        assert_eq!(co["nuclide"], "Co60");
        assert_eq!(co["stable"], false);
        assert_eq!(co["half_life_s"], 1.66e8);
        assert_eq!(co["modes"][0]["type"], "beta-");
        assert_eq!(co["modes"][0]["branching_ratio"], 1.0);
        assert_eq!(co["modes"][0]["daughter"], "Ni60");
        assert_eq!(co["modes"][0]["daughter_za"], 28060);
        assert_eq!(co["mean_energy_eV"]["beta"], 9.6e4);
        assert_eq!(co["mean_energy_eV"]["gamma"], 2.5e6);
        assert_eq!(co["gamma"]["discrete_lines"], 2);
        let total = co["gamma"]["total_energy_eV_per_decay"].as_f64().unwrap();
        let expected = 1.1732e6 * 0.9985 + 1.3325e6 * 0.9998;
        assert!((total - expected).abs() < 1e-6 * expected);
        assert_eq!(co["spectra"][0]["type"], "gamma");

        let table = render_tables(&output.tables);
        assert!(table.starts_with("Decay data: decay.endf\nNuclide"));
        assert!(table.contains("Co60"));
        assert!(table.contains("beta- 100.0000%"));
    }

    #[test]
    fn decay_report_handles_all_nuclides_stable_and_fission_modes() {
        let output = decay_report(&decay_table(), &[], "decay.endf").unwrap();
        assert_eq!(output.json["count"], 3);
        let names: Vec<&str> = output.json["nuclides"]
            .as_array()
            .unwrap()
            .iter()
            .map(|entry| entry["nuclide"].as_str().unwrap())
            .collect();
        assert_eq!(names, ["Fe57", "Co60", "U238"]);
        let fe = &output.json["nuclides"][0];
        assert_eq!(fe["stable"], true);
        assert!(fe["half_life_s"].is_null());
        let u = &output.json["nuclides"][2];
        assert_eq!(u["modes"][0]["type"], "alpha");
        assert_eq!(u["modes"][0]["daughter"], "Th234");
        assert_eq!(u["modes"][1]["type"], "SF");
        assert!(u["modes"][1]["daughter"].is_null());
        assert!(render_tables(&output.tables).contains("stable"));
    }

    #[test]
    fn decay_report_rejects_unknown_and_element_names() {
        let table = decay_table();
        let missing = decay_report(&table, &args(&["Mn54"]), "d").err().unwrap();
        assert!(missing.contains("Mn54") && missing.contains("not in d"));
        let element = decay_report(&table, &args(&["Fe"]), "d").err().unwrap();
        assert!(element.contains("explicit nuclide"));
    }

    #[test]
    fn multi_particle_decay_modes_resolve_daughters() {
        // 1.5: beta- then neutron emission.
        assert_eq!(mode_label(1.5), "beta- + n");
        assert_eq!(mode_daughter(36090, 1.5, 0.0), Some((37089, 0)));
        assert_eq!(mode_daughter(73182, 3.0, 1.0), Some((73182, 1)));
        assert_eq!(mode_daughter(98252, 6.0, 0.0), None);
    }

    fn toy_library() -> Library {
        // Target 0 = Fe56 (n,g) and (n,2n); target 1 = Ni58 (n,p).
        Library {
            rows: vec![
                Row {
                    target: 0,
                    mt: 102,
                    zap: 26057,
                    lfs: 0,
                    lmf: 3,
                },
                Row {
                    target: 0,
                    mt: 16,
                    zap: 26055,
                    lfs: 0,
                    lmf: 3,
                },
                Row {
                    target: 1,
                    mt: 103,
                    zap: 27058,
                    lfs: 1,
                    lmf: 3,
                },
            ],
            sig: vec![2.0, 4.0, 0.0, 1.0, 0.5, 0.0],
            ngroups: 2,
            bounds: vec![1.0, 10.0, 100.0],
        }
    }

    fn toy_index() -> LibraryIndex {
        LibraryIndex::from_value(json!({
            "schema": "actinv-library-index-1",
            "projectile": "neutron",
            "groups": "toy",
            "temperature_K": 293.6,
            "sha256_npz": "abc",
            "targets": [{"za": 26056, "liso": 0}, {"za": 28058, "liso": 0}],
        }))
    }

    fn toy_spectrum() -> Spectrum {
        serde_json::from_value(json!({
            "structure": "custom",
            "boundaries_eV": [1.0, 10.0, 100.0],
            "flux_per_group": [1.0, 3.0],
        }))
        .unwrap()
    }

    #[test]
    fn xs_report_lists_reactions_and_collapses_a_spectrum() {
        let library = toy_library();
        let index = toy_index();
        let selection = XsSelection {
            target: Some(0),
            target_label: Some("Fe56".into()),
            mt: Some(102),
        };
        let output = xs_report(
            &library,
            Some(&index),
            "lib.npz",
            &selection,
            Some(&toy_spectrum()),
        )
        .unwrap();
        let json = &output.json;
        assert_eq!(json["schema"], "actinv-inspect-xs-1");
        assert_eq!(json["energy_structure"], "toy");
        assert_eq!(json["projectile"], "neutron");
        assert_eq!(json["reaction_count"], 1);
        assert_eq!(json["group_cross_sections_included"], true);
        assert_eq!(json["group_bounds_eV"], json!([1.0, 10.0, 100.0]));
        let reaction = &json["reactions"][0];
        assert_eq!(reaction["target"], "Fe56");
        assert_eq!(reaction["mt"], 102);
        assert_eq!(reaction["label"], "(x,g)");
        assert_eq!(reaction["product"], "Fe57");
        assert_eq!(reaction["group_count"], 2);
        assert_eq!(reaction["cross_section_b"], json!([2.0, 4.0]));
        // (2*1 + 4*3) / 4 = 3.5 b; total flux 4; rate = 3.5e-24 * 4.
        assert_eq!(reaction["one_group_xs_b"], 3.5);
        let rate = reaction["reaction_rate_per_target_atom_per_s"]
            .as_f64()
            .unwrap();
        assert!((rate - 1.4e-23).abs() < 1e-36);
        assert_eq!(json["spectrum"]["total_flux_cm2_s"], 4.0);

        let table = render_tables(&output.tables);
        assert!(table.starts_with("Group cross sections: Fe56 MT=102 (2 groups, toy)\nGroup"));
        assert!(table.contains("Flux"));
        assert!(table.contains("One-group xs (b)"));
        assert!(table.contains("3.500000e0"));
    }

    #[test]
    fn xs_report_without_target_summarizes_without_group_values() {
        let library = toy_library();
        let index = toy_index();
        let selection = XsSelection {
            target: None,
            target_label: None,
            mt: None,
        };
        let output = xs_report(&library, Some(&index), "lib.npz", &selection, None).unwrap();
        let json = &output.json;
        assert_eq!(json["reaction_count"], 3);
        assert_eq!(json["group_cross_sections_included"], false);
        assert!(json["reactions"][0].get("cross_section_b").is_none());
        assert!(json["reactions"][0].get("one_group_xs_b").is_none());
        assert!(json.get("spectrum").is_none());
        assert_eq!(json["reactions"][2]["target"], "Ni58");
        assert_eq!(json["reactions"][2]["product"], "Co58m1");
        assert_eq!(json["reactions"][1]["nonzero_groups"], 1);
        assert_eq!(json["reactions"][1]["max_cross_section_b"], 1.0);
        let table = render_tables(&output.tables);
        assert!(table.starts_with("Reactions: lib.npz (toy)\nTarget"));
        assert!(table.contains("Ni58"));
        assert!(!table.contains("Rate"));
    }

    #[test]
    fn xs_report_rejects_empty_selection_and_bad_spectra() {
        let library = toy_library();
        let none = XsSelection {
            target: Some(0),
            target_label: Some("Fe56".into()),
            mt: Some(103),
        };
        let error = xs_report(&library, None, "lib.npz", &none, None)
            .err()
            .unwrap();
        assert!(error.contains("no reactions match target Fe56 and MT 103"));

        let any = XsSelection {
            target: None,
            target_label: None,
            mt: None,
        };
        let short: Spectrum = serde_json::from_value(json!({
            "structure": "custom",
            "boundaries_eV": [1.0, 10.0],
            "flux_per_group": [1.0],
        }))
        .unwrap();
        let error = xs_report(&library, None, "lib.npz", &any, Some(&short))
            .err()
            .unwrap();
        assert!(error.contains("1 group fluxes"));
        let moved: Spectrum = serde_json::from_value(json!({
            "structure": "custom",
            "boundaries_eV": [1.0, 10.0, 200.0],
            "flux_per_group": [1.0, 1.0],
        }))
        .unwrap();
        let error = xs_report(&library, None, "lib.npz", &any, Some(&moved))
            .err()
            .unwrap();
        assert!(error.contains("boundaries_eV differ"));
    }

    #[test]
    fn targets_resolve_by_name_or_index() {
        let targets = [(26056, 0), (28058, 0), (73182, 2)];
        assert_eq!(resolve_target("Fe56", Some(&targets)).unwrap(), 0);
        assert_eq!(resolve_target("Ta182m2", Some(&targets)).unwrap(), 2);
        assert_eq!(resolve_target("1", Some(&targets)).unwrap(), 1);
        assert!(resolve_target("3", Some(&targets)).is_err());
        assert!(resolve_target("Co59", Some(&targets))
            .unwrap_err()
            .contains("not in the library"));
        assert!(resolve_target("Fe56", None)
            .unwrap_err()
            .contains("library index"));
        assert!(resolve_target("Fe", Some(&targets)).is_err());
        assert_eq!(resolve_target("7", None).unwrap(), 7);
    }

    #[test]
    fn structure_name_prefers_the_index_then_falls_back() {
        let index = toy_index();
        assert_eq!(structure_name(2, &[1.0, 10.0, 100.0], Some(&index)), "toy");
        assert_eq!(structure_name(2, &[1.0, 10.0, 100.0], None), "custom");
        let named = GroupStructure::fispact_709().unwrap();
        assert_eq!(
            structure_name(709, &named.boundaries_ev, None),
            "fispact-709"
        );
    }

    #[test]
    fn library_report_summarizes_rows_and_index_fields() {
        let library = toy_library();
        let index = toy_index();
        let output = library_report(
            &library.rows,
            library.ngroups,
            &library.bounds,
            Some(&index),
            "lib.npz",
        );
        let json = &output.json;
        assert_eq!(json["schema"], "actinv-inspect-library-1");
        assert_eq!(json["index_file"], "lib_index.json");
        assert_eq!(json["targets"], 2);
        assert_eq!(json["targets_with_reactions"], 2);
        assert_eq!(json["reactions"], 3);
        assert_eq!(json["reactions_by_mt"]["102"], 1);
        assert_eq!(json["group_count"], 2);
        assert_eq!(json["energy_structure"], "toy");
        assert_eq!(json["energy_range_eV"], json!([1.0, 100.0]));
        assert_eq!(json["projectile"], "neutron");
        assert_eq!(json["projectile_declared"], true);
        assert_eq!(json["index"]["sha256_npz"], "abc");
        assert_eq!(json["index"]["temperature_K"], 293.6);
        assert_eq!(json["index_array_lengths"]["targets"], 2);
        assert_eq!(json["target_names"], json!(["Fe56", "Ni58"]));
        let table = render_tables(&output.tables);
        assert!(table.starts_with("Activation library\nField"));
        assert!(table.contains("index.sha256_npz"));
        assert!(table.contains("MT 102 (x,g)"));

        let bare = library_report(&library.rows, 2, &library.bounds, None, "lib.npz");
        assert_eq!(bare.json["targets"], 2);
        assert_eq!(bare.json["projectile_declared"], false);
        assert!(bare.json["index_file"].is_null());
        assert_eq!(bare.json["energy_structure"], "custom");
    }

    fn yield_table(energy_ev: f64, products: &[(NuclideKey, f64, f64)]) -> YieldTable {
        let products: BTreeMap<_, _> = products
            .iter()
            .map(|&(key, value, uncertainty)| (key, YieldValue { value, uncertainty }))
            .collect();
        YieldTable {
            energy_ev,
            sum: products.values().map(|value| value.value).sum(),
            products,
        }
    }

    fn toy_yields() -> FissionYields {
        FissionYields {
            parent: (92235, 0),
            awr: 233.0,
            independent: vec![
                yield_table(
                    0.0253,
                    &[((53135, 0), 0.02, 0.002), ((54135, 0), 0.01, 0.001)],
                ),
                yield_table(2.0e6, &[((53135, 0), 0.04, 0.004)]),
            ],
            cumulative: vec![
                yield_table(0.0253, &[((53135, 0), 0.06, 0.006)]),
                yield_table(2.0e6, &[((53135, 0), 0.08, 0.008)]),
            ],
        }
    }

    #[test]
    fn yields_report_lists_tables_and_interpolates() {
        let parsed = vec![("u235.endf".to_string(), toy_yields())];
        let all = yields_report(&parsed, None, None, false).unwrap();
        assert_eq!(all.json["schema"], "actinv-inspect-yields-1");
        assert_eq!(all.json["kind"], "independent");
        let parent = &all.json["parents"][0];
        assert_eq!(parent["parent"], "U235");
        assert_eq!(parent["energies"][0]["product_count"], 2);
        assert_eq!(parent["energies"][1]["products"][0]["nuclide"], "I135");
        assert_eq!(parent["energies"][1]["products"][0]["yield"], 0.04);
        let table = render_tables(&all.tables);
        assert!(table.starts_with("U235 independent fission yields\nProduct"));
        assert!(table.contains("Xe135"));
        assert!(table.contains("-"));

        let at = yields_report(&parsed, Some((92235, 0)), Some(1.0e6), false).unwrap();
        let parent = &at.json["parents"][0];
        assert_eq!(parent["clamped"], false);
        assert_eq!(parent["lower_energy_eV"], 0.0253);
        assert_eq!(parent["upper_energy_eV"], 2.0e6);
        assert_eq!(parent["product_count"], 2);
        let iodine = parent["products"]
            .as_array()
            .unwrap()
            .iter()
            .find(|entry| entry["nuclide"] == "I135")
            .unwrap();
        let weight = (1.0e6 - 0.0253) / (2.0e6 - 0.0253);
        let expected = 0.02 * (1.0 - weight) + 0.04 * weight;
        assert!((iodine["yield"].as_f64().unwrap() - expected).abs() < 1e-15);
        assert!(render_tables(&at.tables).contains("fission yields at 1.000000e6 eV"));
    }

    #[test]
    fn cumulative_yields_use_mt459_and_require_it() {
        let parsed = vec![("u235.endf".to_string(), toy_yields())];
        let cumulative = yields_report(&parsed, None, Some(2.0e6), true).unwrap();
        assert_eq!(cumulative.json["kind"], "cumulative");
        let parent = &cumulative.json["parents"][0];
        assert_eq!(parent["products"][0]["yield"], 0.08);
        assert_eq!(parent["sum"], 0.08);

        let mut bare = toy_yields();
        bare.cumulative.clear();
        let parsed = vec![("bare.endf".to_string(), bare)];
        let error = yields_report(&parsed, None, None, true).err().unwrap();
        assert!(error.contains("no cumulative yields (MT=459)"));
        assert!(yields_report(&parsed, None, None, false).is_ok());
    }

    #[test]
    fn yields_parent_filter_names_what_was_found() {
        let parsed = vec![("u235.endf".to_string(), toy_yields())];
        let error = yields_report(&parsed, Some((94239, 0)), None, false)
            .err()
            .unwrap();
        assert!(error.contains("Pu239") && error.contains("U235"));
    }

    #[test]
    fn groups_report_lists_boundaries() {
        let structure = GroupStructure {
            name: "toy".into(),
            boundaries_ev: vec![1.0, 10.0, 100.0],
        };
        let output = groups_report(&structure);
        assert_eq!(output.json["schema"], "actinv-inspect-groups-1");
        assert_eq!(output.json["name"], "toy");
        assert_eq!(output.json["group_count"], 2);
        assert_eq!(output.json["boundaries_eV"], json!([1.0, 10.0, 100.0]));
        assert_eq!(output.json["boundary_sha256"], structure.hash());
        let table = render_tables(&output.tables);
        assert!(table.starts_with("toy: 2 groups, ascending energy\nGroup"));
        assert_eq!(table.lines().count(), 5);

        let named = execute(&Command::Groups {
            name: "fispact-162".into(),
        })
        .unwrap();
        assert_eq!(named.json["group_count"], 162);
    }

    #[test]
    fn library_files_round_trip_through_execute() {
        let scratch = Scratch::new();
        let npz = scratch.file("toy.npz");
        library::write_npz(&npz, &toy_library()).unwrap();
        std::fs::write(
            scratch.file("toy_index.json"),
            json!({
                "projectile": "neutron",
                "groups": "toy",
                "targets": [{"za": 26056, "liso": 0}, {"za": 28058, "liso": 0}],
            })
            .to_string(),
        )
        .unwrap();
        let spectrum = scratch.file("spectrum.json");
        std::fs::write(
            &spectrum,
            json!({
                "spectrum": {
                    "structure": "custom",
                    "boundaries_eV": [1.0, 10.0, 100.0],
                    "flux_per_group": [1.0, 3.0],
                }
            })
            .to_string(),
        )
        .unwrap();

        let summary = execute(&Command::Library { path: npz.clone() }).unwrap();
        assert_eq!(summary.json["reactions"], 3);
        assert_eq!(summary.json["targets"], 2);
        assert_eq!(summary.json["energy_structure"], "toy");

        let xs = execute(&Command::Xs {
            path: npz.clone(),
            target: Some("Ni58".into()),
            mt: None,
            spectrum: Some(spectrum.clone()),
        })
        .unwrap();
        assert_eq!(xs.json["reaction_count"], 1);
        assert_eq!(xs.json["reactions"][0]["target"], "Ni58");
        assert_eq!(xs.json["reactions"][0]["one_group_xs_b"], 0.125);

        let all = execute(&Command::Xs {
            path: npz.clone(),
            target: None,
            mt: Some(16),
            spectrum: Some(spectrum),
        })
        .unwrap();
        assert_eq!(all.json["reaction_count"], 1);
        assert_eq!(all.json["reactions"][0]["one_group_xs_b"], 0.75);

        let missing = execute(&Command::Xs {
            path: npz,
            target: Some("Co59".into()),
            mt: None,
            spectrum: None,
        })
        .err()
        .unwrap();
        assert!(missing.contains("not in the library"));
    }
}
