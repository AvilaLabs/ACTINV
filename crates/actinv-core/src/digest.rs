//! A plain-language reading of a problem and its result: what the problem sets up, when each
//! step is, which steps are worth reading, and which nuclides dominate. The desktop app, the
//! browser and `actinv report --format html` share it, so they say the same thing in the same
//! words. Nothing here computes physics; it reads the JSON the solver wrote.
use crate::spec::parse_duration;
use serde_json::Value;

/// The outputs a problem without `options.outputs` asks for.
pub const DEFAULT_OUTPUTS: [&str; 5] = ["inventory", "activity", "heat", "photons", "pathways"];

const HOUR: f64 = 3600.;
const DAY: f64 = 86400.;
const YEAR: f64 = 365.25 * DAY;

fn number_of(value: &Value) -> f64 {
    value.as_f64().unwrap_or(0.)
}

/// A duration for reading: the largest unit that keeps the number at least 1, three significant
/// figures. Exact seconds stay available beside it wherever precision matters.
pub fn readable_time(seconds: f64) -> String {
    const UNITS: [(f64, &str); 6] = [
        (YEAR, "y"),
        (DAY, "d"),
        (HOUR, "h"),
        (60., "min"),
        (1., "s"),
        (1e-3, "ms"),
    ];
    if !seconds.is_finite() || seconds <= 0. {
        return "0 s".into();
    }
    let (size, unit) = UNITS
        .iter()
        .find(|(size, _)| seconds >= *size)
        .copied()
        .unwrap_or((1e-3, "ms"));
    let value = seconds / size;
    let digits = if value >= 100. {
        0
    } else if value >= 10. {
        1
    } else {
        2
    };
    let text = format!("{value:.digits$}");
    let text = if text.contains('.') {
        text.trim_end_matches('0').trim_end_matches('.').to_owned()
    } else {
        text
    };
    format!("{text} {unit}")
}

/// A quantity with three significant figures in scientific notation, e.g. `1.23e9`.
pub fn sci(value: f64) -> String {
    if value == 0. {
        "0".into()
    } else {
        format!("{value:.2e}")
    }
}

/// Up to four significant figures, without trailing zeros; scientific when very large or small.
pub fn number(value: f64) -> String {
    if value != 0. && !(1e-3..1e5).contains(&value.abs()) {
        return sci(value);
    }
    let text = format!("{value:.4}");
    text.trim_end_matches('0').trim_end_matches('.').to_owned()
}

/// `FE` reads as `Fe`; isotope names keep their spelling.
pub fn display_name(name: &str) -> String {
    if name.len() <= 2 && name.chars().all(|c| c.is_ascii_alphabetic()) {
        let mut chars = name.chars();
        let first = chars.next().map(|c| c.to_ascii_uppercase());
        first
            .into_iter()
            .chain(chars.map(|c| c.to_ascii_lowercase()))
            .collect()
    } else {
        name.to_owned()
    }
}

fn capital(word: &str) -> String {
    let mut chars = word.chars();
    chars
        .next()
        .map(|c| c.to_uppercase().chain(chars).collect())
        .unwrap_or_default()
}

fn declares(step: &Value, key: &str) -> bool {
    step.get(key).is_some_and(|v| !v.is_null())
}

/// A problem's schedule step is cooling when it irradiates nothing: a flux multiplier or a wall
/// loading of zero. A power target is validated positive, so a power step always irradiates.
pub fn is_cooling(step: &Value) -> bool {
    if declares(step, "power_W_cm3") || declares(step, "power_W_per_g") {
        false
    } else if declares(step, "wall_loading_MW_m2") {
        number_of(&step["wall_loading_MW_m2"]) == 0.
    } else {
        number_of(&step["flux"]) == 0.
    }
}

/// The material in one line: the three largest components and the mass, e.g.
/// `Fe 70 wt%, Cr 18 wt%, Ni 10 wt% and 2 more · 1 g`. `Err` says what is missing.
pub fn material(document: &Value) -> Result<String, String> {
    let material = &document["material"];
    let mut parts: Vec<(&str, f64)> = material["composition"]
        .as_object()
        .map(|map| {
            map.iter()
                .map(|(k, v)| (k.as_str(), number_of(v)))
                .filter(|(_, v)| *v > 0.)
                .collect()
        })
        .unwrap_or_default();
    if parts.is_empty() {
        return Err("Add at least one element or isotope.".into());
    }
    parts.sort_by(|a, b| b.1.total_cmp(&a.1).then_with(|| a.0.cmp(b.0)));
    let unit = match material["basis"].as_str() {
        Some("wt_percent") => " wt%",
        Some("atom_fraction") => " atom fraction",
        Some("atoms_per_g") => " atoms/g",
        _ => "",
    };
    let mut text = parts
        .iter()
        .take(3)
        .map(|(name, value)| format!("{} {}{unit}", display_name(name), number(*value)))
        .collect::<Vec<_>>()
        .join(", ");
    if parts.len() > 3 {
        text.push_str(&format!(" and {} more", parts.len() - 3));
    }
    if let Some(mass) = material["mass_g"].as_f64() {
        text.push_str(&format!(" · {} g", number(mass)));
    }
    Ok(text)
}

/// The schedule in one line, e.g. `5 min irradiation, then 20 cooling steps to 57 min after
/// shutdown`. `Err` says what is missing.
pub fn schedule(document: &Value) -> Result<String, String> {
    let steps = document["schedule"]
        .as_array()
        .map_or(&[][..], Vec::as_slice);
    if steps.is_empty() {
        return Err("Add an irradiation step.".into());
    }
    let mut irradiation = 0.;
    let mut cooling = 0.;
    let mut cooling_steps = 0;
    let mut irradiating_steps = 0;
    for step in steps {
        let dt = step["dt"]
            .as_str()
            .and_then(|text| parse_duration(text).ok())
            .or_else(|| step["dt"].as_f64())
            .unwrap_or(0.);
        if is_cooling(step) {
            cooling += dt;
            cooling_steps += 1;
        } else {
            irradiation += dt;
            irradiating_steps += 1;
        }
    }
    if irradiating_steps == 0 {
        return Err(
            "Every step is cooling. Give one step a flux, wall loading or power to irradiate."
                .into(),
        );
    }
    let mut text = format!("{} irradiation", readable_time(irradiation));
    if irradiating_steps > 1 {
        text.push_str(&format!(" in {irradiating_steps} steps"));
    }
    match cooling_steps {
        0 => text.push_str(", no cooling"),
        1 => text.push_str(&format!(", then cooling for {}", readable_time(cooling))),
        n => text.push_str(&format!(
            ", then {n} cooling steps to {} after shutdown",
            readable_time(cooling)
        )),
    }
    Ok(text)
}

/// The source in one line, e.g. `Neutron, 709 groups (fispact-709), normalized to 1.12e10
/// /cm²/s`. `Err` says what is missing.
pub fn source(document: &Value) -> Result<String, String> {
    let projectile = document["projectile"].as_str().unwrap_or("neutron");
    let spectrum = &document["spectrum"];
    let groups = spectrum["flux_per_group"]
        .as_array()
        .map_or(&[][..], Vec::as_slice);
    if !groups.iter().any(|v| number_of(v) > 0.) {
        return Err(format!(
            "The {projectile} spectrum has no positive group value. Paste or import one."
        ));
    }
    let structure = spectrum["structure"].as_str().unwrap_or("custom");
    let flux = match spectrum["total"].as_f64() {
        Some(total) => format!("normalized to {} /cm²/s", sci(total)),
        None => {
            let sum: f64 = groups.iter().map(number_of).sum();
            format!("{} /cm²/s in total", sci(sum))
        }
    };
    Ok(format!(
        "{}, {} groups ({structure}), {flux}",
        capital(projectile),
        groups.len()
    ))
}

/// What the run reports, in one line. Inventory, activity and heat are in every result.
pub fn outputs(document: &Value) -> Result<String, String> {
    let listed: Vec<&str> = document["options"]["outputs"]
        .as_array()
        .map(|items| items.iter().filter_map(Value::as_str).collect())
        .unwrap_or_else(|| DEFAULT_OUTPUTS.to_vec());
    let mut parts = vec!["activity", "decay heat", "inventory"];
    if listed.contains(&"photons") {
        parts.push("photons and dose");
    }
    if listed.contains(&"pathways") {
        parts.push("pathways");
    }
    if document["damage"].is_object() {
        parts.push("dpa");
    }
    if document["uncertainty"].is_object() {
        parts.push("uncertainty");
    }
    if document["summary"].is_object() {
        parts.push("step summary");
    }
    let last = parts.pop().unwrap_or_default();
    Ok(format!("{} and {last}", parts.join(", ")))
}

/// Whether a result step irradiated: a positive flux or a power target.
fn irradiating(step: &Value) -> bool {
    number_of(&step["flux"]) > 0. || step["power"].is_object()
}

/// The last result step that ends an irradiation, if any: the state at shutdown.
pub fn shutdown_step(steps: &[Value]) -> Option<usize> {
    steps.iter().rposition(irradiating)
}

/// When a result step is, in words: during irradiation, at shutdown, or how long after
/// shutdown.
pub fn when(steps: &[Value], index: usize) -> String {
    let Some(step) = steps.get(index) else {
        return String::new();
    };
    let t = number_of(&step["t_s"]);
    match steps[..=index].iter().rposition(irradiating) {
        Some(i) if i == index => {
            if steps[index + 1..].iter().any(irradiating) {
                format!("during irradiation, {} after start", readable_time(t))
            } else {
                "at shutdown".into()
            }
        }
        Some(i) => format!(
            "{} after shutdown",
            readable_time(t - number_of(&steps[i]["t_s"]))
        ),
        None => format!("{} after start", readable_time(t)),
    }
}

/// Steps nearest to round cooling times: (label, step index), shutdown first and the last step
/// last. Only times the result reaches are offered, each step once.
pub fn landmarks(steps: &[Value]) -> Vec<(String, usize)> {
    let mut marks = Vec::new();
    let Some(shut) = shutdown_step(steps) else {
        return marks;
    };
    marks.push(("At shutdown".to_owned(), shut));
    let t0 = number_of(&steps[shut]["t_s"]);
    let last = number_of(&steps[steps.len() - 1]["t_s"]) - t0;
    for (label, after) in [
        ("1 h", HOUR),
        ("1 d", DAY),
        ("1 week", 7. * DAY),
        ("1 y", YEAR),
        ("10 y", 10. * YEAR),
        ("100 y", 100. * YEAR),
    ] {
        if after > last * 1.5 {
            break;
        }
        let nearest = (shut..steps.len())
            .min_by(|a, b| {
                let da = (number_of(&steps[*a]["t_s"]) - t0 - after).abs();
                let db = (number_of(&steps[*b]["t_s"]) - t0 - after).abs();
                da.total_cmp(&db)
            })
            .unwrap_or(shut);
        if marks.iter().all(|(_, i)| *i != nearest) {
            marks.push((format!("~{label}"), nearest));
        }
    }
    if marks.iter().all(|(_, i)| *i != steps.len() - 1) {
        marks.push(("Last step".to_owned(), steps.len() - 1));
    }
    marks
}

/// Total activity of a result step, Bq/g.
pub fn total_activity(step: &Value) -> f64 {
    step["activity_Bq_per_g"]
        .as_object()
        .map(|a| a.values().map(number_of).sum())
        .unwrap_or(0.)
}

/// The nuclides that make up most of a step's activity, largest first, with their percentage.
pub fn top_activity(step: &Value, count: usize) -> Vec<(String, f64)> {
    let total = total_activity(step);
    let mut entries: Vec<(String, f64)> = step["activity_Bq_per_g"]
        .as_object()
        .map(|map| {
            map.iter()
                .filter_map(|(k, v)| v.as_f64().filter(|v| *v > 0.).map(|v| (k.clone(), v)))
                .collect()
        })
        .unwrap_or_default();
    entries.sort_by(|a, b| b.1.total_cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    entries.truncate(count);
    if total > 0. {
        entries.iter_mut().for_each(|e| e.1 = 100. * e.1 / total);
    }
    entries
}

/// Decay heat by nuclide from the step summary when present, else the photon power by nuclide
/// (gamma heat only), with a caption saying which.
pub fn top_heat(step: &Value, count: usize) -> Option<(&'static str, Vec<(String, f64)>)> {
    if let Some(top) = step["summary"]["dominant"]["heat"]["top"].as_array() {
        let list = top
            .iter()
            .take(count)
            .filter_map(|e| Some((e["nuclide"].as_str()?.to_owned(), e["percent"].as_f64()?)))
            .collect();
        return Some(("Decay heat", list));
    }
    let rows = step["photon_source"]["by_nuclide"].as_array()?;
    let total: f64 = rows.iter().map(|r| number_of(&r["source_power_W_g"])).sum();
    if total <= 0. {
        return None;
    }
    let mut list: Vec<(String, f64)> = rows
        .iter()
        .filter_map(|r| {
            let power = number_of(&r["source_power_W_g"]);
            let nuclide = r["nuclide"].as_str().filter(|_| power > 0.)?;
            Some((nuclide.to_owned(), 100. * power / total))
        })
        .collect();
    list.sort_by(|a, b| b.1.total_cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    list.truncate(count);
    Some(("Photon power", list))
}

/// A headline quantity of a result step.
pub struct Headline {
    pub title: &'static str,
    pub unit: &'static str,
    pub value: fn(&Value) -> Option<f64>,
    /// Said instead of a value when the result does not carry it.
    pub absent: &'static str,
}

/// Activity, decay heat, contact dose and damage: what people open a result to read.
pub const HEADLINES: [Headline; 4] = [
    Headline {
        title: "Activity",
        unit: "Bq/g",
        value: |s| Some(total_activity(s)),
        absent: "",
    },
    Headline {
        title: "Decay heat",
        unit: "W/g",
        value: |s| s["heat_W_per_g"]["total"].as_f64(),
        absent: "",
    },
    Headline {
        title: "Contact dose (screening)",
        unit: "Gy/h",
        value: |s| s["photon_source"]["contact_gamma_air_dose_proxy_Gy_h"].as_f64(),
        absent: "Not computed: the problem did not ask for decay photons.",
    },
    Headline {
        title: "Damage",
        unit: "dpa",
        value: |s| s["damage"]["dpa"].as_f64(),
        absent: "Not computed: the problem names no damage table.",
    },
];

/// Why a headline has no value at a step. A photon source without a contact dose means the
/// problem names no photon response, not that photons were left off.
pub fn absent(headline: &Headline, step: &Value) -> &'static str {
    if headline.title.starts_with("Contact dose") && step["photon_source"].is_object() {
        "Not reported: the contact dose needs a photon response for the material."
    } else {
        headline.absent
    }
}

/// The caveats of a result worth stating to a reader, in plain words, from its ledger and
/// photon source. The full ledger stays in the result JSON.
pub fn caveats(result: &Value) -> Vec<String> {
    let ledger = &result["ledger"];
    let mut notes = Vec::new();
    let fallback = number_of(&ledger["decay_nuclides_from_fallback"]);
    if fallback > 0. {
        notes.push(format!(
            "{} nuclides took their decay data from the fallback library, not the primary one.",
            number(fallback)
        ));
    }
    let missing = number_of(&ledger["decay_daughters_missing"]);
    if missing > 0. {
        notes.push(format!(
            "{} decay daughters are missing from the decay data (decay_daughters_missing).",
            number(missing)
        ));
    }
    if ledger["composition_not_summing_to_100"].as_bool() == Some(true) {
        notes.push(format!(
            "The weight percentages total {} %, not 100; they were used as entered.",
            number(number_of(&ledger["composition_weight_percent_total"]))
        ));
    }
    if let Some(unknown) = ledger["composition_elements_unknown"].as_array() {
        if !unknown.is_empty() {
            let names: Vec<_> = unknown.iter().filter_map(Value::as_str).collect();
            notes.push(format!(
                "Elements the natural-abundance table does not know: {}.",
                names.join(", ")
            ));
        }
    }
    if let Some(last) = result["steps"].as_array().and_then(|s| s.last()) {
        if let Some(fraction) = last["photon_source"]["represented_gamma_power_fraction"].as_f64() {
            if fraction < 0.99 {
                notes.push(format!(
                    "The photon groups represent {:.1}% of the gamma power at the last step.",
                    100. * fraction
                ));
            }
        }
        if let Some(covered) = last["damage"]["covered_atom_fraction"].as_f64() {
            if covered < 0.999 {
                notes.push(format!(
                    "The damage table covers {:.1}% of the atoms; the rest add nothing to dpa.",
                    100. * covered
                ));
            }
        }
    }
    if let Some(closure) = result["pathway_closure"].as_f64() {
        if closure > 0.01 {
            notes.push(format!(
                "The listed production pathways differ from the inventory by up to {:.1}% (pathway_closure).",
                100. * closure
            ));
        }
    }
    notes
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn readable_times_pick_the_unit_a_person_would() {
        for (seconds, text) in [
            (0., "0 s"),
            (0.25, "250 ms"),
            (66., "1.1 min"),
            (300., "5 min"),
            (5400., "1.5 h"),
            (1234. * YEAR, "1234 y"),
        ] {
            assert_eq!(readable_time(seconds), text, "{seconds}");
        }
    }

    #[test]
    fn a_long_composition_names_the_largest_three() {
        let doc = json!({"material": {"basis": "wt_percent", "mass_g": 1.0, "composition":
            {"FE": 70.0, "CR": 18.0, "NI": 10.0, "MN": 1.5, "SI": 0.5}}});
        assert_eq!(
            material(&doc).unwrap(),
            "Fe 70 wt%, Cr 18 wt%, Ni 10 wt% and 2 more · 1 g"
        );
        assert!(material(&json!({})).is_err());
    }

    #[test]
    fn a_schedule_reads_as_irradiation_then_cooling() {
        let doc = json!({"schedule": [
            {"dt": "5 min", "flux": 1.0},
            {"dt": "1 h", "flux": 0.0},
            {"dt": "1 d", "flux": 0.0},
        ]});
        assert_eq!(
            schedule(&doc).unwrap(),
            "5 min irradiation, then 2 cooling steps to 1.04 d after shutdown"
        );
        assert!(schedule(&json!({"schedule": [{"dt": "1 h", "flux": 0.0}]})).is_err());
    }

    fn steps() -> Vec<Value> {
        vec![
            json!({"t_s": 300.0, "flux": 1.0}),
            json!({"t_s": 3900.0, "flux": 0.0}),
            json!({"t_s": 300.0 + DAY, "flux": 0.0}),
        ]
    }

    #[test]
    fn steps_are_named_from_shutdown() {
        let steps = steps();
        assert_eq!(shutdown_step(&steps), Some(0));
        assert_eq!(when(&steps, 0), "at shutdown");
        assert_eq!(when(&steps, 1), "1 h after shutdown");
        let marks: Vec<_> = landmarks(&steps).into_iter().map(|m| m.0).collect();
        assert_eq!(marks, ["At shutdown", "~1 h", "~1 d"]);
    }

    #[test]
    fn caveats_speak_plainly_and_skip_what_is_fine() {
        let result = json!({
            "ledger": {"decay_nuclides_from_fallback": 50, "decay_daughters_missing": 0,
                       "composition_not_summing_to_100": false},
            "pathway_closure": 1e-12,
            "steps": [{"photon_source": {"represented_gamma_power_fraction": 1.0}}]
        });
        assert_eq!(
            caveats(&result),
            ["50 nuclides took their decay data from the fallback library, not the primary one."]
        );
    }
}
