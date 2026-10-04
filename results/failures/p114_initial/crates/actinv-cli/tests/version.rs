use std::process::Command;

#[test]
fn reports_package_version() {
    let output = Command::new(env!("CARGO_BIN_EXE_actinv"))
        .arg("--version")
        .output()
        .expect("run actinv --version");

    assert!(output.status.success());
    assert_eq!(
        String::from_utf8_lossy(&output.stdout),
        format!("actinv {}\n", env!("CARGO_PKG_VERSION"))
    );
    assert!(output.stderr.is_empty());
}

#[test]
fn reports_help_without_error() {
    let output = Command::new(env!("CARGO_BIN_EXE_actinv"))
        .arg("--help")
        .output()
        .expect("run actinv --help");

    assert!(output.status.success());
    assert!(String::from_utf8_lossy(&output.stdout).starts_with("usage: actinv run"));
    assert!(output.stderr.is_empty());
}
