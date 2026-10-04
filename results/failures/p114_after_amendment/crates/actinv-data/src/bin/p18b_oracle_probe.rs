//! Small data-independent P18b diagnostic for fixed-width parsing and boundary behavior.

use actinv_data::endf::parse_endf_float;
use actinv_data::groups::Tabulated;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fs;
use std::path::Path;

const EPS_STANDARD: f64 = 0.001;
const P18_ABSOLUTE_TOLERANCE_B: f64 = 1e-12;
const P18_RELATIVE_TOLERANCE: f64 = 5e-10;

#[derive(Deserialize)]
struct Fixtures {
    schema: String,
    field_cases: Vec<FieldCase>,
    tables: Vec<TableCase>,
    comparisons: Vec<ComparisonCase>,
    threshold_cases: Vec<ThresholdCase>,
    excitation_cases: Vec<ExcitationCase>,
}

#[derive(Deserialize)]
struct FieldCase {
    id: String,
    field: String,
    valid: bool,
}

#[derive(Deserialize)]
struct TableCase {
    id: String,
    x: Vec<String>,
    y: Vec<String>,
    interpolation: Vec<(usize, i32)>,
    queries: Vec<QueryCase>,
}

#[derive(Deserialize)]
struct QueryCase {
    id: String,
    x: String,
    side: String,
}

#[derive(Deserialize)]
struct ComparisonCase {
    id: String,
    mf: i32,
    total: Option<String>,
    partials: Vec<String>,
}

#[derive(Deserialize)]
struct ThresholdCase {
    id: String,
    q: String,
    threshold: String,
    first_energy: String,
    first_value: String,
}

#[derive(Deserialize)]
struct ExcitationCase {
    id: String,
    elfs: String,
    qm: String,
    qi: String,
    catalog: String,
}

#[derive(Serialize)]
struct ProbeOutput {
    schema: &'static str,
    fixture_schema: String,
    fixture_sha256: String,
    fields: Vec<FieldOutput>,
    tables: Vec<TableOutput>,
    comparisons: Vec<ComparisonOutput>,
    thresholds: Vec<ThresholdOutput>,
    excitations: Vec<ExcitationOutput>,
}

#[derive(Serialize)]
struct FieldOutput {
    id: String,
    declared_valid: bool,
    parsed: bool,
    value_bits: Option<String>,
    quantum_power_10: Option<i32>,
    error: Option<String>,
}

#[derive(Serialize)]
struct TableOutput {
    id: String,
    queries: Vec<QueryOutput>,
}

#[derive(Serialize)]
struct QueryOutput {
    id: String,
    side: String,
    value: f64,
    value_bits: String,
}

#[derive(Serialize)]
struct ComparisonOutput {
    id: String,
    mf: i32,
    individual_classes: Vec<&'static str>,
    sum_class: &'static str,
    sum: Option<f64>,
    total: Option<f64>,
    p18_sum_violation: Option<bool>,
    standard_compatible: Option<bool>,
}

#[derive(Serialize)]
struct ThresholdOutput {
    id: String,
    decision: &'static str,
}

#[derive(Serialize)]
struct ExcitationOutput {
    id: String,
    derived_ev: f64,
    tolerance_ev: f64,
    decision: &'static str,
}

fn bits(value: f64) -> String {
    format!("{:016x}", value.to_bits())
}

fn checked_field(field: &str) -> Result<f64, String> {
    if field.len() != 11 || !field.is_ascii() {
        return Err("ENDF real field must be exactly 11 ASCII bytes".into());
    }
    parse_endf_float(field)
}

fn quantum_power(field: &str) -> Result<Option<i32>, String> {
    if field.len() != 11 || !field.is_ascii() {
        return Err("ENDF real field must be exactly 11 ASCII bytes".into());
    }
    let text = field.trim();
    if text.is_empty() {
        return Ok(None);
    }
    let bytes = text.as_bytes();
    let explicit = bytes.iter().position(|byte| matches!(byte, b'e' | b'E'));
    let implicit = (1..bytes.len()).find(|&index| {
        matches!(bytes[index], b'+' | b'-') && !matches!(bytes[index - 1], b'e' | b'E')
    });
    let split = explicit.or(implicit);
    let (mantissa, exponent) = match split {
        Some(index) => {
            let exponent_text = if matches!(bytes[index], b'e' | b'E') {
                &text[index + 1..]
            } else {
                &text[index..]
            };
            let exponent = exponent_text
                .parse::<i32>()
                .map_err(|_| format!("invalid ENDF exponent '{exponent_text}'"))?;
            (&text[..index], exponent)
        }
        None => (text, 0),
    };
    let decimals = mantissa
        .find('.')
        .map_or(0, |point| mantissa[point + 1..].len());
    let decimals = i32::try_from(decimals).map_err(|_| "decimal count overflows i32")?;
    Ok(Some(exponent - decimals))
}

fn parse_values(fields: &[String]) -> Result<Vec<f64>, String> {
    fields.iter().map(|field| checked_field(field)).collect()
}

fn field_quantum(field: &str) -> Result<f64, String> {
    Ok(match quantum_power(field)? {
        Some(power) => 10_f64.powi(power),
        None => 0.0,
    })
}

fn printing_class(total: f64, total_quantum: f64, partials: &[(f64, f64)]) -> &'static str {
    if total < 0.0 || partials.iter().any(|(value, _)| *value < 0.0) {
        return "malformed_or_nonfinite";
    }
    let sum: f64 = partials.iter().map(|(value, _)| value).sum();
    if sum <= total {
        return "source_conformant";
    }
    let partial_low: f64 = partials
        .iter()
        .map(|(value, quantum)| (value - quantum * 0.5).max(0.0))
        .sum();
    let total_high = total + total_quantum * 0.5;
    if partial_low > total_high {
        "definite_source_excess"
    } else {
        "printing_envelope_excess"
    }
}

fn standard_compatible(total: f64, sum: f64) -> bool {
    if sum <= total {
        return true;
    }
    if total > 0.0 {
        sum - total <= EPS_STANDARD * total
    } else {
        sum <= EPS_STANDARD
    }
}

fn excitation_tolerance(left_ev: f64, right_ev: f64) -> f64 {
    1.0_f64.max(5e-6 * left_ev.abs().max(right_ev.abs()))
}

fn probe(path: &Path) -> Result<ProbeOutput, String> {
    let bytes_in = fs::read(path).map_err(|error| format!("read {}: {error}", path.display()))?;
    let fixtures: Fixtures = serde_json::from_slice(&bytes_in)
        .map_err(|error| format!("parse {}: {error}", path.display()))?;

    let fields = fixtures
        .field_cases
        .into_iter()
        .map(|case| match checked_field(&case.field) {
            Ok(value) => FieldOutput {
                id: case.id,
                declared_valid: case.valid,
                parsed: true,
                value_bits: Some(bits(value)),
                quantum_power_10: quantum_power(&case.field).ok().flatten(),
                error: None,
            },
            Err(error) => FieldOutput {
                id: case.id,
                declared_valid: case.valid,
                parsed: false,
                value_bits: None,
                quantum_power_10: None,
                error: Some(error),
            },
        })
        .collect();

    let tables = fixtures
        .tables
        .into_iter()
        .map(|case| -> Result<TableOutput, String> {
            let table = Tabulated {
                interpolation: case.interpolation,
                x: parse_values(&case.x)?,
                y: parse_values(&case.y)?,
            };
            table.validate()?;
            let queries = case
                .queries
                .into_iter()
                .map(|query| -> Result<QueryOutput, String> {
                    let x = checked_field(&query.x)?;
                    let value = match query.side.as_str() {
                        "left" => table.evaluate_left_limit(x)?,
                        "right" => table.evaluate(x)?,
                        side => return Err(format!("unsupported query side '{side}'")),
                    };
                    Ok(QueryOutput {
                        id: query.id,
                        side: query.side,
                        value,
                        value_bits: bits(value),
                    })
                })
                .collect::<Result<Vec<_>, _>>()?;
            Ok(TableOutput {
                id: case.id,
                queries,
            })
        })
        .collect::<Result<Vec<_>, _>>()?;

    let comparisons = fixtures
        .comparisons
        .into_iter()
        .map(|case| -> Result<ComparisonOutput, String> {
            let partials = case
                .partials
                .iter()
                .map(|field| Ok((checked_field(field)?, field_quantum(field)?)))
                .collect::<Result<Vec<_>, String>>()?;
            let Some(total_field) = case.total else {
                return Ok(ComparisonOutput {
                    id: case.id,
                    mf: case.mf,
                    individual_classes: Vec::new(),
                    sum_class: "missing_total_or_grid_contract",
                    sum: None,
                    total: None,
                    p18_sum_violation: None,
                    standard_compatible: None,
                });
            };
            let total = checked_field(&total_field)?;
            let total_quantum = field_quantum(&total_field)?;
            let individual_classes = partials
                .iter()
                .map(|partial| printing_class(total, total_quantum, std::slice::from_ref(partial)))
                .collect();
            let sum: f64 = partials.iter().map(|(value, _)| value).sum();
            let p18_tolerance =
                P18_ABSOLUTE_TOLERANCE_B.max(P18_RELATIVE_TOLERANCE * total.max(0.0));
            Ok(ComparisonOutput {
                id: case.id,
                mf: case.mf,
                individual_classes,
                sum_class: printing_class(total, total_quantum, &partials),
                sum: Some(sum),
                total: Some(total),
                p18_sum_violation: Some(sum > total + p18_tolerance),
                standard_compatible: Some(standard_compatible(total, sum)),
            })
        })
        .collect::<Result<Vec<_>, _>>()?;

    let thresholds = fixtures
        .threshold_cases
        .into_iter()
        .map(|case| -> Result<ThresholdOutput, String> {
            let q = checked_field(&case.q)?;
            let threshold = checked_field(&case.threshold)?;
            let first_energy = checked_field(&case.first_energy)?;
            let first_value = checked_field(&case.first_value)?;
            let decision = if q >= 0.0 || (first_energy == threshold && first_value == 0.0) {
                "source_conformant"
            } else {
                "threshold_contract"
            };
            Ok(ThresholdOutput {
                id: case.id,
                decision,
            })
        })
        .collect::<Result<Vec<_>, _>>()?;

    let excitations = fixtures
        .excitation_cases
        .into_iter()
        .map(|case| -> Result<ExcitationOutput, String> {
            let elfs = checked_field(&case.elfs)?;
            let qm = checked_field(&case.qm)?;
            let qi = checked_field(&case.qi)?;
            let catalog = checked_field(&case.catalog)?;
            let derived = qm - qi;
            let identity_tolerance = excitation_tolerance(elfs, derived);
            let decision = if derived < -excitation_tolerance(0.0, derived) {
                "negative_q_excitation_conflict"
            } else if (elfs - derived).abs() > identity_tolerance {
                "mf8_q_excitation_conflict"
            } else if (elfs - catalog).abs() <= excitation_tolerance(elfs, catalog) {
                "catalog_excitation_match"
            } else {
                "no_catalog_excitation_match_to_leakage"
            };
            Ok(ExcitationOutput {
                id: case.id,
                derived_ev: derived,
                tolerance_ev: identity_tolerance,
                decision,
            })
        })
        .collect::<Result<Vec<_>, _>>()?;

    Ok(ProbeOutput {
        schema: "actinv-p18b-oracle-probe-1",
        fixture_schema: fixtures.schema,
        fixture_sha256: format!("{:x}", Sha256::digest(bytes_in)),
        fields,
        tables,
        comparisons,
        thresholds,
        excitations,
    })
}

fn main() {
    let mut arguments = std::env::args_os();
    let _program = arguments.next();
    let Some(path) = arguments.next() else {
        eprintln!("usage: p18b_oracle_probe FIXTURES.json");
        std::process::exit(2);
    };
    if arguments.next().is_some() {
        eprintln!("usage: p18b_oracle_probe FIXTURES.json");
        std::process::exit(2);
    }
    match probe(Path::new(&path)) {
        Ok(output) => match serde_json::to_string_pretty(&output) {
            Ok(json) => println!("{json}"),
            Err(error) => {
                eprintln!("p18b oracle probe: serialize output: {error}");
                std::process::exit(1);
            }
        },
        Err(error) => {
            eprintln!("p18b oracle probe: {error}");
            std::process::exit(1);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{field_quantum, printing_class, quantum_power, standard_compatible};

    #[test]
    fn quantum_tracks_all_three_endf_real_forms() {
        assert_eq!(quantum_power(" 1.23456789").unwrap(), Some(-8));
        assert_eq!(quantum_power(" 1.234567+9").unwrap(), Some(3));
        assert_eq!(quantum_power(" 1.23456-38").unwrap(), Some(-43));
        assert_eq!(quantum_power("           ").unwrap(), None);
    }

    #[test]
    fn standard_envelope_is_closed_at_positive_and_zero_boundaries() {
        assert!(standard_compatible(1_000.0, 1_001.0));
        assert!(!standard_compatible(1_000.0, 1_001.001));
        assert!(standard_compatible(0.0, 0.001));
        assert!(!standard_compatible(0.0, 0.001_000_001));
    }

    #[test]
    fn printed_intervals_distinguish_overlap_from_definite_excess() {
        let total_quantum = field_quantum(" 1.000000+0").unwrap();
        let overlap_quantum = field_quantum(" 5.000001-1").unwrap();
        let definite_quantum = field_quantum(" 5.000010-1").unwrap();
        assert_eq!(
            printing_class(
                1.0,
                total_quantum,
                &[
                    (0.500_000_1, overlap_quantum),
                    (0.500_000_1, overlap_quantum),
                ],
            ),
            "printing_envelope_excess"
        );
        assert_eq!(
            printing_class(
                1.0,
                total_quantum,
                &[(0.500_001, definite_quantum), (0.500_001, definite_quantum)],
            ),
            "definite_source_excess"
        );
    }
}
