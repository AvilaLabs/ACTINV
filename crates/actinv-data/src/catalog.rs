//! Data-distribution catalog: the embedded, strictly validated manifest of the installable data
//! bundles. Pure parse/validate (no file or network access), so the browser build shares it.
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;

const CATALOG_JSON: &str = include_str!("../data/actinv-data-catalog-v1.1.0.json");
const MAX_ARTIFACT_BYTES: u64 = 2_000_000_000;

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

/// A catalog path must be a short, portable, relative path with no traversal.
pub fn validate_relative_path(value: &str, label: &str) -> Result<(), String> {
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
                "proton" | "deuteron" | "triton" | "helion" | "alpha" => "fispact-162",
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
            if crate::builder::index_path(&library.path)? != Path::new(&index.path) {
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
                    if crate::covariance::index_path(&sidecar.path)? != Path::new(&index.path) {
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn embedded_catalog_is_strict_and_matches_release_evidence() {
        let catalog = embedded_catalog().unwrap();
        assert_eq!(catalog.default_bundle, "tendl-2025-neutron");
        assert_eq!(catalog.bundles.len(), 7);
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
    fn relative_paths_reject_traversal_and_platform_forms() {
        assert!(validate_relative_path("activation/a.npz", "path").is_ok());
        for bad in ["", "/abs", "a/../b", "a//b", "a/", "a\\b", "c:x", "a/./b"] {
            assert!(validate_relative_path(bad, "path").is_err(), "{bad}");
        }
    }
}
