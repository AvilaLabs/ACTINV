//! Pathways: the ranked first products of the trace-mode `pathways` output and the opt-in full
//! reaction-and-decay routes of `pathway_routes`, with the closure of each target.
use crate::{
    model,
    views::Target,
    visuals::{accent, json_tree, BLUE},
};
use eframe::egui::{self, Color32, Vec2};
use egui_extras::{Column, TableBuilder};
use egui_plot::{Line, Plot};
use serde_json::Value;

/// The diagram shows at most this many ranked first-product contributions.
const MAX_FIRST_PRODUCTS: usize = 12;
/// A residual above this share of the target gets the remedies line.
const RESIDUAL_HINT: f64 = 0.01;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Mode {
    Routes,
    FirstProducts,
}

/// What the view remembers between frames. The selected target is its own: choosing a pathway
/// never changes the nuclide selected on other pages.
pub struct PathwaysState {
    /// `None` until the user picks a tab: full routes when the result has them, else first products.
    mode: Option<Mode>,
    first_nuclide: String,
    inspected: String,
    target: String,
    route: usize,
    scene_view: egui::Rect,
}

impl Default for PathwaysState {
    fn default() -> Self {
        Self {
            mode: None,
            first_nuclide: String::new(),
            inspected: String::new(),
            target: String::new(),
            route: 0,
            scene_view: egui::Rect::ZERO,
        }
    }
}

pub enum PathwaysAction {
    None,
    Open(Target),
}

/// One ranked first product of a nuclide at one step (`pathways[step][nuclide][]`).
#[derive(Debug, PartialEq)]
struct FirstProduct {
    from: String,
    first_product: String,
    atoms_per_g: f64,
    fraction: f64,
}

/// One route of `pathway_routes.targets[].paths[]`.
#[derive(Debug, PartialEq)]
struct Route {
    source: String,
    nuclides: Vec<String>,
    edges: Vec<String>,
    atoms_per_g: f64,
    fraction: f64,
    is_loop: bool,
    loop_gain: Option<f64>,
    base_path: Vec<String>,
    by_step: Vec<f64>,
}

/// One entry of `pathway_routes.targets[].generic[]`.
#[derive(Debug, PartialEq)]
struct Generic {
    edge_types: String,
    n_paths: usize,
    atoms_per_g: f64,
    fraction: f64,
}

/// One entry of `pathway_routes.targets[]`: the closure numbers exactly as the result records
/// them, and the routes ranked by contribution.
#[derive(Debug, PartialEq)]
struct RouteTarget {
    target: String,
    total: f64,
    routes_total: f64,
    residual: f64,
    residual_fraction: f64,
    explained_fraction: f64,
    loop_atoms: f64,
    dropped_by_cap: usize,
    candidates_dropped: usize,
    search_truncated: bool,
    truncated_sources: Vec<String>,
    routes: Vec<Route>,
    generic: Vec<Generic>,
}

fn strings(value: &Value) -> Vec<String> {
    value
        .as_array()
        .map(|items| {
            items
                .iter()
                .filter_map(|v| v.as_str().map(str::to_owned))
                .collect()
        })
        .unwrap_or_default()
}

fn count(value: &Value) -> usize {
    value.as_u64().unwrap_or(0) as usize
}

/// True when any step recorded first-product pathways.
fn first_products_available(value: &Value) -> bool {
    value["pathways"].as_array().is_some_and(|steps| {
        steps
            .iter()
            .any(|s| s.as_object().is_some_and(|o| !o.is_empty()))
    })
}

/// The nuclides that have first products at `step`.
fn first_product_nuclides(value: &Value, step: usize) -> Vec<String> {
    value["pathways"]
        .as_array()
        .and_then(|steps| steps.get(step))
        .and_then(Value::as_object)
        .map(|o| o.keys().cloned().collect())
        .unwrap_or_default()
}

/// The recorded (already ranked) first products of `nuclide` at `step`.
fn first_products(value: &Value, step: usize, nuclide: &str) -> Vec<FirstProduct> {
    value["pathways"]
        .as_array()
        .and_then(|steps| steps.get(step))
        .and_then(|s| s.get(nuclide))
        .and_then(Value::as_array)
        .map(|paths| {
            paths
                .iter()
                .map(|p| FirstProduct {
                    from: p["from"].as_str().unwrap_or("?").to_owned(),
                    first_product: p["first_product"].as_str().unwrap_or("?").to_owned(),
                    atoms_per_g: model::number(&p["atoms_per_g"]),
                    fraction: model::number(&p["fraction"]),
                })
                .collect()
        })
        .unwrap_or_default()
}

/// Nodes and edge labels of a first-product diagram: source, first product, selected nuclide.
/// The first product is the selected nuclide itself for a one-step route.
fn first_product_chain(product: &FirstProduct, selected: &str) -> (Vec<String>, Vec<String>) {
    if product.first_product == selected {
        (
            vec![product.from.clone(), selected.to_owned()],
            vec![String::new()],
        )
    } else {
        (
            vec![
                product.from.clone(),
                product.first_product.clone(),
                selected.to_owned(),
            ],
            vec![String::new(), "via chain".to_owned()],
        )
    }
}

fn route_from_json(p: &Value) -> Route {
    let source = p["source"].as_str().unwrap_or("?").to_owned();
    let mut nuclides = strings(&p["nuclides"]);
    if nuclides.is_empty() {
        nuclides.push(source.clone());
    }
    Route {
        source,
        nuclides,
        edges: strings(&p["edges"]),
        atoms_per_g: model::number(&p["atoms_per_g"]),
        fraction: model::number(&p["fraction"]),
        is_loop: p["is_loop"].as_bool().unwrap_or(false),
        loop_gain: p["loop_gain"].as_f64(),
        base_path: strings(&p["base_path"]),
        by_step: p["atoms_per_g_by_step"]
            .as_array()
            .map(|a| a.iter().map(model::number).collect())
            .unwrap_or_default(),
    }
}

fn target_from_json(t: &Value) -> Option<RouteTarget> {
    let target = t["target"].as_str()?.to_owned();
    let total = model::number(&t["total_atoms_per_g"]);
    let routes_total = model::number(&t["paths_atoms_per_g"]);
    let explained_fraction = t["explained_fraction"].as_f64().unwrap_or(if total > 0. {
        routes_total / total
    } else {
        0.
    });
    let residual = t["residual_atoms_per_g"]
        .as_f64()
        .unwrap_or(total - routes_total);
    let residual_fraction =
        t["residual_fraction"]
            .as_f64()
            .unwrap_or(if total > 0. { residual / total } else { 0. });
    let mut routes: Vec<Route> = t["paths"]
        .as_array()
        .map(|a| a.iter().map(route_from_json).collect())
        .unwrap_or_default();
    // The result lists routes by contribution; a stable sort keeps that order for ties and
    // repairs a hand-edited file.
    routes.sort_by(|a, b| b.atoms_per_g.total_cmp(&a.atoms_per_g));
    let generic = t["generic"]
        .as_array()
        .map(|a| {
            a.iter()
                .map(|g| Generic {
                    edge_types: g["edge_types"].as_str().unwrap_or("").to_owned(),
                    n_paths: count(&g["n_paths"]),
                    atoms_per_g: model::number(&g["atoms_per_g"]),
                    fraction: model::number(&g["fraction"]),
                })
                .collect()
        })
        .unwrap_or_default();
    Some(RouteTarget {
        target,
        total,
        routes_total,
        residual,
        residual_fraction,
        explained_fraction,
        loop_atoms: model::number(&t["loop_atoms_per_g"]),
        dropped_by_cap: count(&t["paths_dropped_by_cap"]),
        candidates_dropped: count(&t["candidates_dropped_by_cap"]),
        search_truncated: t["search_truncated"].as_bool().unwrap_or(false),
        truncated_sources: strings(&t["truncated_sources"]),
        routes,
        generic,
    })
}

/// The targets of `pathway_routes`, empty when the analysis was not requested.
fn route_targets(value: &Value) -> Vec<RouteTarget> {
    value["pathway_routes"]["targets"]
        .as_array()
        .map(|a| a.iter().filter_map(target_from_json).collect())
        .unwrap_or_default()
}

/// `Fe58 [(n,g)] Fe59 [b-] Co59`: each edge label in brackets, since labels such as `b-` hold dashes.
fn route_text(route: &Route) -> String {
    let mut text = route.nuclides.first().cloned().unwrap_or_default();
    for (i, nuclide) in route.nuclides.iter().enumerate().skip(1) {
        let edge = route.edges.get(i - 1).map(String::as_str).unwrap_or("?");
        text.push_str(&format!(" [{edge}] {nuclide}"));
    }
    text
}

/// The loop column: empty for a loop-free route.
fn loop_label(route: &Route) -> String {
    match (route.is_loop, route.loop_gain) {
        (false, _) => String::new(),
        (true, Some(gain)) => format!("loop (x{gain:.3})"),
        (true, None) => "loop".to_owned(),
    }
}

fn csv_field(text: &str) -> String {
    if text.contains([',', '"', '\n']) {
        format!("\"{}\"", text.replace('"', "\"\""))
    } else {
        text.to_owned()
    }
}

fn routes_csv(target: &RouteTarget) -> String {
    let mut out = String::from("rank,source,route,edges,atoms_per_g,fraction_of_target,is_loop\n");
    for (i, route) in target.routes.iter().enumerate() {
        out.push_str(&format!(
            "{},{},{},{},{:.9e},{:.9e},{}\n",
            i + 1,
            csv_field(&route.source),
            csv_field(&route_text(route)),
            csv_field(&route.edges.join(" ")),
            route.atoms_per_g,
            route.fraction,
            route.is_loop
        ));
    }
    out
}

/// Draw one chain of nodes joined by arrows on row `index` of `rect`: the node and edge style of
/// the first-product diagram, for any number of nodes.
#[allow(clippy::too_many_arguments)]
fn draw_chain(
    ui: &mut egui::Ui,
    rect: egui::Rect,
    salt: &'static str,
    index: usize,
    names: &[String],
    edges: &[String],
    caption: &str,
    inspected: &mut String,
) {
    let y = rect.top() + 26. + index as f32 * 52.;
    let xs: Vec<f32> = (0..names.len())
        .map(|i| {
            if names.len() < 2 {
                rect.center().x
            } else {
                rect.left() + 65. + (rect.width() - 130.) * i as f32 / (names.len() - 1) as f32
            }
        })
        .collect();
    for i in 0..names.len().saturating_sub(1) {
        let from = egui::pos2(xs[i] + 47., y);
        let to = egui::pos2(xs[i + 1] - 50., y);
        ui.painter()
            .line_segment([from, to], egui::Stroke::new(1.5, BLUE));
        ui.painter().add(egui::Shape::convex_polygon(
            vec![to, to + Vec2::new(-9., -5.), to + Vec2::new(-9., 5.)],
            BLUE,
            egui::Stroke::NONE,
        ));
        if let Some(label) = edges.get(i).filter(|l| !l.is_empty()) {
            ui.painter().text(
                egui::pos2((from.x + to.x) / 2., y - 12.),
                egui::Align2::CENTER_CENTER,
                label,
                egui::FontId::proportional(10.),
                Color32::DARK_GRAY,
            );
        }
    }
    for (i, (x, name)) in xs.into_iter().zip(names).enumerate() {
        let node = egui::Rect::from_center_size(egui::pos2(x, y), Vec2::new(95., 32.));
        if ui
            .interact(node, egui::Id::new((salt, index, i)), egui::Sense::click())
            .on_hover_text("Click to inspect this nuclide at the selected step")
            .clicked()
        {
            *inspected = name.clone();
        }
        ui.painter()
            .rect_filled(node, 5., Color32::from_rgb(229, 226, 250));
        ui.painter().text(
            node.center(),
            egui::Align2::CENTER_CENTER,
            name,
            egui::FontId::proportional(14.),
            BLUE,
        );
    }
    ui.painter().text(
        egui::pos2(rect.center().x, y + 20.),
        egui::Align2::CENTER_TOP,
        caption,
        egui::FontId::proportional(10.),
        Color32::DARK_GRAY,
    );
}

/// The clicked node's inventory and activity at the selected step.
fn inspector(ui: &mut egui::Ui, step_value: &Value, step: usize, node: &str) {
    if node.is_empty() {
        return;
    }
    let atom = step_value["inventory"]
        .as_array()
        .and_then(|rows| rows.iter().find(|r| r["nuclide"].as_str() == Some(node)))
        .and_then(|r| r["atoms_per_g"].as_f64());
    ui.group(|ui| {
        ui.strong(format!("{node} at step {}", step + 1));
        if let Some(atoms) = atom {
            ui.label(format!("{atoms:.6e} atoms/g"));
        } else {
            ui.label("No inventory entry was recorded for this node at this step.");
        }
        if let Some(activity) = step_value["activity_Bq_per_g"][node].as_f64() {
            ui.label(format!("{activity:.6e} Bq/g"));
        }
    });
}

/// How much of the selected nuclide's population its listed chains account for at one step. Chains under
/// 1e-6 of the nuclide are not listed, so a full account reads 100.0000 %.
fn coverage_note(result: &Value, step: usize, selected: &str) -> Option<String> {
    let inventory = result["steps"][step]["inventory"].as_array()?;
    let atoms = |row: &Value| row["atoms_per_g"].as_f64().unwrap_or(0.);
    let population = atoms(inventory.iter().find(|row| row["nuclide"] == selected)?);
    let largest = inventory.iter().map(atoms).fold(0f64, f64::max);
    if population <= 1e-12 * largest {
        return Some(format!(
            "{selected} is at noise level at this step (under 1e-12 of the largest population), so its chains are not checked against it."
        ));
    }
    let listed: f64 = result["pathways"][step][selected]
        .as_array()?
        .iter()
        .map(atoms)
        .sum();
    Some(format!(
        "The listed chains account for {:.4}% of {selected} at this step.",
        100. * listed / population
    ))
}

/// `pathway_closure` is one number for the whole result, not for the selected nuclide.
fn closure_note(closure: f64) -> String {
    let gap = if closure < 1e-6 {
        "better than 0.0001%".to_owned()
    } else {
        format!("to within {:.4}%", 100. * closure)
    };
    format!(
        "Over the whole result, chains match every population above noise level {gap} (pathway_closure = {closure:.2e})."
    )
}

/// Pathways page body. `step` is the zero-based step selected in the shell's result bar.
pub fn show(
    ui: &mut egui::Ui,
    result: &model::ResultDocument,
    step: usize,
    state: &mut PathwaysState,
) -> PathwaysAction {
    let step = step.min(result.steps().len().saturating_sub(1));
    let targets = route_targets(&result.value);
    let has_first = first_products_available(&result.value);
    if targets.is_empty() && !has_first {
        return not_recorded(
            ui,
            "No production pathways are recorded in this result.",
            &format!(
                "Turn on Production pathways in What to compute, then {}. First products need \
                 trace mode; the full routes also need a pathways section.",
                super::RUN_AGAIN
            ),
        );
    }
    let mut mode = state.mode.unwrap_or(if targets.is_empty() {
        Mode::FirstProducts
    } else {
        Mode::Routes
    });
    ui.horizontal(|ui| {
        for (value, label) in [
            (Mode::Routes, "Full routes"),
            (Mode::FirstProducts, "First products (trace)"),
        ] {
            if ui.selectable_value(&mut mode, value, label).clicked() {
                state.mode = Some(mode);
            }
        }
    });
    ui.separator();
    match mode {
        Mode::Routes => routes_body(ui, result, step, &targets, state),
        Mode::FirstProducts => first_products_body(ui, result, step, state),
    }
}

/// An empty state that names the cause and links to the Outputs page.
fn not_recorded(ui: &mut egui::Ui, title: &str, how: &str) -> PathwaysAction {
    let mut action = PathwaysAction::None;
    ui.group(|ui| {
        ui.add_space(12.);
        ui.heading(title);
        ui.label(how);
        if ui.button("Open Outputs").clicked() {
            action = PathwaysAction::Open(Target::Outputs);
        }
        ui.add_space(12.);
    });
    action
}

fn first_products_body(
    ui: &mut egui::Ui,
    result: &model::ResultDocument,
    step: usize,
    state: &mut PathwaysState,
) -> PathwaysAction {
    let nuclides = first_product_nuclides(&result.value, step);
    if nuclides.is_empty() {
        return not_recorded(
            ui,
            "No first products were recorded for this step.",
            &format!(
                "First-product decomposition is available in trace mode. Turn on Production \
                 pathways in What to compute, then {}.",
                super::RUN_AGAIN
            ),
        );
    }
    if !nuclides.contains(&state.first_nuclide) {
        state.first_nuclide = nuclides[0].clone();
        state.scene_view = egui::Rect::ZERO;
    }
    egui::ComboBox::from_id_salt("pathway-isotope")
        .selected_text(state.first_nuclide.clone())
        .show_ui(ui, |ui| {
            for key in &nuclides {
                ui.selectable_value(&mut state.first_nuclide, key.clone(), key);
            }
        });
    let selected = state.first_nuclide.clone();
    let products = first_products(&result.value, step, &selected);
    ui.horizontal(|ui| {
        ui.label("Drag to pan; scroll to zoom.");
        if ui.small_button("Fit pathways").clicked() {
            state.scene_view = egui::Rect::ZERO;
        }
    });
    ui.allocate_ui(Vec2::new(ui.available_width(), 240.), |ui| {
        egui::Scene::new()
            .zoom_range(0.2..=2.)
            .show(ui, &mut state.scene_view, |ui| {
                let (rect, _) = ui.allocate_exact_size(
                    Vec2::new(
                        700.,
                        (products.len().min(MAX_FIRST_PRODUCTS) as f32 * 52.).max(70.),
                    ),
                    egui::Sense::hover(),
                );
                for (i, product) in products.iter().take(MAX_FIRST_PRODUCTS).enumerate() {
                    let (names, edges) = first_product_chain(product, &selected);
                    let caption = format!(
                        "{:.2}% of product atoms ({:.3e} atoms/g)",
                        100. * product.fraction,
                        product.atoms_per_g
                    );
                    draw_chain(
                        ui,
                        rect,
                        "pathway-node",
                        i,
                        &names,
                        &edges,
                        &caption,
                        &mut state.inspected,
                    );
                }
            });
    });
    inspector(ui, &result.steps()[step], step, &state.inspected);
    ui.label("Source > first product > selected nuclide. Intermediate chain members are not enumerated here; the Full routes tab lists them. Diagram shows up to 12 ranked contributions.");
    if let Some(note) = coverage_note(&result.value, step, &selected) {
        ui.label(note);
    }
    if let Some(closure) = result.value["pathway_closure"].as_f64() {
        ui.label(egui::RichText::new(closure_note(closure)).weak());
    }
    json_tree(
        ui,
        "All contributions",
        &result.value["pathways"][step][&selected],
    );
    PathwaysAction::None
}

fn routes_body(
    ui: &mut egui::Ui,
    result: &model::ResultDocument,
    step: usize,
    targets: &[RouteTarget],
    state: &mut PathwaysState,
) -> PathwaysAction {
    let mut action = PathwaysAction::None;
    if targets.is_empty() {
        return not_recorded(
            ui,
            "No full routes were recorded.",
            &format!(
                "Turn on Production pathways in What to compute and choose the target nuclides \
                 there, then {}.",
                super::RUN_AGAIN
            ),
        );
    }
    if !targets.iter().any(|t| t.target == state.target) {
        state.target = targets[0].target.clone();
        state.route = 0;
    }
    ui.horizontal(|ui| {
        ui.label("Target");
        egui::ComboBox::from_id_salt("pathway-route-target")
            .selected_text(state.target.clone())
            .show_ui(ui, |ui| {
                for t in targets {
                    if ui
                        .selectable_value(&mut state.target, t.target.clone(), &t.target)
                        .clicked()
                    {
                        state.route = 0;
                    }
                }
            });
    });
    let Some(target) = targets.iter().find(|t| t.target == state.target) else {
        return action;
    };
    if state.route >= target.routes.len() {
        state.route = 0;
    }
    if closure_card(ui, result, target) {
        action = PathwaysAction::Open(Target::Outputs);
    }
    if target.routes.is_empty() {
        ui.label("No route passed the floors for this target.");
        return action;
    }
    ui.horizontal(|ui| {
        ui.strong(format!(
            "{} routes, ranked by contribution",
            target.routes.len()
        ));
        if ui.small_button("Copy routes as CSV").clicked() {
            ui.ctx().copy_text(routes_csv(target));
        }
    });
    route_table(ui, target, state);
    let route = &target.routes[state.route];
    ui.add_space(6.);
    ui.strong(format!(
        "Route {}: {:.6e} atoms/g, {:.2}% of {}",
        state.route + 1,
        route.atoms_per_g,
        100. * route.fraction,
        target.target
    ));
    let needed = 130. + 150. * route.nuclides.len().saturating_sub(1) as f32;
    egui::ScrollArea::horizontal()
        .id_salt("pathway-route-chain")
        .auto_shrink([false, true])
        .show(ui, |ui| {
            let (rect, _) = ui.allocate_exact_size(
                Vec2::new(needed.max(ui.available_width()), 64.),
                egui::Sense::hover(),
            );
            let caption = format!("{:.2}% of {}", 100. * route.fraction, target.target);
            draw_chain(
                ui,
                rect,
                "route-node",
                0,
                &route.nuclides,
                &route.edges,
                &caption,
                &mut state.inspected,
            );
        });
    if route.is_loop {
        ui.label(format!(
            "Loop walk: {} relative to its loop-erased path {}.",
            route
                .loop_gain
                .map(|g| format!("gain x{g:.3}"))
                .unwrap_or_else(|| "gain not recorded".to_owned()),
            if route.base_path.is_empty() {
                "(not recorded)".to_owned()
            } else {
                route.base_path.join(" -> ")
            }
        ));
    }
    inspector(ui, &result.steps()[step], step, &state.inspected);
    if !route.by_step.is_empty() {
        by_step_plot(ui, result, route);
    }
    if !target.generic.is_empty() {
        egui::CollapsingHeader::new("Group by edge sequence")
            .id_salt("pathway-generic")
            .show(ui, |ui| {
                egui::Grid::new("pathway-generic-grid")
                    .striped(true)
                    .show(ui, |ui| {
                        for label in ["Edge sequence", "Routes", "atoms / g", "% target"] {
                            ui.strong(label);
                        }
                        ui.end_row();
                        for g in &target.generic {
                            ui.label(&g.edge_types);
                            ui.monospace(g.n_paths.to_string());
                            ui.monospace(format!("{:.4e}", g.atoms_per_g));
                            ui.monospace(format!("{:.2}", 100. * g.fraction));
                            ui.end_row();
                        }
                    });
            });
    }
    let notes = strings(&result.value["pathway_routes"]["notes"]);
    if !notes.is_empty() {
        egui::CollapsingHeader::new("Notes from the route search")
            .id_salt("pathway-notes")
            .show(ui, |ui| {
                for note in &notes {
                    ui.label(note);
                }
            });
    }
    action
}

/// The summary card of a target. Returns true when the user asks for the Outputs page.
fn closure_card(ui: &mut egui::Ui, result: &model::ResultDocument, target: &RouteTarget) -> bool {
    let mut open_outputs = false;
    let accent = accent(ui);
    ui.group(|ui| {
        ui.strong(format!(
            "{}: total {:.4e} atoms/g | routes {:.4e} | explained {:.1}%",
            target.target,
            target.total,
            target.routes_total,
            100. * target.explained_fraction
        ));
        ui.add(
            egui::ProgressBar::new(target.explained_fraction.clamp(0., 1.) as f32)
                .show_percentage(),
        );
        ui.label(format!(
            "residual {:.4e} atoms/g ({:.2}%) | loops {:.4e} | dropped by cap {} | search truncated: {}",
            target.residual,
            100. * target.residual_fraction,
            target.loop_atoms,
            target.dropped_by_cap,
            if target.search_truncated { "yes" } else { "no" }
        ));
        if let Some(walks) = result.value["pathway_routes"]["explored_walks"].as_u64() {
            ui.label(format!("explored walks {walks}"));
        }
        if target.candidates_dropped > 0 {
            ui.label(format!(
                "{} candidate routes beyond 8 x max_paths were never integrated.",
                target.candidates_dropped
            ));
        }
        if target.search_truncated {
            ui.colored_label(
                accent,
                if target.truncated_sources.is_empty() {
                    "The route search ran out of its budget, so routes may be missing.".to_owned()
                } else {
                    format!(
                        "The route search ran out of its budget for {}; routes may be missing.",
                        target.truncated_sources.join(", ")
                    )
                },
            );
        }
        if target.residual_fraction > RESIDUAL_HINT
            || target.search_truncated
            || target.dropped_by_cap > 0
        {
            ui.horizontal(|ui| {
                ui.label("To close the gap, lower path_floor or raise max_depth and max_paths.");
                if ui.small_button("Open Outputs").clicked() {
                    open_outputs = true;
                }
            });
        }
    });
    open_outputs
}

fn route_table(ui: &mut egui::Ui, target: &RouteTarget, state: &mut PathwaysState) {
    TableBuilder::new(ui)
        .id_salt("pathway-route-table")
        .striped(true)
        .resizable(true)
        .column(Column::initial(50.).at_least(40.))
        .column(Column::initial(380.).at_least(160.))
        .column(Column::initial(130.).at_least(60.))
        .column(Column::initial(110.).at_least(90.))
        .column(Column::initial(80.).at_least(60.))
        .column(Column::remainder().at_least(80.))
        .max_scroll_height(250.)
        .header(28., |mut h| {
            for label in ["Rank", "Route", "Edges", "atoms / g", "% target", "Loop"] {
                h.col(|ui| {
                    ui.strong(label);
                });
            }
        })
        .body(|body| {
            body.rows(27., target.routes.len(), |mut row| {
                let i = row.index();
                let route = &target.routes[i];
                row.set_selected(state.route == i);
                row.col(|ui| {
                    if ui
                        .selectable_label(state.route == i, (i + 1).to_string())
                        .clicked()
                    {
                        state.route = i;
                    }
                });
                row.col(|ui| {
                    ui.add(egui::Label::new(route_text(route)).truncate());
                });
                row.col(|ui| {
                    ui.add(egui::Label::new(route.edges.join(" ")).truncate());
                });
                row.col(|ui| {
                    ui.monospace(format!("{:.4e}", route.atoms_per_g));
                });
                row.col(|ui| {
                    ui.monospace(format!("{:.2}", 100. * route.fraction));
                });
                row.col(|ui| {
                    ui.label(loop_label(route));
                });
            });
        });
}

/// The selected route's contribution at every step, when the analysis recorded it.
fn by_step_plot(ui: &mut egui::Ui, result: &model::ResultDocument, route: &Route) {
    let on_time = route.by_step.len() == result.steps().len();
    let points: Vec<[f64; 2]> = route
        .by_step
        .iter()
        .enumerate()
        .map(|(i, v)| {
            let x = if on_time {
                model::number(&result.steps()[i]["t_s"])
            } else {
                i as f64 + 1.
            };
            [x, *v]
        })
        .collect();
    let color = accent(ui);
    Plot::new("pathway-by-step")
        .height(180.)
        .allow_scroll(false)
        .x_axis_label(if on_time { "Time (s)" } else { "Step" })
        .y_axis_label("Route contribution (atoms / g)")
        .show(ui, |p| {
            p.line(Line::new("Selected route", points).color(color))
        });
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn step(t: f64) -> Value {
        json!({"t_s": t, "inventory": [{"nuclide": "Co60", "atoms_per_g": 5.0}],
            "activity_Bq_per_g": {"Co60": 2.0}, "heat_W_per_g": {"total": 0.0}})
    }

    fn document(extra: Value) -> model::ResultDocument {
        let mut value = json!({"steps": [step(0.0), step(10.0)]});
        for (k, v) in extra.as_object().unwrap() {
            value[k] = v.clone();
        }
        model::ResultDocument::parse(value, "test".into()).unwrap()
    }

    #[test]
    fn coverage_is_the_selected_nuclides_own_and_closure_is_whole_result() {
        let value = json!({
            "steps": [{"inventory": [{"nuclide": "Fe56", "atoms_per_g": 1e22},
                                     {"nuclide": "Co58", "atoms_per_g": 2e12},
                                     {"nuclide": "Mn58", "atoms_per_g": 2.7e-28}]}],
            "pathways": [{"Co58": [{"from": "Fe58", "first_product": "Fe59", "atoms_per_g": 2e12}],
                          "Mn58": [{"from": "Fe58", "first_product": "Mn58", "atoms_per_g": 1.38e-27}]}]
        });
        assert_eq!(
            coverage_note(&value, 0, "Co58").unwrap(),
            "The listed chains account for 100.0000% of Co58 at this step."
        );
        assert!(coverage_note(&value, 0, "Mn58")
            .unwrap()
            .starts_with("Mn58 is at noise level at this step"));
        assert_eq!(coverage_note(&value, 0, "Ni60"), None);
        assert!(closure_note(2.3e-7).contains("Over the whole result"));
        assert!(closure_note(2.3e-7).contains("better than 0.0001%"));
        assert!(closure_note(0.05).contains("to within 5.0000%"));
    }

    fn routes_json() -> Value {
        json!({"schema": "actinv-pathways-1", "explored_walks": 42, "notes": ["note one"],
        "targets": [{
            "target": "Co60",
            "total_atoms_per_g": 1.2e11,
            "paths_atoms_per_g": 1.17e11,
            "residual_atoms_per_g": 3.0e9,
            "residual_fraction": 0.025,
            "explained_fraction": 0.975,
            "loop_atoms_per_g": 1.1e9,
            "paths_dropped_by_cap": 3,
            "search_truncated": true,
            "truncated_sources": ["Fe58"],
            "paths": [
                {"source": "Co59", "nuclides": ["Co59", "Co60"], "edges": ["(n,g)"],
                 "atoms_per_g": 4.2e10, "fraction": 0.35, "is_loop": false},
                {"source": "Fe58", "nuclides": ["Fe58", "Fe59", "Co59", "Co60"],
                 "edges": ["(n,g)", "b-", "(n,g)"], "atoms_per_g": 6.1e10, "fraction": 0.52,
                 "is_loop": false},
                {"source": "Co59", "nuclides": ["Co59", "Co60", "Co60"],
                 "edges": ["(n,g)", "(n,g)"], "atoms_per_g": 1.0e9, "fraction": 0.01,
                 "is_loop": true, "loop_gain": 1.5, "base_path": ["Co59", "Co60"],
                 "atoms_per_g_by_step": [0.0, 1.0e9]}
            ],
            "generic": [{"edge_types": "(n,g)", "n_paths": 1, "atoms_per_g": 4.2e10,
                "fraction": 0.35}]
        }]})
    }

    #[test]
    fn routes_are_ranked_and_closure_is_read_as_recorded() {
        let targets = route_targets(&json!({"pathway_routes": routes_json()}));
        assert_eq!(targets.len(), 1);
        let t = &targets[0];
        assert_eq!(t.target, "Co60");
        let order: Vec<&str> = t.routes.iter().map(|r| r.source.as_str()).collect();
        assert_eq!(order, ["Fe58", "Co59", "Co59"]);
        assert_eq!(t.routes[0].atoms_per_g, 6.1e10);
        assert_eq!(t.total, 1.2e11);
        assert_eq!(t.routes_total, 1.17e11);
        assert_eq!(t.residual, 3.0e9);
        assert_eq!(t.residual_fraction, 0.025);
        assert_eq!(t.explained_fraction, 0.975);
        assert_eq!(t.loop_atoms, 1.1e9);
        assert_eq!(t.dropped_by_cap, 3);
        assert_eq!(t.candidates_dropped, 0);
        assert!(t.search_truncated);
        assert_eq!(t.truncated_sources, ["Fe58"]);
        assert_eq!(t.generic[0].edge_types, "(n,g)");
        assert_eq!(t.generic[0].n_paths, 1);
    }

    #[test]
    fn missing_closure_fields_are_derived_not_invented() {
        let t = target_from_json(&json!({
            "target": "X", "total_atoms_per_g": 10.0, "paths_atoms_per_g": 8.0, "paths": []
        }))
        .unwrap();
        assert_eq!(t.explained_fraction, 0.8);
        assert_eq!(t.residual, 2.0);
        assert!((t.residual_fraction - 0.2).abs() < 1e-12);
        assert!(!t.search_truncated);
        assert!(target_from_json(&json!({"total_atoms_per_g": 1.0})).is_none());
        assert!(route_targets(&json!({})).is_empty());
    }

    #[test]
    fn loops_are_flagged_and_chains_are_written_out() {
        let t = &route_targets(&json!({"pathway_routes": routes_json()}))[0];
        assert_eq!(
            route_text(&t.routes[0]),
            "Fe58 [(n,g)] Fe59 [b-] Co59 [(n,g)] Co60"
        );
        assert_eq!(loop_label(&t.routes[0]), "");
        let looped = &t.routes[2];
        assert!(looped.is_loop);
        assert_eq!(loop_label(looped), "loop (x1.500)");
        assert_eq!(looped.base_path, ["Co59", "Co60"]);
        assert_eq!(looped.by_step, [0.0, 1.0e9]);
        let one_node = route_from_json(&json!({"source": "Co60", "atoms_per_g": 1.0}));
        assert_eq!(one_node.nuclides, ["Co60"]);
        assert_eq!(route_text(&one_node), "Co60");
    }

    #[test]
    fn csv_quotes_commas_in_reaction_names() {
        let t = &route_targets(&json!({"pathway_routes": routes_json()}))[0];
        let csv = routes_csv(t);
        let lines: Vec<&str> = csv.lines().collect();
        assert_eq!(lines.len(), 4);
        assert!(lines[1].starts_with("1,Fe58,\"Fe58 [(n,g)] Fe59 [b-] Co59 [(n,g)] Co60\","));
        assert!(lines[3].ends_with(",true"));
        assert_eq!(csv_field("a\"b"), "\"a\"\"b\"");
    }

    #[test]
    fn first_products_follow_the_recorded_step() {
        let value = json!({"pathways": [
            {},
            {"Co60": [
                {"from": "Co59", "first_product": "Co60", "atoms_per_g": 3.0, "fraction": 0.75},
                {"from": "Fe58", "first_product": "Fe59", "atoms_per_g": 1.0, "fraction": 0.25}
            ]}
        ]});
        assert!(first_products_available(&value));
        assert!(first_product_nuclides(&value, 0).is_empty());
        assert_eq!(first_product_nuclides(&value, 1), ["Co60"]);
        assert!(first_products(&value, 0, "Co60").is_empty());
        let products = first_products(&value, 1, "Co60");
        assert_eq!(products.len(), 2);
        assert_eq!(products[0].fraction, 0.75);
        let (names, edges) = first_product_chain(&products[0], "Co60");
        assert_eq!(names, ["Co59", "Co60"]);
        assert_eq!(edges, [""]);
        let (names, edges) = first_product_chain(&products[1], "Co60");
        assert_eq!(names, ["Fe58", "Fe59", "Co60"]);
        assert_eq!(edges, ["", "via chain"]);
        assert!(!first_products_available(&json!({"pathways": [{}, {}]})));
        assert!(!first_products_available(&json!({})));
    }

    fn render(result: &model::ResultDocument, step: usize, state: &mut PathwaysState) -> bool {
        let ctx = egui::Context::default();
        ctx.memory_mut(|m| m.set_everything_is_visible(true));
        let mut opened = false;
        let mut output = ctx.run_ui(Default::default(), |ui| {
            opened = matches!(show(ui, result, step, state), PathwaysAction::Open(_));
        });
        output.textures_delta.clear();
        opened
    }

    #[test]
    fn both_tabs_render_and_keep_their_own_selection() {
        let result = document(json!({
            "pathway_routes": routes_json(),
            "pathways": [{}, {"Co60": [
                {"from": "Co59", "first_product": "Co60", "atoms_per_g": 3.0, "fraction": 1.0}
            ]}]
        }));
        let mut state = PathwaysState::default();
        assert!(!render(&result, 1, &mut state));
        assert_eq!(state.target, "Co60");
        state.route = 7;
        assert!(!render(&result, 1, &mut state));
        assert_eq!(
            state.route, 0,
            "an out-of-range route falls back to the top rank"
        );
        state.mode = Some(Mode::FirstProducts);
        assert!(!render(&result, 1, &mut state));
        assert_eq!(state.first_nuclide, "Co60");
        assert!(
            !render(&result, 99, &mut state),
            "a step past the end is clamped"
        );
    }

    #[test]
    fn a_result_without_pathway_data_says_how_to_enable_it() {
        let result = document(json!({}));
        let mut state = PathwaysState::default();
        assert!(!render(&result, 0, &mut state));
        assert!(state.target.is_empty() && state.first_nuclide.is_empty());
        let tutorial = model::tutorial_result();
        assert!(!render(&tutorial, 0, &mut state));
    }
}
