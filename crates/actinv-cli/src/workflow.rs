//! Shared user workflow helpers. The scientific specification and solver stay authoritative.
use actinv_core::spec::Spec;
use std::path::{Path, PathBuf};

pub const IRON_EXAMPLE: &str = include_str!("../data/iron-example.json");

pub fn resolve_inputs(spec: &mut Spec, base: &Path) {
    let resolve = |path: &mut String| {
        if !path.is_empty() && Path::new(path).is_relative() {
            *path = base.join(&*path).to_string_lossy().into_owned();
        }
    };
    resolve(&mut spec.library.path);
    resolve(&mut spec.decay.primary);
    if let Some(path) = &mut spec.decay.fallback {
        resolve(path);
    }
    if let Some(r) = &mut spec.photon.response {
        resolve(&mut r.path);
    }
    if let Some(r) = &mut spec.uncertainty {
        resolve(&mut r.covariance.path);
    }
    if let Some(r) = &mut spec.radiological {
        resolve(&mut r.table.path);
    }
    for r in &mut spec.fission_yields.files {
        resolve(&mut r.path);
    }
}

pub fn check_files(spec: &Spec, hashes: bool) -> Result<(), String> {
    let mut inputs: Vec<(&str, &str, Option<&str>)> = vec![
        (
            "Activation library",
            &spec.library.path,
            spec.library.sha256.as_deref(),
        ),
        ("Primary decay data", &spec.decay.primary, None),
    ];
    if let Some(p) = &spec.decay.fallback {
        inputs.push(("Fallback decay data", p, None));
    }
    if let Some(r) = &spec.photon.response {
        inputs.push(("Photon response", &r.path, Some(&r.sha256)));
    }
    if let Some(r) = &spec.uncertainty {
        inputs.push(("Covariance", &r.covariance.path, Some(&r.covariance.sha256)));
    }
    if let Some(r) = &spec.radiological {
        inputs.push(("Radiological table", &r.table.path, Some(&r.table.sha256)));
    }
    for r in &spec.fission_yields.files {
        inputs.push(("Fission yields", &r.path, Some(&r.sha256)));
    }
    let mut errors = Vec::new();
    for (label, path, hash) in inputs {
        if path.is_empty() {
            continue;
        }
        if let Err(e) = std::fs::File::open(path) {
            errors.push(format!("{label}: cannot open {path}: {e}"));
        } else if hashes {
            if let Some(expected) = hash {
                match actinv_core::flux::sha256_file(path) {
                    Ok(actual) if actual == expected => {}
                    Ok(actual) => errors.push(format!(
                        "{label}: SHA-256 mismatch for {path}: expected {expected}, found {actual}"
                    )),
                    Err(e) => errors.push(e),
                }
            }
        }
    }
    for path in std::iter::once(spec.library.path.as_str()).chain(
        spec.uncertainty
            .as_ref()
            .map(|u| u.covariance.path.as_str()),
    ) {
        if path.is_empty() {
            continue;
        }
        let path = Path::new(path);
        let index = path.with_file_name(format!(
            "{}_index.json",
            path.file_stem().unwrap_or_default().to_string_lossy()
        ));
        if let Err(e) = std::fs::File::open(&index) {
            errors.push(format!(
                "Library index: cannot open {}: {e}",
                index.display()
            ));
        }
    }
    if errors.is_empty() {
        Ok(())
    } else {
        Err(format!("{}\nChoose installed data in Overview & data, or use `actinv data fetch` and check the input base folder.", errors.join("\n")))
    }
}

/// Generate a complete editable example, with references valid from any working directory.
/// The default data root emits portable `catalog:` references resolved against the
/// installed data bundle at run time; an explicit `--data-dir` keeps absolute paths.
pub fn new_example(data_root: &Path) -> Result<Spec, String> {
    let mut spec = Spec::from_json(IRON_EXAMPLE)?;
    if data_root == Path::new("actinv-data") {
        let catalog = crate::embedded_catalog()?;
        let to_catalog = |path: &mut String| -> Result<(), String> {
            let relative = path
                .strip_prefix(&format!("actinv-data/v{}/", catalog.catalog_version))
                .unwrap_or(path);
            let artifact = catalog
                .artifacts
                .iter()
                .find(|a| a.path == relative)
                .ok_or_else(|| format!("example data path '{path}' is not in the catalog"))?;
            *path = format!("catalog:{}", artifact.id);
            Ok(())
        };
        to_catalog(&mut spec.library.path)?;
        to_catalog(&mut spec.decay.primary)?;
        if let Some(p) = &mut spec.decay.fallback {
            to_catalog(p)?;
        }
        return Ok(spec);
    }
    let root: PathBuf = if data_root.is_absolute() {
        data_root.into()
    } else {
        std::env::current_dir()
            .map_err(|e| e.to_string())?
            .join(data_root)
    };
    let rewrite = |path: &mut String| {
        *path = root
            .join(path.strip_prefix("actinv-data/").unwrap_or(path))
            .to_string_lossy()
            .into_owned();
    };
    rewrite(&mut spec.library.path);
    rewrite(&mut spec.decay.primary);
    if let Some(p) = &mut spec.decay.fallback {
        rewrite(p);
    }
    Ok(spec)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn example_is_complete_and_relocatable() {
        let s = new_example(Path::new("/example-data")).unwrap();
        assert_eq!(s.spectrum.flux_per_group.len(), 709);
        assert!(s.library.path.starts_with("/example-data/"));
        assert_eq!(s.schedule.len(), 21);
        let portable = new_example(Path::new("actinv-data")).unwrap();
        assert_eq!(
            portable.library.path,
            "catalog:tendl-2025-neutron-709g"
        );
        assert_eq!(portable.decay.primary, "catalog:endfb-viii-0-decay");
        assert_eq!(
            portable.decay.fallback.as_deref(),
            Some("catalog:jeff-3-3-decay")
        );
    }
    #[test]
    fn missing_files_include_sidecars_and_recovery() {
        let s = new_example(Path::new("/nonexistent-actinv-test-data")).unwrap();
        let e = check_files(&s, false).unwrap_err();
        assert!(e.contains("_index.json"));
        assert!(e.contains("actinv data fetch"));
    }
}
