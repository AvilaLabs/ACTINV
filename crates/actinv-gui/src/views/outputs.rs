//! Outputs: choose what the solver computes. One card per feature, each with an enable box, the
//! fields that feature owns and a status. Every control writes one documented spec field, and the
//! conflict text is the solver's own validation message (`Spec::from_json`); no rule is repeated here.
use crate::views::{Marks, Picker, Pinned, Target};
use actinv_core::{
    radiological,
    spec::{Spec, POINT_SOURCE_MIN_DISTANCE_M},
};
use actinv_data::composition::{self, MaterialKey};
use eframe::egui::{self, RichText};
use serde_json::{json, Value};
use std::{collections::BTreeSet, ops::RangeInclusive, sync::Arc};

/// What the solver computes when `options.outputs` is absent: `run.rs` builds the photon source
/// (photons or dose) and the first-product pathways unless a list names the outputs, and
/// inventory, activity and heat are in every result step. The first edit writes this list out.
pub const DEFAULT_OUTPUTS: [&str; 5] = ["inventory", "activity", "heat", "photons", "pathways"];

/// Page the shell must open next, if any.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum OutputsAction {
    None,
    Open(Target),
    /// Run `eval-spread` and put its suggestion into `uncertainty.unmodeled_relative`.
    EstimateSpread,
}

/// What the shell lets the uncertainty card do about the unmodeled error.
#[derive(Default)]
pub struct Spread {
    /// The shell can run `eval-spread` (the desktop app can, the browser cannot).
    pub can_run: bool,
    /// An estimate is running.
    pub running: bool,
    /// What the last estimate found.
    pub note: Option<String>,
}

/// A name list being edited: the text box and the reason the last entry was refused.
#[derive(Default)]
struct ListDraft {
    text: String,
    note: Option<String>,
}

/// Comma-separated response IDs being edited, and the document value they were read from.
#[derive(Default)]
struct IdsDraft {
    text: String,
    source: String,
}

/// Transient state of the page: drafts, the cached validation message and the cached table text.
#[derive(Default)]
pub struct OutputsState {
    document_text: String,
    problem: Option<String>,
    table: Option<(String, Arc<Result<TableInfo, String>>)>,
    custom_table: bool,
    nuclides: ListDraft,
    targets: ListDraft,
    sources: ListDraft,
    choose: ListDraft,
    uncertainty_ids: IdsDraft,
    table_ids: IdsDraft,
    relative_error: ListDraft,
    pub spread: Spread,
}

impl OutputsState {
    /// Re-validate with the solver's own rules when the document changed.
    fn refresh(&mut self, document: &Value) {
        let text = document.to_string();
        if text != self.document_text {
            self.problem = Spec::from_json(&text).err();
            self.document_text = text;
        }
    }

    fn table(&mut self, name: &str) -> Arc<Result<TableInfo, String>> {
        if let Some((cached, info)) = &self.table {
            if cached == name {
                return Arc::clone(info);
            }
        }
        let info = Arc::new(table_info(name));
        self.table = Some((name.to_owned(), Arc::clone(&info)));
        info
    }
}

// ---- document access by dotted spec path ---------------------------------------------------------

static NULL: Value = Value::Null;

fn get<'a>(doc: &'a Value, path: &str) -> &'a Value {
    path.split('.')
        .try_fold(doc, |node, key| node.get(key))
        .unwrap_or(&NULL)
}

fn set(doc: &mut Value, path: &str, value: Value) {
    let keys: Vec<&str> = path.split('.').collect();
    set_keys(doc, &keys, value);
}

fn set_keys(node: &mut Value, keys: &[&str], value: Value) {
    match keys.split_first() {
        None => *node = value,
        Some((key, rest)) => {
            if !node.is_object() {
                *node = json!({});
            }
            if let Some(map) = node.as_object_mut() {
                set_keys(map.entry(*key).or_insert(Value::Null), rest, value);
            }
        }
    }
}

fn remove(doc: &mut Value, path: &str) {
    let (parent, key) = match path.rsplit_once('.') {
        Some((parent, key)) => (Some(parent), key),
        None => (None, path),
    };
    let node = match parent {
        Some(parent) => parent
            .split('.')
            .try_fold(doc, |node, key| node.get_mut(key)),
        None => Some(doc),
    };
    if let Some(map) = node.and_then(Value::as_object_mut) {
        map.remove(key);
    }
}

fn f64_at(doc: &Value, path: &str, default: f64) -> f64 {
    get(doc, path).as_f64().unwrap_or(default)
}

fn bool_at(doc: &Value, path: &str, default: bool) -> bool {
    get(doc, path).as_bool().unwrap_or(default)
}

fn str_at<'a>(doc: &'a Value, path: &str) -> &'a str {
    get(doc, path).as_str().unwrap_or("")
}

fn strings_at(doc: &Value, path: &str) -> Vec<String> {
    get(doc, path)
        .as_array()
        .map(|list| {
            list.iter()
                .filter_map(Value::as_str)
                .map(str::to_owned)
                .collect()
        })
        .unwrap_or_default()
}

// ---- options.outputs ----------------------------------------------------------------------------

/// The outputs the solver would compute for this document.
pub fn effective_outputs(doc: &Value) -> Vec<String> {
    match get(doc, "options.outputs").as_array() {
        Some(_) => strings_at(doc, "options.outputs"),
        None => DEFAULT_OUTPUTS.iter().map(|o| (*o).to_owned()).collect(),
    }
}

/// Whether the solver would compute `key`; `dose` is accepted wherever `photons` is.
pub fn output_enabled(doc: &Value, key: &str) -> bool {
    effective_outputs(doc)
        .iter()
        .any(|o| o == key || (key == "photons" && o == "dose"))
}

/// Switch one output. The first edit of a document without `options.outputs` starts from the
/// solver's real default set, so nothing else changes; a list that ends up equal to that set is
/// written back as absent.
pub fn set_output(doc: &mut Value, key: &str, on: bool) {
    if output_enabled(doc, key) == on {
        return;
    }
    let mut list = effective_outputs(doc);
    list.retain(|o| o != key && !(key == "photons" && o == "dose"));
    if on {
        list.push(key.to_owned());
    }
    let listed: BTreeSet<&str> = list.iter().map(String::as_str).collect();
    let default: BTreeSet<&str> = DEFAULT_OUTPUTS.iter().copied().collect();
    let value = if listed == default && list.len() == DEFAULT_OUTPUTS.len() {
        Value::Null
    } else {
        json!(list)
    };
    set(doc, "options.outputs", value);
}

// ---- card toggles: each writes one documented section ------------------------------------------

fn summary_on(doc: &Value) -> bool {
    get(doc, "summary").is_object()
}

fn set_summary(doc: &mut Value, on: bool) {
    if !on {
        remove(doc, "summary");
    } else if !summary_on(doc) {
        set(doc, "summary", json!({}));
    }
}

/// Depletion is a summary option, so turning it on turns the summary on.
fn set_depletion(doc: &mut Value, on: bool) {
    if on {
        set_summary(doc, true);
        set(doc, "summary.depletion", json!(true));
    } else {
        remove(doc, "summary.depletion");
    }
}

fn set_point_source(doc: &mut Value, on: bool) {
    if !on {
        remove(doc, "photon.point_source");
    } else if !get(doc, "photon.point_source").is_object() {
        set(doc, "photon.point_source", json!({"distance_m": 1.0}));
    }
}

fn set_bremsstrahlung(doc: &mut Value, on: bool) {
    if !on {
        remove(doc, "photon.bremsstrahlung");
    } else if !get(doc, "photon.bremsstrahlung").is_object() {
        set(doc, "photon.bremsstrahlung", json!({"nuclides": []}));
    }
}

fn routes_on(doc: &Value) -> bool {
    get(doc, "pathways").is_object()
}

fn set_routes(doc: &mut Value, on: bool) {
    if !on {
        remove(doc, "pathways");
    } else if !routes_on(doc) {
        set(doc, "pathways", json!({}));
    }
}

fn set_regulatory(doc: &mut Value, on: bool) {
    if !on {
        set(doc, "radiological", Value::Null);
    } else if !get(doc, "radiological").is_object() {
        set(
            doc,
            "radiological",
            json!({"table": {"path": "", "sha256": ""}, "responses": [], "require_complete": false}),
        );
    }
}

fn set_uncertainty(doc: &mut Value, on: bool) {
    if !on {
        set(doc, "uncertainty", Value::Null);
    } else if !get(doc, "uncertainty").is_object() {
        set(
            doc,
            "uncertainty",
            json!({"covariance": {"path": "", "sha256": ""}, "responses": [], "confidence_level": 0.95, "require_complete": false}),
        );
    }
}

/// `uncertainty.channels` in the solver's order, with their labels. An absent list is MF=33 alone.
const CHANNELS: [(&str, &str); 4] = [
    ("cross_section_mf33", "Cross sections (MF=33 covariance)"),
    ("decay_constants", "Half-lives (decay data)"),
    ("fission_yields", "Independent fission yields"),
    ("flux", "Flux tally statistics (relative error per group)"),
];

fn channels(doc: &Value) -> Vec<String> {
    if get(doc, "uncertainty.channels").is_array() {
        strings_at(doc, "uncertainty.channels")
    } else {
        vec![CHANNELS[0].0.to_owned()]
    }
}

fn flux_only(doc: &Value) -> bool {
    channels(doc) == ["flux"]
}

/// Turn one channel on or off. The last channel cannot be removed; MF=33 alone is written as the
/// absent list, so a default spec is unchanged. Flux-only mode needs no covariance sidecar, so an
/// unfilled covariance reference is dropped there and restored when MF=33 returns.
fn set_channel(doc: &mut Value, channel: &str, on: bool) {
    let current = channels(doc);
    let chosen: Vec<&str> = CHANNELS
        .iter()
        .map(|(id, _)| *id)
        .filter(|id| {
            if *id == channel {
                on
            } else {
                current.iter().any(|c| c == id)
            }
        })
        .collect();
    if chosen.is_empty() {
        return;
    }
    if chosen == [CHANNELS[0].0] {
        remove(doc, "uncertainty.channels");
    } else {
        set(doc, "uncertainty.channels", json!(chosen));
    }
    if chosen == ["flux"] {
        if str_at(doc, "uncertainty.covariance.path").is_empty() {
            remove(doc, "uncertainty.covariance");
        }
    } else if !get(doc, "uncertainty.covariance").is_object() {
        set(
            doc,
            "uncertainty.covariance",
            json!({"path": "", "sha256": ""}),
        );
    }
}

/// Pasted relative errors: one finite, nonnegative fraction per flux group, with an optional
/// `relative_error` header.
fn parse_relative_errors(text: &str, count: usize) -> Result<Vec<f64>, String> {
    let mut values = Vec::new();
    for (i, token) in text
        .split(|c: char| c == ',' || c.is_whitespace())
        .filter(|s| !s.is_empty())
        .enumerate()
    {
        if i == 0 && token.eq_ignore_ascii_case("relative_error") {
            continue;
        }
        let v: f64 = token
            .parse()
            .map_err(|_| format!("Value {} ('{token}') is not a number.", values.len() + 1))?;
        if !v.is_finite() || v < 0. {
            return Err(format!(
                "Group {} must be finite and nonnegative.",
                values.len() + 1
            ));
        }
        values.push(v);
    }
    if values.len() != count {
        return Err(format!(
            "Expected {count} values, one per flux group, found {}.",
            values.len()
        ));
    }
    Ok(values)
}

fn geometry_on(doc: &Value) -> bool {
    get(doc, "self_shielding.geometry").is_object()
}

/// A Bondarenko table (or its dilution settings) is in use; geometry is exclusive with it.
fn bondarenko(doc: &Value) -> bool {
    get(doc, "self_shielding").is_object()
        && (get(doc, "self_shielding.table").is_object()
            || get(doc, "self_shielding.sigma0_b").is_number()
            || get(doc, "self_shielding.dilution_overrides")
                .as_object()
                .is_some_and(|map| !map.is_empty())
            || get(doc, "self_shielding.dilution")
                .as_str()
                .is_some_and(|dilution| dilution != "composition"))
}

fn set_geometry(doc: &mut Value, on: bool) {
    if !on {
        remove(doc, "self_shielding.geometry");
        if !get(doc, "self_shielding.table").is_object() {
            set(doc, "self_shielding", Value::Null);
        }
    } else if !geometry_on(doc) {
        set(
            doc,
            "self_shielding.geometry",
            json!({"shape": "foil", "length_cm": 0.01, "resonance_files": [], "reactions": "all"}),
        );
    }
}

fn apply_pin(doc: &mut Value, path: &str, pinned: &Pinned) {
    set(
        doc,
        path,
        json!({"path": pinned.path, "sha256": pinned.sha256}),
    );
}

// ---- bundled regulatory tables -------------------------------------------------------------------

struct ResponseInfo {
    id: String,
    kind: String,
    basis: String,
}

struct TableInfo {
    title: String,
    /// Label and text of each source field the table carries (jurisdiction, scope, citation ...).
    source: Vec<(&'static str, String)>,
    responses: Vec<ResponseInfo>,
}

/// What the picker shows for a bundled table, read from the table itself.
fn table_info(name: &str) -> Result<TableInfo, String> {
    let value: Value = serde_json::from_str(radiological::bundled_table(name)?)
        .map_err(|error| format!("bundled table '{name}': {error}"))?;
    let text = |node: &Value, key: &str| node.get(key).and_then(Value::as_str).map(str::to_owned);
    let mut source = Vec::new();
    for (label, key) in [
        ("Jurisdiction", "jurisdiction"),
        ("Scope", "scope"),
        ("Citation", "citation"),
        ("Edition", "edition"),
        ("Retrieved", "retrieved"),
        ("Licence", "licence"),
    ] {
        if let Some(entry) = text(&value["source"], key) {
            source.push((label, entry));
        }
    }
    let responses = value["responses"]
        .as_array()
        .map(|list| {
            list.iter()
                .map(|response| ResponseInfo {
                    id: text(response, "id").unwrap_or_default(),
                    kind: text(response, "kind").unwrap_or_default(),
                    basis: text(response, "basis").unwrap_or_default(),
                })
                .collect()
        })
        .unwrap_or_default();
    Ok(TableInfo {
        title: text(&value, "title").unwrap_or_else(|| name.to_owned()),
        source,
        responses,
    })
}

const LUNG_CLASSES: [&str; 3] = ["fast", "medium", "slow"];

/// The uranium lung-absorption class a bundled response ID ends in (`...-u-fast`).
fn lung_class(id: &str) -> Option<&'static str> {
    LUNG_CLASSES
        .into_iter()
        .find(|class| id.ends_with(&format!("-u-{class}")))
}

fn has_lung_classes(info: &TableInfo) -> bool {
    info.responses.iter().any(|r| lung_class(&r.id).is_some())
}

/// The class selected in `radiological.responses`, if any. There is no default.
fn chosen_lung(doc: &Value) -> Option<&'static str> {
    strings_at(doc, "radiological.responses")
        .iter()
        .find_map(|id| lung_class(id))
}

/// Select (or with `None` clear) the uranium class; other selected responses are kept.
fn choose_lung(doc: &mut Value, info: &TableInfo, class: Option<&str>) {
    let mut list: Vec<String> = strings_at(doc, "radiological.responses")
        .into_iter()
        .filter(|id| lung_class(id).is_none())
        .collect();
    if let Some(class) = class {
        if let Some(response) = info
            .responses
            .iter()
            .find(|r| lung_class(&r.id) == Some(class))
        {
            list.push(response.id.clone());
        }
    }
    set(doc, "radiological.responses", json!(list));
}

/// Pin a bundled table: `radiological.table.{path,sha256}` and a fresh response selection.
fn select_bundled(doc: &mut Value, name: &str) {
    let sha256 = radiological::bundled_sha256(name).unwrap_or_default();
    set(
        doc,
        "radiological.table",
        json!({"path": format!("{}{name}", radiological::BUNDLED_PREFIX), "sha256": sha256}),
    );
    set(doc, "radiological.responses", json!([]));
}

/// Leave a bundled table for a file of the user's own.
fn select_custom(doc: &mut Value) {
    if radiological::bundled_name(str_at(doc, "radiological.table.path")).is_some() {
        set(doc, "radiological.table", json!({"path": "", "sha256": ""}));
        set(doc, "radiological.responses", json!([]));
    }
}

fn regulatory_needs(doc: &Value, info: Option<&TableInfo>, custom: bool) -> Option<String> {
    let path = str_at(doc, "radiological.table.path");
    if let Some(name) = radiological::bundled_name(path) {
        return match info {
            Some(info) if has_lung_classes(info) && chosen_lung(doc).is_none() => {
                Some("uranium lung class".into())
            }
            Some(_) => None,
            None => Some(format!("a known table, not '{name}'")),
        };
    }
    if path.is_empty() {
        return Some(if custom { "table file" } else { "a table" }.into());
    }
    if str_at(doc, "radiological.table.sha256").is_empty() {
        return Some("table hash".into());
    }
    None
}

// ---- small widgets ------------------------------------------------------------------------------

fn float_field(
    ui: &mut egui::Ui,
    label: &str,
    doc: &mut Value,
    path: &str,
    default: f64,
    range: RangeInclusive<f64>,
    speed: f64,
) {
    let mut value = f64_at(doc, path, default);
    ui.label(label);
    if ui
        .add(
            egui::DragValue::new(&mut value)
                .custom_parser(crate::model::parse_finite)
                .range(range)
                .max_decimals(10)
                .speed(speed),
        )
        .changed()
    {
        set(doc, path, json!(value));
    }
}

fn int_field(
    ui: &mut egui::Ui,
    label: &str,
    doc: &mut Value,
    path: &str,
    default: u64,
    range: RangeInclusive<u64>,
) {
    let mut value = get(doc, path).as_u64().unwrap_or(default);
    ui.label(label);
    if ui
        .add(
            egui::DragValue::new(&mut value)
                .custom_parser(crate::model::parse_finite)
                .range(range)
                .speed(1.),
        )
        .changed()
    {
        set(doc, path, json!(value));
    }
}

fn check_field(ui: &mut egui::Ui, label: &str, doc: &mut Value, path: &str, default: bool) {
    let mut value = bool_at(doc, path, default);
    if ui.checkbox(&mut value, label).changed() {
        set(doc, path, json!(value));
    }
}

fn choice_field(
    ui: &mut egui::Ui,
    label: &str,
    id: &str,
    doc: &mut Value,
    path: &str,
    default: &str,
    options: &[&str],
) {
    let current = match str_at(doc, path) {
        "" => default.to_owned(),
        text => text.to_owned(),
    };
    let mut chosen = None;
    ui.label(label);
    egui::ComboBox::from_id_salt(id)
        .selected_text(&current)
        .show_ui(ui, |ui| {
            for option in options {
                if ui.selectable_label(current == *option, *option).clicked() {
                    chosen = Some(*option);
                }
            }
        });
    if let Some(option) = chosen {
        if option != current {
            set(doc, path, json!(option));
        }
    }
}

/// A hash-pinned file reference: path, hash and a picker button. Nothing is written until the user
/// edits; a missing reference reads as empty.
#[allow(clippy::too_many_arguments)]
fn reference(
    ui: &mut egui::Ui,
    doc: &mut Value,
    path: &str,
    picker: &mut dyn Picker,
    label: &str,
    title: &str,
    extensions: &[&str],
    clearable: bool,
) {
    let file_path = format!("{path}.path");
    let hash_path = format!("{path}.sha256");
    let mut file = str_at(doc, &file_path).to_owned();
    let mut hash = str_at(doc, &hash_path).to_owned();
    let (old_file, old_hash) = (file.clone(), hash.clone());
    let mut pinned = None;
    let mut clear = false;
    ui.horizontal(|ui| {
        ui.label(label);
        ui.add(
            egui::TextEdit::singleline(&mut file)
                .desired_width((ui.available_width() - 260.).max(150.)),
        );
        if ui.button("Choose and pin").clicked() {
            pinned = picker.pick(title, extensions);
        }
        if clearable && !old_file.is_empty() && ui.small_button("Clear").clicked() {
            clear = true;
        }
    });
    ui.horizontal(|ui| {
        ui.label("SHA-256");
        ui.add(egui::TextEdit::singleline(&mut hash).desired_width(ui.available_width().min(560.)));
    });
    if let Some(pinned) = pinned {
        apply_pin(doc, path, &pinned);
    } else if clear {
        set(doc, path, Value::Null);
    } else if file != old_file || hash != old_hash {
        if !get(doc, path).is_object() {
            set(doc, path, json!({"path": "", "sha256": ""}));
        }
        if file != old_file {
            set(doc, &file_path, json!(file));
        }
        if hash != old_hash {
            set(doc, &hash_path, json!(hash));
        }
    }
}

/// An explicit nuclide name the way the solver wants it (`Cl36`): not an element, already canonical.
fn canonical_nuclide(raw: &str) -> Result<String, String> {
    match composition::material_key(raw) {
        Ok(MaterialKey::Nuclide { canonical, .. }) if canonical == raw => Ok(canonical),
        Ok(MaterialKey::Nuclide { canonical, .. }) => {
            Err(format!("'{raw}' is not canonical; use '{canonical}'"))
        }
        Ok(MaterialKey::Element(_)) => Err(format!("'{raw}' is an element, not a nuclide")),
        Err(error) => Err(error),
    }
}

/// A list of names as removable chips with an add box. `validate` can refuse an entry or rewrite it;
/// Checks one list entry and returns its canonical form.
type Validator = fn(&str) -> Result<String, String>;

/// `empty_removes` drops the key instead of writing `[]` when the last entry goes.
fn string_list(
    ui: &mut egui::Ui,
    doc: &mut Value,
    path: &str,
    draft: &mut ListDraft,
    validate: Option<Validator>,
    hint: &str,
    empty_removes: bool,
) {
    let items = strings_at(doc, path);
    let mut remove_at = None;
    if !items.is_empty() {
        ui.horizontal_wrapped(|ui| {
            for (index, item) in items.iter().enumerate() {
                if ui.small_button(format!("{item} \u{d7}")).clicked() {
                    remove_at = Some(index);
                }
            }
        });
    }
    let mut add = false;
    ui.horizontal(|ui| {
        let response = ui.add(
            egui::TextEdit::singleline(&mut draft.text)
                .desired_width(140.)
                .hint_text(hint),
        );
        if response.lost_focus() && ui.input(|input| input.key_pressed(egui::Key::Enter)) {
            add = true;
        }
        if ui.button("Add").clicked() {
            add = true;
        }
    });
    if let Some(index) = remove_at {
        let mut list = items.clone();
        list.remove(index);
        if list.is_empty() && empty_removes {
            remove(doc, path);
        } else {
            set(doc, path, json!(list));
        }
        draft.note = None;
    } else if add {
        let raw = draft.text.trim().to_owned();
        if !raw.is_empty() {
            let checked = match validate {
                Some(validate) => validate(&raw),
                None => Ok(raw),
            };
            match checked {
                Ok(name) if items.contains(&name) => {
                    draft.note = Some(format!("{name} is already listed"));
                }
                Ok(name) => {
                    let mut list = items.clone();
                    list.push(name);
                    set(doc, path, json!(list));
                    draft.text.clear();
                    draft.note = None;
                }
                Err(error) => draft.note = Some(error),
            }
        }
    }
    if let Some(note) = &draft.note {
        ui.colored_label(ui.visuals().error_fg_color, note);
    }
}

/// Comma-separated response IDs with an explicit Apply, as before: nothing changes while typing.
fn ids_editor(ui: &mut egui::Ui, doc: &mut Value, path: &str, draft: &mut IdsDraft) {
    let current = get(doc, path).clone();
    let source = current.to_string();
    if draft.source != source {
        draft.text = current
            .as_array()
            .map(|list| {
                list.iter()
                    .filter_map(Value::as_str)
                    .collect::<Vec<_>>()
                    .join(", ")
            })
            .unwrap_or_default();
        draft.source = source;
    }
    ui.horizontal(|ui| {
        ui.add(egui::TextEdit::singleline(&mut draft.text).desired_width(320.));
        if ui.button("Apply response IDs").clicked() {
            let ids: Vec<&str> = draft
                .text
                .split(',')
                .map(str::trim)
                .filter(|id| !id.is_empty())
                .collect();
            set(doc, path, json!(ids));
        }
    });
}

/// Editor for ascending boundaries in eV. Returns the new array when the user applies a valid one.
pub fn boundaries_edit(ui: &mut egui::Ui, current: &Value, id: &str) -> Option<Value> {
    ui.label("Ascending boundaries in eV, comma or whitespace separated. Apply explicitly; invalid input keeps the existing boundaries.");
    let key = egui::Id::new((id, current.to_string()));
    let mut text = ui
        .ctx()
        .data_mut(|d| d.get_temp::<String>(key))
        .unwrap_or_else(|| {
            current
                .as_array()
                .map(|a| {
                    a.iter()
                        .map(Value::to_string)
                        .collect::<Vec<_>>()
                        .join(", ")
                })
                .unwrap_or_default()
        });
    ui.add(egui::TextEdit::multiline(&mut text).desired_rows(3));
    let mut applied = None;
    if ui.button("Apply boundaries").clicked() {
        let parsed: Result<Vec<f64>, _> = text
            .split(|c: char| c == ',' || c.is_whitespace())
            .filter(|s| !s.is_empty())
            .map(str::parse)
            .collect();
        match parsed {
            Ok(v)
                if v.len() > 1
                    && v.iter().all(|v| v.is_finite() && *v >= 0.)
                    && v.windows(2).all(|w| w[0] < w[1]) =>
            {
                applied = Some(json!(v));
            }
            _ => {
                ui.colored_label(
                    ui.visuals().error_fg_color,
                    "Use at least two finite, nonnegative, strictly increasing boundaries.",
                );
            }
        }
    }
    ui.ctx().data_mut(|d| d.insert_temp(key, text));
    applied
}

// ---- cards --------------------------------------------------------------------------------------

enum Status {
    Off,
    Ready,
    Needs(String),
    Conflict,
}

fn status(on: bool, needs: Option<String>, conflict: bool) -> Status {
    if conflict {
        Status::Conflict
    } else if !on {
        Status::Off
    } else if let Some(needs) = needs {
        Status::Needs(needs)
    } else {
        Status::Ready
    }
}

/// Whether the solver's message names one of this card's spec sections.
fn mentions(problem: &Option<String>, words: &[&str]) -> bool {
    problem
        .as_deref()
        .is_some_and(|text| words.iter().any(|word| text.contains(word)))
}

fn show_status(ui: &mut egui::Ui, status: &Status) {
    match status {
        Status::Off => {
            ui.weak("off");
        }
        Status::Ready => {
            ui.label("ready");
        }
        Status::Needs(what) => {
            ui.colored_label(ui.visuals().warn_fg_color, format!("needs {what}"));
        }
        Status::Conflict => {
            ui.colored_label(ui.visuals().error_fg_color, "conflict: see below");
        }
    }
}

/// A card: enable box and status on the header row, then the body. Returns the card's rectangle.
fn card(
    ui: &mut egui::Ui,
    title: &str,
    on: &mut bool,
    interactive: bool,
    status: &Status,
    body: impl FnOnce(&mut egui::Ui),
) -> egui::Rect {
    let response = egui::Frame::group(ui.style())
        .inner_margin(16.)
        .show(ui, |ui| {
            ui.set_width(ui.available_width());
            ui.horizontal(|ui| {
                ui.add_enabled(
                    interactive,
                    egui::Checkbox::new(on, RichText::new(title).strong()),
                );
                ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                    show_status(ui, status)
                });
            });
            body(ui);
        });
    ui.add_space(8.);
    response.response.rect
}

fn summary_card(ui: &mut egui::Ui, doc: &mut Value, problem: &Option<String>) -> egui::Rect {
    let enabled = summary_on(doc);
    let mut on = enabled;
    let state = status(enabled, None, mentions(problem, &["summary"]));
    let rect = card(ui, "Step summary", &mut on, true, &state, |ui| {
        ui.label(
            "Feeds the Tables page: step totals, elements and dominant nuclides in every step.",
        );
        if enabled {
            ui.horizontal_wrapped(|ui| {
                int_field(
                    ui,
                    "Dominant list size",
                    doc,
                    "summary.dominant_top",
                    10,
                    1..=256,
                );
                float_field(
                    ui,
                    "Min atoms",
                    doc,
                    "summary.min_atoms",
                    0.,
                    0.0..=f64::MAX,
                    1.,
                );
            });
            ui.horizontal_wrapped(|ui| {
                check_field(
                    ui,
                    "Include stable nuclides",
                    doc,
                    "summary.include_stable",
                    true,
                );
                check_field(ui, "Nuclide table", doc, "summary.nuclides", true);
                check_field(ui, "Element table", doc, "summary.elements", true);
            });
        }
        let mut depletion = bool_at(doc, "summary.depletion", false);
        if ui
            .checkbox(
                &mut depletion,
                "Depletion table (per-step removal of each initial nuclide, by channel)",
            )
            .changed()
        {
            set_depletion(doc, depletion);
        }
    });
    if on != enabled {
        set_summary(doc, on);
    }
    rect
}

fn photon_needs(doc: &Value) -> Option<String> {
    if get(doc, "photon.bremsstrahlung").is_object()
        && strings_at(doc, "photon.bremsstrahlung.nuclides").is_empty()
    {
        return Some("bremsstrahlung nuclides".into());
    }
    if str_at(doc, "photon.response.path").is_empty() {
        return Some("response file".into());
    }
    None
}

fn photons_card(
    ui: &mut egui::Ui,
    doc: &mut Value,
    st: &mut OutputsState,
    picker: &mut dyn Picker,
    problem: &Option<String>,
) {
    let enabled = output_enabled(doc, "photons");
    let mut on = enabled;
    let state = status(
        enabled,
        photon_needs(doc),
        mentions(problem, &["photon", "options.outputs"]),
    );
    card(ui, "Decay photons and dose", &mut on, true, &state, |ui| {
        ui.label("Photon source of every step; with a response file also the contact dose proxy.");
        if !enabled {
            if get(doc, "photon.point_source").is_object()
                || get(doc, "photon.bremsstrahlung").is_object()
            {
                ui.label("Point-source dose and bremsstrahlung in this document need this output.");
            }
            return;
        }
        let structure = match str_at(doc, "photon.group_structure") {
            "" => "fispact-24".to_owned(),
            text => text.to_owned(),
        };
        let mut chosen = None;
        ui.horizontal(|ui| {
            ui.label("Group structure");
            egui::ComboBox::from_id_salt("photon-group-structure")
                .selected_text(&structure)
                .show_ui(ui, |ui| {
                    for option in ["fispact-24", "steiner-22", "custom"] {
                        if ui.selectable_label(structure == option, option).clicked() {
                            chosen = Some(option);
                        }
                    }
                });
        });
        if let Some(option) = chosen.filter(|option| *option != structure) {
            set(doc, "photon.group_structure", json!(option));
            if option != "custom" {
                set(doc, "photon.group_boundaries_eV", Value::Null);
            }
        }
        if structure == "custom" {
            if let Some(applied) = boundaries_edit(
                ui,
                get(doc, "photon.group_boundaries_eV"),
                "photon-boundaries",
            ) {
                set(doc, "photon.group_boundaries_eV", applied);
            }
        }
        ui.horizontal_wrapped(|ui| {
            float_field(
                ui,
                "Build-up factor",
                doc,
                "photon.build_up_factor",
                2.,
                f64::MIN_POSITIVE..=f64::MAX,
                0.1,
            );
            float_field(
                ui,
                "Gamma cutoff (eV)",
                doc,
                "photon.gamma_constant_cutoff_eV",
                20000.,
                0.0..=f64::MAX,
                100.,
            );
        });
        reference(
            ui,
            doc,
            "photon.response",
            picker,
            "Dose response",
            "Photon response data",
            &["json"],
            true,
        );
        ui.add_space(4.);
        let point = get(doc, "photon.point_source").is_object();
        let mut point_on = point;
        if ui.checkbox(&mut point_on, "Dose at distance").changed() {
            set_point_source(doc, point_on);
        }
        if point {
            ui.horizontal_wrapped(|ui| {
                float_field(
                    ui,
                    "Distance (m)",
                    doc,
                    "photon.point_source.distance_m",
                    1.,
                    POINT_SOURCE_MIN_DISTANCE_M..=f64::MAX,
                    0.1,
                );
                check_field(
                    ui,
                    "Air attenuation",
                    doc,
                    "photon.point_source.air_attenuation",
                    true,
                );
                if bool_at(doc, "photon.point_source.air_attenuation", true) {
                    float_field(
                        ui,
                        "Air density (g/cm3)",
                        doc,
                        "photon.point_source.air_density_g_cm3",
                        1.20479e-3,
                        f64::MIN_POSITIVE..=f64::MAX,
                        1e-5,
                    );
                }
            });
            ui.weak("The distance is at least 0.3 m. Air attenuation needs an air mass attenuation curve in the response file; without one the dose is null.");
        }
        let brems = get(doc, "photon.bremsstrahlung").is_object();
        let mut brems_on = brems;
        if ui.checkbox(&mut brems_on, "Bremsstrahlung").changed() {
            set_bremsstrahlung(doc, brems_on);
        }
        if brems {
            ui.label("Nuclides, canonical names such as Cl36, Ar42 or K42:");
            string_list(
                ui,
                doc,
                "photon.bremsstrahlung.nuclides",
                &mut st.nuclides,
                Some(canonical_nuclide),
                "e.g. Cl36",
                false,
            );
        }
    });
    if on != enabled {
        set_output(doc, "photons", on);
    }
}

fn pathways_card(
    ui: &mut egui::Ui,
    doc: &mut Value,
    st: &mut OutputsState,
    problem: &Option<String>,
) {
    let first = output_enabled(doc, "pathways");
    let routes = routes_on(doc);
    let enabled = first || routes;
    let mut on = enabled;
    let state = status(enabled, None, mentions(problem, &["pathways"]));
    card(ui, "Production pathways", &mut on, true, &state, |ui| {
        ui.label("Which reactions and decays made the nuclides in the result.");
        let mut first_on = first;
        if ui
            .checkbox(&mut first_on, "Ranked first products (trace mode)")
            .changed()
        {
            set_output(doc, "pathways", first_on);
        }
        let mut routes_checked = routes;
        if ui
            .checkbox(&mut routes_checked, "Full routes with closure")
            .changed()
        {
            set_routes(doc, routes_checked);
        }
        if !routes {
            return;
        }
        ui.label("Targets, empty for the dominant nuclides of the final step:");
        string_list(
            ui,
            doc,
            "pathways.targets",
            &mut st.targets,
            None,
            "e.g. Co60",
            false,
        );
        ui.horizontal_wrapped(|ui| {
            choice_field(
                ui,
                "Rank by",
                "pathways-rank-by",
                doc,
                "pathways.rank_by",
                "activity",
                &["activity", "heat"],
            );
            int_field(ui, "Top targets", doc, "pathways.top_targets", 10, 1..=1000);
            int_field(ui, "Max depth", doc, "pathways.max_depth", 10, 1..=64);
        });
        ui.horizontal_wrapped(|ui| {
            float_field(
                ui,
                "Route floor",
                doc,
                "pathways.path_floor",
                0.005,
                f64::MIN_POSITIVE..=1.0,
                0.001,
            );
            float_field(
                ui,
                "Loop floor",
                doc,
                "pathways.loop_floor",
                0.01,
                0.0..=1.0,
                0.001,
            );
            int_field(
                ui,
                "Max routes",
                doc,
                "pathways.max_paths",
                100,
                1..=100_000,
            );
        });
        check_field(
            ui,
            "Also report each route by schedule step",
            doc,
            "pathways.by_step",
            false,
        );
        ui.collapsing("More: sources", |ui| {
            ui.label("Source nuclides, empty for every initial nuclide:");
            string_list(
                ui,
                doc,
                "pathways.sources",
                &mut st.sources,
                None,
                "e.g. Fe56",
                false,
            );
        });
    });
    if on != enabled {
        if on {
            set_output(doc, "pathways", true);
        } else {
            set_output(doc, "pathways", false);
            set_routes(doc, false);
        }
    }
}

fn show_table_info(ui: &mut egui::Ui, info: &TableInfo) {
    ui.strong(&info.title);
    for (label, text) in &info.source {
        if matches!(*label, "Edition" | "Retrieved" | "Licence") {
            continue;
        }
        ui.label(format!("{label}: {text}"));
    }
    ui.collapsing("Edition, retrieval and licence", |ui| {
        for (label, text) in &info.source {
            if matches!(*label, "Edition" | "Retrieved" | "Licence") {
                ui.label(format!("{label}: {text}"));
            }
        }
    });
    if info
        .responses
        .iter()
        .any(|r| r.kind == "a2" || r.kind == "activity_limit")
    {
        ui.label("The limits are activities in Bq: the index is the number of limits per gram of material, so multiply by the mass in grams.");
    }
}

/// Response selection for a bundled table: the uranium lung class is an explicit choice without a
/// default; other responses are ticked when the table offers several.
fn bundled_responses(ui: &mut egui::Ui, doc: &mut Value, info: &TableInfo) {
    let others: Vec<&ResponseInfo> = info
        .responses
        .iter()
        .filter(|r| lung_class(&r.id).is_none())
        .collect();
    if others.len() > 1 {
        ui.label("Responses (none ticked evaluates all):");
        let selected = strings_at(doc, "radiological.responses");
        let mut list = selected.clone();
        let mut changed = false;
        for response in &others {
            let mut ticked = selected.contains(&response.id);
            if ui
                .checkbox(&mut ticked, format!("{} ({})", response.id, response.kind))
                .changed()
            {
                changed = true;
                list.retain(|id| id != &response.id);
                if ticked {
                    list.push(response.id.clone());
                }
            }
        }
        if changed {
            set(doc, "radiological.responses", json!(list));
        }
    } else if let Some(response) = others.first() {
        ui.label(format!("Response: {} ({})", response.id, response.kind));
        ui.weak(&response.basis);
    }
    if has_lung_classes(info) {
        let chosen = chosen_lung(doc);
        ui.label("Uranium lung-absorption type. Choose one deliberately; there is no default.");
        let mut pick: Option<Option<&str>> = None;
        ui.horizontal(|ui| {
            for class in LUNG_CLASSES {
                if ui.selectable_label(chosen == Some(class), class).clicked() {
                    pick = Some(if chosen == Some(class) {
                        None
                    } else {
                        Some(class)
                    });
                }
            }
        });
        if let Some(class) = pick {
            choose_lung(doc, info, class);
        }
        if chosen.is_none() {
            ui.colored_label(
                ui.visuals().warn_fg_color,
                "No type chosen: with none selected the solver evaluates all three (fast, medium, slow).",
            );
        } else if let Some(response) = info.responses.iter().find(|r| lung_class(&r.id) == chosen) {
            ui.weak(&response.basis);
        }
    }
}

fn regulatory_card(
    ui: &mut egui::Ui,
    doc: &mut Value,
    st: &mut OutputsState,
    picker: &mut dyn Picker,
    problem: &Option<String>,
) {
    let enabled = get(doc, "radiological").is_object();
    let mut on = enabled;
    let path = str_at(doc, "radiological.table.path").to_owned();
    let bundled = radiological::bundled_name(&path).map(str::to_owned);
    let info = bundled.as_deref().map(|name| st.table(name));
    let info_ref = info.as_deref().and_then(|result| result.as_ref().ok());
    let custom = bundled.is_none() && (!path.is_empty() || st.custom_table);
    let state = status(
        enabled,
        regulatory_needs(doc, info_ref, custom),
        mentions(problem, &["radiological"]),
    );
    let mut selection: Option<Option<String>> = None;
    card(ui, "Regulatory tables", &mut on, true, &state, |ui| {
        ui.label("Compare the inventory with a published table. ACTINV makes no jurisdiction, scenario or margin choice, and a bundled table is a faithful conversion, not an assessment that a regulation applies to your case.");
        if !enabled {
            return;
        }
        let shown = match (&bundled, custom) {
            (Some(name), _) => name.clone(),
            (None, true) => "Custom file\u{2026}".to_owned(),
            (None, false) => "Choose a table".to_owned(),
        };
        ui.horizontal(|ui| {
            ui.label("Table");
            egui::ComboBox::from_id_salt("regulatory-table")
                .selected_text(shown)
                .show_ui(ui, |ui| {
                    for name in radiological::bundled_table_names() {
                        if ui
                            .selectable_label(bundled.as_deref() == Some(name), name)
                            .clicked()
                        {
                            selection = Some(Some(name.to_owned()));
                        }
                    }
                    if ui.selectable_label(custom, "Custom file\u{2026}").clicked() {
                        selection = Some(None);
                    }
                });
        });
        match (&bundled, info.as_deref()) {
            (Some(_), Some(Ok(table))) => {
                show_table_info(ui, table);
                bundled_responses(ui, doc, table);
            }
            (Some(name), Some(Err(error))) => {
                ui.colored_label(ui.visuals().error_fg_color, format!("{name}: {error}"));
            }
            _ => {
                if custom {
                    reference(
                        ui,
                        doc,
                        "radiological.table",
                        picker,
                        "Table file",
                        "Radiological response table",
                        &["json"],
                        false,
                    );
                    ui.label("Response IDs from your table, comma separated (empty = all):");
                    ids_editor(ui, doc, "radiological.responses", &mut st.table_ids);
                }
            }
        }
        check_field(
            ui,
            "Require complete coverage (stop if data are missing)",
            doc,
            "radiological.require_complete",
            false,
        );
    });
    match selection {
        Some(Some(name)) => {
            select_bundled(doc, &name);
            st.custom_table = false;
        }
        Some(None) => {
            select_custom(doc);
            st.custom_table = true;
        }
        None => {}
    }
    if on != enabled {
        set_regulatory(doc, on);
        st.custom_table = false;
    }
}

fn geometry_card(
    ui: &mut egui::Ui,
    doc: &mut Value,
    st: &mut OutputsState,
    picker: &mut dyn Picker,
    problem: &Option<String>,
    action: &mut OutputsAction,
) {
    let enabled = geometry_on(doc);
    let table_mode = bondarenko(doc);
    let mut on = enabled;
    let needs = if get(doc, "self_shielding.geometry.resonance_files")
        .as_array()
        .is_none_or(|files| files.is_empty())
    {
        Some("resonance files".to_owned())
    } else if get(doc, "self_shielding.geometry.density_g_cm3").is_null()
        && get(doc, "material.density_g_cm3").is_null()
    {
        Some("a density".to_owned())
    } else {
        None
    };
    let state = status(
        enabled,
        needs,
        mentions(problem, &["self_shielding", "shielding"]),
    );
    card(
        ui,
        "Geometry self-shielding",
        &mut on,
        enabled || !table_mode,
        &state,
        |ui| {
            ui.label("Resolved-resonance self-shielding of a foil, wire, sphere or cylinder target (FISPACT-II SSFGEOMETRY).");
            if table_mode && !enabled {
                ui.label("A Bondarenko table is set. Geometry is exclusive with it; edit the table in Advanced JSON.");
                return;
            }
            if !enabled {
                return;
            }
            ui.horizontal_wrapped(|ui| {
                let shape = str_at(doc, "self_shielding.geometry.shape").to_owned();
                let mut chosen = None;
                ui.label("Shape");
                egui::ComboBox::from_id_salt("geometry-shape")
                    .selected_text(&shape)
                    .show_ui(ui, |ui| {
                        for option in ["foil", "wire", "sphere", "cylinder"] {
                            if ui.selectable_label(shape == option, option).clicked() {
                                chosen = Some(option);
                            }
                        }
                    });
                if let Some(option) = chosen.filter(|option| *option != shape) {
                    set(doc, "self_shielding.geometry.shape", json!(option));
                    if option == "cylinder" {
                        let length = f64_at(doc, "self_shielding.geometry.length_cm", 0.01);
                        set(doc, "self_shielding.geometry.height_cm", json!(length));
                    } else {
                        remove(doc, "self_shielding.geometry.height_cm");
                    }
                }
                let cylinder = str_at(doc, "self_shielding.geometry.shape") == "cylinder";
                let foil = str_at(doc, "self_shielding.geometry.shape") == "foil";
                float_field(
                    ui,
                    if foil {
                        "Thickness (cm)"
                    } else {
                        "Radius (cm)"
                    },
                    doc,
                    "self_shielding.geometry.length_cm",
                    0.01,
                    f64::MIN_POSITIVE..=f64::MAX,
                    0.001,
                );
                if cylinder {
                    float_field(
                        ui,
                        "Height (cm)",
                        doc,
                        "self_shielding.geometry.height_cm",
                        0.01,
                        f64::MIN_POSITIVE..=f64::MAX,
                        0.001,
                    );
                }
            });
            let own = get(doc, "self_shielding.geometry.density_g_cm3").as_f64();
            let material = get(doc, "material.density_g_cm3").as_f64();
            ui.horizontal_wrapped(|ui| match (own, material) {
                (Some(_), material) => {
                    float_field(
                        ui,
                        "Density (g/cm3)",
                        doc,
                        "self_shielding.geometry.density_g_cm3",
                        1.,
                        f64::MIN_POSITIVE..=f64::MAX,
                        0.01,
                    );
                    if material.is_some() && ui.button("Use material density").clicked() {
                        remove(doc, "self_shielding.geometry.density_g_cm3");
                    }
                }
                (None, Some(material)) => {
                    ui.label(format!("Density from Material: {material} g/cm3"));
                    if ui.button("Set a density here").clicked() {
                        set(
                            doc,
                            "self_shielding.geometry.density_g_cm3",
                            json!(material),
                        );
                    }
                }
                (None, None) => {
                    ui.colored_label(
                        ui.visuals().warn_fg_color,
                        "A density is required, here or on Material.",
                    );
                    if ui.button("Set density on Material").clicked() {
                        *action = OutputsAction::Open(Target::Material);
                    }
                }
            });
            ui.label("Resonance files (ENDF-6 evaluations, one nuclide each):");
            let files = get(doc, "self_shielding.geometry.resonance_files")
                .as_array()
                .cloned()
                .unwrap_or_default();
            let mut drop_at = None;
            for (index, file) in files.iter().enumerate() {
                ui.push_id(index, |ui| {
                    ui.horizontal(|ui| {
                        ui.monospace(file["path"].as_str().unwrap_or(""));
                        if ui.small_button("Remove").clicked() {
                            drop_at = Some(index);
                        }
                    });
                });
            }
            let mut added = None;
            if ui.button("Add resonance file").clicked() {
                added = picker.pick("Resonance evaluation (ENDF-6)", &[]);
            }
            if drop_at.is_some() || added.is_some() {
                let mut list = files.clone();
                if let Some(index) = drop_at {
                    list.remove(index);
                }
                if let Some(pinned) = added {
                    list.push(json!({"path": pinned.path, "sha256": pinned.sha256}));
                }
                set(doc, "self_shielding.geometry.resonance_files", json!(list));
            }
            ui.horizontal(|ui| {
                choice_field(
                    ui,
                    "Profile",
                    "geometry-profile",
                    doc,
                    "self_shielding.geometry.profile",
                    "none",
                    &["none", "endfb8", "tendl"],
                );
                ui.weak("endfb8 for ENDF/B-VIII.1 files, tendl for TENDL");
            });
            ui.label("Restrict to nuclides or elements, empty for every nuclide a file supplies:");
            string_list(
                ui,
                doc,
                "self_shielding.geometry.choose",
                &mut st.choose,
                None,
                "e.g. W",
                true,
            );
            ui.horizontal(|ui| {
                choice_field(
                    ui,
                    "Reactions",
                    "geometry-reactions",
                    doc,
                    "self_shielding.geometry.reactions",
                    "all",
                    &["all", "capture"],
                );
            });
        },
    );
    if on != enabled {
        set_geometry(doc, on);
    }
}

/// `spectrum.relative_error`: each group's tally relative standard uncertainty, in the order of
/// `flux_per_group`. Pasted and applied as a whole, never partly written.
fn relative_errors(ui: &mut egui::Ui, doc: &mut Value, draft: &mut ListDraft) {
    let count = get(doc, "spectrum.flux_per_group")
        .as_array()
        .map_or(0, Vec::len);
    match get(doc, "spectrum.relative_error").as_array() {
        Some(values) => ui.label(format!(
            "Relative errors: {} of {count} groups given.",
            values.len()
        )),
        None => ui.label(format!(
            "Relative errors: none given. Paste one fraction per flux group ({count}), in the order of the flux values."
        )),
    };
    ui.horizontal(|ui| {
        ui.add(
            egui::TextEdit::multiline(&mut draft.text)
                .desired_rows(2)
                .desired_width(320.)
                .hint_text("0.02 0.03 0.05 \u{2026}"),
        );
        ui.vertical(|ui| {
            if ui.button("Apply").clicked() {
                match parse_relative_errors(&draft.text, count) {
                    Ok(values) => {
                        set(doc, "spectrum.relative_error", json!(values));
                        draft.text.clear();
                        draft.note = None;
                    }
                    Err(error) => draft.note = Some(error),
                }
            }
            if get(doc, "spectrum.relative_error").is_array() && ui.button("Clear").clicked() {
                remove(doc, "spectrum.relative_error");
            }
        });
    });
    if let Some(note) = &draft.note {
        ui.colored_label(ui.visuals().error_fg_color, note);
    }
}

fn gas_card(ui: &mut egui::Ui, doc: &mut Value, problem: &Option<String>) {
    let enabled = bool_at(doc, "options.gas", false);
    let mut on = enabled;
    let projectile = match str_at(doc, "projectile") {
        "" => "neutron",
        other => other,
    };
    let needs = (projectile != "neutron").then(|| "a neutron projectile".to_owned());
    let state = status(
        enabled,
        needs,
        enabled && mentions(problem, &["options.gas"]),
    );
    card(
        ui,
        "Gas production (H and He)",
        &mut on,
        true,
        &state,
        |ui| {
            ui.label(
            "Tracks H1, H2, H3, He3 and He4 from neutron reactions and decay; each step gains a gas \
             block. Neutron activation only, and not combined with uncertainty.",
        );
        },
    );
    if on != enabled {
        if on {
            set(doc, "options.gas", json!(true));
        } else {
            remove(doc, "options.gas");
        }
    }
}

/// Solver settings most problems leave alone, collapsed, with how many differ from their defaults
/// in the header so a changed one is seen without opening it.
fn solver_card(ui: &mut egui::Ui, doc: &mut Value) {
    let changed = crate::views::review::changes(doc)
        .map(|found| {
            crate::views::review::count(&found, &["options.mode", "options.temperature_K"])
        })
        .unwrap_or(0);
    let title = match changed {
        0 => "Solver settings".to_owned(),
        n => format!("Solver settings · {n} changed"),
    };
    ui.add_space(4.);
    egui::CollapsingHeader::new(title)
        .id_salt("solver-settings")
        .show(ui, |ui| {
            let mode = str_at(doc, "options.mode");
            let mode = if mode.is_empty() { "auto" } else { mode }.to_owned();
            ui.horizontal(|ui| {
                ui.label("Solver mode");
                egui::ComboBox::from_id_salt("mode")
                    .selected_text(&mode)
                    .show_ui(ui, |ui| {
                        for choice in ["auto", "trace", "coupled"] {
                            if ui.selectable_label(mode == choice, choice).clicked() {
                                if choice == "auto" {
                                    remove(doc, "options.mode");
                                } else {
                                    set(doc, "options.mode", json!(choice));
                                }
                            }
                        }
                    });
            });
            ui.weak(
                "Auto chooses the mode from the problem. Trace also ranks production pathways.",
            );
            let mut temperature = doc["options"]["temperature_K"].as_f64().unwrap_or(293.6);
            ui.horizontal(|ui| {
                ui.label("Temperature (K)");
                if ui
                    .add(
                        egui::DragValue::new(&mut temperature)
                            .custom_parser(crate::model::parse_finite)
                            .speed(1.)
                            .range(0.0..=f64::MAX),
                    )
                    .changed()
                {
                    set(doc, "options.temperature_K", json!(temperature));
                }
            });
            ui.weak(
                "Settings no page edits are in Advanced JSON; Check & run lists any you changed.",
            );
        });
}

/// The ways a spec declares the unmodeled error; they exclude each other.
const UNMODELED: [&str; 3] = [
    "uncertainty.unmodeled_relative",
    "uncertainty.unmodeled_table",
    "uncertainty.unmodeled_evalspread",
];

/// Declare an unmodeled relative error `u` in place of any other source; 0 declares none.
pub fn set_unmodeled(doc: &mut Value, u: f64) {
    for path in UNMODELED {
        remove(doc, path);
    }
    if u > 0. {
        set(doc, UNMODELED[0], json!(u));
    }
}

/// The unmodeled error an `eval-spread` answer suggests, and what it was, in words. Every band
/// gains the term, so it is the largest spread among the banded responses; the solver's own
/// `suggested_unmodeled_relative` (the largest over every nuclide, which a nuclide with next to no
/// activity can set) stands in only when the answer has no banded responses. The browser never
/// runs the estimate.
#[cfg(not(target_arch = "wasm32"))]
pub fn spread_estimate(doc: &Value) -> Option<(f64, String)> {
    let worst = |key: &str, prefix: &str| {
        doc[key]
            .as_object()
            .into_iter()
            .flatten()
            .filter_map(|(name, v)| {
                Some((v["max_abs_ln_ratio"].as_f64()?, format!("{prefix}{name}")))
            })
            .max_by(|a, b| a.0.total_cmp(&b.0))
    };
    let (u, what) = match worst("per_response_nominal", "") {
        Some((u, name)) => (u, format!("the banded responses, largest for {name}")),
        None => {
            let u = doc["suggested_unmodeled_relative"].as_f64()?;
            let name = [
                worst("per_nuclide_activity", "the activity of "),
                worst("per_heat_component", "decay heat, "),
            ]
            .into_iter()
            .flatten()
            .max_by(|a, b| a.0.total_cmp(&b.0))
            .map_or(String::new(), |(_, name)| format!(", largest for {name}"));
            (u, format!("any nuclide or heat{name}"))
        }
    };
    if u <= 0. {
        return Some((
            0.,
            "The two decay libraries give the same answer, so no unmodeled error was added.".into(),
        ));
    }
    Some((
        u,
        format!(
            "Set to {}: the two decay libraries differ by up to that much over {what}. It bounds \
             how far the choice of library moves the answer; it is not a measured uncertainty.",
            percent_words(u)
        ),
    ))
}

#[cfg(not(target_arch = "wasm32"))]
fn percent_words(u: f64) -> String {
    let percent = u * 100.;
    if percent >= 10. {
        format!("{percent:.0} %")
    } else if percent >= 0.1 {
        format!("{percent:.2} %")
    } else {
        format!("{percent:.2e} %")
    }
}

fn unmodeled_row(ui: &mut egui::Ui, doc: &mut Value, spread: &Spread, action: &mut OutputsAction) {
    ui.horizontal_wrapped(|ui| {
        ui.label("Unmodeled error");
        let mut percent = f64_at(doc, UNMODELED[0], 0.) * 100.;
        if ui
            .add(
                egui::DragValue::new(&mut percent)
                    .custom_parser(crate::model::parse_finite)
                    .range(0.0..=1000.0)
                    .max_decimals(3)
                    .speed(0.1)
                    .suffix(" %"),
            )
            .on_hover_text(
                "Error the data do not model, as a share of each value, added to every band. \
                 0 adds nothing.",
            )
            .changed()
        {
            set_unmodeled(doc, percent / 100.);
        }
        if spread.can_run {
            let fallback = !str_at(doc, "decay.fallback").is_empty();
            let button = ui
                .add_enabled(
                    fallback && !spread.running,
                    egui::Button::new("Estimate from library disagreement"),
                )
                .on_hover_text(
                    "Solves with the decay library and again with the fallback library, and \
                     uses the largest difference between the two answers",
                )
                .on_disabled_hover_text(if fallback {
                    "Estimating…"
                } else {
                    "Needs a fallback decay library: add one under Nuclear data"
                });
            if button.clicked() {
                *action = OutputsAction::EstimateSpread;
            }
            if spread.running {
                ui.spinner();
            }
        }
    });
    if let Some(source) = UNMODELED[1..].iter().find(|path| !get(doc, path).is_null()) {
        let what = if source.ends_with("table") {
            "a calibrated-error table"
        } else {
            "an eval-spread file"
        };
        ui.weak(format!(
            "The unmodeled error now comes from {what} (Advanced JSON); a value here replaces it."
        ));
    }
    if let Some(note) = &spread.note {
        ui.weak(note);
    }
}

fn uncertainty_card(
    ui: &mut egui::Ui,
    doc: &mut Value,
    st: &mut OutputsState,
    picker: &mut dyn Picker,
    problem: &Option<String>,
    action: &mut OutputsAction,
) {
    let enabled = get(doc, "uncertainty").is_object();
    let mut on = enabled;
    let chosen = channels(doc);
    let wants_flux = chosen.iter().any(|c| c == "flux");
    let needs = if str_at(doc, "uncertainty.covariance.path").is_empty() && !flux_only(doc) {
        Some("covariance file".to_owned())
    } else if wants_flux && !get(doc, "spectrum.relative_error").is_array() {
        Some("flux relative errors".to_owned())
    } else {
        None
    };
    let state = status(enabled, needs, mentions(problem, &["uncertainty"]));
    card(ui, "Uncertainty", &mut on, true, &state, |ui| {
        ui.label("Uncertainty bands on the selected responses, from the channels chosen below.");
        if !enabled {
            return;
        }
        ui.horizontal_wrapped(|ui| {
            ui.label("Channels");
            for (id, label) in CHANNELS {
                let mut value = chosen.iter().any(|c| c == id);
                let last = value && chosen.len() == 1;
                // The last channel stays ticked; disabling it instead hid its tick in the dark theme.
                let response = ui.checkbox(&mut value, label);
                let response = if last {
                    response.on_hover_text("At least one channel is needed")
                } else {
                    response
                };
                if response.changed() && !last {
                    set_channel(doc, id, value);
                }
            }
        });
        if flux_only(doc) {
            ui.weak("Flux-only mode: no covariance sidecar is needed and MF=33 is not propagated.");
        } else {
            reference(
                ui,
                doc,
                "uncertainty.covariance",
                picker,
                "Covariance",
                "Covariance sidecar",
                &["json"],
                false,
            );
        }
        if wants_flux {
            relative_errors(ui, doc, &mut st.relative_error);
        }
        ui.label("Responses, comma separated: heat.total, heat.gamma, activity:Mn56, activity:* (empty = all)");
        ids_editor(ui, doc, "uncertainty.responses", &mut st.uncertainty_ids);
        ui.horizontal_wrapped(|ui| {
            float_field(
                ui,
                "Confidence level (0\u{2013}1)",
                doc,
                "uncertainty.confidence_level",
                0.95,
                0.0..=1.0,
                0.01,
            );
        });
        check_field(
            ui,
            "Require complete coverage (stop if data are missing)",
            doc,
            "uncertainty.require_complete",
            false,
        );
        unmodeled_row(ui, doc, &st.spread, action);
    });
    if on != enabled {
        set_uncertainty(doc, on);
    }
}

/// The Outputs page body. The shell renders the page title; this renders everything below it and
/// marks the first card as the tour anchor `outputs`.
pub fn show(
    ui: &mut egui::Ui,
    document: &mut Value,
    state: &mut OutputsState,
    picker: &mut dyn Picker,
    marks: &mut Marks,
) -> OutputsAction {
    state.refresh(document);
    let problem = state.problem.clone();
    let mut action = OutputsAction::None;

    ui.horizontal_wrapped(|ui| {
        ui.label("Always included");
        for chip in ["Inventory", "Activity", "Decay heat"] {
            ui.group(|ui| {
                ui.label(chip);
            });
        }
    });
    ui.weak("These are in every result step and cannot be switched off.");
    ui.add_space(8.);

    let rect = summary_card(ui, document, &problem);
    marks.mark("outputs", rect.intersect(ui.clip_rect()));
    photons_card(ui, document, state, picker, &problem);
    pathways_card(ui, document, state, &problem);
    regulatory_card(ui, document, state, picker, &problem);
    geometry_card(ui, document, state, picker, &problem, &mut action);
    uncertainty_card(ui, document, state, picker, &problem, &mut action);
    gas_card(ui, document, &problem);
    solver_card(ui, document);

    match &state.problem {
        Some(message) => {
            ui.colored_label(ui.visuals().error_fg_color, format!("Conflicts: {message}"));
        }
        None => {
            ui.weak("Conflicts: the solver's validation reports none.");
        }
    }
    action
}

#[cfg(test)]
mod tests {
    use super::*;

    struct NoPicker;
    impl Picker for NoPicker {
        fn pick(&mut self, _title: &str, _extensions: &[&str]) -> Option<Pinned> {
            None
        }
    }

    fn decoded() -> Value {
        crate::model::decode_problem(crate::model::EXAMPLE).expect("example decodes")
    }

    #[test]
    fn channels_keep_mf33_alone_absent_and_flux_only_drops_the_empty_covariance() {
        let mut doc = decoded();
        set_uncertainty(&mut doc, true);
        assert_eq!(channels(&doc), ["cross_section_mf33"]);
        set_channel(&mut doc, "flux", true);
        assert_eq!(
            doc["uncertainty"]["channels"],
            json!(["cross_section_mf33", "flux"])
        );
        set_channel(&mut doc, "cross_section_mf33", false);
        assert_eq!(doc["uncertainty"]["channels"], json!(["flux"]));
        assert!(doc["uncertainty"].get("covariance").is_none());
        // The last channel stays.
        set_channel(&mut doc, "flux", false);
        assert_eq!(doc["uncertainty"]["channels"], json!(["flux"]));
        set_channel(&mut doc, "cross_section_mf33", true);
        set_channel(&mut doc, "flux", false);
        assert!(doc["uncertainty"].get("channels").is_none());
        assert_eq!(
            doc["uncertainty"]["covariance"],
            json!({"path": "", "sha256": ""})
        );
    }

    #[test]
    fn an_unmodeled_error_replaces_the_other_sources_and_zero_removes_it() {
        let mut doc = json!({"uncertainty": {"unmodeled_table": {"path": "t.json"}}});
        set_unmodeled(&mut doc, 0.032);
        assert_eq!(doc["uncertainty"], json!({"unmodeled_relative": 0.032}));
        set_unmodeled(&mut doc, 0.);
        assert_eq!(doc["uncertainty"], json!({}));
    }

    #[test]
    fn the_estimate_is_the_largest_spread_among_the_banded_responses() {
        let mut doc = json!({
            "suggested_unmodeled_relative": 1.55,
            "per_nuclide_activity": {"Ti51": {"max_abs_ln_ratio": 1.55}, "Co60": {"max_abs_ln_ratio": 0.01}},
            "per_heat_component": {"total": {"max_abs_ln_ratio": 0.0075}},
            "per_response_nominal": {"heat.total": {"max_abs_ln_ratio": 0.0075}, "activity:Co60": {"max_abs_ln_ratio": 0.01}},
        });
        let (u, note) = spread_estimate(&doc).unwrap();
        assert_eq!(u, 0.01);
        assert!(note.starts_with("Set to 1.00 %"), "{note}");
        assert!(note.contains("largest for activity:Co60"), "{note}");
        doc["per_response_nominal"] = json!({});
        let (u, note) = spread_estimate(&doc).unwrap();
        assert_eq!(u, 1.55);
        assert!(note.contains("largest for the activity of Ti51"), "{note}");
        doc["suggested_unmodeled_relative"] = json!(0.0);
        assert!(spread_estimate(&doc).unwrap().1.contains("same answer"));
    }

    #[test]
    fn flux_only_uncertainty_with_relative_errors_is_a_valid_spec() {
        let mut doc = decoded();
        set_uncertainty(&mut doc, true);
        set_channel(&mut doc, "flux", true);
        set_channel(&mut doc, "cross_section_mf33", false);
        // The missing relative errors are a run-time refusal, so the card's status names them.
        assert!(get(&doc, "spectrum.relative_error").is_null());
        let count = doc["spectrum"]["flux_per_group"].as_array().unwrap().len();
        let text = vec!["0.05"; count].join(" ");
        let values = parse_relative_errors(&format!("relative_error {text}"), count).unwrap();
        set(&mut doc, "spectrum.relative_error", json!(values));
        Spec::from_json(&doc.to_string()).expect("flux-only uncertainty validates");
    }

    #[test]
    fn relative_errors_need_one_finite_nonnegative_value_per_group() {
        assert_eq!(
            parse_relative_errors("0 0.1,0.2", 3).unwrap(),
            [0., 0.1, 0.2]
        );
        assert!(parse_relative_errors("0.1 0.2", 3).is_err());
        assert!(parse_relative_errors("0.1 -0.2", 2).is_err());
        assert!(parse_relative_errors("0.1 x", 2).is_err());
        assert!(parse_relative_errors("0.1 inf", 2).is_err());
    }

    #[test]
    fn gas_is_written_only_when_on_and_its_conflict_is_the_solvers() {
        let mut doc = decoded();
        assert!(doc["options"].get("gas").is_none());
        set(&mut doc, "options.gas", json!(true));
        Spec::from_json(&doc.to_string()).expect("gas validates for neutrons");
        set_uncertainty(&mut doc, true);
        set_channel(&mut doc, "flux", true);
        set_channel(&mut doc, "cross_section_mf33", false);
        let error = Spec::from_json(&doc.to_string()).unwrap_err();
        assert!(mentions(&Some(error.clone()), &["options.gas"]), "{error}");
        remove(&mut doc, "options.gas");
        assert!(doc["options"].get("gas").is_none());
    }

    #[test]
    fn the_default_set_is_what_the_solver_computes_without_a_list() {
        // Absent `options.outputs` builds the photon source and the first-product pathways
        // (run.rs: `is_none_or`); the validator is the oracle that photons are in that set, since it
        // refuses point_source exactly when a list lacks photons or dose.
        let mut doc = decoded();
        assert!(doc["options"]["outputs"].is_null());
        doc["photon"]["point_source"] = json!({"distance_m": 1.0});
        assert!(Spec::from_json(&doc.to_string()).is_ok());
        doc["options"]["outputs"] = json!(DEFAULT_OUTPUTS);
        assert!(Spec::from_json(&doc.to_string()).is_ok());
        doc["options"]["outputs"] = json!(["inventory", "activity", "heat", "pathways"]);
        let error = Spec::from_json(&doc.to_string()).unwrap_err();
        assert!(error.contains("photon.point_source"), "{error}");
        assert_eq!(
            effective_outputs(&decoded()),
            ["inventory", "activity", "heat", "photons", "pathways"]
        );
    }

    #[test]
    fn the_first_edit_keeps_what_the_solver_was_going_to_compute() {
        let mut doc = decoded();
        set_output(&mut doc, "pathways", false);
        assert_eq!(
            doc["options"]["outputs"],
            json!(["inventory", "activity", "heat", "photons"]),
            "turning pathways off must not drop photons"
        );
        let mut doc = decoded();
        set_output(&mut doc, "photons", false);
        assert_eq!(
            doc["options"]["outputs"],
            json!(["inventory", "activity", "heat", "pathways"])
        );
        assert!(output_enabled(&doc, "pathways"));
        assert!(!output_enabled(&doc, "photons"));
    }

    #[test]
    fn dose_counts_as_photons_and_other_entries_survive_an_edit() {
        let mut doc = decoded();
        doc["options"]["outputs"] = json!(["heat", "dose", "audit"]);
        assert!(output_enabled(&doc, "photons"));
        set_output(&mut doc, "pathways", true);
        assert_eq!(
            doc["options"]["outputs"],
            json!(["heat", "dose", "audit", "pathways"])
        );
        set_output(&mut doc, "photons", false);
        assert_eq!(
            doc["options"]["outputs"],
            json!(["heat", "audit", "pathways"])
        );
    }

    #[test]
    fn every_toggle_round_trips_an_unmodified_document() {
        type Toggle = (&'static str, fn(&Value) -> bool, fn(&mut Value, bool));
        let toggles: [Toggle; 9] = [
            ("summary", summary_on, set_summary),
            (
                "photons",
                |d| output_enabled(d, "photons"),
                |d, on| set_output(d, "photons", on),
            ),
            (
                "first products",
                |d| output_enabled(d, "pathways"),
                |d, on| set_output(d, "pathways", on),
            ),
            ("routes", routes_on, set_routes),
            (
                "regulatory",
                |d| get(d, "radiological").is_object(),
                set_regulatory,
            ),
            ("geometry", geometry_on, set_geometry),
            (
                "uncertainty",
                |d| get(d, "uncertainty").is_object(),
                set_uncertainty,
            ),
            (
                "point source",
                |d| get(d, "photon.point_source").is_object(),
                set_point_source,
            ),
            (
                "bremsstrahlung",
                |d| get(d, "photon.bremsstrahlung").is_object(),
                set_bremsstrahlung,
            ),
        ];
        let original = decoded();
        for (name, read, write) in toggles {
            let mut doc = original.clone();
            let initial = read(&doc);
            write(&mut doc, !initial);
            assert_eq!(read(&doc), !initial, "{name}");
            assert_ne!(doc, original, "{name} must change the document");
            write(&mut doc, initial);
            assert_eq!(doc, original, "{name} must restore the document");
        }
    }

    #[test]
    fn depletion_turns_the_summary_on_and_off_again() {
        let mut doc = decoded();
        set_depletion(&mut doc, true);
        assert_eq!(doc["summary"], json!({"depletion": true}));
        set_depletion(&mut doc, false);
        assert_eq!(doc["summary"], json!({}));
        set_summary(&mut doc, false);
        assert_eq!(doc, decoded());
    }

    #[test]
    fn geometry_keeps_a_bondarenko_table_it_did_not_create() {
        let mut doc = decoded();
        doc["self_shielding"] = json!({"table": {"path": "t.json", "sha256": "0".repeat(64)}});
        assert!(bondarenko(&doc));
        let before = doc.clone();
        set_geometry(&mut doc, true);
        set_geometry(&mut doc, false);
        assert_eq!(doc, before);
    }

    #[test]
    fn choosing_a_bundled_table_writes_the_documented_fields() {
        let mut doc = decoded();
        set_regulatory(&mut doc, true);
        select_bundled(&mut doc, "us-49cfr173-a2");
        assert_eq!(
            doc["radiological"]["table"]["path"],
            "bundled:us-49cfr173-a2"
        );
        assert_eq!(
            doc["radiological"]["table"]["sha256"],
            radiological::bundled_sha256("us-49cfr173-a2").unwrap()
        );
        assert_eq!(doc["radiological"]["responses"], json!([]));
        assert_eq!(doc["radiological"]["require_complete"], json!(false));
        select_custom(&mut doc);
        assert_eq!(
            doc["radiological"]["table"],
            json!({"path": "", "sha256": ""})
        );
    }

    #[test]
    fn uranium_lung_class_has_no_default_and_is_an_explicit_choice() {
        let info = table_info("us-49cfr173-a2").expect("bundled table");
        assert!(has_lung_classes(&info));
        let mut doc = decoded();
        set_regulatory(&mut doc, true);
        select_bundled(&mut doc, "us-49cfr173-a2");
        assert_eq!(chosen_lung(&doc), None);
        assert_eq!(
            regulatory_needs(&doc, Some(&info), false).as_deref(),
            Some("uranium lung class")
        );
        choose_lung(&mut doc, &info, Some("medium"));
        assert_eq!(doc["radiological"]["responses"], json!(["a2-u-medium"]));
        assert_eq!(chosen_lung(&doc), Some("medium"));
        assert_eq!(regulatory_needs(&doc, Some(&info), false), None);
        assert!(Spec::from_json(&doc.to_string()).is_ok());
        choose_lung(&mut doc, &info, Some("slow"));
        assert_eq!(doc["radiological"]["responses"], json!(["a2-u-slow"]));
        choose_lung(&mut doc, &info, None);
        assert_eq!(doc["radiological"]["responses"], json!([]));
    }

    #[test]
    fn every_bundled_table_shows_its_scope_and_source() {
        for name in radiological::bundled_table_names() {
            let info = table_info(name).unwrap_or_else(|e| panic!("{name}: {e}"));
            for label in ["Jurisdiction", "Scope", "Citation"] {
                assert!(
                    info.source
                        .iter()
                        .any(|(l, text)| *l == label && !text.is_empty()),
                    "{name} lacks {label}"
                );
            }
            assert!(!info.responses.is_empty(), "{name}");
            let transport = name.starts_with("us-");
            assert_eq!(has_lung_classes(&info), transport, "{name}");
        }
        assert!(table_info("no-such-table").is_err());
    }

    #[test]
    fn nuclide_entries_must_be_canonical_nuclides() {
        assert_eq!(canonical_nuclide("Cl36").unwrap(), "Cl36");
        assert_eq!(canonical_nuclide("K42").unwrap(), "K42");
        assert!(canonical_nuclide("Fe").is_err());
        assert!(canonical_nuclide("cl36").is_err());
        assert!(canonical_nuclide("").is_err());
    }

    #[test]
    fn path_helpers_create_read_and_remove_nested_fields() {
        let mut doc = json!({"a": null});
        assert!(get(&doc, "a.b.c").is_null());
        set(&mut doc, "a.b.c", json!(3));
        assert_eq!(doc, json!({"a": {"b": {"c": 3}}}));
        assert_eq!(f64_at(&doc, "a.b.c", 0.), 3.);
        remove(&mut doc, "a.b.c");
        assert_eq!(doc, json!({"a": {"b": {}}}));
        remove(&mut doc, "missing.key");
        remove(&mut doc, "a");
        assert_eq!(doc, json!({}));
    }

    #[test]
    fn pinning_writes_path_and_hash_together() {
        let mut doc = decoded();
        apply_pin(
            &mut doc,
            "photon.response",
            &Pinned {
                path: "response.json".into(),
                sha256: "ab".repeat(32),
            },
        );
        assert_eq!(doc["photon"]["response"]["path"], "response.json");
        assert_eq!(doc["photon"]["response"]["sha256"], "ab".repeat(32));
        assert!(Spec::from_json(&doc.to_string()).is_ok());
    }

    #[test]
    fn the_conflict_text_is_the_solvers_own_message() {
        let mut doc = decoded();
        doc["photon"]["point_source"] = json!({"distance_m": 1.0});
        set_output(&mut doc, "photons", false);
        let mut state = OutputsState::default();
        state.refresh(&doc);
        let message = state.problem.clone().expect("conflict");
        assert_eq!(message, Spec::from_json(&doc.to_string()).unwrap_err());
        assert!(mentions(&state.problem, &["photon"]));
        set_output(&mut doc, "photons", true);
        state.refresh(&doc);
        assert_eq!(state.problem, None);
    }

    #[test]
    fn rendering_every_card_in_both_themes_leaves_the_document_alone() {
        for theme in [egui::ThemePreference::Light, egui::ThemePreference::Dark] {
            let ctx = egui::Context::default();
            ctx.set_theme(theme);
            ctx.memory_mut(|m| m.set_everything_is_visible(true));
            for all_on in [false, true] {
                let mut doc = decoded();
                if all_on {
                    set_summary(&mut doc, true);
                    set_point_source(&mut doc, true);
                    set_bremsstrahlung(&mut doc, true);
                    set_routes(&mut doc, true);
                    set_regulatory(&mut doc, true);
                    select_bundled(&mut doc, "us-49cfr173-a2");
                    set_geometry(&mut doc, true);
                    set_uncertainty(&mut doc, true);
                }
                let before = doc.clone();
                let mut state = OutputsState::default();
                let mut marks = Marks::default();
                let mut picker = NoPicker;
                let mut action = OutputsAction::None;
                let mut output = ctx.run_ui(Default::default(), |ui| {
                    action = show(ui, &mut doc, &mut state, &mut picker, &mut marks);
                });
                output.textures_delta.clear();
                assert_eq!(doc, before, "rendering must not edit the problem");
                assert_eq!(action, OutputsAction::None);
                assert!(marks.0.iter().any(|(anchor, _)| *anchor == "outputs"));
            }
        }
    }
}
