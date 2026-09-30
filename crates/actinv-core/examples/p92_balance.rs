//! P92 G2: Z/A balance of the gas ejectile table against every product row of a real
//! activation library, plus coverage (count and rate share of uncovered rows).
//!
//!   cargo run --release -p actinv-core --example p92_balance -- LIBRARY.npz
//!
//! The "rate share" of uncovered rows uses each row's flat-spectrum weight (the sum of its
//! group cross sections, unweighted by any physical flux) relative to the same sum over every
//! non-loss row in the library. This is a coverage diagnostic, not a physical activation rate:
//! it needs no spectrum file and is stable across libraries built for different problems.
//! Prints one JSON object to stdout and exits 1 if any residual fails to Z/A-balance.

use actinv_core::gas;
use actinv_data::library;
use std::collections::BTreeMap;

fn index_path(library_path: &str) -> String {
    match library_path.strip_suffix(".npz") {
        Some(stem) => format!("{stem}_index.json"),
        None => format!("{library_path}_index.json"),
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    assert_eq!(args.len(), 2, "usage: p92_balance LIBRARY.npz");
    let path = &args[1];

    let lib = library::read_npz(path).expect("read activation library");
    let index_text = std::fs::read_to_string(index_path(path)).expect("read library index sidecar");
    let index: serde_json::Value =
        serde_json::from_str(&index_text).expect("parse library index sidecar");
    let targets: Vec<(i32, i32)> = index["targets"]
        .as_array()
        .expect("library index has no targets array")
        .iter()
        .map(|t| {
            (
                t["za"].as_i64().unwrap_or(0) as i32,
                t["liso"].as_i64().unwrap_or(0) as i32,
            )
        })
        .collect();

    let ngroups = lib.ngroups;
    let mut checked = 0usize;
    let mut failures: Vec<String> = Vec::new();
    let mut uncovered_rows = 0usize;
    let mut uncovered_weight = 0.0f64;
    let mut uncovered_mts: BTreeMap<i32, usize> = BTreeMap::new();
    let mut total_weight = 0.0f64;

    for (i, row) in lib.rows.iter().enumerate() {
        if row.zap == -1 {
            continue; // the per-(target, MT) loss row: not a product, no MT coverage to check.
        }
        let weight: f64 = lib.sig[i * ngroups..(i + 1) * ngroups]
            .iter()
            .copied()
            .filter(|v| v.is_finite())
            .sum();
        total_weight += weight;
        match gas::table(row.mt) {
            Some(ejectiles) => {
                if row.zap <= 0 {
                    failures.push(format!(
                        "row {i}: covered MT {} has non-positive product zap {}",
                        row.mt, row.zap
                    ));
                    continue;
                }
                checked += 1;
                let Some((target_za, _target_liso)) = targets.get(row.target).copied() else {
                    failures.push(format!("row {i}: target index {} out of range", row.target));
                    continue;
                };
                let (dz, da) = ejectiles.za_delta();
                let expected_za = (target_za / 1000 + dz) * 1000 + (target_za % 1000 + da);
                if expected_za != row.zap {
                    failures.push(format!(
                        "row {i}: target {target_za} MT {} expected residual ZA {expected_za}, library zap {}",
                        row.mt, row.zap
                    ));
                }
            }
            None => {
                uncovered_rows += 1;
                uncovered_weight += weight;
                *uncovered_mts.entry(row.mt).or_insert(0) += 1;
            }
        }
    }

    let result = serde_json::json!({
        "library": path,
        "table_version": gas::TABLE_VERSION,
        "rows_total": lib.rows.len(),
        "rows_checked": checked,
        "failures": failures,
        "failure_count": failures.len(),
        "uncovered_rows": uncovered_rows,
        "uncovered_mts": uncovered_mts,
        "total_weight_flat_spectrum": total_weight,
        "uncovered_weight_flat_spectrum": uncovered_weight,
        "uncovered_rate_share_flat_spectrum":
            if total_weight > 0.0 { uncovered_weight / total_weight } else { 0.0 },
    });
    println!("{}", serde_json::to_string_pretty(&result).unwrap());
    std::process::exit(if failures.is_empty() { 0 } else { 1 });
}
