//! P15 control probe for bounded indexed reads from an existing prepared-data artifact.

use actinv_data::prepared::read_prepared_targets;
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::path::Path;

fn main() {
    let arguments: Vec<String> = std::env::args().collect();
    if arguments.len() != 5 {
        eprintln!("usage: prepared_probe ARTIFACT LIBRARY_SHA256 INDEX_SHA256 TARGETS_CSV_OR_DASH");
        std::process::exit(2);
    }
    let targets: BTreeSet<usize> = if arguments[4] == "-" {
        BTreeSet::new()
    } else {
        arguments[4]
            .split(',')
            .map(|value| value.parse().expect("target index"))
            .collect()
    };
    let library = read_prepared_targets(
        Path::new(&arguments[1]),
        &arguments[2],
        &arguments[3],
        &targets,
    )
    .unwrap_or_else(|error| {
        eprintln!("{error}");
        std::process::exit(1);
    });
    let mut hash = Sha256::new();
    for (selected_row, (row, span)) in library.rows().iter().zip(library.spans()).enumerate() {
        hash.update((span.source_row as u64).to_le_bytes());
        hash.update((row.target as u64).to_le_bytes());
        for value in [row.mt, row.zap, row.lfs, row.lmf] {
            hash.update(value.to_le_bytes());
        }
        for group in 0..library.group_count() {
            hash.update(library.cross_section(selected_row, group).to_le_bytes());
        }
    }
    let selected_payload_bytes = std::mem::size_of_val(library.values())
        + library.rows().len() * 40
        + std::mem::size_of_val(library.boundaries_ev());
    println!(
        "{}",
        serde_json::json!({
            "source_rows": library.source_row_count(),
            "selected_rows": library.rows().len(),
            "selected_values": library.values().len(),
            "groups": library.group_count(),
            "selected_payload_bytes": selected_payload_bytes,
            "materialized_bytes": library.materialized_bytes(),
            "selection_sha256": format!("{:x}", hash.finalize()),
        })
    );
}
