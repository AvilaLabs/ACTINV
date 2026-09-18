#![allow(non_snake_case)] // temperature_K is part of the public/canonical wire vocabulary.
//! Deterministic activation-library assembly from strict ENDF evaluations.

use crate::activation::{
    parse_evaluations, Evaluation, Mf6Product, ProductRef, ProductTable, Projectile,
};
use crate::groups::{GroupStructure, Tabulated};
use crate::library::{write_npz, Library, Row};
use crate::processing::{has_resonance_contribution, process_reaction, ProcessedReaction};
use crate::resonance::{omitted_fission_total_width_count, validate_rmatrix_limited, RangeData};
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, HashSet};
use std::ffi::OsStr;
use std::io::Read;
use std::path::{Path, PathBuf};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LibraryFormat {
    Auto,
    Tendl,
    Eaf,
}

impl LibraryFormat {
    pub fn parse(value: &str) -> Result<Self, String> {
        match value {
            "auto" => Ok(Self::Auto),
            "tendl" => Ok(Self::Tendl),
            "eaf" => Ok(Self::Eaf),
            _ => Err(format!(
                "unknown library format '{value}'; expected auto, tendl or eaf"
            )),
        }
    }

    fn name(self) -> &'static str {
        match self {
            Self::Auto => "auto",
            Self::Tendl => "tendl",
            Self::Eaf => "eaf",
        }
    }
}

#[derive(Clone, Debug)]
pub struct BuildOptions {
    pub format: LibraryFormat,
    pub projectile: Option<Projectile>,
    pub groups: GroupStructure,
    pub temperature_K: f64,
    pub workers: usize,
    pub cache: Option<PathBuf>,
    pub grid_density: f64,
    /// Reject every emitted state sum above the runtime total instead of
    /// reconciling sums inside the frozen standard envelope.
    pub strict_states: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct TargetIndex {
    pub file: String,
    pub source_sha256: String,
    pub mat: i32,
    pub za: i32,
    pub liso: i32,
    #[serde(default)]
    pub lis: i32,
    #[serde(default)]
    pub elis_eV: f64,
    pub awr: f64,
    pub evaluation_temperature_K: f64,
    pub n_mf2: usize,
    pub n_mf3: usize,
    pub n_mf6: usize,
    pub n_mf8: usize,
    pub n_mf9: usize,
    pub n_mf10: usize,
    pub n_rows: usize,
    #[serde(default)]
    pub state_mappings: Vec<StateMapping>,
    pub ledger: Vec<String>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct StateMapping {
    pub mt: i32,
    pub zap: i32,
    pub lmf: i32,
    pub raw_lfs: i32,
    pub elfs_eV: Option<f64>,
    pub qm_eV: Option<f64>,
    pub qi_eV: Option<f64>,
    pub qm_minus_qi_eV: Option<f64>,
    pub mapping_excitation_eV: Option<f64>,
    pub canonical_liso: Option<i32>,
    pub catalog_lis: Option<i32>,
    pub catalog_elis_eV: Option<f64>,
    pub catalog_file: Option<String>,
    pub catalog_source_sha256: Option<String>,
    pub catalog_evaluations: Option<usize>,
    pub tolerance_eV: Option<f64>,
    pub excitation_delta_eV: Option<f64>,
    pub decision: String,
}

#[derive(Clone, Debug, Serialize)]
struct CanonicalOptions {
    grid_density: f64,
}

#[derive(Clone, Debug, Serialize)]
struct BuildIndex {
    schema: &'static str,
    format: String,
    projectile: String,
    temperature_K: f64,
    groups: String,
    group_boundary_sha256: String,
    weighting: &'static str,
    builder_fingerprint: String,
    /// P25: declares which emission rules produced the rows. Absent in
    /// pre-repair artifacts; the scorer uses it to select the matching
    /// inelastic reconstruction.
    emission_model: &'static str,
    options: CanonicalOptions,
    state_catalog: Vec<CatalogState>,
    targets: Vec<TargetIndex>,
    n_rows: usize,
    columns: &'static str,
    sha256_npz: String,
}

#[derive(Clone, Debug)]
pub struct BuildSummary {
    pub output: PathBuf,
    pub index: PathBuf,
    pub projectile: Projectile,
    pub targets: usize,
    pub rows: usize,
    pub cache_hits: usize,
    pub sha256_npz: String,
    pub builder_fingerprint: String,
}

#[derive(Clone, Debug)]
struct BuiltRow {
    mt: i32,
    zap: i32,
    lfs: i32,
    lmf: i32,
    sigma: Vec<f64>,
    raw_state: Option<RawProductState>,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Serialize)]
struct RawProductState {
    raw_lfs: i32,
    elfs_eV: Option<f64>,
    qm_eV: Option<f64>,
    qi_eV: Option<f64>,
}

#[derive(Clone, Debug)]
struct BuiltTarget {
    index: TargetIndex,
    rows: Vec<BuiltRow>,
}

#[derive(Clone, Copy)]
struct EvaluationBuildSettings<'a> {
    groups: &'a GroupStructure,
    temperature_K: f64,
    grid_density: f64,
    strict_states: bool,
}

#[derive(Clone, Debug)]
struct BuiltSource {
    format: LibraryFormat,
    projectile: Projectile,
    targets: Vec<BuiltTarget>,
    from_cache: bool,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct CacheIndex {
    schema: String,
    key: String,
    source_sha256: String,
    format: String,
    projectile: String,
    npz_sha256: String,
    targets: Vec<TargetIndex>,
    target_float_bits: Vec<[u64; 3]>,
    raw_product_states: Vec<Option<RawProductState>>,
}

#[derive(Deserialize)]
struct MtProductJson {
    table: BTreeMap<String, [i32; 2]>,
}

fn mt_products() -> Result<BTreeMap<i32, (i32, i32)>, String> {
    let raw: MtProductJson = serde_json::from_str(include_str!("../data/mt_products.json"))
        .map_err(|error| format!("invalid vendored MT product table: {error}"))?;
    raw.table
        .into_iter()
        .map(|(mt, delta)| {
            Ok((
                mt.parse::<i32>()
                    .map_err(|_| format!("invalid MT product key '{mt}'"))?,
                (delta[0], delta[1]),
            ))
        })
        .collect()
}

pub fn builder_fingerprint() -> String {
    let mut hash = Sha256::new();
    hash.update(b"ACTINV-RUST-BUILDER-v1\0");
    hash.update(include_bytes!("builder.rs"));
    hash.update(include_bytes!("activation.rs"));
    hash.update(include_bytes!("endf.rs"));
    hash.update(include_bytes!("groups.rs"));
    hash.update(include_bytes!("doppler.rs"));
    hash.update(include_bytes!("resonance.rs"));
    hash.update(include_bytes!("processing.rs"));
    hash.update(include_bytes!("library.rs"));
    hash.update(include_bytes!("../data/mt_products.json"));
    format!("{:x}", hash.finalize())
}

pub fn sha256_file(path: impl AsRef<Path>) -> Result<String, String> {
    let path = path.as_ref();
    let mut file = std::fs::File::open(path)
        .map_err(|error| format!("cannot open {} for hashing: {error}", path.display()))?;
    let mut hash = Sha256::new();
    let mut buffer = [0u8; 1024 * 1024];
    loop {
        let count = file
            .read(&mut buffer)
            .map_err(|error| format!("cannot hash {}: {error}", path.display()))?;
        if count == 0 {
            break;
        }
        hash.update(&buffer[..count]);
    }
    Ok(format!("{:x}", hash.finalize()))
}

fn checkpoint_key(source_sha256: &str, options: &BuildOptions) -> String {
    let mut hash = Sha256::new();
    hash.update(b"ACTINV-TARGET-CHECKPOINT-v1\0");
    hash.update(source_sha256.as_bytes());
    hash.update([0]);
    hash.update(options.format.name().as_bytes());
    hash.update([0]);
    hash.update(
        options
            .projectile
            .map(Projectile::name)
            .unwrap_or("auto")
            .as_bytes(),
    );
    hash.update(options.temperature_K.to_bits().to_le_bytes());
    hash.update(options.grid_density.to_bits().to_le_bytes());
    hash.update([u8::from(options.strict_states)]);
    hash.update(options.groups.hash().as_bytes());
    hash.update(builder_fingerprint().as_bytes());
    format!("{:x}", hash.finalize())
}

fn cache_paths(directory: &Path, key: &str) -> (PathBuf, PathBuf) {
    (
        directory.join(format!("{key}.npz")),
        directory.join(format!("{key}.json")),
    )
}

fn restore_target_float_bits(target: &mut TargetIndex, bits: [u64; 3]) {
    target.awr = f64::from_bits(bits[0]);
    target.evaluation_temperature_K = f64::from_bits(bits[1]);
    target.elis_eV = f64::from_bits(bits[2]);
}

fn source_library(source: &BuiltSource, groups: &GroupStructure) -> Library {
    let mut rows = Vec::new();
    let mut sig = Vec::new();
    for (target_index, target) in source.targets.iter().enumerate() {
        for row in &target.rows {
            rows.push(Row {
                target: target_index,
                mt: row.mt,
                zap: row.zap,
                lfs: row.lfs,
                lmf: row.lmf,
            });
            sig.extend_from_slice(&row.sigma);
        }
    }
    Library {
        rows,
        sig,
        ngroups: groups.groups(),
        bounds: groups.boundaries_ev.clone(),
    }
}

fn load_checkpoint(
    directory: &Path,
    key: &str,
    source_sha256: &str,
    groups: &GroupStructure,
) -> Option<BuiltSource> {
    let (npz_path, index_path) = cache_paths(directory, key);
    let index: CacheIndex =
        serde_json::from_str(&std::fs::read_to_string(index_path).ok()?).ok()?;
    if index.schema != "actinv-target-checkpoint-3"
        || index.key != key
        || index.source_sha256 != source_sha256
        || sha256_file(&npz_path).ok()? != index.npz_sha256
        || index.target_float_bits.len() != index.targets.len()
    {
        return None;
    }
    let format = match index.format.as_str() {
        "tendl" => LibraryFormat::Tendl,
        "eaf" => LibraryFormat::Eaf,
        _ => return None,
    };
    let projectile = Projectile::parse(&index.projectile).ok()?;
    let library = crate::library::read_npz(npz_path.to_str()?).ok()?;
    if library.ngroups != groups.groups()
        || index.raw_product_states.len() != library.rows.len()
        || library
            .bounds
            .iter()
            .zip(&groups.boundaries_ev)
            .any(|(left, right)| left.to_bits() != right.to_bits())
    {
        return None;
    }
    let mut row_offset = 0usize;
    let mut targets = Vec::with_capacity(index.targets.len());
    for (target_number, (mut target_index, float_bits)) in index
        .targets
        .into_iter()
        .zip(index.target_float_bits)
        .enumerate()
    {
        restore_target_float_bits(&mut target_index, float_bits);
        let end = row_offset.checked_add(target_index.n_rows)?;
        let source_rows = library.rows.get(row_offset..end)?;
        if source_rows.iter().any(|row| row.target != target_number) {
            return None;
        }
        let raw_states = index.raw_product_states.get(row_offset..end)?;
        let rows = source_rows
            .iter()
            .zip(raw_states)
            .enumerate()
            .map(|(local, (row, raw_state))| BuiltRow {
                mt: row.mt,
                zap: row.zap,
                lfs: row.lfs,
                lmf: row.lmf,
                sigma: library.sigma(row_offset + local).to_vec(),
                raw_state: *raw_state,
            })
            .collect();
        targets.push(BuiltTarget {
            index: target_index,
            rows,
        });
        row_offset = end;
    }
    if row_offset != library.rows.len() {
        return None;
    }
    Some(BuiltSource {
        format,
        projectile,
        targets,
        from_cache: true,
    })
}

fn store_checkpoint(
    directory: &Path,
    key: &str,
    source_sha256: &str,
    source: &BuiltSource,
    groups: &GroupStructure,
) -> Result<(), String> {
    let (npz_path, index_path) = cache_paths(directory, key);
    let library = source_library(source, groups);
    write_npz(&npz_path, &library)?;
    let index = CacheIndex {
        schema: "actinv-target-checkpoint-3".into(),
        key: key.into(),
        source_sha256: source_sha256.into(),
        format: source.format.name().into(),
        projectile: source.projectile.name().into(),
        npz_sha256: sha256_file(&npz_path)?,
        targets: source
            .targets
            .iter()
            .map(|target| target.index.clone())
            .collect(),
        target_float_bits: source
            .targets
            .iter()
            .map(|target| {
                [
                    target.index.awr.to_bits(),
                    target.index.evaluation_temperature_K.to_bits(),
                    target.index.elis_eV.to_bits(),
                ]
            })
            .collect(),
        raw_product_states: source
            .targets
            .iter()
            .flat_map(|target| target.rows.iter().map(|row| row.raw_state))
            .collect(),
    };
    write_json_atomic(&index_path, &index)
}

pub(crate) fn discover_inputs(input: &Path, output: Option<&Path>) -> Result<Vec<PathBuf>, String> {
    let metadata = std::fs::symlink_metadata(input)
        .map_err(|error| format!("cannot inspect input {}: {error}", input.display()))?;
    if metadata.file_type().is_symlink() {
        return Err(format!("input {} is a symlink", input.display()));
    }
    if metadata.is_file() {
        if output.is_some_and(|output| input == output) {
            return Err("activation-library output cannot overwrite its ENDF input".into());
        }
        return Ok(vec![input.to_path_buf()]);
    }
    if !metadata.is_dir() {
        return Err(format!(
            "input {} is not a regular file or directory",
            input.display()
        ));
    }
    let input_directory = input
        .canonicalize()
        .map_err(|error| format!("cannot resolve input {}: {error}", input.display()))?;
    if let Some(parent) = output
        .and_then(Path::parent)
        .filter(|parent| parent.exists())
    {
        if parent.canonicalize().ok().as_ref() == Some(&input_directory) {
            return Err("activation-library output must be outside its input directory".into());
        }
    }
    let mut files = Vec::new();
    for entry in std::fs::read_dir(input)
        .map_err(|error| format!("cannot read input directory {}: {error}", input.display()))?
    {
        let entry = entry.map_err(|error| format!("cannot read directory entry: {error}"))?;
        let path = entry.path();
        let metadata = std::fs::symlink_metadata(&path)
            .map_err(|error| format!("cannot inspect {}: {error}", path.display()))?;
        if metadata.file_type().is_symlink() || !metadata.is_file() {
            return Err(format!(
                "input directory contains non-regular entry {}",
                path.display()
            ));
        }
        files.push(path);
    }
    files.sort_by(|left, right| os_bytes(left.file_name()).cmp(os_bytes(right.file_name())));
    if files.is_empty() {
        return Err(format!("input directory {} is empty", input.display()));
    }
    Ok(files)
}

/// Inspect the first bytewise-ordered evaluation to select projectile-dependent CLI defaults. The full build still
/// validates every file and rejects a mixed directory.
pub fn inspect_projectile(input: impl AsRef<Path>) -> Result<Projectile, String> {
    let files = discover_inputs(input.as_ref(), None)?;
    let first = &files[0];
    let text = std::fs::read_to_string(first)
        .map_err(|error| format!("cannot read {} as ENDF text: {error}", first.display()))?;
    parse_evaluations(&text, None)
        .map_err(|error| format!("{}: {error}", first.display()))?
        .first()
        .map(|evaluation| evaluation.metadata.projectile)
        .ok_or_else(|| format!("{} contains no evaluation", first.display()))
}

fn os_bytes(value: Option<&OsStr>) -> &[u8] {
    value.map(OsStr::as_encoded_bytes).unwrap_or_default()
}

fn validate_options(options: &BuildOptions) -> Result<(), String> {
    options.groups.validate()?;
    if !options.temperature_K.is_finite() || options.temperature_K < 0.0 {
        return Err("library temperature must be finite and nonnegative".into());
    }
    if !(1..=256).contains(&options.workers) {
        return Err("workers must be between 1 and 256".into());
    }
    if !options.grid_density.is_finite() || options.grid_density <= 0.0 {
        return Err("grid density must be finite and positive".into());
    }
    if let Some(projectile) = options.projectile {
        if projectile != Projectile::Neutron && options.temperature_K != 0.0 {
            return Err(format!(
                "{} activation libraries require exactly 0 K",
                projectile.name()
            ));
        }
        let expected_groups = match projectile {
            Projectile::Neutron => 709,
            Projectile::Proton | Projectile::Deuteron | Projectile::Alpha => 162,
        };
        if options.groups.name.starts_with("fispact-") && options.groups.groups() != expected_groups
        {
            return Err(format!(
                "{} uses the {}-group structure, not {} groups",
                projectile.name(),
                expected_groups,
                options.groups.groups()
            ));
        }
    }
    Ok(())
}

fn detected_format(text: &str, evaluation: &Evaluation) -> Result<LibraryFormat, String> {
    if text.contains("EAF-2010") || text.contains("EAF-20100") {
        Ok(LibraryFormat::Eaf)
    } else if text.contains("TENDL-")
        || !evaluation.mf2_sections.is_empty()
        || !evaluation.mf6.is_empty()
    {
        Ok(LibraryFormat::Tendl)
    } else {
        Err("cannot auto-detect ENDF library format; pass --format tendl or --format eaf".into())
    }
}

fn checked_collapse(
    groups: &GroupStructure,
    table: &Tabulated,
    label: &str,
) -> Result<Vec<f64>, String> {
    if table.y.iter().any(|value| *value < 0.0) {
        return Err(format!("{label} contains a negative cross section"));
    }
    checked_groups(groups.collapse(table)?, label)
}

fn checked_product(
    groups: &GroupStructure,
    tables: &[&Tabulated],
    label: &str,
) -> Result<Vec<f64>, String> {
    if tables
        .iter()
        .any(|table| table.y.iter().any(|value| *value < 0.0))
    {
        return Err(format!(
            "{label} contains a negative cross section or yield"
        ));
    }
    checked_groups(groups.collapse_product(tables)?, label)
}

fn checked_groups(values: Vec<f64>, label: &str) -> Result<Vec<f64>, String> {
    if values
        .iter()
        .any(|value| !value.is_finite() || *value < 0.0)
    {
        Err(format!(
            "{label} collapsed to a nonfinite or negative group value"
        ))
    } else {
        Ok(values)
    }
}

fn sum_groups(total: &mut [f64], addend: &[f64]) -> Result<(), String> {
    if total.len() != addend.len() {
        return Err("cannot sum mismatched group arrays".into());
    }
    for (left, right) in total.iter_mut().zip(addend) {
        *left += *right;
    }
    Ok(())
}

fn skip_mt(mt: i32, projectile: Projectile, has_mf6: bool) -> bool {
    matches!(mt, 1 | 2 | 3 | 27 | 101 | 444 | 19 | 20 | 21 | 38)
        || (mt == 5 && !(projectile != Projectile::Neutron && has_mf6))
        || (201..=207).contains(&mt)
        || (600..=849).contains(&mt)
        || mt >= 1000
}

/// Inelastic excitation semantics (MT=4 and discrete levels 51–91) apply only
/// to neutron evaluations: there the residual is the target nuclide itself.
/// For charged-particle evaluations these MTs are neutron-emission channels
/// whose residual is a different nuclide (corpus-wide MF=8 evidence: every
/// charged-particle declaration names a different-residual ZAP, every neutron
/// declaration the same residual). Charged files therefore take the normal
/// product path, which emits and reconciles every declared state.
fn inelastic(mt: i32, projectile: Projectile) -> bool {
    projectile == Projectile::Neutron && (mt == 4 || (51..=91).contains(&mt))
}

fn descriptor_set(products: &[ProductRef], lmf: i32) -> Result<BTreeSet<(i32, i32)>, String> {
    let mut declarations = BTreeMap::new();
    for product in products
        .iter()
        .filter(|product| product.lmf == lmf && product.zap >= 0)
    {
        let identity = (product.zap, product.lfs);
        if let Some(previous) = declarations.get(&identity) {
            if *previous != product {
                return Err(format!(
                    "conflicting duplicate MF=8 product ZAP={}/LFS={}/LMF={lmf}",
                    product.zap, product.lfs
                ));
            }
        } else {
            declarations.insert(identity, product);
        }
    }
    Ok(declarations.into_keys().collect())
}

fn unique_product_tables<'a>(
    products: &'a [ProductTable],
    label: &str,
) -> Result<Vec<&'a ProductTable>, String> {
    let mut positions = BTreeMap::new();
    let mut unique = Vec::new();
    for product in products {
        let identity = (product.zap, product.lfs);
        if let Some(position) = positions.get(&identity).copied() {
            if unique[position] != product {
                return Err(format!(
                    "{label} contains conflicting duplicate product ZAP={}/LFS={}",
                    product.zap, product.lfs
                ));
            }
        } else {
            positions.insert(identity, unique.len());
            unique.push(product);
        }
    }
    Ok(unique)
}

const POINTWISE_PARTIAL_ABS_TOLERANCE_B: f64 = 1e-12;
const COLLAPSED_PARTIAL_ABS_TOLERANCE_B: f64 = 1e-14;
const PARTIAL_REL_TOLERANCE: f64 = 5e-10;

fn state_partial_tolerance(total: f64, peak_total: f64, absolute: f64) -> f64 {
    absolute.max(PARTIAL_REL_TOLERANCE * total.max(peak_total))
}

/// The frozen P18b standard envelope: an emitted mutually-exclusive state sum
/// may exceed the runtime total by at most this relative amount and remain
/// eligible for common-factor reconciliation.
const STANDARD_ENVELOPE_REL: f64 = 0.001;
/// For an exactly-zero runtime total, standard compatibility is this absolute
/// state-sum bound in barns.
const STANDARD_ZERO_TOTAL_ABS_B: f64 = 0.001;
/// P25 Amendment B: an emitted-sum excess below this absolute bound in barns
/// is physically weightless and is accepted as `floor_reconciled` without
/// scaling (TENDL prints 1e-20 barn as an effectively-zero floor; observed
/// artifacts carry absolute excesses near that value).
const FLOOR_EXCESS_ABS_B: f64 = 1e-15;
/// P25 Amendment B: the secondary reconciliation envelope, applied only to a
/// ZAP whose MF=10 state sum is proven consistent with the MF=3 total at every
/// declared product gridpoint — i.e. a demonstrated grid-density interpolation
/// artifact, never a declared-value contradiction.
const INTERP_ARTIFACT_ENVELOPE_REL: f64 = 0.03;
/// P25 Amendment B: an MF=8 ELFS that conflicts with the QM-QI-derived
/// excitation by at most this many eV resolves to the evaluated ELFS
/// (`elfs_qm_qi_conflict_resolved`); a larger conflict fails closed.
const ELFS_PRECEDENCE_BOUND_EV: f64 = 1.0e3;

/// Raw-section audit counters for the retired P18 stress gate. Excesses beyond
/// the stress tolerance are reported as source diagnostics; nonfinite or
/// negative values remain hard errors.
#[derive(Default)]
struct SourcePartialAudit {
    partial_excesses: usize,
    sum_excesses: usize,
    max_relative_excess: f64,
}

impl SourcePartialAudit {
    fn observe(&mut self, is_sum: bool, partial: f64, total: f64) {
        if is_sum {
            self.sum_excesses += 1;
        } else {
            self.partial_excesses += 1;
        }
        self.max_relative_excess = self.max_relative_excess.max(if total > 0.0 {
            (partial - total) / total
        } else {
            f64::INFINITY
        });
    }
    fn excesses(&self) -> usize {
        self.partial_excesses + self.sum_excesses
    }
}

/// Returns `Ok(true)` when the value exceeds the retired P18 stress tolerance.
/// Nonfinite or negative data remains an error.
fn classify_state_partial_value(
    mt: i32,
    context: std::fmt::Arguments<'_>,
    identity: std::fmt::Arguments<'_>,
    partial: f64,
    total: f64,
    peak_total: f64,
    absolute_tolerance: f64,
) -> Result<bool, String> {
    if !partial.is_finite() || partial < 0.0 {
        return Err(format!(
            "MT{mt}/MF=10 {context} {identity} is nonfinite or negative ({partial:.17e} barn)"
        ));
    }
    if !total.is_finite() || total < 0.0 {
        return Err(format!(
            "MT{mt}/MF=3 {context} total is nonfinite or negative ({total:.17e} barn)"
        ));
    }
    Ok(partial > total + state_partial_tolerance(total, peak_total, absolute_tolerance))
}

fn audit_pointwise_state_partials(
    mt: i32,
    total: &Tabulated,
    products: &[&ProductTable],
) -> Result<SourcePartialAudit, String> {
    if total
        .y
        .iter()
        .any(|value| !value.is_finite() || *value < 0.0)
    {
        return Err(format!(
            "MT{mt}/MF=3 total contains a nonfinite or negative cross section"
        ));
    }
    let state_products: Vec<_> = products
        .iter()
        .copied()
        .filter(|product| product.zap >= 0)
        .collect();
    if state_products.is_empty() {
        return Ok(SourcePartialAudit::default());
    }
    let peak_total = total.y.iter().copied().fold(0.0, f64::max);
    let mut energies = Vec::with_capacity(
        total.x.len()
            + state_products
                .iter()
                .map(|product| product.table.x.len())
                .sum::<usize>(),
    );
    energies.extend(total.x.iter().copied());
    for product in &state_products {
        energies.extend(product.table.x.iter().copied());
    }
    energies.sort_by(f64::total_cmp);
    energies.dedup_by(|left, right| *left == *right);
    let product_zaps: BTreeSet<_> = state_products.iter().map(|product| product.zap).collect();

    let mut audit = SourcePartialAudit::default();
    for energy in energies {
        for (side, left_limit) in [("right", false), ("left", true)] {
            let evaluate = |table: &Tabulated| {
                if left_limit {
                    table.evaluate_left_limit(energy)
                } else {
                    table.evaluate(energy)
                }
            };
            let total_value = evaluate(total)?;
            for product in &state_products {
                let partial_value = evaluate(&product.table)?;
                if classify_state_partial_value(
                    mt,
                    format_args!("pointwise {side} at {energy:.17e} eV"),
                    format_args!("ZAP={}/LFS={} partial", product.zap, product.lfs),
                    partial_value,
                    total_value,
                    peak_total,
                    POINTWISE_PARTIAL_ABS_TOLERANCE_B,
                )? {
                    audit.observe(false, partial_value, total_value);
                }
            }
            for zap in &product_zaps {
                let mut sum = 0.0;
                for product in state_products.iter().filter(|product| product.zap == *zap) {
                    sum += evaluate(&product.table)?;
                }
                if classify_state_partial_value(
                    mt,
                    format_args!("pointwise {side} at {energy:.17e} eV"),
                    format_args!("ZAP={zap} mutually-exclusive state sum"),
                    sum,
                    total_value,
                    peak_total,
                    POINTWISE_PARTIAL_ABS_TOLERANCE_B,
                )? {
                    audit.observe(true, sum, total_value);
                }
            }
        }
    }
    Ok(audit)
}

/// P25 Amendment B mechanism gate for the interpolation-artifact envelope:
/// returns the ZAPs whose MF=10 mutually-exclusive state sum is consistent
/// with the MF=3 total at *every* declared product gridpoint (the frozen
/// standard envelope, or the zero-total absolute bound where the total is
/// zero). For such a ZAP a collapsed group excess can only arise between
/// declared points — the discriminator that separates grid-density artifacts
/// from declared-value inconsistencies. Any failure excludes the ZAP; it is
/// not itself a construction error.
fn declared_consistent_zaps(
    mt: i32,
    total: &Tabulated,
    products: &[&ProductTable],
) -> Result<BTreeSet<i32>, String> {
    let state_products: Vec<_> = products
        .iter()
        .copied()
        .filter(|product| product.zap >= 0)
        .collect();
    let zaps: BTreeSet<i32> = state_products.iter().map(|product| product.zap).collect();
    let mut qualified = BTreeSet::new();
    'zap: for zap in zaps {
        let members: Vec<_> = state_products
            .iter()
            .filter(|product| product.zap == zap)
            .collect();
        let mut gridpoints: Vec<f64> = members
            .iter()
            .flat_map(|product| product.table.x.iter().copied())
            .collect();
        gridpoints.sort_by(f64::total_cmp);
        gridpoints.dedup();
        for energy in gridpoints {
            let total_value = total.evaluate(energy)?;
            let mut sum = 0.0;
            for product in &members {
                sum += product.table.evaluate(energy)?;
            }
            if !sum.is_finite() || sum < 0.0 || !total_value.is_finite() || total_value < 0.0 {
                return Err(format!(
                    "MT{mt} declared-gridpoint consistency evaluation produced a nonfinite or negative value at {energy:.17e} eV"
                ));
            }
            let consistent = if total_value > 0.0 {
                sum - total_value <= STANDARD_ENVELOPE_REL * total_value
            } else {
                // the mechanism gate must agree with the decimal
                // discriminator: a declared ordinate carrying more than
                // floor-magnitude strength where the total is zero is a
                // zero-total source defect, not an interpolation artifact
                sum <= FLOOR_EXCESS_ABS_B
            };
            if !consistent {
                continue 'zap;
            }
        }
        qualified.insert(zap);
    }
    Ok(qualified)
}

fn collapse_mf10_products<'a>(
    groups: &GroupStructure,
    mt: i32,
    products: &[&'a ProductTable],
) -> Result<Vec<(&'a ProductTable, Vec<f64>)>, String> {
    products
        .iter()
        .map(|product| {
            Ok((
                *product,
                checked_collapse(groups, &product.table, &format!("MT{mt}/MF=10"))?,
            ))
        })
        .collect()
}

fn audit_collapsed_state_partials(
    mt: i32,
    total: &[f64],
    products: &[(&ProductTable, Vec<f64>)],
) -> Result<SourcePartialAudit, String> {
    if total.iter().any(|value| !value.is_finite() || *value < 0.0) {
        return Err(format!(
            "MT{mt}/MF=3 collapsed total contains a nonfinite or negative cross section"
        ));
    }
    let state_products: Vec<_> = products
        .iter()
        .filter(|(product, _)| product.zap >= 0)
        .collect();
    let product_zaps: BTreeSet<_> = state_products
        .iter()
        .map(|(product, _)| product.zap)
        .collect();
    let peak_total = total.iter().copied().fold(0.0, f64::max);
    let mut audit = SourcePartialAudit::default();
    for (product, partial) in &state_products {
        if partial.len() != total.len() {
            return Err(format!(
                "MT{mt}/MF=10 collapsed ZAP={}/LFS={} group count differs from MF=3",
                product.zap, product.lfs
            ));
        }
        for (group, (&partial_value, &total_value)) in partial.iter().zip(total).enumerate() {
            if classify_state_partial_value(
                mt,
                format_args!("collapsed group {group}"),
                format_args!("ZAP={}/LFS={} partial", product.zap, product.lfs),
                partial_value,
                total_value,
                peak_total,
                COLLAPSED_PARTIAL_ABS_TOLERANCE_B,
            )? {
                audit.observe(false, partial_value, total_value);
            }
        }
    }
    for zap in product_zaps {
        for (group, &total_value) in total.iter().enumerate() {
            let mut sum = 0.0;
            for (_, partial) in state_products
                .iter()
                .filter(|(product, _)| product.zap == zap)
            {
                sum += partial[group];
            }
            if classify_state_partial_value(
                mt,
                format_args!("collapsed group {group}"),
                format_args!("ZAP={zap} mutually-exclusive state sum"),
                sum,
                total_value,
                peak_total,
                COLLAPSED_PARTIAL_ABS_TOLERANCE_B,
            )? {
                audit.observe(true, sum, total_value);
            }
        }
    }
    Ok(audit)
}

/// Report for one emitted mutually-exclusive (MT, ZAP) state vector compared
/// group-by-group against the runtime total.
#[derive(Debug, Default)]
struct RuntimeReconciliation {
    checked: usize,
    scaled: usize,
    floor_reconciled: usize,
    interp_reconciled: usize,
    min_scale: f64,
    max_relative_excess: f64,
    ulp_corrections: u64,
}

/// Applies the frozen P18b runtime rule to one emitted (MT, ZAP, MF) state
/// vector: sums at or below the runtime total stay byte-for-byte unchanged;
/// excesses inside the standard envelope are scaled by the common factor T/S
/// with at most a one-ULP downward correction on the largest row; anything
/// further fails construction closed. `strict_states` rejects every excess.
///
/// P25 Amendment B adds two mechanism-gated relaxations, in order: an excess
/// below `FLOOR_EXCESS_ABS_B` barn is accepted unchanged as `floor_reconciled`
/// (physically weightless), and an `interp_qualified` ZAP — proven consistent
/// at every declared product gridpoint — reconciles under the 0.03
/// interpolation-artifact envelope (`interp_artifact_reconciled`).
#[allow(clippy::too_many_arguments)]
fn reconcile_emitted_states(
    mt: i32,
    lmf: i32,
    zap: i32,
    states: &mut [&mut Vec<f64>],
    total: &[f64],
    strict_states: bool,
    interp_qualified: bool,
    mat: i32,
    za: i32,
    source_sha256: &str,
) -> Result<RuntimeReconciliation, String> {
    let mut report = RuntimeReconciliation {
        min_scale: f64::INFINITY,
        ..Default::default()
    };
    for (group, &comparator) in total.iter().enumerate() {
        if !comparator.is_finite() || comparator < 0.0 {
            return Err(format!(
                "MT{mt}/MF={lmf} runtime total group {group} is nonfinite or negative ({comparator:.17e} barn)"
            ));
        }
        let mut sum = 0.0;
        for state in states.iter() {
            let value = state[group];
            if !value.is_finite() || value < 0.0 {
                return Err(format!(
                    "MT{mt}/MF={lmf} ZAP={zap} group {group} emitted state value is nonfinite or negative ({value:.17e} barn)"
                ));
            }
            sum += value;
        }
        report.checked += 1;
        if sum <= comparator {
            continue;
        }
        if !strict_states && sum - comparator <= FLOOR_EXCESS_ABS_B {
            report.floor_reconciled += 1;
            continue;
        }
        let standard_ok = if comparator > 0.0 {
            sum - comparator <= STANDARD_ENVELOPE_REL * comparator
        } else {
            sum <= STANDARD_ZERO_TOTAL_ABS_B
        };
        let envelope = if interp_qualified {
            INTERP_ARTIFACT_ENVELOPE_REL
        } else {
            STANDARD_ENVELOPE_REL
        };
        let compatible = if comparator > 0.0 {
            sum - comparator <= envelope * comparator
        } else {
            standard_ok
        };
        let relative_excess = if comparator > 0.0 {
            (sum - comparator) / comparator
        } else {
            f64::INFINITY
        };
        if strict_states || !compatible {
            let rule = if strict_states {
                "the strict state-conservation option rejects every emitted sum above the runtime total"
            } else if interp_qualified {
                "outside the frozen 0.001 standard envelope and the mechanism-gated 0.03 interpolation-artifact envelope"
            } else {
                "outside the frozen 0.001 standard envelope"
            };
            return Err(format!(
                "MT{mt}/MF={lmf} ZAP={zap} group {group}: emitted state sum {sum:.17e} barn exceeds runtime total {comparator:.17e} barn by relative excess {relative_excess:.6e} ({rule}); MAT={mat} ZA={za} source_sha256={source_sha256}; construction fails closed and no state row is emitted, scaled or relabeled"
            ));
        }
        if !standard_ok {
            report.interp_reconciled += 1;
        }
        let scale = comparator / sum;
        for state in states.iter_mut() {
            state[group] *= scale;
        }
        let mut corrected: f64 = states.iter().map(|state| state[group]).sum();
        let mut steps = 0_u64;
        while corrected > comparator {
            let mut largest = 0_usize;
            for (index, state) in states.iter().enumerate() {
                if state[group] > states[largest][group] {
                    largest = index;
                }
            }
            let value = states[largest][group];
            if value <= 0.0 {
                break;
            }
            states[largest][group] = f64::from_bits(value.to_bits() - 1);
            steps += 1;
            if steps > 4096 {
                return Err(format!(
                    "MT{mt}/MF={lmf} ZAP={zap} group {group}: state-sum closure did not converge within 4096 one-ULP corrections"
                ));
            }
            corrected = states.iter().map(|state| state[group]).sum();
        }
        report.scaled += 1;
        report.ulp_corrections += steps;
        report.min_scale = report.min_scale.min(scale);
        report.max_relative_excess = report.max_relative_excess.max(relative_excess);
    }
    Ok(report)
}

fn validate_descriptors(
    products: &[ProductRef],
    lmf: i32,
    actual: &BTreeSet<(i32, i32)>,
) -> Result<(), String> {
    let declared = descriptor_set(products, lmf)?;
    if !declared.is_empty() && &declared != actual {
        return Err(format!(
            "MF=8/LMF={lmf} products {declared:?} conflict with MF={lmf} products {actual:?}"
        ));
    }
    Ok(())
}

fn excitation_tolerance(left_eV: f64, right_eV: f64) -> f64 {
    1.0_f64.max(5e-6 * left_eV.abs().max(right_eV.abs()))
}

impl RawProductState {
    fn from_descriptor(descriptor: &ProductRef) -> Self {
        Self {
            raw_lfs: descriptor.lfs,
            elfs_eV: Some(descriptor.elfs_ev),
            qm_eV: None,
            qi_eV: None,
        }
    }

    fn from_table(descriptor: Option<&ProductRef>, table: &ProductTable) -> Result<Self, String> {
        let state = Self {
            raw_lfs: table.lfs,
            elfs_eV: descriptor.map(|value| value.elfs_ev),
            qm_eV: Some(table.qm_ev),
            qi_eV: Some(table.qi_ev),
        };
        state.excitation_eV()?;
        Ok(state)
    }

    fn qm_minus_qi_eV(self) -> Result<Option<f64>, String> {
        match (self.qm_eV, self.qi_eV) {
            (Some(qm), Some(qi)) => {
                let value = qm - qi;
                if value < -excitation_tolerance(0.0, value) {
                    return Err(format!(
                        "LFS={} has negative QM-QI excitation {value:.17e} eV",
                        self.raw_lfs
                    ));
                }
                Ok(Some(value.max(0.0)))
            }
            (None, None) => Ok(None),
            _ => Err("product state has only one of QM/QI".into()),
        }
    }

    /// P25 Amendment B: when MF=8 ELFS and the QM-QI-derived excitation
    /// disagree beyond the consistency tolerance but by at most
    /// `ELFS_PRECEDENCE_BOUND_EV`, the evaluated ELFS is authoritative; the
    /// conflict is ledgered by the caller as `elfs_qm_qi_conflict_resolved`.
    /// A conflict beyond the bound remains a construction failure.
    fn excitation_eV(self) -> Result<Option<f64>, String> {
        let q_excitation = self.qm_minus_qi_eV()?;
        if let Some(delta) = self.elfs_qm_qi_delta_eV() {
            if delta > ELFS_PRECEDENCE_BOUND_EV {
                return Err(format!(
                    "LFS={} MF=8 ELFS={:.17e} eV conflicts with QM-QI={:.17e} eV beyond the {:.0e} eV precedence bound",
                    self.raw_lfs,
                    self.elfs_eV.unwrap_or_default(),
                    q_excitation.unwrap_or_default(),
                    ELFS_PRECEDENCE_BOUND_EV
                ));
            }
        }
        Ok(self.elfs_eV.or(q_excitation))
    }

    /// The absolute |ELFS − (QM−QI)| delta when both exist, or None. A delta
    /// inside `excitation_tolerance` is ordinary rounding and is not a
    /// conflict; anything larger must be resolved or fail.
    fn elfs_qm_qi_delta_eV(self) -> Option<f64> {
        let elfs = self.elfs_eV?;
        let derived = self.qm_minus_qi_eV().ok().flatten()?;
        Some((elfs - derived).abs())
    }

    /// Some(delta) only for a genuine conflict — beyond the consistency
    /// tolerance — so callers can ledger `elfs_qm_qi_conflict_resolved`.
    fn elfs_qm_qi_conflict_eV(self) -> Option<f64> {
        let elfs = self.elfs_eV?;
        let derived = self.qm_minus_qi_eV().ok().flatten()?;
        let delta = (elfs - derived).abs();
        (delta > excitation_tolerance(elfs, derived)).then_some(delta)
    }
}

fn descriptor_for(products: &[ProductRef], lmf: i32, zap: i32, lfs: i32) -> Option<&ProductRef> {
    products
        .iter()
        .find(|product| product.lmf == lmf && product.zap == zap && product.lfs == lfs)
}

type MatchedMf6<'a> = (Vec<(&'a Mf6Product, ProductRef)>, bool);

fn match_mf6<'a>(
    products: &[ProductRef],
    yields: &'a [Mf6Product],
) -> Result<MatchedMf6<'a>, String> {
    let declared: Vec<_> = products
        .iter()
        .filter(|product| product.lmf == 6)
        .copied()
        .collect();
    let mut used = vec![false; yields.len()];
    let mut matched = Vec::with_capacity(declared.len());
    let mut omitted_neutron = false;
    for descriptor in declared {
        let index = yields
            .iter()
            .enumerate()
            .position(|(index, product)| !used[index] && product.zap == descriptor.zap)
            .ok_or_else(|| {
                format!(
                    "MF=8 declares ZAP={}/LFS={} with no matching MF=6 yield",
                    descriptor.zap, descriptor.lfs
                )
            })?;
        used[index] = true;
        if descriptor.zap == 1 {
            omitted_neutron = true;
        } else if descriptor.zap != 0 {
            matched.push((&yields[index], descriptor));
        }
    }
    for (used, product) in used.into_iter().zip(yields) {
        if !used && product.zap != 0 {
            return Err(format!(
                "MF=6 ZAP={} yield has no matching MF=8/LMF=6 declaration",
                product.zap
            ));
        }
    }
    Ok((matched, omitted_neutron))
}

fn residual_product(
    target_za: i32,
    projectile: Projectile,
    neutron_delta: (i32, i32),
) -> Option<i32> {
    let target = (target_za / 1000, target_za % 1000);
    let incident = projectile.za();
    let z = target.0 + neutron_delta.0 + incident.0;
    let a = target.1 + neutron_delta.1 + incident.1 - 1;
    (z > 0 && a > 0 && a >= z).then_some(z * 1000 + a)
}

fn remap_eaf_levels(rows: &mut [BuiltRow], ledger: &mut Vec<String>) {
    let mut levels: BTreeMap<(i32, i32), BTreeSet<i32>> = BTreeMap::new();
    for row in rows.iter().filter(|row| row.lfs > 0) {
        levels.entry((row.mt, row.zap)).or_default().insert(row.lfs);
    }
    for ((mt, zap), values) in levels {
        let original: Vec<i32> = values.into_iter().collect();
        let canonical: Vec<i32> = (1..=original.len() as i32).collect();
        if original == canonical {
            continue;
        }
        let mapping: BTreeMap<i32, i32> = original
            .iter()
            .copied()
            .zip(canonical.iter().copied())
            .collect();
        for row in rows
            .iter_mut()
            .filter(|row| row.mt == mt && row.zap == zap && row.lfs > 0)
        {
            row.lfs = mapping[&row.lfs];
        }
        ledger.push(format!(
            "MT{mt}->{zap}: LFS {original:?} remapped to decay isomers {canonical:?}"
        ));
    }
}

#[derive(Clone, Debug, Serialize)]
struct CatalogObservation {
    lis: i32,
    elis_eV: f64,
    file: String,
    source_sha256: String,
}

#[derive(Clone, Debug, Serialize)]
struct CatalogState {
    za: i32,
    liso: i32,
    representative: CatalogObservation,
    evaluations: Vec<CatalogObservation>,
    decision: &'static str,
}

type StateCatalog = BTreeMap<i32, Vec<CatalogState>>;

fn build_state_catalog(sources: &[BuiltSource]) -> Result<StateCatalog, String> {
    let mut observations: BTreeMap<(i32, i32), Vec<CatalogObservation>> = BTreeMap::new();
    for source in sources {
        for target in &source.targets {
            let index = &target.index;
            if !index.elis_eV.is_finite() || index.elis_eV < 0.0 || index.lis < 0 || index.liso < 0
            {
                return Err(format!(
                    "target ZA={}/LISO={} has invalid LIS={} or ELIS={} eV",
                    index.za, index.liso, index.lis, index.elis_eV
                ));
            }
            if (index.liso == 0 && index.lis != 0)
                || (index.liso > 0 && (index.lis == 0 || index.liso > index.lis))
            {
                return Err(format!(
                    "target ZA={}/LISO={} has inconsistent physical LIS={}",
                    index.za, index.liso, index.lis
                ));
            }
            if index.liso == 0 && index.elis_eV.abs() > excitation_tolerance(0.0, index.elis_eV) {
                return Err(format!(
                    "ground target ZA={} has nonzero ELIS={:.17e} eV",
                    index.za, index.elis_eV
                ));
            }
            observations
                .entry((index.za, index.liso))
                .or_default()
                .push(CatalogObservation {
                    lis: index.lis,
                    elis_eV: index.elis_eV,
                    file: index.file.clone(),
                    source_sha256: index.source_sha256.clone(),
                });
        }
    }

    let mut catalog: StateCatalog = BTreeMap::new();
    for ((za, liso), mut evaluations) in observations {
        evaluations.sort_by(|left, right| {
            left.elis_eV
                .total_cmp(&right.elis_eV)
                .then_with(|| left.lis.cmp(&right.lis))
                .then_with(|| left.file.cmp(&right.file))
                .then_with(|| left.source_sha256.cmp(&right.source_sha256))
        });
        let low = evaluations
            .first()
            .ok_or("state catalog observation group is empty")?;
        let high = evaluations
            .last()
            .ok_or("state catalog observation group is empty")?;
        if (high.elis_eV - low.elis_eV).abs() > excitation_tolerance(low.elis_eV, high.elis_eV) {
            return Err(format!(
                "duplicate target ZA={za}/LISO={liso} has conflicting ELIS values {:.17e} eV in '{}' and {:.17e} eV in '{}'",
                low.elis_eV, low.file, high.elis_eV, high.file
            ));
        }
        let representative = low.clone();
        let decision = if evaluations.len() == 1 {
            "unique_evaluation"
        } else {
            "duplicate_evaluations_agree"
        };
        catalog.entry(za).or_default().push(CatalogState {
            za,
            liso,
            representative,
            evaluations,
            decision,
        });
    }
    for states in catalog.values_mut() {
        states.sort_by_key(|state| state.liso);
    }
    Ok(catalog)
}

fn map_product_states(target: &mut BuiltTarget, catalog: &StateCatalog) -> Result<(), String> {
    let mut mappings = Vec::new();
    for row in &mut target.rows {
        let Some(raw) = row.raw_state else {
            continue;
        };
        let original_zap = row.zap;
        let original_lmf = row.lmf;
        let q_excitation = raw.qm_minus_qi_eV()?;
        let excitation = raw.excitation_eV()?;
        let (canonical, catalog_state, decision) = if raw.raw_lfs == 0 {
            if excitation.is_some_and(|value| value.abs() > excitation_tolerance(0.0, value)) {
                return Err(format!(
                    "MT{} product ZAP={} declares ground LFS=0 at excitation {:.17e} eV",
                    row.mt,
                    original_zap,
                    excitation.unwrap_or_default()
                ));
            }
            let ground = catalog
                .get(&original_zap)
                .and_then(|states| states.iter().find(|state| state.liso == 0));
            (Some(0), ground, "ground_lfs0")
        } else if raw.raw_lfs == 98 {
            (None, None, "unspecified_lfs98_to_leakage")
        } else if let Some(excitation_eV) = excitation {
            let matches: Vec<&CatalogState> = catalog
                .get(&original_zap)
                .into_iter()
                .flatten()
                .filter(|state| {
                    state.liso > 0
                        && (state.representative.elis_eV - excitation_eV).abs()
                            <= excitation_tolerance(state.representative.elis_eV, excitation_eV)
                })
                .collect();
            match matches.as_slice() {
                [] => (None, None, "no_catalog_excitation_match_to_leakage"),
                [state] => (Some(state.liso), Some(*state), "catalog_excitation_match"),
                _ => {
                    return Err(format!(
                        "MT{} product ZAP={}/raw LFS={} at {:.17e} eV matches multiple catalog states {:?}",
                        row.mt,
                        original_zap,
                        raw.raw_lfs,
                        excitation_eV,
                        matches
                            .iter()
                            .map(|state| {
                                (
                                    state.liso,
                                    state.representative.lis,
                                    state.representative.elis_eV,
                                )
                            })
                            .collect::<Vec<_>>()
                    ));
                }
            }
        } else {
            (None, None, "missing_excitation_to_leakage")
        };

        if let Some(canonical_liso) = canonical {
            row.lfs = canonical_liso;
            if raw.raw_lfs != canonical_liso {
                target.index.ledger.push(format!(
                    "MT{}->{}: physical excitation mapped raw LFS {} to LISO {}",
                    row.mt, original_zap, raw.raw_lfs, canonical_liso
                ));
            }
        } else {
            row.zap = 0;
            row.lfs = 0;
            row.lmf = -3;
            target.index.ledger.push(format!(
                "MT{}->{}: raw LFS {} is not an auditable decay isomer ({decision}); production retained as explicit leakage",
                row.mt, original_zap, raw.raw_lfs
            ));
        }
        mappings.push(StateMapping {
            mt: row.mt,
            zap: original_zap,
            lmf: original_lmf,
            raw_lfs: raw.raw_lfs,
            elfs_eV: raw.elfs_eV,
            qm_eV: raw.qm_eV,
            qi_eV: raw.qi_eV,
            qm_minus_qi_eV: q_excitation,
            mapping_excitation_eV: excitation,
            canonical_liso: canonical,
            catalog_lis: catalog_state.map(|state| state.representative.lis),
            catalog_elis_eV: catalog_state.map(|state| state.representative.elis_eV),
            catalog_file: catalog_state.map(|state| state.representative.file.clone()),
            catalog_source_sha256: catalog_state
                .map(|state| state.representative.source_sha256.clone()),
            catalog_evaluations: catalog_state.map(|state| state.evaluations.len()),
            tolerance_eV: excitation.map(|value| {
                excitation_tolerance(
                    value,
                    catalog_state
                        .map(|state| state.representative.elis_eV)
                        .unwrap_or(value),
                )
            }),
            excitation_delta_eV: excitation
                .zip(catalog_state.map(|state| state.representative.elis_eV))
                .map(|(value, catalog_value)| value - catalog_value),
            decision: decision.into(),
        });
    }
    target.index.state_mappings = mappings;
    Ok(())
}

fn build_evaluation(
    evaluation: Evaluation,
    format: LibraryFormat,
    file: &str,
    source_sha256: &str,
    settings: EvaluationBuildSettings<'_>,
    products_by_mt: &BTreeMap<i32, (i32, i32)>,
) -> Result<BuiltTarget, String> {
    let EvaluationBuildSettings {
        groups,
        temperature_K,
        grid_density,
        strict_states,
    } = settings;
    let metadata = &evaluation.metadata;
    let mut ledger = Vec::new();
    if metadata.projectile != Projectile::Neutron && temperature_K != 0.0 {
        return Err(format!(
            "{} target requires 0 K, requested {temperature_K} K",
            metadata.projectile.name()
        ));
    }
    let mut processed: BTreeMap<i32, ProcessedReaction> = BTreeMap::new();
    if format != LibraryFormat::Eaf && !evaluation.mf2_sections.is_empty() {
        if metadata.projectile != Projectile::Neutron {
            return Err(format!(
                "MF=2 resonance processing is unsupported for {} evaluations",
                metadata.projectile.name()
            ));
        }
        if metadata.evaluation_temperature_k != 0.0 {
            return Err(format!(
                "raw MF=2 processing requires a 0 K evaluation, got {} K",
                metadata.evaluation_temperature_k
            ));
        }
        if evaluation.mf2_sections != BTreeSet::from([151]) {
            return Err(format!(
                "unsupported MF=2 sections {:?}; expected only MT=151",
                evaluation.mf2_sections
            ));
        }
        let resonance = evaluation
            .resonance
            .as_ref()
            .ok_or("MF=2/MT=151 was declared without parsed resonance parameters")?;
        let omitted_fission_totals = omitted_fission_total_width_count(resonance);
        if omitted_fission_totals > 0 {
            ledger.push(format!(
                "MF=2: {omitted_fission_totals} LRX=0 Breit-Wigner GT fields omit GF; effective total widths reconstructed from GN+GG+GF per P10 Amendment D"
            ));
        }
        for isotope in &resonance.isotopes {
            for range in &isotope.ranges {
                if matches!(range.data, RangeData::RMatrixLimited(_)) {
                    validate_rmatrix_limited(range)
                        .map_err(|error| format!("RML validation: {error}"))?;
                }
            }
        }
        for mt in [18, 102] {
            if has_resonance_contribution(resonance, mt) {
                // ENDF-6 permits File 3 background cross sections in resonance ranges but does not require them.
                // Represent an absent optional background explicitly so resonance-only reactions remain complete.
                let zero_background = (!evaluation.mf3.contains_key(&mt)).then(|| Tabulated {
                    interpolation: vec![(2, 2)],
                    x: vec![
                        groups.boundaries_ev[0],
                        groups.boundaries_ev[groups.groups()],
                    ],
                    y: vec![0.0, 0.0],
                });
                let background = evaluation
                    .mf3
                    .get(&mt)
                    .or(zero_background.as_ref())
                    .expect("an MF=3 or explicit zero background is present");
                if zero_background.is_some() {
                    ledger.push(format!(
                        "MT{mt}: optional MF=3 resonance background absent; explicit zero background used"
                    ));
                }
                let reaction = process_reaction(
                    resonance,
                    background,
                    groups,
                    mt,
                    temperature_K,
                    grid_density,
                )
                .map_err(|error| format!("MT{mt} resonance processing: {error}"))?;
                ledger.push(format!(
                    "MT{mt}: raw 0 K MF=2+MF=3 reconstructed on {} points and processed to {} K on {} points",
                    reaction.certificate.zero_k_points,
                    temperature_K,
                    reaction.certificate.output_points
                ));
                for line in &reaction.certificate.ultra_narrow_lines {
                    ledger.push(format!(
                        "MT{mt}: ultra-narrow ZAI={} E={:.17e} eV Gamma={:.17e} eV, Gamma/Gamma_D={:.17e} at {:.17e} K, direct area={:.17e} barn eV, closed area={:.17e} barn eV, abundance-weighted area={:.17e} barn eV, group={}, range-edge={}, core=[{:.17e},{:.17e}] eV",
                        line.isotope_zai,
                        line.energy_ev,
                        line.total_width_ev,
                        line.width_to_doppler_ratio,
                        line.classification_temperature_k,
                        line.direct_area_barn_ev,
                        line.closed_form_area_barn_ev,
                        line.weighted_area_barn_ev,
                        line.affected_group,
                        line.range_edge_decomposition,
                        line.core_low_ev,
                        line.core_high_ev
                    ));
                }
                processed.insert(mt, reaction);
            }
        }
    } else if metadata.evaluation_temperature_k.to_bits() != temperature_K.to_bits() {
        return Err(format!(
            "tabulated evaluation is at {} K but {temperature_K} K was requested and no raw MF=2 reconstruction applies",
            metadata.evaluation_temperature_k
        ));
    }

    let mut rows = Vec::new();
    if format == LibraryFormat::Eaf && !evaluation.mf2_sections.is_empty() {
        ledger.push(format!(
            "EAF processed MF=3 used at its declared temperature; MF=2 sections {:?} are not added a second time",
            evaluation.mf2_sections
        ));
    }
    let mut mts: BTreeSet<i32> = evaluation.mf3.keys().copied().collect();
    mts.extend(evaluation.mf10.keys().copied());
    mts.extend(processed.keys().copied());
    for mt in mts {
        // TENDL declarations are audited even when the reaction is later omitted. EAF keeps its established
        // skip-before-product-validation behavior and initializes these rows only after the skip decision.
        let mut mf10_products = if format == LibraryFormat::Tendl {
            unique_product_tables(
                evaluation.mf10.get(&mt).map(Vec::as_slice).unwrap_or(&[]),
                &format!("MT{mt}/MF=10"),
            )?
        } else {
            Vec::new()
        };
        let mut collapsed_mf10 = if mf10_products.is_empty() {
            Vec::new()
        } else {
            collapse_mf10_products(groups, mt, &mf10_products)?
        };
        if !mf10_products.is_empty() {
            if let Some(raw_total) = evaluation.mf3.get(&mt) {
                // The retired P18 stress gate is retained as a raw-source
                // diagnostic: the two evaluated sections are compared pointwise and
                // collapsed, but excesses are recorded rather than rejected. The
                // runtime gate below applies the frozen P18b envelope to emitted
                // rows against the processed runtime total.
                let pointwise_audit =
                    audit_pointwise_state_partials(mt, raw_total, &mf10_products)?;
                let collapsed_total = checked_collapse(groups, raw_total, &format!("MT{mt}/MF=3"))?;
                let collapsed_audit =
                    audit_collapsed_state_partials(mt, &collapsed_total, &collapsed_mf10)?;
                let excesses = pointwise_audit.excesses() + collapsed_audit.excesses();
                if excesses > 0 {
                    ledger.push(format!(
                    "MT{mt}: raw MF=10-vs-MF=3 audit recorded {excesses} excess(es) beyond the retired P18 stress tolerance ({} pointwise, {} collapsed; max relative excess {:.6e}); kept as source diagnostics",
                    pointwise_audit.excesses(),
                    collapsed_audit.excesses(),
                    pointwise_audit
                        .max_relative_excess
                        .max(collapsed_audit.max_relative_excess)
                ));
                }
            } else if mt == 18 && mf10_products.iter().any(|product| product.zap == -1) {
                ledger.push(
                    "MT18: no MF=3 total; the MF=10 IZAP=-1 total-fission sentinel supplies the permitted runtime comparator"
                        .into(),
                );
            } else {
                // P38: any MT with MF=10 products but no MF=3 total uses its
                // own MF=10 partial sum as the runtime comparator — internal
                // completeness, not an independently anchored total. The
                // P25 Amendment B path for fission generalizes here: ENDF-6
                // does not require an MF=3 section for every MF=10 section,
                // and activation-oriented files lawfully ship production-only
                // channels. The emitted-row envelope still applies against
                // this comparator downstream.
                ledger.push(format!(
                    "MT{mt}: no MF=3 total{}; the MF=10 partial sum supplies the runtime comparator (missing_total_self_comparator: conservation is internal completeness, not anchored to an independent total)",
                    if mt == 18 { " or total-fission sentinel" } else { "" }
                ));
            }
        }
        let has_lmf6 = evaluation
            .mf8
            .get(&mt)
            .is_some_and(|products| products.iter().any(|product| product.lmf == 6));
        if skip_mt(mt, metadata.projectile, has_lmf6) {
            if mt == 5 && evaluation.mf8.contains_key(&mt) {
                ledger.push("MT5 aggregate products omitted for a neutron evaluation".into());
            }
            continue;
        }
        if format != LibraryFormat::Tendl {
            mf10_products = unique_product_tables(
                evaluation.mf10.get(&mt).map(Vec::as_slice).unwrap_or(&[]),
                &format!("MT{mt}/MF=10"),
            )?;
            collapsed_mf10 = collapse_mf10_products(groups, mt, &mf10_products)?;
        }
        let descriptors = evaluation.mf8.get(&mt).map(Vec::as_slice).unwrap_or(&[]);

        if inelastic(mt, metadata.projectile) {
            if mf10_products.iter().any(|product| product.zap < 0) {
                return Err(format!("MT{mt}/MF=10 contains an invalid negative product"));
            }
            let actual: BTreeSet<_> = mf10_products
                .iter()
                .map(|product| (product.zap, product.lfs))
                .collect();
            // Inelastic MF=10 commonly tabulates both return to the ground state and production of a metastable
            // state. Validate the complete declaration before intentionally retaining only transmuting LFS>0 rows.
            validate_descriptors(descriptors, 10, &actual)?;
            let retained: BTreeSet<_> = mf10_products
                .iter()
                .filter(|product| product.lfs > 0)
                .map(|product| (product.zap, product.lfs))
                .collect();
            if retained.is_empty() {
                continue;
            }
            let mut loss = vec![0.0; groups.groups()];
            let mut product_rows = Vec::new();
            for (product, sigma) in collapsed_mf10
                .into_iter()
                .filter(|(product, _)| product.lfs > 0)
            {
                sum_groups(&mut loss, &sigma)?;
                let raw_state = RawProductState::from_table(
                    descriptor_for(descriptors, 10, product.zap, product.lfs),
                    product,
                )?;
                if let Some(delta) = raw_state.elfs_qm_qi_conflict_eV() {
                    ledger.push(format!(
                        "MT{mt} ZAP={}/LFS={}: MF=8 ELFS conflicts with QM-QI by {delta:.6e} eV within the 1 keV precedence bound; resolved to the evaluated ELFS (elfs_qm_qi_conflict_resolved)",
                        product.zap, product.lfs
                    ));
                }
                product_rows.push(BuiltRow {
                    mt,
                    zap: product.zap,
                    lfs: product.lfs,
                    lmf: 10,
                    sigma,
                    raw_state: Some(raw_state),
                });
            }
            rows.push(BuiltRow {
                mt,
                zap: -1,
                lfs: -1,
                lmf: 0,
                sigma: loss,
                raw_state: None,
            });
            rows.extend(product_rows);
            ledger.push(format!(
                "MT{mt}: retained {} metastable inelastic product channel(s)",
                retained.len()
            ));
            continue;
        }

        let total = if let Some(reaction) = processed.get(&mt) {
            checked_groups(reaction.collapse(groups)?, &format!("MT{mt}/MF=2+MF=3"))?
        } else if let Some(table) = evaluation.mf3.get(&mt) {
            checked_collapse(groups, table, &format!("MT{mt}/MF=3"))?
        } else {
            if !evaluation.mf10.contains_key(&mt) {
                return Err(format!("MT{mt} has neither MF=3 nor MF=10 data"));
            }
            if let Some((_, total_fission)) = collapsed_mf10
                .iter_mut()
                .find(|(product, _)| product.zap == -1)
            {
                std::mem::take(total_fission)
            } else {
                let mut total = vec![0.0; groups.groups()];
                for (_, partial) in collapsed_mf10
                    .iter()
                    .filter(|(product, _)| product.zap >= 0)
                {
                    sum_groups(&mut total, partial)?;
                }
                total
            }
        };
        rows.push(BuiltRow {
            mt,
            zap: -1,
            lfs: -1,
            lmf: 0,
            sigma: total.clone(),
            raw_state: None,
        });
        let mut done = HashSet::new();

        if evaluation.mf10.contains_key(&mt) {
            let total_fission = mf10_products
                .iter()
                .filter(|product| product.zap == -1)
                .count();
            if total_fission > 1 || (total_fission == 1 && mt != 18) {
                return Err(format!(
                    "MT{mt}/MF=10 contains an invalid total-fission sentinel"
                ));
            }
            if total_fission == 1 {
                ledger.push(
                    "MT18: MF=10 IZAP=-1 total-fission sentinel validated and omitted as an inventory product"
                        .into(),
                );
            }
            let actual: BTreeSet<_> = mf10_products
                .iter()
                .filter(|product| product.zap >= 0)
                .map(|product| (product.zap, product.lfs))
                .collect();
            validate_descriptors(descriptors, 10, &actual)?;
            // Amendment B mechanism gate: the secondary envelope is available
            // only to a ZAP whose declared product gridpoints are all
            // consistent with the raw MF=3 total — proven grid-density
            // interpolation artifacts, never declared-value contradictions.
            let interp_zaps: BTreeSet<i32> = match evaluation.mf3.get(&mt) {
                Some(raw_total) if !processed.contains_key(&mt) => {
                    declared_consistent_zaps(mt, raw_total, &mf10_products)?
                }
                _ => BTreeSet::new(),
            };
            let mut vectors: BTreeMap<i32, Vec<&mut Vec<f64>>> = BTreeMap::new();
            for (product, sigma) in collapsed_mf10.iter_mut() {
                if product.zap >= 0 {
                    vectors.entry(product.zap).or_default().push(sigma);
                }
            }
            for (zap, mut states) in vectors {
                let report = reconcile_emitted_states(
                    mt,
                    10,
                    zap,
                    states.as_mut_slice(),
                    &total,
                    strict_states,
                    interp_zaps.contains(&zap),
                    metadata.mat,
                    metadata.za,
                    source_sha256,
                )?;
                if report.floor_reconciled > 0 {
                    ledger.push(format!(
                        "MT{mt}/MF=10 ZAP={zap}: emitted state sum exceeded the runtime total by a physically weightless amount in {} of {} group(s); accepted unchanged as floor_reconciled (absolute excess below 1e-15 barn)",
                        report.floor_reconciled,
                        report.checked
                    ));
                }
                if report.interp_reconciled > 0 {
                    ledger.push(format!(
                        "MT{mt}/MF=10 ZAP={zap}: emitted state sum exceeded the runtime total beyond the standard envelope in {} of {} group(s); scaled by the common factor T/S under the mechanism-gated 0.03 interpolation-artifact envelope (interp_artifact_reconciled; declared product gridpoints all consistent with MF=3)",
                        report.interp_reconciled,
                        report.checked
                    ));
                }
                if report.scaled > 0 {
                    ledger.push(format!(
                        "MT{mt}/MF=10 ZAP={zap}: emitted state sum exceeded the runtime total in {} of {} group(s); scaled by the common factor T/S (min scale {:.6e}, max relative excess {:.6e}, {} one-ULP closure correction(s))",
                        report.scaled,
                        report.checked,
                        report.min_scale,
                        report.max_relative_excess,
                        report.ulp_corrections
                    ));
                }
            }
            for (product, sigma) in collapsed_mf10
                .into_iter()
                .filter(|(product, _)| product.zap >= 0)
            {
                if !done.insert((product.zap, product.lfs)) {
                    return Err(format!(
                        "MT{mt} product ZAP={}/LFS={} has conflicting definitions",
                        product.zap, product.lfs
                    ));
                }
                let raw_state = RawProductState::from_table(
                    descriptor_for(descriptors, 10, product.zap, product.lfs),
                    product,
                )?;
                if let Some(delta) = raw_state.elfs_qm_qi_conflict_eV() {
                    ledger.push(format!(
                        "MT{mt} ZAP={}/LFS={}: MF=8 ELFS conflicts with QM-QI by {delta:.6e} eV within the 1 keV precedence bound; resolved to the evaluated ELFS (elfs_qm_qi_conflict_resolved)",
                        product.zap, product.lfs
                    ));
                }
                rows.push(BuiltRow {
                    mt,
                    zap: product.zap,
                    lfs: product.lfs,
                    lmf: 10,
                    sigma,
                    raw_state: Some(raw_state),
                });
            }
        }

        if let Some(declared_products) = evaluation.mf9.get(&mt) {
            let products = unique_product_tables(declared_products, &format!("MT{mt}/MF=9"))?;
            let reaction = evaluation
                .mf3
                .get(&mt)
                .ok_or_else(|| format!("MT{mt}/MF=9 has no matching MF=3 reaction"))?;
            let actual: BTreeSet<_> = products
                .iter()
                .map(|product| (product.zap, product.lfs))
                .collect();
            validate_descriptors(descriptors, 9, &actual)?;
            let mut collapsed_mf9: Vec<(&ProductTable, Vec<f64>)> = Vec::new();
            for product in products {
                if !done.insert((product.zap, product.lfs)) {
                    return Err(format!(
                        "MT{mt} product ZAP={}/LFS={} has conflicting definitions",
                        product.zap, product.lfs
                    ));
                }
                let sigma = if let Some(processed) = processed.get(&mt) {
                    checked_groups(
                        processed.collapse_product(groups, &[&product.table])?,
                        &format!("MT{mt}/MF=2+MF=3*MF=9"),
                    )?
                } else {
                    checked_product(
                        groups,
                        &[reaction, &product.table],
                        &format!("MT{mt}/MF=3*MF=9"),
                    )?
                };
                collapsed_mf9.push((product, sigma));
            }
            let mut vectors: BTreeMap<i32, Vec<&mut Vec<f64>>> = BTreeMap::new();
            for (product, sigma) in collapsed_mf9.iter_mut() {
                vectors.entry(product.zap).or_default().push(sigma);
            }
            for (zap, mut states) in vectors {
                let report = reconcile_emitted_states(
                    mt,
                    9,
                    zap,
                    states.as_mut_slice(),
                    &total,
                    strict_states,
                    false,
                    metadata.mat,
                    metadata.za,
                    source_sha256,
                )?;
                if report.floor_reconciled > 0 {
                    ledger.push(format!(
                        "MT{mt}/MF=9 ZAP={zap}: emitted production sum exceeded the runtime total by a physically weightless amount in {} of {} group(s); accepted unchanged as floor_reconciled (absolute excess below 1e-15 barn)",
                        report.floor_reconciled,
                        report.checked
                    ));
                }
                if report.scaled > 0 {
                    ledger.push(format!(
                        "MT{mt}/MF=9 ZAP={zap}: emitted production sum exceeded the runtime total in {} of {} group(s); scaled by the common factor T/S under the frozen standard envelope (min scale {:.6e}, max relative excess {:.6e}, {} one-ULP closure correction(s))",
                        report.scaled,
                        report.checked,
                        report.min_scale,
                        report.max_relative_excess,
                        report.ulp_corrections
                    ));
                }
            }
            for (product, sigma) in collapsed_mf9 {
                let raw_state = RawProductState::from_table(
                    descriptor_for(descriptors, 9, product.zap, product.lfs),
                    product,
                )?;
                if let Some(delta) = raw_state.elfs_qm_qi_conflict_eV() {
                    ledger.push(format!(
                        "MT{mt} ZAP={}/LFS={}: MF=8 ELFS conflicts with QM-QI by {delta:.6e} eV within the 1 keV precedence bound; resolved to the evaluated ELFS (elfs_qm_qi_conflict_resolved)",
                        product.zap, product.lfs
                    ));
                }
                rows.push(BuiltRow {
                    mt,
                    zap: product.zap,
                    lfs: product.lfs,
                    lmf: 9,
                    sigma,
                    raw_state: Some(raw_state),
                });
            }
        }

        for descriptor in descriptors.iter().filter(|product| product.lmf == 3) {
            if !done.insert((descriptor.zap, descriptor.lfs)) {
                return Err(format!(
                    "MT{mt} product ZAP={}/LFS={} has conflicting definitions",
                    descriptor.zap, descriptor.lfs
                ));
            }
            rows.push(BuiltRow {
                mt,
                zap: descriptor.zap,
                lfs: descriptor.lfs,
                lmf: 3,
                sigma: total.clone(),
                raw_state: Some(RawProductState::from_descriptor(descriptor)),
            });
        }

        if has_lmf6 {
            let reaction = evaluation
                .mf3
                .get(&mt)
                .ok_or_else(|| format!("MT{mt}/MF=6 has no matching MF=3 reaction"))?;
            let yields = evaluation
                .mf6
                .get(&mt)
                .ok_or_else(|| format!("MT{mt}/MF=8 declares LMF=6 but MF=6 is missing"))?;
            let (products, omitted_neutron) = match_mf6(descriptors, yields)?;
            if omitted_neutron {
                ledger.push(format!(
                    "MT{mt}: emitted free-neutron MF=6 product omitted from inventory"
                ));
            }
            for (product, descriptor) in products {
                if !done.insert((descriptor.zap, descriptor.lfs)) {
                    return Err(format!(
                        "MT{mt} product ZAP={}/LFS={} has conflicting definitions",
                        descriptor.zap, descriptor.lfs
                    ));
                }
                rows.push(BuiltRow {
                    mt,
                    zap: descriptor.zap,
                    lfs: descriptor.lfs,
                    lmf: 6,
                    sigma: checked_product(
                        groups,
                        &[reaction, &product.yield_table],
                        &format!("MT{mt}/MF=3*MF=6"),
                    )?,
                    raw_state: Some(RawProductState::from_descriptor(&descriptor)),
                });
            }
        }

        if done.is_empty() {
            if mt == 18 {
                rows.push(BuiltRow {
                    mt,
                    zap: 0,
                    lfs: 0,
                    lmf: 0,
                    sigma: total,
                    raw_state: None,
                });
            } else if let Some(delta) = products_by_mt.get(&mt) {
                if let Some(zap) = residual_product(metadata.za, metadata.projectile, *delta) {
                    rows.push(BuiltRow {
                        mt,
                        zap,
                        lfs: 0,
                        lmf: -1,
                        sigma: total,
                        raw_state: None,
                    });
                } else {
                    rows.push(BuiltRow {
                        mt,
                        zap: 0,
                        lfs: 0,
                        lmf: -2,
                        sigma: total,
                        raw_state: None,
                    });
                    ledger.push(format!(
                        "MT{mt}: residual arithmetic is not a bound nuclide"
                    ));
                }
            } else {
                rows.push(BuiltRow {
                    mt,
                    zap: 0,
                    lfs: 0,
                    lmf: -2,
                    sigma: total,
                    raw_state: None,
                });
                ledger.push(format!("MT{mt}: product is unmapped leakage"));
            }
        }
    }

    // Lumped-channel synthesis (P39): ENDF-6 carries discrete-state +
    // continuum charged-particle production in MT600-849. Where the
    // summary MT (103-107) has no coverage in MF3, the processed set, or
    // MF10, the family's collapsed tables sum into one canonical-MT row;
    // excitation-level branching to residual isomers is not attributed
    // (REAC-equivalent lumped semantics). Where summary coverage exists
    // the lumped sections are redundant decomposition and skipped.
    const LUMPED_FAMILIES: &[(std::ops::RangeInclusive<i32>, i32)] = &[
        (600..=649, 103),
        (650..=699, 104),
        (700..=749, 105),
        (750..=799, 106),
        (800..=849, 107),
    ];
    for (range, summary_mt) in LUMPED_FAMILIES {
        let lumped: Vec<i32> = evaluation
            .mf3
            .keys()
            .filter(|mt| range.contains(mt))
            .copied()
            .collect();
        if lumped.is_empty() {
            continue;
        }
        if processed.contains_key(summary_mt)
            || evaluation.mf3.contains_key(summary_mt)
            || evaluation.mf10.contains_key(summary_mt)
        {
            ledger.push(format!(
                "MT{}-{}: {} lumped channel(s) present but MT{summary_mt} coverage already governs the residual; lumped sections skipped",
                lumped.first().unwrap(),
                lumped.last().unwrap(),
                lumped.len()
            ));
            continue;
        }
        let mut sigma = vec![0.0; groups.groups()];
        for mt in &lumped {
            let collapsed = checked_collapse(groups, &evaluation.mf3[mt], &format!("MT{mt}/MF=3"))?;
            sum_groups(&mut sigma, &collapsed)?;
        }
        let delta = products_by_mt
            .get(summary_mt)
            .copied()
            .ok_or_else(|| format!("MT{summary_mt} has no product mapping"))?;
        let Some(zap) = residual_product(metadata.za, metadata.projectile, delta) else {
            ledger.push(format!(
                "MT{summary_mt}: lumped-channel residual arithmetic is not a bound nuclide"
            ));
            continue;
        };
        rows.push(BuiltRow {
            mt: *summary_mt,
            zap,
            lfs: 0,
            lmf: -1,
            sigma,
            raw_state: None,
        });
        ledger.push(format!(
            "MT{summary_mt}: synthesized from {} lumped MF=3 channel(s) (lumped_channel_synthesis: discrete-level and continuum production summed; excitation-level branching to residual isomers not attributed)",
            lumped.len()
        ));
    }

    if format == LibraryFormat::Eaf {
        remap_eaf_levels(&mut rows, &mut ledger);
    }
    let index = TargetIndex {
        file: file.into(),
        source_sha256: source_sha256.into(),
        mat: metadata.mat,
        za: metadata.za,
        liso: metadata.liso,
        lis: metadata.lis,
        elis_eV: metadata.elis_ev,
        awr: metadata.awr,
        evaluation_temperature_K: metadata.evaluation_temperature_k,
        n_mf2: evaluation.mf2_sections.len(),
        n_mf3: evaluation.mf3.len(),
        n_mf6: evaluation.mf6.len(),
        n_mf8: evaluation.mf8.len(),
        n_mf9: evaluation.mf9.len(),
        n_mf10: evaluation.mf10.len(),
        n_rows: rows.len(),
        state_mappings: Vec::new(),
        ledger,
    };
    Ok(BuiltTarget { index, rows })
}

fn build_source(
    path: &Path,
    options: &BuildOptions,
    products_by_mt: &BTreeMap<i32, (i32, i32)>,
) -> Result<BuiltSource, String> {
    let filename = path
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(|| format!("input filename '{}' is not UTF-8", path.display()))?;
    let before = sha256_file(path)?;
    let key = checkpoint_key(&before, options);
    if let Some(cache) = &options.cache {
        if let Some(source) = load_checkpoint(cache, &key, &before, &options.groups) {
            if sha256_file(path)? != before {
                return Err(format!(
                    "source {} changed while its checkpoint was validated",
                    path.display()
                ));
            }
            return Ok(source);
        }
    }
    let text = std::fs::read_to_string(path)
        .map_err(|error| format!("cannot read {} as ENDF text: {error}", path.display()))?;
    let evaluations = parse_evaluations(&text, options.projectile)
        .map_err(|error| format!("{}: {error}", path.display()))?;
    let first = evaluations
        .first()
        .ok_or_else(|| format!("{} contains no evaluations", path.display()))?;
    let detected = detected_format(&text, first);
    let format = match options.format {
        LibraryFormat::Auto => detected?,
        explicit => {
            if let Ok(detected) = detected {
                if detected != explicit {
                    return Err(format!(
                        "{} identifies as {}, not requested {} format",
                        path.display(),
                        detected.name(),
                        explicit.name()
                    ));
                }
            }
            explicit
        }
    };
    let projectile = first.metadata.projectile;
    if format == LibraryFormat::Eaf && projectile != Projectile::Neutron {
        return Err(format!(
            "{}: EAF format requires neutron data",
            path.display()
        ));
    }
    let mut targets = Vec::with_capacity(evaluations.len());
    for evaluation in evaluations {
        if evaluation.metadata.projectile != projectile {
            return Err(format!(
                "{} contains mixed {} and {} evaluations",
                path.display(),
                projectile.name(),
                evaluation.metadata.projectile.name()
            ));
        }
        targets.push(
            build_evaluation(
                evaluation,
                format,
                filename,
                &before,
                EvaluationBuildSettings {
                    groups: &options.groups,
                    temperature_K: options.temperature_K,
                    grid_density: options.grid_density,
                    strict_states: options.strict_states,
                },
                products_by_mt,
            )
            .map_err(|error| format!("{}: {error}", path.display()))?,
        );
    }
    let after = sha256_file(path)?;
    if before != after {
        return Err(format!(
            "source {} changed during the library build",
            path.display()
        ));
    }
    let source = BuiltSource {
        format,
        projectile,
        targets,
        from_cache: false,
    };
    if let Some(cache) = &options.cache {
        store_checkpoint(cache, &key, &before, &source, &options.groups)?;
    }
    Ok(source)
}

pub fn index_path(output: impl AsRef<Path>) -> Result<PathBuf, String> {
    let output = output.as_ref();
    if output.extension().and_then(OsStr::to_str) != Some("npz") {
        return Err("activation-library output must end in .npz".into());
    }
    let stem = output
        .file_stem()
        .and_then(OsStr::to_str)
        .ok_or("activation-library output needs a UTF-8 filename")?;
    Ok(output.with_file_name(format!("{stem}_index.json")))
}

fn duplicate_target_error(previous: &TargetIndex, current: &TargetIndex) -> String {
    format!(
        "duplicate target ZA={}/LISO={} in files '{}' and '{}'",
        current.za, current.liso, previous.file, current.file
    )
}

pub(crate) fn write_json_atomic(path: &Path, value: &impl Serialize) -> Result<(), String> {
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT_JSON_TEMPORARY: AtomicU64 = AtomicU64::new(0);
    let text = serde_json::to_string_pretty(value)
        .map_err(|error| format!("cannot serialize library index: {error}"))?;
    let nonce = NEXT_JSON_TEMPORARY.fetch_add(1, Ordering::Relaxed);
    let temporary = path.with_file_name(format!(
        ".{}.{}.{nonce}.tmp",
        path.file_name().and_then(OsStr::to_str).unwrap_or("index"),
        std::process::id()
    ));
    std::fs::write(&temporary, format!("{text}\n"))
        .map_err(|error| format!("cannot write {}: {error}", temporary.display()))?;
    std::fs::rename(&temporary, path)
        .map_err(|error| format!("cannot publish {}: {error}", path.display()))
}

/// Build and atomically publish a deterministic activation-library NPZ and canonical index.
pub fn build_library(
    input: impl AsRef<Path>,
    output: impl AsRef<Path>,
    options: &BuildOptions,
) -> Result<BuildSummary, String> {
    validate_options(options)?;
    let input = input.as_ref();
    let output = output.as_ref();
    let index_path = index_path(output)?;
    let files = discover_inputs(input, Some(output))?;
    if let Some(cache) = &options.cache {
        if input.is_dir() && cache.starts_with(input) {
            return Err("checkpoint cache must be outside the input directory".into());
        }
        match std::fs::symlink_metadata(cache) {
            Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_dir() => {
                return Err(format!(
                    "checkpoint cache {} is not a real directory",
                    cache.display()
                ));
            }
            Ok(_) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                std::fs::create_dir_all(cache).map_err(|error| {
                    format!(
                        "cannot create checkpoint cache {}: {error}",
                        cache.display()
                    )
                })?;
            }
            Err(error) => {
                return Err(format!(
                    "cannot inspect checkpoint cache {}: {error}",
                    cache.display()
                ));
            }
        }
    }
    let products_by_mt = mt_products()?;
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(options.workers)
        .build()
        .map_err(|error| format!("cannot create builder worker pool: {error}"))?;
    let mut sources: Vec<BuiltSource> = pool.install(|| {
        files
            .par_iter()
            .map(|path| build_source(path, options, &products_by_mt))
            .collect::<Result<Vec<_>, _>>()
    })?;
    let projectile = sources
        .first()
        .ok_or("activation-library build produced no sources")?
        .projectile;
    let format = sources[0].format;
    if sources.iter().any(|source| source.projectile != projectile) {
        return Err("input directory contains mixed projectile evaluations".into());
    }
    if sources.iter().any(|source| source.format != format) {
        return Err("input directory contains mixed TENDL/EAF evaluations".into());
    }
    let state_catalog = if format == LibraryFormat::Tendl {
        let catalog = build_state_catalog(&sources)?;
        for source in &mut sources {
            for target in &mut source.targets {
                map_product_states(target, &catalog)?;
            }
        }
        Some(catalog)
    } else {
        None
    };
    let cache_hits = sources.iter().filter(|source| source.from_cache).count();
    if projectile != Projectile::Neutron && options.temperature_K != 0.0 {
        return Err(format!("{} libraries require 0 K", projectile.name()));
    }
    if options.groups.name.starts_with("fispact-") {
        let expected = if projectile == Projectile::Neutron {
            709
        } else {
            162
        };
        if options.groups.groups() != expected {
            return Err(format!(
                "{} requires fispact-{expected}, got {}",
                projectile.name(),
                options.groups.name
            ));
        }
    }

    let mut targets = Vec::new();
    let mut seen_targets = BTreeMap::new();
    let mut rows = Vec::new();
    let mut sig = Vec::new();
    for source in sources {
        for target in source.targets {
            let identity = (target.index.za, target.index.liso);
            if let Some(previous) = seen_targets.insert(identity, targets.len()) {
                return Err(duplicate_target_error(&targets[previous], &target.index));
            }
            let target_number = targets.len();
            for row in target.rows {
                rows.push(Row {
                    target: target_number,
                    mt: row.mt,
                    zap: row.zap,
                    lfs: row.lfs,
                    lmf: row.lmf,
                });
                sig.extend(row.sigma);
            }
            targets.push(target.index);
        }
    }
    let library = Library {
        rows,
        sig,
        ngroups: options.groups.groups(),
        bounds: options.groups.boundaries_ev.clone(),
    };
    library.validate()?;
    write_npz(output, &library)?;
    let npz_hash = sha256_file(output)?;
    let fingerprint = builder_fingerprint();
    let index = BuildIndex {
        schema: if format == LibraryFormat::Tendl {
            "actinv-library-index-2"
        } else {
            "actinv-library-index-1"
        },
        format: format.name().into(),
        projectile: projectile.name().into(),
        temperature_K: options.temperature_K,
        groups: options.groups.name.clone(),
        group_boundary_sha256: options.groups.hash(),
        weighting: "flat-lethargy",
        builder_fingerprint: fingerprint.clone(),
        emission_model: "p25-amendment-b",
        options: CanonicalOptions {
            grid_density: options.grid_density,
        },
        state_catalog: state_catalog
            .into_iter()
            .flat_map(BTreeMap::into_values)
            .flatten()
            .collect(),
        targets,
        n_rows: library.rows.len(),
        columns: "rows: (target, MT, ZAP, LFS, LMF)",
        sha256_npz: npz_hash.clone(),
    };
    if let Err(error) = write_json_atomic(&index_path, &index) {
        let _ = std::fs::remove_file(output);
        return Err(error);
    }
    Ok(BuildSummary {
        output: output.to_path_buf(),
        index: index_path,
        projectile,
        targets: index.targets.len(),
        rows: library.rows.len(),
        cache_hits,
        sha256_npz: npz_hash,
        builder_fingerprint: fingerprint,
    })
}

// ---- damage tables (P23 G3): collapse every MF=3/MT=444 section in an evaluation
// directory into an actinv-damage-table-1. The parse, temperature check and lethargy
// collapse are the same code paths build-library uses for non-resonance MF=3 sections;
// evaluations lacking MT=444 are named in `uncovered`, never fabricated or zero-filled.

#[derive(Clone, Debug)]
pub struct DamageBuildOptions {
    pub projectile: Option<Projectile>,
    pub groups: GroupStructure,
    pub temperature_K: f64,
    pub cache: Option<PathBuf>,
}

#[derive(Debug)]
pub struct DamageBuildSummary {
    pub targets: usize,
    pub uncovered_evaluations: usize,
    pub cache_hits: usize,
    pub projectile: Projectile,
    pub output: PathBuf,
    pub sha256: String,
}

#[derive(Debug, Serialize, Deserialize)]
struct DamageCacheEntry {
    targets: BTreeMap<String, Vec<f64>>,
    uncovered: Vec<String>,
}

fn damage_nuclide_name(za: i32, liso: i32) -> String {
    let symbol = crate::composition::symbol_of(za / 1000);
    if liso > 0 {
        format!("{symbol}{}m{liso}", za % 1000)
    } else {
        format!("{symbol}{}", za % 1000)
    }
}

fn damage_cache_key(source_sha256: &str, options: &DamageBuildOptions) -> String {
    let mut hasher = Sha256::new();
    hasher.update(b"ACTINV-DAMAGE-BUILD-v1\0");
    hasher.update(source_sha256.as_bytes());
    hasher.update(options.temperature_K.to_bits().to_le_bytes());
    hasher.update(options.groups.hash().as_bytes());
    format!("{:x}", hasher.finalize())
}

type DamageSource = Result<(DamageCacheEntry, Vec<(String, String)>, bool), String>;

fn build_damage_source(
    path: &Path,
    options: &DamageBuildOptions,
    projectile: Projectile,
) -> DamageSource {
    let before = sha256_file(path)?;
    let filename = path
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(|| format!("input filename '{}' is not UTF-8", path.display()))?
        .to_owned();
    if let Some(cache) = &options.cache {
        let key = damage_cache_key(&before, options);
        let checkpoint = cache.join(format!("{key}.json"));
        if checkpoint.exists() {
            let entry: DamageCacheEntry =
                serde_json::from_str(&std::fs::read_to_string(&checkpoint).map_err(|error| {
                    format!(
                        "cannot read damage checkpoint {}: {error}",
                        checkpoint.display()
                    )
                })?)
                .map_err(|error| {
                    format!(
                        "cannot parse damage checkpoint {}: {error}",
                        checkpoint.display()
                    )
                })?;
            if sha256_file(path)? != before {
                return Err(format!(
                    "source {} changed while its damage checkpoint was validated",
                    path.display()
                ));
            }
            return Ok((entry, vec![(filename, before)], true));
        }
    }
    let text = std::fs::read_to_string(path)
        .map_err(|error| format!("cannot read {} as ENDF text: {error}", path.display()))?;
    let evaluations = parse_evaluations(&text, Some(projectile))
        .map_err(|error| format!("{}: {error}", path.display()))?;
    let mut entry = DamageCacheEntry {
        targets: BTreeMap::new(),
        uncovered: Vec::new(),
    };
    for evaluation in &evaluations {
        let metadata = &evaluation.metadata;
        if metadata.projectile != projectile {
            return Err(format!(
                "{} contains a {} evaluation inside a {} directory",
                path.display(),
                metadata.projectile.name(),
                projectile.name()
            ));
        }
        let name = damage_nuclide_name(metadata.za, metadata.liso);
        let Some(section) = evaluation.mf3.get(&444) else {
            entry.uncovered.push(name);
            continue;
        };
        // MT=444 is a smooth pointwise section; like every non-resonance MF=3 section it is
        // used at its declared evaluation temperature.
        if metadata.evaluation_temperature_k.to_bits() != options.temperature_K.to_bits() {
            return Err(format!(
                "{}: {} evaluation is at {} K but {} K was requested",
                path.display(),
                name,
                metadata.evaluation_temperature_k,
                options.temperature_K
            ));
        }
        let collapsed = options
            .groups
            .collapse(section)
            .map_err(|error| format!("{} {name} MT=444: {error}", path.display()))?;
        if collapsed
            .iter()
            .any(|value| !value.is_finite() || *value < 0.0)
        {
            return Err(format!(
                "{} {name} MT=444 collapsed to a nonfinite or negative group value",
                path.display()
            ));
        }
        if entry.targets.insert(name.clone(), collapsed).is_some() {
            return Err(format!(
                "{} declares two MT=444 sections for {name}",
                path.display()
            ));
        }
    }
    if let Some(cache) = &options.cache {
        let key = damage_cache_key(&before, options);
        let checkpoint = cache.join(format!("{key}.json"));
        write_json_atomic(&checkpoint, &entry)?;
    }
    Ok((entry, vec![(filename, before)], false))
}

/// Build and atomically publish an `actinv-damage-table-1` from an evaluation directory.
pub fn build_damage(
    input: impl AsRef<Path>,
    output: impl AsRef<Path>,
    options: &DamageBuildOptions,
) -> Result<DamageBuildSummary, String> {
    let input = input.as_ref();
    let output = output.as_ref();
    let files = discover_inputs(input, Some(output))?;
    if let Some(cache) = &options.cache {
        if input.is_dir() && cache.starts_with(input) {
            return Err("damage checkpoint cache must be outside the input directory".into());
        }
        std::fs::create_dir_all(cache).map_err(|error| {
            format!(
                "cannot create damage checkpoint cache {}: {error}",
                cache.display()
            )
        })?;
    }
    let projectile = match options.projectile {
        Some(projectile) => projectile,
        None => inspect_projectile(input)?,
    };
    if !projectile.is_neutron() && options.temperature_K != 0.0 {
        return Err(format!("{} damage tables require 0 K", projectile.name()));
    }
    if options.groups.name.starts_with("fispact-") {
        let expected = if projectile.is_neutron() { 709 } else { 162 };
        if options.groups.groups() != expected {
            return Err(format!(
                "{} damage tables require fispact-{expected}, got {}",
                projectile.name(),
                options.groups.name
            ));
        }
    }
    let mut targets: BTreeMap<String, Vec<f64>> = BTreeMap::new();
    let mut target_files: BTreeMap<String, String> = BTreeMap::new();
    let mut provenance = Vec::new();
    let mut uncovered = Vec::new();
    let mut cache_hits = 0usize;
    for path in &files {
        let (entry, file_provenance, hit) = build_damage_source(path, options, projectile)
            .map_err(|error| format!("{}: {error}", path.display()))?;
        cache_hits += usize::from(hit);
        provenance.extend(file_provenance);
        for (name, row) in entry.targets {
            if let Some(previous) = target_files.get(&name) {
                return Err(format!(
                    "duplicate damage target {name} in '{}' and '{}'",
                    previous,
                    path.display()
                ));
            }
            target_files.insert(name.clone(), path.display().to_string());
            targets.insert(name, row);
        }
        uncovered.extend(entry.uncovered);
    }
    let input_label = input
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("evaluations")
        .to_owned();
    let table = serde_json::json!({
        "format": "actinv-damage-table-1",
        "source": {
            "citation": "ENDF-6 MF=3/MT=444 damage-energy production sections collapsed by actinv build-damage",
            "edition": input_label,
            "url": input.display().to_string(),
        },
        "projectile": projectile.name(),
        "group_structure": options.groups.name,
        "boundaries_eV": options.groups.boundaries_ev,
        "units": "damage_energy_barn_eV_per_group",
        "temperature_K": options.temperature_K,
        "files": provenance
            .iter()
            .map(|(path, sha256)| serde_json::json!({"path": path, "sha256": sha256}))
            .collect::<Vec<_>>(),
        "uncovered": uncovered,
        "targets": targets,
    });
    write_json_atomic(output, &table)?;
    let sha256 = sha256_file(output)?;
    Ok(DamageBuildSummary {
        targets: table["targets"].as_object().map(|m| m.len()).unwrap_or(0),
        uncovered_evaluations: uncovered.len(),
        cache_hits,
        projectile,
        output: output.to_path_buf(),
        sha256,
    })
}

// ---------------------------------------------------------------------------
// P19 shielding-table builder: ENDF-6 MF=2 LRU=2 unresolved-resonance
// statistics -> deterministic Bondarenko factor table (actinv-shield-table-1).
// The pipeline is a deterministic port of NJOY2016 PURR (stratified quantiles
// instead of RNG); see crates/actinv-data/src/shielding.rs.
// ---------------------------------------------------------------------------

#[derive(Clone, Debug)]
pub struct ShieldingBuildOptions {
    pub projectile: Option<Projectile>,
    pub groups: GroupStructure,
    pub cache: Option<PathBuf>,
}

#[derive(Debug)]
pub struct ShieldingBuildSummary {
    pub targets: usize,
    pub uncovered_evaluations: usize,
    pub cache_hits: usize,
    pub projectile: Projectile,
    pub output: PathBuf,
    pub sha256: String,
}

#[derive(Debug, Serialize, Deserialize)]
struct ShieldingCacheEntry {
    nuclides: BTreeMap<String, serde_json::Value>,
    uncovered: Vec<String>,
}

fn shielding_cache_key(source_sha256: &str, options: &ShieldingBuildOptions) -> String {
    let mut hasher = Sha256::new();
    hasher.update(b"ACTINV-SHIELDING-BUILD-v4\0");
    hasher.update(source_sha256.as_bytes());
    hasher.update(options.groups.hash().as_bytes());
    format!("{:x}", hasher.finalize())
}

fn shielding_nuclide_entry(
    evaluation: &crate::activation::Evaluation,
    groups: &GroupStructure,
) -> Result<Option<serde_json::Value>, String> {
    let Some(resonance) = &evaluation.resonance else {
        return Ok(None);
    };
    let mut ranges: Vec<(f64, f64)> = Vec::new();
    for isotope in &resonance.isotopes {
        for range in &isotope.ranges {
            if matches!(range.data, crate::resonance::RangeData::Unresolved(_)) {
                ranges.push((range.energy_min, range.energy_max));
            }
        }
    }
    if ranges.is_empty() {
        return Ok(None);
    }
    let nodes = crate::shielding::shield_evaluation(
        evaluation,
        &crate::shielding::SIGMA0_B,
        &crate::shielding::TEMPERATURES_K,
        crate::shielding::NLADR,
    )?;
    if nodes.is_empty() {
        return Ok(None);
    }
    let collapsed = crate::shielding::collapse_to_groups(
        &nodes,
        &ranges,
        groups,
        &crate::shielding::SIGMA0_B,
        &crate::shielding::TEMPERATURES_K,
        evaluation,
    )?;
    let channel_names = ["total", "elastic", "fission", "capture"];
    let node_rows: Vec<serde_json::Value> = nodes
        .iter()
        .map(|node| {
            let bondarenko = node.sigf.as_ref().map(|sigf| {
                serde_json::Value::Object(
                    channel_names
                        .iter()
                        .enumerate()
                        .map(|(c, name)| (name.to_string(), serde_json::json!(sigf[c])))
                        .collect(),
                )
            });
            let ptable = node.ptable.as_ref().map(|pt| {
                pt.iter()
                    .map(|(bounds, vals)| {
                        serde_json::json!({
                            "bounds_b": bounds,
                            "prob": vals[0],
                            "total_b": vals[1],
                            "elastic_b": vals[2],
                            "fission_b": vals[3],
                            "capture_b": vals[4],
                        })
                    })
                    .collect::<Vec<_>>()
            });
            serde_json::json!({
                "energy_ev": node.energy_ev,
                "ptable": ptable,
                "covered": node.covered,
                "sigma_p_b": node.sigma_p_b,
                "infinite_dilution_b": node.infinite_dilution_b,
                "bondarenko_b": bondarenko,
                "direct_b": node.sigf_direct.as_ref().map(|sigf| {
                    serde_json::Value::Object(
                        channel_names
                            .iter()
                            .enumerate()
                            .map(|(c, name)| (name.to_string(), serde_json::json!(sigf[c])))
                            .collect(),
                    )
                }),
                "ladder_sigma_percent": node.ladder_sigma_percent,
                "mean_unshielded_b": node.mean_unshielded,
                "ladders": node.ladder_count,
                "resonances_per_ladder": node.resonances_per_ladder,
            })
        })
        .collect();
    let group_rows: Vec<serde_json::Value> = collapsed
        .iter()
        .map(|row| {
            serde_json::json!({
                "group": row.group,
                "overlap_fraction": row.overlap_fraction,
                "sigma_p_b": row.sigma_p_b,
                "infinite_dilution_b": row.infinite_dilution_b,
                "factors": serde_json::Value::Object(
                    channel_names
                        .iter()
                        .enumerate()
                        .map(|(c, name)| (name.to_string(), serde_json::json!(row.factors[c])))
                        .collect(),
                ),
                "shielded_b": serde_json::Value::Object(
                    channel_names
                        .iter()
                        .enumerate()
                        .map(|(c, name)| (name.to_string(), serde_json::json!(row.shielded_b[c])))
                        .collect(),
                ),
                "background_b": row.background_b,
                "weight_mean": row.weight_mean,
                "group_unshielded_b": row.group_unshielded_b,
                "group_shielded_b": serde_json::Value::Object(
                    channel_names
                        .iter()
                        .enumerate()
                        .map(|(c, name)| {
                            (name.to_string(), serde_json::json!(row.group_shielded_b[c]))
                        })
                        .collect(),
                ),
                "group_factors": serde_json::Value::Object(
                    channel_names
                        .iter()
                        .enumerate()
                        .map(|(c, name)| {
                            (name.to_string(), serde_json::json!(row.group_factors[c]))
                        })
                        .collect(),
                ),
            })
        })
        .collect();
    Ok(Some(serde_json::json!({
        "za": evaluation.metadata.za,
        "liso": evaluation.metadata.liso,
        "unresolved_ranges_ev": ranges,
        "nodes": node_rows,
        "groups": group_rows,
    })))
}

/// Build and atomically publish an `actinv-shield-table-1` from an evaluation
/// directory. Evaluations without MF=2 LRU=2 unresolved blocks are named in
/// `uncovered` — never fabricated.
pub fn build_shielding(
    input: impl AsRef<Path>,
    output: impl AsRef<Path>,
    options: &ShieldingBuildOptions,
) -> Result<ShieldingBuildSummary, String> {
    let input = input.as_ref();
    let output = output.as_ref();
    let files = discover_inputs(input, Some(output))?;
    if let Some(cache) = &options.cache {
        if input.is_dir() && cache.starts_with(input) {
            return Err("shielding checkpoint cache must be outside the input directory".into());
        }
        std::fs::create_dir_all(cache).map_err(|error| {
            format!(
                "cannot create shielding checkpoint cache {}: {error}",
                cache.display()
            )
        })?;
    }
    let projectile = match options.projectile {
        Some(projectile) => projectile,
        None => inspect_projectile(input)?,
    };
    if !projectile.is_neutron() {
        return Err(format!(
            "{} shielding tables require neutron evaluations",
            projectile.name()
        ));
    }
    let mut nuclides: BTreeMap<String, serde_json::Value> = BTreeMap::new();
    let mut nuclide_files: BTreeMap<String, String> = BTreeMap::new();
    let mut provenance = Vec::new();
    let mut uncovered = Vec::new();
    let mut cache_hits = 0usize;
    for path in &files {
        let before = sha256_file(path)?;
        let filename = path
            .file_name()
            .and_then(|name| name.to_str())
            .ok_or_else(|| format!("input filename '{}' is not UTF-8", path.display()))?
            .to_owned();
        if let Some(cache) = &options.cache {
            let key = shielding_cache_key(&before, options);
            let checkpoint = cache.join(format!("{key}.json"));
            if checkpoint.exists() {
                let entry: ShieldingCacheEntry = serde_json::from_str(
                    &std::fs::read_to_string(&checkpoint).map_err(|error| {
                        format!(
                            "cannot read shielding checkpoint {}: {error}",
                            checkpoint.display()
                        )
                    })?,
                )
                .map_err(|error| {
                    format!(
                        "cannot parse shielding checkpoint {}: {error}",
                        checkpoint.display()
                    )
                })?;
                if sha256_file(path)? != before {
                    return Err(format!(
                        "source {} changed while its shielding checkpoint was validated",
                        path.display()
                    ));
                }
                cache_hits += 1;
                provenance.push((filename.clone(), before));
                for (name, row) in entry.nuclides {
                    nuclide_files.insert(name.clone(), filename.clone());
                    nuclides.insert(name, row);
                }
                uncovered.extend(entry.uncovered);
                continue;
            }
        }
        let text = std::fs::read_to_string(path)
            .map_err(|error| format!("cannot read {} as ENDF text: {error}", path.display()))?;
        let evaluations = parse_evaluations(&text, Some(projectile))
            .map_err(|error| format!("{}: {error}", path.display()))?;
        let mut entry = ShieldingCacheEntry {
            nuclides: BTreeMap::new(),
            uncovered: Vec::new(),
        };
        for evaluation in &evaluations {
            if evaluation.metadata.projectile != projectile {
                return Err(format!(
                    "{} contains a {} evaluation inside a {} directory",
                    path.display(),
                    evaluation.metadata.projectile.name(),
                    projectile.name()
                ));
            }
            let name = damage_nuclide_name(evaluation.metadata.za, evaluation.metadata.liso);
            match shielding_nuclide_entry(evaluation, &options.groups)? {
                Some(row) => {
                    if nuclides.contains_key(&name) || entry.nuclides.contains_key(&name) {
                        return Err(format!(
                            "duplicate shielding target {name} in '{}'",
                            path.display()
                        ));
                    }
                    entry.nuclides.insert(name.clone(), row);
                }
                None => entry.uncovered.push(name),
            }
        }
        if let Some(cache) = &options.cache {
            let key = shielding_cache_key(&before, options);
            let checkpoint = cache.join(format!("{key}.json"));
            write_json_atomic(&checkpoint, &entry)?;
        }
        provenance.push((filename, before));
        for (name, row) in entry.nuclides {
            nuclide_files.insert(name.clone(), path.display().to_string());
            nuclides.insert(name, row);
        }
        uncovered.extend(entry.uncovered);
    }
    let _ = &nuclide_files;
    let input_label = input
        .file_name()
        .and_then(|name| name.to_str())
        .unwrap_or("evaluations")
        .to_owned();
    let table = serde_json::json!({
        "format": "actinv-shield-table-1",
        "generator": "actinv build-shielding (deterministic PURR-equivalent)",
        "source": {
            "citation": "ENDF-6 MF=2 LRU=2 unresolved-resonance statistics processed by actinv build-shielding",
            "edition": input_label,
            "url": input.display().to_string(),
        },
        "method": {
            "pipeline": "ENDF-6 MF=2 LRU=2 -> unresx ladder parameters -> deterministic stratified-quantile ladders -> zoned Voigt accumulation -> probability-table Bondarenko moments -> infinite-dilution renorm (PURR MT=152 convention)",
            "sampling": "stratified quantile grid, deterministic permutation, no RNG",
            "nladr": crate::shielding::NLADR,
            "nstrat": crate::shielding::NSTRAT,
            "strat_step": crate::shielding::STRAT_STEP,
            "ngrid": crate::shielding::NGRID,
            "nbin": crate::shielding::NBIN,
            "nermax": crate::shielding::NERMAX,
            "tref_K": crate::shielding::TREF_K,
            "reference": "NJOY2016.79 purr.f90: rdf2un/rdf3un/unresx/unfac2/gnrx/ladr2/unrest/uw2/uwtab2",
        },
        "sigma0_b": crate::shielding::SIGMA0_B,
        "temperatures_K": crate::shielding::TEMPERATURES_K,
        "group_structure": {
            "name": options.groups.name,
            "boundaries_eV": options.groups.boundaries_ev,
        },
        "files": provenance
            .iter()
            .map(|(path, sha256)| serde_json::json!({"path": path, "sha256": sha256}))
            .collect::<Vec<_>>(),
        "uncovered": uncovered,
        "nuclides": nuclides,
    });
    write_json_atomic(output, &table)?;
    let sha256 = sha256_file(output)?;
    Ok(ShieldingBuildSummary {
        targets: table["nuclides"].as_object().map(|m| m.len()).unwrap_or(0),
        uncovered_evaluations: table["uncovered"].as_array().map(|v| v.len()).unwrap_or(0),
        cache_hits,
        projectile,
        output: output.to_path_buf(),
        sha256,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn table(y: [f64; 2]) -> Tabulated {
        Tabulated {
            interpolation: vec![(2, 2)],
            x: vec![1.0, 4.0],
            y: y.to_vec(),
        }
    }

    fn state_product(zap: i32, lfs: i32, table: Tabulated) -> ProductTable {
        ProductTable {
            zap,
            qm_ev: 1_000_000.0,
            qi_ev: 1_000_000.0 - f64::from(lfs) * 100_000.0,
            lfs,
            table,
        }
    }

    fn evaluation(projectile: Projectile) -> Evaluation {
        Evaluation {
            metadata: crate::activation::TargetMetadata {
                mat: 1,
                za: 26056,
                awr: 55.45,
                elis_ev: 0.0,
                lis: 0,
                liso: 0,
                awi: f64::from(projectile.za().1),
                nsub: projectile.nsub(),
                projectile,
                evaluation_temperature_k: 0.0,
            },
            mf2_sections: BTreeSet::new(),
            resonance: None,
            mf3: BTreeMap::from([(102, table([2.0, 2.0]))]),
            mf6: BTreeMap::new(),
            mf8: BTreeMap::new(),
            mf9: BTreeMap::new(),
            mf10: BTreeMap::new(),
        }
    }

    fn target_index(file: &str) -> TargetIndex {
        TargetIndex {
            file: file.into(),
            source_sha256: "0".repeat(64),
            mat: 1,
            za: 26056,
            liso: 0,
            lis: 0,
            elis_eV: 0.0,
            awr: 55.45,
            evaluation_temperature_K: 0.0,
            n_mf2: 0,
            n_mf3: 1,
            n_mf6: 0,
            n_mf8: 0,
            n_mf9: 0,
            n_mf10: 0,
            n_rows: 2,
            state_mappings: Vec::new(),
            ledger: Vec::new(),
        }
    }

    fn state_row(mt: i32, zap: i32, raw_lfs: i32, excitation_eV: Option<f64>) -> BuiltRow {
        BuiltRow {
            mt,
            zap,
            lfs: raw_lfs,
            lmf: 10,
            sigma: vec![0.25, 0.5],
            raw_state: Some(RawProductState {
                raw_lfs,
                elfs_eV: excitation_eV,
                qm_eV: excitation_eV.map(|value| value + 1_000_000.0),
                qi_eV: excitation_eV.map(|_| 1_000_000.0),
            }),
        }
    }

    fn state_target(
        file: &str,
        za: i32,
        liso: i32,
        lis: i32,
        elis_eV: f64,
        rows: Vec<BuiltRow>,
    ) -> BuiltTarget {
        let mut index = target_index(file);
        index.za = za;
        index.liso = liso;
        index.lis = lis;
        index.elis_eV = elis_eV;
        index.n_rows = rows.len();
        BuiltTarget { index, rows }
    }

    fn fixture_record(values: [&str; 6], mat: i32, mf: i32, mt: i32, sequence: i32) -> String {
        let data: String = values
            .into_iter()
            .map(|value| format!("{value:>11}"))
            .collect();
        format!("{data}{mat:>4}{mf:>2}{mt:>3}{sequence:>5}")
    }

    fn fixture_send(mat: i32, mf: i32) -> String {
        fixture_record(["", "", "", "", "", ""], mat, mf, 0, 99_999)
    }

    fn fixture_header(za: i32, mat: i32, elis: &str, lis: i32, liso: i32) -> Vec<String> {
        vec![
            fixture_record(
                [&za.to_string(), "55.45", "0", "0", "0", "0"],
                mat,
                1,
                451,
                1,
            ),
            fixture_record(
                [elis, "0", &lis.to_string(), &liso.to_string(), "0", "0"],
                mat,
                1,
                451,
                2,
            ),
            fixture_record(["1", "4", "1", "0", "10", "2025"], mat, 1, 451, 3),
            fixture_record(["0", "0", "0", "0", "0", "0"], mat, 1, 451, 4),
            fixture_send(mat, 1),
        ]
    }

    fn fixture_tab1(
        mat: i32,
        mf: i32,
        mt: i32,
        sequence: i32,
        head: [&str; 4],
        value: &str,
    ) -> Vec<String> {
        vec![
            fixture_record(
                [head[0], head[1], head[2], head[3], "1", "2"],
                mat,
                mf,
                mt,
                sequence,
            ),
            fixture_record(["2", "2", "", "", "", ""], mat, mf, mt, sequence + 1),
            fixture_record(["1", value, "4", value, "", ""], mat, mf, mt, sequence + 2),
        ]
    }

    fn fixture_product_tape() -> String {
        let mat = 2759;
        let mut lines = fixture_header(27059, mat, "0", 0, 0);
        lines.push(fixture_record(
            ["27059", "58.9", "0", "0", "0", "0"],
            mat,
            3,
            102,
            1,
        ));
        lines.extend(fixture_tab1(mat, 3, 102, 2, ["0", "0", "0", "0"], "2"));
        lines.push(fixture_send(mat, 3));
        lines.push(fixture_record(
            ["27059", "58.9", "0", "0", "2", "1"],
            mat,
            8,
            102,
            1,
        ));
        lines.push(fixture_record(
            ["26056", "0", "10", "0", "0", "0"],
            mat,
            8,
            102,
            2,
        ));
        lines.push(fixture_record(
            ["26056", "250000", "10", "5", "0", "0"],
            mat,
            8,
            102,
            3,
        ));
        lines.push(fixture_send(mat, 8));
        lines.push(fixture_record(
            ["27059", "58.9", "0", "0", "2", "0"],
            mat,
            10,
            102,
            1,
        ));
        lines.extend(fixture_tab1(
            mat,
            10,
            102,
            2,
            ["1000000", "1000000", "26056", "0"],
            "0.5",
        ));
        lines.extend(fixture_tab1(
            mat,
            10,
            102,
            5,
            ["1000000", "750000", "26056", "5"],
            "0.25",
        ));
        lines.push(fixture_send(mat, 10));
        format!("{}\n", lines.join("\n"))
    }

    #[test]
    fn duplicate_target_error_names_both_sources() {
        let error =
            duplicate_target_error(&target_index("first.endf"), &target_index("second.endf"));
        assert!(error.contains("ZA=26056/LISO=0"), "{error}");
        assert!(error.contains("first.endf"), "{error}");
        assert!(error.contains("second.endf"), "{error}");
    }

    #[test]
    fn generated_endf_fixture_builds_a_v2_physical_state_index() {
        use std::sync::atomic::{AtomicU64, Ordering};

        static NEXT_FIXTURE: AtomicU64 = AtomicU64::new(0);
        let fixture = std::env::temp_dir().join(format!(
            "actinv-p18-state-fixture-{}-{}-{}",
            std::process::id(),
            NEXT_FIXTURE.fetch_add(1, Ordering::Relaxed),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir(&fixture).unwrap();
        let input = fixture.join("input");
        std::fs::create_dir(&input).unwrap();
        let ground = fixture_header(26056, 2631, "0", 0, 0);
        let isomer = fixture_header(26056, 2632, "250000", 2, 1);
        std::fs::write(
            input.join("a-ground.endf"),
            format!("{}\n", ground.join("\n")),
        )
        .unwrap();
        std::fs::write(
            input.join("b-isomer.endf"),
            format!("{}\n", isomer.join("\n")),
        )
        .unwrap();
        std::fs::write(input.join("c-source.endf"), fixture_product_tape()).unwrap();

        let output = fixture.join("candidate.npz");
        let summary = build_library(
            &input,
            &output,
            &BuildOptions {
                format: LibraryFormat::Tendl,
                projectile: Some(Projectile::Neutron),
                groups: GroupStructure {
                    name: "p18-fixture".into(),
                    boundaries_ev: vec![1.0, 4.0],
                },
                temperature_K: 0.0,
                workers: 1,
                cache: None,
                grid_density: 1.0,
                strict_states: false,
            },
        )
        .unwrap();
        let index: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(&summary.index).unwrap()).unwrap();
        assert_eq!(index["schema"], "actinv-library-index-2");
        let mapping = index["targets"]
            .as_array()
            .unwrap()
            .iter()
            .find(|target| target["za"] == 27059)
            .unwrap()["state_mappings"]
            .as_array()
            .unwrap()
            .iter()
            .find(|mapping| mapping["raw_lfs"] == 5)
            .unwrap();
        assert_eq!(mapping["canonical_liso"], 1);
        assert_eq!(mapping["catalog_lis"], 2);
        assert_eq!(mapping["catalog_elis_eV"], 250_000.0);
        assert_eq!(mapping["decision"], "catalog_excitation_match");

        let library = crate::library::read_npz(output.to_str().unwrap()).unwrap();
        let row_number = library
            .rows
            .iter()
            .position(|row| (row.mt, row.zap, row.lfs, row.lmf) == (102, 26056, 1, 10))
            .unwrap();
        assert_eq!(library.sigma(row_number), &[0.25]);
        std::fs::remove_dir_all(fixture).unwrap();
    }

    #[test]
    fn aggregate_mt1_and_mt3_values_cannot_change_emitted_rows() {
        let groups = GroupStructure {
            name: "custom".into(),
            boundaries_ev: vec![1.0, 4.0],
        };
        let mut left = evaluation(Projectile::Neutron);
        left.mf3.insert(1, table([1.0, 2.0]));
        left.mf3.insert(3, table([3.0, 4.0]));
        let mut right = left.clone();
        right.mf3.insert(1, table([1e100, 1e-100]));
        right.mf3.insert(3, table([7.0, 8.0]));
        let settings = EvaluationBuildSettings {
            groups: &groups,
            temperature_K: 0.0,
            grid_density: 1.0,
            strict_states: false,
        };
        let products = BTreeMap::from([(102, (0, 1))]);
        let left = build_evaluation(
            left,
            LibraryFormat::Tendl,
            "n-Fe056",
            &"0".repeat(64),
            settings,
            &products,
        )
        .unwrap();
        let right = build_evaluation(
            right,
            LibraryFormat::Tendl,
            "n-Fe056",
            &"0".repeat(64),
            settings,
            &products,
        )
        .unwrap();
        assert_eq!(left.rows.len(), right.rows.len());
        for (left, right) in left.rows.iter().zip(&right.rows) {
            assert_eq!(
                (left.mt, left.zap, left.lfs, left.lmf, &left.sigma),
                (right.mt, right.zap, right.lfs, right.lmf, &right.sigma)
            );
        }
    }

    #[test]
    fn checkpoint_restores_exact_target_float_bits() {
        let original = target_index("rb94.endf");
        let awr = 105.98700000000001_f64;
        let temperature = -0.0_f64;
        let excitation = 123_456.789_012_345_67_f64;
        let mut cached = original;
        cached.awr = awr;
        cached.evaluation_temperature_K = temperature;
        cached.elis_eV = excitation;
        let bits = [
            cached.awr.to_bits(),
            cached.evaluation_temperature_K.to_bits(),
            cached.elis_eV.to_bits(),
        ];
        let text = serde_json::to_string(&cached).unwrap();
        let mut restored: TargetIndex = serde_json::from_str(&text).unwrap();
        restore_target_float_bits(&mut restored, bits);
        assert_eq!(restored.awr.to_bits(), awr.to_bits());
        assert_eq!(
            restored.evaluation_temperature_K.to_bits(),
            temperature.to_bits()
        );
        assert_eq!(restored.elis_eV.to_bits(), excitation.to_bits());
    }

    #[test]
    fn charged_residual_arithmetic_includes_projectile() {
        let groups = GroupStructure {
            name: "custom".into(),
            boundaries_ev: vec![1.0, 4.0],
        };
        let products = BTreeMap::from([(102, (0, 1))]);
        let built = build_evaluation(
            evaluation(Projectile::Proton),
            LibraryFormat::Tendl,
            "p-Fe056",
            &"0".repeat(64),
            EvaluationBuildSettings {
                groups: &groups,
                temperature_K: 0.0,
                grid_density: 1.0,
                strict_states: false,
            },
            &products,
        )
        .unwrap();
        assert_eq!(built.rows.len(), 2);
        assert_eq!(built.rows[0].zap, -1);
        assert_eq!(built.rows[1].zap, 27057);
        assert_eq!(built.rows[1].sigma, vec![2.0]);
    }

    #[test]
    fn resonance_sections_fail_closed_until_processed() {
        let groups = GroupStructure {
            name: "custom".into(),
            boundaries_ev: vec![1.0, 4.0],
        };
        let mut evaluation = evaluation(Projectile::Neutron);
        evaluation.mf2_sections.insert(151);
        let error = build_evaluation(
            evaluation,
            LibraryFormat::Tendl,
            "n-Fe056",
            &"0".repeat(64),
            EvaluationBuildSettings {
                groups: &groups,
                temperature_K: 0.0,
                grid_density: 1.0,
                strict_states: false,
            },
            &BTreeMap::new(),
        )
        .unwrap_err();
        assert!(
            error.contains("without parsed resonance parameters"),
            "{error}"
        );
    }

    #[test]
    fn unsupported_rml_cannot_fall_back_to_mf3() {
        use crate::resonance::{
            ParticlePair, RMatrixLimited, RangeData, ResonanceEvaluation, ResonanceIsotope,
            ResonanceRange, RmlChannel, RmlResonance, SpinGroup,
        };

        let groups = GroupStructure {
            name: "custom".into(),
            boundaries_ev: vec![1.0, 4.0],
        };
        let mut input = evaluation(Projectile::Neutron);
        input.mf2_sections.insert(151);
        input.resonance = Some(ResonanceEvaluation {
            za: 26056,
            awr: 55.45,
            isotopes: vec![ResonanceIsotope {
                zai: 26056,
                abundance: 1.0,
                fission_widths: false,
                ranges: vec![ResonanceRange {
                    energy_min: 1.0,
                    energy_max: 4.0,
                    lru: 1,
                    lrf: 7,
                    naps: 1,
                    scattering_radius: None,
                    data: RangeData::RMatrixLimited(RMatrixLimited {
                        reduced_widths: false,
                        krm: 3,
                        particle_pairs: vec![ParticlePair {
                            mass_a: 1.0,
                            mass_b: 55.0,
                            za: 0,
                            zb: 26,
                            spin_a: 0.5,
                            spin_b: 0.0,
                            q_value: 0.0,
                            penetrability: 1,
                            shift: 0,
                            mt: 2,
                            parity_a: 1,
                            parity_b: 1,
                        }],
                        spin_groups: vec![SpinGroup {
                            spin: 0.5,
                            parity: 1.0,
                            channels: vec![RmlChannel {
                                pair: 0,
                                l: 0,
                                spin: 0.5,
                                boundary: 0.0,
                                effective_radius: 0.5,
                                true_radius: 0.5,
                            }],
                            resonances: vec![RmlResonance {
                                energy: 2.0,
                                widths: vec![0.4],
                            }],
                            backgrounds: Vec::new(),
                            phase_shifts: Vec::new(),
                        }],
                    }),
                }],
            }],
        });
        let error = build_evaluation(
            input,
            LibraryFormat::Tendl,
            "n-Fe056",
            &"0".repeat(64),
            EvaluationBuildSettings {
                groups: &groups,
                temperature_K: 0.0,
                grid_density: 1.0,
                strict_states: false,
            },
            &BTreeMap::new(),
        )
        .unwrap_err();
        assert!(
            error.contains("needs exactly one eliminated MT=102 channel"),
            "{error}"
        );
    }

    #[test]
    fn resonance_only_capture_uses_an_explicit_zero_background() {
        use crate::resonance::{
            LegacyLGroup, LegacyResolved, LegacyResonance, RangeData, ResonanceEvaluation,
            ResonanceIsotope, ResonanceRange,
        };

        let groups = GroupStructure {
            name: "custom".into(),
            boundaries_ev: vec![1.0, 4.0],
        };
        let mut input = evaluation(Projectile::Neutron);
        input.mf3.clear();
        input.mf2_sections.insert(151);
        input.resonance = Some(ResonanceEvaluation {
            za: 26056,
            awr: 55.45,
            isotopes: vec![ResonanceIsotope {
                zai: 26056,
                abundance: 1.0,
                fission_widths: false,
                ranges: vec![ResonanceRange {
                    energy_min: 1.0,
                    energy_max: 4.0,
                    lru: 1,
                    lrf: 1,
                    naps: 1,
                    scattering_radius: None,
                    data: RangeData::BreitWigner(LegacyResolved {
                        spin: 0.0,
                        ap: 0.5,
                        groups: vec![LegacyLGroup {
                            awri: 55.45,
                            apl: 0.0,
                            qx: 0.0,
                            l: 0,
                            lrx: 0,
                            resonances: vec![LegacyResonance {
                                energy: 2.0,
                                spin: 0.5,
                                total: 0.2,
                                neutron: 0.1,
                                capture: 0.1,
                                fission_a: 0.0,
                                fission_b: 0.0,
                            }],
                        }],
                    }),
                }],
            }],
        });
        let built = build_evaluation(
            input,
            LibraryFormat::Tendl,
            "n-Fe056",
            &"0".repeat(64),
            EvaluationBuildSettings {
                groups: &groups,
                temperature_K: 0.0,
                grid_density: 1.0,
                strict_states: false,
            },
            &BTreeMap::from([(102, (0, 1))]),
        )
        .unwrap();

        assert_eq!(built.rows.len(), 2);
        assert!(built.rows.iter().all(|row| row.mt == 102));
        assert!(built.rows.iter().all(|row| row.sigma[0] > 0.0));
        assert!(built
            .index
            .ledger
            .iter()
            .any(|entry| entry.contains("explicit zero background used")));
    }

    #[test]
    fn inelastic_descriptors_validate_ground_and_metastable_before_ground_omission() {
        let groups = GroupStructure {
            name: "custom".into(),
            boundaries_ev: vec![1.0, 4.0],
        };
        let mut input = evaluation(Projectile::Neutron);
        input.mf3.clear();
        input.mf8.insert(
            4,
            vec![
                ProductRef {
                    zap: 26056,
                    elfs_ev: 0.0,
                    lfs: 0,
                    lmf: 10,
                },
                ProductRef {
                    zap: 26056,
                    elfs_ev: 250_000.0,
                    lfs: 2,
                    lmf: 10,
                },
            ],
        );
        input.mf10.insert(
            4,
            vec![
                crate::activation::ProductTable {
                    zap: 26056,
                    qm_ev: 1_000_000.0,
                    qi_ev: 1_000_000.0,
                    lfs: 0,
                    table: table([3.0, 3.0]),
                },
                crate::activation::ProductTable {
                    zap: 26056,
                    qm_ev: 1_000_000.0,
                    qi_ev: 750_000.0,
                    lfs: 2,
                    table: table([0.25, 0.25]),
                },
            ],
        );
        // P38: an MF=10-only MT is admitted with the self-comparator —
        // internal completeness, ledgered, not an independent total.
        let missing_total = build_evaluation(
            input.clone(),
            LibraryFormat::Tendl,
            "n-Fe056m",
            &"0".repeat(64),
            EvaluationBuildSettings {
                groups: &groups,
                temperature_K: 0.0,
                grid_density: 1.0,
                strict_states: false,
            },
            &BTreeMap::new(),
        )
        .unwrap();
        assert!(
            missing_total
                .index
                .ledger
                .iter()
                .any(|entry| entry.contains("missing_total_self_comparator")),
            "{:?}",
            missing_total.index.ledger
        );
        input.mf3.insert(4, table([4.0, 4.0]));
        let built = build_evaluation(
            input.clone(),
            LibraryFormat::Tendl,
            "n-Fe056m",
            &"0".repeat(64),
            EvaluationBuildSettings {
                groups: &groups,
                temperature_K: 0.0,
                grid_density: 1.0,
                strict_states: false,
            },
            &BTreeMap::new(),
        )
        .unwrap();
        assert_eq!(built.rows.len(), 2);
        assert_eq!((built.rows[0].mt, built.rows[0].zap), (4, -1));
        assert_eq!(built.rows[0].sigma, vec![0.25]);
        assert_eq!(
            (
                built.rows[1].mt,
                built.rows[1].zap,
                built.rows[1].lfs,
                built.rows[1].lmf,
            ),
            (4, 26056, 2, 10)
        );
        assert_eq!(built.rows[1].sigma, vec![0.25]);

        input.mf8.get_mut(&4).unwrap()[0].zap = 25056;
        let error = build_evaluation(
            input,
            LibraryFormat::Tendl,
            "n-Fe056m",
            &"0".repeat(64),
            EvaluationBuildSettings {
                groups: &groups,
                temperature_K: 0.0,
                grid_density: 1.0,
                strict_states: false,
            },
            &BTreeMap::new(),
        )
        .unwrap_err();
        assert!(error.contains("conflict with MF=10 products"), "{error}");
    }

    #[test]
    fn pointwise_state_partial_audit_counts_union_grid_and_state_sum() {
        let total = table([1.0, 1.0]);
        let excess = state_product(
            26056,
            1,
            Tabulated {
                interpolation: vec![(3, 2)],
                x: vec![1.0, 2.0, 4.0],
                y: vec![0.5, 1.1, 0.5],
            },
        );
        let audit = audit_pointwise_state_partials(102, &total, &[&excess]).unwrap();
        assert_eq!(audit.partial_excesses, 2);
        assert_eq!(audit.sum_excesses, 2);
        assert!(audit.max_relative_excess > 0.0);

        let ground = state_product(26056, 0, table([0.6, 0.6]));
        let isomer = state_product(26056, 1, table([0.5, 0.5]));
        let audit = audit_pointwise_state_partials(102, &total, &[&ground, &isomer]).unwrap();
        assert_eq!(audit.partial_excesses, 0);
        // The left limit at the leftmost union abscissa is zero for every
        // table, so only three (energy, side) comparisons carry the 1.1 b sum.
        assert_eq!(audit.sum_excesses, 3);
    }

    #[test]
    fn pointwise_state_partial_audit_checks_left_side_of_double_points() {
        let total = Tabulated {
            interpolation: vec![(4, 2)],
            x: vec![1.0, 2.0, 2.0, 4.0],
            y: vec![2.0, 0.5, 2.0, 2.0],
        };
        let partial = state_product(
            26056,
            1,
            Tabulated {
                interpolation: vec![(4, 2)],
                x: vec![1.0, 2.0, 2.0, 4.0],
                y: vec![1.0, 1.0, 1.0, 1.0],
            },
        );
        let audit = audit_pointwise_state_partials(102, &total, &[&partial]).unwrap();
        assert_eq!(audit.partial_excesses, 1);
        assert_eq!(audit.sum_excesses, 1);
    }

    #[test]
    fn collapsed_state_partial_audit_uses_the_stricter_frozen_tolerance() {
        let groups = GroupStructure {
            name: "custom".into(),
            boundaries_ev: vec![1.0, 4.0],
        };
        let total = table([0.0, 0.0]);
        let partial = state_product(26056, 1, table([5e-13, 5e-13]));
        let pointwise = audit_pointwise_state_partials(102, &total, &[&partial]).unwrap();
        assert_eq!(pointwise.excesses(), 0);
        let collapsed_total = checked_collapse(&groups, &total, "total").unwrap();
        let collapsed_products = collapse_mf10_products(&groups, 102, &[&partial]).unwrap();
        let audit =
            audit_collapsed_state_partials(102, &collapsed_total, &collapsed_products).unwrap();
        assert_eq!(audit.partial_excesses, 1);
        assert_eq!(audit.sum_excesses, 1);
        assert_eq!(audit.max_relative_excess, f64::INFINITY);
    }

    #[test]
    fn runtime_reconciliation_leaves_conformant_sums_byte_identical() {
        let total = vec![1.0, 2.0];
        let mut ground = vec![0.6, 0.4];
        let mut isomer = vec![0.3, 0.0];
        let report = reconcile_emitted_states(
            102,
            10,
            26056,
            &mut [&mut ground, &mut isomer],
            &total,
            false,
            false,
            2631,
            26000,
            &"f".repeat(64),
        )
        .unwrap();
        assert_eq!(report.checked, 2);
        assert_eq!(report.scaled, 0);
        assert_eq!(ground, vec![0.6, 0.4]);
        assert_eq!(isomer, vec![0.3, 0.0]);
    }

    #[test]
    fn runtime_reconciliation_scales_inside_envelope_preserving_ratios_and_zero() {
        let total = vec![1.0, 4.0];
        let mut ground = vec![0.6005, 0.0];
        let mut isomer = vec![0.4, 2.0];
        let report = reconcile_emitted_states(
            102,
            10,
            26056,
            &mut [&mut ground, &mut isomer],
            &total,
            false,
            false,
            2631,
            26000,
            &"f".repeat(64),
        )
        .unwrap();
        assert_eq!(report.scaled, 1);
        assert_eq!(report.checked, 2);
        let sum: f64 = ground[0] + isomer[0];
        assert!(sum <= 1.0, "reconciled sum {sum} exceeds the runtime total");
        assert!(
            (ground[0] / isomer[0] - 0.6005 / 0.4).abs() < 1e-15,
            "common-factor scaling must preserve state ratios: {ground:?} {isomer:?}"
        );
        assert_eq!(ground[1], 0.0);
        assert_eq!(isomer[1], 2.0);
        assert!((0.6005 * report.min_scale - ground[0]).abs() < 1e-16);
    }

    #[test]
    fn runtime_reconciliation_fails_closed_outside_the_standard_envelope() {
        let total = vec![1.0];
        let mut ground = vec![0.7];
        let mut isomer = vec![0.302];
        let error = reconcile_emitted_states(
            16,
            10,
            85197,
            &mut [&mut ground, &mut isomer],
            &total,
            false,
            false,
            8533,
            85000,
            &"a".repeat(64),
        )
        .unwrap_err();
        for required in [
            "MT16/MF=10 ZAP=85197 group 0",
            "emitted state sum 1.002",
            "runtime total 1.00000000000000000e0",
            "outside the frozen 0.001 standard envelope",
            "MAT=8533",
            "ZA=85000",
            "source_sha256=",
            "fails closed",
        ] {
            assert!(error.contains(required), "{error} missing '{required}'");
        }
        assert_eq!(ground, vec![0.7], "failed rows must not be scaled");
        assert_eq!(isomer, vec![0.302]);
    }

    #[test]
    fn runtime_reconciliation_zero_total_uses_the_absolute_bound() {
        let total = vec![0.0];
        let mut ground = vec![0.0006];
        let mut isomer = vec![0.0003];
        let report = reconcile_emitted_states(
            102,
            10,
            26056,
            &mut [&mut ground, &mut isomer],
            &total,
            false,
            false,
            2631,
            26000,
            &"f".repeat(64),
        )
        .unwrap();
        assert_eq!(report.scaled, 1);
        assert_eq!(ground, vec![0.0]);
        assert_eq!(isomer, vec![0.0]);

        let mut excess = vec![0.002];
        let error = reconcile_emitted_states(
            102,
            10,
            26056,
            &mut [&mut excess],
            &total,
            false,
            false,
            2631,
            26000,
            &"f".repeat(64),
        )
        .unwrap_err();
        assert!(error.contains("outside the frozen 0.001"), "{error}");
    }

    #[test]
    fn runtime_reconciliation_strict_option_rejects_every_excess() {
        let total = vec![1.0];
        let mut ground = vec![0.6005];
        let mut isomer = vec![0.4];
        let error = reconcile_emitted_states(
            102,
            10,
            26056,
            &mut [&mut ground, &mut isomer],
            &total,
            true,
            false,
            2631,
            26000,
            &"f".repeat(64),
        )
        .unwrap_err();
        assert!(error.contains("strict state-conservation"), "{error}");
        assert_eq!(ground, vec![0.6005]);
    }

    #[test]
    fn emitted_mf10_states_reconcile_inside_the_envelope_and_audit_stays_diagnostic() {
        let groups = GroupStructure {
            name: "custom".into(),
            boundaries_ev: vec![1.0, 4.0],
        };
        let mut input = evaluation(Projectile::Neutron);
        input.mf10.insert(
            102,
            vec![
                state_product(26057, 0, table([1.2, 1.2])),
                state_product(26057, 1, table([0.801, 0.801])),
            ],
        );
        let built = build_evaluation(
            input,
            LibraryFormat::Tendl,
            "n-Fe056",
            &"0".repeat(64),
            EvaluationBuildSettings {
                groups: &groups,
                temperature_K: 0.0,
                grid_density: 1.0,
                strict_states: false,
            },
            &BTreeMap::new(),
        )
        .unwrap();
        let scale = 2.0 / 2.001;
        assert_eq!(built.rows[0].sigma, vec![2.0], "loss row must not change");
        assert!(
            (built.rows[1].sigma[0] - 1.2 * scale).abs() < 1e-15,
            "{:?}",
            built.rows[1].sigma
        );
        assert!((built.rows[2].sigma[0] - 0.801 * scale).abs() < 1e-15);
        let sum: f64 = built.rows[1].sigma[0] + built.rows[2].sigma[0];
        assert!(sum <= 2.0, "reconciled sum {sum} exceeds the runtime total");
        assert!(
            built
                .index
                .ledger
                .iter()
                .any(|line| line.contains("ZAP=26057")
                    && line.contains("scaled by the common factor"))
        );
        assert!(built
            .index
            .ledger
            .iter()
            .any(|line| line.contains("raw MF=10-vs-MF=3 audit") && line.contains("diagnostics")));
    }

    #[test]
    fn emitted_mf10_states_fail_closed_outside_the_envelope() {
        let groups = GroupStructure {
            name: "custom".into(),
            boundaries_ev: vec![1.0, 4.0],
        };
        let mut input = evaluation(Projectile::Neutron);
        input.mf10.insert(
            102,
            vec![
                state_product(26057, 0, table([1.2, 1.2])),
                state_product(26057, 1, table([0.81, 0.81])),
            ],
        );
        let error = build_evaluation(
            input,
            LibraryFormat::Tendl,
            "n-Fe056",
            &"0".repeat(64),
            EvaluationBuildSettings {
                groups: &groups,
                temperature_K: 0.0,
                grid_density: 1.0,
                strict_states: false,
            },
            &BTreeMap::new(),
        )
        .unwrap_err();
        assert!(error.contains("MT102/MF=10 ZAP=26057 group 0"), "{error}");
        assert!(error.contains("fails closed"), "{error}");
    }

    #[test]
    fn emitted_mf9_production_reconciles_against_the_runtime_total() {
        let groups = GroupStructure {
            name: "custom".into(),
            boundaries_ev: vec![1.0, 4.0],
        };
        let mut input = evaluation(Projectile::Neutron);
        input.mf9.insert(
            102,
            vec![
                state_product(26057, 0, table([0.6, 0.6])),
                state_product(26057, 1, table([0.4005, 0.4005])),
            ],
        );
        let built = build_evaluation(
            input,
            LibraryFormat::Tendl,
            "n-Fe056",
            &"0".repeat(64),
            EvaluationBuildSettings {
                groups: &groups,
                temperature_K: 0.0,
                grid_density: 1.0,
                strict_states: false,
            },
            &BTreeMap::new(),
        )
        .unwrap();
        let scale = 2.0 / 2.001;
        assert!(
            (built.rows[1].sigma[0] - 1.2 * scale).abs() < 1e-15,
            "{:?}",
            built.rows[1].sigma
        );
        assert!((built.rows[2].sigma[0] - 0.801 * scale).abs() < 1e-15);
        assert!(built
            .index
            .ledger
            .iter()
            .any(|line| line.contains("MF=9 ZAP=26057") && line.contains("scaled")));
    }

    #[test]
    fn strict_states_option_rejects_an_envelope_compatible_excess() {
        let groups = GroupStructure {
            name: "custom".into(),
            boundaries_ev: vec![1.0, 4.0],
        };
        let mut input = evaluation(Projectile::Neutron);
        input
            .mf10
            .insert(102, vec![state_product(26057, 0, table([2.001, 2.001]))]);
        let error = build_evaluation(
            input,
            LibraryFormat::Tendl,
            "n-Fe056",
            &"0".repeat(64),
            EvaluationBuildSettings {
                groups: &groups,
                temperature_K: 0.0,
                grid_density: 1.0,
                strict_states: true,
            },
            &BTreeMap::new(),
        )
        .unwrap_err();
        assert!(error.contains("strict state-conservation"), "{error}");
    }

    #[test]
    fn physical_state_mapping_uses_excitation_and_catalog_liso() {
        let mut sources = vec![BuiltSource {
            format: LibraryFormat::Tendl,
            projectile: Projectile::Neutron,
            targets: vec![
                state_target(
                    "n-Fe056.endf",
                    26056,
                    0,
                    0,
                    0.0,
                    vec![state_row(4, 26056, 5, Some(250_000.0))],
                ),
                state_target("n-Fe056m.endf", 26056, 1, 2, 250_000.0, Vec::new()),
            ],
            from_cache: false,
        }];
        let catalog = build_state_catalog(&sources).unwrap();
        let row_before = sources[0].targets[0].rows[0].sigma.clone();
        map_product_states(&mut sources[0].targets[0], &catalog).unwrap();

        let target = &sources[0].targets[0];
        assert_eq!(target.rows[0].lfs, 1);
        assert_eq!(target.rows[0].sigma, row_before);
        assert_eq!(target.index.state_mappings.len(), 1);
        let mapping = &target.index.state_mappings[0];
        assert_eq!(mapping.raw_lfs, 5);
        assert_eq!(mapping.canonical_liso, Some(1));
        assert_eq!(mapping.catalog_lis, Some(2));
        assert_eq!(mapping.catalog_elis_eV, Some(250_000.0));
        assert_eq!(mapping.catalog_file.as_deref(), Some("n-Fe056m.endf"));
        let expected_source = "0".repeat(64);
        assert_eq!(
            mapping.catalog_source_sha256.as_deref(),
            Some(expected_source.as_str())
        );
        assert_eq!(mapping.catalog_evaluations, Some(1));
        assert_eq!(mapping.excitation_delta_eV, Some(0.0));
        assert_eq!(mapping.decision, "catalog_excitation_match");
    }

    #[test]
    fn distinct_isomers_map_by_physics_without_rank_compression() {
        let source = BuiltSource {
            format: LibraryFormat::Tendl,
            projectile: Projectile::Neutron,
            targets: vec![
                state_target("ground.endf", 26056, 0, 0, 0.0, Vec::new()),
                state_target("m1.endf", 26056, 1, 2, 250_000.0, Vec::new()),
                state_target("m2.endf", 26056, 2, 5, 750_000.0, Vec::new()),
            ],
            from_cache: false,
        };
        let catalog = build_state_catalog(&[source]).unwrap();
        let mut target = state_target(
            "source.endf",
            27059,
            0,
            0,
            0.0,
            vec![
                state_row(16, 26056, 8, Some(750_000.0)),
                state_row(102, 26056, 4, Some(250_000.0)),
            ],
        );
        map_product_states(&mut target, &catalog).unwrap();
        assert_eq!(target.rows[0].lfs, 2);
        assert_eq!(target.rows[1].lfs, 1);
    }

    #[test]
    fn state_mapping_is_independent_of_reaction_and_row_order() {
        let catalog_source = BuiltSource {
            format: LibraryFormat::Tendl,
            projectile: Projectile::Neutron,
            targets: vec![
                state_target("ground.endf", 26056, 0, 0, 0.0, Vec::new()),
                state_target("isomer.endf", 26056, 1, 3, 250_000.0, Vec::new()),
            ],
            from_cache: false,
        };
        let catalog = build_state_catalog(&[catalog_source]).unwrap();
        let mut forward = state_target(
            "forward.endf",
            27059,
            0,
            0,
            0.0,
            vec![
                state_row(4, 26056, 2, Some(250_000.0)),
                state_row(102, 26056, 9, Some(250_000.0)),
            ],
        );
        let mut reverse = state_target(
            "reverse.endf",
            27059,
            0,
            0,
            0.0,
            vec![
                state_row(102, 26056, 9, Some(250_000.0)),
                state_row(4, 26056, 2, Some(250_000.0)),
            ],
        );

        map_product_states(&mut forward, &catalog).unwrap();
        map_product_states(&mut reverse, &catalog).unwrap();
        let mut forward_identity: Vec<_> = forward
            .rows
            .iter()
            .map(|row| (row.mt, row.zap, row.lfs, row.lmf, row.sigma.clone()))
            .collect();
        let mut reverse_identity: Vec<_> = reverse
            .rows
            .iter()
            .map(|row| (row.mt, row.zap, row.lfs, row.lmf, row.sigma.clone()))
            .collect();
        forward_identity.sort_by_key(|row| row.0);
        reverse_identity.sort_by_key(|row| row.0);
        assert_eq!(forward_identity, reverse_identity);
        assert!(forward_identity.iter().all(|row| row.2 == 1));
    }

    #[test]
    fn unmapped_state_becomes_explicit_leakage_without_changing_strength() {
        let source = BuiltSource {
            format: LibraryFormat::Tendl,
            projectile: Projectile::Neutron,
            targets: vec![
                state_target("ground.endf", 26056, 0, 0, 0.0, Vec::new()),
                state_target("isomer.endf", 26056, 1, 2, 250_000.0, Vec::new()),
            ],
            from_cache: false,
        };
        let catalog = build_state_catalog(&[source]).unwrap();
        let mut target = state_target(
            "source.endf",
            27059,
            0,
            0,
            0.0,
            vec![state_row(102, 26056, 3, Some(400_000.0))],
        );
        let before: f64 = target.rows[0].sigma.iter().sum();
        map_product_states(&mut target, &catalog).unwrap();

        let row = &target.rows[0];
        assert_eq!((row.zap, row.lfs, row.lmf), (0, 0, -3));
        assert_eq!(row.sigma.iter().sum::<f64>().to_bits(), before.to_bits());
        assert_eq!(
            target.index.state_mappings[0].decision,
            "no_catalog_excitation_match_to_leakage"
        );
    }

    #[test]
    fn lumped_channels_synthesize_only_when_summary_coverage_is_absent() {
        let groups = GroupStructure {
            name: "custom".into(),
            boundaries_ev: vec![1.0, 4.0],
        };
        fn settings<'a>(groups: &'a GroupStructure) -> EvaluationBuildSettings<'a> {
            EvaluationBuildSettings {
                groups,
                temperature_K: 0.0,
                grid_density: 1.0,
                strict_states: false,
            }
        }
        let products = mt_products().unwrap();

        // Lumped-only (n,p) 600-649 + (n,alpha) 800-849 synthesize the
        // canonical summary-MT rows on the family residuals.
        let mut input = evaluation(Projectile::Neutron);
        input.mf3.insert(600, table([1.0, 1.0]));
        input.mf3.insert(649, table([0.5, 0.5]));
        input.mf3.insert(800, table([2.0, 2.0]));
        let built = build_evaluation(
            input,
            LibraryFormat::Tendl,
            "n-Fe056",
            &"0".repeat(64),
            settings(&groups),
            &products,
        )
        .unwrap();
        let synthesized: Vec<_> = built
            .rows
            .iter()
            .filter(|row| row.mt == 103 || row.mt == 107)
            .collect();
        assert_eq!(synthesized.len(), 2);
        let np = synthesized.iter().find(|row| row.mt == 103).unwrap();
        assert_eq!((np.zap, np.lfs), (25056, 0));
        assert_eq!(np.sigma.iter().sum::<f64>(), 1.5);
        let na = synthesized.iter().find(|row| row.mt == 107).unwrap();
        assert_eq!((na.zap, na.lfs), (24053, 0));
        assert_eq!(na.sigma.iter().sum::<f64>(), 2.0);
        assert!(built
            .index
            .ledger
            .iter()
            .any(|entry| entry.contains("lumped_channel_synthesis")));

        // A present summary MT governs: lumped sections skip, no double row.
        let mut input = evaluation(Projectile::Neutron);
        input.mf3.insert(103, table([4.0, 4.0]));
        input.mf3.insert(600, table([1.0, 1.0]));
        let built = build_evaluation(
            input,
            LibraryFormat::Tendl,
            "n-Fe056",
            &"0".repeat(64),
            settings(&groups),
            &products,
        )
        .unwrap();
        let np_rows: Vec<_> = built
            .rows
            .iter()
            .filter(|row| row.mt == 103 && row.zap == 25056)
            .collect();
        assert_eq!(np_rows.len(), 1);
        assert_eq!(np_rows[0].sigma.iter().sum::<f64>(), 4.0);
        assert!(built
            .index
            .ledger
            .iter()
            .any(|entry| entry.contains("coverage already governs")));

        // MF10 partial coverage of the summary MT also skips lumped.
        let mut input = evaluation(Projectile::Neutron);
        input.mf3.insert(600, table([1.0, 1.0]));
        input
            .mf10
            .insert(103, vec![state_product(25056, 0, table([3.0, 3.0]))]);
        let built = build_evaluation(
            input,
            LibraryFormat::Tendl,
            "n-Fe056",
            &"0".repeat(64),
            settings(&groups),
            &products,
        )
        .unwrap();
        let np_rows: Vec<_> = built
            .rows
            .iter()
            .filter(|row| row.mt == 103 && row.zap == 25056)
            .collect();
        assert_eq!(np_rows.len(), 1);
        assert_eq!(np_rows[0].sigma.iter().sum::<f64>(), 3.0);
        assert!(built
            .index
            .ledger
            .iter()
            .any(|entry| entry.contains("coverage already governs")));
    }

    #[test]
    fn ambiguous_catalog_and_conflicting_excitation_fail_closed() {
        let source = BuiltSource {
            format: LibraryFormat::Tendl,
            projectile: Projectile::Neutron,
            targets: vec![
                state_target("ground.endf", 26056, 0, 0, 0.0, Vec::new()),
                state_target("m1.endf", 26056, 1, 1, 250_000.0, Vec::new()),
                state_target("m2.endf", 26056, 2, 2, 250_000.5, Vec::new()),
            ],
            from_cache: false,
        };
        let catalog = build_state_catalog(&[source]).unwrap();
        let mut target = state_target(
            "source.endf",
            27059,
            0,
            0,
            0.0,
            vec![state_row(102, 26056, 4, Some(250_000.25))],
        );
        let error = map_product_states(&mut target, &catalog).unwrap_err();
        assert!(error.contains("matches multiple catalog states"), "{error}");

        let conflict = RawProductState {
            raw_lfs: 1,
            elfs_eV: Some(250_000.0),
            qm_eV: Some(1_000_000.0),
            qi_eV: Some(600_000.0),
        };
        let error = conflict.excitation_eV().unwrap_err();
        assert!(error.contains("conflicts with QM-QI"), "{error}");
    }

    #[test]
    fn duplicate_catalog_agreement_is_order_independent_and_conflict_fails() {
        let low = BuiltSource {
            format: LibraryFormat::Tendl,
            projectile: Projectile::Neutron,
            targets: vec![state_target("low.endf", 26056, 1, 2, 250_000.0, Vec::new())],
            from_cache: false,
        };
        let high = BuiltSource {
            format: LibraryFormat::Tendl,
            projectile: Projectile::Neutron,
            targets: vec![state_target(
                "high.endf",
                26056,
                1,
                2,
                250_000.5,
                Vec::new(),
            )],
            from_cache: false,
        };
        let forward = build_state_catalog(&[low.clone(), high.clone()]).unwrap();
        let reverse = build_state_catalog(&[high.clone(), low.clone()]).unwrap();
        let forward_state = &forward[&26056][0];
        let reverse_state = &reverse[&26056][0];
        assert_eq!(forward_state.representative.elis_eV, 250_000.0);
        assert_eq!(forward_state.representative.file, "low.endf");
        assert_eq!(forward_state.evaluations.len(), 2);
        assert_eq!(forward_state.decision, "duplicate_evaluations_agree");
        assert_eq!(
            (
                forward_state.representative.elis_eV,
                &forward_state.representative.file,
            ),
            (
                reverse_state.representative.elis_eV,
                &reverse_state.representative.file,
            )
        );

        let mut conflicting = high;
        conflicting.targets[0].index.elis_eV = 250_010.0;
        let error = build_state_catalog(&[low, conflicting]).unwrap_err();
        assert!(error.contains("conflicting ELIS values"), "{error}");
    }

    #[test]
    fn duplicate_product_declarations_require_exact_agreement() {
        let product = ProductTable {
            zap: 26056,
            qm_ev: 1_000_000.0,
            qi_ev: 750_000.0,
            lfs: 2,
            table: table([0.25, 0.5]),
        };
        let products = vec![product.clone(), product.clone()];
        let unique = unique_product_tables(&products, "MT102/MF=10").unwrap();
        assert_eq!(unique, vec![&product]);

        let mut conflict = product.clone();
        conflict.qi_ev = 749_999.0;
        let error = unique_product_tables(&[product, conflict], "MT102/MF=10").unwrap_err();
        assert!(error.contains("conflicting duplicate product"), "{error}");

        let descriptor = ProductRef {
            zap: 26056,
            elfs_ev: 250_000.0,
            lfs: 2,
            lmf: 10,
        };
        assert_eq!(
            descriptor_set(&[descriptor, descriptor], 10).unwrap(),
            BTreeSet::from([(26056, 2)])
        );
        let conflicting_descriptor = ProductRef {
            elfs_ev: 250_002.0,
            ..descriptor
        };
        let error = descriptor_set(&[descriptor, conflicting_descriptor], 10).unwrap_err();
        assert!(error.contains("conflicting duplicate MF=8"), "{error}");
    }

    #[test]
    fn unsupported_state_metadata_is_explicit_and_tolerance_is_inclusive() {
        let source = BuiltSource {
            format: LibraryFormat::Tendl,
            projectile: Projectile::Neutron,
            targets: vec![state_target("ground.endf", 26056, 0, 0, 0.0, Vec::new())],
            from_cache: false,
        };
        let catalog = build_state_catalog(&[source]).unwrap();
        let mut target = state_target(
            "source.endf",
            27059,
            0,
            0,
            0.0,
            vec![
                state_row(4, 26056, 0, Some(0.0)),
                state_row(16, 26056, 98, Some(250_000.0)),
                state_row(102, 26056, 2, None),
            ],
        );
        map_product_states(&mut target, &catalog).unwrap();
        assert_eq!((target.rows[0].zap, target.rows[0].lfs), (26056, 0));
        assert_eq!((target.rows[1].zap, target.rows[1].lmf), (0, -3));
        assert_eq!((target.rows[2].zap, target.rows[2].lmf), (0, -3));
        assert_eq!(
            target.index.state_mappings[1].decision,
            "unspecified_lfs98_to_leakage"
        );
        assert_eq!(
            target.index.state_mappings[2].decision,
            "missing_excitation_to_leakage"
        );

        let boundary = RawProductState {
            raw_lfs: 1,
            elfs_eV: Some(200_000.0),
            qm_eV: Some(1_200_001.0),
            qi_eV: Some(1_000_000.0),
        };
        assert_eq!(boundary.excitation_eV().unwrap(), Some(200_000.0));
        // P25 Amendment B: a 2 eV conflict is inside the 1 keV precedence
        // bound and resolves to the evaluated ELFS; beyond the bound fails.
        let within_bound = RawProductState {
            qm_eV: Some(1_200_002.0),
            ..boundary
        };
        assert_eq!(within_bound.excitation_eV().unwrap(), Some(200_000.0));
        assert_eq!(within_bound.elfs_qm_qi_conflict_eV(), Some(2.0));
        let outside = RawProductState {
            qm_eV: Some(1_202_000.0),
            ..boundary
        };
        let error = outside.excitation_eV().unwrap_err();
        assert!(error.contains("conflicts with QM-QI"), "{error}");
        assert!(error.contains("precedence bound"), "{error}");
    }
}
