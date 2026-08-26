#![allow(non_snake_case)] // JSON control input uses the public eV wire spelling.
//! Small control surface for P8's public canonical reader and conservative rebin function.

use actinv_core::flux::{rebin_equal_lethargy, FluxStream};
use serde::Deserialize;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RebinInput {
    source_boundaries_eV: Vec<f64>,
    source_flux: Vec<f64>,
    destination_boundaries_eV: Vec<f64>,
}

fn main() -> Result<(), String> {
    let args: Vec<String> = std::env::args().collect();
    match args.get(1).map(String::as_str) {
        Some("rebin") if args.len() == 3 => {
            let input: RebinInput = serde_json::from_str(
                &std::fs::read_to_string(&args[2])
                    .map_err(|error| format!("cannot read {}: {error}", args[2]))?,
            )
            .map_err(|error| format!("rebin input: {error}"))?;
            let output = rebin_equal_lethargy(
                &input.source_boundaries_eV,
                &input.source_flux,
                &input.destination_boundaries_eV,
            )?;
            println!(
                "{}",
                serde_json::to_string_pretty(&output).map_err(|error| error.to_string())?
            );
        }
        Some("validate") if args.len() == 3 => {
            let mut stream = FluxStream::open(&args[2])?;
            let groups = stream.header.energy_boundaries_eV.len() - 1;
            let mut cells = 0usize;
            loop {
                let chunk = stream.read_chunk(17)?;
                if chunk.is_empty() {
                    break;
                }
                cells += chunk.len();
            }
            let footer = stream.finish()?;
            println!(
                "{}",
                serde_json::json!({
                    "schema": "actinv-flux-1",
                    "cells": cells,
                    "groups": groups,
                    "flux_sum_over_cells": footer.flux_sum_over_cells,
                })
            );
        }
        _ => return Err("usage: flux_probe {rebin INPUT.json|validate FLUX.ndjson}".into()),
    }
    Ok(())
}
