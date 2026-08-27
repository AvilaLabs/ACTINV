//! P5-G1 helper: dump what the Rust readers see, for comparison against the Python implementations.
//!   dump decay FILE          -> "ZA LISO NST half_life e_light e_em e_heavy nmodes br0 q0 ..." per nuclide
//!   dump spectra FILE [ZA:LISO ...] -> lossless line-oriented MF=8/MT=457 spectrum records
//!   dump spectra-summary FILE -> all-file section/spectrum and STYP/LCON counts
//!   dump activation FILE -> strict MF=3/6/8/9/10 evaluation summary
//!   dump activation-json FILE -> canonical JSON for every retained activation/resonance field
//!   dump activation-product FILE ZAP LFS ENERGY_EV [...] -> charged residual-production components
//!   dump resonance FILE -> strict MF=2/MT=151 structure summary
//!   dump resonance-rml FILE -> canonical line-oriented R-matrix-limited structure
//!   dump resonance-xs FILE ENERGY_EV [...] -> zero-K resonance-only cross sections
//!   dump unresolved-probe CASE E D GX GN GG GF MUX MUN MUF LSSF -> synthetic G3 control
//!   dump doppler-probe TEMPERATURE_K -> synthetic P10 G4 SIGMA1 inputs and outputs
//!   dump processed-kernel FILE MT TEMPERATURES_CSV ENERGY_EV [...] -> direct SIGMA1 control
//!   dump processed-xs FILE MT TEMPERATURE_K [ENERGY_EV ...] -> processed points or 709 groups
//!   dump library FILE OUT    -> raw row and group arrays for byte comparison
//!   dump library-target FILE TARGET OUT -> bounded-memory raw target rows, groups and boundaries
//!   dump library-targets FILE TARGETS_CSV OUT -> one-pass sparse-target raw rows, groups and boundaries
//!   dump library-target-compare OLD.npz OLD_TARGET NEW.npz NEW_TARGET -> bounded-memory row/group comparison
use actinv_data::{
    activation, composition, decay, doppler, endf, fission, groups, library, processing, resonance,
};

fn doppler_probe(arguments: &[String]) {
    assert_eq!(arguments.len(), 1, "doppler-probe needs a temperature");
    let temperature: f64 = arguments[0].parse().expect("temperature K");
    assert!(
        temperature.is_finite() && temperature >= 0.0,
        "temperature must be finite and nonnegative"
    );
    let cases = [
        (
            "one_over_v",
            vec![100.0, 200.0],
            vec![1.0, 1.0 / 2.0f64.sqrt()],
            vec![0.1, 1.0, 10.0, 100.0, 200.0],
        ),
        (
            "constant",
            vec![1e-12, 1e7],
            vec![3.0, 3.0],
            vec![1e-12, 1.0, 100.0, 1e4, 1e7],
        ),
        (
            "synthetic_line",
            vec![1e-4, 90.0, 99.0, 100.0, 101.0, 110.0, 1e6],
            vec![0.01, 0.01, 1.0, 10.0, 1.0, 0.01, 0.01],
            vec![
                1e-4, 90.0, 97.0, 99.0, 99.5, 100.0, 100.5, 101.0, 103.0, 110.0, 1e6,
            ],
        ),
    ];
    for (name, energy, sigma, output) in cases {
        for (&point, &value) in energy.iter().zip(&sigma) {
            println!("I {name} {point:.17e} {value:.17e}");
        }
        let broadened = doppler::broaden(&energy, &sigma, temperature, 55.0, &output)
            .expect("broaden synthetic control");
        for (&point, &value) in output.iter().zip(&broadened) {
            println!("O {name} {point:.17e} {value:.17e}");
        }
    }
}

fn processed_file_reaction(
    path: &str,
    mt: i32,
    temperature: f64,
) -> (
    resonance::ResonanceEvaluation,
    groups::GroupStructure,
    processing::ProcessedReaction,
) {
    let text = std::fs::read_to_string(path).expect("read resonance file");
    let sections = endf::parse_sections(&text).expect("parse ENDF sections");
    let resonance_section = sections
        .iter()
        .find(|section| section.mf == 2 && section.mt == 151)
        .expect("find MF=2/MT=151");
    let evaluation = resonance::parse_mf2(resonance_section).expect("parse MF=2/MT=151");
    let mf3_section = sections
        .iter()
        .find(|section| section.mf == 3 && section.mt == mt)
        .expect("find MF=3 reaction");
    let (record, next) = endf::read_tab1_checked(&mf3_section.lines, 1).expect("parse MF=3 TAB1");
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
    (evaluation, group_structure, processed)
}

fn processed_kernel(arguments: &[String]) {
    assert!(
        arguments.len() >= 4,
        "processed-kernel needs FILE MT TEMPERATURES_CSV and at least one energy"
    );
    let mt: i32 = arguments[1].parse().expect("MT");
    let temperatures: Vec<f64> = arguments[2]
        .split(',')
        .map(|value| value.parse().expect("temperature K"))
        .collect();
    assert!(
        temperatures
            .iter()
            .all(|value| value.is_finite() && *value >= 0.0),
        "temperatures must be finite and nonnegative"
    );
    let output: Vec<f64> = arguments[3..]
        .iter()
        .map(|value| value.parse().expect("energy in eV"))
        .collect();
    let (evaluation, _, zero) = processed_file_reaction(&arguments[0], mt, 0.0);
    assert!(
        zero.certificate.ultra_narrow_lines.is_empty(),
        "processed-kernel requires a reaction with no analytic delta lines"
    );
    println!(
        "C {:.17e} {} {}",
        evaluation.awr,
        zero.table.x.len(),
        zero.certificate.zero_k_refinement_passes
    );
    for (&energy, &sigma) in zero.table.x.iter().zip(&zero.table.y) {
        println!("I {energy:.17e} {sigma:.17e}");
    }
    for temperature in temperatures {
        let broadened = doppler::broaden(
            &zero.table.x,
            &zero.table.y,
            temperature,
            evaluation.awr,
            &output,
        )
        .expect("broaden processed zero-K control table");
        for (&energy, &sigma) in output.iter().zip(&broadened) {
            println!("O {temperature:.17e} {energy:.17e} {sigma:.17e}");
        }
    }
}

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

fn activation_product(arguments: &[String]) {
    assert!(
        arguments.len() >= 4,
        "activation-product needs FILE ZAP LFS and at least one energy"
    );
    let evaluations =
        activation::parse_file(&arguments[0], None).expect("read activation evaluation");
    assert_eq!(
        evaluations.len(),
        1,
        "activation-product requires exactly one material"
    );
    let evaluation = &evaluations[0];
    let zap: i32 = arguments[1].parse().expect("product ZAP");
    let lfs: i32 = arguments[2].parse().expect("product LFS");
    println!(
        "E {} {} {} {}",
        evaluation.metadata.projectile.name(),
        evaluation.metadata.za,
        zap,
        lfs
    );
    let mt_products: serde_json::Value =
        serde_json::from_str(include_str!("../../../../data/mt_products.json"))
            .expect("parse MT product table");

    for value in &arguments[3..] {
        let energy: f64 = value.parse().expect("incident energy in eV");
        assert!(
            energy.is_finite() && energy >= 0.0,
            "incident energy must be finite and nonnegative"
        );
        let mut mf3 = 0.0;
        let mut mf9 = 0.0;
        let mut mf10 = 0.0;
        let mut mf6 = 0.0;

        for (&mt, descriptors) in &evaluation.mf8 {
            for descriptor in descriptors
                .iter()
                .filter(|product| product.zap == zap && product.lfs == lfs)
            {
                if descriptor.lmf == 3 {
                    let reaction = evaluation
                        .mf3
                        .get(&mt)
                        .expect("MF=8/LMF=3 descriptor needs MF=3");
                    mf3 += reaction.evaluate(energy).expect("evaluate MF=3 product");
                }
            }
        }
        for (&mt, products) in &evaluation.mf9 {
            let reaction = evaluation.mf3.get(&mt).expect("MF=9 product needs MF=3");
            let reaction_value = reaction.evaluate(energy).expect("evaluate MF=3 reaction");
            for product in products
                .iter()
                .filter(|product| product.zap == zap && product.lfs == lfs)
            {
                mf9 +=
                    reaction_value * product.table.evaluate(energy).expect("evaluate MF=9 yield");
            }
        }
        for products in evaluation.mf10.values() {
            for product in products
                .iter()
                .filter(|product| product.zap == zap && product.lfs == lfs)
            {
                mf10 += product
                    .table
                    .evaluate(energy)
                    .expect("evaluate MF=10 product");
            }
        }

        if let (Some(reaction), Some(yields), Some(descriptors)) = (
            evaluation.mf3.get(&5),
            evaluation.mf6.get(&5),
            evaluation.mf8.get(&5),
        ) {
            let reaction_value = reaction.evaluate(energy).expect("evaluate MF=3/MT=5");
            let mut used = vec![false; yields.len()];
            for descriptor in descriptors.iter().filter(|product| product.lmf == 6) {
                let index = yields
                    .iter()
                    .enumerate()
                    .position(|(index, product)| !used[index] && product.zap == descriptor.zap)
                    .expect("match MF=8/LMF=6 descriptor to MF=6 yield");
                used[index] = true;
                if descriptor.zap == zap && descriptor.lfs == lfs {
                    mf6 += reaction_value
                        * yields[index]
                            .yield_table
                            .evaluate(energy)
                            .expect("evaluate MF=6 yield");
                }
            }
        }

        for (&mt, reaction) in &evaluation.mf3 {
            let skipped = matches!(mt, 1 | 2 | 3 | 5 | 19 | 20 | 21 | 27 | 38 | 101 | 444)
                || mt == 4
                || (51..=91).contains(&mt)
                || (201..=207).contains(&mt)
                || (600..=849).contains(&mt)
                || mt >= 1000;
            let has_explicit_product = evaluation
                .mf8
                .get(&mt)
                .is_some_and(|value| !value.is_empty())
                || evaluation
                    .mf9
                    .get(&mt)
                    .is_some_and(|value| !value.is_empty())
                || evaluation
                    .mf10
                    .get(&mt)
                    .is_some_and(|value| !value.is_empty());
            if skipped || has_explicit_product || lfs != 0 {
                continue;
            }
            let Some(delta) = mt_products["table"].get(mt.to_string()) else {
                continue;
            };
            let delta = delta.as_array().expect("MT product offset pair");
            let delta_z = delta[0].as_i64().expect("MT product Z offset") as i32;
            let delta_a = delta[1].as_i64().expect("MT product A offset") as i32;
            let target = (evaluation.metadata.za / 1000, evaluation.metadata.za % 1000);
            let incident = evaluation.metadata.projectile.za();
            let product_z = target.0 + delta_z + incident.0;
            let product_a = target.1 + delta_a + incident.1 - 1;
            if product_z > 0
                && product_a > 0
                && product_a >= product_z
                && product_z * 1000 + product_a == zap
            {
                mf3 += reaction
                    .evaluate(energy)
                    .expect("evaluate inferred MF=3 product");
            }
        }

        let total = mf3 + mf9 + mf10 + mf6;
        println!("P {energy:.17e} {mf3:.17e} {mf9:.17e} {mf10:.17e} {mf6:.17e} {total:.17e}");
    }
}

fn resonance_rml(arguments: &[String]) {
    assert_eq!(arguments.len(), 1, "resonance-rml needs FILE");
    let text = std::fs::read_to_string(&arguments[0]).expect("read resonance file");
    let sections = endf::parse_sections(&text).expect("parse ENDF sections");
    let mut ranges = 0usize;
    for section in sections
        .iter()
        .filter(|section| section.mf == 2 && section.mt == 151)
    {
        let evaluation = resonance::parse_mf2(section).expect("parse MF=2/MT=151");
        for (isotope_index, isotope) in evaluation.isotopes.iter().enumerate() {
            for (range_index, range) in isotope.ranges.iter().enumerate() {
                let resonance::RangeData::RMatrixLimited(data) = &range.data else {
                    continue;
                };
                println!(
                    "M {isotope_index} {range_index} {:.17e} {:.17e} {} {} {} {} {} {} {} {}",
                    range.energy_min,
                    range.energy_max,
                    range.lru,
                    range.lrf,
                    range.naps,
                    usize::from(range.scattering_radius.is_some()),
                    usize::from(data.reduced_widths),
                    data.krm,
                    data.particle_pairs.len(),
                    data.spin_groups.len()
                );
                for (pair_index, pair) in data.particle_pairs.iter().enumerate() {
                    println!(
                        "P {isotope_index} {range_index} {pair_index} {:.17e} {:.17e} {} {} {:.17e} {:.17e} {:.17e} {} {} {} {} {}",
                        pair.mass_a,
                        pair.mass_b,
                        pair.za,
                        pair.zb,
                        pair.spin_a,
                        pair.spin_b,
                        pair.q_value,
                        pair.penetrability,
                        pair.shift,
                        pair.mt,
                        pair.parity_a,
                        pair.parity_b
                    );
                }
                for (group_index, group) in data.spin_groups.iter().enumerate() {
                    println!(
                        "G {isotope_index} {range_index} {group_index} {:.17e} {:.17e} {} {} {} {}",
                        group.spin,
                        group.parity,
                        group.channels.len(),
                        group.resonances.len(),
                        group.backgrounds.len(),
                        group.phase_shifts.len()
                    );
                    for (channel_index, channel) in group.channels.iter().enumerate() {
                        println!(
                            "C {isotope_index} {range_index} {group_index} {channel_index} {} {} {:.17e} {:.17e} {:.17e} {:.17e}",
                            channel.pair,
                            channel.l,
                            channel.spin,
                            channel.boundary,
                            channel.effective_radius,
                            channel.true_radius
                        );
                    }
                    for (resonance_index, value) in group.resonances.iter().enumerate() {
                        print!(
                            "V {isotope_index} {range_index} {group_index} {resonance_index} {:.17e} {}",
                            value.energy,
                            value.widths.len()
                        );
                        for width in &value.widths {
                            print!(" {width:.17e}");
                        }
                        println!();
                    }
                    for (extension_index, extension) in group.backgrounds.iter().enumerate() {
                        println!(
                            "B {isotope_index} {range_index} {group_index} {extension_index} {} {} {} {} {}",
                            extension.channel,
                            extension.law,
                            extension.real.as_ref().map_or(0, |table| table.x.len()),
                            extension.imaginary.as_ref().map_or(0, |table| table.x.len()),
                            extension.parameters.len()
                        );
                    }
                    for (extension_index, extension) in group.phase_shifts.iter().enumerate() {
                        println!(
                            "S {isotope_index} {range_index} {group_index} {extension_index} {} {} {} {} {}",
                            extension.channel,
                            extension.law,
                            extension.real.as_ref().map_or(0, |table| table.x.len()),
                            extension.imaginary.as_ref().map_or(0, |table| table.x.len()),
                            extension.parameters.len()
                        );
                    }
                }
                ranges += 1;
            }
        }
    }
    println!("N {ranges}");
}

fn tabulated_json(table: &groups::Tabulated) -> serde_json::Value {
    serde_json::json!({
        "interpolation": &table.interpolation,
        "x": &table.x,
        "y": &table.y,
    })
}

fn legacy_resolved_json(data: &resonance::LegacyResolved) -> serde_json::Value {
    serde_json::json!({
        "spin": data.spin,
        "ap": data.ap,
        "groups": data.groups.iter().map(|group| serde_json::json!({
            "awri": group.awri,
            "apl": group.apl,
            "qx": group.qx,
            "l": group.l,
            "lrx": group.lrx,
            "resonances": group.resonances.iter().map(|value| serde_json::json!({
                "energy": value.energy,
                "spin": value.spin,
                "total": value.total,
                "neutron": value.neutron,
                "capture": value.capture,
                "fission_a": value.fission_a,
                "fission_b": value.fission_b,
            })).collect::<Vec<_>>(),
        })).collect::<Vec<_>>(),
    })
}

fn rml_extension_json(extension: &resonance::RmlExtension) -> serde_json::Value {
    serde_json::json!({
        "channel": extension.channel,
        "law": extension.law,
        "real": extension.real.as_ref().map(tabulated_json),
        "imaginary": extension.imaginary.as_ref().map(tabulated_json),
        "parameters": &extension.parameters,
    })
}

fn rmatrix_json(data: &resonance::RMatrixLimited) -> serde_json::Value {
    serde_json::json!({
        "reduced_widths": data.reduced_widths,
        "krm": data.krm,
        "particle_pairs": data.particle_pairs.iter().map(|pair| serde_json::json!({
            "mass_a": pair.mass_a,
            "mass_b": pair.mass_b,
            "za": pair.za,
            "zb": pair.zb,
            "spin_a": pair.spin_a,
            "spin_b": pair.spin_b,
            "q_value": pair.q_value,
            "penetrability": pair.penetrability,
            "shift": pair.shift,
            "mt": pair.mt,
            "parity_a": pair.parity_a,
            "parity_b": pair.parity_b,
        })).collect::<Vec<_>>(),
        "spin_groups": data.spin_groups.iter().map(|group| serde_json::json!({
            "spin": group.spin,
            "parity": group.parity,
            "channels": group.channels.iter().map(|channel| serde_json::json!({
                "pair": channel.pair,
                "l": channel.l,
                "spin": channel.spin,
                "boundary": channel.boundary,
                "effective_radius": channel.effective_radius,
                "true_radius": channel.true_radius,
            })).collect::<Vec<_>>(),
            "resonances": group.resonances.iter().map(|value| serde_json::json!({
                "energy": value.energy,
                "widths": &value.widths,
            })).collect::<Vec<_>>(),
            "backgrounds": group.backgrounds.iter().map(rml_extension_json).collect::<Vec<_>>(),
            "phase_shifts": group.phase_shifts.iter().map(rml_extension_json).collect::<Vec<_>>(),
        })).collect::<Vec<_>>(),
    })
}

fn unresolved_json(data: &resonance::Unresolved) -> serde_json::Value {
    let case = match data.case {
        resonance::UnresolvedCase::A => "A",
        resonance::UnresolvedCase::B => "B",
        resonance::UnresolvedCase::C => "C",
    };
    serde_json::json!({
        "spin": data.spin,
        "ap": data.ap,
        "add_to_background": data.add_to_background,
        "case": case,
        "sequences": data.sequences.iter().map(|sequence| serde_json::json!({
            "awri": sequence.awri,
            "l": sequence.l,
            "spin": sequence.spin,
            "interpolation": sequence.interpolation,
            "competitive_dof": sequence.competitive_dof,
            "neutron_dof": sequence.neutron_dof,
            "fission_dof": sequence.fission_dof,
            "points": sequence.points.iter().map(|point| serde_json::json!({
                "energy": point.energy,
                "spacing": point.spacing,
                "competitive": point.competitive,
                "neutron": point.neutron,
                "capture": point.capture,
                "fission": point.fission,
            })).collect::<Vec<_>>(),
        })).collect::<Vec<_>>(),
        "interpolation_energies": &data.interpolation_energies,
    })
}

fn resonance_json(evaluation: &resonance::ResonanceEvaluation) -> serde_json::Value {
    serde_json::json!({
        "za": evaluation.za,
        "awr": evaluation.awr,
        "isotopes": evaluation.isotopes.iter().map(|isotope| serde_json::json!({
            "zai": isotope.zai,
            "abundance": isotope.abundance,
            "fission_widths": isotope.fission_widths,
            "ranges": isotope.ranges.iter().map(|range| {
                let data = match &range.data {
                    resonance::RangeData::ScatteringOnly { spin, ap } => {
                        serde_json::json!({"ScatteringOnly": {"spin": spin, "ap": ap}})
                    }
                    resonance::RangeData::BreitWigner(data) => {
                        serde_json::json!({"BreitWigner": legacy_resolved_json(data)})
                    }
                    resonance::RangeData::ReichMoore(data) => {
                        serde_json::json!({"ReichMoore": legacy_resolved_json(data)})
                    }
                    resonance::RangeData::RMatrixLimited(data) => {
                        serde_json::json!({"RMatrixLimited": rmatrix_json(data)})
                    }
                    resonance::RangeData::Unresolved(data) => {
                        serde_json::json!({"Unresolved": unresolved_json(data)})
                    }
                };
                serde_json::json!({
                    "energy_min": range.energy_min,
                    "energy_max": range.energy_max,
                    "lru": range.lru,
                    "lrf": range.lrf,
                    "naps": range.naps,
                    "scattering_radius": range.scattering_radius.as_ref().map(tabulated_json),
                    "data": data,
                })
            }).collect::<Vec<_>>(),
        })).collect::<Vec<_>>(),
    })
}

fn product_tables_json(
    values: &std::collections::BTreeMap<i32, Vec<activation::ProductTable>>,
) -> serde_json::Value {
    serde_json::Value::Object(
        values
            .iter()
            .map(|(mt, products)| {
                (
                    mt.to_string(),
                    serde_json::Value::Array(
                        products
                            .iter()
                            .map(|product| {
                                serde_json::json!({
                                    "zap": product.zap,
                                    "lfs": product.lfs,
                                    "table": tabulated_json(&product.table),
                                })
                            })
                            .collect(),
                    ),
                )
            })
            .collect(),
    )
}

fn evaluation_json(evaluation: &activation::Evaluation) -> serde_json::Value {
    let metadata = &evaluation.metadata;
    serde_json::json!({
        "metadata": {
            "mat": metadata.mat,
            "za": metadata.za,
            "awr": metadata.awr,
            "liso": metadata.liso,
            "awi": metadata.awi,
            "nsub": metadata.nsub,
            "projectile": metadata.projectile.name(),
            "evaluation_temperature_k": metadata.evaluation_temperature_k,
        },
        "mf2_sections": evaluation.mf2_sections.iter().copied().collect::<Vec<_>>(),
        "resonance": evaluation.resonance.as_ref().map(resonance_json),
        "mf3": serde_json::Value::Object(evaluation.mf3.iter().map(|(mt, table)| {
            (mt.to_string(), tabulated_json(table))
        }).collect()),
        "mf6": serde_json::Value::Object(evaluation.mf6.iter().map(|(mt, products)| {
            (mt.to_string(), serde_json::Value::Array(products.iter().map(|product| serde_json::json!({
                "zap": product.zap,
                "awp": product.awp,
                "law": product.law,
                "yield_table": tabulated_json(&product.yield_table),
            })).collect()))
        }).collect()),
        "mf8": serde_json::Value::Object(evaluation.mf8.iter().map(|(mt, products)| {
            (mt.to_string(), serde_json::Value::Array(products.iter().map(|product| serde_json::json!({
                "zap": product.zap,
                "lfs": product.lfs,
                "lmf": product.lmf,
            })).collect()))
        }).collect()),
        "mf9": product_tables_json(&evaluation.mf9),
        "mf10": product_tables_json(&evaluation.mf10),
    })
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
        "activation-product" => activation_product(&a[2..]),
        "activation-json" => {
            let evaluations =
                activation::parse_file(&a[2], None).expect("parse activation evaluation");
            let values: Vec<_> = evaluations.iter().map(evaluation_json).collect();
            println!(
                "{}",
                serde_json::to_string(&values).expect("serialize activation evaluation")
            );
        }
        "resonance-rml" => resonance_rml(&a[2..]),
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
        "doppler-probe" => doppler_probe(&a[2..]),
        "processed-kernel" => processed_kernel(&a[2..]),
        "processed-xs" => {
            let mt: i32 = a[3].parse().expect("MT");
            let temperature: f64 = a[4].parse().expect("temperature K");
            let (_, group_structure, processed) =
                processed_file_reaction(&a[2], mt, temperature);
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
        "library-target" => {
            // Stream one target from a potentially multi-gigabyte compressed library. Scientific controls can then
            // inspect an exhaustive seeded subset without materialising the full group matrix in Python memory.
            use std::io::Write;
            let target: usize = a[3].parse().expect("target index");
            let library = library::read_npz_target(&a[2], target).expect("stream library target");
            let out = &a[4];
            let mut rows =
                std::io::BufWriter::new(std::fs::File::create(format!("{out}.rows")).unwrap());
            for row in &library.rows {
                for value in [row.mt as i64, row.zap as i64, row.lfs as i64, row.lmf as i64] {
                    rows.write_all(&value.to_le_bytes()).unwrap();
                }
            }
            rows.flush().unwrap();
            let mut sigma =
                std::io::BufWriter::new(std::fs::File::create(format!("{out}.sig")).unwrap());
            for value in &library.sig {
                sigma.write_all(&value.to_le_bytes()).unwrap();
            }
            sigma.flush().unwrap();
            let mut bounds =
                std::io::BufWriter::new(std::fs::File::create(format!("{out}.bounds")).unwrap());
            for value in &library.bounds {
                bounds.write_all(&value.to_le_bytes()).unwrap();
            }
            bounds.flush().unwrap();
            println!("{} {}", library.rows.len(), library.ngroups);
        }
        "library-targets" => {
            use std::io::Write;
            let targets: std::collections::BTreeSet<usize> = a[3]
                .split(',')
                .map(|value| value.parse().expect("target index"))
                .collect();
            assert!(!targets.is_empty(), "at least one target is required");
            let library =
                library::read_npz_targets(&a[2], &targets).expect("stream library targets");
            let out = &a[4];
            let mut rows =
                std::io::BufWriter::new(std::fs::File::create(format!("{out}.rows")).unwrap());
            for row in &library.rows {
                for value in [
                    row.target as i64,
                    row.mt as i64,
                    row.zap as i64,
                    row.lfs as i64,
                    row.lmf as i64,
                ] {
                    rows.write_all(&value.to_le_bytes()).unwrap();
                }
            }
            rows.flush().unwrap();
            let mut sigma =
                std::io::BufWriter::new(std::fs::File::create(format!("{out}.sig")).unwrap());
            for value in &library.sig {
                sigma.write_all(&value.to_le_bytes()).unwrap();
            }
            sigma.flush().unwrap();
            let mut bounds =
                std::io::BufWriter::new(std::fs::File::create(format!("{out}.bounds")).unwrap());
            for value in &library.bounds {
                bounds.write_all(&value.to_le_bytes()).unwrap();
            }
            bounds.flush().unwrap();
            println!("{} {}", library.rows.len(), library.ngroups);
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
            "usage: dump decay|spectra|spectra-summary|activation|activation-json|activation-product|fission-yields|fission-effective|resonance-rml|library|composition|material|provenance ..."
        ),
    }
}
