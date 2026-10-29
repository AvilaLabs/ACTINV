//! Source: a starting neutron spectrum from the four shipped analytic shapes (P162), integrated over the
//! 709 groups and scaled to the total flux the user gives.
use actinv_core::presets::{self, SpectrumShape};
use eframe::egui;
use serde_json::{json, Value};

/// The shape picked and the total flux typed in the starting-spectrum card.
pub struct SourceState {
    shape: SpectrumShape,
    flux: String,
    notice: Option<String>,
}

impl Default for SourceState {
    fn default() -> Self {
        Self {
            shape: SpectrumShape::Fusion14MeV,
            flux: "1e12".into(),
            notice: None,
        }
    }
}

/// True when the spectrum has at least one positive group value.
pub fn has_flux(document: &Value) -> bool {
    document["spectrum"]["flux_per_group"]
        .as_array()
        .is_some_and(|values| values.iter().any(|v| v.as_f64().is_some_and(|x| x > 0.)))
}

/// The card is for neutron problems: the shapes are neutron spectra.
pub fn applies(document: &Value) -> bool {
    document["projectile"].as_str().unwrap_or("neutron") == "neutron"
}

/// Put a shape in the problem: 709 group values, highest energy first, summing to `flux`, which is also the
/// normalization total so the user can change it in one place.
fn apply_shape(document: &mut Value, shape: SpectrumShape, flux: f64) -> Result<(), String> {
    let values = presets::spectrum(shape, flux)?;
    let spectrum = &mut document["spectrum"];
    if !spectrum.is_object() {
        *spectrum = json!({});
    }
    let map = spectrum.as_object_mut().expect("spectrum is an object");
    map.insert("structure".into(), "fispact-709".into());
    map.insert("flux_per_group".into(), json!(values));
    map.insert("descending".into(), true.into());
    map.insert("total".into(), json!(flux));
    map.remove("boundaries_eV");
    Ok(())
}

/// The flux the user typed, if it is a positive finite number.
fn parse_flux(text: &str) -> Option<f64> {
    text.trim()
        .parse::<f64>()
        .ok()
        .filter(|f| f.is_finite() && *f > 0.)
}

/// A starting spectrum: open above an empty spectrum, folded below a filled one.
pub fn starting_card(ui: &mut egui::Ui, document: &mut Value, state: &mut SourceState) {
    let empty = !has_flux(document);
    let body = |ui: &mut egui::Ui, state: &mut SourceState, document: &mut Value| {
        ui.horizontal_wrapped(|ui| {
            for shape in SpectrumShape::ALL {
                ui.selectable_value(&mut state.shape, shape, shape_name(shape));
            }
        });
        ui.weak(state.shape.description());
        ui.horizontal_wrapped(|ui| {
            ui.label("Total flux (n/cm²/s)");
            ui.add(egui::TextEdit::singleline(&mut state.flux).desired_width(110.));
            let flux = parse_flux(&state.flux);
            let label = if empty {
                "Use this spectrum"
            } else {
                "Replace the group values with this spectrum"
            };
            if ui
                .add_enabled(flux.is_some(), egui::Button::new(label))
                .clicked()
            {
                if let Some(flux) = flux {
                    state.notice = apply_shape(document, state.shape, flux).err();
                }
            }
            if flux.is_none() {
                ui.colored_label(
                    ui.visuals().error_fg_color,
                    "Enter a positive flux such as 1e12.",
                );
            }
        });
        if let Some(notice) = &state.notice {
            ui.colored_label(ui.visuals().error_fg_color, notice);
        }
    };
    if empty {
        egui::Frame::group(ui.style())
            .inner_margin(12.)
            .show(ui, |ui| {
                ui.set_width(ui.available_width());
                ui.strong("Start from a standard spectrum");
                ui.label("A textbook shape on the 709-group structure. For a real facility, import its group values from transport below.");
                body(ui, state, document);
            });
        ui.add_space(12.);
    } else {
        egui::CollapsingHeader::new("Standard spectra")
            .id_salt("standard-spectra")
            .show(ui, |ui| body(ui, state, document));
    }
}

/// The short name on a shape's button.
fn shape_name(shape: SpectrumShape) -> &'static str {
    match shape {
        SpectrumShape::Fusion14MeV => "D-T fusion (14.1 MeV)",
        SpectrumShape::Fission => "Fission (Watt)",
        SpectrumShape::Thermal => "Thermal (Maxwellian)",
        SpectrumShape::OneOverE => "Slowing down (1/E)",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model;
    use actinv_core::spec::Spec;

    fn problem() -> Value {
        let mut doc = model::decode_problem(model::EXAMPLE).unwrap();
        doc["schedule"] = json!([{"dt": "1 d", "flux": 1.0}]);
        doc
    }

    #[test]
    fn every_shape_makes_a_valid_spectrum_summing_to_the_flux() {
        for shape in SpectrumShape::ALL {
            let mut doc = model::blank();
            assert!(!has_flux(&doc));
            apply_shape(&mut doc, shape, 3e13).unwrap();
            assert!(has_flux(&doc));
            assert_eq!(doc["spectrum"]["total"], json!(3e13));
            assert_eq!(doc["spectrum"]["descending"], json!(true));
            let mut full = problem();
            full["spectrum"] = doc["spectrum"].clone();
            let spec = Spec::from_json(&full.to_string()).unwrap();
            assert_eq!(spec.spectrum.flux_per_group.len(), 709);
            let sum: f64 = spec.spectrum.flux_per_group.iter().sum();
            assert!((sum / 3e13 - 1.).abs() < 1e-12, "{}", shape.id());
        }
        assert!(apply_shape(&mut model::blank(), SpectrumShape::Thermal, 0.).is_err());
    }

    #[test]
    fn the_flux_must_be_a_positive_number() {
        assert_eq!(parse_flux(" 1e12 "), Some(1e12));
        for bad in ["", "0", "-1", "lots", "inf", "NaN"] {
            assert_eq!(parse_flux(bad), None, "{bad}");
        }
    }

    #[test]
    fn the_card_is_for_neutron_problems() {
        let mut doc = model::blank();
        assert!(applies(&doc));
        doc["projectile"] = "proton".into();
        assert!(!applies(&doc));
    }
}
