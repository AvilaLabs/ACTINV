//! `actinv impurities` — impurity screening over composition ranges (P157).
//!
//! At fixed flux, coupled-mode activation is linear in the initial composition (P75b), so one solve
//! per element gives every activity, heat, clearance-index and Part 61 table sum of any composition
//! as a weighted sum, with the balance element taking up the remainder to 100 wt%. For each
//! declared endpoint and target the command reports the value with the matrix alone, at nominal and
//! at the worst case over the declared ranges, each impurity's gradient and contribution, a ranking,
//! and (with a limit) each impurity's limit alone, with the others at nominal and with the others at
//! their worst case, plus the joint scale factor.
//!
//! `dose.contact_proxy` is not linear: the material's own attenuation enters it. Its photon source
//! is linear (the weighted sum of the pure elements' grouped sources), and the proxy is evaluated at
//! the composition's attenuation by `photon::contact_proxy_of_groups`, the arithmetic a run uses. Its
//! worst case is a maximum over the corners of the range box and its limits are found by bisection.
//!
//! Every reported composition (nominal, worst-case corners, limits) is re-solved in full unless
//! `--no-verify` is given, and the result carries the predicted and solved values side by side.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use actinv_core::clearance::{table_key, LimitsTable};
use actinv_core::photon::{
    contact_proxy_of_groups, GroupedContactProxy, NuclideGroupSource, PhotonResponse,
};
use actinv_core::waste::{evaluate_component, ClassConstraint, Evaluation, WasteClass, WasteType};
use serde::Deserialize;
use serde_json::{json, Map, Value};

use crate::optimize::sha256_hex;
use crate::{budget, waste_budget};

/// Relative agreement required between composed and solved values.
pub const VERIFY_TOL: f64 = 1e-6;
/// Relative width of the bracket at which a bisection stops, in the impurity's weight fraction.
const BISECT_REL: f64 = 1e-9;
/// The most impurities with a range (min < max) the dose proxy's corner maximum is taken over.
const MAX_DOSE_VARYING: usize = 12;
const TOP_NUCLIDES: usize = 3;
const NO_RESPONSE: &str = "no response";
const INFEASIBLE: &str = "infeasible";
const UNAVAILABLE: &str = "unavailable";

type Comp = BTreeMap<String, f64>;

// ---------------------------------------------------------------------------------------------
// Input
// ---------------------------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ImpuritiesSpec {
    pub schema: String,
    /// Path (relative to this file) to an actinv run spec, or an embedded spec object. Its
    /// material is replaced; everything else is used.
    pub base_spec: Value,
    /// Element that takes up the remainder to 100 wt%.
    pub balance: String,
    /// Fixed constituents, wt%.
    #[serde(default)]
    pub matrix: BTreeMap<String, f64>,
    /// Element -> `{"nominal", "min", "max"}` in wt%, or one number for all three.
    pub impurities: BTreeMap<String, Value>,
    /// One-based result step numbers.
    pub targets: Vec<u64>,
    /// Endpoint names, or objects `{"endpoint": name, "limit": x, "class": "C"}`.
    pub endpoints: Vec<Value>,
    /// Optional `actinv-clearance-limits-1` file (relative to this file) for `clearance_index`.
    #[serde(default)]
    pub limits: Option<String>,
    /// Component geometry and rules, required by a `waste_class` endpoint.
    #[serde(default)]
    pub waste: Option<WasteInput>,
}

fn default_rules() -> String {
    "us-nrc-10cfr61.55-v1".to_string()
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WasteInput {
    #[serde(default = "default_rules")]
    pub rules: String,
    pub mass_g: f64,
    #[serde(default)]
    pub displaced_volume_cm3: Option<f64>,
    #[serde(default)]
    pub density_g_cm3: Option<f64>,
    pub waste_type: WasteType,
    pub external_tritium: Value,
}

/// An impurity's declared range in wt%.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Range {
    pub nominal: f64,
    pub min: f64,
    pub max: f64,
}

#[derive(Clone, Debug, PartialEq)]
pub enum EndpointKind {
    ActivityTotal,
    HeatTotal,
    Nuclide(String),
    ContactProxy,
    Clearance,
    WasteClass(WasteClass),
}

#[derive(Clone, Debug, PartialEq)]
pub struct Endpoint {
    pub id: String,
    pub kind: EndpointKind,
    pub limit: Option<f64>,
}

impl Endpoint {
    fn unit(&self) -> &'static str {
        match self.kind {
            EndpointKind::ActivityTotal | EndpointKind::Nuclide(_) => "Bq/g",
            EndpointKind::HeatTotal => "W/g",
            EndpointKind::ContactProxy => "Gy/h",
            EndpointKind::Clearance => "1 (IAEA clearance index)",
            EndpointKind::WasteClass(_) => "class",
        }
    }

    fn is_linear_scalar(&self) -> bool {
        matches!(
            self.kind,
            EndpointKind::ActivityTotal
                | EndpointKind::HeatTotal
                | EndpointKind::Nuclide(_)
                | EndpointKind::Clearance
        )
    }
}

fn canonical_element(raw: &str) -> Result<String, String> {
    match actinv_data::composition::material_key(raw)? {
        actinv_data::composition::MaterialKey::Element(symbol) => Ok(symbol),
        actinv_data::composition::MaterialKey::Nuclide { .. } => Err(format!(
            "constituent '{raw}' must be a natural-element symbol"
        )),
    }
}

fn canonical_weights<T>(
    raw: BTreeMap<String, T>,
    label: &str,
) -> Result<BTreeMap<String, T>, String> {
    let mut out = BTreeMap::new();
    for (name, value) in raw {
        let element = canonical_element(&name).map_err(|e| format!("{label}: {e}"))?;
        if out.insert(element.clone(), value).is_some() {
            return Err(format!("{label} lists element {element} twice"));
        }
    }
    Ok(out)
}

fn parse_range(element: &str, value: &Value) -> Result<Range, String> {
    let range = match value {
        Value::Number(n) => {
            let x = n
                .as_f64()
                .ok_or_else(|| format!("impurity {element}: wt% must be a number"))?;
            Range {
                nominal: x,
                min: x,
                max: x,
            }
        }
        Value::Object(o) => {
            if let Some(k) = o
                .keys()
                .find(|k| !matches!(k.as_str(), "nominal" | "min" | "max"))
            {
                return Err(format!(
                    "impurity {element}: unknown key '{k}' (expected nominal, min, max)"
                ));
            }
            let get = |k: &str| -> Result<f64, String> {
                o.get(k)
                    .and_then(Value::as_f64)
                    .ok_or_else(|| format!("impurity {element}: '{k}' must be a number (wt%)"))
            };
            Range {
                nominal: get("nominal")?,
                min: get("min")?,
                max: get("max")?,
            }
        }
        _ => {
            return Err(format!(
                "impurity {element}: give a number or an object with nominal, min and max"
            ))
        }
    };
    for x in [range.nominal, range.min, range.max] {
        if !(x.is_finite() && x >= 0.0) {
            return Err(format!(
                "impurity {element}: wt% must be finite and nonnegative"
            ));
        }
    }
    if range.min > range.nominal {
        return Err(format!(
            "impurity {element}: min ({}) exceeds nominal ({})",
            range.min, range.nominal
        ));
    }
    if range.nominal > range.max {
        return Err(format!(
            "impurity {element}: nominal ({}) exceeds max ({})",
            range.nominal, range.max
        ));
    }
    Ok(range)
}

fn parse_class(name: &str, text: &str) -> Result<WasteClass, String> {
    match text {
        "A" => Ok(WasteClass::A),
        "B" => Ok(WasteClass::B),
        "C" => Ok(WasteClass::C),
        other => Err(format!(
            "endpoint {name}: class must be A, B or C, got '{other}'"
        )),
    }
}

pub fn parse_endpoint(value: &Value) -> Result<Endpoint, String> {
    let (name, limit, class) = match value {
        Value::String(s) => (s.clone(), None, None),
        Value::Object(o) => {
            if let Some(k) = o
                .keys()
                .find(|k| !matches!(k.as_str(), "endpoint" | "name" | "limit" | "class"))
            {
                return Err(format!(
                    "endpoint: unknown key '{k}' (expected endpoint, limit, class)"
                ));
            }
            let name = o
                .get("endpoint")
                .or_else(|| o.get("name"))
                .and_then(Value::as_str)
                .ok_or("each endpoint object needs an 'endpoint' name")?
                .to_string();
            let limit = match o.get("limit") {
                None | Some(Value::Null) => None,
                Some(x) => Some(
                    x.as_f64()
                        .filter(|l| l.is_finite() && *l > 0.0)
                        .ok_or_else(|| {
                            format!("endpoint {name}: limit must be a positive number")
                        })?,
                ),
            };
            let class = match o.get("class") {
                None | Some(Value::Null) => None,
                Some(x) => Some(
                    x.as_str()
                        .ok_or_else(|| format!("endpoint {name}: class must be A, B or C"))?
                        .to_string(),
                ),
            };
            (name, limit, class)
        }
        _ => return Err("each endpoint must be a name or an object".into()),
    };
    if class.is_some() && name != "waste_class" {
        return Err(format!("endpoint {name}: only waste_class takes a class"));
    }
    let (id, kind, limit) = match name.as_str() {
        "activity.total" => (name.clone(), EndpointKind::ActivityTotal, limit),
        "heat.total" => (name.clone(), EndpointKind::HeatTotal, limit),
        "dose.contact_proxy" => (name.clone(), EndpointKind::ContactProxy, limit),
        "clearance_index" => (
            name.clone(),
            EndpointKind::Clearance,
            Some(limit.unwrap_or(1.0)),
        ),
        "waste_class" => {
            if limit.is_some() {
                return Err(
                    "endpoint waste_class takes no limit: the class limits are the Part 61 table sums; \
                     set class to the class you require"
                        .into(),
                );
            }
            let text =
                class.ok_or("endpoint waste_class needs \"class\": \"A\", \"B\" or \"C\"")?;
            let target = parse_class("waste_class", &text)?;
            (
                format!("waste_class:{text}"),
                EndpointKind::WasteClass(target),
                None,
            )
        }
        other => match other.strip_prefix("activity:") {
            Some(nuclide) if !nuclide.is_empty() => (
                other.to_string(),
                EndpointKind::Nuclide(nuclide.to_string()),
                limit,
            ),
            _ => {
                return Err(format!(
                    "unknown endpoint '{other}' (activity.total, heat.total, activity:<nuclide>, \
                     dose.contact_proxy, clearance_index, waste_class)"
                ))
            }
        },
    };
    Ok(Endpoint { id, kind, limit })
}

// ---------------------------------------------------------------------------------------------
// Composition frame and the scalar algebra
// ---------------------------------------------------------------------------------------------

/// The balance element, the fixed matrix and the impurity ranges.
#[derive(Clone, Debug)]
pub struct Frame {
    pub balance: String,
    pub matrix: BTreeMap<String, f64>,
    pub imps: BTreeMap<String, Range>,
}

impl Frame {
    pub fn nominal(&self) -> BTreeMap<String, f64> {
        self.imps
            .iter()
            .map(|(e, r)| (e.clone(), r.nominal))
            .collect()
    }

    /// Matrix plus the given impurity weights, balance to 100 wt%; zero entries are dropped.
    pub fn comp(&self, imp: &BTreeMap<String, f64>) -> Result<Comp, String> {
        let mut comp: Comp = BTreeMap::new();
        for (e, w) in self.matrix.iter().chain(imp.iter()) {
            *comp.entry(e.clone()).or_insert(0.0) += w;
        }
        let others: f64 = comp.values().sum();
        let rest = 100.0 - others;
        if rest.is_nan() || rest < -1e-9 {
            return Err(format!(
                "matrix and impurities sum to {others} wt%; the balance element {} needs a positive remainder",
                self.balance
            ));
        }
        comp.insert(self.balance.clone(), rest.max(0.0));
        comp.retain(|_, w| *w > 0.0);
        Ok(comp)
    }

    fn matrix_total(&self) -> f64 {
        self.matrix.values().sum()
    }
}

fn with_weight(weights: &BTreeMap<String, f64>, element: &str, x: f64) -> BTreeMap<String, f64> {
    let mut out = weights.clone();
    out.insert(element.to_string(), x);
    out
}

/// How an endpoint's value follows from a composition.
pub enum Evaluator<'a> {
    /// `offset + sum_e w_e r_e / 100`, `r` the pure elements' values.
    Linear {
        r: &'a BTreeMap<String, f64>,
        offset: f64,
    },
    /// Any function of the composition; `None` where it is unavailable.
    General(&'a dyn Fn(&Comp) -> Option<f64>),
}

impl Evaluator<'_> {
    pub fn value(&self, comp: &Comp) -> Option<f64> {
        match self {
            Evaluator::Linear { r, offset } => Some(
                *offset
                    + comp
                        .iter()
                        .map(|(e, w)| w / 100.0 * r.get(e).copied().unwrap_or(0.0))
                        .sum::<f64>(),
            ),
            Evaluator::General(f) => f(comp),
        }
    }
}

/// A limit on one impurity, or the joint factor `k`: the value when it exists, else why not.
#[derive(Clone, Debug, PartialEq)]
pub struct Limit {
    pub value: Option<f64>,
    pub status: &'static str,
    pub detail: Option<String>,
}

impl Limit {
    fn found(x: f64) -> Self {
        Limit {
            value: Some(x),
            status: "limit",
            detail: None,
        }
    }

    fn none(status: &'static str, detail: &str) -> Self {
        Limit {
            value: None,
            status,
            detail: Some(detail.to_string()),
        }
    }
}

/// The weight `x` at which a linear endpoint with slope `g` per wt% and value `v0` at `x = 0`
/// reaches `limit`, as `budget` defines it: no response when `g <= 0`, infeasible when the other
/// constituents already reach the limit. `cap` is the largest weight the balance can give up.
pub fn limit_linear(g: f64, v0: f64, cap: f64, limit: f64) -> Limit {
    if g <= 0.0 {
        Limit::none(NO_RESPONSE, "the impurity does not raise the endpoint")
    } else if v0 >= limit {
        Limit::none(INFEASIBLE, "the other constituents alone reach the limit")
    } else {
        let x = (limit - v0) / g;
        if x >= cap {
            Limit::none(
                NO_RESPONSE,
                "the limit lies beyond the composition bound (the balance element would be exhausted)",
            )
        } else {
            Limit::found(x)
        }
    }
}

/// The weight `x` in `[0, cap]` at which `f` first reaches `limit`, by bisection to `BISECT_REL`
/// in `x`; assumes `f` increases with `x`. Statuses as `limit_linear`, plus `unavailable` where `f`
/// is `None`.
pub fn limit_bisect(f: &dyn Fn(f64) -> Option<f64>, cap: f64, limit: f64) -> Limit {
    let Some(f0) = f(0.0) else {
        return Limit::none(
            UNAVAILABLE,
            "the endpoint is unavailable without the impurity",
        );
    };
    if f0 >= limit {
        return Limit::none(INFEASIBLE, "the other constituents alone reach the limit");
    }
    let Some(fc) = f(cap) else {
        return Limit::none(
            UNAVAILABLE,
            "the endpoint is unavailable at the composition bound",
        );
    };
    if fc < limit {
        return Limit::none(
            NO_RESPONSE,
            "the limit is not reached before the balance element is exhausted",
        );
    }
    let (mut lo, mut hi) = (0.0, cap);
    for _ in 0..200 {
        if hi - lo <= BISECT_REL * hi {
            break;
        }
        let mid = 0.5 * (lo + hi);
        match f(mid) {
            None => {
                return Limit::none(
                    UNAVAILABLE,
                    "the endpoint is unavailable inside the bracket",
                )
            }
            Some(v) if v >= limit => hi = mid,
            Some(_) => lo = mid,
        }
    }
    Limit::found(0.5 * (lo + hi))
}

/// Everything the algebra says about one scalar endpoint at one target.
#[derive(Clone, Debug)]
pub struct ImpurityResult {
    pub gradient: Option<f64>,
    pub contribution_nominal: Option<f64>,
    pub contribution_max: Option<f64>,
    pub alone: Option<Limit>,
    pub others_nominal: Option<Limit>,
    pub others_worst: Option<Limit>,
}

#[derive(Clone, Debug)]
pub struct ScalarResult {
    pub matrix_only: Option<f64>,
    pub nominal: Option<f64>,
    pub worst: Option<f64>,
    pub worst_corner: BTreeMap<String, &'static str>,
    pub worst_weights: BTreeMap<String, f64>,
    /// True when the worst case is exact (a linear endpoint), false for a corner maximum.
    pub worst_exact: bool,
    pub corners_evaluated: usize,
    pub unavailable_corners: usize,
    pub impurities: BTreeMap<String, ImpurityResult>,
    pub joint: Option<Limit>,
}

/// Whether an impurity's nominal contribution is below zero.
fn negative_at(res: &ScalarResult, element: &str) -> bool {
    res.impurities
        .get(element)
        .and_then(|r| r.contribution_nominal)
        .is_some_and(|c| c < 0.0)
}

fn sub(a: Option<f64>, b: Option<f64>) -> Option<f64> {
    Some(a? - b?)
}

/// The gradient of a general endpoint at nominal: a central difference over +-1% of the nominal
/// weight (one-sided at zero or at the composition bound).
fn numeric_gradient(
    frame: &Frame,
    eval: &Evaluator,
    element: &str,
    nominal: &BTreeMap<String, f64>,
) -> Result<Option<f64>, String> {
    let w = nominal[element];
    let h = if w > 0.0 { 0.01 * w } else { 1e-6 };
    let lo = (w - h).max(0.0);
    let hi_comp = frame.comp(&with_weight(nominal, element, w + h));
    let hi = if hi_comp.is_ok() { w + h } else { w };
    if hi <= lo {
        return Ok(None);
    }
    let f_hi = eval.value(&frame.comp(&with_weight(nominal, element, hi))?);
    let f_lo = eval.value(&frame.comp(&with_weight(nominal, element, lo))?);
    Ok(sub(f_hi, f_lo).map(|d| d / (hi - lo)))
}

type Corner = (
    BTreeMap<String, f64>,
    BTreeMap<String, &'static str>,
    Option<f64>,
    usize,
    usize,
);

/// The corner of the range box with the largest value. Impurities with min = max are fixed.
fn enumerate_corners(frame: &Frame, eval: &Evaluator) -> Result<Corner, String> {
    let varying: Vec<&String> = frame
        .imps
        .iter()
        .filter(|(_, r)| r.min < r.max)
        .map(|(e, _)| e)
        .collect();
    if varying.len() > MAX_DOSE_VARYING {
        return Err(format!(
            "{} impurities have a range; a corner maximum is taken over at most {MAX_DOSE_VARYING}",
            varying.len()
        ));
    }
    let weights_of = |mask: u64| -> (BTreeMap<String, f64>, BTreeMap<String, &'static str>) {
        let mut w = BTreeMap::new();
        let mut label = BTreeMap::new();
        for (e, r) in &frame.imps {
            let up = match varying.iter().position(|v| *v == e) {
                Some(j) => (mask >> j) & 1 == 1,
                None => true,
            };
            w.insert(e.clone(), if up { r.max } else { r.min });
            label.insert(e.clone(), if up { "max" } else { "min" });
        }
        (w, label)
    };
    let total: u64 = 1u64 << varying.len();
    let mut best: Option<(f64, u64)> = None;
    let mut unavailable = 0usize;
    for mask in 0..total {
        let (w, _) = weights_of(mask);
        match eval.value(&frame.comp(&w)?) {
            Some(v) => {
                if best.is_none_or(|(b, _)| v > b) {
                    best = Some((v, mask));
                }
            }
            None => unavailable += 1,
        }
    }
    let mask = best.map_or(total - 1, |(_, m)| m);
    let (w, label) = weights_of(mask);
    Ok((w, label, best.map(|(v, _)| v), unavailable, total as usize))
}

fn solve_limit(
    frame: &Frame,
    eval: &Evaluator,
    limit: f64,
    ctx: &BTreeMap<String, f64>,
    element: &str,
    gradient: Option<f64>,
) -> Result<Limit, String> {
    let cap = 100.0 - frame.matrix_total() - ctx.values().sum::<f64>();
    match eval {
        Evaluator::Linear { .. } => {
            let v0 = eval
                .value(&frame.comp(ctx)?)
                .ok_or("a linear endpoint has no value")?;
            Ok(limit_linear(gradient.unwrap_or(0.0), v0, cap, limit))
        }
        Evaluator::General(_) => {
            let f = |x: f64| -> Option<f64> {
                eval.value(&frame.comp(&with_weight(ctx, element, x)).ok()?)
            };
            Ok(limit_bisect(&f, cap, limit))
        }
    }
}

/// The joint factor `k` that scales every impurity's nominal weight so the endpoint equals `limit`.
fn solve_joint(
    frame: &Frame,
    eval: &Evaluator,
    limit: f64,
    gradients: &BTreeMap<String, Option<f64>>,
) -> Result<Limit, String> {
    let nominal = frame.nominal();
    let total: f64 = nominal.values().sum();
    if total <= 0.0 {
        return Ok(Limit::none(
            NO_RESPONSE,
            "every impurity has a nominal weight of zero",
        ));
    }
    let cap = (100.0 - frame.matrix_total()) / total;
    match eval {
        Evaluator::Linear { .. } => {
            let v_m = eval
                .value(&frame.comp(&BTreeMap::new())?)
                .ok_or("a linear endpoint has no value")?;
            let slope: f64 = nominal
                .iter()
                .map(|(e, w)| w * gradients[e].unwrap_or(0.0))
                .sum();
            Ok(limit_linear(slope, v_m, cap, limit))
        }
        Evaluator::General(_) => {
            let f = |k: f64| -> Option<f64> {
                let scaled: BTreeMap<String, f64> =
                    nominal.iter().map(|(e, w)| (e.clone(), k * w)).collect();
                eval.value(&frame.comp(&scaled).ok()?)
            };
            Ok(limit_bisect(&f, cap, limit))
        }
    }
}

pub fn analyse_scalar(
    frame: &Frame,
    eval: &Evaluator,
    limit: Option<f64>,
) -> Result<ScalarResult, String> {
    let nominal_w = frame.nominal();
    let value_of = |imp: &BTreeMap<String, f64>| -> Result<Option<f64>, String> {
        Ok(eval.value(&frame.comp(imp)?))
    };
    let matrix_only = value_of(&BTreeMap::new())?;
    let nominal = value_of(&nominal_w)?;
    let r_bal = match eval {
        Evaluator::Linear { r, .. } => r.get(&frame.balance).copied().unwrap_or(0.0),
        Evaluator::General(_) => 0.0,
    };

    let mut gradients: BTreeMap<String, Option<f64>> = BTreeMap::new();
    let mut impurities: BTreeMap<String, ImpurityResult> = BTreeMap::new();
    for (e, range) in &frame.imps {
        let g = match eval {
            Evaluator::Linear { r, .. } => Some((r.get(e).copied().unwrap_or(0.0) - r_bal) / 100.0),
            Evaluator::General(_) => numeric_gradient(frame, eval, e, &nominal_w)?,
        };
        gradients.insert(e.clone(), g);
        let (c_nominal, c_max) = match (eval, g) {
            (Evaluator::Linear { .. }, Some(g)) => (Some(range.nominal * g), Some(range.max * g)),
            _ => {
                let v0 = value_of(&with_weight(&nominal_w, e, 0.0))?;
                let v_max = value_of(&with_weight(&nominal_w, e, range.max))?;
                (sub(nominal, v0), sub(v_max, v0))
            }
        };
        impurities.insert(
            e.clone(),
            ImpurityResult {
                gradient: g,
                contribution_nominal: c_nominal,
                contribution_max: c_max,
                alone: None,
                others_nominal: None,
                others_worst: None,
            },
        );
    }

    let (worst_weights, worst_corner, worst, unavailable_corners, corners_evaluated, exact) =
        match eval {
            Evaluator::Linear { .. } => {
                let mut w = BTreeMap::new();
                let mut label = BTreeMap::new();
                for (e, range) in &frame.imps {
                    // Each impurity at the end its gradient favours; a tie goes to max.
                    let up = gradients[e].unwrap_or(0.0) >= 0.0;
                    w.insert(e.clone(), if up { range.max } else { range.min });
                    label.insert(e.clone(), if up { "max" } else { "min" });
                }
                let v = value_of(&w)?;
                (w, label, v, 0, 1, true)
            }
            Evaluator::General(_) => {
                let (w, label, v, unavailable, count) = enumerate_corners(frame, eval)?;
                (w, label, v, unavailable, count, false)
            }
        };

    let mut joint = None;
    if let Some(l) = limit {
        for e in frame.imps.keys() {
            let g = gradients[e];
            let alone = solve_limit(frame, eval, l, &BTreeMap::new(), e, g)?;
            let mut ctx_nominal = nominal_w.clone();
            ctx_nominal.remove(e);
            let mut ctx_worst = worst_weights.clone();
            ctx_worst.remove(e);
            let others_nominal = solve_limit(frame, eval, l, &ctx_nominal, e, g)?;
            let others_worst = solve_limit(frame, eval, l, &ctx_worst, e, g)?;
            if let Some(entry) = impurities.get_mut(e) {
                entry.alone = Some(alone);
                entry.others_nominal = Some(others_nominal);
                entry.others_worst = Some(others_worst);
            }
        }
        joint = Some(solve_joint(frame, eval, l, &gradients)?);
    }

    Ok(ScalarResult {
        matrix_only,
        nominal,
        worst,
        worst_corner,
        worst_weights,
        worst_exact: exact,
        corners_evaluated,
        unavailable_corners,
        impurities,
        joint,
    })
}

/// Impurity names by contribution at nominal, largest first; impurities without one come last.
pub fn ranking(result: &ScalarResult) -> Vec<String> {
    let mut v: Vec<(&String, Option<f64>)> = result
        .impurities
        .iter()
        .map(|(e, r)| (e, r.contribution_nominal))
        .collect();
    v.sort_by(|a, b| match (a.1, b.1) {
        (Some(x), Some(y)) => y.total_cmp(&x).then(a.0.cmp(b.0)),
        (Some(_), None) => std::cmp::Ordering::Less,
        (None, Some(_)) => std::cmp::Ordering::Greater,
        (None, None) => a.0.cmp(b.0),
    });
    v.into_iter().map(|(e, _)| e.clone()).collect()
}

/// The largest per-nuclide shares of an impurity's linear contribution `w (p_i - p_balance) / 100`
/// of the contribution's sign (`negative` for a contribution below zero).
pub fn top_linear(
    parts: &BTreeMap<String, BTreeMap<String, f64>>,
    balance: &str,
    element: &str,
    weight: f64,
    negative: bool,
) -> Vec<(String, f64)> {
    let empty = BTreeMap::new();
    let pi = parts.get(element).unwrap_or(&empty);
    let pb = parts.get(balance).unwrap_or(&empty);
    let names: BTreeSet<&String> = pi.keys().chain(pb.keys()).collect();
    let values = names
        .into_iter()
        .map(|n| {
            let a = pi.get(n).copied().unwrap_or(0.0);
            let b = pb.get(n).copied().unwrap_or(0.0);
            (n.clone(), weight * (a - b) / 100.0)
        })
        .collect();
    top_of(values, negative)
}

/// The shares behind a contribution: the largest positive ones, or for a negative contribution the
/// most negative ones (the nuclides the impurity's balance displacement removes), by magnitude.
fn top_of(values: BTreeMap<String, f64>, negative: bool) -> Vec<(String, f64)> {
    let mut v: Vec<(String, f64)> = values
        .into_iter()
        .filter(|(_, x)| if negative { *x < 0.0 } else { *x > 0.0 })
        .collect();
    v.sort_by(|a, b| b.1.abs().total_cmp(&a.1.abs()).then(a.0.cmp(&b.0)));
    v.truncate(TOP_NUCLIDES);
    v
}

// ---------------------------------------------------------------------------------------------
// Part 61 classes from table sums
// ---------------------------------------------------------------------------------------------

/// One normalized table sum and whether it is a mixture (more than one contributor), which makes
/// the boundary strict.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SumCheck {
    pub normalized_sum: f64,
    pub strict: bool,
}

impl SumCheck {
    pub fn passes(&self) -> bool {
        if self.strict {
            self.normalized_sum < 1.0
        } else {
            self.normalized_sum <= 1.0
        }
    }
}

const CLASSES: [WasteClass; 3] = [WasteClass::A, WasteClass::B, WasteClass::C];

/// The class from the Table 1 and Table 2 checks of each of A, B and C: the smallest class whose
/// two sums pass, else above Class C. The class is non-decreasing in every sum.
pub fn class_of_checks(checks: &[[SumCheck; 2]; 3]) -> WasteClass {
    for (i, class) in CLASSES.iter().enumerate() {
        if checks[i].iter().all(SumCheck::passes) {
            return *class;
        }
    }
    WasteClass::AboveClassC
}

fn class_name(class: WasteClass) -> &'static str {
    match class {
        WasteClass::A => "A",
        WasteClass::B => "B",
        WasteClass::C => "C",
        WasteClass::AboveClassC => "above_class_c",
        WasteClass::Unknown => "unknown",
    }
}

fn class_rank(class: WasteClass) -> u8 {
    match class {
        WasteClass::A => 0,
        WasteClass::B => 1,
        WasteClass::C => 2,
        WasteClass::AboveClassC => 3,
        WasteClass::Unknown => 4,
    }
}

/// Table 1 and Table 2 (column 1, 2, 3) sums as the evaluator reports them before normalization.
fn raw_sums(ev: &Evaluation) -> Result<[f64; 4], String> {
    let get = |target: WasteClass, table: u8, column: Option<u8>| -> Result<f64, String> {
        ev.constraints
            .iter()
            .find(|c| c.target_class == target && c.table == table && c.column == column)
            .map(|c| c.source_sum_fraction)
            .ok_or_else(|| "the evaluation lacks a class constraint".to_string())
    };
    Ok([
        get(WasteClass::C, 1, None)?,
        get(WasteClass::A, 2, Some(1))?,
        get(WasteClass::B, 2, Some(2))?,
        get(WasteClass::C, 2, Some(3))?,
    ])
}

fn constraint(
    ev: &Evaluation,
    target: WasteClass,
    table: u8,
    column: Option<u8>,
) -> Result<&ClassConstraint, String> {
    ev.constraints
        .iter()
        .find(|c| c.target_class == target && c.table == table && c.column == column)
        .ok_or_else(|| "the evaluation lacks a class constraint".to_string())
}

fn sum_check(c: &ClassConstraint) -> SumCheck {
    SumCheck {
        normalized_sum: c.normalized_sum,
        strict: c.strict,
    }
}

/// Table 1's normalization: the sum divided by 1 for Class C and by 0.1 for A and B.
fn table1_threshold(target: WasteClass) -> f64 {
    if target == WasteClass::C {
        1.0
    } else {
        0.1
    }
}

// ---------------------------------------------------------------------------------------------
// Element runs
// ---------------------------------------------------------------------------------------------

struct ElemPhoton {
    nuclides: BTreeMap<String, NuclideGroupSource>,
    /// Power (W/g) outside the group structure.
    ungrouped_power: f64,
    /// The run's own contact proxy (Gy/h), when it reports one.
    contact_proxy: Option<f64>,
}

struct ElemTarget {
    t_s: f64,
    activity: BTreeMap<String, f64>,
    heat_total: f64,
    photon: Option<ElemPhoton>,
}

/// element -> one-based step -> the quantities read from that element's run.
type ElemRuns = BTreeMap<String, BTreeMap<u64, ElemTarget>>;
/// Per step: the waste class at nominal, the worst class over the ranges, and each impurity's contribution.
type ClassSpan = (WasteClass, WasteClass, Comp);

fn extract_target(result: &Value, step: u64, photons: bool) -> Result<ElemTarget, String> {
    let st = budget::step_of(result, step)?;
    let act = st
        .get("activity_Bq_per_g")
        .and_then(Value::as_object)
        .ok_or("result step carries no activity_Bq_per_g")?;
    let mut activity = BTreeMap::new();
    for (n, v) in act {
        let a = v.as_f64().unwrap_or(0.0);
        if a.is_finite() && a > 0.0 {
            activity.insert(n.clone(), a);
        }
    }
    let heat_total = st
        .pointer("/heat_W_per_g/total")
        .and_then(Value::as_f64)
        .ok_or("result step carries no heat_W_per_g.total")?;
    let photon = if photons {
        let ps = st
            .get("photon_source")
            .filter(|v| v.is_object())
            .ok_or("result step carries no photon_source")?;
        let mut nuclides = BTreeMap::new();
        for n in ps
            .get("by_nuclide")
            .and_then(Value::as_array)
            .ok_or("photon_source carries no by_nuclide")?
        {
            let name = n
                .get("nuclide")
                .and_then(Value::as_str)
                .ok_or("photon_source nuclide carries no name")?;
            let groups = n
                .get("groups")
                .and_then(Value::as_array)
                .ok_or("photon_source nuclide carries no groups")?;
            let num = |g: &Value, k: &str| g.get(k).and_then(Value::as_f64).unwrap_or(0.0);
            nuclides.insert(
                name.to_string(),
                NuclideGroupSource {
                    photons_s_g: groups.iter().map(|g| num(g, "photons_s_g")).collect(),
                    power_W_g: groups.iter().map(|g| num(g, "power_W_g")).collect(),
                    source_power_W_g: num(n, "source_power_W_g"),
                },
            );
        }
        Some(ElemPhoton {
            nuclides,
            ungrouped_power: ps
                .get("ungrouped_power_W_g")
                .and_then(Value::as_f64)
                .unwrap_or(0.0),
            contact_proxy: ps
                .get("contact_gamma_air_dose_proxy_Gy_h")
                .and_then(Value::as_f64),
        })
    } else {
        None
    };
    Ok(ElemTarget {
        t_s: st.get("t_s").and_then(Value::as_f64).unwrap_or(f64::NAN),
        activity,
        heat_total,
        photon,
    })
}

/// A linear endpoint's value for a run at one step.
fn linear_value(kind: &EndpointKind, t: &ElemTarget, limits: &BTreeMap<String, f64>) -> f64 {
    match kind {
        EndpointKind::ActivityTotal => t.activity.values().sum(),
        EndpointKind::HeatTotal => t.heat_total,
        EndpointKind::Nuclide(n) => t.activity.get(n).copied().unwrap_or(0.0),
        EndpointKind::Clearance => clearance_parts(t, limits).values().sum(),
        EndpointKind::ContactProxy | EndpointKind::WasteClass(_) => 0.0,
    }
}

/// Per-nuclide shares of a linear endpoint, where the run resolves them.
fn linear_parts(
    kind: &EndpointKind,
    t: &ElemTarget,
    limits: &BTreeMap<String, f64>,
) -> BTreeMap<String, f64> {
    match kind {
        EndpointKind::ActivityTotal => t.activity.clone(),
        EndpointKind::Nuclide(n) => t
            .activity
            .get(n)
            .map(|a| BTreeMap::from([(n.clone(), *a)]))
            .unwrap_or_default(),
        EndpointKind::Clearance => clearance_parts(t, limits),
        _ => BTreeMap::new(),
    }
}

fn clearance_parts(t: &ElemTarget, limits: &BTreeMap<String, f64>) -> BTreeMap<String, f64> {
    t.activity
        .iter()
        .filter_map(|(n, a)| {
            table_key(n)
                .and_then(|k| limits.get(&k).copied())
                .map(|l| (n.clone(), a / l))
        })
        .collect()
}

/// The contact-proxy evaluation of compositions at one step.
struct DoseModel<'a> {
    response: Option<&'a PhotonResponse>,
    build_up: f64,
    runs: &'a ElemRuns,
    step: u64,
}

impl DoseModel<'_> {
    fn proxy(&self, comp: &Comp) -> GroupedContactProxy {
        let Some(response) = self.response else {
            return GroupedContactProxy {
                unavailable_reason: Some(
                    "the base spec's photon section names no response file".into(),
                ),
                ..GroupedContactProxy::default()
            };
        };
        let total: f64 = comp.values().sum();
        let mut fractions = BTreeMap::new();
        let mut nuclides: BTreeMap<String, NuclideGroupSource> = BTreeMap::new();
        let mut ungrouped = 0.0;
        for (e, w) in comp {
            if *w <= 0.0 {
                continue;
            }
            fractions.insert(e.clone(), w / total);
            let Some(p) = self
                .runs
                .get(e)
                .and_then(|m| m.get(&self.step))
                .and_then(|t| t.photon.as_ref())
            else {
                continue;
            };
            let f = w / 100.0;
            ungrouped += f * p.ungrouped_power;
            for (n, src) in &p.nuclides {
                let acc = nuclides
                    .entry(n.clone())
                    .or_insert_with(|| NuclideGroupSource {
                        photons_s_g: vec![0.0; src.photons_s_g.len()],
                        power_W_g: vec![0.0; src.power_W_g.len()],
                        source_power_W_g: 0.0,
                    });
                for (a, x) in acc.photons_s_g.iter_mut().zip(&src.photons_s_g) {
                    *a += f * x;
                }
                for (a, x) in acc.power_W_g.iter_mut().zip(&src.power_W_g) {
                    *a += f * x;
                }
                acc.source_power_W_g += f * src.source_power_W_g;
            }
        }
        contact_proxy_of_groups(response, &fractions, self.build_up, &nuclides, ungrouped)
    }

    fn value(&self, comp: &Comp) -> Option<f64> {
        self.proxy(comp).proxy_Gy_h
    }

    /// Per-nuclide contribution of an impurity to the proxy: the nominal composition's nuclide
    /// proxies minus those with the impurity removed.
    fn top_nuclides(
        &self,
        frame: &Frame,
        element: &str,
        negative: bool,
    ) -> Option<Vec<(String, f64)>> {
        let nominal = frame.nominal();
        let with = self.proxy(&frame.comp(&nominal).ok()?).by_nuclide_Gy_h;
        let without = self
            .proxy(&frame.comp(&with_weight(&nominal, element, 0.0)).ok()?)
            .by_nuclide_Gy_h;
        let diff: BTreeMap<String, f64> = with
            .iter()
            .map(|(n, v)| (n.clone(), v - without.get(n).copied().unwrap_or(0.0)))
            .collect();
        Some(top_of(diff, negative))
    }
}

// ---------------------------------------------------------------------------------------------
// Output assembly helpers
// ---------------------------------------------------------------------------------------------

fn opt_num(x: Option<f64>) -> Value {
    x.map(Value::from).unwrap_or(Value::Null)
}

/// A limit as JSON: its value under `key` (`wt_pct`, or `k` for the joint factor), its status, and why.
fn limit_json(l: &Limit, key: &str) -> Value {
    let mut m = Map::new();
    m.insert(key.to_string(), opt_num(l.value));
    m.insert("status".into(), Value::from(l.status));
    if let Some(d) = &l.detail {
        m.insert("detail".into(), Value::from(d.as_str()));
    }
    Value::Object(m)
}

fn top_json(top: &Option<Vec<(String, f64)>>) -> Value {
    match top {
        Some(list) => Value::Array(list.iter().map(|(n, x)| json!([n, x])).collect()),
        None => Value::Null,
    }
}

fn scalar_json(
    res: &ScalarResult,
    frame: &Frame,
    ep: &Endpoint,
    top: &BTreeMap<String, Option<Vec<(String, f64)>>>,
    reasons: &BTreeMap<&'static str, String>,
) -> Value {
    let mut imps = Map::new();
    for (e, r) in &res.impurities {
        let range = frame.imps[e];
        let mut entry = json!({
            "nominal_wt_pct": range.nominal,
            "min_wt_pct": range.min,
            "max_wt_pct": range.max,
            "gradient_per_wt_pct": opt_num(r.gradient),
            "contribution_nominal": opt_num(r.contribution_nominal),
            "contribution_at_max": opt_num(r.contribution_max),
            "top_nuclides": top_json(top.get(e).unwrap_or(&None)),
        });
        if let (Some(a), Some(n), Some(w)) = (&r.alone, &r.others_nominal, &r.others_worst) {
            entry["limits"] = json!({
                "alone": limit_json(a, "wt_pct"),
                "others_at_nominal": limit_json(n, "wt_pct"),
                "others_at_worst": limit_json(w, "wt_pct"),
            });
        }
        imps.insert(e.clone(), entry);
    }
    let method = if res.worst_exact {
        "exact for a linear endpoint: each impurity at min or max by the sign of its gradient (a tie goes to max)".to_string()
    } else {
        format!(
            "corner maximum: the largest of {} corners of the range box ({} unavailable); an interior maximum is not excluded",
            res.corners_evaluated, res.unavailable_corners
        )
    };
    let mut out = json!({
        "response": ep.id,
        "unit": ep.unit(),
        "kind": if ep.is_linear_scalar() { "linear" } else { "nonlinear" },
        "limit": opt_num(ep.limit),
        "matrix_only": opt_num(res.matrix_only),
        "nominal": opt_num(res.nominal),
        "worst_case": {
            "value": opt_num(res.worst),
            "corner": res.worst_corner,
            "method": method,
        },
        "impurities": imps,
        "ranking": ranking(res),
    });
    if let Some(j) = &res.joint {
        out["joint_k"] = limit_json(j, "value");
    }
    if !reasons.is_empty() {
        out["unavailable"] = json!(reasons);
    }
    out
}

/// Tightest limit across targets for one impurity and context (`key` is `wt_pct`, or `k` for the
/// joint factor): the first target without a limit (other than "no response") decides the status,
/// as in `budget`.
fn tightest<'a>(items: impl Iterator<Item = (u64, &'a Limit)>, key: &str) -> Value {
    let mut tight: Option<(f64, u64)> = None;
    let mut blocked: Option<(u64, &'static str)> = None;
    for (k, l) in items {
        match l.value {
            Some(x) if tight.is_none_or(|(t, _)| x < t) => tight = Some((x, k)),
            Some(_) => {}
            None if l.status != NO_RESPONSE => {
                blocked.get_or_insert((k, l.status));
            }
            None => {}
        }
    }
    let (value, step, status) = match (blocked, tight) {
        (Some((k, s)), _) => (None, Some(k), s),
        (None, Some((x, k))) => (Some(x), Some(k), "limit"),
        (None, None) => (None, None, NO_RESPONSE),
    };
    let mut m = Map::new();
    m.insert(key.to_string(), opt_num(value));
    m.insert("step".into(), step.map_or(Value::Null, Value::from));
    m.insert("status".into(), Value::from(status));
    Value::Object(m)
}

/// Each target's limit of one kind for one impurity.
fn pick_limits<'a>(
    by_step: &'a BTreeMap<u64, ScalarResult>,
    element: &str,
    which: fn(&ImpurityResult) -> &Option<Limit>,
) -> Vec<(u64, &'a Limit)> {
    by_step
        .iter()
        .filter_map(|(k, r)| which(&r.impurities[element]).as_ref().map(|l| (*k, l)))
        .collect()
}

// ---------------------------------------------------------------------------------------------
// Verification bookkeeping
// ---------------------------------------------------------------------------------------------

/// The class and the four raw table sums of an evaluation.
#[derive(Clone, Debug)]
struct ClassSnapshot {
    class: WasteClass,
    raw: [f64; 4],
}

fn snapshot(ev: &Evaluation) -> Result<ClassSnapshot, String> {
    Ok(ClassSnapshot {
        class: ev.class,
        raw: raw_sums(ev)?,
    })
}

#[derive(Clone, Debug)]
enum Expected {
    Scalar {
        predicted: Option<f64>,
        limit: Option<f64>,
    },
    Class(ClassSnapshot),
    /// A class limit: the normalized sum of one constraint at the limit composition, which is 1.
    Boundary {
        target: WasteClass,
        table: u8,
        column: Option<u8>,
        predicted: f64,
    },
}

#[derive(Clone, Debug)]
struct Request {
    id: String,
    comp: Comp,
    endpoint: usize,
    step: u64,
    role: String,
    expected: Expected,
}

fn comp_key(comp: &Comp) -> String {
    comp.iter()
        .map(|(e, w)| format!("{e}:{:016x}", w.to_bits()))
        .collect::<Vec<_>>()
        .join(",")
}

fn rel_dev(predicted: f64, solved: f64) -> f64 {
    let scale = predicted.abs().max(solved.abs());
    if scale == 0.0 {
        0.0
    } else {
        (solved - predicted).abs() / scale
    }
}

// ---------------------------------------------------------------------------------------------
// The command
// ---------------------------------------------------------------------------------------------

pub fn run_impurities(path: &str, out: Option<&str>, verify: bool) -> Result<Value, String> {
    let spath = Path::new(path);
    let dir = spath
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."));
    let text = std::fs::read_to_string(spath)
        .map_err(|e| format!("cannot read impurities spec {}: {e}", spath.display()))?;
    let doc = run_impurities_doc(&text, &dir, verify)?;
    let pretty = serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())?;
    match out {
        Some(o) => std::fs::write(o, format!("{pretty}\n"))
            .map_err(|e| format!("cannot write {o}: {e}"))?,
        None => println!("{pretty}"),
    }
    Ok(doc)
}

/// Everything a document needs before any solve.
struct Parsed {
    spec: ImpuritiesSpec,
    frame: Frame,
    endpoints: Vec<Endpoint>,
    base: Value,
    base_text: String,
    base_label: String,
}

fn parse_doc(text: &str, dir: &Path) -> Result<Parsed, String> {
    let raw: Value =
        serde_json::from_str(text).map_err(|e| format!("cannot parse impurities spec: {e}"))?;
    if raw.get("uncertainty").is_some_and(|v| !v.is_null()) {
        return Err(
            "impurities refuses uncertainty: sampled bands are a separate phase (P158) and do not compose linearly across element runs"
                .into(),
        );
    }
    let mut spec: ImpuritiesSpec =
        serde_json::from_value(raw).map_err(|e| format!("cannot parse impurities spec: {e}"))?;
    if spec.schema != "actinv-impurities-1" {
        return Err(format!(
            "impurities schema must be actinv-impurities-1, got '{}'",
            spec.schema
        ));
    }
    if spec.impurities.is_empty() {
        return Err("impurities must name at least one element".into());
    }
    if spec.targets.is_empty() || spec.targets.contains(&0) {
        return Err("targets must list one-based result step numbers".into());
    }
    if spec.targets.iter().collect::<BTreeSet<_>>().len() != spec.targets.len() {
        return Err("targets must not repeat a step".into());
    }
    if spec.endpoints.is_empty() {
        return Err("endpoints must name at least one endpoint".into());
    }

    let balance = canonical_element(&spec.balance).map_err(|e| format!("balance: {e}"))?;
    let matrix = canonical_weights(std::mem::take(&mut spec.matrix), "matrix")?;
    let raw_imps = canonical_weights(std::mem::take(&mut spec.impurities), "impurities")?;
    let mut imps = BTreeMap::new();
    for (e, v) in &raw_imps {
        imps.insert(e.clone(), parse_range(e, v)?);
    }
    for (e, w) in &matrix {
        if !(w.is_finite() && *w >= 0.0) {
            return Err(format!("{e}: wt% must be finite and nonnegative"));
        }
    }
    let mut seen = BTreeSet::from([balance.clone()]);
    for e in matrix.keys().chain(imps.keys()) {
        if e == &balance {
            return Err(format!(
                "the balance element {balance} cannot also be listed in matrix or impurities"
            ));
        }
        if !seen.insert(e.clone()) {
            return Err(format!("{e} is listed in both matrix and impurities"));
        }
    }
    let frame = Frame {
        balance,
        matrix,
        imps,
    };
    let max_total: f64 = frame.matrix_total() + frame.imps.values().map(|r| r.max).sum::<f64>();
    if max_total >= 100.0 {
        return Err(format!(
            "matrix and impurity maxima sum to {max_total} wt%; the balance element {} needs a positive remainder",
            frame.balance
        ));
    }

    let mut endpoints = Vec::new();
    for v in &spec.endpoints {
        let ep = parse_endpoint(v)?;
        if endpoints.iter().any(|e: &Endpoint| e.id == ep.id) {
            return Err(format!("endpoint {} is listed twice", ep.id));
        }
        endpoints.push(ep);
    }

    let (base_text, base_label) = match &spec.base_spec {
        Value::String(rel) => {
            let p = dir.join(rel);
            let t = std::fs::read_to_string(&p)
                .map_err(|e| format!("cannot read base spec {}: {e}", p.display()))?;
            (t, rel.clone())
        }
        v @ Value::Object(_) => (
            serde_json::to_string(v).map_err(|e| e.to_string())?,
            "<embedded>".to_string(),
        ),
        _ => return Err("base_spec must be a path string or an embedded object".into()),
    };
    let base: Value =
        serde_json::from_str(&base_text).map_err(|e| format!("cannot parse base spec: {e}"))?;
    budget::check_linear_base(&base).map_err(|e| e.replacen("budget", "impurities", 1))?;

    if endpoints
        .iter()
        .any(|e| e.kind == EndpointKind::ContactProxy)
    {
        if base.get("photon").is_none_or(Value::is_null) {
            return Err(
                "dose.contact_proxy needs the base spec's photon section (with a photon response)"
                    .into(),
            );
        }
        if base
            .pointer("/photon/missing_spectra")
            .and_then(Value::as_str)
            == Some("estimate")
        {
            return Err("dose.contact_proxy refuses photon.missing_spectra \"estimate\": estimated spectra are not part of the grouped source this command sums".into());
        }
        let varying = frame.imps.values().filter(|r| r.min < r.max).count();
        if varying > MAX_DOSE_VARYING {
            return Err(format!(
                "dose.contact_proxy takes a corner maximum over at most {MAX_DOSE_VARYING} impurities with a range; {varying} have one"
            ));
        }
    }
    if endpoints
        .iter()
        .any(|e| matches!(e.kind, EndpointKind::WasteClass(_)))
        && spec.waste.is_none()
    {
        return Err("a waste_class endpoint needs a waste section (rules, mass_g, displaced_volume_cm3 or density_g_cm3, waste_type, external_tritium)".into());
    }
    Ok(Parsed {
        spec,
        frame,
        endpoints,
        base,
        base_text,
        base_label,
    })
}

/// Everything the Part 61 endpoints need besides the element runs: one `waste budget` setup per
/// `waste_class` endpoint, which carries the rule pack, geometry and nuclide properties.
struct ClassContext {
    endpoint: usize,
    target: WasteClass,
    setup: waste_budget::Setup,
    basis: BTreeMap<String, waste_budget::Basis>,
}

fn eval_activities(
    cx: &waste_budget::Setup,
    activities_bq: &BTreeMap<String, f64>,
    h3_bq: f64,
) -> Result<Evaluation, String> {
    let mut activities = activities_bq.clone();
    if h3_bq > 0.0 {
        waste_budget::merge_activity(&mut activities, "H3", h3_bq)?;
    }
    evaluate_component(
        &cx.rules,
        cx.b.waste_type,
        cx.geometry,
        &activities,
        &cx.b.nuclide_properties,
    )
}

/// The class evaluation of a composition at a step from the element runs.
fn eval_composition(
    cx: &waste_budget::Setup,
    runs: &ElemRuns,
    step: u64,
    comp: &Comp,
) -> Result<Evaluation, String> {
    let mut act: BTreeMap<String, f64> = BTreeMap::new();
    for (e, w) in comp {
        let t = runs
            .get(e)
            .and_then(|m| m.get(&step))
            .ok_or_else(|| format!("no elemental solve for {e} at step {step}"))?;
        for (n, a) in &t.activity {
            *act.entry(n.clone()).or_default() += w / 100.0 * a * cx.b.mass_g;
        }
    }
    let (h3, _) = waste_budget::external_at(&cx.b.external_tritium, step)?;
    eval_activities(cx, &act, h3)
}

/// The class evaluation of a solved run at a step. A full solve can hold a nuclide that no element
/// run has (one at round-off level, such as Pd115 a day after irradiation), so its properties are
/// derived from the decay data here rather than left unknown.
fn eval_solved(
    cx: &waste_budget::Setup,
    t: &ElemTarget,
    step: u64,
    prepared: &actinv_core::run::PreparedRun,
) -> Result<Evaluation, String> {
    let mut act: BTreeMap<String, f64> = t
        .activity
        .iter()
        .map(|(n, a)| (n.clone(), a * cx.b.mass_g))
        .collect();
    let (h3, _) = waste_budget::external_at(&cx.b.external_tritium, step)?;
    let missing: BTreeSet<String> = act
        .keys()
        .filter(|n| !cx.b.nuclide_properties.contains_key(*n))
        .cloned()
        .collect();
    if missing.is_empty() {
        return eval_activities(cx, &act, h3);
    }
    let mut properties = cx.b.nuclide_properties.clone();
    properties.extend(crate::waste_composition_verify::derive_properties(
        prepared, &missing,
    )?);
    if h3 > 0.0 {
        waste_budget::merge_activity(&mut act, "H3", h3)?;
    }
    evaluate_component(&cx.rules, cx.b.waste_type, cx.geometry, &act, &properties)
}

fn class_json(ev: &Evaluation, target: WasteClass) -> Result<Value, String> {
    let mut binding = Vec::new();
    for c in ev.constraints_for_class(target)? {
        binding.push(json!({
            "table": c.table, "column": c.column, "normalized_sum": c.normalized_sum,
            "strict": c.strict, "passes": c.passes, "nuclides": c.contributors,
        }));
    }
    Ok(json!({
        "class": class_name(ev.class),
        "meets_target": waste_budget::class_at_most(ev.class, target),
        "target_class_constraints": binding,
    }))
}

/// The result of a class endpoint at one target.
struct ClassOutcome {
    json: Value,
    nominal: WasteClass,
    worst: WasteClass,
    /// Binding (largest nominal normalized sum) constraint of the target class: contribution per impurity.
    contributions: BTreeMap<String, f64>,
}

fn class_target(
    cx: &ClassContext,
    frame: &Frame,
    runs: &ElemRuns,
    step: u64,
    requests: &mut Vec<Request>,
) -> Result<ClassOutcome, String> {
    let s = &cx.setup;
    // Pure-element raw table sums: the linear basis of every class constraint.
    let mut pure: BTreeMap<String, [f64; 4]> = BTreeMap::new();
    for (e, by_step) in runs {
        let t = &by_step[&step];
        // A pure element at 100 wt% is the 1 g run scaled by the component mass.
        let ev = eval_solved_without_external(s, t)?;
        if ev.coverage != actinv_core::waste::Coverage::Complete {
            return Err(format!(
                "basis {e} has incomplete property coverage at step {step}: {:?}",
                ev.unknown_nuclides
            ));
        }
        pure.insert(e.clone(), raw_sums(&ev)?);
    }
    let (h3, _) = waste_budget::external_at(&s.b.external_tritium, step)?;
    let offset = if h3 > 0.0 {
        raw_sums(&eval_activities(s, &BTreeMap::new(), h3)?)?
    } else {
        [0.0; 4]
    };
    let mut results = Vec::new();
    for (k, off) in offset.iter().enumerate() {
        let r: BTreeMap<String, f64> = pure.iter().map(|(e, v)| (e.clone(), v[k])).collect();
        let eval = Evaluator::Linear {
            r: &r,
            offset: *off,
        };
        results.push(analyse_scalar(frame, &eval, None)?);
    }

    let nominal_comp = frame.comp(&frame.nominal())?;
    let nominal_ev = eval_composition(s, runs, step, &nominal_comp)?;
    let matrix_ev = eval_composition(s, runs, step, &frame.comp(&BTreeMap::new())?)?;
    requests.push(Request {
        id: "nominal".into(),
        comp: nominal_comp,
        endpoint: cx.endpoint,
        step,
        role: "nominal".into(),
        expected: Expected::Class(snapshot(&nominal_ev)?),
    });

    // Worst case: each raw sum maximised over the box at its own corner, then classified.
    let mut corner_evs = Vec::new();
    let names = [
        "table_1",
        "table_2_column_1",
        "table_2_column_2",
        "table_2_column_3",
    ];
    for (k, res) in results.iter().enumerate() {
        let comp = frame.comp(&res.worst_weights)?;
        let ev = eval_composition(s, runs, step, &comp)?;
        requests.push(Request {
            id: format!("worst_{}", names[k]),
            comp,
            endpoint: cx.endpoint,
            step,
            role: format!("worst_case_{}", names[k]),
            expected: Expected::Class(snapshot(&ev)?),
        });
        corner_evs.push(ev);
    }
    let mut checks = [[SumCheck {
        normalized_sum: 0.0,
        strict: false,
    }; 2]; 3];
    let mut worst_rows = Vec::new();
    for (i, target) in CLASSES.iter().enumerate() {
        let t1 = constraint(&corner_evs[0], *target, 1, None)?;
        let t2 = constraint(&corner_evs[1 + i], *target, 2, Some(1 + i as u8))?;
        checks[i] = [sum_check(t1), sum_check(t2)];
    }
    let worst = class_of_checks(&checks);
    let ti = CLASSES
        .iter()
        .position(|c| *c == cx.target)
        .ok_or("class endpoint target must be A, B or C")?;
    for (j, name) in [(0usize, "table_1"), (1 + ti, names[1 + ti])] {
        let table_check = checks[ti][usize::from(j != 0)];
        let c = if j == 0 {
            constraint(&corner_evs[0], cx.target, 1, None)?
        } else {
            constraint(&corner_evs[j], cx.target, 2, Some(1 + ti as u8))?
        };
        let corner: Value = json!(results[j].worst_corner);
        worst_rows.push(json!({
            "constraint": name,
            "table": c.table, "column": c.column,
            "normalized_sum_max": table_check.normalized_sum,
            "strict": table_check.strict, "passes": table_check.passes(),
            "corner": corner, "nuclides": c.contributors,
        }));
    }

    let worst_binding = worst_rows
        .iter()
        .max_by(|a, b| {
            let x = a["normalized_sum_max"].as_f64().unwrap_or(0.0);
            let y = b["normalized_sum_max"].as_f64().unwrap_or(0.0);
            x.total_cmp(&y)
        })
        .cloned()
        .unwrap_or(Value::Null);

    // Attribution to the target class's binding constraint at nominal.
    let thr = table1_threshold(cx.target);
    let n_t1 = constraint(&nominal_ev, cx.target, 1, None)?.normalized_sum;
    let n_t2 = constraint(&nominal_ev, cx.target, 2, Some(1 + ti as u8))?.normalized_sum;
    let (bk, scale, bname) = if n_t1 >= n_t2 {
        (0usize, 1.0 / thr, "table_1")
    } else {
        (1 + ti, 1.0, names[1 + ti])
    };
    let mut contributions = BTreeMap::new();
    let mut imps = Map::new();
    let mut order: Vec<(String, f64)> = Vec::new();
    for (e, r) in &results[bk].impurities {
        let g = r.gradient.map(|x| x * scale);
        let cn = r.contribution_nominal.map(|x| x * scale);
        let cm = r.contribution_max.map(|x| x * scale);
        contributions.insert(e.clone(), cn.unwrap_or(0.0));
        order.push((e.clone(), cn.unwrap_or(0.0)));
        imps.insert(
            e.clone(),
            json!({
                "gradient_normalized_sum_per_wt_pct": opt_num(g),
                "contribution_nominal": opt_num(cn),
                "contribution_at_max": opt_num(cm),
            }),
        );
    }
    order.sort_by(|a, b| b.1.total_cmp(&a.1).then(a.0.cmp(&b.0)));

    let json = json!({
        "response": format!("waste_class:{}", class_name(cx.target)),
        "target_class": class_name(cx.target),
        "nominal_class": class_name(nominal_ev.class),
        "matrix_only": class_json(&matrix_ev, cx.target)?,
        "nominal": class_json(&nominal_ev, cx.target)?,
        "worst_case": {
            "class": class_name(worst),
            "meets_target": waste_budget::class_at_most(worst, cx.target),
            "method": "each Part 61 table sum maximised over the range box at its own corner (exact, the sums are linear), then classified; a conservative bound, since the class is non-decreasing in every sum",
            "binding": worst_binding,
            "target_class_constraints": worst_rows,
        },
        "binding_constraint_at_nominal": bname,
        "impurities": imps,
        "ranking": order.iter().map(|(e, _)| e.clone()).collect::<Vec<_>>(),
    });
    Ok(ClassOutcome {
        json,
        nominal: nominal_ev.class,
        worst,
        contributions,
    })
}

/// A solved run's class evaluation without the external tritium: the linear basis of the sums.
fn eval_solved_without_external(
    cx: &waste_budget::Setup,
    t: &ElemTarget,
) -> Result<Evaluation, String> {
    let act: BTreeMap<String, f64> = t
        .activity
        .iter()
        .map(|(n, a)| (n.clone(), a * cx.b.mass_g))
        .collect();
    eval_activities(cx, &act, 0.0)
}

fn interval_json(i: &waste_budget::Interval) -> Value {
    json!({
        "status": i.status,
        "feasible": i.feasible,
        "no_response": i.no_response,
        "lower": i.lower.value,
        "upper_supremum": i.upper.value,
        "supremum_attained": !i.upper.open,
        "binding_constraint": i.upper.binding.as_ref().map(|k| json!({
            "step": k.step, "table": k.table, "column": k.column,
            "nuclides": i.upper.contributors,
        })),
    })
}

pub fn run_impurities_doc(text: &str, dir: &Path, verify: bool) -> Result<Value, String> {
    let started = std::time::Instant::now();
    let Parsed {
        spec,
        frame,
        endpoints,
        base,
        base_text,
        base_label,
    } = parse_doc(text, dir)?;

    let table = if endpoints.iter().any(|e| e.kind == EndpointKind::Clearance) {
        Some(match &spec.limits {
            Some(rel) => {
                let p = dir.join(rel);
                LimitsTable::parse(
                    std::fs::read_to_string(&p)
                        .map_err(|e| format!("cannot read limits {}: {e}", p.display()))?,
                )?
            }
            None => LimitsTable::bundled()?,
        })
    } else {
        None
    };
    let limits: BTreeMap<String, f64> = table
        .as_ref()
        .map(|t| t.limits.iter().cloned().collect())
        .unwrap_or_default();

    let want_dose = endpoints
        .iter()
        .any(|e| e.kind == EndpointKind::ContactProxy);
    let outputs: Vec<&str> = if want_dose {
        vec!["ledger", "photons"]
    } else {
        vec!["ledger"]
    };

    // One solve per element, as a pure material.
    let elements: BTreeSet<String> = std::iter::once(frame.balance.clone())
        .chain(frame.matrix.keys().cloned())
        .chain(frame.imps.keys().cloned())
        .collect();
    let mut cache = actinv_core::run::PreparedCache::new();
    let mut runs: ElemRuns = BTreeMap::new();
    let mut solves = Vec::new();
    let mut build_up = 2.0;
    for e in &elements {
        let comp = BTreeMap::from([(e.clone(), 100.0)]);
        let title = format!("impurities element {e}");
        let run_spec = budget::element_spec_with_outputs(&base, &comp, &title, &outputs)?;
        build_up = run_spec.photon.build_up_factor;
        let (res, ms) = budget::solve_spec(&run_spec, &title, &mut cache)?;
        let mut by_step = BTreeMap::new();
        for &k in &spec.targets {
            by_step.insert(k, extract_target(&res, k, want_dose)?);
        }
        solves.push(json!({"element": e, "ms": ms, "mode": res["mode"]}));
        runs.insert(e.clone(), by_step);
    }
    let response: Option<PhotonResponse> =
        cache.prepared().and_then(|p| p.photon_response()).cloned();

    let mut warnings: Vec<String> = Vec::new();
    for ep in &endpoints {
        if let EndpointKind::Nuclide(n) = &ep.kind {
            let present = runs
                .values()
                .any(|m| m.values().any(|t| t.activity.contains_key(n)));
            if !present {
                warnings.push(format!(
                    "{}: no element run has activity in {n} at any target; the endpoint is zero",
                    ep.id
                ));
            }
        }
    }

    // Part 61 contexts: nuclide properties come from the run's decay data.
    let mut class_cx: Vec<ClassContext> = Vec::new();
    for (idx, ep) in endpoints.iter().enumerate() {
        let EndpointKind::WasteClass(target) = ep.kind else {
            continue;
        };
        let w = spec.waste.as_ref().ok_or("missing waste section")?;
        let prepared = cache.prepared().ok_or("no prepared run")?;
        let mut active: BTreeSet<String> = runs
            .values()
            .flat_map(|m| m.values().flat_map(|t| t.activity.keys().cloned()))
            .collect();
        active.insert("H3".to_string());
        let properties = crate::waste_composition_verify::derive_properties(prepared, &active)?;
        let mut doc = json!({
            "schema": "actinv-waste-budget-1",
            "base_spec": base.clone(),
            "balance": frame.balance,
            "matrix": frame.matrix,
            "impurities": frame.nominal(),
            "targets": spec.targets,
            "rules": w.rules,
            "target_class": class_name(target),
            "mass_g": w.mass_g,
            "waste_type": w.waste_type,
            "nuclide_properties": properties,
            "external_tritium": w.external_tritium,
        });
        if let Some(v) = w.displaced_volume_cm3 {
            doc["displaced_volume_cm3"] = Value::from(v);
        }
        if let Some(v) = w.density_g_cm3 {
            doc["density_g_cm3"] = Value::from(v);
        }
        let setup = waste_budget::setup(&doc.to_string(), dir)?;
        let mut basis = BTreeMap::new();
        for e in &elements {
            let steps: Vec<Value> = runs[e]
                .iter()
                .map(|(k, t)| json!({"step": k, "t_s": t.t_s, "activity_Bq_per_g": t.activity}))
                .collect();
            let run = json!({"mode": "coupled", "steps": steps});
            let (b, _record) = waste_budget::element_basis(&setup, e, &run)?;
            basis.insert(e.clone(), b);
        }
        class_cx.push(ClassContext {
            endpoint: idx,
            target,
            setup,
            basis,
        });
    }

    // Per target and endpoint.
    let mut requests: Vec<Request> = Vec::new();
    let mut targets_out = Vec::new();
    let mut scalar_by_ep: BTreeMap<String, BTreeMap<u64, ScalarResult>> = BTreeMap::new();
    let mut class_by_ep: BTreeMap<String, BTreeMap<u64, ClassSpan>> = BTreeMap::new();
    for &k in &spec.targets {
        let t_s = runs[&frame.balance][&k].t_s;
        let mut eps: Vec<Value> = Vec::new();
        for (idx, ep) in endpoints.iter().enumerate() {
            match &ep.kind {
                EndpointKind::WasteClass(_) => {
                    let cx = class_cx
                        .iter()
                        .find(|c| c.endpoint == idx)
                        .ok_or("missing class context")?;
                    let out = class_target(cx, &frame, &runs, k, &mut requests)?;
                    eps.push(out.json);
                    class_by_ep
                        .entry(ep.id.clone())
                        .or_default()
                        .insert(k, (out.nominal, out.worst, out.contributions));
                }
                EndpointKind::ContactProxy => {
                    let model = DoseModel {
                        response: response.as_ref(),
                        build_up,
                        runs: &runs,
                        step: k,
                    };
                    let f = |c: &Comp| model.value(c);
                    let res = analyse_scalar(&frame, &Evaluator::General(&f), ep.limit)?;
                    let mut top = BTreeMap::new();
                    for e in frame.imps.keys() {
                        top.insert(
                            e.clone(),
                            model.top_nuclides(&frame, e, negative_at(&res, e)),
                        );
                    }
                    let mut reasons: BTreeMap<&'static str, String> = BTreeMap::new();
                    let probes: [(&'static str, Option<Comp>); 3] = [
                        ("matrix_only", frame.comp(&BTreeMap::new()).ok()),
                        ("nominal", frame.comp(&frame.nominal()).ok()),
                        ("worst_case", frame.comp(&res.worst_weights).ok()),
                    ];
                    for (name, comp) in probes {
                        if let Some(c) = comp {
                            if let Some(r) = model.proxy(&c).unavailable_reason {
                                reasons.insert(name, r);
                            }
                        }
                    }
                    eps.push(scalar_json(&res, &frame, ep, &top, &reasons));
                    register_scalar(
                        &mut requests,
                        &frame,
                        &Evaluator::General(&f),
                        ep,
                        idx,
                        k,
                        &res,
                    )?;
                    scalar_by_ep
                        .entry(ep.id.clone())
                        .or_default()
                        .insert(k, res);
                }
                _ => {
                    let r: BTreeMap<String, f64> = runs
                        .iter()
                        .map(|(e, m)| (e.clone(), linear_value(&ep.kind, &m[&k], &limits)))
                        .collect();
                    let parts: BTreeMap<String, BTreeMap<String, f64>> = runs
                        .iter()
                        .map(|(e, m)| (e.clone(), linear_parts(&ep.kind, &m[&k], &limits)))
                        .collect();
                    let eval = Evaluator::Linear { r: &r, offset: 0.0 };
                    let res = analyse_scalar(&frame, &eval, ep.limit)?;
                    let mut top: BTreeMap<String, Option<Vec<(String, f64)>>> = BTreeMap::new();
                    for (e, range) in &frame.imps {
                        let resolved = parts.values().any(|p| !p.is_empty());
                        top.insert(
                            e.clone(),
                            resolved.then(|| {
                                top_linear(
                                    &parts,
                                    &frame.balance,
                                    e,
                                    range.nominal,
                                    negative_at(&res, e),
                                )
                            }),
                        );
                    }
                    eps.push(scalar_json(&res, &frame, ep, &top, &BTreeMap::new()));
                    register_scalar(&mut requests, &frame, &eval, ep, idx, k, &res)?;
                    scalar_by_ep
                        .entry(ep.id.clone())
                        .or_default()
                        .insert(k, res);
                }
            }
        }
        targets_out.push(json!({"step": k, "t_s": t_s, "endpoints": eps}));
    }

    // Per-impurity class limits as `waste budget` reports them, over all targets.
    let mut class_limits: BTreeMap<String, Value> = BTreeMap::new();
    for cx in &class_cx {
        let ext = waste_budget::external_basis(&cx.setup)?;
        let iv = waste_budget::intervals_from_basis(&cx.setup, &cx.basis, &ext, true)?;
        let mut per: Map<String, Value> = Map::new();
        for e in frame.imps.keys() {
            let mut entry = Map::new();
            for (name, kind, interval) in &iv.by_impurity {
                if name != e {
                    continue;
                }
                let key = if *kind == "sole" {
                    "alone"
                } else {
                    "others_at_nominal"
                };
                entry.insert(key.into(), interval_json(interval));
                register_class_limit(
                    &mut requests,
                    &frame,
                    cx,
                    &runs,
                    interval,
                    e,
                    key,
                    &spec.targets,
                )?;
            }
            per.insert(e.clone(), Value::Object(entry));
        }
        register_class_limit(
            &mut requests,
            &frame,
            cx,
            &runs,
            &iv.joint,
            "",
            "joint",
            &spec.targets,
        )?;
        class_limits.insert(
            endpoints[cx.endpoint].id.clone(),
            json!({
                "impurities": per,
                "joint_scale_factor": interval_json(&iv.joint),
                "note": "supremum values over all targets, as `actinv waste budget` reports them; an open boundary is a supremum, not an attained maximum",
            }),
        );
    }

    // Summary per endpoint.
    let mut summary = Map::new();
    for ep in &endpoints {
        if let EndpointKind::WasteClass(_) = ep.kind {
            let by_step = &class_by_ep[&ep.id];
            let worst = by_step
                .iter()
                .max_by_key(|(_, v)| class_rank(v.1))
                .map(|(k, v)| json!({"class": class_name(v.1), "step": k}));
            let nominal = by_step
                .iter()
                .max_by_key(|(_, v)| class_rank(v.0))
                .map(|(k, v)| json!({"class": class_name(v.0), "step": k}));
            let mut largest: BTreeMap<String, (f64, u64)> = BTreeMap::new();
            for (k, v) in by_step {
                for (e, c) in &v.2 {
                    let entry = largest.entry(e.clone()).or_insert((*c, *k));
                    if *c > entry.0 {
                        *entry = (*c, *k);
                    }
                }
            }
            summary.insert(
                ep.id.clone(),
                json!({
                    "worst": worst,
                    "worst_nominal": nominal,
                    "ranking": rank_largest(&largest),
                    "impurity_class_limits": class_limits.get(&ep.id),
                }),
            );
            continue;
        }
        let by_step = &scalar_by_ep[&ep.id];
        let mut largest: BTreeMap<String, (f64, u64)> = BTreeMap::new();
        for (k, r) in by_step {
            for (e, imp) in &r.impurities {
                if let Some(c) = imp.contribution_nominal {
                    let entry = largest.entry(e.clone()).or_insert((c, *k));
                    if c > entry.0 {
                        *entry = (c, *k);
                    }
                }
            }
        }
        let worst_value = by_step
            .iter()
            .filter_map(|(k, r)| r.worst.map(|v| (*k, v)))
            .max_by(|a, b| a.1.total_cmp(&b.1))
            .map(|(k, v)| json!({"value": v, "step": k}));
        let mut entry = json!({
            "worst": worst_value,
            "ranking": rank_largest(&largest),
        });
        if ep.limit.is_some() {
            let mut tight = Map::new();
            for (kind, which) in [
                (
                    "alone",
                    (|r: &ImpurityResult| &r.alone) as fn(&ImpurityResult) -> &Option<Limit>,
                ),
                ("others_at_nominal", |r: &ImpurityResult| &r.others_nominal),
                ("others_at_worst", |r: &ImpurityResult| &r.others_worst),
            ] {
                let mut per = Map::new();
                for e in frame.imps.keys() {
                    per.insert(
                        e.clone(),
                        tightest(pick_limits(by_step, e, which).into_iter(), "wt_pct"),
                    );
                }
                tight.insert(kind.to_string(), Value::Object(per));
            }
            entry["tightest_limit"] = Value::Object(tight);
            let joint: Vec<(u64, &Limit)> = by_step
                .iter()
                .filter_map(|(k, r)| r.joint.as_ref().map(|l| (*k, l)))
                .collect();
            entry["joint_k"] = tightest(joint.into_iter(), "value");
        }
        summary.insert(ep.id.clone(), entry);
    }

    let verification = if verify {
        verify_requests(
            &requests,
            &endpoints,
            &base,
            &outputs,
            &spec.targets,
            &limits,
            &class_cx,
            &mut cache,
        )?
    } else {
        json!({"skipped": true, "verified": false})
    };

    let ranges: BTreeMap<String, Value> = frame
        .imps
        .iter()
        .map(|(e, r)| {
            let range = json!({"nominal": r.nominal, "min": r.min, "max": r.max});
            (e.clone(), range)
        })
        .collect();
    let endpoint_ids: Vec<String> = endpoints.iter().map(|e| e.id.clone()).collect();
    let mut result = json!({
        "schema": "actinv-impurities-result-1",
        "impurities_sha256": sha256_hex(text.as_bytes()),
        "base_spec": base_label,
        "base_spec_sha256": sha256_hex(base_text.as_bytes()),
        "solver": {"mode": "coupled", "prune": "reach", "outputs": outputs},
        "balance": frame.balance,
        "matrix": frame.matrix,
        "impurities": ranges,
        "endpoints": endpoint_ids,
        "element_solves": solves,
        "targets": targets_out,
        "summary": summary,
        "verification": verification,
        "elapsed_ms": started.elapsed().as_millis() as u64,
    });
    if let Some(t) = &table {
        result["clearance_limits"] = json!({"source": t.source, "sha256": t.sha256});
    }
    if !warnings.is_empty() {
        result["warnings"] = json!(warnings);
    }
    Ok(result)
}

fn rank_largest(largest: &BTreeMap<String, (f64, u64)>) -> Vec<Value> {
    let mut v: Vec<(&String, &(f64, u64))> = largest.iter().collect();
    v.sort_by(|a, b| b.1 .0.total_cmp(&a.1 .0).then(a.0.cmp(b.0)));
    v.into_iter()
        .map(|(e, (c, k))| json!({"impurity": e, "largest_contribution": c, "step": k}))
        .collect()
}

/// Queue the full-solve checks of a scalar endpoint: nominal, the worst-case corner, and every
/// limit that exists.
fn register_scalar(
    requests: &mut Vec<Request>,
    frame: &Frame,
    eval: &Evaluator,
    ep: &Endpoint,
    endpoint: usize,
    step: u64,
    res: &ScalarResult,
) -> Result<(), String> {
    let nominal_w = frame.nominal();
    let mut push = |id: String, comp: Comp, role: String, limit: Option<f64>| {
        let predicted = eval.value(&comp);
        requests.push(Request {
            id,
            comp,
            endpoint,
            step,
            role,
            expected: Expected::Scalar { predicted, limit },
        });
    };
    push(
        "nominal".into(),
        frame.comp(&nominal_w)?,
        "nominal".into(),
        None,
    );
    push(
        format!("worst@{}", ep.id),
        frame.comp(&res.worst_weights)?,
        "worst_case".into(),
        None,
    );
    let Some(l) = ep.limit else {
        return Ok(());
    };
    for (e, r) in &res.impurities {
        let mut ctx_nominal = nominal_w.clone();
        ctx_nominal.remove(e);
        let mut ctx_worst = res.worst_weights.clone();
        ctx_worst.remove(e);
        let contexts: [(&str, &Option<Limit>, BTreeMap<String, f64>); 3] = [
            ("alone", &r.alone, BTreeMap::new()),
            ("others_at_nominal", &r.others_nominal, ctx_nominal),
            ("others_at_worst_case", &r.others_worst, ctx_worst),
        ];
        for (name, limit, ctx) in contexts {
            if let Some(Limit { value: Some(x), .. }) = limit {
                push(
                    format!("limit_{e}_{name}@{}", ep.id),
                    frame.comp(&with_weight(&ctx, e, *x))?,
                    format!("limit_{e}_{name}"),
                    Some(l),
                );
            }
        }
    }
    if let Some(Limit { value: Some(k), .. }) = &res.joint {
        let scaled: BTreeMap<String, f64> =
            nominal_w.iter().map(|(e, w)| (e.clone(), k * w)).collect();
        push(
            format!("joint_k@{}", ep.id),
            frame.comp(&scaled)?,
            "joint_k".into(),
            Some(l),
        );
    }
    Ok(())
}

/// Queue the check that a class limit's composition sits on its binding constraint.
#[allow(clippy::too_many_arguments)]
fn register_class_limit(
    requests: &mut Vec<Request>,
    frame: &Frame,
    cx: &ClassContext,
    runs: &ElemRuns,
    interval: &waste_budget::Interval,
    element: &str,
    kind: &str,
    targets: &[u64],
) -> Result<(), String> {
    let (Some(key), true) = (interval.upper.binding, interval.feasible) else {
        return Ok(());
    };
    if interval.status != "limit" || interval.no_response || !targets.contains(&key.step) {
        return Ok(());
    }
    let x = interval.upper.value;
    let nominal = frame.nominal();
    let imp: BTreeMap<String, f64> = match kind {
        "alone" => BTreeMap::from([(element.to_string(), x)]),
        "joint" => nominal.iter().map(|(e, w)| (e.clone(), x * w)).collect(),
        _ => with_weight(&nominal, element, x),
    };
    let Ok(comp) = frame.comp(&imp) else {
        return Ok(());
    };
    let ev = eval_composition(&cx.setup, runs, key.step, &comp)?;
    let predicted = constraint(&ev, cx.target, key.table, key.column)?.normalized_sum;
    requests.push(Request {
        id: format!("class_limit_{kind}_{element}@{}", key.step),
        comp,
        endpoint: cx.endpoint,
        step: key.step,
        role: format!("class_limit_{kind}_{element}"),
        expected: Expected::Boundary {
            target: cx.target,
            table: key.table,
            column: key.column,
            predicted,
        },
    });
    Ok(())
}

/// Re-solve every distinct composition in full and compare each queued prediction.
#[allow(clippy::too_many_arguments)]
fn verify_requests(
    requests: &[Request],
    endpoints: &[Endpoint],
    base: &Value,
    outputs: &[&str],
    targets: &[u64],
    limits: &BTreeMap<String, f64>,
    class_cx: &[ClassContext],
    cache: &mut actinv_core::run::PreparedCache,
) -> Result<Value, String> {
    let mut order: Vec<String> = Vec::new();
    let mut groups: BTreeMap<String, Vec<&Request>> = BTreeMap::new();
    for r in requests {
        let key = comp_key(&r.comp);
        if !groups.contains_key(&key) {
            order.push(key.clone());
        }
        groups.entry(key).or_default().push(r);
    }
    let want_photons = outputs.contains(&"photons");
    let mut points = Vec::new();
    let mut worst: f64 = 0.0;
    let mut all_pass = true;
    for key in &order {
        let group = &groups[key];
        let first = group[0];
        let title = format!("impurities verify {}", first.id);
        let spec = budget::element_spec_with_outputs(base, &first.comp, &title, outputs)?;
        let (res, ms) = budget::solve_spec(&spec, &title, cache)?;
        let mut solved: BTreeMap<u64, ElemTarget> = BTreeMap::new();
        for &k in targets {
            solved.insert(k, extract_target(&res, k, want_photons)?);
        }
        let prepared = cache.prepared().ok_or("no prepared run")?;
        let mut checks = Vec::new();
        let mut point_dev: f64 = 0.0;
        let mut point_pass = true;
        for r in group {
            let ep = &endpoints[r.endpoint];
            let t = &solved[&r.step];
            let (entry, dev, pass) = match &r.expected {
                Expected::Scalar { predicted, limit } => {
                    let s = if ep.kind == EndpointKind::ContactProxy {
                        t.photon.as_ref().and_then(|p| p.contact_proxy)
                    } else {
                        Some(linear_value(&ep.kind, t, limits))
                    };
                    let mut dev = match (predicted, s) {
                        (Some(p), Some(s)) => rel_dev(*p, s),
                        (None, None) => 0.0,
                        _ => 1.0,
                    };
                    if let (Some(l), Some(s)) = (limit, s) {
                        dev = dev.max(rel_dev(*l, s));
                    }
                    (
                        json!({
                            "endpoint": ep.id, "step": r.step, "role": r.role,
                            "predicted": opt_num(*predicted), "solved": opt_num(s),
                            "limit": opt_num(*limit), "rel_dev": dev, "pass": dev <= VERIFY_TOL,
                        }),
                        dev,
                        dev <= VERIFY_TOL,
                    )
                }
                Expected::Class(p) => {
                    let cx = class_cx
                        .iter()
                        .find(|c| c.endpoint == r.endpoint)
                        .ok_or("missing class context")?;
                    let sv = snapshot(&eval_solved(&cx.setup, t, r.step, prepared)?)?;
                    let dev = p
                        .raw
                        .iter()
                        .zip(&sv.raw)
                        .map(|(a, b)| rel_dev(*a, *b))
                        .fold(0.0, f64::max);
                    let pass = dev <= VERIFY_TOL && p.class == sv.class;
                    (
                        json!({
                            "endpoint": ep.id, "step": r.step, "role": r.role,
                            "predicted_class": class_name(p.class), "solved_class": class_name(sv.class),
                            "predicted_table_sums": p.raw, "solved_table_sums": sv.raw,
                            "rel_dev": dev, "pass": pass,
                        }),
                        dev,
                        pass,
                    )
                }
                Expected::Boundary {
                    target,
                    table,
                    column,
                    predicted,
                } => {
                    let cx = class_cx
                        .iter()
                        .find(|c| c.endpoint == r.endpoint)
                        .ok_or("missing class context")?;
                    let ev = eval_solved(&cx.setup, t, r.step, prepared)?;
                    let s = constraint(&ev, *target, *table, *column)?.normalized_sum;
                    let dev = rel_dev(*predicted, s).max((s - 1.0).abs());
                    (
                        json!({
                            "endpoint": ep.id, "step": r.step, "role": r.role,
                            "table": table, "column": column,
                            "predicted_normalized_sum": predicted, "solved_normalized_sum": s,
                            "rel_dev": dev, "pass": dev <= VERIFY_TOL,
                        }),
                        dev,
                        dev <= VERIFY_TOL,
                    )
                }
            };
            point_dev = point_dev.max(dev);
            point_pass &= pass;
            checks.push(entry);
        }
        worst = worst.max(point_dev);
        all_pass &= point_pass;
        points.push(json!({
            "id": first.id,
            "composition_wt_pct": first.comp,
            "checks": checks,
            "max_rel_dev": point_dev,
            "pass": point_pass,
            "ms": ms,
        }));
    }
    Ok(json!({
        "tolerance_rel": VERIFY_TOL,
        "points": points,
        "max_rel_dev": worst,
        "verified": all_pass && worst <= VERIFY_TOL,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn m(pairs: &[(&str, f64)]) -> BTreeMap<String, f64> {
        pairs.iter().map(|(k, v)| (k.to_string(), *v)).collect()
    }

    fn range(nominal: f64, min: f64, max: f64) -> Range {
        Range { nominal, min, max }
    }

    /// Fe balance, 1 wt% Ni matrix, Co and Nb impurities with ranges.
    fn frame() -> Frame {
        Frame {
            balance: "Fe".into(),
            matrix: m(&[("Ni", 1.0)]),
            imps: BTreeMap::from([
                ("Co".to_string(), range(0.0005, 0.0, 0.002)),
                ("Nb".to_string(), range(0.001, 0.0005, 0.004)),
                ("Al".to_string(), range(0.01, 0.0, 0.05)),
            ]),
        }
    }

    fn r() -> BTreeMap<String, f64> {
        m(&[
            ("Fe", 0.01),
            ("Ni", 10.0),
            ("Co", 1000.0),
            ("Nb", 2000.0),
            ("Al", 0.001),
        ])
    }

    #[test]
    fn range_parsing_and_refusals() {
        assert_eq!(
            parse_range("Co", &json!(0.01)).unwrap(),
            range(0.01, 0.01, 0.01)
        );
        assert_eq!(
            parse_range("Co", &json!({"nominal": 0.01, "min": 0.0, "max": 0.02})).unwrap(),
            range(0.01, 0.0, 0.02)
        );
        let min_over = parse_range("Co", &json!({"nominal": 0.01, "min": 0.02, "max": 0.03}));
        assert!(min_over.unwrap_err().contains("min"));
        let nominal_over = parse_range("Co", &json!({"nominal": 0.05, "min": 0.0, "max": 0.03}));
        assert!(nominal_over.unwrap_err().contains("nominal"));
        assert!(parse_range("Co", &json!({"nominal": 0.01, "min": 0.0})).is_err());
        assert!(parse_range(
            "Co",
            &json!({"nominal": 0.01, "min": 0.0, "max": 1.0, "x": 1})
        )
        .is_err());
        assert!(parse_range("Co", &json!(-1.0)).is_err());
        assert!(parse_range("Co", &json!("x")).is_err());
    }

    #[test]
    fn endpoint_parsing_and_refusals() {
        let ok = |v: Value| parse_endpoint(&v).unwrap();
        assert_eq!(
            ok(json!("activity.total")).kind,
            EndpointKind::ActivityTotal
        );
        assert_eq!(
            ok(json!({"endpoint": "heat.total", "limit": 1e-3})).limit,
            Some(1e-3)
        );
        assert_eq!(
            ok(json!("activity:Nb94")).kind,
            EndpointKind::Nuclide("Nb94".into())
        );
        assert_eq!(ok(json!("clearance_index")).limit, Some(1.0));
        let wc = ok(json!({"endpoint": "waste_class", "class": "C"}));
        assert_eq!(wc.kind, EndpointKind::WasteClass(WasteClass::C));
        assert_eq!(wc.id, "waste_class:C");
        assert!(parse_endpoint(&json!("magnetism")).is_err());
        assert!(parse_endpoint(&json!("activity:")).is_err());
        // a limit on waste_class, a class on another endpoint, a missing or bad class
        assert!(
            parse_endpoint(&json!({"endpoint": "waste_class", "class": "C", "limit": 1.0}))
                .is_err()
        );
        assert!(parse_endpoint(&json!({"endpoint": "heat.total", "class": "C"})).is_err());
        assert!(parse_endpoint(&json!("waste_class")).is_err());
        assert!(parse_endpoint(&json!({"endpoint": "waste_class", "class": "D"})).is_err());
        assert!(parse_endpoint(&json!({"endpoint": "heat.total", "limit": -1.0})).is_err());
        assert!(parse_endpoint(&json!({"endpoint": "heat.total", "extra": 1})).is_err());
    }

    fn doc(extra: Value) -> String {
        let mut v = json!({
            "schema": "actinv-impurities-1", "base_spec": {"spec": "actinv-spec-1"},
            "balance": "Fe", "matrix": {"Cr": 9.0},
            "impurities": {"Co": {"nominal": 0.01, "min": 0.0, "max": 0.02}},
            "targets": [1], "endpoints": ["activity.total"],
        });
        for (k, x) in extra.as_object().unwrap() {
            v[k] = x.clone();
        }
        v.to_string()
    }

    #[test]
    fn documents_with_nonlinear_or_unsupported_content_are_refused_before_any_solve() {
        let dir = Path::new(".");
        let refused = |extra: Value, needle: &str| {
            let err = parse_doc(&doc(extra), dir)
                .err()
                .expect("accepted a bad document");
            assert!(err.contains(needle), "{err}");
        };
        refused(json!({"uncertainty": {"covariance": {}}}), "uncertainty");
        refused(json!({"endpoints": ["magnetism"]}), "unknown endpoint");
        refused(
            json!({"impurities": {"Co": {"nominal": 0.01, "min": 0.02, "max": 0.03}}}),
            "min",
        );
        refused(
            json!({"endpoints": [{"endpoint": "waste_class", "class": "C", "limit": 1.0}]}),
            "waste_class",
        );
        refused(
            json!({"endpoints": ["dose.contact_proxy"]}),
            "photon section",
        );
        refused(json!({"endpoints": ["waste_class:C"]}), "unknown endpoint");
        refused(
            json!({"endpoints": [{"endpoint": "waste_class", "class": "C"}]}),
            "waste section",
        );
        refused(json!({"schema": "x"}), "schema");
        refused(json!({"targets": [0]}), "targets");
        refused(json!({"matrix": {"Fe": 1.0}}), "balance");
        refused(json!({"matrix": {"Co": 1.0}}), "both");
        refused(json!({"matrix": {"Cr": 99.99}}), "balance");
        refused(json!({"endpoints": ["heat.total", "heat.total"]}), "twice");
        refused(
            json!({"base_spec": {"spec": "actinv-spec-1", "self_shielding": {"table": {}}}}),
            "self_shielding",
        );
        refused(
            json!({"base_spec": {"spec": "actinv-spec-1", "options": {"screen": {"bmin_atoms_per_g": 1e-4}}}}),
            "screen",
        );
        refused(
            json!({"base_spec": {"spec": "actinv-spec-1", "schedule": [{"dt": "1 s", "flux": 1.0, "feed": {"Fe56": 1.0}}]}}),
            "feed",
        );
        // the same document with none of the above parses
        assert!(parse_doc(&doc(json!({})), dir).is_ok());
    }

    #[test]
    fn dose_without_a_photon_section_or_with_too_many_ranges_is_refused() {
        let dir = Path::new(".");
        let with_photon = json!({
            "endpoints": ["dose.contact_proxy"],
            "base_spec": {"spec": "actinv-spec-1", "photon": {"response": {"path": "p", "sha256": "s"}}},
        });
        assert!(parse_doc(&doc(with_photon), dir).is_ok());
        let mut imps = serde_json::Map::new();
        for e in [
            "Al", "B", "C", "Ca", "Co", "Cu", "Eu", "K", "Mg", "Mn", "Mo", "N", "Na",
        ] {
            imps.insert(
                e.into(),
                json!({"nominal": 0.001, "min": 0.0, "max": 0.002}),
            );
        }
        let many = json!({
            "endpoints": ["dose.contact_proxy"], "impurities": imps,
            "base_spec": {"spec": "actinv-spec-1", "photon": {"response": {"path": "p", "sha256": "s"}}},
        });
        let err = parse_doc(&doc(many), dir).err().unwrap();
        assert!(err.contains("at most 12"), "{err}");
    }

    #[test]
    fn linear_gradient_contribution_and_worst_corner_by_sign() {
        let f = frame();
        let rv = r();
        let res = analyse_scalar(
            &f,
            &Evaluator::Linear {
                r: &rv,
                offset: 0.0,
            },
            None,
        )
        .unwrap();
        // gradient = (r_i - r_balance) / 100
        let g_co = (1000.0 - 0.01) / 100.0;
        let g_al = (0.001 - 0.01) / 100.0;
        assert!((res.impurities["Co"].gradient.unwrap() - g_co).abs() < 1e-12);
        assert!((res.impurities["Al"].gradient.unwrap() - g_al).abs() < 1e-15);
        // contribution at nominal and at the range maximum
        assert!((res.impurities["Co"].contribution_nominal.unwrap() - 0.0005 * g_co).abs() < 1e-12);
        assert!((res.impurities["Co"].contribution_max.unwrap() - 0.002 * g_co).abs() < 1e-12);
        // values
        let nominal = f.comp(&f.nominal()).unwrap();
        let direct: f64 = nominal.iter().map(|(e, w)| w / 100.0 * rv[e]).sum();
        assert!((res.nominal.unwrap() - direct).abs() < 1e-12);
        let matrix = (99.0 * 0.01 + 1.0 * 10.0) / 100.0;
        assert!((res.matrix_only.unwrap() - matrix).abs() < 1e-14);
        // worst corner: Co and Nb at max (positive gradient), Al at min (negative)
        assert_eq!(res.worst_corner["Co"], "max");
        assert_eq!(res.worst_corner["Nb"], "max");
        assert_eq!(res.worst_corner["Al"], "min");
        assert!(res.worst_exact);
        assert_eq!(res.worst_weights["Al"], 0.0);
        // no corner exceeds the reported worst value
        for mask in 0..8u32 {
            let w: BTreeMap<String, f64> = f
                .imps
                .iter()
                .enumerate()
                .map(|(j, (e, rg))| {
                    (
                        e.clone(),
                        if (mask >> j) & 1 == 1 { rg.max } else { rg.min },
                    )
                })
                .collect();
            let v = Evaluator::Linear {
                r: &rv,
                offset: 0.0,
            }
            .value(&f.comp(&w).unwrap())
            .unwrap();
            assert!(v <= res.worst.unwrap() + 1e-15, "{v} > {:?}", res.worst);
        }
    }

    #[test]
    fn a_zero_gradient_ties_to_max() {
        let f = frame();
        let rv = m(&[
            ("Fe", 1.0),
            ("Co", 1.0),
            ("Nb", 1.0),
            ("Al", 1.0),
            ("Ni", 1.0),
        ]);
        let res = analyse_scalar(
            &f,
            &Evaluator::Linear {
                r: &rv,
                offset: 0.0,
            },
            None,
        )
        .unwrap();
        assert!(res.worst_corner.values().all(|c| *c == "max"));
    }

    #[test]
    fn linear_limits_in_three_contexts_and_their_statuses() {
        let f = frame();
        let rv = r();
        let ev = Evaluator::Linear {
            r: &rv,
            offset: 0.0,
        };
        let l = 0.5;
        let res = analyse_scalar(&f, &ev, Some(l)).unwrap();
        assert!(res.impurities["Co"].gradient.is_some());
        // alone: only Co present
        let alone = res.impurities["Co"].alone.clone().unwrap();
        assert_eq!(alone.status, "limit");
        let comp = f.comp(&m(&[("Co", alone.value.unwrap())])).unwrap();
        assert!((ev.value(&comp).unwrap() - l).abs() < 1e-12);
        // others at nominal
        let on = res.impurities["Co"].others_nominal.clone().unwrap();
        let mut w = f.nominal();
        w.insert("Co".into(), on.value.unwrap());
        assert!((ev.value(&f.comp(&w).unwrap()).unwrap() - l).abs() < 1e-12);
        // others at their worst case: Nb at max, Al at min
        let ow = res.impurities["Co"].others_worst.clone().unwrap();
        let mut w = res.worst_weights.clone();
        w.insert("Co".into(), ow.value.unwrap());
        assert!((ev.value(&f.comp(&w).unwrap()).unwrap() - l).abs() < 1e-12);
        assert_eq!(w["Nb"], 0.004);
        assert_eq!(w["Al"], 0.0);
        // tighter with worse others
        assert!(ow.value.unwrap() < on.value.unwrap() && on.value.unwrap() < alone.value.unwrap());
        // joint factor k
        let k = res.joint.clone().unwrap();
        assert_eq!(k.status, "limit");
        let scaled: BTreeMap<String, f64> = f
            .nominal()
            .iter()
            .map(|(e, x)| (e.clone(), k.value.unwrap() * x))
            .collect();
        assert!((ev.value(&f.comp(&scaled).unwrap()).unwrap() - l).abs() < 1e-12);
        // Al is below the balance: no response
        let al = res.impurities["Al"].alone.clone().unwrap();
        assert_eq!(al.status, NO_RESPONSE);
        assert_eq!(al.value, None);
    }

    #[test]
    fn linear_limits_infeasible_when_the_others_already_reach_the_limit() {
        let f = frame();
        let rv = r();
        let ev = Evaluator::Linear {
            r: &rv,
            offset: 0.0,
        };
        // matrix alone is 0.1099 > 0.05
        let res = analyse_scalar(&f, &ev, Some(0.05)).unwrap();
        for e in ["Co", "Nb"] {
            assert_eq!(res.impurities[e].alone.as_ref().unwrap().status, INFEASIBLE);
            assert_eq!(
                res.impurities[e].others_nominal.as_ref().unwrap().status,
                INFEASIBLE
            );
        }
        assert_eq!(res.joint.as_ref().unwrap().status, INFEASIBLE);
        assert_eq!(res.joint.as_ref().unwrap().value, None);
    }

    #[test]
    fn linear_limit_helper_boundaries() {
        assert_eq!(limit_linear(0.0, 0.0, 10.0, 1.0).status, NO_RESPONSE);
        assert_eq!(limit_linear(-1.0, 0.0, 10.0, 1.0).status, NO_RESPONSE);
        assert_eq!(limit_linear(1.0, 1.0, 10.0, 1.0).status, INFEASIBLE);
        assert_eq!(limit_linear(1.0, 0.5, 10.0, 1.0).value, Some(0.5));
        // beyond the composition bound
        assert_eq!(limit_linear(1.0, 0.0, 0.5, 1.0).status, NO_RESPONSE);
    }

    #[test]
    fn bisection_on_a_monotone_nonlinear_function() {
        // f(x) = 3 x + x^2 / 10 reaches 7 at x = 2 (3*2 + 0.4 = 6.4 -> adjust)
        let f = |x: f64| -> Option<f64> { Some(3.0 * x + x * x / 10.0) };
        let want = {
            // solve x^2/10 + 3x - 7 = 0
            let (a, b, c): (f64, f64, f64) = (0.1, 3.0, -7.0);
            (-b + (b * b - 4.0 * a * c).sqrt()) / (2.0 * a)
        };
        let got = limit_bisect(&f, 50.0, 7.0);
        assert_eq!(got.status, "limit");
        assert!(
            (got.value.unwrap() - want).abs() <= 2e-9 * want,
            "{got:?} vs {want}"
        );
        // not reached before the bound
        assert_eq!(limit_bisect(&f, 1.0, 7.0).status, NO_RESPONSE);
        // already reached with nothing added
        let g = |x: f64| -> Option<f64> { Some(8.0 + x) };
        assert_eq!(limit_bisect(&g, 50.0, 7.0).status, INFEASIBLE);
        // unavailable
        let h = |_: f64| -> Option<f64> { None };
        assert_eq!(limit_bisect(&h, 50.0, 7.0).status, UNAVAILABLE);
    }

    #[test]
    fn a_general_evaluator_gets_a_corner_maximum_and_bisected_limits() {
        // a saturating response: v = 100 * s / (s + 0.01), s = 1000 Co + 2000 Nb (wt fractions)
        let f = frame();
        let func = |c: &Comp| -> Option<f64> {
            let s = 1000.0 * c.get("Co").copied().unwrap_or(0.0) / 100.0
                + 2000.0 * c.get("Nb").copied().unwrap_or(0.0) / 100.0;
            Some(100.0 * s / (s + 0.01))
        };
        let ev = Evaluator::General(&func);
        let res = analyse_scalar(&f, &ev, Some(80.0)).unwrap();
        assert!(!res.worst_exact);
        assert_eq!(res.corners_evaluated, 8);
        assert_eq!(res.worst_corner["Co"], "max");
        assert_eq!(res.worst_corner["Nb"], "max");
        // brute force agrees
        let mut best = f64::MIN;
        for mask in 0..8u32 {
            let w: BTreeMap<String, f64> = f
                .imps
                .iter()
                .enumerate()
                .map(|(j, (e, rg))| {
                    (
                        e.clone(),
                        if (mask >> j) & 1 == 1 { rg.max } else { rg.min },
                    )
                })
                .collect();
            best = best.max(func(&f.comp(&w).unwrap()).unwrap());
        }
        assert!((res.worst.unwrap() - best).abs() < 1e-12);
        // contribution by difference, gradient by central difference
        let co = &res.impurities["Co"];
        let v0 = func(&f.comp(&with_weight(&f.nominal(), "Co", 0.0)).unwrap()).unwrap();
        assert!((co.contribution_nominal.unwrap() - (res.nominal.unwrap() - v0)).abs() < 1e-12);
        assert!(co.gradient.unwrap() > 0.0);
        // a limit is found by bisection and lands on the limit
        let lim = co.others_nominal.clone().unwrap();
        assert_eq!(lim.status, "limit");
        let mut w = f.nominal();
        w.insert("Co".into(), lim.value.unwrap());
        assert!((func(&f.comp(&w).unwrap()).unwrap() - 80.0).abs() < 1e-6);
        // Al does not enter the function
        assert_eq!(
            res.impurities["Al"].others_nominal.as_ref().unwrap().status,
            NO_RESPONSE
        );
    }

    #[test]
    fn ranking_orders_by_contribution_and_puts_missing_last() {
        let f = frame();
        let rv = r();
        let res = analyse_scalar(
            &f,
            &Evaluator::Linear {
                r: &rv,
                offset: 0.0,
            },
            None,
        )
        .unwrap();
        // Nb: 0.001 * 19.9999 ; Co: 0.0005 * 9.9999 ; Al negative
        assert_eq!(ranking(&res), vec!["Nb", "Co", "Al"]);
        let mut cut = res.clone();
        cut.impurities.get_mut("Nb").unwrap().contribution_nominal = None;
        assert_eq!(ranking(&cut), vec!["Co", "Al", "Nb"]);
    }

    #[test]
    fn top_nuclides_are_the_largest_shares_of_the_contributions_sign() {
        let parts: BTreeMap<String, BTreeMap<String, f64>> = BTreeMap::from([
            ("Fe".into(), m(&[("Mn54", 1.0), ("Fe55", 5.0)])),
            (
                "Nb".into(),
                m(&[
                    ("Nb94", 50.0),
                    ("Mn54", 3.0),
                    ("Nb92", 10.0),
                    ("Zr95", 7.0),
                    ("Fe55", 1.0),
                ]),
            ),
        ]);
        let top = top_linear(&parts, "Fe", "Nb", 100.0, false);
        let names: Vec<&str> = top.iter().map(|(n, _)| n.as_str()).collect();
        assert_eq!(names, vec!["Nb94", "Nb92", "Zr95"]);
        assert!((top[0].1 - 50.0).abs() < 1e-12);
        // Mn54 gains 2, Fe55 loses 4 (excluded)
        let all = top_linear(&parts, "Fe", "Nb", 1.0, false);
        assert!(all.iter().all(|(n, _)| n != "Fe55"));
        // A negative contribution is explained by the shares it removes, largest magnitude first.
        let lost = top_linear(&parts, "Nb", "Fe", 100.0, true);
        let names: Vec<&str> = lost.iter().map(|(n, _)| n.as_str()).collect();
        assert_eq!(names, vec!["Nb94", "Nb92", "Zr95"]);
        assert!((lost[0].1 + 50.0).abs() < 1e-12);
    }

    #[test]
    fn worst_class_from_maximised_sums() {
        let pass = |x: f64| SumCheck {
            normalized_sum: x,
            strict: true,
        };
        // A passes both: class A
        assert_eq!(
            class_of_checks(&[
                [pass(0.5), pass(0.5)],
                [pass(0.5), pass(0.5)],
                [pass(0.5), pass(0.5)]
            ]),
            WasteClass::A
        );
        // Table 1 normalized for A/B at 0.1 fails A, passes B
        assert_eq!(
            class_of_checks(&[
                [pass(1.5), pass(0.5)],
                [pass(0.9), pass(0.5)],
                [pass(0.09), pass(0.5)]
            ]),
            WasteClass::B
        );
        // Table 2 column 2 over: B fails, C passes
        assert_eq!(
            class_of_checks(&[
                [pass(0.5), pass(2.0)],
                [pass(0.5), pass(1.2)],
                [pass(0.05), pass(0.4)]
            ]),
            WasteClass::C
        );
        // everything over
        assert_eq!(
            class_of_checks(&[
                [pass(5.0), pass(5.0)],
                [pass(5.0), pass(5.0)],
                [pass(5.0), pass(5.0)]
            ]),
            WasteClass::AboveClassC
        );
        // a sum of exactly 1 passes for a single nuclide and fails for a mixture
        let single = SumCheck {
            normalized_sum: 1.0,
            strict: false,
        };
        let mixture = SumCheck {
            normalized_sum: 1.0,
            strict: true,
        };
        assert!(single.passes() && !mixture.passes());
        let low = pass(0.1);
        assert_eq!(
            class_of_checks(&[[single, low], [low, low], [low, low]]),
            WasteClass::A
        );
        assert_eq!(
            class_of_checks(&[[mixture, low], [low, low], [low, low]]),
            WasteClass::B
        );
        // the class is non-decreasing in every sum
        let sums = [0.05, 0.5, 0.99, 1.5, 12.0];
        let mut last = 0u8;
        for s in sums {
            let c = class_of_checks(&[
                [pass(s * 10.0), pass(s)],
                [pass(s * 10.0), pass(s / 2.0)],
                [pass(s), pass(s / 4.0)],
            ]);
            assert!(class_rank(c) >= last);
            last = class_rank(c);
        }
    }

    #[test]
    fn composition_frame_fills_the_balance_and_drops_zeros() {
        let f = frame();
        let c = f.comp(&m(&[("Co", 0.0), ("Nb", 0.5)])).unwrap();
        assert!(!c.contains_key("Co"));
        assert!((c.values().sum::<f64>() - 100.0).abs() < 1e-12);
        assert!((c["Fe"] - 98.5).abs() < 1e-12);
        assert!(f.comp(&m(&[("Nb", 99.5)])).is_err());
        // the balance may be exhausted exactly
        let g = f.comp(&m(&[("Nb", 99.0)])).unwrap();
        assert!(!g.contains_key("Fe"));
    }

    #[test]
    fn tightest_picks_the_smallest_limit_and_blocking_status() {
        let lims = [
            (1u64, Limit::found(0.5)),
            (2, Limit::found(0.2)),
            (3, Limit::none(NO_RESPONSE, "")),
        ];
        let v = tightest(lims.iter().map(|(k, l)| (*k, l)), "wt_pct");
        assert_eq!(v["wt_pct"], json!(0.2));
        assert_eq!(v["step"], json!(2));
        let blocked = [(1u64, Limit::found(0.5)), (2, Limit::none(INFEASIBLE, ""))];
        let v = tightest(blocked.iter().map(|(k, l)| (*k, l)), "wt_pct");
        assert_eq!(v["status"], json!(INFEASIBLE));
        assert_eq!(v["step"], json!(2));
        let none = [(1u64, Limit::none(NO_RESPONSE, ""))];
        assert_eq!(
            tightest(none.iter().map(|(k, l)| (*k, l)), "wt_pct")["status"],
            json!(NO_RESPONSE)
        );
    }

    #[test]
    fn rel_dev_is_symmetric_and_zero_safe() {
        assert_eq!(rel_dev(0.0, 0.0), 0.0);
        assert!((rel_dev(1.0, 1.0 + 1e-8) - 1e-8).abs() < 1e-12);
        assert_eq!(rel_dev(1.0, 2.0), rel_dev(2.0, 1.0));
    }

    #[test]
    fn composition_keys_distinguish_weights() {
        let a = m(&[("Fe", 99.0), ("Co", 1.0)]);
        let b = m(&[("Fe", 99.0), ("Co", 1.0 + 1e-15)]);
        assert_eq!(comp_key(&a), comp_key(&a.clone()));
        assert_ne!(comp_key(&a), comp_key(&b));
    }
}
