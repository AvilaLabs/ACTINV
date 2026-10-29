//! Summary: the first page a result opens on. It answers the questions people open a result to
//! ask (how active, how hot, how much dose, what dominates, how it changes as it cools) at the
//! step the result bar selects, and sends the user to the page that answers the follow-up.
use crate::{model, model::ResultDocument, nav::Page};
use eframe::egui::{self, RichText};
use egui_plot::{Line, Plot, PlotPoints, Points};
use serde_json::Value;

/// What the shell must do after the summary was drawn.
#[derive(Clone, PartialEq, Debug)]
pub enum SummaryAction {
    None,
    /// Select this computed step (zero-based).
    Step(usize),
    Open(Page),
    /// Follow this nuclide's history on the time-history page.
    Follow(String),
}

fn irradiating(step: &Value) -> bool {
    model::number(&step["flux"]) > 0. || step["power"].is_object()
}

/// The last step that ends an irradiation, if the result records any: the state at shutdown.
pub fn shutdown_step(result: &ResultDocument) -> Option<usize> {
    result.steps().iter().rposition(irradiating)
}

/// When a step is, in words: during irradiation, at shutdown, or how long after shutdown.
pub fn when(result: &ResultDocument, index: usize) -> String {
    let steps = result.steps();
    let Some(step) = steps.get(index) else {
        return String::new();
    };
    let t = model::number(&step["t_s"]);
    let shutdown = steps[..=index].iter().rposition(irradiating);
    match shutdown {
        Some(i) if i == index => {
            if steps[index + 1..].iter().any(irradiating) {
                format!(
                    "during irradiation, {} after start",
                    model::readable_time(t)
                )
            } else {
                "at shutdown".into()
            }
        }
        Some(i) => format!(
            "{} after shutdown",
            model::readable_time(t - model::number(&steps[i]["t_s"]))
        ),
        None => format!("{} after start", model::readable_time(t)),
    }
}

/// Steps nearest to round cooling times, for one-click selection: (label, step index). Only times
/// the result reaches are offered, each once.
fn landmarks(result: &ResultDocument) -> Vec<(String, usize)> {
    let steps = result.steps();
    let mut marks = Vec::new();
    let Some(shut) = shutdown_step(result) else {
        return marks;
    };
    marks.push(("At shutdown".to_owned(), shut));
    let t0 = model::number(&steps[shut]["t_s"]);
    let last = model::number(&steps[steps.len() - 1]["t_s"]) - t0;
    const HOUR: f64 = 3600.;
    const DAY: f64 = 86400.;
    const YEAR: f64 = 365.25 * DAY;
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
                let da = (model::number(&steps[*a]["t_s"]) - t0 - after).abs();
                let db = (model::number(&steps[*b]["t_s"]) - t0 - after).abs();
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

/// A headline quantity of a step and how to draw it.
struct Quantity {
    title: &'static str,
    unit: &'static str,
    value: fn(&Value) -> Option<f64>,
    /// The page that explains it.
    page: Page,
    /// Said instead of a value when the result does not carry it.
    absent: &'static str,
}

const QUANTITIES: [Quantity; 4] = [
    Quantity {
        title: "Activity",
        unit: "Bq/g",
        value: |s| Some(model::total_activity(s)),
        page: Page::Charts,
        absent: "",
    },
    Quantity {
        title: "Decay heat",
        unit: "W/g",
        value: |s| s["heat_W_per_g"]["total"].as_f64(),
        page: Page::Charts,
        absent: "",
    },
    Quantity {
        title: "Contact dose (screening)",
        unit: "Gy/h",
        value: |s| s["photon_source"]["contact_gamma_air_dose_proxy_Gy_h"].as_f64(),
        page: Page::Photons,
        absent: "Not computed. Turn on Decay photons and dose in What to compute.",
    },
    Quantity {
        title: "Damage",
        unit: "dpa",
        value: |s| s["damage"]["dpa"].as_f64(),
        page: Page::Charts,
        absent: "Not computed. Add a damage table in What to compute.",
    },
];

/// Why a headline quantity has no value at this step. A photon source without a contact dose
/// means the problem names no photon response, not that photons were left off.
fn absent(q: &Quantity, step: &Value) -> &'static str {
    if q.page == Page::Photons && step["photon_source"].is_object() {
        "Not reported. The contact dose needs a photon response for the material; set it under Decay photons and dose in What to compute."
    } else {
        q.absent
    }
}

/// The comparison step at the same physical time, if the comparison has one.
fn matching_step(other: &ResultDocument, t: f64) -> Option<&Value> {
    other.steps().iter().find(|step| {
        let u = model::number(&step["t_s"]);
        (u - t).abs() <= 1e-9 * t.abs().max(1.)
    })
}

/// The nuclides that make up most of a step's activity, largest first, with their share.
fn top_activity(step: &Value, count: usize) -> Vec<(String, f64)> {
    let total = model::total_activity(step);
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
fn top_heat(step: &Value, count: usize) -> Option<(&'static str, Vec<(String, f64)>)> {
    if let Some(top) = step["summary"]["dominant"]["heat"]["top"].as_array() {
        let list = top
            .iter()
            .take(count)
            .filter_map(|e| Some((e["nuclide"].as_str()?.to_owned(), e["percent"].as_f64()?)))
            .collect();
        return Some(("Decay heat", list));
    }
    let rows = step["photon_source"]["by_nuclide"].as_array()?;
    let total: f64 = rows
        .iter()
        .map(|r| model::number(&r["source_power_W_g"]))
        .sum();
    if total <= 0. {
        return None;
    }
    let mut list: Vec<(String, f64)> = rows
        .iter()
        .filter_map(|r| {
            let power = model::number(&r["source_power_W_g"]);
            let nuclide = r["nuclide"].as_str().filter(|_| power > 0.)?;
            Some((nuclide.to_owned(), 100. * power / total))
        })
        .collect();
    list.sort_by(|a, b| b.1.total_cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    list.truncate(count);
    Some(("Photon power", list))
}

fn sparkline(ui: &mut egui::Ui, id: &str, result: &ResultDocument, q: &Quantity, index: usize) {
    let accent = crate::visuals::accent(ui);
    let points: Vec<[f64; 2]> = result
        .steps()
        .iter()
        .enumerate()
        .filter_map(|(i, s)| {
            (q.value)(s)
                .filter(|v| *v > 0.)
                .map(|v| [i as f64, v.log10()])
        })
        .collect();
    if points.len() < 2 {
        return;
    }
    let here: Vec<[f64; 2]> = points
        .iter()
        .filter(|p| p[0] as usize == index)
        .copied()
        .collect();
    Plot::new(id)
        .height(46.)
        .width(ui.available_width())
        .show_axes(false)
        .show_grid(false)
        .show_x(false)
        .show_y(false)
        .allow_drag(false)
        .allow_zoom(false)
        .allow_scroll(false)
        .allow_boxed_zoom(false)
        .allow_double_click_reset(false)
        .show_background(false)
        .show(ui, |plot| {
            plot.line(Line::new(q.title, PlotPoints::from(points)).color(accent));
            plot.points(
                Points::new("selected", PlotPoints::from(here))
                    .radius(3.5)
                    .color(accent),
            );
        });
}

/// Draw the summary of the step `index`.
pub fn show(
    ui: &mut egui::Ui,
    result: &ResultDocument,
    comparison: Option<&ResultDocument>,
    index: usize,
) -> SummaryAction {
    let mut action = SummaryAction::None;
    let steps = result.steps();
    let Some(step) = steps.get(index) else {
        return action;
    };
    let accent = crate::visuals::accent(ui);
    let t = model::number(&step["t_s"]);
    ui.label(
        RichText::new(format!(
            "{} · step {} of {}",
            capitalized(&when(result, index)),
            index + 1,
            steps.len()
        ))
        .size(18.)
        .strong(),
    );
    let marks = landmarks(result);
    if marks.len() > 1 {
        ui.horizontal_wrapped(|ui| {
            ui.label("Jump to");
            for (label, i) in marks {
                if ui.selectable_label(i == index, label).clicked() {
                    action = SummaryAction::Step(i);
                }
            }
        });
    }
    ui.add_space(8.);
    let other = comparison.and_then(|c| matching_step(c, t));
    let shown: Vec<&Quantity> = QUANTITIES
        .iter()
        .filter(|q| q.title != "Damage" || steps.iter().any(|s| (q.value)(s).is_some()))
        .collect();
    // Equal columns always fit the page; each card fills its column and they share one height.
    let card_height = 140.;
    ui.columns(shown.len(), |columns| {
        for (ui, q) in columns.iter_mut().zip(shown) {
            egui::Frame::group(ui.style()).show(ui, |ui| {
                ui.set_width(ui.available_width());
                ui.set_min_height(card_height);
                ui.label(RichText::new(q.title.to_uppercase()).small().color(accent));
                match (q.value)(step) {
                    Some(value) => {
                        ui.label(
                            RichText::new(format!("{} {}", model::sci(value), q.unit))
                                .size(22.)
                                .strong(),
                        );
                        if let Some(base) = other.and_then(|o| (q.value)(o)) {
                            ui.label(delta(value, base));
                        }
                        sparkline(ui, &format!("spark-{}", q.title), result, q, index);
                    }
                    None => {
                        crate::visuals::unjustified(ui, RichText::new(absent(q, step)).weak());
                    }
                }
                // in a row of its own so the column layout does not stretch it
                ui.horizontal(|ui| {
                    if ui.small_button("Details >").clicked() {
                        action = SummaryAction::Open(q.page);
                    }
                });
            });
        }
    });
    if comparison.is_some() && other.is_none() {
        ui.label(
            RichText::new("The comparison has no step at this time, so no differences are shown.")
                .weak(),
        );
    }
    ui.add_space(14.);
    ui.heading("What dominates");
    ui.label("Select a nuclide to follow its history.");
    ui.columns(2, |columns| {
        dominant_list(
            &mut columns[0],
            "Activity",
            &top_activity(step, 6),
            &mut action,
        );
        match top_heat(step, 6) {
            Some((title, list)) => dominant_list(&mut columns[1], title, &list, &mut action),
            None => {
                columns[1].strong("Decay heat");
                columns[1].label(
                    RichText::new(
                        "Heat by nuclide needs a step summary or the photon source. Turn either on in What to compute.",
                    )
                    .weak(),
                );
            }
        }
    });
    ui.add_space(14.);
    ui.heading("Look closer");
    ui.horizontal_wrapped(|ui| {
        for (page, why) in [
            (Page::Charts, "Every quantity against time"),
            (Page::Tables, "Inventory and regulatory tables"),
            (Page::Photons, "Photon spectra and dose"),
            (Page::Pathways, "How each nuclide was made"),
            (Page::Waste, "Disposal and clearance class"),
            (Page::Ledger, "Inputs, data and checks behind this result"),
        ] {
            if ui.button(page.label()).on_hover_text(why).clicked() {
                action = SummaryAction::Open(page);
            }
        }
    });
    action
}

fn dominant_list(
    ui: &mut egui::Ui,
    title: &str,
    list: &[(String, f64)],
    action: &mut SummaryAction,
) {
    ui.strong(title);
    if list.is_empty() {
        ui.label(RichText::new("Nothing positive at this step.").weak());
        return;
    }
    let accent = crate::visuals::accent(ui);
    for (nuclide, percent) in list {
        ui.horizontal(|ui| {
            if ui.link(nuclide).clicked() {
                *action = SummaryAction::Follow(nuclide.clone());
            }
            let (rect, _) = ui.allocate_exact_size(
                egui::vec2((ui.available_width() - 80.).max(40.), 8.),
                egui::Sense::hover(),
            );
            ui.painter()
                .rect_filled(rect, 2., ui.visuals().faint_bg_color);
            let mut bar = rect;
            bar.set_width(rect.width() * (*percent as f32 / 100.).clamp(0., 1.));
            ui.painter().rect_filled(bar, 2., accent);
            ui.label(if *percent < 0.05 {
                "<0.1%".to_owned()
            } else {
                format!("{percent:.1}%")
            });
        });
    }
}

/// The relative change against the comparison, in words.
fn delta(value: f64, base: f64) -> String {
    if base == 0. {
        return if value == 0. {
            "Same as comparison".into()
        } else {
            "Comparison is zero".into()
        };
    }
    let change = 100. * (value - base) / base;
    if change.abs() < 0.05 {
        "Same as comparison".into()
    } else {
        format!("{change:+.1}% vs comparison")
    }
}

fn capitalized(text: &str) -> String {
    let mut chars = text.chars();
    chars
        .next()
        .map(|c| c.to_uppercase().chain(chars).collect())
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn result(steps: Value) -> ResultDocument {
        ResultDocument::parse(json!({"steps": steps}), "test".into()).unwrap()
    }

    fn step(t: f64, flux: f64, activity: Value) -> Value {
        json!({"t_s": t, "flux": flux, "inventory": [], "activity_Bq_per_g": activity,
               "heat_W_per_g": {"total": 1.0}})
    }

    #[test]
    fn steps_read_as_irradiation_shutdown_and_cooling_time() {
        let r = result(json!([
            step(60., 1., json!({})),
            step(300., 1., json!({})),
            step(3900., 0., json!({})),
            step(300. + 86400., 0., json!({})),
        ]));
        assert_eq!(when(&r, 0), "during irradiation, 1 min after start");
        assert_eq!(when(&r, 1), "at shutdown");
        assert_eq!(when(&r, 2), "1 h after shutdown");
        assert_eq!(when(&r, 3), "1 d after shutdown");
        assert_eq!(shutdown_step(&r), Some(1));
        assert_eq!(
            landmarks(&r),
            [
                ("At shutdown".to_owned(), 1),
                ("~1 h".to_owned(), 2),
                ("~1 d".to_owned(), 3)
            ]
        );
    }

    #[test]
    fn a_result_without_irradiation_counts_from_the_start() {
        let r = model::tutorial_result();
        assert_eq!(shutdown_step(&r), None);
        assert_eq!(when(&r, 3), "1 h after start");
        assert!(landmarks(&r).is_empty());
    }

    #[test]
    fn dominant_activity_is_ranked_as_a_share_of_the_total() {
        let s = step(1., 0., json!({"A": 1.0, "B": 3.0, "C": 0.0}));
        assert_eq!(
            top_activity(&s, 5),
            [("B".to_owned(), 75.0), ("A".to_owned(), 25.0)]
        );
    }

    #[test]
    fn heat_falls_back_to_photon_power_and_says_so() {
        let mut s = step(1., 0., json!({}));
        assert!(top_heat(&s, 3).is_none());
        s["photon_source"] = json!({"by_nuclide": [
            {"nuclide": "Mn56", "source_power_W_g": 3.0},
            {"nuclide": "Co58", "source_power_W_g": 1.0}]});
        assert_eq!(
            top_heat(&s, 3),
            Some((
                "Photon power",
                vec![("Mn56".to_owned(), 75.0), ("Co58".to_owned(), 25.0)]
            ))
        );
        s["summary"] =
            json!({"dominant": {"heat": {"top": [{"nuclide": "Fe55", "percent": 60.0}]}}});
        assert_eq!(top_heat(&s, 3).unwrap().0, "Decay heat");
    }

    #[test]
    fn deltas_read_as_percent_changes() {
        assert_eq!(delta(110., 100.), "+10.0% vs comparison");
        assert_eq!(delta(100., 100.), "Same as comparison");
        assert_eq!(delta(1., 0.), "Comparison is zero");
    }
}
