use std::process::{Command, Output};

fn actinv(args: &[&str], cwd: &std::path::Path) -> Output {
    Command::new(env!("CARGO_BIN_EXE_actinv"))
        .args(args)
        .current_dir(cwd)
        .output()
        .expect("run actinv")
}

/// A fresh empty directory for one test, so nothing it writes can be mistaken for another's.
fn scratch(name: &str) -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!("actinv-help-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

fn text(bytes: &[u8]) -> String {
    String::from_utf8_lossy(bytes).into_owned()
}

#[test]
fn bare_actinv_is_a_usage_error_that_shows_the_landing() {
    let dir = scratch("bare");
    let output = actinv(&[], &dir);
    assert_eq!(output.status.code(), Some(2));
    assert!(output.stdout.is_empty());
    let stderr = text(&output.stderr);
    assert!(stderr.starts_with("ACTINV — activation and inventory"));
    assert!(stderr.contains("Start here"));
}

#[test]
fn help_is_a_command_and_writes_to_stdout() {
    let dir = scratch("help");
    let output = actinv(&["help"], &dir);
    assert!(output.status.success());
    assert!(text(&output.stdout).contains("Ask a design question"));
    assert!(output.stderr.is_empty());

    let output = actinv(&["help", "run"], &dir);
    assert!(output.status.success());
    assert!(text(&output.stdout).starts_with("actinv run: solve a problem"));

    let output = actinv(&["help", "exit-codes"], &dir);
    assert!(output.status.success());
    assert!(text(&output.stdout).contains("3  a check ran and failed"));
}

#[test]
fn help_anywhere_shows_the_page_and_never_writes_a_file() {
    let dir = scratch("anywhere");
    let output = actinv(&["run", "problem.json", "--help"], &dir);
    assert!(output.status.success());
    assert!(text(&output.stdout).starts_with("actinv run: solve a problem"));
    assert!(!dir.join("--help").exists());

    let output = actinv(&["data", "fetch", "--help"], &dir);
    assert!(output.status.success());
    assert!(text(&output.stdout).starts_with("actinv data: install and check nuclear data"));
    assert!(!dir.join("actinv-data").exists());
}

#[test]
fn a_mistyped_command_gets_the_nearest_name() {
    let dir = scratch("typo");
    let output = actinv(&["rnu", "problem.json"], &dir);
    assert_eq!(output.status.code(), Some(2));
    assert_eq!(
        text(&output.stderr),
        "actinv: unknown command 'rnu'. Did you mean 'run'? See 'actinv help'.\n"
    );

    let output = actinv(&["help", "frobnicate"], &dir);
    assert_eq!(output.status.code(), Some(2));
    assert_eq!(
        text(&output.stderr),
        "actinv: unknown command 'frobnicate'. See 'actinv help'.\n"
    );
}

#[test]
fn a_usage_error_names_the_command_and_its_page() {
    let dir = scratch("usage");
    let output = actinv(&["run"], &dir);
    assert_eq!(output.status.code(), Some(2));
    assert_eq!(
        text(&output.stderr),
        "usage: actinv run PROBLEM.json [OUT.json]\nSee 'actinv help run'.\n"
    );

    let output = actinv(&["run", "problem.json", "--hashes"], &dir);
    assert_eq!(output.status.code(), Some(2));
    assert!(text(&output.stderr).starts_with("unknown run option --hashes\n"));
    assert!(!dir.join("--hashes").exists());
}

#[test]
fn new_says_what_it_wrote_and_what_to_do_next() {
    let dir = scratch("new");
    let output = Command::new(env!("CARGO_BIN_EXE_actinv"))
        .args(["new", "problem.json"])
        .current_dir(&dir)
        .env_remove("ACTINV_DATA_DIR")
        .output()
        .expect("run actinv new");
    assert!(output.status.success());
    assert!(output.stdout.is_empty());
    let stderr = text(&output.stderr);
    assert!(stderr.starts_with(
        "Wrote problem.json: iron, 5 minutes at the FNS 14 MeV neutron source, then 20 cooling steps.\n"
    ));
    assert!(stderr.contains("Data: not installed in "));
    assert!(stderr.contains("Next: actinv data fetch, then actinv run problem.json result.json"));
}

#[test]
fn doctor_without_data_says_to_fetch_it() {
    let dir = scratch("doctor");
    let output = Command::new(env!("CARGO_BIN_EXE_actinv"))
        .arg("doctor")
        .current_dir(&dir)
        .env_remove("ACTINV_DATA_DIR")
        .output()
        .expect("run actinv doctor");
    assert_eq!(output.status.code(), Some(1));
    let stdout = text(&output.stdout);
    assert!(stdout.contains("Data directory: "));
    assert!(stdout.contains("Data catalog: 1.2.0 (standard bundle tendl-2025-neutron)"));
    assert!(text(&output.stderr).contains("Next: actinv data fetch"));
}
