//! Exercise the production process adapter with separately built system apps.
//! No current_exe(), shell, recursive test executable, or descendant process tree.
#![cfg(unix)]
#[path = "../../../crates/actinv-gui/src/worker_wait.rs"]
mod worker_wait;
use std::{
    process::{Child, Command, Stdio},
    time::{Duration, Instant},
};
use worker_wait::CancellationCheck;

struct ReapOnDrop(Child);
impl Drop for ReapOnDrop {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

#[test]
fn cancelling_an_actual_child_terminates_and_reaps_it() {
    let mut child = ReapOnDrop(
        Command::new("sleep")
            .arg("30")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap(),
    );
    let started = Instant::now();
    let (result, cancelled) = worker_wait::wait_for_exit(
        &mut child.0,
        |phase| worker_wait::cancellation_requested(Ok(()), phase),
        || unreachable!(),
    );
    assert!(cancelled);
    assert!(!result.unwrap().success());
    assert!(started.elapsed() < Duration::from_secs(5));
    assert!(child.0.try_wait().unwrap().is_some());
}

#[test]
fn actual_normal_exit_is_observed_without_cancellation() {
    let mut child = ReapOnDrop(
        Command::new("true")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap(),
    );
    let deadline = Instant::now() + Duration::from_secs(5);
    let (result, cancelled) = worker_wait::wait_for_exit(
        &mut child.0,
        |phase| matches!(phase, CancellationCheck::WhileRunning) && Instant::now() >= deadline,
        || std::thread::sleep(Duration::from_millis(1)),
    );
    assert!(!cancelled, "child exceeded bounded completion deadline");
    assert!(result.unwrap().success());
    assert!(child.0.try_wait().unwrap().is_some());
}
