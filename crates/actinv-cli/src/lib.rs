//! Command-line support that does not belong to the scientific solver.

pub mod command;

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{File, OpenOptions};
use std::io::{BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

const CATALOG_JSON: &str = include_str!("../data/actinv-data-catalog-v1.0.0.json");
const MAX_ARTIFACT_BYTES: u64 = 2_000_000_000;
const COPY_BUFFER_BYTES: usize = 64 * 1024;
const PROGRESS_INTERVAL_BYTES: u64 = 16 * 1024 * 1024;
static TEMPORARY_NONCE: AtomicU64 = AtomicU64::new(0);

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum ArtifactRole {
    ActivationLibrary,
    ActivationIndex,
    CovarianceSidecar,
    CovarianceIndex,
    DecayPrimary,
    DecayFallback,
    Notice,
}

impl ArtifactRole {
    pub fn name(self) -> &'static str {
        match self {
            Self::ActivationLibrary => "activation-library",
            Self::ActivationIndex => "activation-index",
            Self::CovarianceSidecar => "covariance-sidecar",
            Self::CovarianceIndex => "covariance-index",
            Self::DecayPrimary => "decay-primary",
            Self::DecayFallback => "decay-fallback",
            Self::Notice => "notice",
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct DownloadSource {
    pub url: String,
    pub bytes: u64,
    pub sha256: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub archive_member: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct DataArtifact {
    pub id: String,
    pub role: ArtifactRole,
    pub path: String,
    pub bytes: u64,
    pub sha256: String,
    pub licence: String,
    pub source: DownloadSource,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct DataBundle {
    pub id: String,
    pub description: String,
    pub projectile: String,
    pub groups: String,
    #[serde(rename = "temperature_K")]
    pub temperature_k: f64,
    pub artifacts: Vec<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct DataCatalog {
    pub schema: String,
    pub catalog_version: String,
    pub default_bundle: String,
    pub release_url: String,
    pub notice: String,
    pub artifacts: Vec<DataArtifact>,
    pub bundles: Vec<DataBundle>,
}

fn valid_identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 96
        && value
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
        && !value.starts_with('-')
        && !value.ends_with('-')
        && !value.contains("--")
}

fn valid_version(value: &str) -> bool {
    let parts: Vec<_> = value.split('.').collect();
    parts.len() == 3
        && parts.iter().all(|part| {
            !part.is_empty()
                && part.bytes().all(|byte| byte.is_ascii_digit())
                && (part == &"0" || !part.starts_with('0'))
        })
}

fn valid_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn validate_relative_path(value: &str, label: &str) -> Result<(), String> {
    if value.is_empty()
        || value.len() > 240
        || value.starts_with('/')
        || value.ends_with('/')
        || value.bytes().any(|byte| byte == 92)
        || value.contains(':')
        || value.bytes().any(|byte| byte.is_ascii_control())
        || value
            .split('/')
            .any(|component| component.is_empty() || component == "." || component == "..")
    {
        return Err(format!(
            "{label} '{value}' is not a safe portable relative path"
        ));
    }
    Ok(())
}

fn validate_source(source: &DownloadSource, artifact: &DataArtifact) -> Result<(), String> {
    if !source.url.starts_with("https://")
        || source.url.len() > 2048
        || source.url.bytes().any(|byte| byte.is_ascii_whitespace())
    {
        return Err(format!(
            "artifact '{}' source URL must be a bounded HTTPS URL",
            artifact.id
        ));
    }
    for (label, bytes, sha256) in [
        ("installed", artifact.bytes, artifact.sha256.as_str()),
        ("source", source.bytes, source.sha256.as_str()),
    ] {
        if bytes == 0 || bytes > MAX_ARTIFACT_BYTES {
            return Err(format!(
                "artifact '{}' {label} byte count is outside 1..={MAX_ARTIFACT_BYTES}",
                artifact.id
            ));
        }
        if !valid_sha256(sha256) {
            return Err(format!(
                "artifact '{}' {label} SHA-256 must be 64 lowercase hexadecimal digits",
                artifact.id
            ));
        }
    }
    if let Some(member) = &source.archive_member {
        validate_relative_path(member, "archive member")?;
        if member.contains('/') {
            return Err(format!(
                "artifact '{}' archive member must be one filename",
                artifact.id
            ));
        }
    } else if source.bytes != artifact.bytes || source.sha256 != artifact.sha256 {
        return Err(format!(
            "direct source identity for artifact '{}' differs from its installed identity",
            artifact.id
        ));
    }
    Ok(())
}

impl DataCatalog {
    pub fn parse(json: &str) -> Result<Self, String> {
        let catalog: Self = serde_json::from_str(json)
            .map_err(|error| format!("cannot parse data catalog: {error}"))?;
        catalog.validate()?;
        Ok(catalog)
    }

    pub fn validate(&self) -> Result<(), String> {
        if self.schema != "actinv-data-catalog-1" {
            return Err(format!("unsupported data catalog schema '{}'", self.schema));
        }
        if !valid_version(&self.catalog_version) {
            return Err("data catalog version must be MAJOR.MINOR.PATCH".into());
        }
        if !self.release_url.starts_with("https://") || self.release_url.len() > 2048 {
            return Err("data catalog release_url must be a bounded HTTPS URL".into());
        }
        validate_relative_path(&self.notice, "catalog notice")?;

        let mut artifact_ids = BTreeSet::new();
        let mut artifact_paths: BTreeMap<&str, &DataArtifact> = BTreeMap::new();
        for artifact in &self.artifacts {
            if !valid_identifier(&artifact.id) || !artifact_ids.insert(artifact.id.as_str()) {
                return Err(format!(
                    "artifact ID '{}' is invalid or duplicated",
                    artifact.id
                ));
            }
            validate_relative_path(&artifact.path, "artifact path")?;
            if artifact.licence.trim().is_empty() || artifact.licence.len() > 128 {
                return Err(format!(
                    "artifact '{}' has no bounded licence label",
                    artifact.id
                ));
            }
            if let Some(previous) = artifact_paths.insert(&artifact.path, artifact) {
                return Err(format!(
                    "artifacts '{}' and '{}' use the same destination path",
                    previous.id, artifact.id
                ));
            }
            validate_source(&artifact.source, artifact)?;
        }

        let by_id: BTreeMap<_, _> = self
            .artifacts
            .iter()
            .map(|artifact| (artifact.id.as_str(), artifact))
            .collect();
        let mut bundle_ids = BTreeSet::new();
        let mut referenced = BTreeSet::new();
        for bundle in &self.bundles {
            if !valid_identifier(&bundle.id) || !bundle_ids.insert(bundle.id.as_str()) {
                return Err(format!(
                    "bundle ID '{}' is invalid or duplicated",
                    bundle.id
                ));
            }
            if bundle.description.trim().is_empty() || bundle.description.len() > 240 {
                return Err(format!(
                    "bundle '{}' needs a bounded description",
                    bundle.id
                ));
            }
            let expected_groups = match bundle.projectile.as_str() {
                "neutron" => "fispact-709",
                "proton" | "deuteron" | "alpha" => "fispact-162",
                other => {
                    return Err(format!(
                        "bundle '{}' has unsupported projectile '{other}'",
                        bundle.id
                    ))
                }
            };
            if bundle.groups != expected_groups
                || !bundle.temperature_k.is_finite()
                || (bundle.projectile == "neutron" && bundle.temperature_k != 293.6)
                || (bundle.projectile != "neutron" && bundle.temperature_k != 0.0)
            {
                return Err(format!(
                    "bundle '{}' projectile, groups, and temperature are inconsistent",
                    bundle.id
                ));
            }
            let mut local_ids = BTreeSet::new();
            let mut roles: BTreeMap<ArtifactRole, &DataArtifact> = BTreeMap::new();
            for id in &bundle.artifacts {
                if !local_ids.insert(id.as_str()) {
                    return Err(format!("bundle '{}' repeats artifact '{id}'", bundle.id));
                }
                let artifact = by_id.get(id.as_str()).ok_or_else(|| {
                    format!("bundle '{}' names unknown artifact '{id}'", bundle.id)
                })?;
                if let Some(previous) = roles.insert(artifact.role, artifact) {
                    return Err(format!(
                        "bundle '{}' has two {} artifacts ('{}' and '{}')",
                        bundle.id,
                        artifact.role.name(),
                        previous.id,
                        artifact.id
                    ));
                }
                referenced.insert(id.as_str());
            }
            for required in [
                ArtifactRole::ActivationLibrary,
                ArtifactRole::ActivationIndex,
                ArtifactRole::DecayPrimary,
                ArtifactRole::DecayFallback,
                ArtifactRole::Notice,
            ] {
                if !roles.contains_key(&required) {
                    return Err(format!(
                        "bundle '{}' has no {} artifact",
                        bundle.id,
                        required.name()
                    ));
                }
            }
            let library = roles[&ArtifactRole::ActivationLibrary];
            let index = roles[&ArtifactRole::ActivationIndex];
            if actinv_data::builder::index_path(&library.path)? != Path::new(&index.path) {
                return Err(format!(
                    "bundle '{}' activation library/index filenames are not adjacent",
                    bundle.id
                ));
            }
            match (
                roles.get(&ArtifactRole::CovarianceSidecar),
                roles.get(&ArtifactRole::CovarianceIndex),
            ) {
                (Some(sidecar), Some(index)) if bundle.projectile == "neutron" => {
                    if actinv_data::covariance::index_path(&sidecar.path)? != Path::new(&index.path)
                    {
                        return Err(format!(
                            "bundle '{}' covariance sidecar/index filenames are not adjacent",
                            bundle.id
                        ));
                    }
                }
                (None, None) => {}
                _ => {
                    return Err(format!(
                        "bundle '{}' has an incomplete or non-neutron covariance pair",
                        bundle.id
                    ))
                }
            }
        }
        if !bundle_ids.contains(self.default_bundle.as_str()) {
            return Err(format!(
                "default bundle '{}' is absent from the catalog",
                self.default_bundle
            ));
        }
        if referenced.len() != self.artifacts.len() {
            let unreferenced: Vec<_> = artifact_ids.difference(&referenced).copied().collect();
            return Err(format!(
                "catalog has unreferenced artifacts: {}",
                unreferenced.join(", ")
            ));
        }
        Ok(())
    }

    pub fn bundle(&self, id: Option<&str>) -> Result<&DataBundle, String> {
        let id = id.unwrap_or(&self.default_bundle);
        self.bundles
            .iter()
            .find(|bundle| bundle.id == id)
            .ok_or_else(|| {
                let available = self
                    .bundles
                    .iter()
                    .map(|bundle| bundle.id.as_str())
                    .collect::<Vec<_>>()
                    .join(", ");
                format!("unknown data bundle '{id}'; available bundles: {available}")
            })
    }

    pub fn artifact(&self, id: &str) -> Result<&DataArtifact, String> {
        self.artifacts
            .iter()
            .find(|artifact| artifact.id == id)
            .ok_or_else(|| format!("catalog has no artifact '{id}'"))
    }

    pub fn source_download_bytes(&self, bundle: &DataBundle) -> Result<u64, String> {
        bundle.artifacts.iter().try_fold(0_u64, |total, id| {
            total
                .checked_add(self.artifact(id)?.source.bytes)
                .ok_or_else(|| format!("bundle '{}' download size overflows", bundle.id))
        })
    }
}

pub fn embedded_catalog_json() -> &'static str {
    CATALOG_JSON
}

pub fn embedded_catalog() -> Result<DataCatalog, String> {
    DataCatalog::parse(CATALOG_JSON)
}

struct Download {
    reader: Box<dyn Read>,
    content_length: Option<u64>,
}

trait Downloader {
    fn open(&self, source: &DownloadSource) -> Result<Download, String>;
}

struct NetworkDownloader {
    agent: ureq::Agent,
}

impl NetworkDownloader {
    fn new() -> Self {
        Self {
            agent: ureq::Agent::new_with_defaults(),
        }
    }
}

impl Downloader for NetworkDownloader {
    fn open(&self, source: &DownloadSource) -> Result<Download, String> {
        let response = self
            .agent
            .get(&source.url)
            .header(
                "User-Agent",
                concat!("actinv/", env!("CARGO_PKG_VERSION"), " data-fetch"),
            )
            .header("Accept-Encoding", "identity")
            .call()
            .map_err(|error| format!("download request failed: {error}"))?;
        let content_length = response.body().content_length();
        let (_, body) = response.into_parts();
        Ok(Download {
            reader: Box::new(body.into_reader()),
            content_length,
        })
    }
}

struct TemporaryFile {
    path: PathBuf,
}

impl TemporaryFile {
    fn create(parent: &Path, hint: &str) -> Result<(Self, File), String> {
        for _ in 0..100 {
            let nonce = TEMPORARY_NONCE.fetch_add(1, Ordering::Relaxed);
            let path = parent.join(format!(
                ".actinv-{hint}-{}-{nonce}.part",
                std::process::id()
            ));
            match OpenOptions::new().write(true).create_new(true).open(&path) {
                Ok(file) => return Ok((Self { path }, file)),
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(error) => {
                    return Err(format!(
                        "cannot create temporary file in {}: {error}",
                        parent.display()
                    ))
                }
            }
        }
        Err(format!(
            "cannot allocate a unique temporary file in {}",
            parent.display()
        ))
    }
}

impl Drop for TemporaryFile {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.path);
    }
}

fn real_directory(path: &Path, create: bool) -> Result<(), String> {
    match std::fs::symlink_metadata(path) {
        Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_dir() => Err(format!(
            "data destination component {} is not a real directory",
            path.display()
        )),
        Ok(_) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound && create => {
            std::fs::create_dir(path).map_err(|error| {
                format!("cannot create data directory {}: {error}", path.display())
            })?;
            Ok(())
        }
        Err(error) => Err(format!(
            "cannot inspect data directory {}: {error}",
            path.display()
        )),
    }
}

fn real_directory_path(path: &Path, create: bool) -> Result<(), String> {
    if path.as_os_str().is_empty() {
        return Err("data output directory must not be empty".into());
    }
    let mut current = PathBuf::new();
    let mut inspected = false;
    for component in path.components() {
        match component {
            std::path::Component::Prefix(prefix) => current.push(prefix.as_os_str()),
            std::path::Component::RootDir => current.push(component.as_os_str()),
            std::path::Component::CurDir => {
                if current.as_os_str().is_empty() {
                    current.push(".");
                }
            }
            std::path::Component::ParentDir => {
                return Err(format!(
                    "data output directory {} must not contain parent traversal",
                    path.display()
                ))
            }
            std::path::Component::Normal(value) => current.push(value),
        }
        if matches!(
            component,
            std::path::Component::CurDir | std::path::Component::Normal(_)
        ) {
            real_directory(&current, create)?;
            inspected = true;
        }
    }
    if !inspected {
        real_directory(&current, create)?;
    }
    Ok(())
}

fn prepare_root(output: &Path, version: &str) -> Result<PathBuf, String> {
    real_directory_path(output, true)?;
    let root = output.join(format!("v{version}"));
    real_directory(&root, true)?;
    Ok(root)
}

fn destination_for(
    root: &Path,
    artifact: &DataArtifact,
    create_directories: bool,
) -> Result<PathBuf, String> {
    validate_relative_path(&artifact.path, "artifact path")?;
    let mut directory = root.to_path_buf();
    let relative = Path::new(&artifact.path);
    if let Some(parent) = relative.parent() {
        for component in parent.components() {
            directory.push(component.as_os_str());
            real_directory(&directory, create_directories)?;
        }
    }
    Ok(root.join(relative))
}

fn sha256_file(path: &Path) -> Result<String, String> {
    let file = File::open(path)
        .map_err(|error| format!("cannot open {} for hashing: {error}", path.display()))?;
    let mut reader = BufReader::new(file);
    let mut hash = Sha256::new();
    let mut buffer = [0_u8; COPY_BUFFER_BYTES];
    loop {
        let count = reader
            .read(&mut buffer)
            .map_err(|error| format!("cannot hash {}: {error}", path.display()))?;
        if count == 0 {
            break;
        }
        hash.update(&buffer[..count]);
    }
    Ok(format!("{:x}", hash.finalize()))
}

enum ExistingFile {
    Missing,
    Valid,
    Invalid(String),
}

fn existing_file(path: &Path, artifact: &DataArtifact) -> Result<ExistingFile, String> {
    let metadata = match std::fs::symlink_metadata(path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(ExistingFile::Missing)
        }
        Err(error) => return Err(format!("cannot inspect {}: {error}", path.display())),
    };
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(format!(
            "data destination {} is not a real regular file",
            path.display()
        ));
    }
    if metadata.len() != artifact.bytes {
        return Ok(ExistingFile::Invalid(format!(
            "has {} bytes, expected {}",
            metadata.len(),
            artifact.bytes
        )));
    }
    let hash = sha256_file(path)?;
    if hash != artifact.sha256 {
        return Ok(ExistingFile::Invalid(format!(
            "has SHA-256 {hash}, expected {}",
            artifact.sha256
        )));
    }
    Ok(ExistingFile::Valid)
}

fn copy_verified<R: Read>(
    mut reader: R,
    mut output: File,
    expected_bytes: u64,
    expected_sha256: &str,
    label: &str,
    show_progress: bool,
) -> Result<(), String> {
    let mut hash = Sha256::new();
    let mut total = 0_u64;
    let mut next_progress = PROGRESS_INTERVAL_BYTES;
    let mut buffer = [0_u8; COPY_BUFFER_BYTES];
    loop {
        let count = reader
            .read(&mut buffer)
            .map_err(|error| format!("cannot read {label}: {error}"))?;
        if count == 0 {
            break;
        }
        total = total
            .checked_add(count as u64)
            .ok_or_else(|| format!("{label} byte count overflows"))?;
        if total > expected_bytes {
            return Err(format!(
                "{label} exceeds its declared {expected_bytes}-byte size"
            ));
        }
        output
            .write_all(&buffer[..count])
            .map_err(|error| format!("cannot write {label}: {error}"))?;
        hash.update(&buffer[..count]);
        if show_progress && total >= next_progress {
            eprintln!(
                "  {label}: {:.1}% ({total}/{expected_bytes} bytes)",
                total as f64 * 100.0 / expected_bytes as f64
            );
            next_progress = next_progress.saturating_add(PROGRESS_INTERVAL_BYTES);
        }
    }
    if total != expected_bytes {
        return Err(format!(
            "{label} ended at {total} bytes, expected {expected_bytes}"
        ));
    }
    let actual_sha256 = format!("{:x}", hash.finalize());
    if actual_sha256 != expected_sha256 {
        return Err(format!(
            "{label} has SHA-256 {actual_sha256}, expected {expected_sha256}"
        ));
    }
    output
        .flush()
        .map_err(|error| format!("cannot flush {label}: {error}"))?;
    output
        .sync_all()
        .map_err(|error| format!("cannot sync {label}: {error}"))?;
    if show_progress {
        eprintln!("  {label}: verified {expected_sha256}");
    }
    Ok(())
}

fn download_source(
    downloader: &dyn Downloader,
    source: &DownloadSource,
    parent: &Path,
    hint: &str,
    show_progress: bool,
) -> Result<TemporaryFile, String> {
    let download = downloader.open(source)?;
    if let Some(length) = download.content_length {
        if length != source.bytes {
            return Err(format!(
                "{hint} HTTP length is {length} bytes, expected {}",
                source.bytes
            ));
        }
    }
    let (temporary, file) = TemporaryFile::create(parent, hint)?;
    copy_verified(
        download.reader,
        file,
        source.bytes,
        &source.sha256,
        &format!("download {hint}"),
        show_progress,
    )?;
    Ok(temporary)
}

fn extracted_artifact(
    archive: &TemporaryFile,
    artifact: &DataArtifact,
    parent: &Path,
    show_progress: bool,
) -> Result<TemporaryFile, String> {
    let member_name = artifact
        .source
        .archive_member
        .as_deref()
        .ok_or_else(|| format!("artifact '{}' has no archive member", artifact.id))?;
    let archive_file = File::open(&archive.path)
        .map_err(|error| format!("cannot open downloaded archive: {error}"))?;
    let mut archive = zip::ZipArchive::new(archive_file)
        .map_err(|error| format!("cannot parse archive for '{}': {error}", artifact.id))?;
    if archive.len() != 1 {
        return Err(format!(
            "archive for '{}' contains {} members, expected exactly one",
            artifact.id,
            archive.len()
        ));
    }
    let member = archive
        .by_name(member_name)
        .map_err(|error| format!("archive has no declared member '{member_name}': {error}"))?;
    if !member.is_file()
        || member.encrypted()
        || member.enclosed_name().as_deref() != Some(Path::new(member_name))
        || member.size() != artifact.bytes
    {
        return Err(format!(
            "archive member '{member_name}' type, path, encryption, or size is invalid"
        ));
    }
    let (temporary, file) = TemporaryFile::create(parent, &artifact.id)?;
    copy_verified(
        member,
        file,
        artifact.bytes,
        &artifact.sha256,
        &format!("extract {}", artifact.id),
        show_progress,
    )?;
    Ok(temporary)
}

fn publish(temporary: &TemporaryFile, destination: &Path, force: bool) -> Result<(), String> {
    if !force {
        std::fs::hard_link(&temporary.path, destination).map_err(|error| {
            format!(
                "cannot publish {} without replacing an existing path: {error}",
                destination.display()
            )
        })?;
        return Ok(());
    }

    let parent = destination
        .parent()
        .ok_or_else(|| format!("{} has no parent directory", destination.display()))?;
    let (backup, backup_file) = TemporaryFile::create(parent, "backup")?;
    drop(backup_file);
    std::fs::remove_file(&backup.path)
        .map_err(|error| format!("cannot reserve replacement backup path: {error}"))?;
    std::fs::rename(destination, &backup.path).map_err(|error| {
        format!(
            "cannot preserve existing {} before replacement: {error}",
            destination.display()
        )
    })?;
    match std::fs::hard_link(&temporary.path, destination) {
        Ok(()) => {
            std::fs::remove_file(&backup.path).map_err(|error| {
                format!(
                    "replacement succeeded but old {} could not be removed: {error}",
                    destination.display()
                )
            })?;
            Ok(())
        }
        Err(error) => {
            let restore = std::fs::rename(&backup.path, destination);
            match restore {
                Ok(()) => Err(format!(
                    "cannot publish replacement {}: {error}; original restored",
                    destination.display()
                )),
                Err(restore_error) => Err(format!(
                    "cannot publish replacement {}: {error}; cannot restore original: {restore_error}",
                    destination.display()
                )),
            }
        }
    }
}

#[derive(Debug, Serialize)]
pub struct InstalledFile {
    pub id: String,
    pub role: ArtifactRole,
    pub path: String,
    pub bytes: u64,
    pub sha256: String,
    pub status: String,
}

#[derive(Debug, Serialize)]
pub struct DataOperationSummary {
    pub schema: String,
    pub operation: String,
    pub catalog_version: String,
    pub bundle: String,
    pub root: String,
    pub downloaded: usize,
    pub reused: usize,
    pub files: Vec<InstalledFile>,
    pub problem_fragment: serde_json::Value,
}

fn problem_fragment(
    catalog: &DataCatalog,
    bundle: &DataBundle,
    root: &Path,
) -> Result<serde_json::Value, String> {
    let mut roles = BTreeMap::new();
    for id in &bundle.artifacts {
        let artifact = catalog.artifact(id)?;
        roles.insert(artifact.role, artifact);
    }
    let path = |role: ArtifactRole| -> Result<String, String> {
        let artifact = roles
            .get(&role)
            .ok_or_else(|| format!("bundle '{}' has no {}", bundle.id, role.name()))?;
        Ok(root.join(&artifact.path).display().to_string())
    };
    let library = roles[&ArtifactRole::ActivationLibrary];
    let mut fragment = serde_json::json!({
        "projectile": bundle.projectile,
        "library": {
            "path": path(ArtifactRole::ActivationLibrary)?,
            "sha256": library.sha256,
        },
        "decay": {
            "primary": path(ArtifactRole::DecayPrimary)?,
            "fallback": path(ArtifactRole::DecayFallback)?,
        },
        "spectrum": {"structure": bundle.groups},
        "options": {"temperature_K": bundle.temperature_k},
    });
    if let Some(covariance) = roles.get(&ArtifactRole::CovarianceSidecar) {
        fragment["uncertainty"] = serde_json::json!({
            "covariance": {
                "path": path(ArtifactRole::CovarianceSidecar)?,
                "sha256": covariance.sha256,
            }
        });
    }
    Ok(fragment)
}

fn fetch_bundle_with(
    catalog: &DataCatalog,
    bundle_id: Option<&str>,
    output: &Path,
    force: bool,
    downloader: &dyn Downloader,
    show_progress: bool,
) -> Result<DataOperationSummary, String> {
    catalog.validate()?;
    let bundle = catalog.bundle(bundle_id)?;
    let root = prepare_root(output, &catalog.catalog_version)?;
    let mut files = Vec::with_capacity(bundle.artifacts.len());
    let mut downloaded = 0;
    let mut reused = 0;
    for id in &bundle.artifacts {
        let artifact = catalog.artifact(id)?;
        let destination = destination_for(&root, artifact, true)?;
        let status = match existing_file(&destination, artifact)? {
            ExistingFile::Valid => {
                reused += 1;
                "reused"
            }
            ExistingFile::Invalid(reason) if !force => {
                return Err(format!(
                "existing {} {reason}; rerun with --force to replace it after a verified download",
                destination.display()
            ))
            }
            existing => {
                if show_progress {
                    eprintln!("fetching {}", artifact.id);
                }
                let parent = destination.parent().ok_or_else(|| {
                    format!("data destination {} has no parent", destination.display())
                })?;
                let source = download_source(
                    downloader,
                    &artifact.source,
                    parent,
                    &artifact.id,
                    show_progress,
                )?;
                let payload = if artifact.source.archive_member.is_some() {
                    extracted_artifact(&source, artifact, parent, show_progress)?
                } else {
                    source
                };
                let replacing = matches!(existing, ExistingFile::Invalid(_));
                publish(&payload, &destination, replacing)?;
                if !matches!(existing_file(&destination, artifact)?, ExistingFile::Valid) {
                    return Err(format!(
                        "published data file {} failed post-publication verification",
                        destination.display()
                    ));
                }
                downloaded += 1;
                if replacing {
                    "replaced"
                } else {
                    "downloaded"
                }
            }
        };
        files.push(InstalledFile {
            id: artifact.id.clone(),
            role: artifact.role,
            path: destination.display().to_string(),
            bytes: artifact.bytes,
            sha256: artifact.sha256.clone(),
            status: status.into(),
        });
    }
    Ok(DataOperationSummary {
        schema: "actinv-data-operation-1".into(),
        operation: "fetch".into(),
        catalog_version: catalog.catalog_version.clone(),
        bundle: bundle.id.clone(),
        root: root.display().to_string(),
        downloaded,
        reused,
        files,
        problem_fragment: problem_fragment(catalog, bundle, &root)?,
    })
}

pub fn fetch_bundle(
    bundle_id: Option<&str>,
    output: impl AsRef<Path>,
    force: bool,
) -> Result<DataOperationSummary, String> {
    let catalog = embedded_catalog()?;
    fetch_bundle_with(
        &catalog,
        bundle_id,
        output.as_ref(),
        force,
        &NetworkDownloader::new(),
        true,
    )
}

pub fn verify_bundle(
    bundle_id: Option<&str>,
    output: impl AsRef<Path>,
) -> Result<DataOperationSummary, String> {
    let catalog = embedded_catalog()?;
    let bundle = catalog.bundle(bundle_id)?;
    real_directory_path(output.as_ref(), false)?;
    let root = output
        .as_ref()
        .join(format!("v{}", catalog.catalog_version));
    real_directory(&root, false)?;
    let mut files = Vec::with_capacity(bundle.artifacts.len());
    for id in &bundle.artifacts {
        let artifact = catalog.artifact(id)?;
        let destination = destination_for(&root, artifact, false)?;
        match existing_file(&destination, artifact)? {
            ExistingFile::Valid => files.push(InstalledFile {
                id: artifact.id.clone(),
                role: artifact.role,
                path: destination.display().to_string(),
                bytes: artifact.bytes,
                sha256: artifact.sha256.clone(),
                status: "verified".into(),
            }),
            ExistingFile::Missing => {
                return Err(format!("data file {} is missing", destination.display()))
            }
            ExistingFile::Invalid(reason) => {
                return Err(format!("data file {} {reason}", destination.display()))
            }
        }
    }
    Ok(DataOperationSummary {
        schema: "actinv-data-operation-1".into(),
        operation: "verify".into(),
        catalog_version: catalog.catalog_version.clone(),
        bundle: bundle.id.clone(),
        root: root.display().to_string(),
        downloaded: 0,
        reused: files.len(),
        files,
        problem_fragment: problem_fragment(&catalog, bundle, &root)?,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    struct FakeDownloader {
        payloads: BTreeMap<String, Vec<u8>>,
    }

    impl Downloader for FakeDownloader {
        fn open(&self, source: &DownloadSource) -> Result<Download, String> {
            let bytes = self
                .payloads
                .get(&source.url)
                .ok_or_else(|| "planted download failure".to_string())?
                .clone();
            Ok(Download {
                content_length: Some(bytes.len() as u64),
                reader: Box::new(Cursor::new(bytes)),
            })
        }
    }

    struct TestDirectory(PathBuf);

    impl TestDirectory {
        fn new(label: &str) -> Self {
            let nonce = TEMPORARY_NONCE.fetch_add(1, Ordering::Relaxed);
            let path = std::env::temp_dir().join(format!(
                "actinv-data-test-{label}-{}-{nonce}",
                std::process::id()
            ));
            std::fs::create_dir(&path).unwrap();
            Self(path)
        }
    }

    impl Drop for TestDirectory {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn hash_bytes(bytes: &[u8]) -> String {
        format!("{:x}", Sha256::digest(bytes))
    }

    fn direct_artifact(id: &str, role: ArtifactRole, path: &str, bytes: &[u8]) -> DataArtifact {
        let sha256 = hash_bytes(bytes);
        DataArtifact {
            id: id.into(),
            role,
            path: path.into(),
            bytes: bytes.len() as u64,
            sha256: sha256.clone(),
            licence: "test-only".into(),
            source: DownloadSource {
                url: format!("https://fixtures.invalid/{id}"),
                bytes: bytes.len() as u64,
                sha256,
                archive_member: None,
            },
        }
    }

    fn fixture_catalog() -> (DataCatalog, FakeDownloader) {
        let fixtures = [
            (
                "library",
                ArtifactRole::ActivationLibrary,
                "activation/test.npz",
                b"library".as_slice(),
            ),
            (
                "library-index",
                ArtifactRole::ActivationIndex,
                "activation/test_index.json",
                b"index".as_slice(),
            ),
            (
                "primary",
                ArtifactRole::DecayPrimary,
                "decay/primary.dat",
                b"primary".as_slice(),
            ),
            (
                "fallback",
                ArtifactRole::DecayFallback,
                "decay/fallback.dat",
                b"fallback".as_slice(),
            ),
            (
                "notice",
                ArtifactRole::Notice,
                "NOTICE.md",
                b"notice".as_slice(),
            ),
        ];
        let artifacts: Vec<_> = fixtures
            .iter()
            .map(|(id, role, path, bytes)| direct_artifact(id, *role, path, bytes))
            .collect();
        let payloads = artifacts
            .iter()
            .zip(fixtures)
            .map(|(artifact, (_, _, _, bytes))| (artifact.source.url.clone(), bytes.to_vec()))
            .collect();
        let catalog = DataCatalog {
            schema: "actinv-data-catalog-1".into(),
            catalog_version: "1.0.0".into(),
            default_bundle: "test-neutron".into(),
            release_url: "https://fixtures.invalid/release".into(),
            notice: "NOTICE.md".into(),
            artifacts,
            bundles: vec![DataBundle {
                id: "test-neutron".into(),
                description: "Test neutron data".into(),
                projectile: "neutron".into(),
                groups: "fispact-709".into(),
                temperature_k: 293.6,
                artifacts: vec![
                    "library".into(),
                    "library-index".into(),
                    "primary".into(),
                    "fallback".into(),
                    "notice".into(),
                ],
            }],
        };
        (catalog, FakeDownloader { payloads })
    }

    #[test]
    fn embedded_catalog_is_strict_and_matches_release_evidence() {
        let catalog = embedded_catalog().unwrap();
        assert_eq!(catalog.default_bundle, "tendl-2025-neutron");
        assert_eq!(catalog.bundles.len(), 5);
        assert_eq!(
            catalog.artifact("tendl-2025-neutron-709g").unwrap().sha256,
            "ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44"
        );
        assert_eq!(
            catalog
                .artifact("tendl-2025-neutron-709g-covariance")
                .unwrap()
                .sha256,
            "c19dec86b44ad5d90b66c9ab94d53e18641a1d354a89402a4da7986b6c530cde"
        );

        let mut value: serde_json::Value = serde_json::from_str(CATALOG_JSON).unwrap();
        value["unexpected"] = serde_json::json!(true);
        assert!(DataCatalog::parse(&serde_json::to_string(&value).unwrap()).is_err());

        let mut value: serde_json::Value = serde_json::from_str(CATALOG_JSON).unwrap();
        value["artifacts"][0]["path"] = serde_json::json!("../escape.npz");
        assert!(DataCatalog::parse(&serde_json::to_string(&value).unwrap()).is_err());

        let mut value: serde_json::Value = serde_json::from_str(CATALOG_JSON).unwrap();
        value["artifacts"][0]["source"]["url"] = serde_json::json!("http://example.invalid/data");
        assert!(DataCatalog::parse(&serde_json::to_string(&value).unwrap()).is_err());
    }

    #[test]
    fn fetch_is_atomic_reuses_valid_files_and_requires_force_for_repair() {
        let (catalog, downloader) = fixture_catalog();
        let output = TestDirectory::new("atomic");
        let first =
            fetch_bundle_with(&catalog, None, &output.0, false, &downloader, false).unwrap();
        assert_eq!(first.downloaded, 5);
        assert_eq!(first.reused, 0);

        let second =
            fetch_bundle_with(&catalog, None, &output.0, false, &downloader, false).unwrap();
        assert_eq!(second.downloaded, 0);
        assert_eq!(second.reused, 5);

        let library = output.0.join("v1.0.0/activation/test.npz");
        std::fs::write(&library, b"damaged").unwrap();
        let before = std::fs::read(&library).unwrap();
        assert!(fetch_bundle_with(&catalog, None, &output.0, false, &downloader, false,).is_err());
        assert_eq!(std::fs::read(&library).unwrap(), before);

        let repaired =
            fetch_bundle_with(&catalog, None, &output.0, true, &downloader, false).unwrap();
        assert_eq!(repaired.downloaded, 1);
        assert_eq!(std::fs::read(&library).unwrap(), b"library");
    }

    #[cfg(unix)]
    #[test]
    fn fetch_rejects_a_symlinked_destination_component() {
        use std::os::unix::fs::symlink;

        let (catalog, downloader) = fixture_catalog();
        let container = TestDirectory::new("symlink");
        let actual = container.0.join("actual");
        std::fs::create_dir(&actual).unwrap();
        let linked = container.0.join("linked");
        symlink(&actual, &linked).unwrap();

        assert!(fetch_bundle_with(&catalog, None, &linked, false, &downloader, false).is_err());
        assert!(!actual.join("v1.0.0").exists());
    }

    #[cfg(unix)]
    #[test]
    fn verification_rejects_a_symlinked_artifact_directory() {
        use std::os::unix::fs::symlink;

        let (catalog, _) = fixture_catalog();
        let container = TestDirectory::new("verify-symlink");
        let root = container.0.join("v1.0.0");
        let actual = container.0.join("actual-activation");
        std::fs::create_dir(&root).unwrap();
        std::fs::create_dir(&actual).unwrap();
        symlink(&actual, root.join("activation")).unwrap();

        assert!(destination_for(&root, catalog.artifact("library").unwrap(), false).is_err());
    }

    #[test]
    fn truncated_excess_bad_hash_and_failed_download_publish_nothing() {
        for (label, payload) in [
            ("truncated", b"librar".to_vec()),
            ("excess", b"library!".to_vec()),
            ("bad-hash", b"LIBRARY".to_vec()),
        ] {
            let (catalog, mut downloader) = fixture_catalog();
            let source = catalog.artifact("library").unwrap().source.url.clone();
            downloader.payloads.insert(source, payload);
            let output = TestDirectory::new(label);
            assert!(
                fetch_bundle_with(&catalog, None, &output.0, false, &downloader, false,).is_err()
            );
            assert!(!output.0.join("v1.0.0/activation/test.npz").exists());
        }

        let (catalog, mut downloader) = fixture_catalog();
        let source = catalog.artifact("library").unwrap().source.url.clone();
        downloader.payloads.remove(&source);
        let output = TestDirectory::new("failure");
        assert!(fetch_bundle_with(&catalog, None, &output.0, false, &downloader, false,).is_err());
        assert!(!output.0.join("v1.0.0/activation/test.npz").exists());
    }

    fn zip_bytes(member_name: &str, payload: &[u8], extra: bool) -> Vec<u8> {
        let mut cursor = Cursor::new(Vec::new());
        {
            let mut writer = zip::ZipWriter::new(&mut cursor);
            let options = zip::write::SimpleFileOptions::default();
            writer.start_file(member_name, options).unwrap();
            writer.write_all(payload).unwrap();
            if extra {
                writer.start_file("unexpected.dat", options).unwrap();
                writer.write_all(b"extra").unwrap();
            }
            writer.finish().unwrap();
        }
        cursor.into_inner()
    }

    #[test]
    fn archive_and_extracted_identities_are_both_required() {
        let (mut catalog, mut downloader) = fixture_catalog();
        let archive = zip_bytes("primary.dat", b"primary", false);
        let source_url = {
            let primary = catalog
                .artifacts
                .iter_mut()
                .find(|artifact| artifact.id == "primary")
                .unwrap();
            primary.source.bytes = archive.len() as u64;
            primary.source.sha256 = hash_bytes(&archive);
            primary.source.archive_member = Some("primary.dat".into());
            primary.source.url.clone()
        };
        downloader
            .payloads
            .insert(source_url.clone(), archive.clone());
        catalog.validate().unwrap();

        let output = TestDirectory::new("archive");
        fetch_bundle_with(&catalog, None, &output.0, false, &downloader, false).unwrap();
        assert_eq!(
            std::fs::read(output.0.join("v1.0.0/decay/primary.dat")).unwrap(),
            b"primary"
        );

        let bad_archive = zip_bytes("primary.dat", b"primary", true);
        {
            let primary = catalog
                .artifacts
                .iter_mut()
                .find(|artifact| artifact.id == "primary")
                .unwrap();
            primary.source.bytes = bad_archive.len() as u64;
            primary.source.sha256 = hash_bytes(&bad_archive);
        }
        downloader.payloads.insert(source_url, bad_archive);
        let second_output = TestDirectory::new("archive-extra");
        assert!(
            fetch_bundle_with(&catalog, None, &second_output.0, false, &downloader, false,)
                .is_err()
        );
        assert!(!second_output.0.join("v1.0.0/decay/primary.dat").exists());
    }
}
