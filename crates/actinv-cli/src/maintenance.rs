//! `actinv maintenance PLAN.json [OUT.json]`: when maintenance jobs may start after shutdown, what
//! dose their crews receive, and the wait that trades collective dose against outage length
//! (`actinv_core::maintenance`).
//!
//! The dose rates come from `actinv run` results (each step's point-source dose, Sv/h at the
//! problem's `photon.point_source` distance, one result per location), an `actinv twin` result
//! (its `dose_points`, in Gy/h of air), or an `actinv-dose-map-1` table (Sv/h, times from
//! shutdown).

use std::path::Path;

use actinv_core::maintenance::{self, DoseCurve, Plan, DOSE_MAP_SCHEMA, PLAN_SCHEMA};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::optimize::sha256_hex;

/// Larger inputs are refused rather than read: a twin result of a large mesh stays well under it.
const MAX_INPUT_BYTES: u64 = 1 << 30;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct TwinSource {
    twin: String,
    /// The time, from the start of the twin's problem, at which the machine shuts down.
    shutdown_s: f64,
    /// Sv per Gy of air for the twin's dose points: stated, never assumed.
    sv_per_gy: f64,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct TableSource {
    table: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RunsSource {
    runs: Vec<RunLocation>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RunLocation {
    /// An `actinv run` result whose problem asked for `photon.point_source`.
    result: String,
    location: String,
}

#[derive(Debug, Deserialize)]
#[serde(untagged)]
enum Source {
    Runs(RunsSource),
    Twin(TwinSource),
    Table(TableSource),
}

/// A run's point-source dose after its last irradiated step, in Sv/h, and when that step ended.
fn run_curve(result: &Value, location: &RunLocation) -> Result<(DoseCurve, f64), String> {
    let steps = result["steps"]
        .as_array()
        .filter(|steps| !steps.is_empty())
        .ok_or_else(|| format!("{} is not an actinv run result", location.result))?;
    let shutdown = steps
        .iter()
        .filter(|step| step["flux"].as_f64().is_some_and(|flux| flux > 0.0))
        .filter_map(|step| step["t_s"].as_f64())
        .fold(None, |last: Option<f64>, t| {
            Some(last.map_or(t, |l| l.max(t)))
        })
        .ok_or_else(|| format!("{} has no irradiated step", location.result))?;
    let (mut times, mut rates) = (Vec::new(), Vec::new());
    for step in steps {
        let t = step["t_s"]
            .as_f64()
            .ok_or_else(|| format!("{}: a step has no t_s", location.result))?;
        if t < shutdown {
            continue;
        }
        let dose = &step["photon_source"]["point_source_dose"];
        let sv_h = dose["gamma_air_dose_Sv_h"].as_f64().ok_or_else(|| {
            let why = dose["unavailable_reason"].as_str().map(|r| format!(" ({r})")).unwrap_or_default();
            format!(
                "{} has no point-source dose at t = {t} s{why}: its problem needs photon.point_source and the photons output",
                location.result
            )
        })?;
        times.push(t - shutdown);
        rates.push(sv_h);
    }
    Ok((DoseCurve::new(&location.location, times, rates)?, shutdown))
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct DoseMap {
    schema: String,
    unit: String,
    #[serde(default)]
    source: Option<String>,
    locations: Vec<DoseMapLocation>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct DoseMapLocation {
    name: String,
    times_s: Vec<f64>,
    dose_rate: Vec<f64>,
}

fn read(path: &Path, what: &str) -> Result<Vec<u8>, String> {
    let meta = std::fs::metadata(path).map_err(|e| format!("{what} {}: {e}", path.display()))?;
    if !meta.is_file() {
        return Err(format!("{what} {} is not a file", path.display()));
    }
    if meta.len() > MAX_INPUT_BYTES {
        return Err(format!("{what} {} is larger than 1 GiB", path.display()));
    }
    std::fs::read(path).map_err(|e| format!("{what} {}: {e}", path.display()))
}

/// The twin's dose points after shutdown, in Sv/h.
fn twin_curves(twin: &Value, source: &TwinSource) -> Result<Vec<DoseCurve>, String> {
    if twin["schema"] != "actinv-twin-1" {
        return Err(format!("{} is not an actinv-twin-1 result", source.twin));
    }
    if !(source.shutdown_s.is_finite() && source.shutdown_s >= 0.0) {
        return Err("shutdown_s must be finite and not negative".into());
    }
    if !(source.sv_per_gy.is_finite() && source.sv_per_gy > 0.0) {
        return Err("sv_per_gy must be positive".into());
    }
    let points = twin["dose_points"]
        .as_array()
        .filter(|points| !points.is_empty())
        .ok_or_else(|| format!("{} has no dose_points", source.twin))?;
    let mut curves = Vec::with_capacity(points.len());
    for point in points {
        let name = point["name"].as_str().ok_or("a dose point has no name")?;
        let (mut times, mut rates) = (Vec::new(), Vec::new());
        for step in point["steps"].as_array().into_iter().flatten() {
            let t = step["t_s"]
                .as_f64()
                .ok_or_else(|| format!("dose point '{name}': a step has no t_s"))?;
            if t < source.shutdown_s {
                continue;
            }
            let gy_h = step["dose_gy_h"].as_f64().ok_or_else(|| {
                format!("dose point '{name}' has no dose_gy_h: give it dose_coeff_gy_cm2_per_photon_h in the twin spec")
            })?;
            times.push(t - source.shutdown_s);
            rates.push(gy_h * source.sv_per_gy);
        }
        curves.push(DoseCurve::new(name, times, rates)?);
    }
    Ok(curves)
}

fn table_curves(map: DoseMap, path: &str) -> Result<Vec<DoseCurve>, String> {
    if map.schema != DOSE_MAP_SCHEMA {
        return Err(format!(
            "{path} must declare \"schema\": \"{DOSE_MAP_SCHEMA}\""
        ));
    }
    if map.unit != "Sv/h" {
        return Err(format!(
            "{path}: unit must be \"Sv/h\", not \"{}\"",
            map.unit
        ));
    }
    if map.locations.is_empty() {
        return Err(format!("{path} has no locations"));
    }
    map.locations
        .into_iter()
        .map(|location| DoseCurve::new(&location.name, location.times_s, location.dose_rate))
        .collect()
}

/// Read and solve a plan; the result, with the inputs it used and their SHA-256.
pub fn evaluate_file(plan_path: &str) -> Result<Value, String> {
    let bytes = read(Path::new(plan_path), "plan")?;
    let mut document: Value =
        serde_json::from_slice(&bytes).map_err(|e| format!("{plan_path}: {e}"))?;
    let object = document
        .as_object_mut()
        .ok_or_else(|| format!("{plan_path} is not a JSON object"))?;
    if object.remove("schema") != Some(Value::from(PLAN_SCHEMA)) {
        return Err(format!(
            "{plan_path} must declare \"schema\": \"{PLAN_SCHEMA}\""
        ));
    }
    let source: Source = serde_json::from_value(
        object
            .remove("dose_rates")
            .ok_or_else(|| format!("{plan_path} has no dose_rates"))?,
    )
    .map_err(|_| {
        "dose_rates must be {\"runs\": [{\"result\", \"location\"}]}, {\"twin\", \"shutdown_s\", \"sv_per_gy\"} or {\"table\"}".to_string()
    })?;
    let plan: Plan = serde_json::from_value(document).map_err(|e| format!("{plan_path}: {e}"))?;
    let base = Path::new(plan_path).parent().unwrap_or(Path::new("."));
    let (curves, dose_rates) = match &source {
        Source::Runs(runs) => {
            if runs.runs.is_empty() {
                return Err("dose_rates.runs is empty".into());
            }
            let mut curves = Vec::with_capacity(runs.runs.len());
            let mut used = Vec::with_capacity(runs.runs.len());
            for location in &runs.runs {
                let bytes = read(&base.join(&location.result), "run result")?;
                let value: Value = serde_json::from_slice(&bytes)
                    .map_err(|e| format!("{}: {e}", location.result))?;
                let (curve, shutdown) = run_curve(&value, location)?;
                let distance =
                    value["steps"][0]["photon_source"]["point_source_dose"]["distance_m"].as_f64();
                used.push(json!({
                    "result": location.result, "location": location.location,
                    "sha256": sha256_hex(&bytes), "shutdown_s": shutdown, "distance_m": distance,
                }));
                curves.push(curve);
            }
            (
                curves,
                json!({
                    "runs": used,
                    "note": "point-source dose of each run's whole mass at its photon.point_source distance; shutdown is the end of its last irradiated step",
                }),
            )
        }
        Source::Twin(twin) => {
            let path = base.join(&twin.twin);
            let bytes = read(&path, "twin result")?;
            let value: Value =
                serde_json::from_slice(&bytes).map_err(|e| format!("{}: {e}", twin.twin))?;
            let curves = twin_curves(&value, twin)?;
            let note = value["note"].as_str().unwrap_or_default().to_owned();
            (
                curves,
                json!({
                    "twin": twin.twin, "sha256": sha256_hex(&bytes),
                    "shutdown_s": twin.shutdown_s, "sv_per_gy": twin.sv_per_gy,
                    "note": "point-kernel screening estimates from actinv twin (no scatter or buildup), converted to Sv/h with the plan's sv_per_gy",
                    "twin_note": note,
                }),
            )
        }
        Source::Table(table) => {
            let path = base.join(&table.table);
            let bytes = read(&path, "dose map")?;
            let map: DoseMap =
                serde_json::from_slice(&bytes).map_err(|e| format!("{}: {e}", table.table))?;
            let described = map.source.clone();
            (
                table_curves(map, &table.table)?,
                json!({"table": table.table, "sha256": sha256_hex(&bytes), "source": described}),
            )
        }
    };
    let outcome = maintenance::evaluate(&plan, &curves)?;
    let mut result = serde_json::to_value(&outcome).map_err(|e| e.to_string())?;
    result["plan"] = json!({"path": plan_path, "sha256": sha256_hex(&bytes)});
    result["dose_rates"] = dose_rates;
    result["note"] = json!("The planner does arithmetic on the dose rates it is given; their accuracy is that of their source. It does not say a plan is safe.");
    Ok(result)
}

/// Write a result to `path`.
pub fn write(result: &Value, path: &str) -> Result<(), String> {
    let text = serde_json::to_string_pretty(result).map_err(|e| e.to_string())?;
    std::fs::write(path, text).map_err(|e| format!("cannot write {path}: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn twin() -> Value {
        json!({
            "schema": "actinv-twin-1",
            "dose_points": [{"name": "port", "steps": [
                {"t_s": 100.0, "photon_flux_cm2_s": 1.0, "dose_gy_h": 9.0},
                {"t_s": 1000.0, "photon_flux_cm2_s": 1.0, "dose_gy_h": 2.0},
                {"t_s": 4600.0, "photon_flux_cm2_s": 1.0, "dose_gy_h": 1.0}
            ]}]
        })
    }

    fn source() -> TwinSource {
        TwinSource {
            twin: "t.json".into(),
            shutdown_s: 1000.0,
            sv_per_gy: 0.5,
        }
    }

    #[test]
    fn a_twin_gives_sv_per_hour_from_shutdown() {
        let curves = twin_curves(&twin(), &source()).unwrap();
        assert_eq!(curves[0].first_s(), 0.0);
        assert_eq!(curves[0].last_s(), 3600.0);
        assert_eq!(curves[0].rate(0.0), Some(1.0));
        assert_eq!(curves[0].rate(3600.0), Some(0.5));
    }

    #[test]
    fn a_twin_without_dose_or_conversion_is_refused() {
        let mut bare = twin();
        bare["dose_points"][0]["steps"][2]
            .as_object_mut()
            .unwrap()
            .remove("dose_gy_h");
        assert!(twin_curves(&bare, &source())
            .unwrap_err()
            .contains("dose_coeff_gy_cm2_per_photon_h"));
        let unconverted = TwinSource {
            sv_per_gy: 0.0,
            ..source()
        };
        assert!(twin_curves(&twin(), &unconverted)
            .unwrap_err()
            .contains("sv_per_gy"));
        let wrong = json!({"schema": "actinv-mesh-1"});
        assert!(twin_curves(&wrong, &source()).is_err());
    }

    #[test]
    fn a_run_gives_its_point_source_dose_after_the_last_irradiation() {
        let step = |t: f64, flux: f64, sv_h: Option<f64>| {
            json!({"t_s": t, "flux": flux,
                   "photon_source": {"point_source_dose": {"gamma_air_dose_Sv_h": sv_h, "distance_m": 1.0}}})
        };
        let result = json!({"steps": [
            step(100.0, 1.0, Some(5.0)), step(200.0, 0.0, Some(4.0)),
            step(300.0, 1.0, Some(6.0)), step(400.0, 0.0, Some(3.0)), step(1300.0, 0.0, Some(1.0))
        ]});
        let location = RunLocation {
            result: "r.json".into(),
            location: "bench".into(),
        };
        let (curve, shutdown) = run_curve(&result, &location).unwrap();
        assert_eq!(shutdown, 300.0);
        assert_eq!(curve.first_s(), 0.0);
        assert_eq!(curve.last_s(), 1000.0);
        assert_eq!(curve.rate(100.0), Some(3.0));
        let unasked = json!({"steps": [step(100.0, 1.0, None), step(200.0, 0.0, None)]});
        assert!(run_curve(&unasked, &location)
            .unwrap_err()
            .contains("photon.point_source"));
    }

    #[test]
    fn a_dose_map_must_be_in_sv_per_hour() {
        let map = |unit: &str| DoseMap {
            schema: DOSE_MAP_SCHEMA.into(),
            unit: unit.into(),
            source: None,
            locations: vec![DoseMapLocation {
                name: "port".into(),
                times_s: vec![0.0, 10.0],
                dose_rate: vec![1.0, 0.5],
            }],
        };
        assert_eq!(table_curves(map("Sv/h"), "m.json").unwrap().len(), 1);
        assert!(table_curves(map("mSv/h"), "m.json")
            .unwrap_err()
            .contains("Sv/h"));
    }
}
