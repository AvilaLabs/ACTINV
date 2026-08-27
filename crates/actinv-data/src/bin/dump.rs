//! P5-G1 helper: dump what the Rust readers see, for comparison against the Python implementations.
//!   dump decay FILE          -> "ZA LISO NST half_life e_light e_em e_heavy nmodes br0 q0 ..." per nuclide
//!   dump spectra FILE [ZA:LISO ...] -> lossless line-oriented MF=8/MT=457 spectrum records
//!   dump spectra-summary FILE -> all-file section/spectrum and STYP/LCON counts
//!   dump activation FILE -> strict MF=3/6/8/9/10 evaluation summary
//!   dump resonance FILE -> strict MF=2/MT=151 structure summary
//!   dump resonance-xs FILE ENERGY_EV [...] -> zero-K resonance-only cross sections
//!   dump unresolved-probe CASE E D GX GN GG GF MUX MUN MUF LSSF -> synthetic G3 control
//!   dump processed-xs FILE MT TEMPERATURE_K [ENERGY_EV ...] -> processed points or 709 groups
//!   dump library FILE OUT    -> raw row and group arrays for byte comparison
//!   dump library-target-compare OLD.npz OLD_TARGET NEW.npz NEW_TARGET -> bounded-memory row/group comparison
use actinv_data::{
    activation, composition, decay, endf, fission, groups, library, processing, resonance,
};

fn unresolved_probe(arguments: &[String]) {
    assert_eq!(
        arguments.len(),
        11,
        "unresolved-probe needs eleven arguments"
    );
    let case = match arguments[0].as_str() {
        "A" => resonance::UnresolvedCase::A,
        "B" => resonance::UnresolvedCase::B,
        "C" => resonance::UnresolvedCase::C,
        value => panic!("unknown unresolved case {value}"),
    };
    let parse_float = |index: usize, label: &str| {
        arguments[index]
            .parse::<f64>()
            .unwrap_or_else(|_| panic!("invalid unresolved-probe {label}"))
    };
    let parse_dof = |index: usize, label: &str| {
        arguments[index]
            .parse::<i32>()
            .unwrap_or_else(|_| panic!("invalid unresolved-probe {label}"))
    };
    let energy = parse_float(1, "energy");
    let spacing = parse_float(2, "spacing");
    let competitive = parse_float(3, "competitive width");
    let neutron = parse_float(4, "neutron width");
    let capture = parse_float(5, "capture width");
    let fission = parse_float(6, "fission width");
    let competitive_dof = parse_dof(7, "competitive degrees of freedom");
    let neutron_dof = parse_dof(8, "neutron degrees of freedom");
    let fission_dof = parse_dof(9, "fission degrees of freedom");
    let lssf = parse_dof(10, "LSSF");
    assert!(matches!(lssf, 0 | 1), "LSSF must be zero or one");
    assert!(
        energy.is_finite() && energy > 0.0,
        "energy must be positive and finite"
    );
    assert!(
        spacing.is_finite() && spacing > 0.0,
        "spacing must be positive and finite"
    );
    assert!(
        [competitive, neutron, capture, fission]
            .into_iter()
            .all(|width| width.is_finite() && width >= 0.0),
        "widths must be finite and nonnegative"
    );
    assert!(
        [competitive_dof, neutron_dof, fission_dof]
            .into_iter()
            .all(|degrees| (0..=4).contains(&degrees)),
        "degrees of freedom must be in 0..4"
    );
    match case {
        resonance::UnresolvedCase::A => assert!(
            competitive == 0.0 && fission == 0.0 && competitive_dof == 0 && fission_dof == 0,
            "case A cannot declare competitive or fission widths"
        ),
        resonance::UnresolvedCase::B => assert!(
            competitive == 0.0 && competitive_dof == 0,
            "case B cannot declare a competitive width"
        ),
        resonance::UnresolvedCase::C => {}
    }

    let energy_nodes: Vec<Option<f64>> = if case == resonance::UnresolvedCase::A {
        vec![None]
    } else {
        vec![Some(energy / 2.0), Some(energy), Some(2.0 * energy)]
    };
    let points = energy_nodes
        .into_iter()
        .map(|point_energy| resonance::UnresolvedPoint {
            energy: point_energy,
            spacing,
            competitive,
            neutron,
            capture,
            fission,
        })
        .collect();
    let range = resonance::ResonanceRange {
        energy_min: energy / 2.0,
        energy_max: 2.0 * energy,
        lru: 2,
        lrf: if case == resonance::UnresolvedCase::C {
            2
        } else {
            1
        },
        naps: 1,
        scattering_radius: None,
        data: resonance::RangeData::Unresolved(resonance::Unresolved {
            spin: 0.5,
            ap: 0.5,
            add_to_background: lssf == 0,
            case,
            sequences: vec![resonance::UnresolvedSequence {
                awri: 56.0,
                l: 0,
                spin: 0.5,
                interpolation: 2,
                competitive_dof,
                neutron_dof,
                fission_dof,
                points,
            }],
            interpolation_energies: vec![energy / 2.0, energy, 2.0 * energy],
        }),
    };
    let direct = resonance::reconstruct_unresolved(&range, energy)
        .expect("reconstruct synthetic unresolved case");
    println!(
        "U {} {lssf} {energy:.17e} {:.17e} {:.17e} {:.17e} {:.17e}",
        arguments[0], direct.elastic, direct.capture, direct.fission, direct.competitive
    );

    let evaluation = resonance::ResonanceEvaluation {
        za: 26_056,
        awr: 56.0,
        isotopes: vec![resonance::ResonanceIsotope {
            zai: 26_056,
            abundance: 1.0,
            fission_widths: fission > 0.0,
            ranges: vec![range],
        }],
    };
    let control_groups = groups::GroupStructure {
        name: "unresolved-probe".into(),
        boundaries_ev: vec![energy / 2.0, 2.0 * energy],
    };
    for (mt, background_value) in [(2, 3.0), (18, 5.0), (102, 7.0)] {
        let background = groups::Tabulated {
            interpolation: vec![(2, 2)],
            x: vec![energy / 2.0, 2.0 * energy],
            y: vec![background_value, background_value],
        };
        let processed =
            processing::process_reaction(&evaluation, &background, &control_groups, mt, 0.0, 1.0)
                .expect("process synthetic unresolved case");
        let value = processed
            .table
            .evaluate(energy)
            .expect("evaluate synthetic processed reaction");
        println!("P {mt} {background_value:.17e} {value:.17e}");
    }
}

fn main() {
    let a: Vec<String> = std::env::args().collect();
    match a[1].as_str() {
        "decay" => {
            let m = decay::parse_file(&a[2]).expect("read decay file");
            let mut keys: Vec<_> = m.keys().copied().collect();
            keys.sort();
            println!("{}", keys.len());
            for k in keys {
                let n = &m[&k];
                print!(
                    "{} {} {} {:.17e} {:.17e} {:.17e} {:.17e} {}",
                    n.za,
                    n.liso,
                    n.nst,
                    n.half_life,
                    n.e_light(),
                    n.e_em(),
                    n.e_heavy(),
                    n.modes.len()
                );
                let mut ms: Vec<_> = n
                    .modes
                    .iter()
                    .map(|md| (md.rtyp, md.rfs, md.br, md.q))
                    .collect();
                ms.sort_by(|x, y| x.partial_cmp(y).unwrap());
                for (rtyp, rfs, br, q) in ms {
                    print!(" {:.17e} {:.17e} {:.17e} {:.17e}", rtyp, rfs, br, q);
                }
                println!();
            }
        }
        "spectra" => {
            let m = decay::parse_file(&a[2]).expect("read decay file");
            let mut keys: Vec<_> = m.keys().copied().collect();
            keys.sort();
            if a.len() > 3 {
                let selected: std::collections::HashSet<(i32, i32)> = a[3..]
                    .iter()
                    .map(|value| {
                        let mut fields = value.split(':');
                        let za = fields.next().unwrap_or("").parse().expect("ZA in ZA:LISO");
                        let liso = fields
                            .next()
                            .unwrap_or("0")
                            .parse()
                            .expect("LISO in ZA:LISO");
                        (za, liso)
                    })
                    .collect();
                keys.retain(|key| selected.contains(key));
            }
            println!("{}", keys.len());
            for key in keys {
                let n = &m[&key];
                println!("N {} {} {}", n.za, n.liso, n.spectra.len());
                for (si, s) in n.spectra.iter().enumerate() {
                    println!("S {si} {:.17e} {} {} {:.17e} {:.17e} {:.17e} {:.17e} {:.17e} {:.17e} {} {}",
                        s.styp, s.lcon, s.lcov, s.fd, s.d_fd, s.average_energy, s.d_average_energy,
                        s.fc, s.d_fc, s.discrete.len(), usize::from(s.continuous.is_some()));
                    for d in &s.discrete {
                        println!("D {si} {:.17e} {:.17e} {:.17e} {:.17e} {:.17e} {:.17e} {:.17e} {:.17e} {:.17e} {:.17e} {:.17e} {:.17e} {:.17e} {:.17e}",
                            d.energy, d.d_energy, d.rtyp, d.transition_type, d.intensity, d.d_intensity,
                            d.pair_intensity, d.d_pair_intensity, d.conversion_total, d.d_conversion_total,
                            d.conversion_k, d.d_conversion_k, d.conversion_l, d.d_conversion_l);
                    }
                    if let Some(c) = &s.continuous {
                        println!(
                            "C {si} {:.17e} {} {}",
                            c.rtyp,
                            c.interpolation.len(),
                            c.points.len()
                        );
                        for (nbt, int) in &c.interpolation {
                            println!("R {si} {nbt} {int}");
                        }
                        for (e, p) in &c.points {
                            println!("P {si} {:.17e} {:.17e}", e, p);
                        }
                    }
                }
            }
        }
        "spectra-summary" => {
            let m = decay::parse_file(&a[2]).expect("read decay file");
            let mut counts: std::collections::BTreeMap<(i32, i32), usize> =
                std::collections::BTreeMap::new();
            let mut spectra = 0usize;
            for nuclide in m.values() {
                spectra += nuclide.spectra.len();
                for spectrum in &nuclide.spectra {
                    *counts
                        .entry((spectrum.styp.round() as i32, spectrum.lcon))
                        .or_default() += 1;
                }
            }
            println!("{} {}", m.len(), spectra);
            for ((styp, lcon), count) in counts {
                println!("C {styp} {lcon} {count}");
            }
        }
        "fission-yields" => {
            let yields = fission::parse_file(&a[2]).expect("read fission-yield file");
            println!(
                "F {} {} {:.17e} {} {}",
                yields.parent.0,
                yields.parent.1,
                yields.awr,
                yields.independent.len(),
                yields.cumulative.len()
            );
            for (kind, tables) in [
                ("I", &yields.independent),
                ("C", &yields.cumulative),
            ] {
                for table in tables {
                    println!(
                        "{kind} {:.17e} {} {:.17e}",
                        table.energy_ev,
                        table.products.len(),
                        table.sum
                    );
                    for ((za, liso), value) in &table.products {
                        println!(
                            "Y {kind} {:.17e} {za} {liso} {:.17e} {:.17e}",
                            table.energy_ev, value.value, value.uncertainty
                        );
                    }
                }
            }
        }
        "fission-effective" => {
            let yields = fission::parse_file(&a[2]).expect("read fission-yield file");
            let energy: f64 = a[3].parse().expect("incident energy in eV");
            let effective = yields.effective(energy).expect("select effective yields");
            println!(
                "E {:.17e} {:.17e} {:.17e} {:.17e} {} {:.17e} {}",
                effective.requested_energy_ev,
                effective.lower_energy_ev,
                effective.upper_energy_ev,
                effective.upper_weight,
                usize::from(effective.clamped),
                effective.sum,
                effective.products.len()
            );
            for ((za, liso), value) in effective.products {
                println!("Y {za} {liso} {value:.17e}");
            }
        }
        "activation" => {
            let evaluations = activation::parse_file(&a[2], None).expect("read activation file");
            println!("{}", evaluations.len());
            for evaluation in evaluations {
                let laws: std::collections::BTreeSet<i32> = evaluation
                    .mf6
                    .values()
                    .flatten()
                    .map(|product| product.law)
                    .collect();
                println!(
                    "E {} {} {} {} {:.17e} {:.17e} {} {} {} {} {} {:?}",
                    evaluation.metadata.mat,
                    evaluation.metadata.za,
                    evaluation.metadata.liso,
                    evaluation.metadata.projectile.name(),
                    evaluation.metadata.awr,
                    evaluation.metadata.awi,
                    evaluation.mf3.len(),
                    evaluation.mf6.len(),
                    evaluation.mf8.len(),
                    evaluation.mf9.len(),
                    evaluation.mf10.len(),
                    laws
                );
                if let Some(mt) = a.get(3).and_then(|value| value.parse::<i32>().ok()) {
                    if let Some(table) = evaluation.mf3.get(&mt) {
                        println!(
                            "X {mt} {} {:.17e} {:.17e}",
                            table.x.len(),
                            table.y.iter().copied().fold(f64::INFINITY, f64::min),
                            table.y.iter().copied().fold(f64::NEG_INFINITY, f64::max)
                        );
                    }
                    for product in evaluation.mf8.get(&mt).into_iter().flatten() {
                        println!("R {mt} {} {} {}", product.zap, product.lfs, product.lmf);
                    }
                    for product in evaluation.mf6.get(&mt).into_iter().flatten() {
                        println!(
                            "Y {mt} {} {} {} {}",
                            product.zap,
                            product.law,
                            product.yield_table.x.len(),
                            product.yield_table.y.iter().copied().fold(0.0, f64::max)
                        );
                    }
                }
            }
        }
        "resonance" => {
            let text = std::fs::read_to_string(&a[2]).expect("read resonance file");
            let sections = endf::parse_sections(&text).expect("parse ENDF sections");
            let mut count = 0usize;
            for section in sections
                .iter()
                .filter(|section| section.mf == 2 && section.mt == 151)
            {
                let evaluation = resonance::parse_mf2(section).expect("parse MF=2/MT=151");
                println!(
                    "E {} {} {:.17e} {}",
                    section.mat,
                    evaluation.za,
                    evaluation.awr,
                    evaluation.isotopes.len()
                );
                for isotope in &evaluation.isotopes {
                    println!(
                        "I {} {:.17e} {} {}",
                        isotope.zai,
                        isotope.abundance,
                        usize::from(isotope.fission_widths),
                        isotope.ranges.len()
                    );
                    for range in &isotope.ranges {
                        let (kind, groups, resonances, channels, sequences, points) = match &range.data {
                            resonance::RangeData::ScatteringOnly { .. } => {
                                ("scattering", 0, 0, 0, 0, 0)
                            }
                            resonance::RangeData::BreitWigner(data) => (
                                "breit-wigner",
                                data.groups.len(),
                                data.groups.iter().map(|group| group.resonances.len()).sum(),
                                0,
                                0,
                                0,
                            ),
                            resonance::RangeData::ReichMoore(data) => (
                                "reich-moore",
                                data.groups.len(),
                                data.groups.iter().map(|group| group.resonances.len()).sum(),
                                0,
                                0,
                                0,
                            ),
                            resonance::RangeData::RMatrixLimited(data) => (
                                "r-matrix-limited",
                                data.spin_groups.len(),
                                data.spin_groups
                                    .iter()
                                    .map(|group| group.resonances.len())
                                    .sum(),
                                data.spin_groups.iter().map(|group| group.channels.len()).sum(),
                                0,
                                0,
                            ),
                            resonance::RangeData::Unresolved(data) => (
                                "unresolved",
                                0,
                                0,
                                0,
                                data.sequences.len(),
                                data.sequences.iter().map(|sequence| sequence.points.len()).sum(),
                            ),
                        };
                        println!(
                            "R {:.17e} {:.17e} {} {} {} {} {} {} {} {} {} {}",
                            range.energy_min,
                            range.energy_max,
                            range.lru,
                            range.lrf,
                            range.naps,
                            usize::from(range.scattering_radius.is_some()),
                            kind,
                            groups,
                            resonances,
                            channels,
                            sequences,
                            points
                        );
                    }
                }
                count += 1;
            }
            println!("N {count}");
        }
        "resonance-xs" => {
            let text = std::fs::read_to_string(&a[2]).expect("read resonance file");
            let sections = endf::parse_sections(&text).expect("parse ENDF sections");
            let section = sections
                .iter()
                .find(|section| section.mf == 2 && section.mt == 151)
                .expect("find MF=2/MT=151");
            let evaluation = resonance::parse_mf2(section).expect("parse MF=2/MT=151");
            for value in &a[3..] {
                let energy: f64 = value.parse().expect("energy in eV");
                let mut total = resonance::CrossSections::default();
                for isotope in &evaluation.isotopes {
                    for range in &isotope.ranges {
                        let xs = match &range.data {
                            resonance::RangeData::BreitWigner(_)
                            | resonance::RangeData::ReichMoore(_) => {
                                resonance::reconstruct_legacy(range, energy)
                            }
                            resonance::RangeData::RMatrixLimited(_) => {
                                resonance::reconstruct_rmatrix_limited(range, energy)
                            }
                            resonance::RangeData::Unresolved(_) => {
                                resonance::reconstruct_unresolved(range, energy)
                            }
                            _ => Ok(resonance::CrossSections::default()),
                        }
                        .expect("reconstruct resonance range");
                        total.elastic += isotope.abundance * xs.elastic;
                        total.capture += isotope.abundance * xs.capture;
                        total.fission += isotope.abundance * xs.fission;
                        total.competitive += isotope.abundance * xs.competitive;
                    }
                }
                println!(
                    "X {:.17e} {:.17e} {:.17e} {:.17e} {:.17e}",
                    energy, total.elastic, total.capture, total.fission, total.competitive
                );
            }
        }
        "unresolved-probe" => unresolved_probe(&a[2..]),
        "processed-xs" => {
            let text = std::fs::read_to_string(&a[2]).expect("read resonance file");
            let mt: i32 = a[3].parse().expect("MT");
            let temperature: f64 = a[4].parse().expect("temperature K");
            let sections = endf::parse_sections(&text).expect("parse ENDF sections");
            let resonance_section = sections
                .iter()
                .find(|section| section.mf == 2 && section.mt == 151)
                .expect("find MF=2/MT=151");
            let evaluation =
                resonance::parse_mf2(resonance_section).expect("parse MF=2/MT=151");
            let mf3_section = sections
                .iter()
                .find(|section| section.mf == 3 && section.mt == mt)
                .expect("find MF=3 reaction");
            let (record, next) = endf::read_tab1_checked(&mf3_section.lines, 1)
                .expect("parse MF=3 TAB1");
            assert_eq!(next, mf3_section.lines.len(), "consume MF=3 reaction");
            let background = groups::Tabulated::try_from(record).expect("validate MF=3 TAB1");
            let group_structure = groups::GroupStructure::fispact_709().expect("709 groups");
            let processed = processing::process_reaction(
                &evaluation,
                &background,
                &group_structure,
                mt,
                temperature,
                1.0,
            )
            .expect("process resonances");
            println!(
                "C {} {} {} {}",
                processed.certificate.zero_k_points,
                processed.certificate.output_points,
                processed.certificate.zero_k_refinement_passes,
                processed.certificate.output_refinement_passes
            );
            for line in &processed.certificate.ultra_narrow_lines {
                println!(
                    "U {} {:.17e} {:.17e} {:.17e} {:.17e} {:.17e} {:.17e} {} {} {:.17e} {:.17e}",
                    line.isotope_zai,
                    line.energy_ev,
                    line.total_width_ev,
                    line.doppler_width_ev,
                    line.width_to_doppler_ratio,
                    line.direct_area_barn_ev,
                    line.closed_form_area_barn_ev,
                    line.affected_group,
                    usize::from(line.range_edge_decomposition),
                    line.core_low_ev,
                    line.core_high_ev
                );
            }
            if a.len() > 5 {
                for value in &a[5..] {
                    let energy: f64 = value.parse().expect("energy in eV");
                    println!(
                        "X {:.17e} {:.17e}",
                        energy,
                        processed.table.evaluate(energy).expect("evaluate processed table")
                    );
                }
            } else {
                for (index, value) in processed
                    .collapse(&group_structure)
                    .expect("collapse processed reaction")
                    .into_iter()
                    .enumerate()
                {
                    println!("G {index} {value:.17e}");
                }
            }
        }
        "ultra-lines" => {
            let text = std::fs::read_to_string(&a[2]).expect("read resonance file");
            let mt: i32 = a[3].parse().expect("MT");
            let temperature: f64 = a[4].parse().expect("temperature K");
            let sections = endf::parse_sections(&text).expect("parse ENDF sections");
            let section = sections
                .iter()
                .find(|section| section.mf == 2 && section.mt == 151)
                .expect("find MF=2/MT=151");
            let evaluation = resonance::parse_mf2(section).expect("parse MF=2/MT=151");
            let groups = groups::GroupStructure::fispact_709().expect("709 groups");
            let lines = processing::ultra_narrow_certificates(
                &evaluation,
                &groups,
                mt,
                temperature,
            )
            .expect("classify ultra-narrow lines");
            for line in &lines {
                println!(
                    "U {} {:.17e} {:.17e} {:.17e} {:.17e} {:.17e} {:.17e} {} {} {:.17e} {:.17e}",
                    line.isotope_zai,
                    line.energy_ev,
                    line.total_width_ev,
                    line.doppler_width_ev,
                    line.width_to_doppler_ratio,
                    line.direct_area_barn_ev,
                    line.closed_form_area_barn_ev,
                    line.affected_group,
                    usize::from(line.range_edge_decomposition),
                    line.core_low_ev,
                    line.core_high_ev
                );
            }
            println!("N {}", lines.len());
            for candidate in processing::ultra_narrow_diagnostics(
                &evaluation,
                &groups,
                mt,
                temperature,
            )
            .expect("diagnose ultra-narrow candidates")
            {
                println!(
                    "D {:.17e} {:.17e} {:.17e} {:.17e} {} {:.17e} {}",
                    candidate.energy_ev,
                    candidate.width_to_doppler_ratio,
                    candidate.edge_distance_widths,
                    candidate.nearest_line_distance_widths,
                    usize::from(candidate.treated),
                    candidate.area_relative_difference.unwrap_or(f64::NAN),
                    candidate.reason
                );
            }
        }
        "library" => {
            // Write rows and sig as raw little-endian bytes so the control can test byte identity with numpy.
            // (A checksum cannot: floating-point addition is not associative, so summation order alone moves the last bit.)
            use std::io::Write;
            let l = library::read_npz(&a[2]).expect("read library");
            let out = &a[3];
            let mut fr =
                std::io::BufWriter::new(std::fs::File::create(format!("{out}.rows")).unwrap());
            for r in &l.rows {
                for v in [
                    r.target as i64,
                    r.mt as i64,
                    r.zap as i64,
                    r.lfs as i64,
                    r.lmf as i64,
                ] {
                    fr.write_all(&v.to_le_bytes()).unwrap();
                }
            }
            fr.flush().unwrap();
            let mut fs =
                std::io::BufWriter::new(std::fs::File::create(format!("{out}.sig")).unwrap());
            for v in &l.sig {
                fs.write_all(&v.to_le_bytes()).unwrap();
            }
            fs.flush().unwrap();
            println!("{} {}", l.rows.len(), l.ngroups);
        }
        "library-target-compare" => {
            let old_target: usize = a[3].parse().expect("old target index");
            let new_target: usize = a[5].parse().expect("new target index");
            let maximum_energy: f64 = a
                .get(6)
                .map(|value| value.parse().expect("maximum comparison energy"))
                .unwrap_or(f64::INFINITY);
            let old = library::read_npz_target(&a[2], old_target).expect("stream old target");
            let new = library::read_npz_target(&a[4], new_target).expect("stream new target");
            assert_eq!(old.ngroups, new.ngroups, "group count");
            assert_eq!(old.bounds, new.bounds, "group boundaries");
            let key = |row: &library::Row| (row.mt, row.zap, row.lfs, row.lmf);
            let old_rows: std::collections::BTreeMap<_, _> = old
                .rows
                .iter()
                .enumerate()
                .map(|(index, row)| (key(row), index))
                .collect();
            let new_rows: std::collections::BTreeMap<_, _> = new
                .rows
                .iter()
                .enumerate()
                .map(|(index, row)| (key(row), index))
                .collect();
            let old_only: Vec<_> = old_rows
                .keys()
                .filter(|key| !new_rows.contains_key(key))
                .collect();
            let new_only: Vec<_> = new_rows
                .keys()
                .filter(|key| !old_rows.contains_key(key))
                .collect();
            let mut maximum_absolute = 0.0f64;
            let mut maximum_relative = 0.0f64;
            let mut worst = None;
            let mut compared = 0usize;
            let mut negative_old = 0usize;
            let mut minimum_old = 0.0f64;
            for (identity, &old_index) in &old_rows {
                let Some(&new_index) = new_rows.get(identity) else {
                    continue;
                };
                for (group, (&left, &right)) in old
                    .sigma(old_index)
                    .iter()
                    .zip(new.sigma(new_index))
                    .enumerate()
                {
                    if old.bounds[group + 1] > maximum_energy {
                        continue;
                    }
                    if left < 0.0 {
                        negative_old += 1;
                        minimum_old = minimum_old.min(left);
                    }
                    let absolute = (left - right).abs();
                    maximum_absolute = maximum_absolute.max(absolute);
                    if left.abs().max(right.abs()) >= 1e-12 {
                        compared += 1;
                        let relative = absolute / left.abs().max(right.abs());
                        if relative > maximum_relative {
                            maximum_relative = relative;
                            worst = Some((identity, group, left, right));
                        }
                    }
                }
            }
            println!(
                "old_rows={} new_rows={} common={} old_only={} new_only={} compared={} max_abs={maximum_absolute:.17e} max_rel={maximum_relative:.17e} negative_old={} min_old={minimum_old:.17e}",
                old.rows.len(),
                new.rows.len(),
                old_rows.len() - old_only.len(),
                old_only.len(),
                new_only.len(),
                compared,
                negative_old
            );
            println!("worst={worst:?}");
            println!("old_only={:?}", &old_only[..old_only.len().min(20)]);
            println!("new_only={:?}", &new_only[..new_only.len().min(20)]);
        }
        "composition" => {
            // dump composition '{"Fe":63.72,"Cr":18.28}' -> "ZA LISO atoms_per_g" per isotope, then the diagnostics
            let spec = &a[2];
            let mut el = std::collections::BTreeMap::new();
            for part in spec.trim_matches(|c| c == '{' || c == '}').split(',') {
                let mut kv = part.splitn(2, ':');
                let k = kv.next().unwrap_or("").trim().trim_matches('"').to_string();
                let v: f64 = kv.next().unwrap_or("0").trim().parse().unwrap_or(0.0);
                if !k.is_empty() {
                    el.insert(k, v);
                }
            }
            let (inv, diag) = composition::atoms_per_gram(&el);
            println!("{}", inv.len());
            for ((za, liso), n) in &inv {
                println!("{} {} {:.17e}", za, liso, n);
            }
            for (e, (molar, apg, niso)) in &diag.elements {
                println!("# {} {:.17e} {:.17e} {}", e, molar, apg, niso);
            }
            for u in &diag.unknown {
                println!("# UNKNOWN {}", u);
            }
        }
        "material" => {
            // dump material DECAY BASIS KEY VALUE [KEY VALUE ...]
            // Uses the complete mixed natural-element/explicit-nuclide path exercised by the solver.
            assert!(a.len() >= 6 && a.len().is_multiple_of(2));
            let nuclides = decay::parse_file(&a[2]).expect("read decay file");
            let mut values = std::collections::BTreeMap::new();
            let (pairs, remainder) = a[4..].as_chunks::<2>();
            assert!(remainder.is_empty());
            for pair in pairs {
                values.insert(
                    pair[0].clone(),
                    pair[1].parse::<f64>().expect("material value"),
                );
            }
            let (inventory, diagnostic) =
                composition::material_atoms_per_gram(&values, &a[3], &nuclides)
                    .expect("convert material");
            println!("{}", inventory.len());
            for ((za, liso), atoms) in inventory {
                println!("I {za} {liso} {atoms:.17e}");
            }
            for (name, (za, liso, molar_mass, atoms)) in diagnostic.explicit_nuclides {
                println!("N {name} {za} {liso} {molar_mass:.17e} {atoms:.17e}");
            }
            for (element, (molar_mass, atoms, isotopes)) in diagnostic.elements {
                println!("E {element} {molar_mass:.17e} {atoms:.17e} {isotopes}");
            }
            for unknown in diagnostic.unknown {
                println!("U {unknown}");
            }
        }
        "provenance" => println!("{}", composition::provenance()),
        _ => eprintln!(
            "usage: dump decay|spectra|spectra-summary|fission-yields|fission-effective|library|composition|material|provenance ..."
        ),
    }
}
