//! Shared user workflow helpers. The scientific specification and solver stay authoritative.
use crate::DataCatalog;
use actinv_core::spec::{LibraryRef, Spec};
use std::path::{Path, PathBuf};

pub const IRON_EXAMPLE: &str = include_str!("../data/iron-example.json");

/// Installed-data root for `catalog:` references in a problem whose inputs resolve against
/// `base`: `$ACTINV_DATA_DIR`, else the `actinv-data` folder beside the problem.
pub fn catalog_data_root(base: &Path) -> PathBuf {
    match std::env::var_os("ACTINV_DATA_DIR") {
        Some(value) if !value.is_empty() => {
            let root = PathBuf::from(value);
            match std::env::current_dir() {
                Ok(cwd) if root.is_relative() => cwd.join(root),
                _ => root,
            }
        }
        _ => base.join("actinv-data"),
    }
}

/// Where a `catalog:<artifact-id>` reference points under the installed-data root, with the
/// catalog's SHA-256 for it. `None` for any other path and for an id the catalog does not list.
fn catalog_target(path: &str, catalog: &DataCatalog, data_root: &Path) -> Option<(String, String)> {
    let artifact = catalog.artifact(path.strip_prefix("catalog:")?).ok()?;
    let target = data_root
        .join(format!("v{}", catalog.catalog_version))
        .join(&artifact.path);
    Some((
        target.to_string_lossy().into_owned(),
        artifact.sha256.clone(),
    ))
}

/// Make every input path absolute: relative paths join `base`, `catalog:<id>` references point
/// into the installed data (see [`catalog_data_root`]) and a library reference without a SHA-256
/// takes the catalog's, as `resolve_catalog_json` does for problem text.
pub fn resolve_inputs(spec: &mut Spec, base: &Path) {
    resolve_inputs_under(spec, base, &catalog_data_root(base));
}

fn resolve_inputs_under(spec: &mut Spec, base: &Path, data_root: &Path) {
    let catalog = crate::embedded_catalog().ok();
    let target = |path: &str| {
        catalog
            .as_ref()
            .and_then(|catalog| catalog_target(path, catalog, data_root))
    };
    let resolve = |path: &mut String| {
        if let Some((resolved, _)) = target(path.as_str()) {
            *path = resolved;
        } else if !path.is_empty() && !path.starts_with("catalog:") && Path::new(path).is_relative()
        {
            *path = base.join(&*path).to_string_lossy().into_owned();
        }
    };
    let resolve_library = |library: &mut LibraryRef| {
        let from_catalog = target(&library.path);
        resolve(&mut library.path);
        if library.sha256.is_none() {
            library.sha256 = from_catalog.map(|(_, sha256)| sha256);
        }
    };
    resolve_library(&mut spec.library);
    resolve(&mut spec.decay.primary);
    if let Some(path) = &mut spec.decay.fallback {
        resolve(path);
    }
    if let Some(path) = &mut spec.decay.overrides {
        resolve(path);
    }
    for step in &mut spec.schedule {
        if let Some(library) = &mut step.library {
            resolve_library(library);
        }
    }
    if let Some(shielding) = &mut spec.self_shielding {
        if let Some(table) = &mut shielding.table {
            resolve(&mut table.path);
        }
        if let Some(geometry) = &mut shielding.geometry {
            for file in &mut geometry.resonance_files {
                resolve(&mut file.path);
            }
        }
    }
    if let Some(damage) = &mut spec.damage {
        resolve(&mut damage.table.path);
    }
    if let Some(r) = &mut spec.photon.response {
        resolve(&mut r.path);
    }
    if let Some(r) = &mut spec.uncertainty {
        if let Some(covariance) = &mut r.covariance {
            resolve(&mut covariance.path);
        }
    }
    if let Some(r) = &mut spec.radiological {
        // `bundled:NAME` is a table inside the binary, not a path under `base`.
        if actinv_core::radiological::bundled_name(&r.table.path).is_none() {
            resolve(&mut r.table.path);
        }
    }
    if let Some(r) = &mut spec.kerma {
        resolve(&mut r.table.path);
    }
    for r in &mut spec.fission_yields.files {
        resolve(&mut r.path);
    }
    if let Some(spontaneous) = &mut spec.fission_yields.spontaneous {
        for r in &mut spontaneous.files {
            resolve(&mut r.path);
        }
        if let Some(r) = &mut spontaneous.nubar_endf {
            resolve(&mut r.path);
        }
        if let Some(r) = &mut spontaneous.nubar_table {
            resolve(&mut r.path);
        }
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
    if let Some(p) = &spec.decay.overrides {
        inputs.push(("Decay overrides", p, None));
    }
    for step in &spec.schedule {
        if let Some(library) = &step.library {
            inputs.push((
                "Schedule step library",
                &library.path,
                library.sha256.as_deref(),
            ));
        }
    }
    if let Some(shielding) = &spec.self_shielding {
        if let Some(table) = &shielding.table {
            inputs.push(("Self-shielding table", &table.path, Some(&table.sha256)));
        }
        if let Some(geometry) = &shielding.geometry {
            for file in &geometry.resonance_files {
                inputs.push(("Resonance file", &file.path, Some(&file.sha256)));
            }
        }
    }
    if let Some(damage) = &spec.damage {
        inputs.push((
            "Damage table",
            &damage.table.path,
            Some(&damage.table.sha256),
        ));
    }
    if let Some(r) = &spec.photon.response {
        inputs.push(("Photon response", &r.path, Some(&r.sha256)));
    }
    if let Some(covariance) = spec
        .uncertainty
        .as_ref()
        .and_then(|r| r.covariance.as_ref())
    {
        inputs.push(("Covariance", &covariance.path, Some(&covariance.sha256)));
    }
    if let Some(r) = &spec.radiological {
        inputs.push(("Radiological table", &r.table.path, Some(&r.table.sha256)));
    }
    if let Some(r) = &spec.kerma {
        inputs.push(("Kerma table", &r.table.path, Some(&r.table.sha256)));
    }
    for r in &spec.fission_yields.files {
        inputs.push(("Fission yields", &r.path, Some(&r.sha256)));
    }
    if let Some(spontaneous) = &spec.fission_yields.spontaneous {
        for r in &spontaneous.files {
            inputs.push(("Spontaneous-fission yields", &r.path, Some(&r.sha256)));
        }
        if let Some(r) = &spontaneous.nubar_endf {
            inputs.push((
                "Spontaneous-fission nu-bar (ENDF)",
                &r.path,
                Some(&r.sha256),
            ));
        }
        if let Some(r) = &spontaneous.nubar_table {
            inputs.push(("Spontaneous-fission nu-bar table", &r.path, Some(&r.sha256)));
        }
    }
    let mut errors = Vec::new();
    for (label, path, hash) in inputs {
        if path.is_empty() {
            continue;
        }
        if path.starts_with("catalog:") {
            errors.push(format!(
                "{label}: unresolved catalog reference {path}; it names no artifact in the data catalog"
            ));
            continue;
        }
        if let Some(name) = actinv_core::radiological::bundled_name(path) {
            let declared = if hashes { hash } else { None };
            if let Err(e) = actinv_core::radiological::read_bundled(name, declared) {
                errors.push(format!("{label}: {e}"));
            }
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
    for path in std::iter::once(spec.library.path.as_str())
        .chain(
            spec.uncertainty
                .as_ref()
                .and_then(|u| u.covariance.as_ref())
                .map(|c| c.path.as_str()),
        )
        .chain(
            spec.schedule
                .iter()
                .filter_map(|step| step.library.as_ref())
                .map(|library| library.path.as_str()),
        )
    {
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

/// The data path of the example spec without its `actinv-data/v<version>/` prefix, whatever
/// catalog version the template was written against.
fn example_relative_path(path: &str) -> &str {
    let rest = path.strip_prefix("actinv-data/").unwrap_or(path);
    match rest.split_once('/') {
        Some((version, tail))
            if version.starts_with('v')
                && version[1..]
                    .bytes()
                    .all(|b| b.is_ascii_digit() || b == b'.') =>
        {
            tail
        }
        _ => rest,
    }
}

/// The catalog's shipped neutron damage table, which the example names so a dpa run needs no
/// table built first.
const EXAMPLE_DAMAGE_TABLE: &str = "tendl-2025-neutron-damage";

/// Generate a complete editable example, with references valid from any working directory.
/// The default data root emits portable `catalog:` references resolved against the
/// installed data bundle at run time; an explicit `--data-dir` keeps absolute paths.
/// The example also names the catalog damage table, so its run reports dpa without setup.
pub fn new_example(data_root: &Path) -> Result<Spec, String> {
    let mut spec = Spec::from_json(IRON_EXAMPLE)?;
    let catalog = crate::embedded_catalog()?;
    let table = catalog.artifact(EXAMPLE_DAMAGE_TABLE)?;
    let damage_ref = |path: String| -> Result<_, String> {
        serde_json::from_value(serde_json::json!({
            "table": {"path": path, "sha256": table.sha256},
        }))
        .map_err(|e| format!("cannot build the example damage section: {e}"))
    };
    if data_root == Path::new("actinv-data") {
        let to_catalog = |path: &mut String| -> Result<(), String> {
            let relative = example_relative_path(path);
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
        spec.damage = Some(damage_ref(format!("catalog:{EXAMPLE_DAMAGE_TABLE}"))?);
        return Ok(spec);
    }
    let root: PathBuf = if data_root.is_absolute() {
        data_root.into()
    } else {
        std::env::current_dir()
            .map_err(|e| e.to_string())?
            .join(data_root)
    }
    .join(format!("v{}", catalog.catalog_version));
    let rewrite = |path: &mut String| {
        *path = root
            .join(example_relative_path(path))
            .to_string_lossy()
            .into_owned();
    };
    rewrite(&mut spec.library.path);
    rewrite(&mut spec.decay.primary);
    if let Some(p) = &mut spec.decay.fallback {
        rewrite(p);
    }
    spec.damage = Some(damage_ref(
        root.join(&table.path).to_string_lossy().into_owned(),
    )?);
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
        assert_eq!(portable.library.path, "catalog:tendl-2025-neutron-709g");
        assert_eq!(portable.decay.primary, "catalog:endfb-viii-0-decay");
        assert_eq!(
            portable.decay.fallback.as_deref(),
            Some("catalog:jeff-3-3-decay")
        );
        let damage = portable.damage.as_ref().unwrap();
        assert_eq!(damage.table.path, "catalog:tendl-2025-neutron-damage");
        assert!(damage.displacement_energy_eV.is_empty());
        let explicit = s.damage.as_ref().unwrap();
        assert!(explicit.table.path.starts_with("/example-data/v"));
        assert!(explicit
            .table
            .path
            .ends_with("/damage/tendl-2025-neutron-damage-709g.json"));
        assert_eq!(explicit.table.sha256, damage.table.sha256);
    }
    #[test]
    fn missing_files_include_sidecars_and_recovery() {
        let s = new_example(Path::new("/nonexistent-actinv-test-data")).unwrap();
        let e = check_files(&s, false).unwrap_err();
        assert!(e.contains("_index.json"));
        assert!(e.contains("actinv data fetch"));
    }

    #[test]
    fn catalog_references_resolve_under_the_data_root_with_the_catalog_hash() {
        let catalog = crate::embedded_catalog().unwrap();
        let mut spec = new_example(Path::new("actinv-data")).unwrap();
        spec.library.sha256 = None;
        resolve_inputs_under(&mut spec, Path::new("/project"), Path::new("/data"));
        let library = catalog.artifact("tendl-2025-neutron-709g").unwrap();
        assert_eq!(
            spec.library.path,
            format!("/data/v{}/{}", catalog.catalog_version, library.path)
        );
        assert_eq!(
            spec.library.sha256.as_deref(),
            Some(library.sha256.as_str())
        );
        let decay = catalog.artifact("endfb-viii-0-decay").unwrap();
        assert_eq!(
            spec.decay.primary,
            format!("/data/v{}/{}", catalog.catalog_version, decay.path)
        );
        let fallback = spec.decay.fallback.as_deref().unwrap();
        assert!(fallback.starts_with("/data/v"), "{fallback}");
        // a declared hash is kept, and a plain relative path still joins the base
        let mut spec = new_example(Path::new("actinv-data")).unwrap();
        spec.library.sha256 = Some("ab".repeat(32));
        spec.decay.overrides = Some("overrides.json".into());
        resolve_inputs_under(&mut spec, Path::new("/project"), Path::new("/data"));
        assert_eq!(spec.library.sha256, Some("ab".repeat(32)));
        assert_eq!(
            spec.decay.overrides.as_deref(),
            Some("/project/overrides.json")
        );
    }

    #[test]
    fn unknown_catalog_references_stay_put_and_check_files_names_them() {
        let mut spec = new_example(Path::new("actinv-data")).unwrap();
        spec.library.path = "catalog:no-such-artifact".into();
        spec.library.sha256 = None;
        resolve_inputs_under(&mut spec, Path::new("/project"), Path::new("/data"));
        assert_eq!(spec.library.path, "catalog:no-such-artifact");
        assert!(spec.library.sha256.is_none());
        let error = check_files(&spec, false).unwrap_err();
        assert!(
            error.contains("unresolved catalog reference catalog:no-such-artifact"),
            "{error}"
        );
    }

    #[test]
    fn catalog_data_root_is_the_environment_override_or_beside_the_problem() {
        let root = catalog_data_root(Path::new("/project"));
        match std::env::var_os("ACTINV_DATA_DIR") {
            Some(value) if !value.is_empty() => assert!(root.is_absolute()),
            _ => assert_eq!(root, Path::new("/project/actinv-data")),
        }
    }

    // ---- P140 regressions (SG-09, power/fission F8): they assert the intended behaviour ----

    /// The example spec with every newer path-bearing field populated under `prefix`.
    fn p140_spec(prefix: &str) -> Spec {
        let hash = "0".repeat(64);
        let file =
            |name: &str| serde_json::json!({"path": format!("{prefix}/{name}"), "sha256": hash});
        let mut spec = Spec::from_json(IRON_EXAMPLE).unwrap();
        spec.schedule[1].library = Some(actinv_core::spec::LibraryRef {
            path: format!("{prefix}/step-lib.npz"),
            sha256: None,
        });
        spec.self_shielding = Some(
            serde_json::from_value(serde_json::json!({
                "table": file("shield-table.json"),
                "geometry": {
                    "shape": "foil",
                    "length_cm": 0.01,
                    "density_g_cm3": 19.3,
                    "resonance_files": [file("w186.endf")],
                },
            }))
            .unwrap(),
        );
        spec.damage = Some(
            serde_json::from_value(serde_json::json!({"table": file("damage-table.json")}))
                .unwrap(),
        );
        spec.fission_yields.spontaneous = Some(
            serde_json::from_value(serde_json::json!({
                "files": [file("sfy.endf")],
                "nubar_endf": file("nubar-decay.endf"),
                "nubar_table": file("nubar-table.json"),
            }))
            .unwrap(),
        );
        spec
    }

    #[test]
    fn p140_sg09_resolve_inputs_rebases_every_path_bearing_field() {
        let mut spec = p140_spec("rel");
        resolve_inputs(&mut spec, Path::new("/project"));
        let shielding = spec.self_shielding.as_ref().unwrap();
        let spontaneous = spec.fission_yields.spontaneous.as_ref().unwrap();
        let fields: Vec<(&str, &str)> = vec![
            (
                "schedule[1].library.path",
                spec.schedule[1].library.as_ref().unwrap().path.as_str(),
            ),
            (
                "self_shielding.table",
                shielding.table.as_ref().unwrap().path.as_str(),
            ),
            (
                "self_shielding.geometry.resonance_files[0]",
                shielding.geometry.as_ref().unwrap().resonance_files[0]
                    .path
                    .as_str(),
            ),
            (
                "damage.table",
                spec.damage.as_ref().unwrap().table.path.as_str(),
            ),
            (
                "fission_yields.spontaneous.files[0]",
                spontaneous.files[0].path.as_str(),
            ),
            (
                "fission_yields.spontaneous.nubar_endf",
                spontaneous.nubar_endf.as_ref().unwrap().path.as_str(),
            ),
            (
                "fission_yields.spontaneous.nubar_table",
                spontaneous.nubar_table.as_ref().unwrap().path.as_str(),
            ),
        ];
        let unresolved: Vec<&str> = fields
            .iter()
            .filter(|(_, path)| !path.starts_with("/project/"))
            .map(|(label, _)| *label)
            .collect();
        assert!(unresolved.is_empty(), "paths left relative: {unresolved:?}");
    }

    #[test]
    fn p140_sg09_check_files_opens_every_path_bearing_field() {
        let spec = p140_spec("/nonexistent-actinv-p140");
        let error = check_files(&spec, false).unwrap_err();
        let unchecked: Vec<&str> = [
            "step-lib.npz",
            "shield-table.json",
            "w186.endf",
            "damage-table.json",
            "sfy.endf",
            "nubar-decay.endf",
            "nubar-table.json",
        ]
        .into_iter()
        .filter(|name| !error.contains(name))
        .collect();
        assert!(
            unchecked.is_empty(),
            "files never opened: {unchecked:?}\n{error}"
        );
    }
}
