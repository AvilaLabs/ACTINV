#![allow(non_snake_case)] // field names are the JSON wire format (boundaries_eV)
//! `actinv-study-1`: the versioned study document and its deterministic expansion
//! into an `actinv-spec-1` case population (P27).
//!
//! A study names a shared library/decay context, a cases grid
//! (materials x spectra x schedules), the responses to extract and an optional
//! predeclared comparison. `expand` produces a byte-stable case manifest;
//! `execute` runs the population through the unchanged `run` path and writes a
//! `study_record` that preserves the eligible population — executed, failed,
//! contract-gap and undefined-metric cases are counted, never dropped.
//!
//! Families beyond the qualified pair (ACT-STUDY-01, ACT-COMPARE-01) are
//! recognised fields that fail `family_not_qualified` at validation; they are
//! never silently ignored. Records on this path are `unqualified`: the study
//! layer certifies completion and accounting, not scientific qualification.
use crate::run::run;
use crate::spec::{parse_duration, DecayRef, LibraryRef, Material, Options, Spec, Spectrum, Step};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::Instant;

pub const STUDY_SCHEMA: &str = "actinv-study-1";
pub const RECORD_SCHEMA: &str = "actinv-study-record-1";
/// Hard population cap for this schema version: keeps a single study bounded
/// and resumable. Named refusal, never truncation.
pub const MAX_CASES: usize = 1024;
/// Responses the study layer may extract: exactly the set qualified for
/// ACT-STUDY-01 at the P27 G0 freeze.
pub const QUALIFIED_RESPONSES: &[&str] = &[
    "total_activity_bq_per_g",
    "decay_heat_w_per_g",
    "photon_source_per_group",
    "inventory_per_nuclide",
    "total_atoms_per_g",
];

pub const AXES: &[&str] = &["material", "spectrum", "schedule"];
pub const RULE_KINDS: &[&str] = &[
    "within_rel",
    "max_rel",
    "min_rel",
    "ratio_band",
    "rank_equal",
];

const UNQUALIFIED_FAMILIES: &[(&str, &str, &str)] = &[
    ("robustness", "ACT-ROBUST-01", "P30"),
    ("refinement", "ACT-REFINE-01", "P29"),
    ("spatial_handoff", "ACT-SOURCE-01", "P32"),
];

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Study {
    pub study: String,
    pub study_id: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub template: Option<TemplateRef>,
    pub library: LibraryRef,
    #[serde(default)]
    pub decay: DecayRef,
    pub cases: CasesGrid,
    /// absolute post-shutdown times appended as flux-0 schedule steps
    #[serde(default)]
    pub cooling_times_s: Vec<f64>,
    #[serde(default)]
    pub responses: Vec<String>,
    #[serde(default)]
    pub comparison: Option<Comparison>,
    #[serde(default)]
    pub options: Options,
    // Families not yet qualified: accepted by the schema, refused at
    // validation with `family_not_qualified` naming the delivering phase.
    #[serde(default)]
    pub robustness: Option<Value>,
    #[serde(default)]
    pub refinement: Option<Value>,
    #[serde(default)]
    pub spatial_handoff: Option<Value>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TemplateRef {
    pub id: String,
    #[serde(default = "template_v1")]
    pub version: u32,
}

fn template_v1() -> u32 {
    1
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CasesGrid {
    pub materials: Vec<StudyMaterial>,
    pub spectra: Vec<StudySpectrum>,
    pub schedules: Vec<StudySchedule>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StudyMaterial {
    pub name: String,
    #[serde(default)]
    pub mass_g: Option<f64>,
    /// weight-percent composition (v1 fixes the wt_percent basis)
    pub composition: BTreeMap<String, f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StudySpectrum {
    pub name: String,
    /// take the spectrum block from an existing `actinv-spec-1` file
    #[serde(default)]
    pub spec_ref: Option<String>,
    /// whitespace-separated group fluxes on disk, resolved against the
    /// study file's directory
    #[serde(default)]
    pub flux_file: Option<String>,
    /// inline group fluxes
    #[serde(default)]
    pub flux_per_group: Option<Vec<f64>>,
    #[serde(default = "fispact_709")]
    pub structure: String,
    #[serde(default)]
    pub descending: bool,
    #[serde(default)]
    pub total: Option<f64>,
    #[serde(default)]
    pub boundaries_eV: Option<Vec<f64>>,
}

fn fispact_709() -> String {
    "fispact-709".into()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StudySchedule {
    pub name: String,
    /// irradiation step(s); cooling steps are appended from
    /// `cooling_times_s`
    pub steps: Vec<Step>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Comparison {
    /// axes along which cases may differ inside a comparison slice; each in
    /// {material, spectrum, schedule}
    pub axes: Vec<String>,
    pub decision_rules: Vec<DecisionRule>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionRule {
    pub id: String,
    /// bounded vocabulary frozen at G0
    pub kind: String,
    pub response: String,
    /// relative bound for within_rel / max_rel / min_rel
    #[serde(default)]
    pub bound: Option<f64>,
    /// [lo, hi] ratio bound for ratio_band
    #[serde(default)]
    pub band: Option<[f64; 2]>,
    /// expected rank order of case ids for rank_equal
    #[serde(default)]
    pub expected_order: Option<Vec<String>>,
}

impl Study {
    pub fn from_json(text: &str) -> Result<Self, String> {
        let study: Study = serde_json::from_str(text).map_err(|e| format!("study parse: {e}"))?;
        study.validate()?;
        Ok(study)
    }

    /// Schema + scope validation: every refusal is named, nothing
    /// unqualified is silently skipped.
    pub fn validate(&self) -> Result<(), String> {
        if self.study != STUDY_SCHEMA {
            return Err(format!(
                "unsupported study version '{}': this release accepts \
                 [{STUDY_SCHEMA}]",
                self.study
            ));
        }
        if self.robustness.is_some() {
            return Err(family_err("robustness"));
        }
        if self.refinement.is_some() {
            return Err(family_err("refinement"));
        }
        if self.spatial_handoff.is_some() {
            return Err(family_err("spatial_handoff"));
        }
        if self.study_id.is_empty() {
            return Err("study_id must be non-empty".into());
        }
        if self.cases.materials.is_empty()
            || self.cases.spectra.is_empty()
            || self.cases.schedules.is_empty()
        {
            return Err("cases grid requires nonempty materials, spectra and \
                        schedules"
                .into());
        }
        for (label, names) in [
            ("material", self.cases.materials.iter().map(|x| x.name.as_str()).collect::<Vec<_>>()),
            ("spectrum", self.cases.spectra.iter().map(|x| x.name.as_str()).collect()),
            ("schedule", self.cases.schedules.iter().map(|x| x.name.as_str()).collect()),
        ] {
            let mut seen = std::collections::HashSet::new();
            for n in names {
                if !seen.insert(n) {
                    return Err(format!("duplicate_{label} '{n}'"));
                }
            }
        }
        for m in &self.cases.materials {
            check_name("material", &m.name)?;
            if m.composition.is_empty() {
                return Err(format!("material '{}': empty composition", m.name));
            }
            if let Some(mass) = m.mass_g {
                if !(mass > 0.0 && mass.is_finite()) {
                    return Err(format!(
                        "material '{}': mass_g must be a positive finite \
                         number",
                        m.name
                    ));
                }
            }
        }
        for s in &self.cases.spectra {
            check_name("spectrum", &s.name)?;
            let forms = s.spec_ref.is_some() as u8
                + s.flux_file.is_some() as u8
                + s.flux_per_group.is_some() as u8;
            if forms != 1 {
                return Err(format!(
                    "spectrum '{}': exactly one of spec_ref, flux_file, \
                     flux_per_group is required",
                    s.name
                ));
            }
        }
        for s in &self.cases.schedules {
            check_name("schedule", &s.name)?;
            if s.steps.is_empty() {
                return Err(format!("schedule '{}': no steps", s.name));
            }
            for step in &s.steps {
                parse_duration(&step.dt).map_err(|e| format!("schedule '{}': {e}", s.name))?;
                if !step.flux.is_finite() || step.flux < 0.0 {
                    return Err(format!(
                        "schedule '{}': flux must be a finite non-negative \
                         multiplier",
                        s.name
                    ));
                }
            }
        }
        for (i, &t) in self.cooling_times_s.iter().enumerate() {
            if !(t.is_finite() && t >= 0.0) {
                return Err("cooling_times_s entries must be finite non-negative".into());
            }
            if i > 0 && t <= self.cooling_times_s[i - 1] {
                return Err("cooling_times_s must be strictly increasing".into());
            }
        }
        for r in &self.responses {
            if !QUALIFIED_RESPONSES.contains(&r.as_str()) {
                return Err(format!(
                    "response '{r}' is not in the qualified set \
                     {QUALIFIED_RESPONSES:?} (family_not_qualified)"
                ));
            }
        }
        let n = self.cases.materials.len() * self.cases.spectra.len() * self.cases.schedules.len();
        if n > MAX_CASES {
            return Err(format!(
                "study_too_large: {n} cases exceeds the {MAX_CASES} cap"
            ));
        }
        if let Some(c) = &self.comparison {
            for a in &c.axes {
                if !AXES.contains(&a.as_str()) {
                    return Err(format!("comparison axis '{a}' not in {AXES:?}"));
                }
            }
            if c.decision_rules.is_empty() {
                return Err("comparison declares axes but no decision_rules".into());
            }
            for r in &c.decision_rules {
                validate_rule(r)?;
            }
        }
        Ok(())
    }

    /// Deterministic expansion: materials x spectra x schedules in
    /// declaration order; case id `{material}__{spectrum}__{schedule}`.
    pub fn case_ids(&self) -> Vec<String> {
        let mut ids = Vec::new();
        for m in &self.cases.materials {
            for s in &self.cases.spectra {
                for c in &self.cases.schedules {
                    ids.push(format!("{}__{}__{}", m.name, s.name, c.name));
                }
            }
        }
        ids
    }

    /// Emit the `actinv-spec-1` document for one case. `base` resolves
    /// relative spec_refs / flux_files.
    pub fn case_spec(&self, case_id: &str, base: &Path) -> Result<Spec, String> {
        let (m, s, c) = self.case_parts(case_id)?;
        let mut schedule = c.steps.clone();
        for (i, &t) in self.cooling_times_s.iter().enumerate() {
            let prev = if i == 0 {
                0.0
            } else {
                self.cooling_times_s[i - 1]
            };
            let dt = t - prev;
            if dt > 0.0 {
                schedule.push(Step {
                    dt: format!("{dt} s"),
                    flux: 0.0,
                    feed: None,
                    removal: None,
                });
            }
        }
        Ok(Spec {
            spec: "actinv-spec-1".into(),
            title: format!("{} :: {}", self.study_id, case_id),
            projectile: Default::default(),
            library: self.library.clone(),
            decay: self.decay.clone(),
            material: Material {
                mass_g: m.mass_g.unwrap_or(1.0),
                basis: "wt_percent".into(),
                composition: m.composition.clone(),
            },
            spectrum: s.resolve(base)?,
            schedule,
            options: self.options.clone(),
            photon: Default::default(),
            fission_yields: Default::default(),
            uncertainty: None,
            radiological: None,
            damage: None,
            self_shielding: None,
        })
    }

    fn case_parts(
        &self,
        case_id: &str,
    ) -> Result<(&StudyMaterial, &StudySpectrum, &StudySchedule), String> {
        let mut it = case_id.splitn(3, "__");
        let (mn, sn, cn) = (
            it.next().unwrap_or(""),
            it.next().unwrap_or(""),
            it.next().unwrap_or(""),
        );
        let m = self
            .cases
            .materials
            .iter()
            .find(|x| x.name == mn)
            .ok_or_else(|| format!("case '{case_id}': unknown material"))?;
        let s = self
            .cases
            .spectra
            .iter()
            .find(|x| x.name == sn)
            .ok_or_else(|| format!("case '{case_id}': unknown spectrum"))?;
        let c = self
            .cases
            .schedules
            .iter()
            .find(|x| x.name == cn)
            .ok_or_else(|| format!("case '{case_id}': unknown schedule"))?;
        Ok((m, s, c))
    }
}

fn family_err(field: &str) -> String {
    for (f, family, phase) in UNQUALIFIED_FAMILIES {
        if *f == field {
            return format!(
                "family_not_qualified: {family} (field '{field}') is \
                 delivered by {phase}; this release supports ACT-STUDY-01 \
                 and ACT-COMPARE-01 only"
            );
        }
    }
    format!("family_not_qualified: '{field}'")
}

fn validate_rule(r: &DecisionRule) -> Result<(), String> {
    if !RULE_KINDS.contains(&r.kind.as_str()) {
        return Err(format!(
            "decision rule kind '{}' not in {RULE_KINDS:?}",
            r.kind
        ));
    }
    if !QUALIFIED_RESPONSES.contains(&r.response.as_str()) {
        return Err(format!(
            "decision rule '{}' response '{}' is not qualified",
            r.id, r.response
        ));
    }
    match r.kind.as_str() {
        "within_rel" | "max_rel" | "min_rel" => {
            let b = r
                .bound
                .ok_or_else(|| format!("rule '{}' requires bound", r.id))?;
            if !(b.is_finite() && b >= 0.0) {
                return Err(format!("rule '{}' bound must be finite >= 0", r.id));
            }
        }
        "ratio_band" => {
            let b = r
                .band
                .ok_or_else(|| format!("rule '{}' requires band", r.id))?;
            if !(b[0].is_finite() && b[1].is_finite() && b[0] > 0.0 && b[0] <= b[1]) {
                return Err(format!("rule '{}' band must satisfy 0 < lo <= hi", r.id));
            }
        }
        "rank_equal" if r.expected_order.is_none() => {
            return Err(format!("rule '{}' requires expected_order", r.id));
        }
        _ => {}
    }
    Ok(())
}

fn check_name(kind: &str, name: &str) -> Result<(), String> {
    if name.is_empty()
        || !name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-' | '.'))
    {
        return Err(format!(
            "{kind} name '{name}' must be nonempty ASCII [a-zA-Z0-9_.-]"
        ));
    }
    Ok(())
}

impl StudySpectrum {
    fn resolve(&self, base: &Path) -> Result<Spectrum, String> {
        if let Some(r) = &self.spec_ref {
            let text = fs::read_to_string(base.join(r))
                .map_err(|e| format!("spectrum '{}': spec_ref read: {e}", self.name))?;
            let v: Value = serde_json::from_str(&text)
                .map_err(|e| format!("spectrum '{}': spec_ref parse: {e}", self.name))?;
            let sv = v
                .get("spectrum")
                .cloned()
                .ok_or_else(|| format!("spectrum '{}': spec_ref has no spectrum", self.name))?;
            return serde_json::from_value(sv)
                .map_err(|e| format!("spectrum '{}': spec_ref spectrum: {e}", self.name));
        }
        let flux = if let Some(f) = &self.flux_file {
            let text = fs::read_to_string(base.join(f))
                .map_err(|e| format!("spectrum '{}': flux_file read: {e}", self.name))?;
            text.split_whitespace()
                .map(|w| {
                    w.parse::<f64>()
                        .map_err(|e| format!("spectrum '{}': bad float '{w}': {e}", self.name))
                })
                .collect::<Result<Vec<f64>, String>>()?
        } else {
            self.flux_per_group.clone().unwrap_or_default()
        };
        Ok(Spectrum {
            structure: self.structure.clone(),
            flux_per_group: flux,
            total: self.total,
            boundaries_eV: self.boundaries_eV.clone(),
            descending: self.descending,
        })
    }
}

/// A revoked-template ledger: `{revoked_templates: ["id", ...]}`. Refusal is
/// forward-looking; historical records are never edited.
pub fn revoked_templates(path: &Path) -> Result<Vec<String>, String> {
    let v: Value = serde_json::from_str(
        &fs::read_to_string(path).map_err(|e| format!("revocations read: {e}"))?,
    )
    .map_err(|e| format!("revocations parse: {e}"))?;
    v.get("revoked_templates")
        .and_then(Value::as_array)
        .ok_or("revocations: expected revoked_templates array")?
        .iter()
        .map(|x| {
            x.as_str()
                .map(str::to_string)
                .ok_or_else(|| "revocations: entries must be strings".into())
        })
        .collect()
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(bytes);
    format!("{:x}", h.finalize())
}

fn sha256_path(path: &Path) -> Result<String, String> {
    Ok(sha256_hex(
        &fs::read(path).map_err(|e| format!("hash {path:?}: {e}"))?,
    ))
}

/// Canonical JSON: sorted object keys, compact separators — the emitted
/// manifest and spec bytes are stable for identical inputs.
fn canonical_json(v: &Value) -> String {
    fn canon(v: &Value, out: &mut String) {
        match v {
            Value::Object(m) => {
                out.push('{');
                for (i, (k, val)) in m.iter().enumerate() {
                    if i > 0 {
                        out.push(',');
                    }
                    out.push_str(&serde_json::to_string(k).unwrap_or_default());
                    out.push(':');
                    canon(val, out);
                }
                out.push('}');
            }
            Value::Array(a) => {
                out.push('[');
                for (i, val) in a.iter().enumerate() {
                    if i > 0 {
                        out.push(',');
                    }
                    canon(val, out);
                }
                out.push(']');
            }
            other => out.push_str(&serde_json::to_string(other).unwrap_or_else(|_| "null".into())),
        }
    }
    let mut s = String::new();
    canon(v, &mut s);
    s
}

/// `study build`: expand, emit `manifest.json` + `specs/<case>.json`.
/// Byte-deterministic for identical study bytes and resolver inputs.
pub fn build(
    study: &Study,
    study_path: &Path,
    outdir: &Path,
    revocations: Option<&Path>,
) -> Result<Value, String> {
    if let (Some(t), Some(rv)) = (&study.template, revocations) {
        if revoked_templates(rv)?.contains(&t.id) {
            return Err(format!(
                "template_revoked: '{}' is revoked; it cannot produce new \
                 accepted runs",
                t.id
            ));
        }
    }
    let base = study_path.parent().unwrap_or(Path::new("."));
    let specs_dir = outdir.join("specs");
    fs::create_dir_all(&specs_dir).map_err(|e| format!("create {specs_dir:?}: {e}"))?;
    let mut manifest_cases = Vec::new();
    for id in study.case_ids() {
        let spec = study.case_spec(&id, base)?;
        let spec_json = canonical_json(
            &serde_json::to_value(&spec).map_err(|e| format!("spec serialise {id}: {e}"))?,
        );
        let path = specs_dir.join(format!("{id}.json"));
        fs::write(&path, format!("{spec_json}\n")).map_err(|e| format!("write {path:?}: {e}"))?;
        manifest_cases.push(json!({
            "case_id": id,
            "spec": format!("specs/{id}.json"),
            "spec_sha256": sha256_hex(format!("{spec_json}\n").as_bytes()),
        }));
    }
    let study_bytes = fs::read(study_path).map_err(|e| format!("study read: {e}"))?;
    let manifest = json!({
        "schema": "actinv-study-manifest-1",
        "study": STUDY_SCHEMA,
        "study_id": study.study_id,
        "study_sha256": sha256_hex(&study_bytes),
        "n_cases": manifest_cases.len(),
        "cases": manifest_cases,
    });
    let text = canonical_json(&manifest);
    fs::write(outdir.join("manifest.json"), format!("{text}\n"))
        .map_err(|e| format!("write manifest: {e}"))?;
    Ok(manifest)
}

/// `study run`: build + execute each case through the unchanged `run` path,
/// then evaluate the predeclared comparison. Writes `study_record.json`.
pub fn execute(
    study: &Study,
    study_path: &Path,
    outdir: &Path,
    revocations: Option<&Path>,
) -> Result<Value, String> {
    let manifest = build(study, study_path, outdir, revocations)?;
    let base = study_path.parent().unwrap_or(Path::new("."));
    let cases_dir = outdir.join("cases");
    fs::create_dir_all(&cases_dir).map_err(|e| format!("create {cases_dir:?}: {e}"))?;

    let mut per_case = Vec::new();
    let (mut n_executed, mut n_failed, mut n_gap, mut n_undef) = (0usize, 0usize, 0usize, 0usize);
    for cent in manifest["cases"].as_array().cloned().unwrap_or_default() {
        let id = cent["case_id"].as_str().unwrap_or("?").to_string();
        let cdir = cases_dir.join(&id);
        let _ = fs::create_dir_all(&cdir);
        let started = Instant::now();
        let rec = match study
            .case_spec(&id, base)
            .and_then(|spec| run(&spec, "study").map(|rr| (spec, rr)))
        {
            Ok((_spec, rr)) => {
                let outv = serde_json::to_value(&rr).map_err(|e| format!("serialise {id}: {e}"))?;
                let out_path = cdir.join("out.json");
                let out_text = serde_json::to_string_pretty(&outv).unwrap_or_default();
                let _ = fs::write(&out_path, format!("{out_text}\n"));
                let (per_time, undef) = extract_per_time(&outv, &study.responses);
                n_executed += 1;
                n_undef += undef.len();
                json!({
                    "case_id": id,
                    "status": "executed",
                    "evidence_kind": "fresh",
                    "spec_sha256": cent["spec_sha256"],
                    "out_sha256": sha256_path(&out_path).ok(),
                    "wall_s": started.elapsed().as_secs_f64(),
                    "per_time": per_time,
                    "undefined_responses": undef,
                })
            }
            Err(e) => {
                let gap = e.contains("family_not_qualified")
                    || e.contains("not in the qualified")
                    || e.contains("template_revoked")
                    || e.contains("coverage")
                    || e.contains("not found");
                if gap {
                    n_gap += 1;
                } else {
                    n_failed += 1;
                }
                json!({
                    "case_id": id,
                    "status": if gap { "contract_gap" } else { "failed" },
                    "evidence_kind": "fresh",
                    "spec_sha256": cent["spec_sha256"],
                    "wall_s": started.elapsed().as_secs_f64(),
                    "error": e,
                })
            }
        };
        per_case.push(rec);
    }

    let comparison = study
        .comparison
        .as_ref()
        .map(|c| evaluate_comparison(c, &per_case));

    let population = per_case.len();
    let verdict = if n_executed == population && n_undef == 0 {
        "complete"
    } else if n_executed > 0 {
        "incomplete"
    } else {
        "failed"
    };

    let record = json!({
        "schema": RECORD_SCHEMA,
        "study_id": study.study_id,
        "study_sha256": manifest["study_sha256"],
        "manifest_sha256": sha256_path(&outdir.join("manifest.json")).ok(),
        "tool": {
            "actinv_version": env!("CARGO_PKG_VERSION"),
            "binary_sha256": std::env::current_exe().ok()
                .and_then(|p| sha256_path(&p).ok()),
            "entry_point": "study",
        },
        "population": {
            "declared": population,
            "executed": n_executed,
            "failed": n_failed,
            "contract_gap": n_gap,
            "undefined_response_instances": n_undef,
        },
        "cases": per_case,
        "comparison": comparison,
        "qualification": "unqualified",
        "verdict": verdict,
    });
    fs::write(
        outdir.join("study_record.json"),
        serde_json::to_string_pretty(&record).unwrap_or_default() + "\n",
    )
    .map_err(|e| format!("write study_record: {e}"))?;
    Ok(record)
}

/// Time key: fixed-format seconds so manifest/record keys are stable.
fn tkey(t: f64) -> String {
    if t == t.trunc() && t.abs() < 1e17 {
        format!("{}", t.trunc() as i64)
    } else {
        format!("{t:e}")
    }
}

/// Extract the declared responses per post-shutdown time. Undefined
/// (absent) responses are named in `undefined_responses`, not dropped.
fn extract_per_time(outv: &Value, responses: &[String]) -> (Map<String, Value>, Vec<String>) {
    let mut per_time = Map::new();
    let mut undef = Vec::new();
    let steps = outv["steps"].as_array().cloned().unwrap_or_default();
    let irr_end = steps
        .iter()
        .filter(|st| st["flux"].as_f64().unwrap_or(0.0) > 0.0)
        .filter_map(|st| st["t_s"].as_f64())
        .next_back()
        .unwrap_or(0.0);
    for st in &steps {
        let t = st["t_s"].as_f64().unwrap_or(0.0) - irr_end;
        let mut metrics = Map::new();
        for r in responses {
            let v: Option<Value> = match r.as_str() {
                "total_activity_bq_per_g" => Some(json!(st["activity_Bq_per_g"]
                    .as_object()
                    .map(|m| m.values().filter_map(Value::as_f64).sum::<f64>())
                    .unwrap_or(0.0))),
                "decay_heat_w_per_g" => st["heat_W_per_g"]["total"].as_f64().map(Value::from),
                "photon_source_per_group" => {
                    st["photon_source"]["groups"].as_array().map(|g| json!(g))
                }
                "inventory_per_nuclide" => st["inventory"].as_array().map(|inv| json!(inv)),
                "total_atoms_per_g" => st["total_atoms_per_g"].as_f64().map(Value::from),
                _ => None,
            };
            match v {
                Some(v) => {
                    metrics.insert(r.clone(), v);
                }
                None => undef.push(format!("{r}@{}", tkey(t))),
            }
        }
        per_time.insert(tkey(t), Value::Object(metrics));
    }
    (per_time, undef)
}

/// Scalar view of a response: numbers reduce to themselves; arrays and
/// objects reduce to the sum of their numeric leaves.
fn scalar_of(v: &Value) -> Option<f64> {
    match v {
        Value::Number(n) => n.as_f64(),
        Value::Array(a) => {
            let vals: Vec<f64> = a.iter().filter_map(scalar_of).collect();
            if vals.is_empty() {
                None
            } else {
                Some(vals.iter().sum())
            }
        }
        Value::Object(o) => {
            let vals: Vec<f64> = o.values().filter_map(scalar_of).collect();
            if vals.is_empty() {
                None
            } else {
                Some(vals.iter().sum())
            }
        }
        _ => None,
    }
}

fn case_parts_of(case_id: &str) -> (String, String, String) {
    let mut it = case_id.splitn(3, "__");
    (
        it.next().unwrap_or("").to_string(),
        it.next().unwrap_or("").to_string(),
        it.next().unwrap_or("").to_string(),
    )
}

/// Evaluate the predeclared decision rules. Cases are grouped by the axes
/// NOT in the comparison; within a group the compared axes vary. For each
/// shared time point the group produces a spread `(max-min)/max` and ratio
/// `max/min` over the scalar response. A group containing a failed, gapped
/// or metric-undefined case is counted `undefined`, which leaves the rule
/// `undefined` — preserved, never dropped.
fn evaluate_comparison(c: &Comparison, per_case: &[Value]) -> Value {
    let rules: Vec<Value> = c
        .decision_rules
        .iter()
        .map(|rule| evaluate_rule(rule, c, per_case))
        .collect();
    let count = |v: &str| rules.iter().filter(|r| r["verdict"] == v).count();
    json!({
        "axes": c.axes,
        "rules": rules,
        "verdicts": {
            "pass": count("pass"),
            "fail": count("fail"),
            "undefined": count("undefined"),
        },
    })
}

fn evaluate_rule(rule: &DecisionRule, cmp: &Comparison, per_case: &[Value]) -> Value {
    let axis_index = |a: &str| AXES.iter().position(|x| *x == a);
    // group key = values of the axes not being compared
    let fixed: Vec<usize> = AXES
        .iter()
        .enumerate()
        .filter(|(_, a)| !cmp.axes.contains(&a.to_string()))
        .map(|(i, _)| i)
        .collect();
    type CaseGroup = Vec<(String, Map<String, Value>)>;
    let mut groups: BTreeMap<String, CaseGroup> = BTreeMap::new();
    let mut groups_undefined = 0usize;
    for case in per_case {
        let id = case["case_id"].as_str().unwrap_or("?").to_string();
        let parts = {
            let (m, s, c) = case_parts_of(&id);
            vec![m, s, c]
        };
        let key = fixed
            .iter()
            .map(|&i| format!("{}={}", AXES[i], parts[i]))
            .collect::<Vec<_>>()
            .join(",");
        if case["status"].as_str() != Some("executed")
            || !(case["undefined_responses"]
                .as_array()
                .map(|u| u.is_empty())
                .unwrap_or(true))
        {
            groups_undefined += 1;
            continue;
        }
        let per_time = case["per_time"].as_object().cloned().unwrap_or_default();
        groups.entry(key).or_default().push((id, per_time));
    }
    let axis_i = cmp.axes.first().and_then(|a| axis_index(a));

    // per group: at every shared time key collect (case_id, scalar)
    let mut max_rel = 0.0f64;
    let mut min_rel = f64::INFINITY;
    let mut max_ratio = 1.0f64;
    let mut min_ratio = f64::INFINITY;
    let mut groups_evaluated = 0usize;
    let mut rank_mismatch = false;
    for cases in groups.values() {
        // shared time keys across the group's cases
        let mut shared: Option<Vec<String>> = None;
        for (_id, pt) in cases {
            let keys: Vec<String> = pt.keys().cloned().collect();
            shared = Some(match shared {
                None => keys,
                Some(prev) => prev.into_iter().filter(|k| keys.contains(k)).collect(),
            });
        }
        let times = shared.unwrap_or_default();
        if times.is_empty() {
            groups_undefined += 1;
            continue;
        }
        groups_evaluated += 1;
        for t in &times {
            let vals: Vec<(String, f64)> = cases
                .iter()
                .filter_map(|(id, pt)| {
                    scalar_of(&pt[t][rule.response.as_str()]).map(|v| (id.clone(), v))
                })
                .collect();
            if vals.len() != cases.len() || vals.is_empty() {
                groups_undefined += 1;
                continue;
            }
            let (mn, mx) = vals
                .iter()
                .fold((f64::INFINITY, f64::NEG_INFINITY), |(a, b), (_, x)| {
                    (a.min(*x), b.max(*x))
                });
            let rel = if mx.abs() > 0.0 {
                (mx - mn) / mx.abs()
            } else {
                0.0
            };
            let ratio = if mn > 0.0 { mx / mn } else { f64::INFINITY };
            max_rel = max_rel.max(rel);
            min_rel = min_rel.min(rel);
            max_ratio = max_ratio.max(ratio);
            min_ratio = min_ratio.min(ratio);
            if rule.kind == "rank_equal" {
                if let Some(eo) = &rule.expected_order {
                    let mut order = vals.clone();
                    order
                        .sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
                    let got: Vec<String> = order.into_iter().map(|(id, _)| id).collect();
                    // expected_order lists the compared-axis values in
                    // descending-response order, or full case ids
                    let got_axis: Vec<String> = got
                        .iter()
                        .map(|id| {
                            let (m, s, c) = case_parts_of(id);
                            match axis_i {
                                Some(0) => m,
                                Some(1) => s,
                                _ => c,
                            }
                        })
                        .collect();
                    if *eo != got && *eo != got_axis {
                        rank_mismatch = true;
                    }
                }
            }
        }
    }
    let any_undef = groups_undefined > 0;
    let (verdict, detail) = match rule.kind.as_str() {
        "within_rel" | "max_rel" => {
            let b = rule.bound.unwrap_or(f64::MAX);
            let v = if groups_evaluated == 0 || any_undef {
                "undefined"
            } else if max_rel <= b {
                "pass"
            } else {
                "fail"
            };
            (v, json!({"max_rel": max_rel, "bound": b}))
        }
        "min_rel" => {
            let b = rule.bound.unwrap_or(0.0);
            let mr = if min_rel.is_infinite() { 0.0 } else { min_rel };
            let v = if groups_evaluated == 0 || any_undef {
                "undefined"
            } else if mr >= b {
                "pass"
            } else {
                "fail"
            };
            (v, json!({"min_rel": mr, "bound": b}))
        }
        "ratio_band" => {
            let band = rule.band.unwrap_or([0.0, f64::MAX]);
            let lo = if min_ratio.is_infinite() {
                1.0
            } else {
                min_ratio
            };
            let v = if groups_evaluated == 0 || any_undef {
                "undefined"
            } else if lo >= band[0] && max_ratio <= band[1] {
                "pass"
            } else {
                "fail"
            };
            (
                v,
                json!({"min_ratio": lo, "max_ratio": max_ratio, "band": band}),
            )
        }
        "rank_equal" => {
            let v = if groups_evaluated == 0 || any_undef {
                "undefined"
            } else if rank_mismatch {
                "fail"
            } else {
                "pass"
            };
            (v, json!({}))
        }
        _ => ("undefined", json!({})),
    };
    json!({
        "id": rule.id,
        "kind": rule.kind,
        "response": rule.response,
        "verdict": verdict,
        "groups_evaluated": groups_evaluated,
        "groups_undefined": groups_undefined,
        "detail": detail,
    })
}

/// The output directory a `study` invocation resolves to.
pub fn outdir_for(study_path: &Path, outdir: Option<&str>) -> PathBuf {
    outdir
        .map(PathBuf::from)
        .unwrap_or_else(|| study_path.with_extension("study-out"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn study_json() -> Value {
        json!({
            "study": "actinv-study-1",
            "study_id": "t",
            "library": {"path": "lib.npz"},
            "decay": {"primary": "d.dat"},
            "cases": {
                "materials": [
                    {"name": "a", "composition": {"Fe": 100.0}},
                    {"name": "b", "composition": {"Fe": 99.0, "Co": 1.0}}
                ],
                "spectra": [
                    {"name": "s1", "flux_per_group": [1.0, 2.0], "descending": true},
                    {"name": "s2", "flux_per_group": [3.0]}
                ],
                "schedules": [
                    {"name": "p", "steps": [{"dt": "5 s", "flux": 1.0}]}
                ]
            },
            "cooling_times_s": [0.0, 10.0, 20.0],
            "responses": ["total_activity_bq_per_g", "total_atoms_per_g"]
        })
    }

    #[test]
    fn expansion_order_and_cooling_steps() {
        let s: Study = serde_json::from_value(study_json()).unwrap();
        s.validate().unwrap();
        assert_eq!(
            s.case_ids(),
            vec!["a__s1__p", "a__s2__p", "b__s1__p", "b__s2__p"]
        );
        let spec = s.case_spec("a__s1__p", Path::new(".")).unwrap();
        let fluxes: Vec<f64> = spec.schedule.iter().map(|st| st.flux).collect();
        assert_eq!(fluxes, vec![1.0, 0.0, 0.0]);
        // cooling deltas: 10 s then another 10 s to reach t=20
        let dts: Vec<String> = spec.schedule.iter().map(|st| st.dt.clone()).collect();
        assert_eq!(dts[1], "10 s");
        assert_eq!(dts[2], "10 s");
        assert_eq!(spec.spectrum.flux_per_group, vec![1.0, 2.0]);
        assert!(spec.spectrum.descending);
    }

    #[test]
    fn version_and_unknown_fields_are_refused() {
        let mut v = study_json();
        v["study"] = json!("actinv-study-9");
        let e = Study::from_json(&v.to_string()).unwrap_err();
        assert!(e.contains("unsupported study version"));
        let mut v2 = study_json();
        v2["surprise"] = json!(1);
        let e2 = Study::from_json(&v2.to_string()).unwrap_err();
        assert!(e2.contains("study parse"));
    }

    #[test]
    fn unqualified_families_are_named() {
        for (field, phase) in [
            ("robustness", "P30"),
            ("refinement", "P29"),
            ("spatial_handoff", "P32"),
        ] {
            let mut v = study_json();
            v[field] = json!({});
            let e = Study::from_json(&v.to_string()).unwrap_err();
            assert!(e.contains("family_not_qualified"), "{e}");
            assert!(e.contains(phase), "{e}");
        }
    }

    #[test]
    fn spectrum_form_exactly_one() {
        let mut v = study_json();
        v["cases"]["spectra"][0]["flux_file"] = json!("x.flux");
        let e = Study::from_json(&v.to_string()).unwrap_err();
        assert!(e.contains("exactly one"));
    }

    #[test]
    fn duplicate_axis_names_are_refused() {
        for axis in ["materials", "spectra", "schedules"] {
            let mut v = study_json();
            let dup = v["cases"][axis][0].clone();
            v["cases"][axis].as_array_mut().unwrap().push(dup);
            let e = Study::from_json(&v.to_string()).unwrap_err();
            assert!(e.contains("duplicate_"), "{axis}: {e}");
        }
    }

    #[test]
    fn unqualified_response_and_rule_kinds_refused() {
        let mut v = study_json();
        v["responses"] = json!(["magic_w_per_g"]);
        let e = Study::from_json(&v.to_string()).unwrap_err();
        assert!(e.contains("not in the qualified set"));
        let mut v2 = study_json();
        v2["comparison"] = json!({
            "axes": ["material"],
            "decision_rules": [{"id": "r", "kind": "teleport",
                               "response": "total_activity_bq_per_g"}]
        });
        let e2 = Study::from_json(&v2.to_string()).unwrap_err();
        assert!(e2.contains("not in"));
    }

    #[test]
    fn study_too_large_is_named() {
        let mut v = study_json();
        v["cases"]["materials"] = json!((0..64)
            .map(|i| json!({
                "name": format!("m{i}"),
                "composition": {"Fe": 100.0}
            }))
            .collect::<Vec<_>>());
        v["cases"]["spectra"] = json!((0..17)
            .map(|i| json!({
                "name": format!("s{i}"),
                "flux_per_group": [1.0]
            }))
            .collect::<Vec<_>>());
        let e = Study::from_json(&v.to_string()).unwrap_err();
        assert!(e.contains("study_too_large"), "{e}");
    }

    #[test]
    fn comparison_counts_undefined_groups() {
        // one failed case leaves the containing group undefined -> the rule
        // verdict is undefined, and the group is counted, not dropped
        let s: Study = serde_json::from_value(study_json()).unwrap();
        let mut per_case = Vec::new();
        for (i, id) in s.case_ids().iter().enumerate() {
            let mut pt = Map::new();
            pt.insert(
                "0".to_string(),
                json!({"total_activity_bq_per_g": if i % 2 == 0 {2.0} else {1.0}}),
            );
            per_case.push(json!({
                "case_id": id,
                "status": if i == 3 {"failed"} else {"executed"},
                "undefined_responses": [],
                "per_time": pt,
            }));
        }
        let cmp = Comparison {
            axes: vec!["spectrum".into()],
            decision_rules: vec![DecisionRule {
                id: "r".into(),
                kind: "within_rel".into(),
                response: "total_activity_bq_per_g".into(),
                bound: Some(0.9),
                band: None,
                expected_order: None,
            }],
        };
        let out = evaluate_comparison(&cmp, &per_case);
        assert_eq!(out["verdicts"]["undefined"], json!(1));
    }

    #[test]
    fn canonical_json_is_key_stable() {
        let a = canonical_json(&json!({"b": 1, "a": [2.0, {"z": null}]}));
        assert_eq!(a, r#"{"a":[2.0,{"z":null}],"b":1}"#);
    }
}
