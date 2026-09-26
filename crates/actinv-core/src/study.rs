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
use crate::run::{run, PreparedRun, RunResult};
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
/// The qualified responses carrying one value per time point. Robustness
/// statistics and comparison rules are defined only on these; the other two
/// qualified responses are per-group and per-nuclide arrays.
pub const SCALAR_RESPONSES: &[&str] = &[
    "total_activity_bq_per_g",
    "decay_heat_w_per_g",
    "total_atoms_per_g",
];
/// Per-case robustness draw cap: every draw is a full solve plus two files,
/// and each enabled channel is drawn again for attribution. Named refusal.
pub const MAX_ROBUSTNESS_SAMPLES: u32 = 4096;

pub const AXES: &[&str] = &["material", "spectrum", "schedule"];
pub const RULE_KINDS: &[&str] = &[
    "within_rel",
    "max_rel",
    "min_rel",
    "ratio_band",
    "rank_equal",
];

const UNQUALIFIED_FAMILIES: &[(&str, &str, &str)] = &[("spatial_handoff", "ACT-SOURCE-01", "P32")];

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
    /// ACT-REFINE-01 (qualified by P29): per-(response, time) numerical
    /// criteria discharged against a reference solve with per-component
    /// error accounting.
    #[serde(default)]
    pub refinement: Option<Refinement>,
    /// ACT-ROBUST-01 (qualified by P30): nonlinear input sampling against
    /// the nominal case — correlated MF=33 cross-section draws, flux
    /// normalization and composition channels.
    #[serde(default)]
    pub robustness: Option<Robustness>,
    // Families not yet qualified: accepted by the schema, refused at
    // validation with `family_not_qualified` naming the delivering phase.
    #[serde(default)]
    pub spatial_handoff: Option<Value>,
}

/// Sampling channels for ACT-ROBUST-01.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RobustChannels {
    /// correlated draws on the spectrum-collapsed MF=33 covariance over
    /// active library rows (requires `robustness.covariance`).
    #[serde(default)]
    pub cross_section_mf33: bool,
    /// relative std on the total flux normalization, applied to every
    /// schedule step; draws are mean-preserving lognormal factors
    /// (positive by construction).
    #[serde(default)]
    pub flux_rel_std: f64,
    /// per-element relative stds on the wt_percent composition; draws are
    /// mean-preserving lognormal factors renormalized to the declared
    /// total.
    #[serde(default)]
    pub composition_rel_std: BTreeMap<String, f64>,
    /// mean-preserving lognormal draws on every radioactive nuclide's
    /// decay constant; each nuclide's relative sigma is its declared
    /// `d_half_life / half_life` from the decay archives (P43). A
    /// radioactive nuclide with no declared uncertainty carries factor
    /// 1.0 and is reported as an uncovered input.
    #[serde(default)]
    pub decay_constants: bool,
    /// mean-preserving lognormal draws on the effective independent
    /// fission yields of every material declaring `fission_yields`;
    /// each pair's relative sigma is its declared `sigma_yield / yield`
    /// (P43). A pair with no declared uncertainty — or a zero yield —
    /// carries factor 1.0 and is reported as an uncovered input.
    #[serde(default)]
    pub fission_yields: bool,
}

/// ACT-ROBUST-01 block: sample count, seed, channels, responses.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Robustness {
    /// number of perturbed solves per case (>= 2)
    pub samples: u32,
    /// fixed PRNG seed; repeatability only, not convergence evidence
    pub seed: u64,
    #[serde(default)]
    pub channels: RobustChannels,
    /// covariance sidecar {path, sha256} for cross_section_mf33
    #[serde(default)]
    pub covariance: Option<LibraryRef>,
    /// qualified responses collected from every sample
    pub responses: Vec<String>,
    /// optional cap on total perturbed runs per case
    #[serde(default)]
    pub resource_limit_runs: Option<u32>,
    /// run the first-order local propagation comparison after sampling
    /// (P11 vs sampled spread). Its tangent system scales with the
    /// chain's active reaction rows, so on wide fission chains the
    /// comparison can cost more than the whole campaign; campaigns may
    /// switch it off — the mechanics gate still demonstrates it.
    #[serde(default = "default_true")]
    pub first_order_comparison: bool,
}

/// One user-declared numerical criterion on a response at a cooling time.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Criterion {
    /// one of QUALIFIED_RESPONSES
    pub response: String,
    /// cooling time (s) the criterion applies to; 0 = shutdown
    pub time_s: f64,
    /// relative bound; applied when |reference| >= abs scale
    #[serde(default)]
    pub rel: Option<f64>,
    /// absolute bound; applied when |reference| < abs scale
    #[serde(default)]
    pub abs: Option<f64>,
}

/// ACT-REFINE-01 block: criteria + a declared escalation resource limit.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Refinement {
    pub criteria: Vec<Criterion>,
    /// maximum escalation ladder steps per case (P29 resource limit)
    #[serde(default = "default_resource_limit")]
    pub resource_limit_runs: u32,
}

fn default_true() -> bool {
    true
}
fn default_resource_limit() -> u32 {
    4
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
    /// per-material fission-yield declaration, propagated verbatim into
    /// the case spec (P43). Non-fissile materials leave it absent.
    #[serde(default)]
    pub fission_yields: crate::spec::FissionYieldOptions,
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
    /// optional restriction to declared cooling times (seconds); when
    /// absent the rule evaluates at every shared time key
    #[serde(default)]
    pub times_s: Option<Vec<f64>>,
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
        if self.spatial_handoff.is_some() {
            return Err(family_err("spatial_handoff"));
        }
        if let Some(rf) = &self.refinement {
            if rf.criteria.is_empty() {
                return Err("refinement.criteria must be non-empty".into());
            }
        }
        if let Some(rb) = &self.robustness {
            if rb.samples < 2 {
                return Err("robustness.samples must be >= 2".into());
            }
            if rb.samples > MAX_ROBUSTNESS_SAMPLES {
                return Err(format!(
                    "robustness_too_large: {} samples exceeds the \
                     {MAX_ROBUSTNESS_SAMPLES} cap",
                    rb.samples
                ));
            }
            if rb.responses.is_empty() {
                return Err("robustness.responses must be non-empty".into());
            }
            for response in &rb.responses {
                if !QUALIFIED_RESPONSES.contains(&response.as_str()) {
                    return Err(format!(
                        "robustness response '{response}' is not qualified \
                         (family_not_qualified)"
                    ));
                }
                if !SCALAR_RESPONSES.contains(&response.as_str()) {
                    return Err(format!(
                        "robustness response '{response}' is array-valued; \
                         sample statistics are defined for {SCALAR_RESPONSES:?}"
                    ));
                }
            }
            let ch = &rb.channels;
            if !ch.flux_rel_std.is_finite() || ch.flux_rel_std < 0.0 {
                return Err("robustness.channels.flux_rel_std must be finite and \
                     nonnegative"
                    .into());
            }
            for (element, std) in &ch.composition_rel_std {
                if !std.is_finite() || *std < 0.0 {
                    return Err(format!(
                        "robustness composition std for '{element}' must be \
                         finite and nonnegative"
                    ));
                }
            }
            if ch.cross_section_mf33
                && rb
                    .covariance
                    .as_ref()
                    .map(|c| c.sha256.is_none())
                    .unwrap_or(true)
            {
                return Err("robustness.channels.cross_section_mf33 requires \
                     robustness.covariance {path, sha256}"
                    .into());
            }
            if ch.fission_yields
                && !self
                    .cases
                    .materials
                    .iter()
                    .any(|m| !m.fission_yields.files.is_empty())
            {
                return Err("robustness.channels.fission_yields requires at least one \
                     material declaring fission_yields.files"
                    .into());
            }
            if !ch.cross_section_mf33
                && ch.flux_rel_std == 0.0
                && ch.composition_rel_std.is_empty()
                && !ch.decay_constants
                && !ch.fission_yields
            {
                return Err("robustness enables no channel".into());
            }
        }
        if let Some(rf) = &self.refinement {
            for c in &rf.criteria {
                if !QUALIFIED_RESPONSES.contains(&c.response.as_str()) {
                    return Err(format!(
                        "refinement criterion response '{}' is not \
                         qualified (family_not_qualified)",
                        c.response
                    ));
                }
                if !c.time_s.is_finite() || c.time_s < 0.0 {
                    return Err(format!(
                        "refinement criterion time_s {} must be finite \
                         >= 0",
                        c.time_s
                    ));
                }
                if c.rel.is_none() && c.abs.is_none() {
                    return Err(format!(
                        "refinement criterion {}@{} requires rel or abs",
                        c.response, c.time_s
                    ));
                }
                for (kind, b) in [("rel", c.rel), ("abs", c.abs)] {
                    if let Some(b) = b {
                        if !(b.is_finite() && b >= 0.0) {
                            return Err(format!(
                                "refinement criterion {kind} bound must \
                                 be finite >= 0"
                            ));
                        }
                    }
                }
            }
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
            (
                "material",
                self.cases
                    .materials
                    .iter()
                    .map(|x| x.name.as_str())
                    .collect::<Vec<_>>(),
            ),
            (
                "spectrum",
                self.cases.spectra.iter().map(|x| x.name.as_str()).collect(),
            ),
            (
                "schedule",
                self.cases
                    .schedules
                    .iter()
                    .map(|x| x.name.as_str())
                    .collect(),
            ),
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
                // The study schema's step object has no spectrum; the shared
                // spec Step type would otherwise accept one silently.
                if step.spectrum.is_some() {
                    return Err(format!(
                        "schedule '{}': per-step spectrum is not part of \
                         actinv-study-1",
                        s.name
                    ));
                }
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
                    ids.push(
                        [m.name.as_str(), s.name.as_str(), c.name.as_str()].join(CASE_ID_SEPARATOR),
                    );
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
                    spectrum: None,
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
            fission_yields: m.fission_yields.clone(),
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
        let (mn, sn, cn) = split_case_id(case_id);
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
    if !SCALAR_RESPONSES.contains(&r.response.as_str()) {
        return Err(format!(
            "decision rule '{}' response '{}' is array-valued; comparisons \
             use a scalar response {SCALAR_RESPONSES:?}",
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
    if let Some(ts) = &r.times_s {
        if ts.is_empty() || ts.iter().any(|t| !(t.is_finite() && *t >= 0.0)) {
            return Err(format!(
                "rule '{}' times_s must be nonempty and nonnegative",
                r.id
            ));
        }
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
    // `__` joins the three names into a case id; allowing it in a name makes
    // the split ambiguous (two cases could share one id and one spec file).
    if name.contains(CASE_ID_SEPARATOR) {
        return Err(format!(
            "{kind} name '{name}' must not contain '{CASE_ID_SEPARATOR}', the \
             case-id separator"
        ));
    }
    Ok(())
}

const CASE_ID_SEPARATOR: &str = "__";

/// Split a case id into its (material, spectrum, schedule) names.
fn split_case_id(case_id: &str) -> (&str, &str, &str) {
    let mut it = case_id.splitn(3, CASE_ID_SEPARATOR);
    (
        it.next().unwrap_or(""),
        it.next().unwrap_or(""),
        it.next().unwrap_or(""),
    )
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

    // Resume state: a prior record is authority only where its per-case
    // digests re-verify against the artifacts on disk.
    let prior_record: Option<Value> = fs::read_to_string(outdir.join("study_record.json"))
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok());
    let prior_cases: Map<String, Value> = prior_record
        .as_ref()
        .and_then(|r| r["cases"].as_array().cloned())
        .unwrap_or_default()
        .into_iter()
        .filter_map(|c| c["case_id"].as_str().map(|id| (id.to_string(), c.clone())))
        .collect();

    let mut prep = PreparedCache::default();
    let mut resumed_cases: Vec<String> = Vec::new();
    let record_path = outdir.join("study_record.json");
    let study_sha = manifest["study_sha256"].clone();
    let manifest_sha = sha256_path(&outdir.join("manifest.json")).ok();
    let robustness_sha = study
        .robustness
        .as_ref()
        .map(|rb| robustness_config_sha(study, rb));

    let mut per_case = Vec::new();
    let (mut n_executed, mut n_failed, mut n_gap, mut n_undef) = (0usize, 0usize, 0usize, 0usize);
    for cent in manifest["cases"].as_array().cloned().unwrap_or_default() {
        let id = cent["case_id"].as_str().unwrap_or("?").to_string();
        let cdir = cases_dir.join(&id);
        let _ = fs::create_dir_all(&cdir);
        let started = Instant::now();
        // resume: reuse a prior case record only when the recorded
        // spec/out digests re-verify against the current artifacts
        if let Some(prev) = prior_cases.get(&id) {
            if case_resumable(prev, &cent, &cdir, robustness_sha.as_deref()) {
                let mut rec = prev.clone();
                rec["evidence_kind"] = json!("resumed");
                resumed_cases.push(id.clone());
                match prev["status"].as_str() {
                    Some("executed") => {
                        n_executed += 1;
                        n_undef += prev["undefined_responses"]
                            .as_array()
                            .map(|u| u.len())
                            .unwrap_or(0);
                    }
                    Some("contract_gap") => n_gap += 1,
                    _ => n_failed += 1,
                }
                rec["wall_s"] = json!(started.elapsed().as_secs_f64());
                per_case.push(rec);
                write_partial_record(
                    &record_path,
                    study,
                    &study_sha,
                    &manifest_sha,
                    &per_case,
                    &resumed_cases,
                    prep.count,
                );
                continue;
            }
        }
        let rec = match study
            .case_spec(&id, base)
            .and_then(|spec| prep.run_prepared(&spec).map(|rr| (spec, rr)))
        {
            Ok((spec, rr)) => {
                let outv = serde_json::to_value(&rr).map_err(|e| format!("serialise {id}: {e}"))?;
                let out_path = cdir.join("out.json");
                let out_text = serde_json::to_string_pretty(&outv).unwrap_or_default();
                let _ = fs::write(&out_path, format!("{out_text}\n"));
                let (per_time, undef) = extract_per_time(&outv, &study.responses);
                n_executed += 1;
                n_undef += undef.len();
                let mut rec = json!({
                    "case_id": id,
                    "status": "executed",
                    "evidence_kind": "fresh",
                    "spec_sha256": cent["spec_sha256"],
                    "out_sha256": sha256_path(&out_path).ok(),
                    "wall_s": started.elapsed().as_secs_f64(),
                    "per_time": per_time,
                    "undefined_responses": undef,
                });
                if let Some(rf) = &study.refinement {
                    let rr2 = evaluate_refinement(rf, &spec, &outv, &cdir);
                    rec["refinement"] = rr2;
                }
                if let Some(rb) = &study.robustness {
                    rec["robustness"] =
                        evaluate_robustness(study, rb, &spec, &outv, base, &cdir, &mut prep);
                }
                rec
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
        write_partial_record(
            &record_path,
            study,
            &study_sha,
            &manifest_sha,
            &per_case,
            &resumed_cases,
            prep.count,
        );
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
        "status": "complete",
        "population": {
            "declared": population,
            "executed": n_executed,
            "failed": n_failed,
            "contract_gap": n_gap,
            "undefined_response_instances": n_undef,
        },
        "cases": per_case,
        "resumed_cases": resumed_cases,
        "prepared_runs": prep.count,
        "prepare_wall_s": prep.wall_s,
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

/// Absolute scale below which a criterion applies its `abs` bound
/// (atoms/Bq/W per gram). Matches the P29 G0 seal.
const ABS_SCALE: f64 = 1e-6;

/// Keyed numeric view of one response value, so arrays compare element by
/// element. A scalar is a single entry. `inventory_per_nuclide` keys atoms/g
/// by nuclide name: pruning changes which nuclides are present, so position
/// is not identity. `photon_source_per_group` keys photons/s/g by group
/// index: the group structure is fixed across the variants of one case.
/// Returns `(is_array, values)`; any other shape is not a qualified response.
fn keyed_response(v: &Value) -> Option<(bool, BTreeMap<String, f64>)> {
    match v {
        Value::Number(n) => Some((false, BTreeMap::from([(String::new(), n.as_f64()?)]))),
        Value::Array(items) => {
            let mut values = BTreeMap::new();
            for (index, item) in items.iter().enumerate() {
                let (key, value) = if let Some(name) = item.get("nuclide").and_then(Value::as_str) {
                    (name.to_string(), item.get("atoms_per_g")?.as_f64()?)
                } else if let Some(rate) = item.get("photons_s_g") {
                    (format!("{index:08}"), rate.as_f64()?)
                } else {
                    (format!("{index:08}"), item.as_f64()?)
                };
                if values.insert(key, value).is_some() {
                    return None;
                }
            }
            Some((true, values))
        }
        _ => None,
    }
}

/// One number per response value: the scalar itself, or the array total
/// (atoms/g over nuclides, photons/s/g over groups).
fn response_total(v: &Value) -> Option<f64> {
    keyed_response(v).map(|(_, values)| values.values().sum())
}

/// Maximum relative difference between two values of one response: scalars
/// compare directly; arrays compare element by element over the union of
/// keys (see `keyed_response`), a missing element counting as 0.
fn max_rel_diff(a: &Value, b: &Value) -> Option<f64> {
    let (a_is_array, x) = keyed_response(a)?;
    let (b_is_array, y) = keyed_response(b)?;
    if a_is_array != b_is_array {
        return None;
    }
    let mut m = 0.0f64;
    for key in x.keys().chain(y.keys()) {
        let xa = x.get(key).copied().unwrap_or(0.0);
        let xb = y.get(key).copied().unwrap_or(0.0);
        if xa == 0.0 && xb == 0.0 {
            continue;
        }
        m = m.max((xa - xb).abs() / xa.abs().max(xb.abs()));
    }
    Some(m)
}

/// Response value at a cooling time, extracted from a run output Value.
fn response_at(outv: &Value, response: &str, time_s: f64) -> Option<Value> {
    let (per_time, _) = extract_per_time(outv, &[response.to_string()]);
    per_time
        .get(&tkey(time_s))
        .and_then(|m| m.get(response))
        .cloned()
}

/// ACT-REFINE-01 discharge: re-solve the case at reference settings
/// (prune none, bmin 0, cram 48, coupled), isolate the solver and pruning
/// components with two intermediate variants, then evaluate each
/// criterion. Escalation strengthens the declared spec one ladder step at
/// a time until the criterion is satisfied or `resource_limit_runs` is
/// exhausted.
fn evaluate_refinement(rf: &Refinement, spec: &Spec, declared_out: &Value, cdir: &Path) -> Value {
    let mut variant_specs: Vec<(&str, Spec)> = Vec::new();
    let mut v_solver = spec.clone();
    v_solver.options.cram_order = 48;
    variant_specs.push(("cram48_variant", v_solver));
    let mut v_ref = spec.clone();
    v_ref.options.prune = "none".into();
    v_ref.options.bmin_atoms_per_g = 0.0;
    v_ref.options.cram_order = 48;
    v_ref.options.mode = "coupled".into();
    variant_specs.push(("reference", v_ref));

    let mut outs: Vec<(String, Value)> = Vec::new();
    let mut runs_used = 1u32; // the declared run
    let mut reference_err = None;
    for (label, vs) in &variant_specs {
        match run(vs, "study") {
            Ok(rr) => {
                let outv = serde_json::to_value(&rr).unwrap_or_default();
                let _ = fs::write(
                    cdir.join(format!("ref_{label}.json")),
                    serde_json::to_string_pretty(&outv).unwrap_or_default(),
                );
                outs.push((label.to_string(), outv));
            }
            Err(e) => {
                if *label == "reference" {
                    reference_err = Some(e.clone());
                }
            }
        }
        runs_used += 1;
    }
    let solver_out = outs.iter().find(|(l, _)| l == "cram48_variant");
    let reference_out = outs.iter().find(|(l, _)| l == "reference");

    // Escalation ladder applied to the declared spec when a criterion is
    // unmet: [+bmin 0, +prune none, +cram 48, +mode coupled].
    type Ladder = Vec<(&'static str, Box<dyn Fn(&mut Spec)>)>;
    let ladder: Ladder = vec![
        (
            "bmin0",
            Box::new(|s: &mut Spec| s.options.bmin_atoms_per_g = 0.0),
        ),
        (
            "prune_none",
            Box::new(|s: &mut Spec| s.options.prune = "none".into()),
        ),
        ("cram48", Box::new(|s: &mut Spec| s.options.cram_order = 48)),
        (
            "coupled",
            Box::new(|s: &mut Spec| s.options.mode = "coupled".into()),
        ),
    ];

    let mut criteria = Vec::new();
    for c in &rf.criteria {
        let declared_v = response_at(declared_out, &c.response, c.time_s);
        let (mut best_diff, mut verdict, mut esc_runs) = (f64::INFINITY, "unestablished", 0u32);
        let mut initial_diff = f64::INFINITY;
        let mut components = json!({});
        if reference_err.is_none() {
            if let (Some(dv), Some(rv)) = (&declared_v, reference_out.map(|(_, o)| o)) {
                let r_ref = response_at(rv, &c.response, c.time_s);
                if let Some(rv_) = r_ref {
                    if let Some(d) = max_rel_diff(dv, &rv_) {
                        best_diff = d;
                        initial_diff = d;
                        verdict = criterion_status(&rv_, d, c);
                        // per-component empirical estimates
                        if let Some((_, sv)) = solver_out {
                            if let Some(sv_) = response_at(sv, &c.response, c.time_s) {
                                if let Some(ds) = max_rel_diff(dv, &sv_) {
                                    components["solver_time_integration"] = json!({"estimate": ds,
                                               "class": "empirically_estimated"});
                                }
                                if let Some(dp) = max_rel_diff(&sv_, &rv_) {
                                    components["population_pruning_and_mode"] = json!({"estimate": dp,
                                               "class": "empirically_estimated"});
                                }
                            }
                        }
                        components["processing_collapse"] = json!({
                            "bound": 1e-6,
                            "class": "bounded",
                            "basis": "P25c/P28 measured tolerances"});
                        components["total_empirical"] = json!({"estimate": d});
                    }
                    // escalate while unmet, within the resource limit
                    let mut esc_spec = spec.clone();
                    while verdict == "unmet"
                        && esc_runs < rf.resource_limit_runs
                        && (esc_runs as usize) < ladder.len()
                    {
                        ladder[esc_runs as usize].1(&mut esc_spec);
                        esc_runs += 1;
                        runs_used += 1;
                        match run(&esc_spec, "study") {
                            Ok(rr) => {
                                let ov = serde_json::to_value(&rr).unwrap_or_default();
                                let _ = fs::write(
                                    cdir.join(format!(
                                        "esc_{}_{}_{}.json",
                                        c.response,
                                        tkey(c.time_s),
                                        esc_runs
                                    )),
                                    serde_json::to_string_pretty(&ov).unwrap_or_default(),
                                );
                                if let (Some(ea), Some(rb)) = (
                                    response_at(&ov, &c.response, c.time_s),
                                    response_at(rv, &c.response, c.time_s),
                                ) {
                                    if let Some(d) = max_rel_diff(&ea, &rb) {
                                        best_diff = d;
                                        verdict = criterion_status(&rb, d, c);
                                    }
                                }
                            }
                            Err(_) => break,
                        }
                    }
                }
            }
        }
        criteria.push(json!({
            "response": c.response,
            "time_s": c.time_s,
            "rel": c.rel, "abs": c.abs,
            "initial_rel_diff": (initial_diff.is_finite())
                .then_some(initial_diff),
            "observed_rel_diff": (best_diff.is_finite()).then_some(best_diff),
            "verdict": verdict,
            "escalation_runs": esc_runs,
            "resource_limit_reached": verdict == "unmet"
                && esc_runs >= rf.resource_limit_runs,
            "components": components,
            "reference_error": reference_err,
        }));
    }
    let n_sat = criteria
        .iter()
        .filter(|c| c["verdict"] == "satisfied")
        .count();
    let n_unmet = criteria.iter().filter(|c| c["verdict"] == "unmet").count();
    let n_unest = criteria
        .iter()
        .filter(|c| c["verdict"] == "unestablished")
        .count();
    json!({
        "criteria": criteria,
        "runs_used": runs_used,
        "satisfied": n_sat, "unmet": n_unmet, "unestablished": n_unest,
        "verdict": if n_unest > 0 { "unestablished" }
                   else if n_unmet > 0 { "unmet" }
                   else { "satisfied" },
    })
}

/// A criterion verdict: `abs` applies when |reference| < ABS_SCALE and is
/// declared; `rel` applies otherwise. If no applicable bound exists the
/// criterion is `unestablished`, never a silent pass.
fn criterion_status(reference: &Value, diff: f64, c: &Criterion) -> &'static str {
    let near_zero = response_total(reference)
        .map(|v| v.abs() < ABS_SCALE)
        .unwrap_or(false);
    if near_zero {
        match c.abs {
            Some(a) if diff <= a.max(1e-300) => "satisfied",
            Some(_) => "unmet",
            None => "unestablished",
        }
    } else {
        match c.rel {
            Some(r) if diff <= r => "satisfied",
            Some(_) => "unmet",
            None => "unestablished",
        }
    }
}

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

fn case_parts_of(case_id: &str) -> (String, String, String) {
    let (m, s, c) = split_case_id(case_id);
    (m.to_string(), s.to_string(), c.to_string())
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
    // per-group shared time keys, retained for paired-sample survival
    let mut group_times: Vec<(String, Vec<String>)> = Vec::new();
    for (gkey, cases) in &groups {
        // shared time keys across the group's cases
        let mut shared: Option<Vec<String>> = None;
        for (_id, pt) in cases {
            let keys: Vec<String> = pt.keys().cloned().collect();
            shared = Some(match shared {
                None => keys,
                Some(prev) => prev.into_iter().filter(|k| keys.contains(k)).collect(),
            });
        }
        let mut times = shared.unwrap_or_default();
        if let Some(scope) = &rule.times_s {
            times.retain(|k| {
                k.parse::<f64>()
                    .map(|v| scope.contains(&v))
                    .unwrap_or(false)
            });
        }
        if times.is_empty() {
            groups_undefined += 1;
            continue;
        }
        group_times.push((gkey.clone(), times.clone()));
        groups_evaluated += 1;
        for t in &times {
            let vals: Vec<(String, f64)> = cases
                .iter()
                .filter_map(|(id, pt)| {
                    pt[t][rule.response.as_str()]
                        .as_f64()
                        .map(|v| (id.clone(), v))
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
    // ---- paired-sample survival (P43) ------------------------------
    // When every case in the evaluated groups carries index-aligned
    // robustness sample values (common random numbers: index i is the
    // same draw set in every case), the rule's nominal predicate is
    // re-evaluated per paired draw; the fraction of paired samples in
    // which the rule's nominal verdict survives is reported per rule.
    let sample_sets: BTreeMap<String, Vec<Value>> = per_case
        .iter()
        .filter_map(|case| {
            let id = case["case_id"].as_str()?;
            let sv = case["robustness"]["sample_values"].as_array()?.clone();
            Some((id.to_string(), sv))
        })
        .collect();
    let survival = {
        let evaluated: Vec<(&String, &CaseGroup)> = group_times
            .iter()
            .map(|(gkey, _)| (gkey, &groups[gkey]))
            .collect();
        let ready = !evaluated.is_empty()
            && evaluated
                .iter()
                .all(|(_, cases)| cases.iter().all(|(id, _)| sample_sets.contains_key(id)));
        if !ready {
            Value::Null
        } else {
            let n_paired = evaluated
                .iter()
                .flat_map(|(_, cases)| cases.iter().map(|(id, _)| sample_sets[id].len()))
                .min()
                .unwrap_or(0);
            let mut paired = 0usize;
            let mut satisfied = 0usize;
            let mut failed = 0usize;
            // i indexes the per-case rows of sample_sets (a map of vectors);
            // no single container exists to enumerate over.
            #[allow(clippy::needless_range_loop)]
            for i in 0..n_paired {
                let any_missing = evaluated
                    .iter()
                    .any(|(_, cases)| cases.iter().any(|(id, _)| sample_sets[id][i].is_null()));
                if any_missing {
                    failed += 1;
                    continue;
                }
                paired += 1;
                let mut ok = true;
                'groups: for (gkey, cases) in &evaluated {
                    let times = group_times
                        .iter()
                        .find(|(k, _)| k == *gkey)
                        .map(|(_, t)| t.as_slice())
                        .unwrap_or_default();
                    for t in times {
                        let vals: Vec<(String, f64)> = cases
                            .iter()
                            .filter_map(|(id, _)| {
                                sample_sets[id][i][t][rule.response.as_str()]
                                    .as_f64()
                                    .map(|v| (id.clone(), v))
                            })
                            .collect();
                        if vals.len() != cases.len() || !rule_predicate(rule, &vals, axis_i) {
                            ok = false;
                            break 'groups;
                        }
                    }
                }
                if ok {
                    satisfied += 1;
                }
            }
            json!({
                "paired_samples": paired,
                "failed_samples": failed,
                "satisfied": satisfied,
                "fraction_satisfied": if paired > 0 {
                    json!(satisfied as f64 / paired as f64)
                } else {
                    Value::Null
                },
                "semantics": "fraction of paired common-random-number draws in which the rule's nominal predicate holds over every evaluated group and time",
            })
        }
    };
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
        "survival": survival,
    })
}

/// The per-(group, time) boolean inside a decision rule's nominal
/// aggregation — reused verbatim for paired-sample survival evaluation
/// (P43), so a surviving draw satisfies exactly the nominal predicate.
fn rule_predicate(rule: &DecisionRule, vals: &[(String, f64)], axis_i: Option<usize>) -> bool {
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
    match rule.kind.as_str() {
        "within_rel" | "max_rel" => rel <= rule.bound.unwrap_or(f64::MAX),
        "min_rel" => rel >= rule.bound.unwrap_or(0.0),
        "ratio_band" => {
            let band = rule.band.unwrap_or([0.0, f64::MAX]);
            let ratio = if mn > 0.0 { mx / mn } else { f64::INFINITY };
            ratio >= band[0] && ratio <= band[1]
        }
        "rank_equal" => match &rule.expected_order {
            Some(eo) => {
                let mut order = vals.to_vec();
                order.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
                let got: Vec<String> = order.into_iter().map(|(id, _)| id).collect();
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
                *eo == got || *eo == got_axis
            }
            None => true,
        },
        _ => false,
    }
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
    fn array_responses_compare_by_identity_not_position() {
        // Photon groups carry photons_s_g, not atoms_per_g: they must still
        // differ when they differ (they used to compare as 0).
        let group = |p: f64| {
            json!({"low_eV": 1.0, "high_eV": 2.0, "centroid_eV": 1.5,
                                   "photons_s_g": p, "photons_s": p, "power_W_g": 0.0, "power_W": 0.0})
        };
        let a = json!([group(1.0), group(2.0)]);
        let b = json!([group(1.0), group(3.0)]);
        assert!((max_rel_diff(&a, &b).unwrap() - 1.0 / 3.0).abs() < 1e-15);
        assert_eq!(max_rel_diff(&a, &a), Some(0.0));
        // Inventories join by nuclide: an extra nuclide in the reference
        // (pruning kept fewer states) no longer shifts every later position.
        let n = |name: &str, atoms: f64| {
            json!({"nuclide": name, "Z": 0, "A": 0, "LISO": 0,
                                               "atoms_per_g": atoms})
        };
        let declared = json!([n("Fe55", 10.0), n("Mn54", 4.0)]);
        let reference = json!([n("Cr51", 1e-30), n("Fe55", 10.0), n("Mn54", 4.0)]);
        assert_eq!(max_rel_diff(&declared, &reference), Some(1.0)); // Cr51 absent: 100 %
        let reordered = json!([n("Mn54", 4.0), n("Fe55", 10.0)]);
        assert_eq!(max_rel_diff(&declared, &reordered), Some(0.0));
        let shifted = json!([n("Fe55", 10.0), n("Mn54", 5.0)]);
        assert!((max_rel_diff(&declared, &shifted).unwrap() - 0.2).abs() < 1e-15);
        // Scalars are unchanged; mixed shapes are not comparable.
        assert_eq!(max_rel_diff(&json!(2.0), &json!(4.0)), Some(0.5));
        assert_eq!(max_rel_diff(&json!(0.0), &json!(0.0)), Some(0.0));
        assert_eq!(max_rel_diff(&json!(1.0), &declared), None);
        // Near-zero tests use the physical total, not Z+A+LISO+atoms.
        assert_eq!(response_total(&declared), Some(14.0));
    }

    #[test]
    fn scalar_only_consumers_refuse_array_responses() {
        let mut v = study_json();
        v["comparison"] = json!({"axes": ["material"], "decision_rules": [
            {"id": "r", "kind": "within_rel", "response": "photon_source_per_group", "bound": 0.1}]});
        let s: Study = serde_json::from_value(v).unwrap();
        assert!(s.validate().unwrap_err().contains("array-valued"));
    }

    #[test]
    fn case_id_separator_is_reserved_in_names() {
        let mut v = study_json();
        v["cases"]["spectra"][0]["name"] = json!("s__1");
        let s: Study = serde_json::from_value(v).unwrap();
        assert!(s.validate().unwrap_err().contains("case-id separator"));
    }

    #[test]
    fn study_steps_do_not_accept_per_step_spectra() {
        let mut v = study_json();
        v["cases"]["schedules"][0]["steps"][0]["spectrum"] =
            json!({"structure": "custom", "boundaries_eV": [1.0, 2.0], "flux_per_group": [1.0]});
        let s: Study = serde_json::from_value(v).unwrap();
        assert!(s.validate().unwrap_err().contains("per-step spectrum"));
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
        let (field, phase) = ("spatial_handoff", "P32");
        let mut v = study_json();
        v[field] = json!({});
        let e = Study::from_json(&v.to_string()).unwrap_err();
        assert!(e.contains("family_not_qualified"), "{e}");
        assert!(e.contains(phase), "{e}");
    }

    #[test]
    fn refinement_criteria_are_validated() {
        let mut v = study_json();
        v["refinement"] = json!({
            "criteria": [{
                "response": "total_activity_bq_per_g",
                "time_s": 0.0, "rel": 1e-6}],
            "resource_limit_runs": 4});
        assert!(Study::from_json(&v.to_string()).is_ok());
        // unqualified response
        let mut v = study_json();
        v["refinement"] = json!({"criteria": [{
            "response": "dose_rate", "time_s": 0.0, "rel": 1e-6}]});
        let e = Study::from_json(&v.to_string()).unwrap_err();
        assert!(e.contains("not qualified"), "{e}");
        // no bound declared
        let mut v = study_json();
        v["refinement"] = json!({"criteria": [{
            "response": "total_activity_bq_per_g", "time_s": 0.0}]});
        assert!(Study::from_json(&v.to_string())
            .unwrap_err()
            .contains("requires rel or abs"));
        // negative bound
        let mut v = study_json();
        v["refinement"] = json!({"criteria": [{
            "response": "total_activity_bq_per_g", "time_s": 0.0,
            "rel": -1.0}]});
        assert!(Study::from_json(&v.to_string())
            .unwrap_err()
            .contains("finite >= 0"));
    }

    #[test]
    fn criterion_verdict_semantics() {
        let crit = |rel, abs| Criterion {
            response: "total_activity_bq_per_g".into(),
            time_s: 0.0,
            rel,
            abs,
        };
        // above the abs scale the rel bound applies
        assert_eq!(
            criterion_status(&json!(1e5), 5e-8, &crit(Some(1e-6), None)),
            "satisfied"
        );
        assert_eq!(
            criterion_status(&json!(1e5), 5e-4, &crit(Some(1e-6), None)),
            "unmet"
        );
        // below the abs scale a rel-only criterion is unestablished,
        // not a silent pass
        assert_eq!(
            criterion_status(&json!(1e-9), 0.5, &crit(Some(1e-6), None)),
            "unestablished"
        );
        assert_eq!(
            criterion_status(&json!(1e-9), 0.5, &crit(None, Some(1.0))),
            "satisfied"
        );
        assert_eq!(
            criterion_status(&json!(1e-9), 2.0, &crit(None, Some(1.0))),
            "unmet"
        );
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
                times_s: None,
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

// ---- ACT-ROBUST-01: nonlinear input sampling (P30) ----

/// Deterministic PRNG (xorshift64* + Box-Muller). Fixed seed gives
/// repeatability, not convergence evidence.
struct Rng(u64);

impl Rng {
    fn next_u64(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.0 = x;
        x.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }
    fn uniform(&mut self) -> f64 {
        (self.next_u64() >> 11) as f64 * (1.0 / (1u64 << 53) as f64)
    }
    fn normal(&mut self) -> f64 {
        let u1 = self.uniform().max(1e-300);
        let u2 = self.uniform();
        (-2.0 * u1.ln()).sqrt() * (2.0 * std::f64::consts::PI * u2).cos()
    }
}

/// Symmetric eigendecomposition by cyclic Jacobi sweeps. Returns
/// (eigenvalues in descending order, row-major eigenvector matrix V with
/// column j the eigenvector of eigenvalue j), or None when the input is
/// nonfinite. Deterministic: same input, same rotations, same result.
fn jacobi_eigh(a: &[f64], n: usize) -> Option<(Vec<f64>, Vec<f64>)> {
    if a.iter().any(|v| !v.is_finite()) {
        return None;
    }
    let mut m = a.to_vec();
    let mut v = vec![0.0f64; n * n];
    for i in 0..n {
        v[i * n + i] = 1.0;
    }
    let off_norm = |m: &[f64]| -> f64 {
        let mut s = 0.0;
        for i in 0..n {
            for j in (i + 1)..n {
                s += m[i * n + j] * m[i * n + j];
            }
        }
        s.sqrt()
    };
    let scale = (0..n)
        .map(|i| m[i * n + i].abs())
        .fold(0.0f64, f64::max)
        .max(1e-300);
    for _ in 0..200 {
        if off_norm(&m) <= 1e-12 * scale {
            break;
        }
        for p in 0..n {
            for q in (p + 1)..n {
                let apq = m[p * n + q];
                if apq == 0.0 {
                    continue;
                }
                let app = m[p * n + p];
                let aqq = m[q * n + q];
                let theta = 0.5 * (aqq - app) / apq;
                let t = theta.signum() / (theta.abs() + (theta * theta + 1.0).sqrt());
                let c = 1.0 / (t * t + 1.0).sqrt();
                let s = t * c;
                for k in 0..n {
                    let mkp = m[k * n + p];
                    let mkq = m[k * n + q];
                    m[k * n + p] = c * mkp - s * mkq;
                    m[k * n + q] = s * mkp + c * mkq;
                }
                for k in 0..n {
                    let mpk = m[p * n + k];
                    let mqk = m[q * n + k];
                    m[p * n + k] = c * mpk - s * mqk;
                    m[q * n + k] = s * mpk + c * mqk;
                }
                for k in 0..n {
                    let vkp = v[k * n + p];
                    let vkq = v[k * n + q];
                    v[k * n + p] = c * vkp - s * vkq;
                    v[k * n + q] = s * vkp + c * vkq;
                }
            }
        }
    }
    let mut order: Vec<usize> = (0..n).collect();
    order.sort_by(|&i, &j| {
        m[j * n + j]
            .partial_cmp(&m[i * n + i])
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    let eigenvalues: Vec<f64> = order.iter().map(|&i| m[i * n + i]).collect();
    let mut vectors = vec![0.0f64; n * n];
    for (new_j, &old_j) in order.iter().enumerate() {
        for i in 0..n {
            vectors[i * n + new_j] = v[i * n + old_j];
        }
    }
    Some((eigenvalues, vectors))
}

/// Sampling factor of the nearest positive-semidefinite covariance:
/// F = V·diag(√max(λ,0)) so draws are `σ0 + F·z` with z standard normal.
/// Eigen-clipping removes only the non-PSD mass — unlike a diagonal ridge
/// it does not inflate every variance. Returns (factor, clipped negative
/// eigenvalue mass) for the channel ledger.
fn psd_factor(cov: &[f64], n: usize) -> Option<(Vec<f64>, f64)> {
    // Jacobi assumes symmetric input; collapsed covariances can carry a
    // ledgered asymmetry, so take the symmetric part first
    let mut sym = vec![0.0f64; n * n];
    for i in 0..n {
        for j in 0..n {
            sym[i * n + j] = 0.5 * (cov[i * n + j] + cov[j * n + i]);
        }
    }
    let (eigenvalues, vectors) = jacobi_eigh(&sym, n)?;
    let clipped: f64 = eigenvalues.iter().map(|l| (-l).max(0.0)).sum();
    let mut factor = vec![0.0f64; n * n];
    for j in 0..n {
        let root = eigenvalues[j].max(0.0).sqrt();
        if root == 0.0 {
            continue;
        }
        for i in 0..n {
            factor[i * n + j] = vectors[i * n + j] * root;
        }
    }
    Some((factor, clipped))
}

fn resolve_path(base: &Path, p: &str) -> PathBuf {
    let path = Path::new(p);
    if path.is_absolute() {
        path.to_path_buf()
    } else {
        base.join(path)
    }
}

/// sha256 of a file, hex.
fn file_sha256(path: &Path) -> Result<String, String> {
    use sha2::Digest;
    let mut h = sha2::Sha256::new();
    let mut f = fs::File::open(path).map_err(|e| format!("cannot read {}: {e}", path.display()))?;
    std::io::copy(&mut f, &mut h).map_err(|e| format!("cannot read {}: {e}", path.display()))?;
    Ok(format!("{:x}", h.finalize()))
}

/// Perturb `spec` per the drawn factors: rate_scale entries, decay- and
/// yield-scale entries, total flux normalization, wt_percent composition
/// renormalized to its declared total. Returns the count of clamped
/// composition draws.
#[allow(clippy::too_many_arguments)]
fn perturb_spec(
    spec: &mut Spec,
    rate_factors: &[(usize, f64)],
    flux_factor: f64,
    comp_factors: &BTreeMap<String, f64>,
    decay_factors: &BTreeMap<String, f64>,
    yield_factors: &BTreeMap<String, f64>,
) -> usize {
    if !rate_factors.is_empty() {
        spec.options.rate_scale = Some(
            rate_factors
                .iter()
                .map(|(row, f)| (row.to_string(), *f))
                .collect(),
        );
    }
    if let Some(total) = &mut spec.spectrum.total {
        *total *= flux_factor;
    } else {
        for g in &mut spec.spectrum.flux_per_group {
            *g *= flux_factor;
        }
    }
    if !decay_factors.is_empty() {
        spec.options.decay_scale = Some(decay_factors.clone());
    }
    if !yield_factors.is_empty() {
        spec.options.yield_scale = Some(yield_factors.clone());
    }
    let mut clamps = 0;
    if !comp_factors.is_empty() {
        let total: f64 = spec.material.composition.values().sum();
        for (key, value) in spec.material.composition.iter_mut() {
            if let Some(f) = comp_factors.get(key) {
                if *f <= 0.0 {
                    clamps += 1;
                }
                *value = (*value * f).max(0.0);
            }
        }
        let perturbed: f64 = spec.material.composition.values().sum();
        if perturbed > 0.0 {
            for value in spec.material.composition.values_mut() {
                *value *= total / perturbed;
            }
        }
    }
    clamps
}

/// Collapsed-covariance context for one case.
struct CovCtx {
    rows: Vec<usize>,
    sigma0: Vec<f64>,
    cov: Vec<f64>,
    uncovered: Vec<usize>,
}

/// Digest of the study-level configuration a case record is derived
/// from: recorded per case so a rerun only resumes a case whose
/// recorded block was computed under the same sampling contract.
/// Nominal spec digests cannot see `samples`, `seed`, `channels`,
/// the comparison flag, or the study-level `responses` list that
/// `per_time`/`undefined_responses` are extracted with — without this
/// digest a config change would silently reuse stale sample
/// statistics.
fn robustness_config_sha(study: &Study, rb: &Robustness) -> String {
    sha256_hex(
        serde_json::to_vec(&json!({
            "samples": rb.samples,
            "seed": rb.seed,
            "channels": rb.channels,
            "covariance": rb.covariance,
            "responses": rb.responses,
            "resource_limit_runs": rb.resource_limit_runs,
            "first_order_comparison": rb.first_order_comparison,
            "study_responses": study.responses,
        }))
        .unwrap_or_default()
        .as_slice(),
    )
}

/// Evaluate ACT-ROBUST-01 for one case: nominal + `samples` perturbed
/// solves, per-response sample statistics and coverage accounting.
fn evaluate_robustness(
    study: &Study,
    rb: &Robustness,
    spec: &Spec,
    nominal_out: &Value,
    base: &Path,
    cdir: &Path,
    prep: &mut PreparedCache,
) -> Value {
    match evaluate_robustness_inner(study, rb, spec, nominal_out, base, cdir, prep) {
        Ok(v) => v,
        Err(e) => json!({"status": "gap", "error": e}),
    }
}

fn evaluate_robustness_inner(
    study: &Study,
    rb: &Robustness,
    spec: &Spec,
    nominal_out: &Value,
    base: &Path,
    cdir: &Path,
    prep: &mut PreparedCache,
) -> Result<Value, String> {
    use actinv_data::{composition, covariance, decay, library};

    let physical = spec.physical_inputs()?;
    let phi = physical.flux.values();

    // resolve the covariance collapse over rows active for this material
    let mut cov_ctx: Option<CovCtx> = None;
    if rb.channels.cross_section_mf33 {
        let cref = rb
            .covariance
            .as_ref()
            .ok_or("cross_section_mf33 requires robustness.covariance")?;
        let cov_path = resolve_path(base, &cref.path);
        if let Some(want) = &cref.sha256 {
            let got = file_sha256(&cov_path)?;
            if got != *want {
                return Err(format!(
                    "covariance sha256 mismatch: declared {want}, got {got}"
                ));
            }
        }
        let cov = covariance::read_npz(cov_path.to_str().ok_or("covariance path not utf-8")?)?;
        let lib_path = resolve_path(base, &spec.library.path);
        let lib = library::read_npz_after_sha256_verification(
            lib_path.to_str().ok_or("library path not utf-8")?,
        )?;
        let index_path = {
            let p = lib_path.display().to_string();
            let stem = p.strip_suffix(".npz").unwrap_or(&p);
            PathBuf::from(format!("{stem}_index.json"))
        };
        let index: Value = serde_json::from_str(
            &fs::read_to_string(&index_path)
                .map_err(|e| format!("cannot read {index_path:?}: {e}"))?,
        )
        .map_err(|e| format!("cannot parse {index_path:?}: {e}"))?;
        let lib_targets: Vec<(i32, i32)> = index["targets"]
            .as_array()
            .ok_or("library index has no targets")?
            .iter()
            .map(|t| {
                (
                    t["za"].as_i64().unwrap_or(0) as i32,
                    t["liso"].as_i64().unwrap_or(0) as i32,
                )
            })
            .collect();
        let mut nuclides = decay::parse_file(&spec.decay.primary).map_err(|e| e.to_string())?;
        if let Some(fallback) = &spec.decay.fallback {
            if !fallback.is_empty() {
                for (k, v) in decay::parse_file(fallback).map_err(|e| e.to_string())? {
                    nuclides.entry(k).or_insert(v);
                }
            }
        }
        let (isotopes, _) = composition::material_atoms_per_gram(
            &spec.material.composition,
            &spec.material.basis,
            &nuclides,
        )?;
        let isotope_set: std::collections::HashSet<(i32, i32)> = isotopes.keys().copied().collect();
        let active_rows: Vec<usize> = lib
            .rows
            .iter()
            .enumerate()
            .filter(|(_, r)| {
                lib_targets
                    .get(r.target)
                    .map(|t| isotope_set.contains(t))
                    .unwrap_or(false)
            })
            .map(|(i, _)| i)
            .collect();
        // When the spec declares self-shielding the per-sample runs fold the
        // plan's per-group scales into collapsed cross sections — the MF=33
        // collapse must weight each row's flux by the same factors or the
        // sampled parameters would not match the depleted matrix.
        let shield_plan: Option<crate::shielding::ShieldPlan> = match &spec.self_shielding {
            Some(options) => {
                let table_path = resolve_path(base, &options.table.path);
                let got = file_sha256(&table_path)?;
                if got != options.table.sha256 {
                    return Err(format!(
                        "self-shielding table sha256 mismatch: declared {}, got {got}",
                        options.table.sha256
                    ));
                }
                let text = fs::read_to_string(&table_path).map_err(|error| {
                    format!(
                        "cannot read self-shielding table {}: {error}",
                        table_path.display()
                    )
                })?;
                let table = crate::shielding::PreparedShieldTable::from_json(&text)?;
                Some(table.plan(
                    &isotopes,
                    &options.dilution,
                    options.sigma0_b,
                    spec.options.temperature_K,
                    spec.options.require_shielding_complete,
                )?)
            }
            None => None,
        };
        let row_scale = |row: usize, group: usize| -> f64 {
            let Some(plan) = shield_plan.as_ref() else {
                return 1.0;
            };
            let descriptor = lib.rows[row];
            let Some(&(za, liso)) = lib_targets.get(descriptor.target) else {
                return 1.0;
            };
            plan.row_scales(za, liso, descriptor.mt)
                .and_then(|scales| scales.get(&group).copied())
                .unwrap_or(1.0)
        };
        let collapsed = cov.collapse_weighted(&lib, phi, &active_rows, &row_scale)?;
        cov_ctx = Some(CovCtx {
            rows: collapsed.row_indices,
            sigma0: collapsed.one_group_barns,
            cov: collapsed.covariance_barn2,
            uncovered: collapsed.uncovered_rows,
        });
    }

    // P43 input sets from the prepared run: radioactive chain nuclides
    // with their declared relative sigma, and the effective fission-yield
    // pairs this case's material selects.
    let decay_inputs: Vec<((i32, i32), f64, f64)> = if rb.channels.decay_constants {
        prep.prepared(spec)?.decay_inputs()
    } else {
        Vec::new()
    };
    let yield_inputs: crate::run::YieldInputs = if rb.channels.fission_yields {
        prep.prepared(spec)?.yield_inputs(spec, &physical)?
    } else {
        Vec::new()
    };
    let decay_names: Vec<String> = decay_inputs
        .iter()
        .map(|(key, _, _)| crate::run::name_of(key.0, key.1))
        .collect();
    let yield_names: Vec<String> = yield_inputs
        .iter()
        .map(|(key, _, _)| {
            format!(
                "{}:{}",
                crate::run::name_of(key.0, key.1),
                crate::run::name_of(key.2, key.3)
            )
        })
        .collect();
    let decay_partition = |covered: bool| -> Vec<String> {
        decay_inputs
            .iter()
            .zip(decay_names.iter())
            .filter(|((_, _, rel), _)| (*rel > 0.0) == covered)
            .map(|(_, name)| name.clone())
            .collect()
    };
    let yield_partition = |covered: bool| -> Vec<String> {
        yield_inputs
            .iter()
            .zip(yield_names.iter())
            .filter(|((_, _, rel), _)| (*rel > 0.0) == covered)
            .map(|(_, name)| name.clone())
            .collect()
    };

    let n = rb
        .resource_limit_runs
        .map(|m| m.min(rb.samples))
        .unwrap_or(rb.samples);
    let truncated = n < rb.samples;

    // precompute the xs sampling factor once per case: draws are
    // multiplicative lognormal factors exp(F_rel·z − ½·diag(Σ_rel)) on the
    // relative covariance Σ_rel[i,j] = Σ[i,j]/(σ_i σ_j), spectral-factored
    // on the nearest-PSD projection. Multiplicative draws preserve the
    // mean, keep the full correlation structure, and never go nonpositive
    // — no ridge, no independence fallback, no clamps.
    let (xs_factor, xs_clipped, xs_rel_diag) = cov_ctx
        .as_ref()
        .and_then(|c| {
            let m = c.rows.len();
            let mut rel = vec![0.0f64; m * m];
            for i in 0..m {
                for j in 0..m {
                    let denom = c.sigma0[i] * c.sigma0[j];
                    if denom > 0.0 {
                        rel[i * m + j] = c.cov[i * m + j] / denom;
                    }
                }
            }
            let (factor, clipped) = psd_factor(&rel, m)?;
            // projected diagonal diag(F F^T)_i: the −½·diag shift that
            // keeps E[factor]=1 under the actually-sampled (clipped)
            // distribution, not the unclipped Σ_rel diagonal
            let diag: Vec<f64> = (0..m)
                .map(|i| (0..m).map(|j| factor[i * m + j].powi(2)).sum())
                .collect();
            Some((factor, clipped, diag))
        })
        .map(|(factor, clipped, diag)| (Some(factor), clipped, diag))
        .unwrap_or((None, f64::NAN, Vec::new()));
    let xs_correlated = xs_factor.is_some();

    let nominal_vals = response_times(nominal_out, &rb.responses);
    let mut samples_out: Vec<Value> = Vec::new();
    let mut sample_values: Vec<Value> = vec![Value::Null; n as usize];
    let mut n_failed = 0usize;
    let mut n_clamped_comp = 0usize;
    let mut n_clamped_xs = 0usize;
    let mut n_clamped_decay = 0usize;
    let mut n_clamped_yield = 0usize;
    let mut n_applied_rows = 0usize;
    let mut n_reused_samples = 0usize;
    let mut sample_digests = Vec::new();

    // Sample-granular progress: one NDJSON line per finished sample is the
    // stream-safe resume record (P43). On a resumed run the last line per
    // index wins; a line is reused only when the re-drawn sample spec hash
    // and every on-disk artifact digest it records re-verify.
    let progress_path = cdir.join("rob_samples.ndjson");
    let mut progress: BTreeMap<usize, Value> = BTreeMap::new();
    if let Ok(text) = fs::read_to_string(&progress_path) {
        for line in text.lines() {
            if let Ok(v) = serde_json::from_str::<Value>(line) {
                if let Some(i) = v["sample"].as_u64() {
                    progress.insert(i as usize, v);
                }
            }
        }
    }

    // Per-sample streams: sample index i seeds an independent stream, so
    // the same index in every case shares the drawn z-values (common
    // random numbers for paired comparisons). Each channel consumes its
    // own sub-stream tagged off the sample seed in a fixed order —
    // flux, composition, decay, yield, cross-section — so a
    // case-dependent draw count (xs rows, yield pairs) can never shift
    // another channel's stream position, and a joint run reads the same
    // channel streams as an isolated run on the same tag.
    let mut draw = |sample_seed: u64, channels: &RobustChannels, sspec: &mut Spec| {
        let stream = |tag: u64| Rng(sample_seed ^ tag);
        // draw stage: consume z's for every study-enabled channel, each
        // from its own tagged stream
        let flux_z = if rb.channels.flux_rel_std > 0.0 {
            stream(0x1000_0000_0000_0001).normal()
        } else {
            0.0
        };
        let comp_z: Vec<f64> = {
            let mut r = stream(0x2000_0000_0000_0002);
            (0..rb.channels.composition_rel_std.len())
                .map(|_| r.normal())
                .collect()
        };
        let decay_z: Vec<f64> = if rb.channels.decay_constants {
            let mut r = stream(0x3000_0000_0000_0003);
            (0..decay_inputs.len()).map(|_| r.normal()).collect()
        } else {
            Vec::new()
        };
        let yield_z: Vec<f64> = if rb.channels.fission_yields {
            let mut r = stream(0x4000_0000_0000_0004);
            (0..yield_inputs.len()).map(|_| r.normal()).collect()
        } else {
            Vec::new()
        };
        let xs_z: Vec<f64> = if rb.channels.cross_section_mf33 && xs_factor.is_some() {
            let mut r = stream(0x5000_0000_0000_0005);
            let m = cov_ctx.as_ref().map(|c| c.rows.len()).unwrap_or(0);
            (0..m).map(|_| r.normal()).collect()
        } else {
            Vec::new()
        };

        // apply stage: only this run's channel subset is applied
        let flux_factor = if channels.flux_rel_std > 0.0 {
            let s2 = (1.0 + channels.flux_rel_std * channels.flux_rel_std).ln();
            (-0.5 * s2 + s2.sqrt() * flux_z).exp()
        } else {
            1.0
        };
        let comp_factors: BTreeMap<String, f64> = channels
            .composition_rel_std
            .iter()
            .zip(comp_z.iter())
            .map(|((k, s), z)| {
                let s2 = (1.0 + s * s).ln();
                (k.clone(), (-0.5 * s2 + s2.sqrt() * z).exp())
            })
            .collect();
        let mut decay_factors = BTreeMap::new();
        if channels.decay_constants {
            for (((za, liso), _, rel), z) in decay_inputs.iter().zip(decay_z.iter()) {
                if *rel <= 0.0 {
                    continue; // uncovered input: factor 1.0, listed in coverage
                }
                let s2 = (1.0 + rel * rel).ln();
                let f = (-0.5 * s2 + s2.sqrt() * z).exp();
                if !(f.is_finite() && f > 0.0) {
                    n_clamped_decay += 1;
                    continue;
                }
                decay_factors.insert(crate::run::name_of(*za, *liso), f);
            }
        }
        let mut yield_factors = BTreeMap::new();
        if channels.fission_yields {
            for ((key, _, rel), z) in yield_inputs.iter().zip(yield_z.iter()) {
                if *rel <= 0.0 {
                    continue;
                }
                let s2 = (1.0 + rel * rel).ln();
                let f = (-0.5 * s2 + s2.sqrt() * z).exp();
                if !(f.is_finite() && f > 0.0) {
                    n_clamped_yield += 1;
                    continue;
                }
                yield_factors.insert(
                    format!(
                        "{}:{}",
                        crate::run::name_of(key.0, key.1),
                        crate::run::name_of(key.2, key.3)
                    ),
                    f,
                );
            }
        }
        let mut rate_factors: Vec<(usize, f64)> = Vec::new();
        if channels.cross_section_mf33 {
            if let (Some(ctx), Some(f)) = (&cov_ctx, &xs_factor) {
                for (i2, &row) in ctx.rows.iter().enumerate() {
                    if ctx.sigma0[i2] <= 0.0 {
                        continue;
                    }
                    let mut delta = 0.0;
                    for (j, &zv) in xs_z.iter().enumerate() {
                        delta += f[i2 * ctx.rows.len() + j] * zv;
                    }
                    let factor = (delta - 0.5 * xs_rel_diag[i2]).exp();
                    if !(factor.is_finite() && factor > 0.0) {
                        n_clamped_xs += 1;
                        continue;
                    }
                    rate_factors.push((row, factor));
                }
            }
        }
        n_clamped_comp += perturb_spec(
            sspec,
            &rate_factors,
            flux_factor,
            &comp_factors,
            &decay_factors,
            &yield_factors,
        );
        (flux_factor, rate_factors.len())
    };

    let mut draw_samples = |channels: &RobustChannels,
                            prefix: &str,
                            seed_tag: u64,
                            keep_outs: bool,
                            outs: &mut Vec<Value>,
                            aligned: Option<&mut Vec<Value>>|
     -> usize {
        let mut aligned = aligned;
        let mut failed = 0usize;
        for i in 0..n {
            let mut sspec = spec.clone();
            let sample_seed =
                rb.seed ^ seed_tag ^ (i as u64 + 1).wrapping_mul(0x9E37_79B9_7F4A_7C15);
            let (flux_factor, n_rows) = draw(sample_seed, channels, &mut sspec);
            n_applied_rows = n_applied_rows.max(n_rows);
            let spath = cdir.join(format!("{prefix}{i}.json"));
            let stext =
                serde_json::to_string_pretty(&serde_json::to_value(&sspec).unwrap_or_default())
                    .unwrap_or_default();
            let spec_sha = format!("{:x}", Sha256::digest(format!("{stext}\n").as_bytes()));
            // resume check: a recorded line is reused only when the
            // re-drawn spec hash and the on-disk artifacts re-verify
            if keep_outs {
                if let Some(line) = progress.get(&(i as usize)) {
                    let spec_ok = line["spec_sha256"].as_str() == Some(spec_sha.as_str())
                        && file_sha256(&spath).ok().as_deref() == Some(spec_sha.as_str());
                    match (spec_ok, line["status"].as_str()) {
                        (true, Some("executed")) => {
                            let opath = cdir.join(format!("{prefix}{i}.out.json"));
                            let out_ok = line["out_sha256"].as_str().is_some_and(|want| {
                                file_sha256(&opath).ok().as_deref() == Some(want)
                            });
                            if out_ok {
                                n_reused_samples += 1;
                                sample_digests.push(json!({
                                    "sample": i,
                                    "spec_sha256": spec_sha,
                                    "out_sha256": line["out_sha256"],
                                    "flux_factor": line["flux_factor"],
                                    "resumed": true,
                                }));
                                let vals = line["responses"].clone();
                                outs.push(vals.clone());
                                if let Some(av) = aligned.as_deref_mut() {
                                    av[i as usize] = vals;
                                }
                                continue;
                            }
                        }
                        (true, Some("failed")) => {
                            n_reused_samples += 1;
                            failed += 1;
                            n_failed += 1;
                            sample_digests.push(json!({
                                "sample": i,
                                "spec_sha256": spec_sha,
                                "failed": line["failed"],
                                "resumed": true,
                            }));
                            continue;
                        }
                        _ => {}
                    }
                }
            }
            let _ = fs::write(&spath, format!("{stext}\n"));
            match prep.run_prepared(&sspec) {
                Ok(rr) => {
                    let ov = serde_json::to_value(&rr).unwrap_or_default();
                    let opath = cdir.join(format!("{prefix}{i}.out.json"));
                    let otext = serde_json::to_string_pretty(&ov).unwrap_or_default();
                    let _ = fs::write(&opath, format!("{otext}\n"));
                    if keep_outs {
                        let out_sha = file_sha256(&opath).ok();
                        sample_digests.push(json!({
                            "sample": i,
                            "spec_sha256": spec_sha,
                            "out_sha256": out_sha,
                            "flux_factor": flux_factor,
                        }));
                        // Keep only the declared per-time responses; the full
                        // output is on disk, so memory does not grow with it.
                        let vals = Value::Object(extract_per_time(&ov, &rb.responses).0);
                        let line = json!({
                            "sample": i,
                            "status": "executed",
                            "spec_sha256": spec_sha,
                            "out_sha256": out_sha,
                            "flux_factor": flux_factor,
                            "responses": vals,
                        });
                        let _ = fs::OpenOptions::new()
                            .create(true)
                            .append(true)
                            .open(&progress_path)
                            .and_then(|mut f| {
                                use std::io::Write;
                                writeln!(f, "{line}")
                            });
                        outs.push(vals.clone());
                        if let Some(av) = aligned.as_deref_mut() {
                            av[i as usize] = vals;
                        }
                    } else {
                        outs.push(Value::Object(extract_per_time(&ov, &rb.responses).0));
                    }
                }
                Err(e) => {
                    failed += 1;
                    n_failed += 1;
                    if keep_outs {
                        sample_digests.push(json!({
                            "sample": i,
                            "spec_sha256": spec_sha,
                            "failed": e,
                        }));
                        let line = json!({
                            "sample": i,
                            "status": "failed",
                            "spec_sha256": spec_sha,
                            "failed": e,
                        });
                        let _ = fs::OpenOptions::new()
                            .create(true)
                            .append(true)
                            .open(&progress_path)
                            .and_then(|mut f| {
                                use std::io::Write;
                                writeln!(f, "{line}")
                            });
                    }
                }
            }
        }
        failed
    };
    draw_samples(
        &rb.channels,
        "rob_",
        0x9E37_79B9_7F4A_7C15,
        true,
        &mut samples_out,
        Some(&mut sample_values),
    );

    // per-response statistics across samples
    let mut response_stats = serde_json::Map::new();
    for response in &rb.responses {
        for (_, tkey, nominal_v) in &nominal_vals[response] {
            let vals: Vec<f64> = samples_out
                .iter()
                .filter_map(|o| sample_response(o, tkey, response))
                .collect();
            let m = vals.len();
            let (mean, std) = if m > 0 {
                let mean = vals.iter().sum::<f64>() / m as f64;
                let var =
                    vals.iter().map(|v| (v - mean).powi(2)).sum::<f64>() / (m - 1).max(1) as f64;
                (mean, var.sqrt())
            } else {
                (f64::NAN, f64::NAN)
            };
            let half = 1.96 * std / (m.max(1) as f64).sqrt();
            let stat = json!({
                "nominal": nominal_v,
                "n_samples": m,
                "mean": mean,
                "std": std,
                "ci95_half_width": half,
                "sampling_error_std": std / (m.max(1) as f64).sqrt(),
            });
            let entry = response_stats
                .entry(response.clone())
                .or_insert_with(|| json!({}));
            if let Value::Object(map) = entry {
                map.insert(tkey.clone(), stat);
            }
        }
    }

    // --- channel attribution ----------------------------------------
    // Each enabled channel is sampled alone with the same count and a
    // stream-tagged seed; variance fractions and the unexplained
    // interaction remainder are reported per (response, time).
    let mut attribution = Map::new();
    {
        let isolations: Vec<(&str, RobustChannels)> = [
            (
                "cross_section_mf33",
                RobustChannels {
                    cross_section_mf33: rb.channels.cross_section_mf33,
                    ..Default::default()
                },
            ),
            (
                "flux",
                RobustChannels {
                    flux_rel_std: rb.channels.flux_rel_std,
                    ..Default::default()
                },
            ),
            (
                "composition",
                RobustChannels {
                    composition_rel_std: rb.channels.composition_rel_std.clone(),
                    ..Default::default()
                },
            ),
            (
                "decay_constants",
                RobustChannels {
                    decay_constants: rb.channels.decay_constants,
                    ..Default::default()
                },
            ),
            (
                "fission_yields",
                RobustChannels {
                    fission_yields: rb.channels.fission_yields,
                    ..Default::default()
                },
            ),
        ]
        .into_iter()
        .filter(|(_, c)| {
            c.cross_section_mf33
                || c.flux_rel_std > 0.0
                || !c.composition_rel_std.is_empty()
                || c.decay_constants
                || c.fission_yields
        })
        .collect();
        let mut channel_var: Map<String, Value> = Map::new();
        for (tag, chan) in &isolations {
            let mut outs = Vec::new();
            draw_samples(
                chan,
                &format!("attr_{tag}_"),
                match *tag {
                    "cross_section_mf33" => 0xA5A5_0000_0000_0001,
                    "flux" => 0xA5A5_0000_0000_0002,
                    "composition" => 0xA5A5_0000_0000_0003,
                    "decay_constants" => 0xA5A5_0000_0000_0004,
                    _ => 0xA5A5_0000_0000_0005,
                },
                false,
                &mut outs,
                None,
            );
            let mut vars = Map::new();
            for response in &rb.responses {
                let mut per_time = Map::new();
                for (_, tkey, _) in &nominal_vals[response] {
                    let vals: Vec<f64> = outs
                        .iter()
                        .filter_map(|o| sample_response(o, tkey, response))
                        .collect();
                    let m = vals.len();
                    // Fewer than two successful isolated draws leave the
                    // channel variance undetermined: null, never zero.
                    let var = (m > 1).then(|| {
                        let mean = vals.iter().sum::<f64>() / m as f64;
                        vals.iter().map(|v| (v - mean).powi(2)).sum::<f64>() / (m - 1) as f64
                    });
                    per_time.insert(tkey.clone(), json!(var));
                }
                vars.insert(response.clone(), Value::Object(per_time));
            }
            channel_var.insert(tag.to_string(), Value::Object(vars));
        }
        for response in &rb.responses {
            let mut per_time = Map::new();
            for (_, tkey, _) in &nominal_vals[response] {
                let total = response_stats
                    .get(response)
                    .and_then(|m| m.get(tkey))
                    .and_then(|v| v.get("std"))
                    .and_then(Value::as_f64)
                    .map(|x| x.powi(2));
                let mut parts = Map::new();
                let mut sum = 0.0;
                let mut undetermined = false;
                for (tag, _) in &isolations {
                    let v = channel_var
                        .get(*tag)
                        .and_then(|m| m.get(response))
                        .and_then(|m| m.get(tkey))
                        .and_then(Value::as_f64);
                    match v {
                        Some(v) => sum += v,
                        None => undetermined = true,
                    }
                    parts.insert(tag.to_string(), json!(v));
                }
                per_time.insert(
                    tkey.clone(),
                    json!({
                        "total_variance": total,
                        "channel_variances": parts,
                        // An undetermined channel makes the remainder unknown too.
                        "unexplained_remainder":
                            total.filter(|_| !undetermined).map(|t| t - sum),
                    }),
                );
            }
            attribution.insert(response.clone(), Value::Object(per_time));
        }
    }

    // --- pathway view -------------------------------------------------
    // For each declared response and cooling time: the dominant
    // contributing nuclides and their first production legs, plus the
    // run-level pathway-closure remainder. No pathway is fabricated;
    // only what the nominal run attributed is shown.
    let mut pathway_view = Map::new();
    {
        let steps = nominal_out["steps"].as_array().cloned().unwrap_or_default();
        let irr_end = steps
            .iter()
            .filter(|st| st["flux"].as_f64().unwrap_or(0.0) > 0.0)
            .filter_map(|st| st["t_s"].as_f64())
            .next_back()
            .unwrap_or(0.0);
        let closure = nominal_out["pathway_closure"].as_f64();
        for response in &rb.responses {
            let mut per_time = Map::new();
            for (time_s, tkey, _) in &nominal_vals[response] {
                let step = steps.iter().find(|st| {
                    (st["t_s"].as_f64().unwrap_or(f64::NAN) - irr_end - time_s).abs()
                        < 1e-6 * time_s.abs().max(1.0)
                });
                if let Some(st) = step {
                    let mut top: Vec<(&String, f64)> = st["activity_Bq_per_g"]
                        .as_object()
                        .map(|m| {
                            m.iter()
                                .map(|(k, v)| (k, v.as_f64().unwrap_or(0.0)))
                                .collect()
                        })
                        .unwrap_or_default();
                    top.sort_by(|a, b| b.1.total_cmp(&a.1));
                    let legs: Vec<Value> = top
                        .iter()
                        .take(3)
                        .filter(|(_n, a)| *a > 0.0)
                        .map(|(n, a)| {
                            let legs = nominal_out["pathways"]
                                .as_array()
                                .and_then(|p| p.iter().find_map(|e| e.get(*n)))
                                .cloned()
                                .unwrap_or(Value::Null);
                            json!({
                                "nuclide": n,
                                "activity_share": a,
                                "production_legs": legs,
                            })
                        })
                        .collect();
                    per_time.insert(
                        tkey.clone(),
                        json!({
                            "dominant_nuclides": legs,
                            "pathway_closure_unattributed": closure,
                        }),
                    );
                }
            }
            pathway_view.insert(response.clone(), Value::Object(per_time));
        }
    }

    // --- local-vs-nonlinear applicability check ---------------------
    // First-order MF=33 propagation on the nominal spec, compared with
    // the sampled std. For decay heat the `heat.total` selector gives an
    // exact first-order band; for total activity the per-nuclide bands
    // are combined by root-sum-square, which neglects cross-nuclide
    // covariance — that is recorded, not hidden.
    let mut local_vs_nonlinear: Value = Value::Null;
    if !rb.first_order_comparison {
        local_vs_nonlinear = json!({
            "status": "not_evaluated",
            "reason": "robustness.first_order_comparison is false",
        });
    }
    if rb.channels.cross_section_mf33 && !truncated && rb.first_order_comparison {
        if let Some(cov_ref) = &rb.covariance {
            let mut uspec = spec.clone();
            uspec.options.rate_scale = None;
            uspec.uncertainty = Some(crate::spec::UncertaintyOptions {
                covariance: crate::spec::HashedFileRef {
                    path: cov_ref.path.clone(),
                    sha256: cov_ref.sha256.clone().unwrap_or_default(),
                },
                responses: vec![
                    "activity:*".to_string(),
                    "activity.total".to_string(),
                    "heat.total".to_string(),
                ],
                channels: vec!["cross_section_mf33".to_string()],
                confidence_level: 0.95,
                require_complete: false,
                voi: None,
                isomer: None,
            });
            match prep.run_prepared(&uspec) {
                Ok(urr) => {
                    let uv = serde_json::to_value(&urr).unwrap_or_default();
                    let mut comparisons = Map::new();
                    for response in &rb.responses {
                        let mut per_time = Map::new();
                        for (time_s, tkey, _) in &nominal_vals[response] {
                            let lfo = first_order_std(&uv, response, *time_s);
                            let sampled = response_stats
                                .get(response)
                                .and_then(|m| m.get(tkey))
                                .and_then(|v| v.get("std"))
                                .and_then(Value::as_f64);
                            if let (Some(l), Some(smp)) = (lfo, sampled) {
                                per_time.insert(
                                    tkey.clone(),
                                    json!({
                                        "first_order_std": l,
                                        "sampled_std": smp,
                                        "sampled_over_first_order":
                                            if l > 0.0 { smp / l } else {
                                                f64::NAN
                                            },
                                    }),
                                );
                            }
                        }
                        comparisons.insert(response.clone(), Value::Object(per_time));
                    }
                    local_vs_nonlinear = json!({
                        "method": "first-order local propagation (P11) vs nonlinear sample spread",
                        "comparisons": comparisons,
                        "total_activity_approximation":
                            "activity.total propagated directly through the full covariance (s^T Σ s); no root-sum-square combination",
                    });
                }
                Err(e) => {
                    local_vs_nonlinear = json!({
                        "status": "failed",
                        "error": e,
                    });
                }
            }
        }
    }

    let (covered_rows, uncovered_rows) = cov_ctx
        .as_ref()
        .map(|c| (c.rows.len(), c.uncovered.clone()))
        .unwrap_or_default();
    Ok(json!({
        "local_vs_nonlinear": local_vs_nonlinear,
        "channel_attribution": attribution,
        "pathway_view": pathway_view,
        "status": "executed",
        "samples": n,
        "samples_declared": rb.samples,
        "seed": rb.seed,
        "truncated_by_resource_limit": truncated,
        "n_failed_samples": n_failed,
        "n_reused_samples": n_reused_samples,
        "config_sha256": robustness_config_sha(study, rb),
        "channels": {
            "cross_section_mf33": {
                "enabled": rb.channels.cross_section_mf33,
                "correlated": xs_correlated,
                "sampling": "correlated lognormal multiplicative factors on the nearest-PSD relative covariance",
                "negative_eigenvalue_mass_clipped_relative": if xs_clipped.is_nan() {
                    Value::Null
                } else {
                    json!(xs_clipped)
                },
                "independence_assumption": if xs_correlated {
                    Value::Null
                } else {
                    json!("collapsed covariance not samplable (nonfinite or absent)")
                },
                "covered_rows": covered_rows,
                "n_applied_rows": n_applied_rows,
                "uncovered_rows": uncovered_rows,
                "n_clamped_nonpositive_draws": n_clamped_xs,
            },
            "flux_rel_std": rb.channels.flux_rel_std,
            "composition_rel_std": rb.channels.composition_rel_std,
            "n_composition_clamps": n_clamped_comp,
            "decay_constants": {
                "enabled": rb.channels.decay_constants,
                "sampling": "per-nuclide mean-preserving lognormal factors; relative sigma = declared d_half_life/half_life",
                "n_active": decay_inputs.len(),
                "covered": decay_partition(true),
                "uncovered": decay_partition(false),
                "n_clamped_nonpositive_draws": n_clamped_decay,
            },
            "fission_yields": {
                "enabled": rb.channels.fission_yields,
                "sampling": "per-(parent,product) mean-preserving lognormal factors on effective independent yields; relative sigma = declared sigma_yield/yield",
                "n_active": yield_inputs.len(),
                "covered": yield_partition(true),
                "uncovered": yield_partition(false),
                "n_clamped_nonpositive_draws": n_clamped_yield,
            },
        },
        "responses": response_stats,
        // index-aligned per-sample response maps for paired rule
        // evaluation: entry i is null when sample i failed
        "sample_values": sample_values,
        "sample_artifacts": sample_digests,
        "semantics": "sample spread is a sensitivity over the declared input distributions — not a domain bound or an evaluation comparison; sampling error, covered uncertainty, missing covariance and failures are reported separately",
    }))
}

/// First-order MF=33 standard uncertainty for a declared robustness
/// response at cooling time `time_s`, from a run whose `uncertainty`
/// block propagated `heat.total` and `activity:*`. `decay_heat_w_per_g`
/// maps to `heat.total`; `total_activity_bq_per_g` is the
/// root-sum-square over per-nuclide activity bands (cross-nuclide
/// covariance neglected — recorded upstream).
fn first_order_std(uv: &Value, response: &str, time_s: f64) -> Option<f64> {
    let steps = uv["steps"].as_array()?;
    let irr_end = steps
        .iter()
        .filter(|st| st["flux"].as_f64().unwrap_or(0.0) > 0.0)
        .filter_map(|st| st["t_s"].as_f64())
        .next_back()
        .unwrap_or(0.0);
    let step = steps.iter().find(|st| {
        (st["t_s"].as_f64().unwrap_or(f64::NAN) - irr_end - time_s).abs()
            < 1e-6 * time_s.abs().max(1.0)
    })?;
    let responses = &step["uncertainty"]["responses"];
    match response {
        "decay_heat_w_per_g" => responses["heat.total"]["mf33_standard_uncertainty"].as_f64(),
        // direct propagated band on the aggregate response — no
        // root-sum-square approximation
        "total_activity_bq_per_g" => {
            responses["activity.total"]["mf33_standard_uncertainty"].as_f64()
        }
        _ => None,
    }
}

/// Prepared-run cache: contexts keyed by data-and-option signature,
/// with construction accounting. `ACTINV_STUDY_NO_REUSE` bypasses the
/// cache entirely (per-run prepare) for equivalence verification.
#[derive(Default)]
struct PreparedCache {
    map: std::collections::HashMap<String, PreparedRun>,
    count: usize,
    wall_s: f64,
}

impl PreparedCache {
    /// The prepared run for `spec`, building and caching it on first use.
    /// The robustness driver uses this to read the per-case sampling
    /// input sets (decay constants, effective yields) without paying
    /// preparation cost per draw.
    fn prepared(&mut self, spec: &Spec) -> Result<&PreparedRun, String> {
        let sig = prepared_signature(spec);
        if !self.map.contains_key(&sig) {
            let t0 = Instant::now();
            let p = PreparedRun::prepare(spec)?;
            self.wall_s += t0.elapsed().as_secs_f64();
            self.count += 1;
            self.map.insert(sig.clone(), p);
        }
        Ok(&self.map[&sig])
    }

    fn run_prepared(&mut self, spec: &Spec) -> Result<RunResult, String> {
        let no_reuse = std::env::var_os("ACTINV_STUDY_NO_REUSE").is_some();
        let sig = if no_reuse {
            String::new()
        } else {
            prepared_signature(spec)
        };
        if no_reuse || !self.map.contains_key(&sig) {
            let t0 = Instant::now();
            let p = PreparedRun::prepare(spec)?;
            self.wall_s += t0.elapsed().as_secs_f64();
            self.count += 1;
            if no_reuse {
                return p.run(spec, "study");
            }
            self.map.insert(sig.clone(), p);
        }
        self.map[&sig].run(spec, "study")
    }
}

/// Data-and-option signature of a spec for `PreparedRun` reuse: the
/// fields `ensure_compatible` verifies, plus the spectrum — the
/// collapsed activation library inside a prepared run is bound to the
/// flux vector (`validate_flux` fails closed on a different spectrum),
/// so spectrum is part of the reuse identity.
fn prepared_signature(spec: &Spec) -> String {
    canonical_json(&json!({
        "library": spec.library,
        "decay": spec.decay,
        "photon_response": spec.photon.response,
        "fission_yields": spec.fission_yields,
        "projectile": spec.projectile,
        // collapse-relevant spectrum fields only: `total` is a
        // normalization scalar that leaves the collapsed library
        // bit-identical, so flux-normalization perturbations share
        // the prepared run
        "spectrum_structure": spec.spectrum.structure,
        "spectrum_boundaries": spec.spectrum.boundaries_eV,
        "spectrum_descending": spec.spectrum.descending,
        "spectrum_flux_per_group": spec.spectrum.flux_per_group,
        "temperature_K": spec.options.temperature_K,
        "uncertainty": spec.uncertainty,
        "radiological": spec.radiological,
        "damage": spec.damage,
        "self_shielding": spec.self_shielding,
    }))
}

/// A prior case record is resumable only when every recorded artifact
/// digest re-verifies against the files on disk: spec identity, nominal
/// output, every robustness sample artifact, and the study-level
/// robustness configuration the recorded block was computed under.
fn case_resumable(prev: &Value, cent: &Value, cdir: &Path, robustness_sha: Option<&str>) -> bool {
    if prev["status"].as_str() != Some("executed") {
        return false;
    }
    if prev["spec_sha256"] != cent["spec_sha256"] {
        return false;
    }
    if let Some(want) = robustness_sha {
        if prev["robustness"]["config_sha256"].as_str() != Some(want) {
            return false;
        }
    }
    let out_path = cdir.join("out.json");
    match (prev["out_sha256"].as_str(), sha256_path(&out_path).ok()) {
        (Some(a), Some(b)) if a == b => {}
        _ => return false,
    }
    if let Some(arts) = prev["robustness"]["sample_artifacts"].as_array() {
        for a in arts {
            let i = a["sample"].as_u64().unwrap_or(0);
            let sp = cdir.join(format!("rob_{i}.json"));
            match (a["spec_sha256"].as_str(), sha256_path(&sp).ok()) {
                (Some(x), Some(y)) if x == y => {}
                _ => return false,
            }
            if a.get("failed").is_none() {
                let op = cdir.join(format!("rob_{i}.out.json"));
                match (a["out_sha256"].as_str(), sha256_path(&op).ok()) {
                    (Some(x), Some(y)) if x == y => {}
                    _ => return false,
                }
            }
        }
    }
    true
}

/// Write the in-flight record after every case so an interrupted run
/// leaves a resumable, honest partial record.
fn write_partial_record(
    path: &Path,
    study: &Study,
    study_sha: &Value,
    manifest_sha: &Option<String>,
    per_case: &[Value],
    resumed_cases: &[String],
    prepared_count: usize,
) {
    let n_exec = per_case
        .iter()
        .filter(|c| c["status"] == "executed")
        .count();
    let record = json!({
        "schema": RECORD_SCHEMA,
        "study_id": study.study_id,
        "study_sha256": study_sha,
        "manifest_sha256": manifest_sha,
        "status": "partial",
        "cases_completed": per_case.len(),
        "cases_declared": study.case_ids().len(),
        "cases_executed_so_far": n_exec,
        "resumed_cases": resumed_cases,
        "prepared_runs": prepared_count,
        "cases": per_case,
        "qualification": "unqualified",
    });
    let _ = fs::write(
        path,
        serde_json::to_string_pretty(&record).unwrap_or_default() + "\n",
    );
}

/// One sample's scalar response at a time key, from its compact per-time map.
fn sample_response(sample: &Value, tkey: &str, response: &str) -> Option<f64> {
    sample.get(tkey)?.get(response)?.as_f64()
}

/// nominal response values as (time_s, tkey, value) triples
fn response_times(
    nominal_out: &Value,
    responses: &[String],
) -> BTreeMap<String, Vec<(f64, String, f64)>> {
    let mut out = BTreeMap::new();
    for r in responses {
        let (per_time, _) = extract_per_time(nominal_out, std::slice::from_ref(r));
        let mut m = Vec::new();
        for (tk, entry) in per_time {
            // Robustness responses are validated scalar.
            if let Some(s) = entry.get(r.as_str()).and_then(Value::as_f64) {
                let time_s: f64 = tk.parse().unwrap_or(f64::NAN);
                m.push((time_s, tk, s));
            }
        }
        out.insert(r.clone(), m);
    }
    out
}

#[cfg(test)]
mod robustness_tests {
    use super::*;

    fn base_study() -> Value {
        json!({
            "study": "actinv-study-1",
            "study_id": "t",
            "library": {"path": "lib.npz"},
            "decay": {"primary": "d.dat"},
            "cases": {
                "materials": [{"name": "a", "composition": {"Fe": 100.0}}],
                "spectra": [{"name": "s", "flux_per_group": [1.0]}],
                "schedules": [{"name": "p", "steps": [{"dt": "5 s", "flux": 1.0}]}]
            },
            "responses": ["total_activity_bq_per_g"],
            "robustness": {
                "samples": 4,
                "seed": 7,
                "channels": {"flux_rel_std": 0.05},
                "responses": ["total_activity_bq_per_g"]
            }
        })
    }

    #[test]
    fn robustness_validation() {
        let s: Study = serde_json::from_value(base_study()).unwrap();
        s.validate().unwrap();

        let mut v = base_study();
        v["robustness"]["samples"] = json!(1);
        let s: Study = serde_json::from_value(v).unwrap();
        assert!(s.validate().unwrap_err().contains("samples"));

        let mut v = base_study();
        v["robustness"]["samples"] = json!(MAX_ROBUSTNESS_SAMPLES + 1);
        let s: Study = serde_json::from_value(v).unwrap();
        assert!(s.validate().unwrap_err().contains("robustness_too_large"));

        // Array responses used to summarise as a fabricated 0 +/- 0.
        for array in ["photon_source_per_group", "inventory_per_nuclide"] {
            let mut v = base_study();
            v["robustness"]["responses"] = json!([array]);
            let s: Study = serde_json::from_value(v).unwrap();
            assert!(
                s.validate().unwrap_err().contains("array-valued"),
                "{array}"
            );
        }

        let mut v = base_study();
        v["robustness"]["channels"]["flux_rel_std"] = json!(-0.5);
        let s: Study = serde_json::from_value(v).unwrap();
        assert!(s.validate().unwrap_err().contains("flux_rel_std"));

        let mut v = base_study();
        v["robustness"]["channels"]["bogus"] = json!(true);
        assert!(serde_json::from_value::<Study>(v).is_err());

        let mut v = base_study();
        v["robustness"]["channels"]["cross_section_mf33"] = json!(true);
        let s: Study = serde_json::from_value(v).unwrap();
        assert!(s.validate().unwrap_err().contains("covariance"));

        let mut v = base_study();
        v["robustness"]["channels"] = json!({});
        let s: Study = serde_json::from_value(v).unwrap();
        assert!(s.validate().unwrap_err().contains("no channel"));

        let mut v = base_study();
        v["robustness"]["responses"] = json!(["bogus_response"]);
        let s: Study = serde_json::from_value(v).unwrap();
        assert!(s.validate().unwrap_err().contains("family_not_qualified"));
    }

    #[test]
    fn p43_channel_validation() {
        // decay_constants alone is a complete channel declaration
        let mut v = base_study();
        v["robustness"]["channels"] = json!({"decay_constants": true});
        let s: Study = serde_json::from_value(v).unwrap();
        s.validate().unwrap();

        // fission_yields requires a material declaring yield files
        let mut v = base_study();
        v["robustness"]["channels"] = json!({"fission_yields": true});
        let s: Study = serde_json::from_value(v.clone()).unwrap();
        assert!(s.validate().unwrap_err().contains("fission_yields.files"));

        // a fissile material satisfies the requirement
        v["cases"]["materials"][0]["fission_yields"] = json!({
            "files": [{"path": "nfy.endf", "sha256": "0".repeat(64)}],
            "energy": "spectrum_average"
        });
        let s: Study = serde_json::from_value(v.clone()).unwrap();
        s.validate().unwrap();
        // and the block propagates verbatim into the case spec
        let spec = s.case_spec("a__s__p", Path::new(".")).unwrap();
        assert_eq!(spec.fission_yields.files.len(), 1);
        assert_eq!(spec.fission_yields.energy, "spectrum_average");

        // a non-fissile material leaves the field at its default
        let mut v = base_study();
        v["cases"]["materials"].as_array_mut().unwrap().push(json!({
            "name": "b", "composition": {"Fe": 100.0}
        }));
        let s: Study = serde_json::from_value(v).unwrap();
        let spec = s.case_spec("b__s__p", Path::new(".")).unwrap();
        assert!(spec.fission_yields.files.is_empty());
    }

    #[test]
    fn rule_predicate_evaluates_paired_sample_values() {
        let rule = DecisionRule {
            id: "r".into(),
            kind: "rank_equal".into(),
            response: "total_activity_bq_per_g".into(),
            times_s: Some(vec![0.0]),
            expected_order: Some(vec!["u".into(), "b".into(), "a".into()]),
            bound: None,
            band: None,
        };
        let vals = |a: f64, b: f64, u: f64| {
            vec![
                ("a__s__p".to_string(), a),
                ("b__s__p".to_string(), b),
                ("u__s__p".to_string(), u),
            ]
        };
        // expected order by axis value (material names)
        assert!(rule_predicate(&rule, &vals(1.0, 2.0, 3.0), Some(0)));
        assert!(!rule_predicate(&rule, &vals(3.0, 2.0, 1.0), Some(0)));

        let wr = DecisionRule {
            id: "w".into(),
            kind: "within_rel".into(),
            response: "r".into(),
            times_s: None,
            expected_order: None,
            bound: Some(0.25),
            band: None,
        };
        // group spread (max-min)/max: (3-2)/3 = 0.333 > 0.25 -> fail;
        // tight draws satisfy the bound
        assert!(!rule_predicate(&wr, &vals(3.0, 2.0, 2.5), Some(0)));
        assert!(rule_predicate(&wr, &vals(2.9, 3.0, 2.8), Some(0)));
    }

    #[test]
    fn rng_deterministic() {
        let mut a = Rng(42);
        let mut b = Rng(42);
        for _ in 0..32 {
            assert_eq!(a.next_u64(), b.next_u64());
        }
        let mut c = Rng(43);
        assert_ne!(c.next_u64(), Rng(42).next_u64());
    }

    #[test]
    fn jacobi_recovers_known_eigensystem() {
        // [[4,2],[2,3]] has eigenvalues (7±√17)/2 = 5.5615..., 1.4384...
        let a = [4.0, 2.0, 2.0, 3.0];
        let (w, v) = jacobi_eigh(&a, 2).unwrap();
        let e1 = (7.0 + 17.0f64.sqrt()) / 2.0;
        let e2 = (7.0 - 17.0f64.sqrt()) / 2.0;
        assert!((w[0] - e1).abs() < 1e-12);
        assert!((w[1] - e2).abs() < 1e-12);
        // A·v_j = λ_j·v_j for both columns of V
        for j in 0..2 {
            for i in 0..2 {
                let av = a[i * 2] * v[j] + a[i * 2 + 1] * v[2 + j];
                assert!((av - w[j] * v[i * 2 + j]).abs() < 1e-12);
            }
        }
        // nonfinite input refuses
        assert!(jacobi_eigh(&[1.0, f64::NAN, f64::NAN, 1.0], 2).is_none());
    }

    #[test]
    fn psd_factor_reproduces_spd_and_clips_indefinite() {
        // SPD input: factor reconstructs the matrix exactly, zero clipped
        let a = [4.0, 2.0, 2.0, 3.0];
        let (f, clipped) = psd_factor(&a, 2).unwrap();
        assert_eq!(clipped, 0.0);
        for i in 0..2 {
            for j in 0..2 {
                let mut rec = 0.0;
                for k in 0..2 {
                    rec += f[i * 2 + k] * f[j * 2 + k];
                }
                assert!((rec - a[i * 2 + j]).abs() < 1e-12);
            }
        }
        // indefinite input: clipped mass equals the negative eigenvalue and
        // the reconstructed matrix is the eigen-clipped projection
        let b = [1.0, 2.0, 2.0, 1.0];
        let (f, clipped) = psd_factor(&b, 2).unwrap();
        // eigenvalues of [[1,2],[2,1]] are 3 and -1
        assert!((clipped - 1.0).abs() < 1e-12);
        for i in 0..2 {
            for j in 0..2 {
                let mut rec = 0.0;
                for k in 0..2 {
                    rec += f[i * 2 + k] * f[j * 2 + k];
                }
                // projection of [[1,2],[2,1]] keeps the λ=3 eigenpair:
                // 3·(1,1)/√2 outer product = [[1.5,1.5],[1.5,1.5]]
                let want = [[1.5, 1.5], [1.5, 1.5]][i][j];
                assert!((rec - want).abs() < 1e-12);
            }
        }
    }

    fn min_spec() -> Spec {
        serde_json::from_value(json!({
            "spec": "actinv-spec-1",
            "library": {"path": "lib.npz"},
            "decay": {"primary": "d.dat"},
            "material": {"mass_g": 1.0, "basis": "wt_percent",
                         "composition": {"Fe": 99.0, "Co": 1.0}},
            "spectrum": {"structure": "fispact_709",
                         "flux_per_group": [1.0], "descending": false},
            "schedule": [{"dt": "5 s", "flux": 1.0}]
        }))
        .unwrap()
    }

    #[test]
    fn perturb_spec_preserves_composition_sum() {
        let mut spec = min_spec();
        let comp = BTreeMap::from([("Fe".to_string(), 1.1), ("Co".to_string(), -0.5)]);
        let clamps = perturb_spec(
            &mut spec,
            &[],
            1.0,
            &comp,
            &BTreeMap::new(),
            &BTreeMap::new(),
        );
        let total: f64 = spec.material.composition.values().sum();
        assert!((total - 100.0).abs() < 1e-9);
        assert_eq!(clamps, 1);
        assert!(spec.material.composition.values().all(|v| *v >= 0.0));
    }

    #[test]
    fn prepared_signature_tracks_compatible_fields() {
        let mut spec = min_spec();
        let sig0 = prepared_signature(&spec);
        // material and schedule are runtime inputs — same signature
        spec.material.composition.insert("Ni".into(), 1.0);
        spec.schedule[0].flux = 2.0;
        assert_eq!(prepared_signature(&spec), sig0);
        // a different flux vector is a different collapse — different
        // signature
        spec.spectrum.flux_per_group = vec![2.0];
        assert_ne!(prepared_signature(&spec), sig0);
        // normalization scalar alone does not change the collapse
        let mut spec2 = min_spec();
        spec2.spectrum.total = Some(5.0);
        assert_eq!(prepared_signature(&spec2), sig0);
        // uncertainty options form their own signature
        spec2.uncertainty = Some(crate::spec::UncertaintyOptions {
            covariance: crate::spec::HashedFileRef {
                path: "c.npz".into(),
                sha256: "ab".repeat(32),
            },
            responses: vec![],
            channels: vec![],
            confidence_level: 0.95,
            require_complete: false,
            voi: None,
            isomer: None,
        });
        assert_ne!(prepared_signature(&spec2), sig0);
    }

    #[test]
    fn case_resumable_requires_matching_digests() {
        let dir = std::env::temp_dir().join(format!(
            "actinv-p31-resume-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0)
        ));
        std::fs::create_dir_all(&dir).unwrap();
        let out = dir.join("out.json");
        std::fs::write(&out, "{}").unwrap();
        let out_sha = {
            let mut h = sha2::Sha256::new();
            use sha2::Digest;
            h.update(b"{}");
            format!("{:x}", h.finalize())
        };
        let cent = json!({"case_id": "c", "spec_sha256": "spec1"});
        let prev = json!({
            "case_id": "c", "status": "executed",
            "spec_sha256": "spec1", "out_sha256": out_sha,
        });
        assert!(case_resumable(&prev, &cent, &dir, None));
        // wrong status
        let mut p2 = prev.clone();
        p2["status"] = json!("failed");
        assert!(!case_resumable(&p2, &cent, &dir, None));
        // spec drift
        let mut c2 = cent.clone();
        c2["spec_sha256"] = json!("other");
        assert!(!case_resumable(&prev, &c2, &dir, None));
        // missing artifact
        std::fs::remove_file(&out).unwrap();
        assert!(!case_resumable(&prev, &cent, &dir, None));
        std::fs::write(&out, "{}").unwrap();
        // study-level robustness config must match the recorded digest:
        // the nominal spec cannot see samples, seed or channels, so a
        // config change must not silently reuse the stale block
        let mut p3 = prev.clone();
        p3["robustness"] = json!({"config_sha256": "abc"});
        assert!(case_resumable(&p3, &cent, &dir, Some("abc")));
        assert!(!case_resumable(&p3, &cent, &dir, Some("xyz")));
        // a record lacking the digest is not resumable under a
        // robustness-bearing study
        assert!(!case_resumable(&prev, &cent, &dir, Some("abc")));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn rate_scale_spec_validation() {
        let mut spec = min_spec();
        spec.options.rate_scale = Some(BTreeMap::from([("not_a_row".into(), 1.0)]));
        assert!(spec.validate().is_err());
        spec.options.rate_scale = Some(BTreeMap::from([("7".into(), 0.0)]));
        assert!(spec.validate().is_err());
        spec.options.rate_scale = Some(BTreeMap::from([("7".into(), 1.5)]));
        // remaining fields fail spec validation for unrelated reasons;
        // ensure the rate_scale check itself accepts a positive factor
        let err = spec.validate().unwrap_err();
        assert!(!err.contains("rate_scale"), "{err}");
    }
}
