//! Deterministic P12 production-reader reliability probe.
//!
//! The probe applies a fixed, bounded transformation set to small valid inputs and delivers every resulting case to
//! the same parsers used by ACTINV. It records only deterministic outcome totals and hashes; input bytes and expected
//! parser errors are intentionally not emitted.

use actinv_core::flux::FluxStream;
use actinv_core::mesh::MeshSpec;
use actinv_core::photon::PhotonResponse;
use actinv_core::spec::Spec;
use actinv_data::activation::parse_evaluations;
use actinv_data::covariance::parse_mf33;
use actinv_data::decay;
use actinv_data::endf::{parse_sections, read_list_checked};
use actinv_data::fission;
use actinv_data::groups::GroupStructure;
use actinv_data::library::{read_npz_bytes, write_npz, Library, Row};
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::io::Write;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::{Path, PathBuf};
use std::time::Instant;

const SMOKE_CASES: usize = 10_000;
const FULL_CASES: usize = 1_000_000;
const MASTER_SEED: u64 = 0x4143_5449_4e56_5031;
const OPERATORS: usize = 8;

#[derive(Clone, Copy, Debug)]
#[repr(u8)]
enum FamilyKind {
    RunSpec,
    MeshSpec,
    PhotonResponse,
    GroupStructure,
    EndfRecords,
    ActivationEvaluation,
    Covariance,
    Decay,
    FissionYields,
    ActivationLibrary,
    FluxStream,
}

impl FamilyKind {
    const ALL: [Self; 11] = [
        Self::RunSpec,
        Self::MeshSpec,
        Self::PhotonResponse,
        Self::GroupStructure,
        Self::EndfRecords,
        Self::ActivationEvaluation,
        Self::Covariance,
        Self::Decay,
        Self::FissionYields,
        Self::ActivationLibrary,
        Self::FluxStream,
    ];

    fn name(self) -> &'static str {
        match self {
            Self::RunSpec => "run_spec",
            Self::MeshSpec => "mesh_spec",
            Self::PhotonResponse => "photon_response",
            Self::GroupStructure => "group_structure",
            Self::EndfRecords => "endf_records_sections",
            Self::ActivationEvaluation => "activation_evaluation",
            Self::Covariance => "mf33_covariance",
            Self::Decay => "decay",
            Self::FissionYields => "fission_yields",
            Self::ActivationLibrary => "activation_library_npz",
            Self::FluxStream => "canonical_flux_stream",
        }
    }
}

struct Family {
    kind: FamilyKind,
    seed: Vec<u8>,
}

#[derive(Clone, Copy)]
enum MutationOperator {
    Truncate,
    Insert,
    Delete,
    ReplaceBit,
    DuplicateSpan,
    CountValue,
    InvalidEncoding,
    NumericEdge,
}

impl MutationOperator {
    fn from_index(index: usize) -> Self {
        match index % OPERATORS {
            0 => Self::Truncate,
            1 => Self::Insert,
            2 => Self::Delete,
            3 => Self::ReplaceBit,
            4 => Self::DuplicateSpan,
            5 => Self::CountValue,
            6 => Self::InvalidEncoding,
            _ => Self::NumericEdge,
        }
    }

    fn name(self) -> &'static str {
        match self {
            Self::Truncate => "truncate",
            Self::Insert => "insert",
            Self::Delete => "delete",
            Self::ReplaceBit => "replace_bit",
            Self::DuplicateSpan => "duplicate_span",
            Self::CountValue => "count_value",
            Self::InvalidEncoding => "invalid_encoding",
            Self::NumericEdge => "numeric_edge",
        }
    }
}

struct SplitMix64(u64);

impl SplitMix64 {
    fn new(seed: u64) -> Self {
        Self(seed)
    }

    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9e37_79b9_7f4a_7c15);
        let mut value = self.0;
        value = (value ^ (value >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
        value = (value ^ (value >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
        value ^ (value >> 31)
    }

    fn below(&mut self, limit: usize) -> usize {
        if limit == 0 {
            0
        } else {
            (self.next() % limit as u64) as usize
        }
    }
}

#[derive(Serialize)]
struct FamilySummary {
    family: &'static str,
    cases: usize,
    accepted: usize,
    rejected: usize,
    panics: usize,
    corpus_sha256: String,
}

#[derive(Serialize)]
struct FailureLocation {
    family: &'static str,
    case: usize,
    operator: &'static str,
}

#[derive(Serialize)]
struct DeterministicSummary {
    schema: &'static str,
    cases: usize,
    families: Vec<FamilySummary>,
    operators: BTreeMap<&'static str, usize>,
    outcome_sha256: String,
    first_panic: Option<FailureLocation>,
}

#[derive(Serialize)]
struct Report {
    gate: &'static str,
    probe_version: &'static str,
    partition: &'static str,
    seed_hex: String,
    deterministic: DeterministicSummary,
    elapsed_seconds: f64,
    peak_rss_bytes: u64,
    pass: bool,
}

fn record(values: [&str; 6], mat: i32, mf: i32, mt: i32, sequence: i32) -> String {
    let data: String = values
        .into_iter()
        .map(|value| format!("{value:>11}"))
        .collect();
    format!("{data}{mat:>4}{mf:>2}{mt:>3}{sequence:>5}")
}

fn send(mat: i32, mf: i32) -> String {
    record(["", "", "", "", "", ""], mat, mf, 0, 99_999)
}

fn endf_seed() -> Vec<u8> {
    [
        record(["1", "2", "0", "0", "6", "0"], 125, 3, 102, 1),
        record(["1", "2", "3", "4", "5", "6"], 125, 3, 102, 2),
        send(125, 3),
    ]
    .join("\n")
    .into_bytes()
}

fn activation_seed() -> Vec<u8> {
    [
        record(["26056", "55.45", "0", "0", "0", "0"], 2631, 1, 451, 1),
        record(["0", "0", "0", "0", "0", "0"], 2631, 1, 451, 2),
        record(["0.999", "2e8", "1", "0", "10010", "2025"], 2631, 1, 451, 3),
        record(["0", "0", "0", "0", "0", "0"], 2631, 1, 451, 4),
        send(2631, 1),
    ]
    .join("\n")
    .into_bytes()
}

fn covariance_seed() -> Vec<u8> {
    [
        record(["26056", "55.45", "0", "0", "0", "1"], 2631, 33, 102, 1),
        record(["0", "0", "0", "102", "0", "1"], 2631, 33, 102, 2),
        record(["0", "0", "1", "5", "6", "3"], 2631, 33, 102, 3),
        record(["1", "2", "4", "0.01", "0.002", "0.04"], 2631, 33, 102, 4),
        send(2631, 33),
    ]
    .join("\n")
    .into_bytes()
}

fn decay_seed() -> Vec<u8> {
    [
        record(["26056", "55.45", "0", "0", "0", "0"], 125, 8, 457, 1),
        record(["1", "0", "0", "0", "0", "0"], 125, 8, 457, 2),
        record(["0", "0", "0", "0", "0", "0"], 125, 8, 457, 3),
        send(125, 8),
    ]
    .join("\n")
    .into_bytes()
}

fn fission_seed() -> Vec<u8> {
    [
        record(["92235", "233", "0", "0", "0", "0"], 9237, 1, 451, 1),
        record(["0", "0", "0", "0", "0", "0"], 9237, 1, 451, 2),
        send(9237, 1),
        record(["92235", "233", "1", "0", "0", "0"], 9237, 8, 454, 1),
        record(["0.0253", "0", "0", "0", "4", "1"], 9237, 8, 454, 2),
        record(["53135", "0", "2", "0.1", "", ""], 9237, 8, 454, 3),
        send(9237, 8),
    ]
    .join("\n")
    .into_bytes()
}

fn library_seed(scratch: &Path) -> Result<Vec<u8>, String> {
    let library = Library {
        rows: vec![Row {
            target: 0,
            mt: 102,
            zap: 26_057,
            lfs: 0,
            lmf: 3,
        }],
        sig: vec![1.25],
        ngroups: 1,
        bounds: vec![1.0, 2.0],
    };
    let path = scratch.join("library-seed.npz");
    write_npz(&path, &library)?;
    let bytes = std::fs::read(&path)
        .map_err(|error| format!("cannot read generated library seed: {error}"))?;
    std::fs::remove_file(&path)
        .map_err(|error| format!("cannot remove generated library seed: {error}"))?;
    Ok(bytes)
}

fn families(scratch: &Path) -> Result<Vec<Family>, String> {
    let run_spec = br#"{"spec":"actinv-spec-1","library":{"path":"library.npz"},"decay":{"primary":"decay.endf"},"material":{"basis":"atoms_per_g","composition":{"Fe56":1.0}},"spectrum":{"structure":"custom","flux_per_group":[1.0],"boundaries_eV":[1.0,2.0]},"schedule":[{"dt":"1 s","flux":1.0}]}"#;
    let mesh_spec = br#"{"spec":"actinv-mesh-spec-1","library":{"path":"library.npz"},"decay":{"primary":"decay.endf"},"material":{"basis":"atoms_per_g","composition":{"Fe56":1.0}},"flux":{"path":"flux.ndjson","sha256":"0000000000000000000000000000000000000000000000000000000000000000"},"schedule":[{"dt":"1 s","flux":1.0}],"chunk_cells":1,"threads":1}"#;
    let photon = br#"{"schema":"actinv-photon-response-1","air_mass_energy_absorption":{"energy_eV":[1.0,2.0],"values_cm2_g":[1.0,1.0]},"element_mass_attenuation":{"Fe":{"energy_eV":[1.0,2.0],"values_cm2_g":[1.0,1.0]}}}"#;
    let groups = br#"{"structure":"fixture","boundaries_eV":[1.0,2.0,4.0]}"#;
    let flux = br#"{"record":"header","schema":"actinv-flux-1","source":{"format":"fixture","path":"fixture","sha256":"0000000000000000000000000000000000000000000000000000000000000000"},"energy_boundaries_eV":[1.0,2.0],"flux_units":"n cm^-2 s^-1","cell_count":1}
{"record":"cell","ordinal":0,"id":"cell-0","flux_per_group":[1.0],"flux_total":1.0}
{"record":"footer","cell_count":1,"flux_sum_over_cells":1.0}"#;
    Ok(vec![
        Family {
            kind: FamilyKind::RunSpec,
            seed: run_spec.to_vec(),
        },
        Family {
            kind: FamilyKind::MeshSpec,
            seed: mesh_spec.to_vec(),
        },
        Family {
            kind: FamilyKind::PhotonResponse,
            seed: photon.to_vec(),
        },
        Family {
            kind: FamilyKind::GroupStructure,
            seed: groups.to_vec(),
        },
        Family {
            kind: FamilyKind::EndfRecords,
            seed: endf_seed(),
        },
        Family {
            kind: FamilyKind::ActivationEvaluation,
            seed: activation_seed(),
        },
        Family {
            kind: FamilyKind::Covariance,
            seed: covariance_seed(),
        },
        Family {
            kind: FamilyKind::Decay,
            seed: decay_seed(),
        },
        Family {
            kind: FamilyKind::FissionYields,
            seed: fission_seed(),
        },
        Family {
            kind: FamilyKind::ActivationLibrary,
            seed: library_seed(scratch)?,
        },
        Family {
            kind: FamilyKind::FluxStream,
            seed: flux.to_vec(),
        },
    ])
}

fn digit_runs(input: &[u8]) -> Vec<(usize, usize)> {
    let mut runs = Vec::new();
    let mut index = 0;
    while index < input.len() {
        if input[index].is_ascii_digit() {
            let start = index;
            index += 1;
            while index < input.len() && input[index].is_ascii_digit() {
                index += 1;
            }
            runs.push((start, index));
        } else {
            index += 1;
        }
    }
    runs
}

fn replace_number(input: &mut Vec<u8>, rng: &mut SplitMix64, replacements: &[&[u8]]) {
    let runs = digit_runs(input);
    let replacement = replacements[rng.below(replacements.len())];
    if runs.is_empty() {
        let position = rng.below(input.len() + 1);
        input.splice(position..position, replacement.iter().copied());
    } else {
        let (start, end) = runs[rng.below(runs.len())];
        input.splice(start..end, replacement.iter().copied());
    }
}

fn mutate(seed: &[u8], operator: MutationOperator, rng: &mut SplitMix64) -> Vec<u8> {
    let mut input = seed.to_vec();
    match operator {
        MutationOperator::Truncate => input.truncate(rng.below(input.len())),
        MutationOperator::Insert => {
            const INSERTIONS: [u8; 6] = *b"0{] \n,";
            let position = rng.below(input.len() + 1);
            let value = INSERTIONS[rng.below(INSERTIONS.len())];
            input.insert(position, value);
        }
        MutationOperator::Delete => {
            let start = rng.below(input.len());
            let count = 1 + rng.below((input.len() - start).min(16));
            input.drain(start..start + count);
        }
        MutationOperator::ReplaceBit => {
            let position = rng.below(input.len());
            input[position] ^= 1 << rng.below(8);
        }
        MutationOperator::DuplicateSpan => {
            let start = rng.below(input.len());
            let count = 1 + rng.below((input.len() - start).min(16));
            let duplicate = input[start..start + count].to_vec();
            let position = rng.below(input.len() + 1);
            input.splice(position..position, duplicate);
        }
        MutationOperator::CountValue => replace_number(
            &mut input,
            rng,
            &[b"0", b"-1", b"2147483647", b"99999999999"],
        ),
        MutationOperator::InvalidEncoding => {
            let position = rng.below(input.len());
            if rng.next() & 1 == 0 {
                input[position] = 0xff;
            } else {
                input.splice(position..=position, [0xc2, 0xa0]);
            }
        }
        MutationOperator::NumericEdge => replace_number(
            &mut input,
            rng,
            &[b"NaN", b"1e309", b"-0", b"-1e309", b"5e-324"],
        ),
    }
    input
}

fn text(input: &[u8]) -> Result<&str, String> {
    std::str::from_utf8(input).map_err(|_| "input is not UTF-8".to_string())
}

fn evaluate(kind: FamilyKind, input: &[u8], flux_path: &Path) -> Result<(), String> {
    match kind {
        FamilyKind::RunSpec => Spec::from_json(text(input)?).map(|_| ()),
        FamilyKind::MeshSpec => MeshSpec::from_json(text(input)?).map(|_| ()),
        FamilyKind::PhotonResponse => PhotonResponse::from_json(text(input)?).map(|_| ()),
        FamilyKind::GroupStructure => GroupStructure::from_json(text(input)?).map(|_| ()),
        FamilyKind::EndfRecords => {
            let sections = parse_sections(text(input)?)?;
            let section = sections.first().ok_or("ENDF seed produced no section")?;
            let (_, next) = read_list_checked(&section.lines, 0)?;
            if next != section.lines.len() {
                return Err("ENDF LIST left unconsumed records".into());
            }
            Ok(())
        }
        FamilyKind::ActivationEvaluation => parse_evaluations(text(input)?, None).map(|_| ()),
        FamilyKind::Covariance => parse_mf33(text(input)?).map(|_| ()),
        FamilyKind::Decay => decay::parse_text(text(input)?).map(|_| ()),
        FamilyKind::FissionYields => fission::parse_text(text(input)?).map(|_| ()),
        FamilyKind::ActivationLibrary => read_npz_bytes(input).map(|_| ()),
        FamilyKind::FluxStream => {
            let mut file = std::fs::File::create(flux_path)
                .map_err(|error| format!("cannot create flux case: {error}"))?;
            file.write_all(input)
                .map_err(|error| format!("cannot write flux case: {error}"))?;
            drop(file);
            FluxStream::open(flux_path)?.finish().map(|_| ())
        }
    }
}

fn partition(cases: usize) -> Result<(&'static str, [usize; 11]), String> {
    match cases {
        SMOKE_CASES => Ok((
            "smoke",
            [
                1_090, 1_090, 1_090, 1_090, 1_090, 1_090, 1_090, 1_090, 1_080, 100, 100,
            ],
        )),
        FULL_CASES => Ok((
            "full",
            [
                109_000, 109_000, 109_000, 109_000, 109_000, 109_000, 109_000, 109_000, 108_000,
                10_000, 10_000,
            ],
        )),
        _ => Err(format!(
            "cases must be the fixed {SMOKE_CASES} or {FULL_CASES} partition"
        )),
    }
}

fn sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn peak_rss_bytes() -> u64 {
    std::fs::read_to_string("/proc/self/status")
        .ok()
        .and_then(|status| {
            status.lines().find_map(|line| {
                line.strip_prefix("VmHWM:")
                    .and_then(|value| value.split_whitespace().next())
                    .and_then(|value| value.parse::<u64>().ok())
            })
        })
        .and_then(|kibibytes| kibibytes.checked_mul(1024))
        .unwrap_or(0)
}

fn parse_arguments() -> Result<(usize, PathBuf), String> {
    let mut cases = None;
    let mut scratch = None;
    let mut arguments = std::env::args().skip(1);
    while let Some(argument) = arguments.next() {
        match argument.as_str() {
            "--cases" => {
                let value = arguments.next().ok_or("--cases needs a value")?;
                cases = Some(
                    value
                        .parse::<usize>()
                        .map_err(|_| format!("invalid case count '{value}'"))?,
                );
            }
            "--scratch" => {
                scratch = Some(PathBuf::from(
                    arguments.next().ok_or("--scratch needs a path")?,
                ));
            }
            _ => return Err(format!("unknown argument '{argument}'")),
        }
    }
    let scratch = scratch.unwrap_or_else(|| {
        std::env::temp_dir().join(format!("actinv-p12-verify-{}", std::process::id()))
    });
    Ok((cases.unwrap_or(SMOKE_CASES), scratch))
}

fn run(cases: usize, scratch: &Path) -> Result<Report, String> {
    let (partition_name, counts) = partition(cases)?;
    std::fs::create_dir_all(scratch)
        .map_err(|error| format!("cannot create scratch directory: {error}"))?;
    let flux_path = scratch.join("flux-case.ndjson");
    let families = families(scratch)?;
    if families.len() != FamilyKind::ALL.len() {
        return Err("internal family count mismatch".into());
    }
    for family in &families {
        evaluate(family.kind, &family.seed, &flux_path)
            .map_err(|error| format!("valid {} seed failed: {error}", family.kind.name()))?;
    }

    let started = Instant::now();
    let mut digest = Sha256::new();
    digest.update(b"ACTINV-P12-READER-OUTCOMES-v1\0");
    digest.update((cases as u64).to_le_bytes());
    let mut summaries = Vec::with_capacity(families.len());
    let mut operator_counts = BTreeMap::new();
    let mut first_panic = None;

    let previous_hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(|_| {}));
    for (family_index, (family, &family_cases)) in families.iter().zip(&counts).enumerate() {
        let mut accepted = 0;
        let mut rejected = 0;
        let mut panics = 0;
        let corpus_sha256 = sha256(&family.seed);
        digest.update(family.kind.name().as_bytes());
        digest.update(corpus_sha256.as_bytes());
        for case in 0..family_cases {
            let operator = MutationOperator::from_index(case + family_index);
            *operator_counts.entry(operator.name()).or_insert(0) += 1;
            let case_seed = MASTER_SEED
                ^ (family_index as u64 + 1).wrapping_mul(0x9e37_79b9_7f4a_7c15)
                ^ case as u64;
            let mut rng = SplitMix64::new(case_seed);
            let input = mutate(&family.seed, operator, &mut rng);
            let outcome = match catch_unwind(AssertUnwindSafe(|| {
                evaluate(family.kind, &input, &flux_path)
            })) {
                Ok(Ok(())) => {
                    accepted += 1;
                    1u8
                }
                Ok(Err(_)) => {
                    rejected += 1;
                    0u8
                }
                Err(_) => {
                    panics += 1;
                    if first_panic.is_none() {
                        first_panic = Some(FailureLocation {
                            family: family.kind.name(),
                            case,
                            operator: operator.name(),
                        });
                    }
                    2u8
                }
            };
            digest.update([family.kind as u8, operator as u8, outcome]);
            digest.update((case as u64).to_le_bytes());
            digest.update((input.len() as u64).to_le_bytes());
            digest.update(&input);
        }
        summaries.push(FamilySummary {
            family: family.kind.name(),
            cases: family_cases,
            accepted,
            rejected,
            panics,
            corpus_sha256,
        });
    }
    std::panic::set_hook(previous_hook);

    let _ = std::fs::remove_file(&flux_path);
    let pass = summaries.iter().all(|summary| summary.panics == 0);
    Ok(Report {
        gate: "P12-G3",
        probe_version: env!("CARGO_PKG_VERSION"),
        partition: partition_name,
        seed_hex: format!("{MASTER_SEED:016x}"),
        deterministic: DeterministicSummary {
            schema: "actinv-p12-reader-outcomes-1",
            cases,
            families: summaries,
            operators: operator_counts,
            outcome_sha256: format!("{:x}", digest.finalize()),
            first_panic,
        },
        elapsed_seconds: started.elapsed().as_secs_f64(),
        peak_rss_bytes: peak_rss_bytes(),
        pass,
    })
}

fn main() {
    let result = parse_arguments().and_then(|(cases, scratch)| run(cases, &scratch));
    match result {
        Ok(report) => {
            let pass = report.pass;
            println!(
                "{}",
                serde_json::to_string(&report).expect("serialize P12 report")
            );
            if !pass {
                std::process::exit(1);
            }
        }
        Err(error) => {
            eprintln!("P12 verification failed: {error}");
            std::process::exit(2);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fixed_partitions_have_the_required_totals() {
        let (_, smoke) = partition(SMOKE_CASES).unwrap();
        let (_, full) = partition(FULL_CASES).unwrap();
        assert_eq!(smoke.iter().sum::<usize>(), SMOKE_CASES);
        assert_eq!(full.iter().sum::<usize>(), FULL_CASES);
        assert!(full[FamilyKind::ActivationLibrary as usize] >= 10_000);
        assert!(full[FamilyKind::FluxStream as usize] >= 10_000);
        assert!(full.into_iter().all(|count| count > 0));
    }

    #[test]
    fn transformations_are_deterministic_and_bounded() {
        let seed = b"{\"count\":12,\"value\":3.5}";
        for index in 0..OPERATORS {
            let operator = MutationOperator::from_index(index);
            let mut first_rng = SplitMix64::new(7);
            let mut second_rng = SplitMix64::new(7);
            let first = mutate(seed, operator, &mut first_rng);
            let second = mutate(seed, operator, &mut second_rng);
            assert_eq!(first, second);
            assert_ne!(first, seed);
            assert!(first.len() <= seed.len() + 32);
        }
    }
}
