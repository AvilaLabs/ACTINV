//! The parts the waste questions share: the question tiles at the top of the Waste class page, the
//! component a class is computed for, and the nuclide properties every class needs (Z, half-life,
//! alpha emission), read from the decay data on the desktop or entered by hand.
use crate::{model, visuals::accent};
use actinv_core::waste::{normalize_nuclide_key, NuclideProperties};
use eframe::egui::{self, Color32, RichText};
use egui_extras::{Column, TableBuilder};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};

pub const RULES: &str = "us-nrc-10cfr61.55-v1";

/// The four waste questions, in the order the page offers them.
#[derive(Clone, Copy, PartialEq, Eq, Debug, Default)]
pub enum Question {
    #[default]
    Class,
    Container,
    Ranges,
    HowSure,
}

impl Question {
    pub const ALL: [Question; 4] = [
        Question::Class,
        Question::Container,
        Question::Ranges,
        Question::HowSure,
    ];

    pub fn title(self) -> &'static str {
        match self {
            Question::Class => "What class is this result?",
            Question::Container => "What class is a container of several items?",
            Question::Ranges => "Which classes can it reach if activities are ranges?",
            Question::HowSure => "How sure is the class?",
        }
    }

    pub fn note(self) -> &'static str {
        match self {
            Question::Class => "Each step of the run, against the 10 CFR 61.55 tables",
            Question::Container => "Plugs, rod sections and sealed sources averaged together",
            Question::Ranges => "Activities known only as lower and upper values; no run needed",
            Question::HowSure => "Samples the nuclear-data uncertainty; one solve per sample",
        }
    }
}

/// The questions as tiles, two to a row; the chosen one is outlined in the accent colour.
pub fn question_tiles(ui: &mut egui::Ui, chosen: &mut Question) {
    let accent = accent(ui);
    let gap = 10.;
    // leave room for the page's scroll bar, which floats over the right edge
    let bar = ui.spacing().scroll.bar_width + ui.spacing().scroll.bar_outer_margin;
    let width = ((ui.available_width() - gap - bar) / 2.).max(220.);
    let font_title = egui::FontId::proportional(16.);
    let font_note = egui::FontId::proportional(13.);
    let weak = ui.visuals().weak_text_color();
    let text = ui.visuals().text_color();
    for pair in Question::ALL.chunks(2) {
        ui.horizontal(|ui| {
            ui.spacing_mut().item_spacing.x = gap;
            for &question in pair {
                let selected = *chosen == question;
                let wrap = width - 28.;
                let title = ui.painter().layout(
                    question.title().to_owned(),
                    font_title.clone(),
                    if selected { accent } else { text },
                    wrap,
                );
                let note =
                    ui.painter()
                        .layout(question.note().to_owned(), font_note.clone(), weak, wrap);
                let height = 14. + title.size().y + 4. + note.size().y + 14.;
                let (rect, response) =
                    ui.allocate_exact_size(egui::vec2(width, height), egui::Sense::click());
                let visuals = ui.visuals();
                let fill = if selected {
                    accent.gamma_multiply(0.10)
                } else if response.hovered() {
                    accent.gamma_multiply(0.05)
                } else {
                    visuals.extreme_bg_color
                };
                let stroke = if selected {
                    egui::Stroke::new(2., accent)
                } else {
                    visuals.widgets.noninteractive.bg_stroke
                };
                let painter = ui.painter();
                painter.rect_filled(rect, 6., fill);
                painter.rect_stroke(rect, 6., stroke, egui::StrokeKind::Inside);
                let top = rect.left_top() + egui::vec2(14., 14.);
                let below = top + egui::vec2(0., title.size().y + 4.);
                painter.galley(top, title, text);
                painter.galley(below, note, weak);
                if response
                    .on_hover_cursor(egui::CursorIcon::PointingHand)
                    .clicked()
                {
                    *chosen = question;
                }
            }
        });
        ui.add_space(gap);
    }
}

/// A section heading and its short explanation, as the newer pages draw them.
pub fn section(ui: &mut egui::Ui, title: &str, note: &str) {
    ui.add_space(10.);
    ui.heading(title);
    if !note.is_empty() {
        ui.label(note);
    }
    ui.add_space(6.);
}

/// A full-width card.
pub fn card<R>(ui: &mut egui::Ui, add: impl FnOnce(&mut egui::Ui) -> R) -> R {
    let inner = egui::Frame::group(ui.style())
        .inner_margin(12.)
        .show(ui, |ui| {
            ui.set_width(ui.available_width());
            add(ui)
        })
        .inner;
    ui.add_space(6.);
    inner
}

/// The filled accent button that starts a calculation.
pub fn primary(ui: &mut egui::Ui, enabled: bool, text: &str) -> egui::Response {
    ui.add_enabled(
        enabled,
        egui::Button::new(RichText::new(text).color(Color32::WHITE)).fill(crate::visuals::BLUE),
    )
}

/// A number field for a non-negative quantity, written in scientific notation when it is large or
/// small.
pub fn amount(ui: &mut egui::Ui, value: &mut f64, width: f32) -> egui::Response {
    ui.add_sized(
        [width, ui.spacing().interact_size.y],
        egui::DragValue::new(value)
            .custom_parser(model::parse_finite)
            .custom_formatter(|v, _| {
                if v != 0. && !(1e-3..1e6).contains(&v.abs()) {
                    format!("{v:.4e}")
                } else {
                    format!("{v}")
                }
            })
            .speed(0.)
            .range(0.0..=f64::MAX),
    )
}

/// One component's shape for the class tables: waste type and the volume its concentrations use.
#[derive(Clone, Debug)]
pub struct Component {
    pub activated_metal: bool,
    /// Derive the volume from the density instead of entering it.
    pub use_density: bool,
    pub volume_cm3: f64,
    pub density_g_cm3: f64,
}

impl Default for Component {
    fn default() -> Self {
        Self {
            activated_metal: true,
            use_density: true,
            volume_cm3: 0.,
            density_g_cm3: 0.,
        }
    }
}

impl Component {
    /// The `waste_type` and geometry keys of a component; `mass_g` is the caller's.
    pub fn fields(&self, mass_g: f64) -> Result<Value, String> {
        if !(mass_g.is_finite() && mass_g > 0.) {
            return Err("The component mass must be positive.".into());
        }
        let (key, value, what) = if self.use_density {
            ("density_g_cm3", self.density_g_cm3, "density")
        } else {
            ("displaced_volume_cm3", self.volume_cm3, "displaced volume")
        };
        if !(value.is_finite() && value > 0.) {
            return Err(format!("Enter a positive {what} for the component."));
        }
        let mut out = json!({
            "mass_g": mass_g,
            "waste_type": if self.activated_metal { "activated_metal" } else { "general" },
        });
        out[key] = json!(value);
        Ok(out)
    }

    /// Waste type and volume, on one card. `density` is the problem's, offered as the default.
    pub fn edit(&mut self, ui: &mut egui::Ui, salt: &str, mass_g: f64) {
        ui.horizontal(|ui| {
            ui.label("Waste type");
            ui.selectable_value(&mut self.activated_metal, true, "Activated metal");
            ui.selectable_value(&mut self.activated_metal, false, "General");
        });
        ui.horizontal(|ui| {
            ui.label("Volume from");
            ui.selectable_value(&mut self.use_density, true, "the density");
            ui.selectable_value(&mut self.use_density, false, "a displaced volume");
        });
        ui.horizontal(|ui| {
            ui.push_id(salt, |ui| {
                if self.use_density {
                    ui.label("Density");
                    amount(ui, &mut self.density_g_cm3, 90.);
                    ui.label("g/cm³");
                    if self.density_g_cm3 > 0. && mass_g > 0. {
                        ui.weak(format!(
                            "volume {} cm³, the solid volume with no voids",
                            three(mass_g / self.density_g_cm3)
                        ));
                    }
                } else {
                    ui.label("Displaced volume");
                    amount(ui, &mut self.volume_cm3, 90.);
                    ui.label("cm³");
                    ui.weak("the envelope with major voids removed");
                }
            });
        });
    }
}

/// Three significant figures, in scientific notation outside 0.01 to 10⁵.
pub fn three(value: f64) -> String {
    if value == 0. {
        "0".into()
    } else if !(1e-2..1e5).contains(&value.abs()) {
        format!("{value:.2e}")
    } else {
        let digits = (2 - value.abs().log10().floor() as i32).max(0) as usize;
        format!("{value:.digits$}")
    }
}

pub fn canonical(name: &str) -> String {
    normalize_nuclide_key(name).unwrap_or_else(|_| name.to_owned())
}

/// The atomic number of the element a nuclide name starts with.
pub fn z_of(nuclide: &str) -> Option<i32> {
    let symbol: String = nuclide
        .trim()
        .chars()
        .take_while(|c| c.is_ascii_alphabetic())
        .enumerate()
        .map(|(i, c)| {
            if i == 0 {
                c.to_ascii_uppercase()
            } else {
                c.to_ascii_lowercase()
            }
        })
        .collect();
    actinv_data::composition::z_of(&symbol)
}

/// The properties every class needs, shared by the questions: what the decay data gave for each
/// nuclide looked up, and the user's own entries, which take precedence.
#[derive(Default)]
pub struct Properties {
    /// Every nuclide looked up, by normalized key, with the decay data's entry when it has one.
    pub found: BTreeMap<String, Option<NuclideProperties>>,
    /// The problem's `decay` section the lookups were made with.
    pub decay: Value,
    pub overrides: BTreeMap<String, NuclideProperties>,
}

impl Properties {
    /// The nuclides of `active` the decay data has not been asked about yet. A changed `decay`
    /// section makes every earlier answer stale.
    pub fn to_look_up(&self, active: &BTreeSet<String>, decay: &Value) -> BTreeSet<String> {
        active
            .iter()
            .filter(|name| {
                let key = canonical(name);
                !self.overrides.contains_key(&key)
                    && (&self.decay != decay || !self.found.contains_key(&key))
            })
            .cloned()
            .collect()
    }

    /// True when every nuclide of `active` has been looked up or entered.
    pub fn ready(&self, active: &BTreeSet<String>, decay: &Value) -> bool {
        self.to_look_up(active, decay).is_empty()
    }

    /// Keep what the decay data gave for `asked`.
    #[cfg_attr(target_arch = "wasm32", allow(dead_code))]
    pub fn absorb(
        &mut self,
        asked: &BTreeSet<String>,
        decay: Value,
        derived: &BTreeMap<String, NuclideProperties>,
    ) {
        if self.decay != decay {
            self.found.clear();
            self.decay = decay;
        }
        let by_key: BTreeMap<String, NuclideProperties> = derived
            .iter()
            .map(|(name, p)| (canonical(name), *p))
            .collect();
        for name in asked {
            let key = canonical(name);
            let entry = by_key.get(&key).copied();
            self.found.insert(key, entry);
        }
    }

    /// The `nuclide_properties` map for `active`: an entry where there is one, else the decay
    /// data's value. A nuclide with neither stays absent, so the evaluator reports it as unknown.
    pub fn map(&self, active: &BTreeSet<String>) -> Result<Value, String> {
        let mut out = serde_json::Map::new();
        for name in active {
            let key = canonical(name);
            let chosen = match self.overrides.get(&key) {
                Some(entry) => {
                    if entry.z < 1 || !entry.half_life_s.is_finite() || entry.half_life_s <= 0. {
                        return Err(format!(
                            "The entry for {name} needs Z of at least 1 and a positive half-life."
                        ));
                    }
                    Some(*entry)
                }
                None => self.found.get(&key).copied().flatten(),
            };
            if let Some(p) = chosen {
                out.insert(key, serde_json::to_value(p).map_err(|e| e.to_string())?);
            }
        }
        Ok(Value::Object(out))
    }

    /// The nuclides of `active` that will be left without properties.
    pub fn blank(&self, active: &BTreeSet<String>) -> Vec<String> {
        active
            .iter()
            .filter(|name| {
                let key = canonical(name);
                !self.overrides.contains_key(&key) && !matches!(self.found.get(&key), Some(Some(_)))
            })
            .cloned()
            .collect()
    }
}

/// The properties card: a button to read them from the decay data when `can_load`, and the table
/// of every active nuclide. Returns true when the button was pressed.
pub fn properties_card(
    ui: &mut egui::Ui,
    salt: &str,
    properties: &mut Properties,
    active: &BTreeSet<String>,
    decay: &Value,
    can_load: Option<bool>,
) -> bool {
    let mut load = false;
    card(ui, |ui| {
        ui.horizontal_wrapped(|ui| {
            let ready = properties.ready(active, decay);
            if let Some(enabled) = can_load {
                // the next step until every nuclide is read, then an ordinary button
                let button = if ready {
                    ui.add_enabled(enabled, egui::Button::new("Load from decay data"))
                } else {
                    primary(ui, enabled, "Load from decay data")
                };
                if button.clicked() {
                    load = true;
                }
            }
            let blank = properties.blank(active).len();
            ui.label(match (active.len(), ready, blank) {
                (0, ..) => "No active nuclides yet.".to_owned(),
                (n, false, _) if can_load.is_some() => {
                    format!("{n} nuclides; not all read from the decay data yet.")
                }
                (n, _, 0) => format!("{n} nuclides, every one with properties."),
                (n, _, b) => format!(
                    "{n} nuclides; {b} without properties, which leaves the class unknown unless you enter them."
                ),
            });
        });
        property_table(ui, salt, properties, active, can_load.is_some());
    });
    load
}

fn property_table(
    ui: &mut egui::Ui,
    salt: &str,
    properties: &mut Properties,
    active: &BTreeSet<String>,
    from_data: bool,
) {
    let marker = accent(ui);
    let warn = ui.visuals().warn_fg_color;
    let rows: Vec<&String> = active.iter().collect();
    // a row's button cannot change the map the row is drawn from; it is applied after
    let mut change: Option<(String, Option<NuclideProperties>)> = None;
    TableBuilder::new(ui)
        .id_salt(("waste-properties", salt))
        .striped(true)
        .cell_layout(egui::Layout::left_to_right(egui::Align::Center))
        .column(Column::initial(90.).at_least(60.))
        .column(Column::initial(60.).at_least(50.))
        .column(Column::initial(150.).at_least(90.))
        .column(Column::initial(60.).at_least(40.))
        .column(Column::initial(120.).at_least(80.))
        .column(Column::remainder().at_least(80.))
        .max_scroll_height(260.)
        .header(24., |mut header| {
            for label in ["Nuclide", "Z", "Half-life (s)", "Alpha", "From", ""] {
                header.col(|ui| {
                    ui.strong(label);
                });
            }
        })
        .body(|body| {
            body.rows(26., rows.len(), |mut row| {
                let name = rows[row.index()];
                let key = canonical(name);
                row.col(|ui| {
                    ui.monospace(name);
                });
                if let Some(edit) = properties.overrides.get_mut(&key) {
                    row.col(|ui| {
                        ui.add(egui::DragValue::new(&mut edit.z).range(0..=120));
                    });
                    row.col(|ui| {
                        ui.add(
                            egui::DragValue::new(&mut edit.half_life_s)
                                .custom_parser(model::parse_finite)
                                .custom_formatter(|v, _| format!("{v:.6e}"))
                                .range(0.0..=f64::MAX),
                        );
                    });
                    row.col(|ui| {
                        ui.checkbox(&mut edit.alpha_emitting, "");
                    });
                    row.col(|ui| {
                        ui.label(RichText::new("your entry").strong().color(marker));
                    });
                    row.col(|ui| {
                        if ui.small_button("Clear").clicked() {
                            change = Some((key.clone(), None));
                        }
                    });
                } else {
                    let found = properties.found.get(&key).copied().flatten();
                    let looked = properties.found.contains_key(&key);
                    row.col(|ui| {
                        ui.monospace(found.map_or("–".to_owned(), |p| p.z.to_string()));
                    });
                    row.col(|ui| {
                        ui.monospace(
                            found.map_or("–".to_owned(), |p| format!("{:.6e}", p.half_life_s)),
                        );
                    });
                    row.col(|ui| {
                        ui.monospace(found.map_or("–", |p| {
                            if p.alpha_emitting {
                                "yes"
                            } else {
                                "no"
                            }
                        }));
                    });
                    row.col(|ui| match (found, looked, from_data) {
                        (Some(_), ..) => {
                            ui.label("decay data");
                        }
                        (None, true, _) => {
                            ui.label(RichText::new("no decay record").color(warn));
                        }
                        (None, false, true) => {
                            ui.weak("not loaded");
                        }
                        (None, false, false) => {
                            ui.label(RichText::new("enter it").color(warn));
                        }
                    });
                    row.col(|ui| {
                        if ui.small_button("Enter").clicked() {
                            // start from the decay data's value; with none, from Z alone and a
                            // half-life the class refuses until it is filled in
                            let start = found.unwrap_or(NuclideProperties {
                                z: z_of(name).unwrap_or(0),
                                half_life_s: 0.,
                                alpha_emitting: false,
                            });
                            change = Some((key.clone(), Some(start)));
                        }
                    });
                }
            });
        });
    match change {
        Some((key, Some(entry))) => {
            properties.overrides.insert(key, entry);
        }
        Some((key, None)) => {
            properties.overrides.remove(&key);
        }
        None => {}
    }
}

/// Class words and colours shared by every waste result.
pub fn class_words(class: &str) -> String {
    match class {
        "A" | "B" | "C" => format!("Class {class}"),
        "above_class_c" => "Above Class C".into(),
        "unknown" => "Unknown".into(),
        "not_established" => "Not established".into(),
        other => other.replace('_', " "),
    }
}

pub fn class_colour(ui: &egui::Ui, class: &str) -> Color32 {
    match class {
        "A" => accent(ui),
        "B" => ui.visuals().warn_fg_color,
        "C" | "above_class_c" => ui.visuals().error_fg_color,
        _ => ui.visuals().weak_text_color(),
    }
}

/// A class as a short coloured word.
pub fn class_chip(ui: &mut egui::Ui, class: &str) {
    ui.label(
        RichText::new(class_words(class))
            .strong()
            .color(class_colour(ui, class)),
    );
}

/// The steps of a run as checkboxes; `skipped` holds the steps taken out.
pub fn step_choice(
    ui: &mut egui::Ui,
    salt: &str,
    steps: &[(u64, String)],
    skipped: &mut BTreeSet<u64>,
) {
    ui.horizontal(|ui| {
        if ui.small_button("All").clicked() {
            skipped.clear();
        }
        if ui.small_button("Last only").clicked() {
            skipped.clear();
            skipped.extend(steps.iter().rev().skip(1).map(|(n, _)| *n));
        }
    });
    egui::ScrollArea::vertical()
        .id_salt(("waste-steps", salt))
        .max_height(130.)
        .show(ui, |ui| {
            ui.horizontal_wrapped(|ui| {
                for (number, label) in steps {
                    let mut on = !skipped.contains(number);
                    if ui
                        .checkbox(&mut on, format!("{number} · {label}"))
                        .changed()
                    {
                        if on {
                            skipped.remove(number);
                        } else {
                            skipped.insert(*number);
                        }
                    }
                }
            });
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fe55() -> NuclideProperties {
        NuclideProperties {
            z: 26,
            half_life_s: 8.66e7,
            alpha_emitting: false,
        }
    }

    #[test]
    fn lookups_are_kept_per_nuclide_and_go_stale_with_the_decay_data() {
        let mut p = Properties::default();
        let decay = json!({"primary": "a"});
        let active: BTreeSet<String> = ["Fe55", "Xx99"].map(str::to_owned).into();
        assert_eq!(p.to_look_up(&active, &decay), active);
        p.absorb(
            &active,
            decay.clone(),
            &BTreeMap::from([("Fe55".to_owned(), fe55())]),
        );
        assert!(p.ready(&active, &decay));
        assert_eq!(p.blank(&active), ["Xx99"]);
        let map = p.map(&active).unwrap();
        assert_eq!(map["Fe55"]["z"], 26);
        assert!(map.get("Xx99").is_none());
        // a wider set asks only for the new nuclide
        let wider: BTreeSet<String> = ["Fe55", "Xx99", "Co60"].map(str::to_owned).into();
        assert_eq!(p.to_look_up(&wider, &decay), ["Co60".to_owned()].into());
        // other decay data makes every lookup stale
        assert_eq!(p.to_look_up(&active, &json!({"primary": "b"})), active);
    }

    #[test]
    fn an_entry_replaces_the_decay_data_and_must_be_complete() {
        let mut p = Properties::default();
        let active: BTreeSet<String> = ["Nb94".to_owned()].into();
        p.overrides.insert(
            "Nb94".into(),
            NuclideProperties {
                z: 41,
                half_life_s: 0.,
                alpha_emitting: false,
            },
        );
        assert!(p.ready(&active, &Value::Null));
        assert!(p.map(&active).unwrap_err().contains("Nb94"));
        p.overrides.get_mut("Nb94").unwrap().half_life_s = 6.4e11;
        assert_eq!(p.map(&active).unwrap()["Nb94"]["z"], 41);
        assert!(p.blank(&active).is_empty());
    }

    #[test]
    fn z_comes_from_the_element_symbol() {
        assert_eq!(z_of("Nb94"), Some(41));
        assert_eq!(z_of("Co60m"), Some(27));
        assert_eq!(z_of("H3"), Some(1));
        assert_eq!(z_of("Mn54"), Some(25));
        assert_eq!(z_of("CO60"), Some(27));
        assert_eq!(z_of("Zz1"), None);
    }

    #[test]
    fn a_component_needs_a_positive_volume_basis() {
        let mut c = Component {
            density_g_cm3: 7.8,
            ..Component::default()
        };
        let fields = c.fields(1000.).unwrap();
        assert_eq!(fields["density_g_cm3"], 7.8);
        assert_eq!(fields["waste_type"], "activated_metal");
        c.use_density = false;
        assert!(c.fields(1000.).unwrap_err().contains("displaced volume"));
        c.volume_cm3 = 128.2;
        assert_eq!(c.fields(1000.).unwrap()["displaced_volume_cm3"], 128.2);
        assert!(c.fields(0.).is_err());
    }
}
