use std::process::Command;

fn actinv() -> Command {
    Command::new(env!("CARGO_BIN_EXE_actinv"))
}

#[test]
fn lists_embedded_data_bundles() {
    let output = actinv()
        .args(["data", "list"])
        .output()
        .expect("run actinv data list");
    assert!(output.status.success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("ACTINV data catalog v1.0.0"));
    assert!(stdout.contains("tendl-2025-neutron [default]"));
    assert!(stdout.contains("tendl-2025-neutron-covariance"));
    assert!(output.stderr.is_empty());
}

#[test]
fn prints_the_strict_embedded_manifest() {
    let output = actinv()
        .args(["data", "manifest"])
        .output()
        .expect("run actinv data manifest");
    assert!(output.status.success());
    let manifest: serde_json::Value =
        serde_json::from_slice(&output.stdout).expect("manifest is JSON");
    assert_eq!(manifest["schema"], "actinv-data-catalog-1");
    assert_eq!(manifest["catalog_version"], "1.0.0");
    assert_eq!(manifest["default_bundle"], "tendl-2025-neutron");
    assert!(output.stderr.is_empty());
}

#[test]
fn rejects_bad_data_arguments_without_network_access() {
    for args in [
        &["data", "fetch", "--unknown"][..],
        &["data", "fetch", "one", "two"][..],
        &["data", "verify", "--force"][..],
        &["data", "missing"][..],
    ] {
        let output = actinv().args(args).output().expect("run bad data command");
        assert!(!output.status.success(), "unexpected success for {args:?}");
        assert!(output.stdout.is_empty());
        assert!(!output.stderr.is_empty());
        assert!(output.stderr.len() < 4096);
    }
}
