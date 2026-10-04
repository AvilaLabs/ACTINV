use actinv_core::spec::Spec;
use serde_json::Value;
use std::{
    io::{Read, Write},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::mpsc::{self, Receiver, Sender},
    thread,
    time::Duration,
};

// stdout is a complete JSON result; stderr is diagnostic text and is kept
// deliberately smaller so a failed child cannot consume unbounded memory.
const MAX_STDOUT_BYTES: usize = 256 * 1024 * 1024;
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

struct ChildGuard(Child);

impl Drop for ChildGuard {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

struct CacheGuard(PathBuf);

impl Drop for CacheGuard {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

pub fn spawn(spec: Spec, cache: PathBuf) -> Result<Handle, String> {
    // A Rust unit-test executable does not run our application main. Launching
    // current_exe from it would recursively run the entire test suite.
    if cfg!(test) {
        return Err("Calculation spawning is disabled in the unit-test harness; use a separately built application for integration tests".into());
    }
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let (tx, rx) = mpsc::channel();
    let (cancel_tx, cancel_rx) = mpsc::channel();
    let input = serde_json::to_vec(&spec).map_err(|e| e.to_string())?;
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

            let mut cancelled = false;
            let status = loop {
                match cancel_rx.try_recv() {
                    Ok(_) | Err(mpsc::TryRecvError::Disconnected) => {
                        cancelled = true;
                        let _ = child.0.kill();
                        break child.0.wait();
                    }
                    Err(mpsc::TryRecvError::Empty) => {}
                }
                match child.0.try_wait() {
                    Ok(Some(status)) => break Ok(status),
                    Ok(None) => thread::sleep(Duration::from_millis(10)),
                    Err(error) => {
                        let _ = child.0.kill();
                        let _ = child.0.wait();
                        break Err(error);
                    }
                }
            };
            // Cancellation wins a race with process completion.
            if !cancelled && cancel_rx.try_recv().is_ok() {
                cancelled = true;
            }
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
