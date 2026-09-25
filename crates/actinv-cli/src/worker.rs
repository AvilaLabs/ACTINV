//! `actinv worker` — P51 persistent-worker mode (see
//! protocols/ACTINV-P51_PROTOCOL.md). Reads one
//! `actinv-worker-request-1` JSON object per line on stdin, serves requests
//! sequentially against a single-slot `PreparedCache`, and writes one
//! `actinv-worker-response-1` object per line on stdout. The `result`
//! document is the same `RunResult` serialization `actinv run` emits;
//! wall-clock fields live only in the side-channel `timing_ms` ledger.
//! Cancellation is by process termination; EOF or `op:"stop"` exits cleanly.

use actinv_core::run::{run_with_cache, PreparedCache};
use actinv_core::spec::Spec;
use serde_json::{json, Value};
use std::io::{BufRead, Write};
use std::time::Instant;

const REQUEST_SCHEMA: &str = "actinv-worker-request-1";
const RESPONSE_SCHEMA: &str = "actinv-worker-response-1";
const ALLOWED_KEYS: &[&str] = &["schema", "id", "op", "spec"];

fn error_response(id: Value, error: impl Into<String>) -> Value {
    json!({"schema": RESPONSE_SCHEMA, "id": id, "ok": false, "error": error.into()})
}

fn handle(request: Value, cache: &mut PreparedCache, queued_at: Instant) -> Value {
    let id = request.get("id").cloned().unwrap_or(Value::Null);
    match request.get("schema").and_then(Value::as_str) {
        Some(REQUEST_SCHEMA) => {}
        _ => return error_response(id, "request schema must be 'actinv-worker-request-1'"),
    }
    if !id.is_u64() && !id.is_i64() {
        return error_response(id, "request id must be an integer");
    }
    if let Some(bad) = request
        .as_object()
        .and_then(|map| map.keys().find(|key| !ALLOWED_KEYS.contains(&key.as_str())))
    {
        return error_response(id, format!("unknown request key '{bad}'"));
    }
    let op = match request.get("op").and_then(Value::as_str) {
        Some(op) => op,
        None => return error_response(id, "request op must be a string"),
    };
    match op {
        "run" => {
            let Some(spec_value) = request.get("spec") else {
                return error_response(id, "run request has no spec");
            };
            let spec_text = match serde_json::to_string(spec_value) {
                Ok(text) => text,
                Err(error) => return error_response(id, format!("cannot encode spec: {error}")),
            };
            let resolved = match crate::resolve_catalog_json(&spec_text) {
                Ok(text) => text,
                Err(error) => return error_response(id, error),
            };
            let spec = match Spec::from_json(&resolved) {
                Ok(spec) => spec,
                Err(error) => return error_response(id, error),
            };
            let queue_ms = queued_at.elapsed().as_secs_f64() * 1e3;
            let solve_started = Instant::now();
            match run_with_cache(&spec, "worker", cache) {
                Ok(result) => json!({
                    "schema": RESPONSE_SCHEMA,
                    "id": id,
                    "ok": true,
                    "result": serde_json::to_value(&result)
                        .expect("run result serializes to a JSON value"),
                    "timing_ms": {
                        "queue_ms": queue_ms,
                        "solve_ms": solve_started.elapsed().as_secs_f64() * 1e3,
                        "fingerprint_ms": cache.last_fingerprint_ms(),
                        "warm": cache.last_hit(),
                    }
                }),
                Err(error) => {
                    let mut response = error_response(id, error);
                    response["timing_ms"] = json!({
                        "queue_ms": queue_ms,
                        "solve_ms": solve_started.elapsed().as_secs_f64() * 1e3,
                        "fingerprint_ms": cache.last_fingerprint_ms(),
                        "warm": cache.last_hit(),
                    });
                    response
                }
            }
        }
        "stop" => json!({"schema": RESPONSE_SCHEMA, "id": id, "ok": true, "stopped": true}),
        other => error_response(id, format!("unknown op '{other}'")),
    }
}

/// Serve requests until EOF or `stop`; returns the process exit code.
pub fn serve() -> i32 {
    let stdin = std::io::stdin();
    let stdout = std::io::stdout();
    let mut out = stdout.lock();
    let mut cache = PreparedCache::new();
    let mut lines = stdin.lock().lines();
    loop {
        let line = match lines.next() {
            Some(Ok(line)) => line,
            _ => return 0, // EOF or a dead stdin: clean exit
        };
        if line.trim().is_empty() {
            continue;
        }
        let queued_at = Instant::now();
        let request: Value = match serde_json::from_str(&line) {
            Ok(request) => request,
            Err(error) => {
                let response = error_response(Value::Null, format!("invalid JSON: {error}"));
                if writeln!(out, "{response}").is_err() || out.flush().is_err() {
                    return 0;
                }
                continue;
            }
        };
        let is_stop = request.get("op").and_then(Value::as_str) == Some("stop")
            && request.get("schema").and_then(Value::as_str) == Some(REQUEST_SCHEMA);
        if !request.is_object() {
            let response = error_response(Value::Null, "request must be a JSON object");
            if writeln!(out, "{response}").is_err() || out.flush().is_err() {
                return 0;
            }
            continue;
        }
        let response = handle(request, &mut cache, queued_at);
        if writeln!(out, "{response}").is_err() || out.flush().is_err() {
            return 0;
        }
        if is_stop {
            return 0;
        }
    }
}
