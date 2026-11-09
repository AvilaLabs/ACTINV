//! Activation-library packs for hosts that cannot hold a whole library (the browser).
//!
//! [`build`] splits a library into one small NPZ per target plus a graph naming, for each target,
//! the nuclides its reactions produce, and splits the decay data into one record per state, with
//! each unstable state's daughters in the graph. [`reachable`] follows that graph from a
//! material's nuclides for a set number of reactions (decays between them are free), so a host
//! knows which files to fetch from the graph alone, and [`assemble`] joins the chosen targets back
//! into one NPZ and index that any run accepts. [`decay_text`] joins the chosen decay records. A run on an assembled library
//! differs from one on the whole library only by the reactions of targets left out; the same
//! assembly runs natively, so that difference can be measured.

use crate::run::{run_with_inputs, RunResult};
use crate::spec::Spec;
use actinv_data::composition::{self, MaterialKey};
use actinv_data::decay::Nuclide;
use actinv_data::library::{self, Library, Row};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, HashMap, VecDeque};

pub const SCHEMA: &str = "actinv-library-pack-1";

/// A nuclide state: (ZA, isomeric state).
pub type State = (i32, i32);

/// The depths, in reactions followed, that a [`Deepening`] run tries in turn. Two reactions
/// reproduce most problems, but high thermal fluence and actinide impurities carry activity a dozen
/// or more captures from the material (P196: tungsten at 3e23 n/cm² still differs at twelve).
pub const ROUNDS: [usize; 11] = [2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64];

/// Two rounds agree when, at every step, total activity, heat and photon emission change by at
/// most `SETTLED_TOTAL` (relative), every nuclide with 0.1% of the activity by at most
/// `SETTLED_NUCLIDE`, and the atoms that moved between nuclides are at most `SETTLED_ATOMS` of the
/// total. The atoms catch a depth that burns material into stable nuclides whose own reactions
/// are still left out, where activity alone would not yet change. Totals are compared relatively
/// at any size, so a trace that still moves keeps the run going deeper: with a floor under the
/// totals, water at 1e15 n/cm²/s thermal for ten years stopped at four reactions with a late step's
/// photon emission wrong (P196 Amendment A).
pub const SETTLED_TOTAL: f64 = 1e-6;
pub const SETTLED_NUCLIDE: f64 = 1e-5;
pub const SETTLED_ATOMS: f64 = 1e-6;

/// Agreements in a row a [`Deepening`] run needs: one can be a plateau (P196: water's trace photon
/// emitters agreed at three and four reactions and needed six).
pub const SETTLED_ROUNDS: usize = 2;

/// Where a packed run's assembled library lives among its in-memory inputs.
pub const PACKED_LIBRARY: &str = "pack.npz";

/// One target of a pack: its entry in the source index, the file holding its rows, and the
/// nuclides (ZA, isomeric state) its reactions produce.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PackTarget {
    pub entry: Value,
    pub file: String,
    pub sha256: String,
    pub rows: usize,
    pub products: Vec<(i32, i32)>,
    /// The target has a total-fission row, whose products come from fission yields.
    pub fission: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PackGraph {
    pub schema: String,
    /// SHA-256 of the source NPZ and of its index.
    pub library_sha256: String,
    pub index_sha256: String,
    /// The source index without `targets`, `n_rows` and `sha256_npz`.
    pub index: Value,
    pub targets: Vec<PackTarget>,
    /// The decay sublibraries, in the order a run merges them: primary, then fallback.
    pub decay: Vec<PackDecaySource>,
    /// The library's and decay sublibraries' names, which a problem must use to run from the pack.
    pub names: PackNames,
    /// Each unstable state's daughters in the merged decay data, as the chain forms them.
    pub daughters: Vec<(State, Vec<State>)>,
}

impl PackGraph {
    /// Every file of the pack, target and decay record, with its SHA-256.
    pub fn file_hashes(&self) -> HashMap<&str, &str> {
        let targets = self
            .targets
            .iter()
            .map(|target| (target.file.as_str(), target.sha256.as_str()));
        let states = self.decay.iter().flat_map(|source| {
            source
                .states
                .iter()
                .map(|state| (state.file.as_str(), state.sha256.as_str()))
        });
        targets.chain(states).collect()
    }
}

/// What a problem may call the pack's sources: a catalog id (`catalog:ID`) or a path ending in
/// the source's file name. Decay names are in merge order.
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct PackNames {
    pub library: SourceName,
    pub decay: Vec<SourceName>,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct SourceName {
    pub id: String,
    pub file: String,
}

impl SourceName {
    fn matches(&self, path: &str) -> bool {
        path.strip_prefix("catalog:") == Some(self.id.as_str())
            || std::path::Path::new(path)
                .file_name()
                .is_some_and(|name| name == self.file.as_str())
    }
}

/// One decay sublibrary of a pack: its SHA-256 and, per state, the file holding its record.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PackDecaySource {
    pub sha256: String,
    pub states: Vec<PackDecayState>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct PackDecayState {
    pub za: i32,
    pub liso: i32,
    pub file: String,
    pub sha256: String,
}

/// The SHA-256 a pack's graph records for a file's bytes.
pub fn sha256_hex(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn target_key(entry: &Value) -> Result<(i32, i32), String> {
    let field = |name: &str| {
        entry[name]
            .as_i64()
            .and_then(|value| i32::try_from(value).ok())
            .ok_or_else(|| format!("index target has no integer {name}"))
    };
    Ok((field("za")?, field("liso")?))
}

/// A pack's files: each one's name and bytes.
pub type PackFiles = Vec<(String, Vec<u8>)>;

/// Split a library into per-target NPZ files and its decay sublibraries (primary first) into
/// per-state records. Returns the graph and the files.
pub fn build(
    npz: &[u8],
    index_text: &str,
    decay_texts: &[&str],
    names: PackNames,
) -> Result<(PackGraph, PackFiles), String> {
    if names.decay.len() != decay_texts.len() {
        return Err("a pack needs one name per decay sublibrary".into());
    }
    let source = library::read_npz_bytes(npz)?;
    let index: Value = serde_json::from_str(index_text).map_err(|e| e.to_string())?;
    let entries = index["targets"]
        .as_array()
        .ok_or("library index has no targets")?;
    let mut by_target: Vec<Vec<usize>> = vec![Vec::new(); entries.len()];
    for (position, row) in source.rows.iter().enumerate() {
        by_target
            .get_mut(row.target)
            .ok_or("library row names a target the index lacks")?
            .push(position);
    }
    let mut targets = Vec::with_capacity(entries.len());
    let mut files = Vec::with_capacity(entries.len());
    for (target, entry) in entries.iter().enumerate() {
        let (za, liso) = target_key(entry)?;
        let rows = &by_target[target];
        let mut part = Library {
            rows: Vec::with_capacity(rows.len()),
            sig: Vec::with_capacity(rows.len() * source.ngroups),
            ngroups: source.ngroups,
            bounds: source.bounds.clone(),
        };
        let mut products = BTreeSet::new();
        let mut fission = false;
        for &position in rows {
            let row = source.rows[position];
            part.rows.push(Row { target: 0, ..row });
            part.sig.extend_from_slice(
                &source.sig[position * source.ngroups..(position + 1) * source.ngroups],
            );
            if row.zap > 0 {
                products.insert((row.zap, row.lfs));
            } else if row.mt == 18 {
                fission = true;
            }
        }
        let bytes = library::npz_bytes(&part)?;
        let file = format!("targets/{za}_{liso}.npz");
        targets.push(PackTarget {
            entry: entry.clone(),
            file: file.clone(),
            sha256: sha256_hex(&bytes),
            rows: rows.len(),
            products: products.into_iter().collect(),
            fission,
        });
        files.push((file, bytes));
    }
    let mut decay_sources = Vec::with_capacity(decay_texts.len());
    let mut merged: HashMap<(i32, i32), Nuclide> = HashMap::new();
    for (source, text) in decay_texts.iter().enumerate() {
        let mut states = Vec::new();
        for ((za, liso), record) in split_decay(text)? {
            let file = format!("decay/{source}/{za}_{liso}.txt");
            states.push(PackDecayState {
                za,
                liso,
                file: file.clone(),
                sha256: sha256_hex(record.as_bytes()),
            });
            for (key, nuclide) in actinv_data::decay::parse_text(&record)? {
                merged.entry(key).or_insert(nuclide);
            }
            files.push((file, record.into_bytes()));
        }
        decay_sources.push(PackDecaySource {
            sha256: sha256_hex(text.as_bytes()),
            states,
        });
    }
    let mut daughters: Vec<(State, Vec<State>)> = merged
        .keys()
        .map(|&key| {
            let mut daughters = decay_daughters(&merged, key);
            daughters.sort_unstable();
            daughters.dedup();
            (key, daughters)
        })
        .filter(|(_, daughters)| !daughters.is_empty())
        .collect();
    daughters.sort_unstable();
    let mut base = index.clone();
    if let Some(object) = base.as_object_mut() {
        for key in ["targets", "n_rows", "sha256_npz"] {
            object.remove(key);
        }
    }
    Ok((
        PackGraph {
            schema: SCHEMA.into(),
            library_sha256: sha256_hex(npz),
            index_sha256: sha256_hex(index_text.as_bytes()),
            index: base,
            targets,
            decay: decay_sources,
            names,
            daughters,
        },
        files,
    ))
}

/// The targets reachable from `start` by at most `reactions` reactions, decays between them
/// followed without limit, in the graph's order. `fission` reports whether a fissionable target
/// was reached; its fission products are not followed. `nuclides` is every state the chosen
/// targets' reactions and the decays after them can form: the decay records a run on the
/// assembly needs.
#[derive(Debug)]
pub struct Reach {
    pub targets: Vec<usize>,
    pub fission: bool,
    pub nuclides: Vec<(i32, i32)>,
}

pub fn reachable(
    graph: &PackGraph,
    start: &[(i32, i32)],
    reactions: usize,
) -> Result<Reach, String> {
    let mut position = HashMap::new();
    for (target, entry) in graph.targets.iter().enumerate() {
        position.insert(target_key(&entry.entry)?, target);
    }
    let decay: HashMap<(i32, i32), &[(i32, i32)]> = graph
        .daughters
        .iter()
        .map(|(key, daughters)| (*key, daughters.as_slice()))
        .collect();
    let daughters_of = |nuclide: &(i32, i32)| decay.get(nuclide).copied().unwrap_or_default();
    // Fewest reactions needed to reach each nuclide.
    let mut depth: BTreeMap<(i32, i32), usize> = BTreeMap::new();
    let mut queue: VecDeque<((i32, i32), usize)> = VecDeque::new();
    for &nuclide in start {
        queue.push_back((nuclide, 0));
    }
    while let Some((nuclide, used)) = queue.pop_front() {
        if depth.get(&nuclide).is_some_and(|&best| best <= used) {
            continue;
        }
        depth.insert(nuclide, used);
        for &daughter in daughters_of(&nuclide) {
            queue.push_back((daughter, used));
        }
        if used < reactions {
            if let Some(&target) = position.get(&nuclide) {
                for &product in &graph.targets[target].products {
                    queue.push_back((product, used + 1));
                }
            }
        }
    }
    let mut targets: Vec<usize> = depth
        .keys()
        .filter_map(|nuclide| position.get(nuclide).copied())
        .collect();
    targets.sort_unstable();
    let fission = targets.iter().any(|&target| graph.targets[target].fission);
    let mut nuclides: BTreeSet<(i32, i32)> = BTreeSet::new();
    // The light ground states gas production fills.
    let mut pending: Vec<(i32, i32)> = depth.keys().copied().collect();
    pending.extend([(1001, 0), (1002, 0), (1003, 0), (2003, 0), (2004, 0)]);
    for &target in &targets {
        pending.extend(graph.targets[target].products.iter().copied());
    }
    while let Some(nuclide) = pending.pop() {
        if nuclides.insert(nuclide) {
            pending.extend(daughters_of(&nuclide));
        }
    }
    let known: BTreeSet<(i32, i32)> = graph
        .decay
        .iter()
        .flat_map(|source| source.states.iter().map(|state| (state.za, state.liso)))
        .collect();
    let nuclides = nuclides
        .into_iter()
        .filter(|key| known.contains(key))
        .collect();
    Ok(Reach {
        targets,
        fission,
        nuclides,
    })
}

/// Split a decay sublibrary into one text per state: its MF=8/MT=457 section and the record that
/// ends it, which `decay::parse_text` reads back as that one state.
pub fn split_decay(text: &str) -> Result<BTreeMap<(i32, i32), String>, String> {
    let mut sections = BTreeMap::new();
    let mut current: Option<i32> = None;
    let mut section = String::new();
    for line in text.lines() {
        let Some((mat, mf, mt)) = actinv_data::endf::tail(line) else {
            continue;
        };
        let inside = mf == 8 && mt == 457;
        if inside && current.is_some_and(|open| open != mat) {
            return Err(format!("decay section MAT={mat} began inside another"));
        }
        if inside || current.is_some() {
            section.push_str(line);
            section.push('\n');
        }
        if inside {
            current = Some(mat);
        } else if current.take().is_some() {
            let parsed = actinv_data::decay::parse_text(&section)?;
            let mut keys = parsed.keys();
            let (Some(&key), None) = (keys.next(), keys.next()) else {
                return Err("a decay section did not parse as one state".into());
            };
            if sections.insert(key, std::mem::take(&mut section)).is_some() {
                return Err(format!(
                    "duplicate decay record for ZA={} LISO={}",
                    key.0, key.1
                ));
            }
        }
    }
    if current.is_some() {
        return Err("the decay sublibrary ends inside a section".into());
    }
    Ok(sections)
}

/// The daughters of `nuclide`'s decay modes, as the transmutation chain forms them.
fn decay_daughters(decay: &HashMap<(i32, i32), Nuclide>, nuclide: (i32, i32)) -> Vec<(i32, i32)> {
    let Some(data) = decay.get(&nuclide) else {
        return Vec::new();
    };
    if data.lambda() <= 0.0 {
        return Vec::new();
    }
    let mut daughters = Vec::new();
    for mode in &data.modes {
        if mode.br <= 0.0 {
            continue;
        }
        let (mut z, mut a) = (data.za / 1000, data.za % 1000);
        let mut formed = true;
        for digit in crate::chain::rtyp_digits(mode.rtyp) {
            match crate::chain::step_of(digit) {
                Some((dz, da)) => {
                    z += dz;
                    a += da;
                }
                None => {
                    formed = false;
                    break;
                }
            }
        }
        if formed {
            // The chain falls back to the ground state when the level is absent; keep both.
            daughters.push((z * 1000 + a, mode.rfs.round() as i32));
            daughters.push((z * 1000 + a, 0));
        }
    }
    daughters
}

/// Join the chosen targets' files (graph position, NPZ bytes) into one library: the NPZ and an
/// index naming exactly those targets, with the NPZ's own SHA-256.
pub fn assemble(
    graph: &PackGraph,
    parts: &[(usize, Vec<u8>)],
) -> Result<(Vec<u8>, String), String> {
    let mut parts: Vec<&(usize, Vec<u8>)> = parts.iter().collect();
    parts.sort_by_key(|(target, _)| *target);
    if parts.windows(2).any(|pair| pair[0].0 == pair[1].0) {
        return Err("a pack target was supplied twice".into());
    }
    let mut joined: Option<Library> = None;
    let mut entries = Vec::with_capacity(parts.len());
    for (slot, (target, bytes)) in parts.iter().enumerate() {
        let described = graph
            .targets
            .get(*target)
            .ok_or("a pack part names a target the graph lacks")?;
        if sha256_hex(bytes) != described.sha256 {
            return Err(format!("{} does not match its pack hash", described.file));
        }
        let part = library::read_npz_bytes(bytes)?;
        let joined = joined.get_or_insert_with(|| Library {
            rows: Vec::new(),
            sig: Vec::new(),
            ngroups: part.ngroups,
            bounds: part.bounds.clone(),
        });
        if part.ngroups != joined.ngroups
            || part
                .bounds
                .iter()
                .map(|b| b.to_bits())
                .ne(joined.bounds.iter().map(|b| b.to_bits()))
        {
            return Err(format!("{} has another group structure", described.file));
        }
        joined.rows.extend(part.rows.iter().map(|row| Row {
            target: slot,
            ..*row
        }));
        joined.sig.extend_from_slice(&part.sig);
        entries.push(described.entry.clone());
    }
    let joined = joined.ok_or("a pack needs at least one target")?;
    let npz = library::npz_bytes_stored(&joined)?;
    let mut index = graph.index.clone();
    let object = index.as_object_mut().ok_or("pack index is not an object")?;
    object.insert("targets".into(), Value::Array(entries));
    object.insert("n_rows".into(), joined.rows.len().into());
    object.insert("sha256_npz".into(), sha256_hex(&npz).into());
    let index_text = serde_json::to_string(&index).map_err(|e| e.to_string())?;
    Ok((npz, index_text))
}

/// The nuclides a problem starts from: its material's (an element's natural isotopes) and those
/// fed during any step. Needs no decay data, so a host can choose files before fetching any.
pub fn start_nuclides(spec: &Spec) -> Result<Vec<(i32, i32)>, String> {
    let fed = spec
        .schedule
        .iter()
        .flat_map(|step| step.feed.iter().flat_map(|feed| feed.keys()));
    let mut start = BTreeSet::new();
    for name in spec.material.composition.keys().chain(fed) {
        match composition::material_key(name)? {
            MaterialKey::Nuclide { za, liso, .. } => {
                start.insert((za, liso));
            }
            MaterialKey::Element(_) => {
                let one = BTreeMap::from([(name.clone(), 1.0)]);
                let (atoms, _) =
                    composition::material_atoms_per_gram(&one, "atoms_per_g", &HashMap::new())?;
                start.extend(atoms.keys().copied());
            }
        }
    }
    Ok(start.into_iter().collect())
}

/// The decay sublibraries' text holding only the records of `nuclides`, keyed by the source's
/// position, from the fetched records (file name, bytes). Each record is checked against its hash.
pub fn decay_text(
    graph: &PackGraph,
    nuclides: &[(i32, i32)],
    records: &HashMap<String, Vec<u8>>,
) -> Result<Vec<String>, String> {
    let wanted: BTreeSet<&(i32, i32)> = nuclides.iter().collect();
    let mut texts = Vec::with_capacity(graph.decay.len());
    for source in &graph.decay {
        let mut text = String::new();
        for state in &source.states {
            if !wanted.contains(&(state.za, state.liso)) {
                continue;
            }
            let bytes = records
                .get(&state.file)
                .ok_or_else(|| format!("{} was not fetched", state.file))?;
            if sha256_hex(bytes) != state.sha256 {
                return Err(format!("{} does not match its pack hash", state.file));
            }
            text.push_str(std::str::from_utf8(bytes).map_err(|e| e.to_string())?);
        }
        texts.push(text);
    }
    Ok(texts)
}

/// The decay record files a run on `nuclides` needs.
pub fn decay_files<'a>(graph: &'a PackGraph, nuclides: &[(i32, i32)]) -> Vec<&'a str> {
    let wanted: BTreeSet<&(i32, i32)> = nuclides.iter().collect();
    graph
        .decay
        .iter()
        .flat_map(|source| source.states.iter())
        .filter(|state| wanted.contains(&(state.za, state.liso)))
        .map(|state| state.file.as_str())
        .collect()
}

/// Run the problem `spec_text` on the library assembled from `parts`, reading every other input
/// from `inputs`. The spec's `library` is replaced by the assembly, so the result's certificate
/// names the assembled library and its SHA-256.
pub fn run_packed(
    spec_text: &str,
    entry_point: &str,
    graph: &PackGraph,
    parts: &[(usize, Vec<u8>)],
    mut inputs: HashMap<String, Vec<u8>>,
) -> Result<RunResult, String> {
    let (npz, index) = assemble(graph, parts)?;
    let mut spec: Value = serde_json::from_str(spec_text).map_err(|e| e.to_string())?;
    spec["library"] = serde_json::json!({"path": PACKED_LIBRARY});
    let spec = Spec::from_json(&spec.to_string())?;
    let index_path = format!(
        "{}_index.json",
        PACKED_LIBRARY
            .strip_suffix(".npz")
            .unwrap_or(PACKED_LIBRARY)
    );
    inputs.insert(PACKED_LIBRARY.to_owned(), npz);
    inputs.insert(index_path, index.into_bytes());
    run_with_inputs(&spec, entry_point, inputs)
}

/// The problem's decay files in merge order: primary, then fallback when named.
fn decay_paths(spec: &Spec) -> Vec<String> {
    let mut paths = vec![spec.decay.primary.clone()];
    paths.extend(
        spec.decay
            .fallback
            .iter()
            .filter(|f| !f.is_empty())
            .cloned(),
    );
    paths
}

/// What a host fetches from a pack for one problem: the reach and every file it needs. Refused
/// where a pack cannot reproduce the whole library: fission products (a pack holds neither their
/// reactions nor their decay records), damage (no damage table) and a step changing library (the
/// pack replaces only the base).
pub fn plan(
    spec: &Spec,
    graph: &PackGraph,
    reactions: usize,
) -> Result<(Reach, Vec<String>), String> {
    if !spec.fission_yields.files.is_empty() || spec.fission_yields.spontaneous.is_some() {
        return Err(
            "a pack run cannot follow fission products; run this problem with the whole library"
                .into(),
        );
    }
    if !graph.names.library.matches(&spec.library.path) {
        return Err(format!(
            "this pack holds {}; the problem names {}",
            graph.names.library.id, spec.library.path
        ));
    }
    if spec
        .library
        .sha256
        .as_ref()
        .is_some_and(|sha| *sha != graph.library_sha256)
    {
        return Err("the problem pins another library SHA-256 than the pack was built from".into());
    }
    let decay_paths = decay_paths(spec);
    if decay_paths.len() != graph.names.decay.len()
        || decay_paths
            .iter()
            .zip(&graph.names.decay)
            .any(|(path, name)| !name.matches(path))
    {
        let expected: Vec<&str> = graph
            .names
            .decay
            .iter()
            .map(|name| name.id.as_str())
            .collect();
        return Err(format!(
            "this pack holds the decay data {}; the problem names {}",
            expected.join(" then "),
            decay_paths.join(" then ")
        ));
    }
    if spec.damage.is_some() {
        return Err(
            "a pack holds no damage table; run a problem with damage with the whole library".into(),
        );
    }
    if spec.schedule.iter().any(|step| step.library.is_some()) {
        return Err("a pack run replaces only the problem's library; run a problem whose steps change library with the whole library".into());
    }
    let reach = reachable(graph, &start_nuclides(spec)?, reactions)?;
    let mut files: Vec<String> = reach
        .targets
        .iter()
        .map(|&target| graph.targets[target].file.clone())
        .collect();
    files.extend(
        decay_files(graph, &reach.nuclides)
            .into_iter()
            .map(str::to_owned),
    );
    Ok((reach, files))
}

/// Run `spec_text` from a pack: the library and decay data come from `pack_files` (the files
/// [`plan`] names, by pack path) and every other input from `inputs`.
pub fn run_from_pack(
    spec_text: &str,
    entry_point: &str,
    graph: &PackGraph,
    reactions: usize,
    pack_files: &HashMap<String, Vec<u8>>,
    mut inputs: HashMap<String, Vec<u8>>,
) -> Result<RunResult, String> {
    let spec = Spec::from_json(spec_text)?;
    let (reach, _) = plan(&spec, graph, reactions)?;
    let mut parts = Vec::with_capacity(reach.targets.len());
    for &target in &reach.targets {
        let file = &graph.targets[target].file;
        let bytes = pack_files
            .get(file)
            .ok_or_else(|| format!("{file} was not fetched"))?;
        parts.push((target, bytes.clone()));
    }
    for (path, text) in
        decay_paths(&spec)
            .into_iter()
            .zip(decay_text(graph, &reach.nuclides, pack_files)?)
    {
        inputs.insert(path, text.into_bytes());
    }
    run_packed(spec_text, entry_point, graph, &parts, inputs)
}

fn relative(a: f64, b: f64) -> f64 {
    if a == b {
        0.0
    } else {
        (a - b).abs() / a.abs().max(b.abs())
    }
}

/// What two rounds are compared on, for one step: activity by nuclide, total heat and photon
/// emission, and atoms by nuclide.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct StepSummary {
    pub activity: BTreeMap<String, f64>,
    pub heat: f64,
    pub photons: f64,
    pub atoms: BTreeMap<String, f64>,
    pub total_atoms: f64,
}

pub fn summary(result: &RunResult) -> Vec<StepSummary> {
    result
        .steps
        .iter()
        .map(|step| StepSummary {
            activity: step.activity_Bq_per_g.clone(),
            heat: step.heat_W_per_g.total,
            photons: step
                .photon_source
                .as_ref()
                .map_or(0.0, |source| source.total_photons_s_g),
            atoms: step
                .inventory
                .iter()
                .map(|entry| (entry.nuclide.clone(), entry.atoms_per_g))
                .collect(),
            total_atoms: step.total_atoms_per_g,
        })
        .collect()
}

/// How far one round's result moved from the previous one's: the largest relative change at any
/// step of a total (activity, heat, photon emission), of a nuclide carrying 0.1% of the step's
/// activity, and of the atoms that changed nuclide as a share of all atoms.
#[derive(Clone, Copy, Debug, PartialEq, Serialize)]
pub struct Change {
    pub totals: f64,
    pub nuclide: f64,
    pub atoms: f64,
}

impl Change {
    /// None when the two have different numbers of steps.
    pub fn between(previous: &[StepSummary], next: &[StepSummary]) -> Option<Change> {
        if previous.len() != next.len() {
            return None;
        }
        let mut change = Change {
            totals: 0.0,
            nuclide: 0.0,
            atoms: 0.0,
        };
        let totals =
            |step: &StepSummary| [step.activity.values().sum::<f64>(), step.heat, step.photons];
        for (a, b) in previous.iter().zip(next) {
            for (x, y) in totals(a).into_iter().zip(totals(b)) {
                change.totals = change.totals.max(relative(x, y));
            }
            let total = totals(b)[0];
            for (name, &value) in &b.activity {
                if total > 0.0 && value >= 1e-3 * total {
                    let before = a.activity.get(name).copied().unwrap_or(0.0);
                    change.nuclide = change.nuclide.max(relative(before, value));
                }
            }
            let mut moved = 0.0;
            for (name, &atoms) in &b.atoms {
                moved += (atoms - a.atoms.get(name).copied().unwrap_or(0.0)).abs();
            }
            for (name, &atoms) in &a.atoms {
                if !b.atoms.contains_key(name) {
                    moved += atoms.abs();
                }
            }
            if b.total_atoms > 0.0 {
                change.atoms = change.atoms.max(moved / b.total_atoms);
            }
        }
        Some(change)
    }

    pub fn settled(&self) -> bool {
        self.totals <= SETTLED_TOTAL
            && self.nuclide <= SETTLED_NUCLIDE
            && self.atoms <= SETTLED_ATOMS
    }
}

/// One round of a [`Deepening`] run, as it reports it.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Round {
    pub reactions: usize,
    pub targets: usize,
    pub nuclides: usize,
    /// Against the previous round; none on the first.
    pub change: Option<Change>,
}

/// What a [`Deepening`] run does next.
pub enum Next {
    /// Fetch what [`plan`] names at this depth and solve again.
    Run(usize),
    /// The answer: the last round's result.
    Done(Box<RunResult>),
}

/// A run that follows more reactions, round by round through [`ROUNDS`], until the result stops
/// depending on the depth: [`SETTLED_ROUNDS`] agreements in a row, or a depth that reaches no
/// target or nuclide the previous one did not (the result can then no longer change), or the last
/// depth. The host plans, fetches and solves each round (the browser fetches between them) and
/// hands the result to [`Deepening::solved`].
#[derive(Default)]
pub struct Deepening {
    agreed: usize,
    previous: Option<Vec<StepSummary>>,
    pub rounds: Vec<Round>,
}

impl Deepening {
    pub fn new() -> Self {
        Self::default()
    }

    /// Take the result of the round just solved, on `reach` (from [`plan`] at that depth).
    pub fn solved(&mut self, reach: &Reach, result: RunResult) -> Next {
        match self.advance(reach.targets.len(), reach.nuclides.len(), summary(&result)) {
            Some(deeper) => Next::Run(deeper),
            None => Next::Done(Box::new(result)),
        }
    }

    /// Record a round that reached `targets` targets and `nuclides` decay states; the next depth,
    /// or none when this round's result is the answer.
    fn advance(
        &mut self,
        targets: usize,
        nuclides: usize,
        steps: Vec<StepSummary>,
    ) -> Option<usize> {
        let round = self.rounds.len();
        let closed = self
            .rounds
            .last()
            .is_some_and(|last| last.targets == targets && last.nuclides == nuclides);
        let change = self
            .previous
            .as_deref()
            .and_then(|previous| Change::between(previous, &steps));
        self.rounds.push(Round {
            reactions: ROUNDS[round],
            targets,
            nuclides,
            change,
        });
        self.agreed = match change {
            Some(change) if change.settled() => self.agreed + 1,
            _ => 0,
        };
        self.previous = Some(steps);
        if closed || self.agreed >= SETTLED_ROUNDS || round + 1 == ROUNDS.len() {
            None
        } else {
            Some(ROUNDS[round + 1])
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn record(values: [&str; 6], mat: i32, mt: i32, sequence: i32) -> String {
        let data: String = values
            .into_iter()
            .map(|value| format!("{value:>11}"))
            .collect();
        format!("{data}{mat:>4} 8{mt:>3}{sequence:>5}\n")
    }

    /// A decay record: stable when `beta_minus` is false, else one beta-minus mode.
    fn decay_record(mat: i32, za: &str, beta_minus: bool) -> String {
        let mut text = record([za, "55.0", "0", "0", "0", "0"], mat, 457, 1);
        let half_life = if beta_minus { "100.0" } else { "0.0" };
        text += &record([half_life, "0", "0", "0", "0", "0"], mat, 457, 2);
        if beta_minus {
            text += &record(["0", "1", "0", "0", "6", "1"], mat, 457, 3);
            text += &record(["1", "0", "0", "0", "1", "0"], mat, 457, 4);
        } else {
            text += &record(["0", "0", "0", "0", "0", "0"], mat, 457, 3);
        }
        text + &record(["", "", "", "", "", ""], mat, 0, 99_999)
    }

    fn names() -> PackNames {
        PackNames {
            library: SourceName {
                id: "test-library".into(),
                file: "test-library.npz".into(),
            },
            decay: vec![SourceName {
                id: "test-decay".into(),
                file: "test_decay.dat".into(),
            }],
        }
    }

    fn step(activity: f64, atoms: [(&str, f64); 2]) -> StepSummary {
        StepSummary {
            activity: [("Co60".to_owned(), activity)].into_iter().collect(),
            heat: activity * 1e-13,
            photons: activity * 2.0,
            atoms: atoms
                .into_iter()
                .map(|(name, value)| (name.to_owned(), value))
                .collect(),
            total_atoms: atoms.iter().map(|(_, value)| value).sum(),
        }
    }

    #[test]
    fn a_change_counts_totals_nuclides_and_moved_atoms() {
        let before = [step(100.0, [("Fe56", 1e22), ("Co59", 1e18)])];
        let same = Change::between(&before, &before).unwrap();
        assert_eq!(
            same,
            Change {
                totals: 0.0,
                nuclide: 0.0,
                atoms: 0.0
            }
        );
        assert!(same.settled());
        // Stable atoms moving deeper change nothing a detector sees, yet the round is not settled.
        let burnt = [step(100.0, [("Fe56", 0.9e22), ("Co59", 1e18)])];
        let change = Change::between(&before, &burnt).unwrap();
        assert_eq!(change.totals, 0.0);
        assert!((change.atoms - 1e21 / (0.9e22 + 1e18)).abs() < 1e-12);
        assert!(!change.settled());
        let hotter = [step(100.1, [("Fe56", 1e22), ("Co59", 1e18)])];
        let change = Change::between(&before, &hotter).unwrap();
        assert!((change.totals - 0.1 / 100.1).abs() < 1e-12);
        assert!(!change.settled());
        assert_eq!(Change::between(&before, &[]), None);
        // A total far below its peak still counts relatively: a trace that moves is not settled.
        let cooled = [
            before[0].clone(),
            step(1e-11, [("Fe56", 1e22), ("Co59", 1e18)]),
        ];
        let trace = [
            before[0].clone(),
            step(2e-11, [("Fe56", 1e22), ("Co59", 1e18)]),
        ];
        let change = Change::between(&cooled, &trace).unwrap();
        assert!((change.totals - 0.5).abs() < 1e-12, "{change:?}");
        assert!(!change.settled());
    }

    #[test]
    fn deepening_needs_two_agreements_in_a_row_or_a_closed_reach() {
        let a = vec![step(100.0, [("Fe56", 1e22), ("Co59", 1e18)])];
        let b = vec![step(50.0, [("Fe56", 1e22), ("Co59", 1e18)])];
        // A plateau: rounds 2 and 3 agree, 4 moves, then 6 and 8 agree with 4.
        let mut run = Deepening::new();
        assert_eq!(run.advance(10, 20, a.clone()), Some(3));
        assert_eq!(run.advance(11, 21, a.clone()), Some(4));
        assert_eq!(run.advance(12, 22, b.clone()), Some(6));
        assert_eq!(run.advance(13, 23, b.clone()), Some(8));
        assert_eq!(run.advance(14, 24, b.clone()), None);
        let depths: Vec<usize> = run.rounds.iter().map(|round| round.reactions).collect();
        assert_eq!(depths, [2, 3, 4, 6, 8]);
        assert_eq!(run.rounds[0].change, None);
        // A depth that reaches nothing new ends the run at once.
        let mut run = Deepening::new();
        assert_eq!(run.advance(10, 20, a.clone()), Some(3));
        assert_eq!(run.advance(10, 20, a.clone()), None);
        // Never past the last depth.
        let mut run = Deepening::new();
        let mut targets = 0;
        let mut next = Some(ROUNDS[0]);
        while next.is_some() {
            targets += 1;
            let steps = if targets % 2 == 0 {
                a.clone()
            } else {
                b.clone()
            };
            next = run.advance(targets, targets, steps);
        }
        assert_eq!(run.rounds.len(), ROUNDS.len());
    }

    #[test]
    fn names_match_a_catalog_id_or_a_file_name() {
        let name = &names().library;
        assert!(name.matches("catalog:test-library"));
        assert!(name.matches("data/v1/test-library.npz"));
        assert!(!name.matches("catalog:test-library.npz"));
        assert!(!name.matches("data/other.npz"));
    }

    /// Fe56(n,g)Fe57, Fe57(n,g)Fe58, Co57(n,g)Co58, and Fe57 decaying to Co57.
    fn library() -> (Vec<u8>, String, String) {
        let row = |target: usize, zap: i32| Row {
            target,
            mt: 102,
            zap,
            lfs: 0,
            lmf: if zap == -1 { 0 } else { -1 },
        };
        let source = Library {
            rows: vec![
                row(0, -1),
                row(0, 26_057),
                row(1, -1),
                row(1, 26_058),
                row(2, -1),
                row(2, 27_058),
            ],
            sig: (0..12).map(f64::from).collect(),
            ngroups: 2,
            bounds: vec![1.0, 2.0, 3.0],
        };
        let npz = library::npz_bytes(&source).expect("npz");
        let index = serde_json::json!({
            "library": "test",
            "targets": [{"za": 26056, "liso": 0}, {"za": 26057, "liso": 0}, {"za": 27057, "liso": 0}],
            "n_rows": 6,
            "sha256_npz": sha256_hex(&npz),
        });
        let decay = decay_record(2631, "26056", false) + &decay_record(2634, "26057", true);
        (npz, index.to_string(), decay)
    }

    #[test]
    fn reach_counts_reactions_and_follows_decay_freely() {
        let (npz, index, decay) = library();
        let (graph, _) = build(&npz, &index, &[&decay], names()).expect("pack");
        assert_eq!(graph.daughters, vec![((26_057, 0), vec![(27_057, 0)])]);
        let none = reachable(&graph, &[(26_056, 0)], 0).expect("reach");
        assert_eq!(none.targets, vec![0]);
        let one = reachable(&graph, &[(26_056, 0)], 1).expect("reach");
        assert_eq!(one.targets, vec![0, 1, 2]);
        assert_eq!(one.nuclides, vec![(26_056, 0), (26_057, 0)]);
        assert!(!one.fission);
    }

    #[test]
    fn assembling_every_target_gives_back_the_library_and_its_decay() {
        let (npz, index, decay) = library();
        let (graph, files) = build(&npz, &index, &[&decay], names()).expect("pack");
        let files: HashMap<String, Vec<u8>> = files.into_iter().collect();
        let parts: Vec<(usize, Vec<u8>)> = graph
            .targets
            .iter()
            .enumerate()
            .rev()
            .map(|(target, entry)| (target, files[&entry.file].clone()))
            .collect();
        let (joined, joined_index) = assemble(&graph, &parts).expect("assembly");
        let (original, copy) = (
            library::read_npz_bytes(&npz).expect("source"),
            library::read_npz_bytes(&joined).expect("assembly"),
        );
        assert_eq!(copy.rows, original.rows);
        assert_eq!(copy.sig, original.sig);
        let joined_index: Value = serde_json::from_str(&joined_index).expect("index");
        assert_eq!(joined_index["sha256_npz"], sha256_hex(&joined).as_str());
        assert_eq!(joined_index["n_rows"], 6);
        assert_eq!(joined_index["library"], "test");

        let nuclides = [(26_056, 0), (26_057, 0)];
        let texts = decay_text(&graph, &nuclides, &files).expect("decay");
        let rebuilt = actinv_data::decay::parse_text(&texts[0]).expect("rebuilt decay");
        let whole = actinv_data::decay::parse_text(&decay).expect("decay");
        assert_eq!(rebuilt.len(), 2);
        for key in nuclides {
            assert_eq!(rebuilt[&key].modes.len(), whole[&key].modes.len());
            assert_eq!(rebuilt[&key].half_life, whole[&key].half_life);
        }
    }

    #[test]
    fn the_graph_names_every_file_with_its_hash() {
        let (npz, index, decay) = library();
        let (graph, files) = build(&npz, &index, &[&decay], names()).expect("pack");
        let hashes = graph.file_hashes();
        assert_eq!(hashes.len(), files.len());
        for (file, bytes) in &files {
            assert_eq!(hashes[file.as_str()], sha256_hex(bytes), "{file}");
        }
    }

    #[test]
    fn a_tampered_part_is_refused() {
        let (npz, index, decay) = library();
        let (graph, files) = build(&npz, &index, &[&decay], names()).expect("pack");
        let files: HashMap<String, Vec<u8>> = files.into_iter().collect();
        let mut bytes = files[&graph.targets[1].file].clone();
        let last = bytes.len() - 1;
        bytes[last] ^= 1;
        let error = assemble(&graph, &[(1, bytes)]).unwrap_err();
        assert!(error.contains("does not match its pack hash"), "{error}");
        let mut records = files.clone();
        records
            .get_mut(&graph.decay[0].states[1].file)
            .expect("record")[0] ^= 1;
        let error = decay_text(&graph, &[(26_057, 0)], &records).unwrap_err();
        assert!(error.contains("does not match its pack hash"), "{error}");
    }
}
