//! P20 census probe: structural MF=33 inventory of one or more ENDF tapes.
//!
//! Emits one JSON object per input file with the target identity, the parsed
//! component list (same `parse_mf33` path the covariance builder uses), and
//! the exact parse error when a file fails closed.  Output goes to stdout.

use actinv_data::covariance::{parse_mf33, ComponentKind};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::fs;
use std::path::Path;

#[derive(Serialize)]
struct ComponentOut {
    mt: i32,
    mt1: i32,
    lb: i32,
    kind: &'static str,
    row_e_lo: f64,
    row_e_hi: f64,
    column_e_lo: f64,
    column_e_hi: f64,
    value_count: usize,
}

#[derive(Serialize)]
struct FileOut {
    file: String,
    source_sha256: String,
    za: Option<i32>,
    liso: Option<i32>,
    sections: usize,
    components: Vec<ComponentOut>,
    parse_error: Option<String>,
}

fn kind_name(kind: &ComponentKind) -> &'static str {
    match kind {
        ComponentKind::Absolute => "absolute",
        ComponentKind::Relative => "relative",
        ComponentKind::ShortRange8 => "short_range_8",
        ComponentKind::ShortRange9 => "short_range_9",
    }
}

fn target_header(text: &str) -> (Option<i32>, Option<i32>) {
    // ZA is field C1 of the first MF=1/MT=451 record and LISO field N1 of the second.
    // Locate them by their MF/MT tag rather than assuming exactly one banner line.
    let mut head = text
        .lines()
        .filter(|line| line.get(70..72) == Some(" 1") && line.get(72..75) == Some("451"));
    let field = |line: Option<&str>, columns: std::ops::Range<usize>| {
        line.and_then(|line| line.get(columns))
            .and_then(|field| actinv_data::endf::parse_endf_float(field).ok())
            .map(|value| value.round() as i32)
    };
    let first = head.next();
    let second = head.next();
    (field(first, 0..11), field(second, 33..44))
}

fn probe(path: &Path) -> FileOut {
    let bytes = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(error) => {
            return FileOut {
                file: path.display().to_string(),
                source_sha256: String::new(),
                za: None,
                liso: None,
                sections: 0,
                components: Vec::new(),
                parse_error: Some(format!("read: {error}")),
            }
        }
    };
    let text = String::from_utf8_lossy(&bytes);
    let (za, liso) = target_header(&text);
    match parse_mf33(&text) {
        Ok(components) => {
            let sections = components
                .iter()
                .map(|component| component.mt)
                .collect::<std::collections::BTreeSet<_>>()
                .len();
            FileOut {
                file: path.display().to_string(),
                source_sha256: format!("{:x}", Sha256::digest(&bytes)),
                za,
                liso,
                sections,
                components: components
                    .iter()
                    .map(|component| ComponentOut {
                        mt: component.mt,
                        mt1: component.mt1,
                        lb: component.lb,
                        kind: kind_name(&component.kind),
                        row_e_lo: component.row_grid.first().copied().unwrap_or(0.0),
                        row_e_hi: component.row_grid.last().copied().unwrap_or(0.0),
                        column_e_lo: component.column_grid.first().copied().unwrap_or(0.0),
                        column_e_hi: component.column_grid.last().copied().unwrap_or(0.0),
                        value_count: component.values.len(),
                    })
                    .collect(),
                parse_error: None,
            }
        }
        Err(error) => FileOut {
            file: path.display().to_string(),
            source_sha256: format!("{:x}", Sha256::digest(&bytes)),
            za,
            liso,
            sections: 0,
            components: Vec::new(),
            parse_error: Some(error),
        },
    }
}

fn main() {
    let mut arguments = std::env::args_os();
    let _program = arguments.next();
    let paths: Vec<_> = arguments.collect();
    if paths.is_empty() {
        eprintln!("usage: p20_corpus_probe FILE.tendl [FILE2.tendl ...]");
        std::process::exit(2);
    }
    println!("[");
    let last = paths.len() - 1;
    for (index, path) in paths.iter().enumerate() {
        let output = probe(Path::new(path));
        match serde_json::to_string(&output) {
            Ok(json) => println!("{json}{}", if index == last { "" } else { "," }),
            Err(error) => {
                eprintln!("p20 corpus probe: serialize {}: {error}", output.file);
                std::process::exit(1);
            }
        }
    }
    println!("]");
}
