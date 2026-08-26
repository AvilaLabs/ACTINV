//! Small, deterministic P7 control surface for evaluated decay-photon spectra.
use std::collections::BTreeMap;

use actinv_core::photon::{source_for_step, PhotonResponse, FISPACT_24_BOUNDARIES_EV};
use actinv_data::decay;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 3 {
        eprintln!("usage: photon_probe DECAY.endf PHOTON_RESPONSE.json");
        std::process::exit(2);
    }
    let records = decay::parse_file(&args[1]).unwrap_or_else(|e| {
        eprintln!("cannot parse {}: {e}", args[1]);
        std::process::exit(1);
    });
    let response_text = std::fs::read_to_string(&args[2]).unwrap_or_else(|e| {
        eprintln!("cannot read {}: {e}", args[2]);
        std::process::exit(1);
    });
    let response = PhotonResponse::from_json(&response_text).unwrap_or_else(|e| {
        eprintln!("{e}");
        std::process::exit(1);
    });
    let material = BTreeMap::from([("Fe".to_string(), 1.0)]);
    let cases = [
        ("Co60", (27060, 0)),
        ("Cs137", (55137, 0)),
        ("Ba137m1", (56137, 1)),
        ("Mn68", (25068, 0)),
    ];
    let mut nuclides = serde_json::Map::new();
    for (name, key) in cases {
        let nuclide = records.get(&key).unwrap_or_else(|| {
            eprintln!("missing {name} ({}, {})", key.0, key.1);
            std::process::exit(1);
        });
        let active = [(name, nuclide, 1.0)];
        let (source, diagnostics) = source_for_step(
            &active,
            &FISPACT_24_BOUNDARIES_EV,
            "fispact-24",
            1.0,
            Some(&response),
            &material,
            true,
            2.0,
            2.0e4,
        )
        .unwrap_or_else(|e| {
            eprintln!("{name}: {e}");
            std::process::exit(1);
        });
        nuclides.insert(
            name.into(),
            serde_json::json!({"source": source, "diagnostics": diagnostics}),
        );
    }

    let mut planted = records[&(27060, 0)].clone();
    planted.spectra.clear();
    let active = [("Co60_no_spectrum", &planted, 2.5)];
    let (source, diagnostics) = source_for_step(
        &active,
        &FISPACT_24_BOUNDARIES_EV,
        "fispact-24",
        1.0,
        Some(&response),
        &material,
        true,
        2.0,
        2.0e4,
    )
    .unwrap_or_else(|e| {
        eprintln!("planted missing spectrum: {e}");
        std::process::exit(1);
    });

    let narrow_boundaries = [1.2e6, 1.4e6];
    let co60 = &records[&(27060, 0)];
    let active = [("Co60", co60, 1.0)];
    let (narrow_source, narrow_diagnostics) = source_for_step(
        &active,
        &narrow_boundaries,
        "custom",
        1.0,
        Some(&response),
        &material,
        true,
        2.0,
        2.0e4,
    )
    .unwrap_or_else(|e| {
        eprintln!("planted narrow groups: {e}");
        std::process::exit(1);
    });

    let combined_active = [
        ("Co60", &records[&(27060, 0)], 1.0),
        ("Cs137", &records[&(55137, 0)], 2.0),
        ("Ba137m1", &records[&(56137, 1)], 3.0),
        ("Mn68", &records[&(25068, 0)], 4.0),
    ];
    let (combined_source, combined_diagnostics) = source_for_step(
        &combined_active,
        &FISPACT_24_BOUNDARIES_EV,
        "fispact-24",
        1.0,
        Some(&response),
        &material,
        true,
        2.0,
        2.0e4,
    )
    .unwrap_or_else(|e| {
        eprintln!("combined source: {e}");
        std::process::exit(1);
    });

    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "nuclides": nuclides,
            "planted_missing_spectrum": {"source": source, "diagnostics": diagnostics},
            "planted_narrow_groups": {"source": narrow_source, "diagnostics": narrow_diagnostics},
            "combined": {"source": combined_source, "diagnostics": combined_diagnostics},
        }))
        .expect("serialize probe")
    );
}
