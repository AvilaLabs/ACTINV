//! Interactive parameter exploration through the identical qualified solver
//! path: each sweep point generates a full problem spec, solves it through the
//! same isolated worker protocol as a manual run, and binds the rendered
//! result to the parameters that produced it. There is no second numerics
//! path.
use serde_json::Value;
use std::sync::mpsc;

/// Declared sweep axes only — arbitrary JSON edits are not a sweep axis.
#[derive(Clone, Debug, PartialEq)]
pub enum SweepAxis {
    /// wt% of one element; the rest of the composition is renormalized to
    /// keep the wt_percent basis consistent.
    CompositionFraction { element: String },
    /// Multiplier on spectrum.total (flux magnitude, shape preserved).
    FluxNormalization,
    /// Duration of one cooling step, seconds.
    CoolingTimeS { step_index: usize },
}

impl SweepAxis {
    pub fn label(&self) -> String {
        match self {
            Self::CompositionFraction { element } => format!("{element} wt%"),
            Self::FluxNormalization => "flux normalization ×".into(),
            Self::CoolingTimeS { step_index } => {
                format!("cooling step {} duration s", step_index + 1)
            }
        }
    }
}

#[derive(Clone)]
pub struct SweepPoint {
    /// axis value
    pub param: f64,
    pub label: String,
    /// canonical generated spec JSON — the exact worker input
    pub spec_json: String,
    /// sha256(spec_json) — every rendered result is bound to this
    pub spec_sha256: String,
}

fn sha256_hex(data: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    let mut h = Sha256::new();
    h.update(data);
    hex(&h.finalize())
}

fn hex(bytes: &[u8]) -> String {
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push_str(&format!("{b:02x}"));
    }
    s
}

/// Generate one spec per axis value from the editable document. The base
/// document must already decode; each point re-materializes defaults so the
/// emitted spec JSON is exactly what `actinv run` would consume.
pub fn sweep_specs(
    base: &Value,
    axis: &SweepAxis,
    points: &[f64],
) -> Result<Vec<SweepPoint>, String> {
    if points.is_empty() {
        return Err("sweep needs at least one point".into());
    }
    if points.len() > MAX_SWEEP_POINTS {
        return Err(format!(
            "sweep limited to {MAX_SWEEP_POINTS} points per interaction"
        ));
    }
    let mut out = Vec::with_capacity(points.len());
    for &v in points.iter() {
        if !v.is_finite() {
            return Err("sweep point is not finite".into());
        }
        let mut doc = base.clone();
        let label = match axis {
            SweepAxis::CompositionFraction { element } => {
                let comp = doc["material"]["composition"]
                    .as_object_mut()
                    .ok_or("material.composition is not a map")?;
                let rest: f64 = comp
                    .iter()
                    .filter(|(k, _)| !k.eq_ignore_ascii_case(element))
                    .map(|(_, x)| x.as_f64().unwrap_or(0.0))
                    .sum();
                if rest <= 0.0 && v < 100.0 {
                    return Err("cannot renormalize: no other constituents".into());
                }
                let scale = (100.0 - v).max(0.0) / rest;
                for (k, x) in comp.iter_mut() {
                    if k.eq_ignore_ascii_case(element) {
                        *x = Value::from(v);
                    } else {
                        *x = Value::from(x.as_f64().unwrap_or(0.0) * scale);
                    }
                }
                format!("{element}={v} wt%")
            }
            SweepAxis::FluxNormalization => {
                let cur = doc["spectrum"]["total"].as_f64().unwrap_or(0.0);
                if cur <= 0.0 {
                    return Err("spectrum.total is not positive; flux sweep ".into());
                }
                doc["spectrum"]["total"] = Value::from(cur * v);
                format!("flux ×{v}")
            }
            SweepAxis::CoolingTimeS { step_index } => {
                let steps = doc["schedule"]
                    .as_array_mut()
                    .ok_or("schedule is not a list")?;
                let step = steps
                    .get_mut(*step_index)
                    .ok_or("cooling step index out of range")?;
                if v <= 0.0 {
                    return Err("cooling duration must be positive".into());
                }
                step["dt"] = Value::from(format!("{v} s"));
                format!("step {} = {v} s", step_index + 1)
            }
        };
        // Materialize solver defaults and reject invalid docs the same way
        // the desktop's Apply path does — the emitted spec_json is the
        // canonical worker input.
        let spec_json = {
            let spec = actinv_core::spec::Spec::from_json(
                &serde_json::to_string(&doc).map_err(|e| e.to_string())?,
            )?;
            serde_json::to_string(&spec).map_err(|e| e.to_string())?
        };
        out.push(SweepPoint {
            param: v,
            label,
            spec_sha256: sha256_hex(spec_json.as_bytes()),
            spec_json,
        });
    }
    Ok(out)
}

/// Per-interaction compute bound: hard cap on points per sweep.
pub const MAX_SWEEP_POINTS: usize = 32;

/// A completed point: the result is carried with the parameter and spec
/// digest that produced it — never detached.
pub struct CompletedPoint {
    pub generation: u64,
    pub param: f64,
    pub label: String,
    pub spec_sha256: String,
    pub result: Result<Value, String>,
    pub elapsed_ms: u64,
}

/// Cancellable sweep run. Dropping the handle requests cancellation and
/// joins the supervisor — a superseded sweep can never leave a worker
/// running or a cache behind.
pub struct SweepHandle {
    pub rx: mpsc::Receiver<CompletedPoint>,
    cancel: mpsc::Sender<()>,
    supervisor: Option<std::thread::JoinHandle<()>>,
}

impl SweepHandle {
    pub fn request_cancel(&self) {
        let _ = self.cancel.send(());
    }
}

impl Drop for SweepHandle {
    fn drop(&mut self) {
        self.request_cancel();
        if let Some(s) = self.supervisor.take() {
            let _ = s.join();
        }
    }
}

/// Sequentially execute a sweep through `worker::spawn` — the same isolated
/// worker protocol a manual Run uses. Each point gets its own private cache.
/// Cancellation stops the current worker and abandons the queue; dropping the
/// run handle requests cancellation.
pub fn spawn_sweep(
    points: Vec<SweepPoint>,
    generation: u64,
    cache_root: std::path::PathBuf,
) -> Result<SweepHandle, String> {
    let (tx, rx) = mpsc::channel();
    let (cancel_tx, cancel_rx) = mpsc::channel::<()>();
    let supervisor = std::thread::Builder::new()
        .name("actinv-sweep-worker".into())
        .spawn(move || {
            // The sweep root is owned by this run: created up front and
            // removed when the run ends — a cancelled or superseded sweep
            // leaves no worker caches behind.
            struct RootGuard(std::path::PathBuf);
            impl Drop for RootGuard {
                fn drop(&mut self) {
                    let _ = std::fs::remove_dir_all(&self.0);
                }
            }
            if let Err(e) = std::fs::create_dir_all(&cache_root) {
                let _ = tx.send(CompletedPoint {
                    generation,
                    param: f64::NAN,
                    label: "sweep launch".into(),
                    spec_sha256: String::new(),
                    result: Err(format!("could not create sweep cache root: {e}")),
                    elapsed_ms: 0,
                });
                return;
            }
            let _root_guard = RootGuard(cache_root.clone());
            for (idx, point) in points.into_iter().enumerate() {
                if cancel_rx.try_recv().is_ok() {
                    return;
                }
                let cache = cache_root.join(format!("pt{idx}"));
                let spec = match actinv_core::spec::Spec::from_json(&point.spec_json) {
                    Ok(s) => s,
                    Err(e) => {
                        let _ = tx.send(CompletedPoint {
                            generation,
                            param: point.param,
                            label: point.label,
                            spec_sha256: point.spec_sha256,
                            result: Err(e),
                            elapsed_ms: 0,
                        });
                        continue;
                    }
                };
                let t0 = std::time::Instant::now();
                let handle = match crate::worker::spawn(spec, cache) {
                    Ok(h) => h,
                    Err(e) => {
                        let _ = tx.send(CompletedPoint {
                            generation,
                            param: point.param,
                            label: point.label,
                            spec_sha256: point.spec_sha256,
                            result: Err(e),
                            elapsed_ms: 0,
                        });
                        continue;
                    }
                };
                // Poll so a sweep cancel is forwarded to the in-flight
                // worker even mid-solve — the race between the worker
                // finishing and a cancel arriving is won by cancellation.
                let mut cancelled = false;
                let result = loop {
                    if cancel_rx.try_recv().is_ok() && !cancelled {
                        cancelled = true;
                        handle.request_cancel();
                    }
                    match handle.rx.try_recv() {
                        Ok(r) => break r,
                        Err(mpsc::TryRecvError::Empty) => {
                            std::thread::sleep(std::time::Duration::from_millis(10));
                        }
                        Err(mpsc::TryRecvError::Disconnected) => {
                            break Err("sweep worker channel closed".into());
                        }
                    }
                };
                let elapsed_ms = t0.elapsed().as_millis() as u64;
                cancelled = cancelled
                    || matches!(
                        result,
                        Err(ref e) if e == "calculation cancelled"
                    );
                let send = tx.send(CompletedPoint {
                    generation,
                    param: point.param,
                    label: point.label,
                    spec_sha256: point.spec_sha256,
                    result,
                    elapsed_ms,
                });
                if send.is_err() || cancelled {
                    return; // superseded or cancelled: stop dequeuing
                }
            }
        })
        .map_err(|e| e.to_string())?;
    Ok(SweepHandle {
        rx,
        cancel: cancel_tx,
        supervisor: Some(supervisor),
    })
}

/// Response extraction for the plotted quantity — named, not implicit.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SweepResponse {
    TotalActivityBqPerG,
    TotalHeatWPerG,
}

impl SweepResponse {
    pub fn extract(&self, result: &Value, step: usize) -> Option<f64> {
        let s = result["steps"].as_array()?.get(step)?;
        match self {
            Self::TotalActivityBqPerG => {
                let m = s["activity_Bq_per_g"].as_object()?;
                Some(m.values().filter_map(|v| v.as_f64()).sum())
            }
            Self::TotalHeatWPerG => s["heat_W_per_g"]["total"].as_f64(),
        }
    }
    pub fn label(&self) -> &'static str {
        match self {
            Self::TotalActivityBqPerG => "total activity Bq/g",
            Self::TotalHeatWPerG => "total heat W/g",
        }
    }
}

/// The binding check: a rendered point is admissible only when it belongs to
/// the sweep generation the UI is showing. This is what prevents a cancelled
/// or superseded sweep from rendering a stale result under new parameters.
pub fn admissible(current: u64, point: &CompletedPoint) -> bool {
    point.generation == current
}

#[cfg(test)]
mod tests {
    use super::*;
    fn doc() -> Value {
        let mut flux = vec![0.0; 709];
        flux[350] = 1.0e10;
        serde_json::json!({
            "spec": "actinv-spec-1", "title": "t",
            "projectile": "neutron",
            "library": {"path": "lib.npz"},
            "decay": {"primary": "d.dat"},
            "material": {"mass_g": 1.0, "basis": "wt_percent",
                "composition": {"FE": 90.0, "CR": 10.0}},
            "spectrum": {"structure": "fispact-709",
                "flux_per_group": flux, "total": 3.0e12},
            "schedule": [{"dt": "300.0 s", "flux": 1.0},
                         {"dt": "60.0 s", "flux": 0.0}]
        })
    }
    #[test]
    fn composition_renormalizes_rest() {
        let pts = sweep_specs(
            &doc(),
            &SweepAxis::CompositionFraction {
                element: "FE".into(),
            },
            &[50.0],
        )
        .unwrap();
        let d: Value = serde_json::from_str(&pts[0].spec_json).unwrap();
        let c = &d["material"]["composition"];
        assert_eq!(c["FE"], 50.0);
        assert_eq!(c["CR"], 50.0);
    }
    #[test]
    fn flux_scales_total() {
        let pts = sweep_specs(&doc(), &SweepAxis::FluxNormalization, &[2.0]).unwrap();
        let d: Value = serde_json::from_str(&pts[0].spec_json).unwrap();
        assert_eq!(d["spectrum"]["total"], 6.0e12);
    }
    #[test]
    fn cooling_sets_dt() {
        let pts =
            sweep_specs(&doc(), &SweepAxis::CoolingTimeS { step_index: 1 }, &[120.0]).unwrap();
        let d: Value = serde_json::from_str(&pts[0].spec_json).unwrap();
        assert_eq!(d["schedule"][1]["dt"], "120 s");
    }
    #[test]
    fn point_cap_enforced() {
        assert!(sweep_specs(
            &doc(),
            &SweepAxis::FluxNormalization,
            &vec![1.0; MAX_SWEEP_POINTS + 1],
        )
        .is_err());
    }
    #[test]
    fn distinct_points_have_distinct_digests() {
        let pts = sweep_specs(&doc(), &SweepAxis::FluxNormalization, &[1.0, 2.0]).unwrap();
        assert_ne!(pts[0].spec_sha256, pts[1].spec_sha256);
    }
    #[test]
    fn stale_generation_rejected() {
        let p = CompletedPoint {
            generation: 1,
            param: 1.0,
            label: "x".into(),
            spec_sha256: "s".into(),
            result: Err("e".into()),
            elapsed_ms: 0,
        };
        assert!(!admissible(2, &p));
        assert!(admissible(1, &p));
    }
}
