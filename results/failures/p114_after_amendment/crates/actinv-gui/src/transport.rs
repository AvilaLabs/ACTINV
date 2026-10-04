use actinv_core::flux::{self, FluxStream};
use eframe::egui;
use serde_json::{json, Value};
use std::path::PathBuf;

#[derive(Clone)]
pub struct Import {
    format: String,
    source: String,
    groups: String,
    tally: i64,
    rate: f64,
    ordinal: u64,
    floor: String,
    rebin: bool,
    error: Option<String>,
}
impl Default for Import {
    fn default() -> Self {
        Self {
            format: "openmc".into(),
            source: String::new(),
            groups: String::new(),
            tally: 1,
            rate: 0.,
            ordinal: 0,
            floor: String::new(),
            rebin: false,
            error: None,
        }
    }
}
pub struct Request {
    options: Import,
    output: PathBuf,
    destination: Vec<f64>,
}
pub struct Preview {
    pub spectrum: Value,
    pub description: String,
}
impl Import {
    pub fn show(&mut self, ui: &mut egui::Ui, doc: &Value) -> Option<Request> {
        let mut request = None;
        ui.collapsing("Import OpenMC / MCNP / FISPACT spectrum",|ui| {
            ui.label("Select a tally and one cell. The retained NDJSON file records source hashes and normalization; import validates the complete stream.");
            egui::ComboBox::from_id_salt("transport-format").selected_text(&self.format).show_ui(ui,|ui| {for f in ["openmc","meshtal","mctal","fispact","ndjson"] {ui.selectable_value(&mut self.format,f.into(),f);}});
            file(ui,"Source file",&mut self.source);
            if self.format=="fispact" {file(ui,"Group boundaries JSON",&mut self.groups);}
            if matches!(self.format.as_str(),"openmc"|"meshtal"|"mctal") {
                ui.horizontal(|ui| {ui.label("Tally ID");ui.add(egui::DragValue::new(&mut self.tally).custom_parser(crate::model::parse_finite).range(1..=i64::MAX));});
                ui.horizontal(|ui| {ui.label("Source rate (particles/s)");ui.add(egui::DragValue::new(&mut self.rate).custom_parser(crate::model::parse_finite).speed(1e10));});
                ui.horizontal(|ui| {ui.label("Energy floor (eV, only for a zero lower boundary)");ui.text_edit_singleline(&mut self.floor);});
            }
            ui.horizontal(|ui| {ui.label("Cell ordinal (0 = first cell)");ui.add(egui::DragValue::new(&mut self.ordinal).custom_parser(crate::model::parse_finite));});
            ui.checkbox(&mut self.rebin,"Allow conversion to the problem's groups (equal flux per unit lethargy)");
            ui.label("Applying replaces the spectrum with absolute transport flux and removes any prior total-flux override.");
            if ui.button("Import and preview selected cell").clicked() {
                self.error = None;
                match boundaries(doc) {
                    Ok(destination)=> {
                        let output=if self.format=="ndjson" {Some(PathBuf::from(&self.source))} else {rfd::FileDialog::new().set_title("Save transport spectrum and provenance").set_file_name("transport-flux.ndjson").save_file()};
                        if let Some(output)=output {request=Some(Request{options:self.clone(),output,destination});}
                    },
                    Err(e)=>{self.error = Some(e);},
                }
            }
            if let Some(error) = &self.error { ui.colored_label(ui.visuals().error_fg_color, error); }
        });
        request
    }
}
fn file(ui: &mut egui::Ui, label: &str, text: &mut String) {
    ui.push_id(label, |ui| {
        ui.horizontal(|ui| {
            ui.label(label);
            ui.text_edit_singleline(text);
            if ui.button("Choose").clicked() {
                if let Some(p) = rfd::FileDialog::new().pick_file() {
                    *text = p.to_string_lossy().into_owned();
                }
            }
        });
    });
}
fn boundaries(doc: &Value) -> Result<Vec<f64>, String> {
    use actinv_data::groups::GroupStructure;
    match doc["spectrum"]["structure"].as_str() {
        Some("fispact-709") => GroupStructure::fispact_709().map(|g| g.boundaries_ev),
        Some("fispact-162") => GroupStructure::fispact_162().map(|g| g.boundaries_ev),
        Some("custom") => serde_json::from_value(doc["spectrum"]["boundaries_eV"].clone())
            .map_err(|e| format!("Set custom energy boundaries first: {e}")),
        _ => Err("Choose a recognized group structure first.".into()),
    }
}
pub fn run(request: Request) -> Result<Preview, String> {
    let o = request.options;
    let floor = if o.floor.trim().is_empty() || matches!(o.format.as_str(), "ndjson" | "fispact") {
        None
    } else {
        Some(
            o.floor
                .parse::<f64>()
                .map_err(|_| "Energy floor must be numeric")?,
        )
    };
    if o.format != "ndjson" && request.output.exists() {
        return Err("Import output already exists. Choose a new filename.".into());
    }
    match o.format.as_str() {
        "openmc" => {
            flux::import_openmc(&o.source, &request.output, o.tally, o.rate, floor, 16384)?;
        }
        "meshtal" => {
            let tally = u64::try_from(o.tally).map_err(|_| "Tally must be nonnegative")?;
            flux::import_meshtal(&o.source, &request.output, tally, o.rate, floor)?;
        }
        "mctal" => {
            let tally = u64::try_from(o.tally).map_err(|_| "Tally must be nonnegative")?;
            flux::import_mctal(&o.source, &request.output, tally, o.rate, floor)?;
        }
        "fispact" => {
            flux::import_fispact(&o.source, &o.groups, &request.output)?;
        }
        "ndjson" => {}
        _ => return Err("Unsupported transport format".into()),
    }
    let mut stream = FluxStream::open(&request.output)?;
    let header = stream.header.clone();
    let mut selected = None;
    loop {
        let cells = stream.read_chunk(1024)?;
        if cells.is_empty() {
            break;
        }
        for cell in cells {
            if cell.ordinal == o.ordinal {
                selected = Some(cell);
            }
        }
    }
    stream.finish()?;
    let selected = selected.ok_or_else(|| {
        format!(
            "Cell {} does not exist; stream has {} cells.",
            o.ordinal, header.cell_count
        )
    })?;
    if header.energy_boundaries_eV != request.destination && !o.rebin {
        return Err("Energy groups differ. Enable equal-lethargy conversion and import the saved NDJSON file, or use matching groups.".into());
    }
    let rebinned = flux::rebin_equal_lethargy(
        &header.energy_boundaries_eV,
        &selected.flux_per_group,
        &request.destination,
    )?;
    if rebinned.underflow > 0. || rebinned.overflow > 0. {
        return Err(format!("Destination groups exclude flux: below {}, above {}. Widen the energy range; no spectrum was applied.",rebinned.underflow,rebinned.overflow));
    }
    Ok(Preview{spectrum:json!({"structure":"custom","boundaries_eV":request.destination,"flux_per_group":rebinned.flux_per_group,"descending":false}),description:format!("Cell {} ({}) · total {:.6e} particles/cm²/s · {}. Retain {} with the problem: it records the source provenance; the single-material result certificate does not embed transport-file provenance.",selected.ordinal,selected.id,rebinned.destination_total,if rebinned.exact_grid{"exact energy grid"}else{"equal-lethargy conversion"},request.output.display())})
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn import_preserves_absolute_flux_and_requires_explicit_safe_rebin() {
        let dir = std::env::temp_dir().join(format!("actinv-gui-transport-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let source = dir.join("fluxes");
        let groups = dir.join("groups.json");
        let output = dir.join("flux.ndjson");
        std::fs::write(&source, "20 10\n0\nSynthetic test\n").unwrap();
        std::fs::write(&groups, "[100,10,1]").unwrap();
        let options = Import {
            format: "fispact".into(),
            source: source.display().to_string(),
            groups: groups.display().to_string(),
            ..Default::default()
        };
        let preview = run(Request {
            options: options.clone(),
            output: output.clone(),
            destination: vec![1., 10., 100.],
        })
        .ok()
        .unwrap();
        assert_eq!(preview.spectrum["flux_per_group"], json!([10., 20.]));
        assert!(preview.spectrum.get("total").is_none());
        assert_eq!(preview.spectrum["descending"], false);
        assert!(run(Request {
            options: options.clone(),
            output: output.clone(),
            destination: vec![1., 100.]
        })
        .err()
        .unwrap()
        .contains("already exists"));
        let mut options = Import {
            format: "ndjson".into(),
            ..options
        };
        assert!(run(Request {
            options: options.clone(),
            output: output.clone(),
            destination: vec![1., 100.]
        })
        .err()
        .unwrap()
        .contains("Energy groups differ"));
        options.rebin = true;
        let preview = run(Request {
            options: options.clone(),
            output: output.clone(),
            destination: vec![1., 100.],
        })
        .ok()
        .unwrap();
        assert_eq!(preview.spectrum["flux_per_group"], json!([30.]));
        assert!(run(Request {
            options: options.clone(),
            output: output.clone(),
            destination: vec![10., 100.]
        })
        .err()
        .unwrap()
        .contains("exclude flux"));
        // Even after selecting the first cell, a corrupt footer must reject the import.
        let text = std::fs::read_to_string(&output).unwrap();
        let lines: Vec<_> = text.lines().collect();
        std::fs::write(&output, format!("{}\n{}\n", lines[0], lines[1])).unwrap();
        assert!(run(Request {
            options,
            output: output.clone(),
            destination: vec![1., 100.]
        })
        .is_err());
        for path in [source, groups, output] {
            std::fs::remove_file(path).unwrap();
        }
        std::fs::remove_dir(dir).unwrap();
    }
}
