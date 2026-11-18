use crate::jobs::Job;
use actinv_core::spec::Spec;
use serde_json::Value;
use std::{
    io::Read,
    path::PathBuf,
    sync::mpsc::{self, Receiver, Sender},
    thread,
};
#[cfg(not(test))]
use std::{
    io::Write,
    process::{Child, Command, Stdio},
    time::Duration,
};

// stdout is a complete JSON result; stderr is diagnostic text and is kept
// deliberately smaller so a failed child cannot consume unbounded memory.
#[cfg(not(test))]
const MAX_STDOUT_BYTES: usize = 256 * 1024 * 1024;
#[cfg(not(test))]
const MAX_STDERR_BYTES: usize = 1024 * 1024;

pub struct Handle {
    pub rx: Receiver<Result<Value, String>>,
    cancel: Sender<()>,
    supervisor: Option<thread::JoinHandle<()>>,
}

impl Handle {
    pub fn request_cancel(&self) {
        let _ = self.cancel.send(());
    }
}

impl Drop for Handle {
    fn drop(&mut self) {
        self.request_cancel();
        if let Some(supervisor) = self.supervisor.take() {
            let _ = supervisor.join();
        }
    }
}

fn finish(
    status: std::process::ExitStatus,
    stdout: &[u8],
    stderr: &[u8],
    cancelled: bool,
) -> Result<Value, String> {
    if cancelled {
        return Err("calculation cancelled".into());
    }
    if status.success() {
        serde_json::from_slice(stdout).map_err(|e| format!("worker returned invalid JSON: {e}"))
    } else if stderr.is_empty() {
        Err(format!("worker exited with {status}"))
    } else {
        Err(String::from_utf8_lossy(stderr).into_owned())
    }
}

fn read_bounded(input: impl Read, max_bytes: usize, stream: &str) -> Result<Vec<u8>, String> {
    let mut bytes = Vec::new();
    input
        .take((max_bytes + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() > max_bytes {
        return Err(format!("worker {stream} exceeded {max_bytes} bytes"));
    }
    Ok(bytes)
}

#[cfg(not(test))]
struct ChildGuard(Child);

#[cfg(not(test))]
impl Drop for ChildGuard {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

#[cfg(not(test))]
struct CacheGuard(PathBuf);

#[cfg(not(test))]
impl Drop for CacheGuard {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

pub fn spawn(spec: Spec, cache: PathBuf) -> Result<Handle, String> {
    spawn_input(serde_json::to_vec(&spec).map_err(|e| e.to_string())?, cache)
}

/// Run an impurities screen with its embedded base specification.
pub fn spawn_impurities(document: &Value, cache: PathBuf) -> Result<Handle, String> {
    if document["schema"] != IMPURITIES_SCHEMA || !document["base_spec"].is_object() {
        return Err("an impurities screen needs an actinv-impurities-1 document with its base spec embedded".into());
    }
    spawn_input(
        serde_json::to_vec(document).map_err(|e| e.to_string())?,
        cache,
    )
}

/// Run a job, retaining its output directory and cleaning only the private cache.
pub fn spawn_job(job: &Job, cache: PathBuf) -> Result<Handle, String> {
    job.check()?;
    spawn_input(
        serde_json::to_vec(&job.to_value()).map_err(|e| e.to_string())?,
        cache,
    )
}

pub const IMPURITIES_SCHEMA: &str = "actinv-impurities-1";

// Keep application launching out of every unit-test entry point entirely.
#[cfg(test)]
fn spawn_input(_input: Vec<u8>, _cache: PathBuf) -> Result<Handle, String> {
    Err("Calculation spawning is disabled in the unit-test harness; use a separately built application for integration tests".into())
}

#[cfg(not(test))]
fn spawn_input(input: Vec<u8>, cache: PathBuf) -> Result<Handle, String> {
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let (tx, rx) = mpsc::channel();
    let (cancel_tx, cancel_rx) = mpsc::channel();
    let supervisor = thread::Builder::new()
        .name("actinv-solver-worker".into())
        .spawn(move || {
            // Cleanup is permitted only after this exclusive create succeeds.
            if let Err(error) = std::fs::create_dir(&cache) {
                let _ = tx.send(Err(format!("could not create private cache: {error}")));
                return;
            }
            // Own the directory even if process creation fails. Declaring this
            // before ChildGuard ensures the child is reaped before deletion.
            let _cache_guard = CacheGuard(cache.clone());
            let child = match Command::new(exe)
                .env("ACTINV_GUI_CALCULATION_WORKER", "1")
                .env("ACTINV_CACHE_DIR", &cache)
                .env_remove("ACTINV_GUI_CAPTURE_DIR")
                .env_remove("ACTINV_GUI_CAPTURE_RESULT")
                .env_remove("ACTINV_GUI_CAPTURE_THEME")
                .env_remove("ACTINV_GUI_SMOKE_SPEC")
                .env_remove("ACTINV_GUI_SMOKE_OUT")
                .env_remove("ACTINV_GUI_SMOKE_MODEL_ONLY")
                .stdin(Stdio::piped())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .spawn()
            {
                Ok(c) => c,
                Err(e) => {
                    let _ = tx.send(Err(e.to_string()));
                    return;
                }
            };
            let mut child = ChildGuard(child);
            let mut stdin = child.0.stdin.take().expect("worker stdin");
            let writer = thread::spawn(move || stdin.write_all(&input).map_err(|e| e.to_string()));
            let stdout = child.0.stdout.take().expect("worker stdout");
            let stderr = child.0.stderr.take().expect("worker stderr");
            let out_thread = thread::spawn(|| read_bounded(stdout, MAX_STDOUT_BYTES, "stdout"));
            let err_thread = thread::spawn(|| read_bounded(stderr, MAX_STDERR_BYTES, "stderr"));

            let (status, cancelled) = crate::worker_wait::wait_for_exit(
                &mut child.0,
                |check| crate::worker_wait::cancellation_requested(cancel_rx.try_recv(), check),
                || thread::sleep(Duration::from_millis(10)),
            );
            let write_result = writer
                .join()
                .unwrap_or_else(|_| Err("worker stdin thread panicked".into()));
            let stdout = out_thread
                .join()
                .unwrap_or_else(|_| Err("worker stdout thread panicked".into()));
            let stderr = err_thread
                .join()
                .unwrap_or_else(|_| Err("worker stderr thread panicked".into()));
            let result = if cancelled {
                Err("calculation cancelled".into())
            } else if let Err(error) = write_result {
                Err(error)
            } else {
                match (stdout, stderr) {
                    (Ok(stdout), Ok(stderr)) => status
                        .map(|s| finish(s, &stdout, &stderr, false))
                        .unwrap_or_else(|e| Err(e.to_string())),
                    (stdout, stderr) => Err(stdout
                        .err()
                        .or_else(|| stderr.err())
                        .unwrap_or_else(|| "worker output error".into())),
                }
            };
            let _ = tx.send(result);
        })
        .map_err(|e| e.to_string())?;
    Ok(Handle {
        rx,
        cancel: cancel_tx,
        supervisor: Some(supervisor),
    })
}

/// Run an impurities screen on a thread with the solver's stack. Verification is on: every
/// reported composition is re-solved, as `actinv impurities` does without `--no-verify`.
pub fn screen_in_solver_thread(document: String) -> Result<Value, String> {
    let (tx, rx) = mpsc::sync_channel(1);
    thread::Builder::new()
        .stack_size(crate::model::SOLVER_STACK_BYTES)
        .spawn(move || {
            let dir = std::env::current_dir().unwrap_or_default();
            let _ = tx.send(actinv_cli::impurities::run_impurities_doc(
                &document, &dir, true,
            ));
        })
        .map_err(|e| e.to_string())?
        .join()
        .map_err(|_| "impurities thread panicked".to_owned())?;
    rx.recv().map_err(|e| e.to_string())?
}

pub fn run_in_solver_thread(spec: Spec) -> Result<Value, String> {
    let (tx, rx) = mpsc::sync_channel(1);
    thread::Builder::new()
        .stack_size(crate::model::SOLVER_STACK_BYTES)
        .spawn(move || {
            let _ = tx.send(crate::model::solve(spec));
        })
        .map_err(|e| e.to_string())?
        .join()
        .map_err(|_| "solver worker panicked".to_owned())?;
    rx.recv().map_err(|e| e.to_string())?
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn spawning_is_disabled_in_the_unit_test_executable() {
        let spec = Spec::from_json(include_str!("../../../examples/fns_fe_5min.json")).unwrap();
        let Err(error) = spawn(spec, PathBuf::from("unused-test-cache")) else {
            panic!("unit tests must never launch their own executable as a worker");
        };
        assert!(error.contains("disabled in the unit-test harness"));
    }

    fn status(code: u32) -> std::process::ExitStatus {
        #[cfg(unix)]
        {
            use std::os::unix::process::ExitStatusExt;
            std::process::ExitStatus::from_raw((code as i32) << 8)
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::ExitStatusExt;
            std::process::ExitStatus::from_raw(code)
        }
    }
    #[test]
    fn cancelled_result_has_priority_over_success() {
        assert!(finish(status(0), b"{}", b"", true).is_err());
    }
    #[test]
    fn failed_exit_preserves_bounded_error_text() {
        let e = vec![b'x'; 131072];
        assert_eq!(
            finish(status(1), b"{}", &e, false).unwrap_err().len(),
            e.len()
        );
    }
    #[test]
    fn bounded_reader_reports_overflow_without_spawning() {
        assert!(read_bounded(std::io::Cursor::new(vec![0; 17]), 16, "test").is_err());
    }
}
