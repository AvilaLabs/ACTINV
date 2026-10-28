use std::process::Command;

#[test]
fn default_validation_does_not_require_downloaded_data() {
    let dir = std::env::temp_dir().join(format!("actinv-validation-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let spec = dir.join("problem.json");
    let example = actinv_cli::workflow::new_example(&dir.join("missing-data")).unwrap();
    // The example names the catalog damage table without any displacement energies.
    let damage = serde_json::to_value(&example).unwrap()["damage"].clone();
    assert!(damage["table"]["path"]
        .as_str()
        .is_some_and(|path| path.ends_with("/damage/tendl-2025-neutron-damage-709g.json")));
    assert!(damage["displacement_energy_eV"]
        .as_object()
        .is_none_or(|energies| energies.is_empty()));
    std::fs::write(&spec, serde_json::to_vec(&example).unwrap()).unwrap();
    for flag in [None, Some("--schema")] {
        let mut command = Command::new(env!("CARGO_BIN_EXE_actinv"));
        command.arg("validate").arg(&spec);
        if let Some(flag) = flag {
            command.arg(flag);
        }
        let output = command.output().unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
    }
    for flag in ["--files", "--hashes"] {
        let output = Command::new(env!("CARGO_BIN_EXE_actinv"))
            .arg("validate")
            .arg(&spec)
            .arg(flag)
            .output()
            .unwrap();
        assert!(!output.status.success());
        assert!(String::from_utf8_lossy(&output.stderr).contains("cannot open"));
    }
    std::fs::remove_file(spec).unwrap();
    std::fs::remove_dir(dir).unwrap();
}
