//! P9 control probe: expose the raw reaction triplets assembled from a collapsed library and one
//! effective independent-yield table. This deliberately stops before pruning or solving so an
//! independent control can compare the exact matrix entries.
use actinv_core::chain::{self, RateLedger};
use actinv_data::{decay, fission, library};
use std::collections::HashMap;

fn key_name(chain: &chain::Chain, index: usize) -> String {
    if index == chain.leak {
        "LEAK".into()
    } else if index == chain.unit {
        "UNIT".into()
    } else {
        let (za, liso) = chain.keys[index];
        format!("{za}_{liso}")
    }
}

fn main() {
    let arguments: Vec<String> = std::env::args().collect();
    if arguments.len() != 7 {
        eprintln!("usage: fission_probe DECAY LIBRARY INDEX YIELDS|- ENERGY_EV FLUX[,FLUX...]");
        std::process::exit(2);
    }
    let nuclides = decay::parse_file(&arguments[1]).expect("read decay file");
    let library = library::read_npz(&arguments[2]).expect("read activation library");
    let index: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string(&arguments[3]).expect("read activation index"),
    )
    .expect("parse activation index");
    let targets: Vec<(i32, i32)> = index["targets"]
        .as_array()
        .expect("index targets")
        .iter()
        .map(|target| {
            (
                target["za"].as_i64().expect("target ZA") as i32,
                target["liso"].as_i64().expect("target LISO") as i32,
            )
        })
        .collect();
    let energy: f64 = arguments[5].parse().expect("incident energy");
    let flux: Vec<f64> = arguments[6]
        .split(',')
        .map(|value| value.parse().expect("group flux"))
        .collect();
    assert_eq!(flux.len(), library.ngroups, "flux group count");

    let mut selected = HashMap::new();
    let mut effective_output = serde_json::Value::Null;
    if arguments[4] != "-" {
        let data = fission::parse_file(&arguments[4]).expect("read fission yields");
        let effective = data.effective(energy).expect("select fission yields");
        effective_output = serde_json::json!({
            "parent": format!("{}_{}", data.parent.0, data.parent.1),
            "requested_energy_eV": effective.requested_energy_ev,
            "lower_energy_eV": effective.lower_energy_ev,
            "upper_energy_eV": effective.upper_energy_ev,
            "upper_weight": effective.upper_weight,
            "clamped": effective.clamped,
            "sum": effective.sum,
            "products": effective.products.iter().map(|(key, value)| {
                (format!("{}_{}", key.0, key.1), *value)
            }).collect::<std::collections::BTreeMap<_, _>>(),
        });
        selected.insert(data.parent, effective);
    }

    let chain = chain::build(&nuclides);
    let mut ledger = RateLedger::default();
    let triplets = chain::reaction_rates(&library, &targets, &flux, &chain, &selected, &mut ledger);
    let triplets: Vec<_> = triplets
        .iter()
        .map(|(row, column, value)| {
            serde_json::json!({
                "row": key_name(&chain, *row),
                "column": key_name(&chain, *column),
                "value_per_s": value,
            })
        })
        .collect();
    println!(
        "{}",
        serde_json::to_string_pretty(&serde_json::json!({
            "effective": effective_output,
            "triplets": triplets,
            "ledger": {
                "fission_no_yields_to_leakage": ledger.fission_no_yields,
                "fission_yield_products_to_leakage": ledger.fission_product_leakage,
                "fission_yield_balance": ledger.fission_balance,
            },
        }))
        .expect("serialise probe output")
    );
}
