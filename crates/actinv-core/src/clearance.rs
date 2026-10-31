//! Certified probabilistic clearance (P54): `actinv clearance`.
//!
//! Evaluates the IAEA-style sum-of-ratios criterion `S = Σᵢ aᵢ/Lᵢ` per cell
//! (or per run result) at a named schedule step and certifies
//! `P(S < 1)` as an interval bounded by the two declared combination rules —
//! independence (√Σσᵢ²) and full correlation (Σσᵢ) — under the declared
//! linearized-Gaussian propagation model. Coverage ledgers make sure nothing
//! outside the statistic is silently ignored.
//!
//! With a national table selected (`NationalSelect`: Germany, the United Kingdom or Canada, from the
//! bundled `actinv-radiological-table-1` files) the same statistic runs against that table, and every
//! nuclide present but absent from it is reported in `not_listed`, never treated as zero or ignored.

use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};

/// Bundled IAEA 2004 clearance table (RS-G-1.7 Table 2 basis, Bq g⁻¹).
/// Canonical artifact: `data/clearance_iaea_2004.json`; `crates/actinv-core/data/`
/// carries the same bytes so the published crate tarball contains the table
/// (cargo packages may not include paths outside the package root).
const BUNDLED_LIMITS: &str = include_str!("../data/clearance_iaea_2004.json");

pub struct LimitsTable {
    /// RS-G-1.7-style keys ("Fe-55", "Co-60m") -> Bq/g.
    pub limits: Vec<(String, f64)>,
    pub source: String,
    pub sha256: String,
}

impl LimitsTable {
    pub fn bundled() -> Result<Self, String> {
        Self::parse(BUNDLED_LIMITS.to_string())
    }

    pub fn parse(text: String) -> Result<Self, String> {
        let doc: Value = serde_json::from_str(&text)
            .map_err(|e| format!("clearance limits file is not JSON: {e}"))?;
        if doc.get("schema").and_then(Value::as_str) != Some("actinv-clearance-limits-1") {
            return Err("limits file schema is not actinv-clearance-limits-1".into());
        }
        let source = doc
            .get("source")
            .and_then(Value::as_str)
            .unwrap_or("unspecified")
            .to_string();
        let obj = doc
            .get("limits")
            .and_then(Value::as_object)
            .ok_or("limits file carries no 'limits' object")?;
        let mut limits: Vec<(String, f64)> = Vec::with_capacity(obj.len());
        for (k, v) in obj {
            let lim = v
                .as_f64()
                .ok_or_else(|| format!("limits entry '{k}' is not a number"))?;
            if !lim.is_finite() || lim <= 0.0 {
                return Err(format!("limits entry '{k}' must be a positive finite Bq/g"));
            }
            limits.push((k.clone(), lim));
        }
        limits.sort_by(|a, b| a.0.cmp(&b.0));
        let sha256 = sha256_hex(text.as_bytes());
        Ok(LimitsTable {
            limits,
            source,
            sha256,
        })
    }
}

/// Normalise an ACTINV inventory name ("Co60m1", "Fe55") to the RS-G-1.7
/// dashed table form ("Co-60m", "Fe-55"). `m1`/`m2` map to `m`/`m2`.
pub fn table_key(nuclide: &str) -> Option<String> {
    let bytes = nuclide.as_bytes();
    let mut split = None;
    for (i, &b) in bytes.iter().enumerate() {
        if b.is_ascii_digit() {
            split = Some(i);
            break;
        }
    }
    let i = split?;
    let (elem, rest) = nuclide.split_at(i);
    if elem.is_empty() || !elem.chars().next()?.is_ascii_uppercase() {
        return None;
    }
    let mut digits_end = rest.len();
    let mut iso = String::new();
    for (j, c) in rest.char_indices() {
        if c.is_ascii_digit() {
            continue;
        }
        digits_end = j;
        iso = rest[j..].to_string();
        break;
    }
    let mass = &rest[..digits_end];
    let iso = match iso.as_str() {
        "" => String::new(),
        "m1" => "m".to_string(),
        "m2" => "m2".to_string(),
        other => other.to_string(),
    };
    Some(format!("{elem}-{mass}{iso}"))
}

/// Activity (Bq/g) below which a nuclide missing from a national table is counted but not listed in
/// `not_listed`.
pub const DEFAULT_NEGLIGIBLE_BQ_G: f64 = 1e-9;

/// National tables shipped inside the program: `--jurisdiction` code -> bundled radiological table.
pub const JURISDICTIONS: [(&str, &str); 3] = [
    ("ca", "ca-nsrdr-2000-207-schedule-2"),
    ("de", "de-strlschv-2018-anlage-4"),
    ("uk", "uk-epr-2016-sch23-pt3-table-2"),
];

/// Element symbols for atomic numbers 1 to 81: the Canadian rule for nuclides that Schedule 2 does not
/// list applies up to atomic number 81.
const ELEMENTS_Z1_81: [&str; 81] = [
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne", "Na", "Mg", "Al", "Si", "P", "S", "Cl",
    "Ar", "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn", "Ga", "Ge", "As",
    "Se", "Br", "Kr", "Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In",
    "Sn", "Sb", "Te", "I", "Xe", "Cs", "Ba", "La", "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb",
    "Dy", "Ho", "Er", "Tm", "Yb", "Lu", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg", "Tl",
];

/// Which national table, and which route (response) of it, `emit_clearance_for` evaluates.
#[derive(Clone, Debug)]
pub struct NationalSelect {
    /// `de`, `uk` or `ca`.
    pub jurisdiction: String,
    /// A response ID of the table; `None` is the table's first response.
    pub route: Option<String>,
    /// Activity (Bq/g) below which a nuclide missing from the table is counted, not listed.
    pub negligible_bq_g: f64,
}

/// A bundled national table reduced to one route, in the units and spelling this module uses.
#[derive(Clone, Debug)]
pub struct NationalTable {
    pub code: String,
    pub table: String,
    pub route: String,
    pub routes: Vec<String>,
    pub title: String,
    pub basis: String,
    pub sha256: String,
    pub source: Value,
    /// Table-form key ("Co-60m") -> Bq/g.
    pub limits: BTreeMap<String, f64>,
    /// Progeny -> parents whose value already includes it (table-form keys).
    pub covered_by_parent: BTreeMap<String, Vec<String>>,
    /// Every nuclide any route of the table lists.
    pub listed_in_table: BTreeSet<String>,
}

impl NationalTable {
    /// The bundled table of `code` (`de`, `uk`, `ca`), at `route` (default: its first response).
    pub fn bundled(code: &str, route: Option<&str>) -> Result<Self, String> {
        let code = code.to_ascii_lowercase();
        let name = JURISDICTIONS
            .iter()
            .find(|(c, _)| *c == code)
            .map(|(_, name)| *name)
            .ok_or_else(|| {
                format!(
                    "unknown jurisdiction '{code}'; available: iaea (the default), {}",
                    JURISDICTIONS
                        .iter()
                        .map(|(c, _)| *c)
                        .collect::<Vec<_>>()
                        .join(", ")
                )
            })?;
        let (text, sha256) = crate::radiological::read_bundled(name, None)?;
        Self::from_text(&code, name, &text, sha256, route)
    }

    fn from_text(
        code: &str,
        name: &str,
        text: &str,
        sha256: String,
        route: Option<&str>,
    ) -> Result<Self, String> {
        let doc: Value = serde_json::from_str(text)
            .map_err(|e| format!("national table {name} is not JSON: {e}"))?;
        let responses = doc
            .get("responses")
            .and_then(Value::as_array)
            .ok_or_else(|| format!("national table {name} carries no responses"))?;
        let mut routes = Vec::new();
        let mut listed_in_table = BTreeSet::new();
        let mut chosen: Option<&Value> = None;
        for response in responses {
            if response.get("kind").and_then(Value::as_str) != Some("clearance_index") {
                continue;
            }
            let id = response
                .get("id")
                .and_then(Value::as_str)
                .ok_or_else(|| format!("national table {name} has a response without an id"))?;
            routes.push(id.to_string());
            if let Some(coefficients) = response.get("coefficients").and_then(Value::as_object) {
                listed_in_table.extend(coefficients.keys().filter_map(|k| table_key(k)));
            }
            let wanted = match route {
                Some(r) => r == id,
                None => chosen.is_none(),
            };
            if wanted && chosen.is_none() {
                chosen = Some(response);
            }
        }
        let response = chosen.ok_or_else(|| {
            format!(
                "jurisdiction '{code}' has no route '{}'; routes: {}",
                route.unwrap_or(""),
                routes.join(", ")
            )
        })?;
        let route_id = response
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let coefficients = response
            .get("coefficients")
            .and_then(Value::as_object)
            .ok_or_else(|| format!("route '{route_id}' of {name} carries no coefficients"))?;
        let mut limits = BTreeMap::new();
        for (key, value) in coefficients {
            let bq_per_kg = value
                .as_f64()
                .filter(|c| c.is_finite() && *c > 0.0)
                .ok_or_else(|| format!("{name} coefficient '{key}' is not a positive number"))?;
            let label = table_key(key)
                .ok_or_else(|| format!("{name} coefficient key '{key}' is not a nuclide"))?;
            limits.insert(label, bq_per_kg / 1000.0);
        }
        let mut covered_by_parent = BTreeMap::new();
        if let Some(map) = response.get("covered_by_parent").and_then(Value::as_object) {
            for (progeny, parents) in map {
                let label = table_key(progeny)
                    .ok_or_else(|| format!("{name} progeny key '{progeny}' is not a nuclide"))?;
                let list = parents
                    .as_array()
                    .ok_or_else(|| format!("{name} parents of '{progeny}' are not a list"))?
                    .iter()
                    .filter_map(Value::as_str)
                    .filter_map(table_key)
                    .collect::<Vec<_>>();
                covered_by_parent.insert(label, list);
            }
        }
        let text_of = |node: &Value, key: &str| {
            node.get(key)
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string()
        };
        Ok(NationalTable {
            code: code.to_string(),
            table: name.to_string(),
            route: route_id,
            routes,
            title: text_of(&doc, "title"),
            basis: text_of(response, "basis"),
            sha256,
            source: doc.get("source").cloned().unwrap_or(Value::Null),
            limits,
            covered_by_parent,
            listed_in_table,
        })
    }

    /// The regulation's own level for a nuclide the table does not list, when it gives one that needs no
    /// further data. Canada: 1 Bq/g up to atomic number 81 (definition of unconditional clearance level,
    /// paragraph (b)(i)). Above 81 the level depends on alpha emission, which is not decided here.
    pub fn default_limit(&self, key: &str) -> Option<f64> {
        if self.code != "ca" {
            return None;
        }
        let symbol = key.split('-').next()?;
        ELEMENTS_Z1_81.contains(&symbol).then_some(1.0)
    }

    fn note(&self) -> &'static str {
        match self.code.as_str() {
            "de" => "StrlSchV Erlaeuterung zu den Spalten 2 und 3 lets nuclides be left out of the sum when they are at most 10 percent of it; ACTINV does not, and reports nuclides without a value in the route column in not_listed. The conditions of sections 31 to 42 and Anlage 8 apply to a clearance decision.",
            "uk" => "Schedule 23 Part 3 Table 2 is the concentration table referred to in paragraphs 5 and 6 of Part 2; the regulation does not call it clearance. For any other artificial radionuclide it gives 0.01 Bq/g or a dose-based concentration (IAEA RS-G-1.7); ACTINV does not apply that row and reports such nuclides in not_listed.",
            "ca" => "Nuclides Schedule 2 does not list take the regulation's level of 1 Bq/g up to atomic number 81 (basis default_rule). Above 81 the level is 1 Bq/g without alpha emission and 0.1 Bq/g with it; ACTINV does not decide alpha emission and reports those nuclides in not_listed. Natural thorium and uranium rows are not nuclide-keyed and are not used.",
            _ => "",
        }
    }

    fn describe(&self) -> String {
        format!("{} ({}; route {})", self.title, self.table, self.route)
    }
}

/// A selected national table with the reporting threshold, as `evaluate_step` consumes it.
struct NationalCtx<'a> {
    table: &'a NationalTable,
    negligible: f64,
}

/// Nuclides present in a cell that the statistic does not use, and why.
#[derive(Default)]
struct UnlistedLedger {
    not_listed: Vec<Value>,
    covered: Vec<Value>,
    below_count: usize,
    below_activity: f64,
}

impl NationalCtx<'_> {
    /// Parents present in the cell whose table value already includes `key`.
    fn live_parents(&self, present: &BTreeSet<String>, key: Option<&str>) -> Vec<&String> {
        key.and_then(|k| self.table.covered_by_parent.get(k))
            .map(|parents| parents.iter().filter(|p| present.contains(*p)).collect())
            .unwrap_or_default()
    }

    fn record_unlisted(
        &self,
        ledger: &mut UnlistedLedger,
        nuclide: &str,
        key: Option<&str>,
        activity: f64,
    ) {
        if activity < self.negligible {
            ledger.below_count += 1;
            ledger.below_activity += activity;
            return;
        }
        let reason = match key {
            None => "name has no table form",
            Some(k) if self.table.listed_in_table.contains(k) => {
                "listed in the table but has no value for the selected route"
            }
            Some(_) => "not in the table",
        };
        ledger.not_listed.push(json!({
            "nuclide": nuclide,
            "table_label": key,
            "activity_Bq_g": activity,
            "reason": reason,
        }));
    }

    fn header_json(&self) -> Value {
        let t = self.table;
        json!({
            "jurisdiction": t.code,
            "table": t.table,
            "route": t.route,
            "routes_available": t.routes,
            "title": t.title,
            "basis": t.basis,
            "source": t.source,
            "table_sha256": t.sha256,
            "negligible_threshold_Bq_g": self.negligible,
            "limits_unit": "Bq/g (the table's Bq/kg divided by 1000)",
            "note": t.note(),
        })
    }
}

fn sha256_hex(bytes: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// Standard normal CDF Φ(x) = ½[1 + erf(x/√2)].
fn normal_cdf(x: f64) -> f64 {
    0.5 * (1.0 + erf(x * std::f64::consts::FRAC_1_SQRT_2))
}

fn erf(x: f64) -> f64 {
    // Abramowitz–Stegun 7.1.26, |ε| ≤ 1.5e-7 — declared accuracy class for a
    // certification *interval* whose endpoints already bracket the model.
    let t = 1.0 / (1.0 + 0.3275911 * x.abs());
    let poly = t
        * (0.254829592
            + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429))));
    let y = 1.0 - poly * (-x * x).exp();
    if x >= 0.0 {
        y
    } else {
        -y
    }
}

fn step_from(record: &Value, step: u64) -> Result<&Value, String> {
    let steps = record
        .pointer("/result/steps")
        .or_else(|| record.get("steps"))
        .and_then(Value::as_array)
        .ok_or("record carries no result.steps array")?;
    steps
        .iter()
        .find(|s| s.get("step").and_then(Value::as_u64) == Some(step))
        .ok_or_else(|| format!("record has no step {step} (carries {} steps)", steps.len()))
}

fn f64v(v: &Value, key: &str) -> Option<f64> {
    v.get(key).and_then(Value::as_f64)
}

fn response_sigma(resp: &Value) -> f64 {
    for key in ["combined_standard_uncertainty", "mf33_standard_uncertainty"] {
        if let Some(v) = f64v(resp, key) {
            return v;
        }
    }
    0.0
}

fn cell_id_of(rec: &Value, index: usize) -> String {
    rec.get("id")
        .and_then(Value::as_str)
        .map(str::to_string)
        .unwrap_or_else(|| format!("cell-{index}"))
}

/// Evaluate one step's activity map against the limits table.
fn evaluate_step(
    step_obj: &Value,
    table: &BTreeMap<String, f64>,
    confidence: f64,
    national: Option<&NationalCtx<'_>>,
) -> Result<Value, String> {
    let activities = step_obj
        .get("activity_Bq_per_g")
        .and_then(Value::as_object)
        .ok_or("step carries no activity_Bq_per_g map")?;
    let responses = step_obj
        .pointer("/uncertainty/responses")
        .and_then(Value::as_object)
        .ok_or(
            "step carries no uncertainty.responses — clearance requires an uncertainty-banded run",
        )?;

    // Nuclides with activity in the cell: a progeny is covered while its listed parent is among them.
    let present: BTreeSet<String> = if national.is_some() {
        activities
            .iter()
            .filter(|(_, av)| av.as_f64().is_some_and(|a| a.is_finite() && a > 0.0))
            .filter_map(|(nuc, _)| table_key(nuc))
            .collect()
    } else {
        BTreeSet::new()
    };
    let mut ledger = UnlistedLedger::default();
    let mut sum_ratio = 0.0;
    let mut var_ind = 0.0;
    let mut sig_cons = 0.0;
    let mut banded_ratio = 0.0;
    let mut unbanded_ratio = 0.0;
    let mut unregulated_act = 0.0;
    let mut total_act = 0.0;
    let mut regulated_count = 0usize;
    let mut nuclides = Vec::new();
    let mut dominant_ratio: Option<(f64, &str)> = None;
    let mut dominant_sigma: Option<(f64, &str)> = None;

    for (nuc, av) in activities {
        let a = av.as_f64().unwrap_or(0.0);
        if !(a.is_finite() && a > 0.0) {
            continue;
        }
        total_act += a;
        let tkey = table_key(nuc);
        if let Some(n) = national {
            let live = n.live_parents(&present, tkey.as_deref());
            if !live.is_empty() {
                ledger.covered.push(json!({
                    "nuclide": nuc,
                    "table_label": tkey,
                    "activity_Bq_g": a,
                    "covered_by_parent": live,
                }));
                continue;
            }
        }
        let mut basis = "table";
        let mut found = tkey.as_deref().and_then(|k| table.get(k).copied());
        let default = match found {
            Some(_) => None,
            None => tkey
                .as_deref()
                .and_then(|k| national.and_then(|n| n.table.default_limit(k))),
        };
        if default.is_some() {
            found = default;
            basis = "default_rule";
        }
        let Some(limit) = found else {
            unregulated_act += a;
            nuclides.push(match &tkey {
                Some(k) => json!({
                    "nuclide": nuc,
                    "table_label": k,
                    "activity_Bq_g": a,
                    "in_table": false,
                    "banded": false,
                }),
                None => json!({
                    "nuclide": nuc,
                    "activity_Bq_g": a,
                    "in_table": false,
                    "banded": false,
                }),
            });
            if let Some(n) = national {
                n.record_unlisted(&mut ledger, nuc, tkey.as_deref(), a);
            }
            continue;
        };
        let tkey = tkey.unwrap_or_default();
        regulated_count += 1;
        let resp = responses.get(&format!("activity:{nuc}"));
        let sigma = resp.map(response_sigma).unwrap_or(0.0);
        let r_i = a / limit;
        let sr_i = sigma / limit;
        let banded = sr_i > 0.0;
        sum_ratio += r_i;
        var_ind += sr_i * sr_i;
        sig_cons += sr_i;
        if banded {
            banded_ratio += r_i;
        } else {
            unbanded_ratio += r_i;
        }
        if dominant_ratio.is_none_or(|(v, _)| r_i > v) {
            dominant_ratio = Some((r_i, nuc));
        }
        if dominant_sigma.is_none_or(|(v, _)| sr_i > v) {
            dominant_sigma = Some((sr_i, nuc));
        }
        let mut entry = json!({
            "nuclide": nuc,
            "table_label": tkey,
            "activity_Bq_g": a,
            "limit_Bq_g": limit,
            "ratio": r_i,
            "sigma_Bq_g": sigma,
            "sigma_ratio": sr_i,
            "banded": banded,
            "in_table": true,
        });
        if national.is_some() {
            entry["basis"] = json!(basis);
        }
        nuclides.push(entry);
    }

    let sigma_ind = var_ind.sqrt();
    let (p_lo, p_hi, classification) = if sigma_ind == 0.0 && sig_cons == 0.0 {
        let det = if sum_ratio < 1.0 {
            "deterministic_clear"
        } else {
            "deterministic_fail"
        };
        let p = if sum_ratio < 1.0 { 1.0 } else { 0.0 };
        (p, p, det.to_string())
    } else {
        let p_ind = normal_cdf((1.0 - sum_ratio) / sigma_ind.max(1e-300));
        let p_cons = normal_cdf((1.0 - sum_ratio) / sig_cons.max(1e-300));
        let (lo, hi) = (p_cons.min(p_ind), p_cons.max(p_ind));
        let cls = if lo >= confidence {
            "clears_certified"
        } else if hi <= 1.0 - confidence {
            "fails_certified"
        } else {
            "indeterminate"
        };
        (lo, hi, cls.to_string())
    };

    let clears = match classification.as_str() {
        "clears_certified" | "deterministic_clear" => Some(true),
        "fails_certified" | "deterministic_fail" => Some(false),
        _ => None,
    };
    let mut out = json!({
        "record": "clearance",
        "sum_ratio": sum_ratio,
        "sigma_sum_independent": sigma_ind,
        "sigma_sum_conservative": sig_cons,
        "p_clear_interval": [p_lo, p_hi],
        "classification": classification,
        "clears": clears,
        "dominant_ratio": dominant_ratio.map(|(v, n)| json!({"nuclide": n, "ratio": v})),
        "dominant_sigma": dominant_sigma.map(|(v, n)| json!({"nuclide": n, "sigma_ratio": v})),
        "coverage": {
            "banded_ratio_share": if sum_ratio > 0.0 { banded_ratio / sum_ratio } else { 0.0 },
            "unbanded_ratio_share": if sum_ratio > 0.0 { unbanded_ratio / sum_ratio } else { 0.0 },
            "unregulated_activity_share": if total_act > 0.0 { unregulated_act / total_act } else { 0.0 },
            "regulated_count": regulated_count,
        },
        "nuclides": nuclides,
    });
    if let Some(n) = national {
        ledger.not_listed.sort_by(|x, y| {
            y["activity_Bq_g"]
                .as_f64()
                .partial_cmp(&x["activity_Bq_g"].as_f64())
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        out["not_listed"] = json!(ledger.not_listed);
        out["covered_by_parent"] = json!(ledger.covered);
        out["below_negligible_unlisted"] = json!({
            "threshold_Bq_g": n.negligible,
            "count": ledger.below_count,
            "activity_Bq_g": ledger.below_activity,
        });
    }
    Ok(out)
}

/// Emit `actinv-clearance-1` records for every cell/result in INPUT.
/// INPUT may be an `actinv mesh` NDJSON (record:cell) or an `actinv run`
/// result JSON (single object with a steps array).
pub fn emit_clearance(
    input_bytes: &[u8],
    step: u64,
    limits_path: Option<&str>,
    confidence: f64,
) -> Result<String, String> {
    emit_clearance_for(input_bytes, step, limits_path, confidence, None)
}

/// `emit_clearance` against a bundled national table when `national` is given: the records then also carry
/// `not_listed` (nuclides present above the negligible threshold that the table lacks), `covered_by_parent`
/// (progeny whose listed parent is present, left out of the sum) and `below_negligible_unlisted`, and the
/// header a `jurisdiction` block. Without `national` the output is that of `emit_clearance`.
pub fn emit_clearance_for(
    input_bytes: &[u8],
    step: u64,
    limits_path: Option<&str>,
    confidence: f64,
    national: Option<&NationalSelect>,
) -> Result<String, String> {
    // At or below 1/2 the clears and fails tests (lo >= c, hi <= 1 - c) can both
    // hold, and at 0 every cell would certify, so the lower bound is exclusive 1/2.
    if !confidence.is_finite() || confidence <= 0.5 || confidence >= 1.0 {
        return Err("confidence must be in the open interval (0.5, 1)".into());
    }
    let national_table = match national {
        Some(select) => {
            if limits_path.is_some() {
                return Err("a limits file and a jurisdiction cannot be combined".into());
            }
            if !select.negligible_bq_g.is_finite() || select.negligible_bq_g < 0.0 {
                return Err("negligible threshold must be a finite number >= 0 Bq/g".into());
            }
            Some(NationalTable::bundled(
                &select.jurisdiction,
                select.route.as_deref(),
            )?)
        }
        None => None,
    };
    let national_ctx = national_table
        .as_ref()
        .zip(national)
        .map(|(table, select)| NationalCtx {
            table,
            negligible: select.negligible_bq_g,
        });
    let started = std::time::Instant::now();
    let input_sha = sha256_hex(input_bytes);
    let (table, table_sha, table_source) = match (&national_table, limits_path) {
        (Some(t), _) => (t.limits.clone(), t.sha256.clone(), t.describe()),
        (None, Some(path)) => {
            let text = std::fs::read_to_string(path)
                .map_err(|e| format!("cannot read limits file {path}: {e}"))?;
            let t = LimitsTable::parse(text)?;
            (
                t.limits.iter().cloned().collect::<BTreeMap<_, _>>(),
                t.sha256.clone(),
                t.source.clone(),
            )
        }
        (None, None) => {
            let t = LimitsTable::bundled()?;
            (
                t.limits.iter().cloned().collect::<BTreeMap<_, _>>(),
                t.sha256.clone(),
                t.source.clone(),
            )
        }
    };

    let text = std::str::from_utf8(input_bytes).map_err(|e| format!("input is not UTF-8: {e}"))?;
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return Err("input is empty".into());
    }

    // Shape detection: try a whole-document parse first (run result JSON may
    // be pretty-printed over many lines); else treat the file as NDJSON.
    let records: Vec<Value> = if let Ok(doc) = serde_json::from_str::<Value>(trimmed) {
        vec![doc]
    } else if trimmed.starts_with('{') {
        trimmed
            .lines()
            .filter(|l| !l.trim().is_empty())
            .map(|l| serde_json::from_str(l).map_err(|e| format!("input line is not JSON: {e}")))
            .collect::<Result<_, _>>()?
    } else {
        return Err("input is neither a run result JSON nor a mesh NDJSON".into());
    };

    let mut out_lines = Vec::new();
    let mut header = json!({
        "record": "header",
        "schema": "actinv-clearance-1",
        "input_sha256": input_sha,
        "step": step,
        "confidence_threshold": confidence,
        "limits": {
            "sha256": table_sha,
            "source": table_source,
            "count": table.len(),
        },
        "probability_model": "per-nuclide activities are Gaussian marginals under the linearized MF33 XS propagation (same coverage class as the emitted bands); P(S<1) is bracketed by the independent and fully-correlated variance rules",
        "clearance_rule": "sum-of-ratios S = sum a_i/L_i < 1 (IAEA unconditional-clearance convention); nuclides absent from the table are excluded from S and reported in unregulated_activity_share",
    });
    if let Some(ctx) = &national_ctx {
        header["jurisdiction"] = ctx.header_json();
    }
    out_lines.push(header);

    let mut n_cells = 0usize;
    let mut n_clear = 0usize;
    let mut n_fail = 0usize;
    let mut n_indeterminate = 0usize;
    let mut n_deterministic = 0usize;
    let mut n_not_listed = 0usize;
    for rec in records.iter() {
        let kind = rec.get("record").and_then(Value::as_str);
        let target = match kind {
            Some("cell") => Some(rec),
            Some("header") | Some("correlation") | Some("footer") => None,
            Some(other) => return Err(format!("unexpected mesh record '{other}'")),
            None => {
                if rec.get("steps").is_some() {
                    Some(rec)
                } else {
                    None
                }
            }
        };
        let Some(target) = target else { continue };
        let step_obj = step_from(target, step).map_err(|e| format!("cell {n_cells}: {e}"))?;
        let cell_id = cell_id_of(target, n_cells);
        let mut eval = evaluate_step(step_obj, &table, confidence, national_ctx.as_ref())?;
        if eval
            .get("not_listed")
            .and_then(Value::as_array)
            .is_some_and(|list| !list.is_empty())
        {
            n_not_listed += 1;
        }
        eval["cell_index"] = json!(n_cells);
        eval["cell_id"] = json!(cell_id);
        match eval["classification"].as_str().unwrap_or("") {
            "clears_certified" => n_clear += 1,
            "deterministic_clear" => {
                n_clear += 1;
                n_deterministic += 1;
            }
            "fails_certified" => n_fail += 1,
            "deterministic_fail" => {
                n_fail += 1;
                n_deterministic += 1;
            }
            _ => n_indeterminate += 1,
        }
        n_cells += 1;
        out_lines.push(eval);
    }
    if n_cells == 0 {
        return Err("input carries no cell records or run results".into());
    }

    let mut footer = json!({
        "record": "footer",
        "cells_evaluated": n_cells,
        "cells_certified_clear": n_clear,
        "cells_certified_fail": n_fail,
        "cells_indeterminate": n_indeterminate,
        "cells_deterministic": n_deterministic,
        "totals_cover": "certifications cover the MF33-propagated channel only; unbanded_ratio_share and unregulated_activity_share mark what the statistic does not cover",
        "wall_time_s": started.elapsed().as_secs_f64(),
    });
    if national_ctx.is_some() {
        footer["cells_with_not_listed"] = json!(n_not_listed);
    }
    out_lines.push(footer);

    let mut out = String::new();
    for line in out_lines {
        out.push_str(&serde_json::to_string(&line).map_err(|e| e.to_string())?);
        out.push('\n');
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn confidence_outside_open_half_to_one_is_refused() {
        for c in [0.0, 0.3, 0.5, 1.0, 1.5, -0.1, f64::NAN, f64::INFINITY] {
            let err = emit_clearance(b"{}", 1, None, c).unwrap_err();
            assert!(err.contains("(0.5, 1)"), "{c}: {err}");
        }
    }

    #[test]
    fn valid_confidence_passes_the_range_check() {
        // The range check precedes parsing, so a valid level fails later on the input.
        let err = emit_clearance(b"not json", 1, None, 0.95).unwrap_err();
        assert!(!err.contains("confidence"), "{err}");
    }

    /// A run result with one step of deterministic (unbanded) activities in Bq/g.
    fn run_result(activities: &[(&str, f64)]) -> Vec<u8> {
        let activity: serde_json::Map<String, Value> = activities
            .iter()
            .map(|(name, a)| (name.to_string(), json!(a)))
            .collect();
        serde_json::to_vec(&json!({
            "steps": [{
                "step": 1,
                "activity_Bq_per_g": activity,
                "uncertainty": {"responses": {}},
            }]
        }))
        .unwrap()
    }

    fn select(code: &str, route: Option<&str>) -> NationalSelect {
        NationalSelect {
            jurisdiction: code.to_string(),
            route: route.map(str::to_string),
            negligible_bq_g: DEFAULT_NEGLIGIBLE_BQ_G,
        }
    }

    fn records(out: &str) -> Vec<Value> {
        out.lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect()
    }

    fn evaluate(activities: &[(&str, f64)], national: &NationalSelect) -> Vec<Value> {
        let out =
            emit_clearance_for(&run_result(activities), 1, None, 0.95, Some(national)).unwrap();
        records(&out)
    }

    fn close(a: f64, b: f64) -> bool {
        (a - b).abs() <= 1e-12 * b.abs().max(1.0)
    }

    #[test]
    fn default_output_is_unchanged_by_the_jurisdiction_option() {
        let input = run_result(&[("Co60", 0.05), ("Fe55", 10.0), ("Nb92", 0.2)]);
        let plain = emit_clearance(&input, 1, None, 0.95).unwrap();
        let via = emit_clearance_for(&input, 1, None, 0.95, None).unwrap();
        // The footer carries the wall time; the header and the cell record must be identical.
        let (plain, via) = (records(&plain), records(&via));
        assert_eq!(plain[0], via[0]);
        assert_eq!(plain[1], via[1]);
        assert!(plain[0].get("jurisdiction").is_none());
        assert!(plain[1].get("not_listed").is_none());
        assert!(plain[1]["nuclides"][0].get("basis").is_none());
        assert!(plain[2].get("cells_with_not_listed").is_none());
        // Co-60 at 0.1 Bq/g and Fe-55 at 1000 Bq/g in the bundled IAEA table; Nb-92 is not in it.
        assert!(close(plain[1]["sum_ratio"].as_f64().unwrap(), 0.5 + 0.01));
    }

    #[test]
    fn national_limits_match_the_source_tables() {
        // Bq/g as printed: StrlSchV Anlage 4 Tabelle 1 column 3, EPR 2016 Sch. 23 Pt. 3 Table 2,
        // NSRDR Schedule 2.
        let cases = [
            ("de", "Co-60", 0.1),
            ("de", "H-3", 100.0),
            ("de", "Cs-137", 0.1),
            ("de", "Hf-178m2", 0.1),
            ("uk", "Co-60", 0.1),
            ("uk", "H-3", 100.0),
            ("uk", "Cs-137", 1.0),
            ("ca", "Co-60", 0.1),
            ("ca", "H-3", 100.0),
            ("ca", "Fe-55", 1000.0),
        ];
        for (code, nuclide, expected) in cases {
            let table = NationalTable::bundled(code, None).unwrap();
            let limit = table.limits.get(nuclide).copied();
            assert!(
                limit.is_some_and(|l| close(l, expected)),
                "{code} {nuclide}: {limit:?} vs {expected}"
            );
        }
    }

    #[test]
    fn german_routes_read_their_own_columns() {
        let table = NationalTable::bundled("de", None).unwrap();
        assert_eq!(table.route, "unrestricted-clearance");
        assert_eq!(table.routes.len(), 8);
        let scrap = NationalTable::bundled("de", Some("scrap-metal-recycling")).unwrap();
        let soil = NationalTable::bundled("de", Some("soil")).unwrap();
        // Co-60: 6 E-1 Bq/g for scrap metal, 3 E-2 Bq/g for soil areas.
        assert!(close(scrap.limits["Co-60"], 0.6));
        assert!(close(soil.limits["Co-60"], 0.03));
        let error = NationalTable::bundled("de", Some("nope")).unwrap_err();
        assert!(error.contains("no route 'nope'") && error.contains("soil"));
        let error = NationalTable::bundled("uk", Some("soil")).unwrap_err();
        assert!(error.contains("table-2-solid-or-relevant-liquid"));
        let error = NationalTable::bundled("fr", None).unwrap_err();
        assert!(error.contains("unknown jurisdiction 'fr'") && error.contains("iaea"));
    }

    #[test]
    fn sum_of_fractions_names_the_driving_nuclide() {
        let out = evaluate(&[("Co60", 0.03), ("H3", 20.0)], &select("de", None));
        let cell = &out[1];
        // 0.03 / 0.1 + 20 / 100
        assert!(close(cell["sum_ratio"].as_f64().unwrap(), 0.5));
        assert_eq!(cell["dominant_ratio"]["nuclide"], "Co60");
        assert_eq!(cell["classification"], "deterministic_clear");
        assert_eq!(cell["clears"], true);
        let co60 = cell["nuclides"]
            .as_array()
            .unwrap()
            .iter()
            .find(|n| n["nuclide"] == "Co60")
            .unwrap();
        assert!(close(co60["limit_Bq_g"].as_f64().unwrap(), 0.1));
        assert!(close(co60["ratio"].as_f64().unwrap(), 0.3));
        assert_eq!(co60["basis"], "table");
        assert_eq!(out[0]["jurisdiction"]["jurisdiction"], "de");
        assert_eq!(out[0]["jurisdiction"]["route"], "unrestricted-clearance");
        assert_eq!(
            out[0]["limits"]["sha256"],
            out[0]["jurisdiction"]["table_sha256"]
        );
        assert_eq!(out[2]["cells_with_not_listed"], 0);
        // 0.2 / 0.1 alone exceeds 1.
        let out = evaluate(&[("Co60", 0.2)], &select("uk", None));
        assert_eq!(out[1]["classification"], "deterministic_fail");
    }

    #[test]
    fn a_nuclide_missing_from_the_table_is_listed_not_ignored() {
        let out = evaluate(
            &[("Co60", 0.05), ("Nb92", 0.5), ("Fe55", 1.0e-12)],
            &select("de", None),
        );
        let cell = &out[1];
        // Nb-92 has no row in the German table: it stays out of S and is named.
        assert!(close(
            cell["sum_ratio"].as_f64().unwrap(),
            0.5 + 1.0e-12 / 1000.0
        ));
        let listed = cell["not_listed"].as_array().unwrap();
        assert_eq!(listed.len(), 1);
        assert_eq!(listed[0]["nuclide"], "Nb92");
        assert_eq!(listed[0]["reason"], "not in the table");
        assert!(close(listed[0]["activity_Bq_g"].as_f64().unwrap(), 0.5));
        assert_eq!(out[2]["cells_with_not_listed"], 1);
        // Raising the threshold moves it to the counted-but-not-listed ledger.
        let mut high = select("de", None);
        high.negligible_bq_g = 1.0;
        let out = evaluate(&[("Co60", 0.05), ("Nb92", 0.5)], &high);
        assert!(out[1]["not_listed"].as_array().unwrap().is_empty());
        assert_eq!(out[1]["below_negligible_unlisted"]["count"], 1);
        assert!(close(
            out[1]["below_negligible_unlisted"]["activity_Bq_g"]
                .as_f64()
                .unwrap(),
            0.5
        ));
    }

    #[test]
    fn a_blank_route_column_is_not_listed_for_that_route() {
        // Hf-178n+ has an unrestricted-clearance value but none for scrap metal.
        let route = select("de", Some("scrap-metal-recycling"));
        let out = evaluate(&[("Hf178m2", 0.5)], &route);
        let listed = out[1]["not_listed"].as_array().unwrap();
        assert_eq!(listed.len(), 1);
        assert_eq!(
            listed[0]["reason"],
            "listed in the table but has no value for the selected route"
        );
        let out = evaluate(&[("Hf178m2", 0.05)], &select("de", None));
        assert!(close(out[1]["sum_ratio"].as_f64().unwrap(), 0.5));
    }

    #[test]
    fn progeny_of_a_present_parent_is_not_counted_twice() {
        // Cs-137+ covers Ba-137m; Ba-137m also has a row of its own (10 Bq/g).
        let out = evaluate(&[("Cs137", 0.05), ("Ba137m1", 5.0)], &select("de", None));
        let cell = &out[1];
        assert!(close(cell["sum_ratio"].as_f64().unwrap(), 0.5));
        let covered = cell["covered_by_parent"].as_array().unwrap();
        assert_eq!(covered.len(), 1);
        assert_eq!(covered[0]["nuclide"], "Ba137m1");
        assert_eq!(covered[0]["covered_by_parent"][0], "Cs-137");
        assert!(cell["not_listed"].as_array().unwrap().is_empty());
        // Without the parent the progeny is evaluated against its own row.
        let out = evaluate(&[("Ba137m1", 5.0)], &select("de", None));
        assert!(close(out[1]["sum_ratio"].as_f64().unwrap(), 0.5));
        assert!(out[1]["covered_by_parent"].as_array().unwrap().is_empty());
    }

    #[test]
    fn canada_applies_its_default_level_to_light_unlisted_nuclides_only() {
        // Be-10 (Z = 4) is not in Schedule 2: 1 Bq/g by the definition's paragraph (b)(i), flagged as such.
        // Pb-212 (Z = 82) is not listed either and its level depends on alpha emission: not_listed.
        let out = evaluate(&[("Be10", 0.5), ("Pb212", 0.5)], &select("ca", None));
        let cell = &out[1];
        assert!(close(cell["sum_ratio"].as_f64().unwrap(), 0.5));
        let be10 = cell["nuclides"]
            .as_array()
            .unwrap()
            .iter()
            .find(|n| n["nuclide"] == "Be10")
            .unwrap();
        assert_eq!(be10["basis"], "default_rule");
        assert!(close(be10["limit_Bq_g"].as_f64().unwrap(), 1.0));
        let listed = cell["not_listed"].as_array().unwrap();
        assert_eq!(listed.len(), 1);
        assert_eq!(listed[0]["nuclide"], "Pb212");
        // Only Canada has a default.
        let out = evaluate(&[("Be10", 0.5)], &select("uk", None));
        assert_eq!(out[1]["not_listed"][0]["nuclide"], "Be10");
    }

    #[test]
    fn uk_parent_printed_in_several_forms_takes_the_strictest_with_its_chain() {
        // U-238+ is 1 Bq/g, U-238sec is 0.01 Bq/g: the table keeps 0.01 and the secular chain is covered.
        let table = NationalTable::bundled("uk", None).unwrap();
        assert!(close(table.limits["U-238"], 0.01));
        assert!(table.covered_by_parent["Ra-226"].contains(&"U-238".to_string()));
        let out = evaluate(&[("U238", 0.005), ("Th234", 3.0)], &select("uk", None));
        assert!(close(out[1]["sum_ratio"].as_f64().unwrap(), 0.5));
        assert_eq!(out[1]["covered_by_parent"][0]["nuclide"], "Th234");
        // Cf-253 is 100 Bq/g plain and 1 Bq/g with Cm-249: the smaller applies.
        assert!(close(table.limits["Cf-253"], 1.0));
    }

    #[test]
    fn a_jurisdiction_cannot_be_combined_with_a_limits_file() {
        let input = run_result(&[("Co60", 0.05)]);
        let error = emit_clearance_for(
            &input,
            1,
            Some("limits.json"),
            0.95,
            Some(&select("de", None)),
        )
        .unwrap_err();
        assert!(error.contains("cannot be combined"));
        let mut bad = select("de", None);
        bad.negligible_bq_g = -1.0;
        assert!(emit_clearance_for(&input, 1, None, 0.95, Some(&bad)).is_err());
    }

    #[test]
    fn every_national_table_loads_and_is_sized() {
        for (code, name) in JURISDICTIONS {
            let table = NationalTable::bundled(code, None).unwrap();
            assert_eq!(table.table, name);
            assert!(table.limits.len() > 250, "{code}: {}", table.limits.len());
            assert!(!table.covered_by_parent.is_empty(), "{code}");
            assert_eq!(table.sha256.len(), 64);
            assert!(table.source["licence"]
                .as_str()
                .is_some_and(|l| !l.is_empty()));
            // A progeny's parents all have limits of their own.
            for parents in table.covered_by_parent.values() {
                assert!(parents.iter().all(|p| table.limits.contains_key(p)));
            }
        }
    }
}
