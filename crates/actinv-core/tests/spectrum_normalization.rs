use actinv_core::spec::Spec;

fn normalized(weights: Vec<f64>, total: f64) -> Vec<f64> {
    let mut spec = Spec::from_json(include_str!("../../../examples/fns_fe_5min.json")).unwrap();
    spec.spectrum.structure = "custom".into();
    spec.spectrum.boundaries_eV = Some((0..=weights.len()).map(|i| i as f64 + 1.0).collect());
    spec.spectrum.flux_per_group = weights;
    spec.spectrum.total = Some(total);
    spec.spectrum.descending = false;
    spec.spectrum.relative_error = None;
    spec.spectrum.rebin = None;
    spec.spectrum.wall_loading_MW_m2 = None;
    spec.validate().unwrap();
    spec.spectrum.ascending_flux()
}

#[test]
fn finite_shape_extremes_preserve_the_declared_total_and_ratios() {
    // Independent equal-weight and half-weight oracles, including a trace that
    // remains representable. No library or activation calculation is involved.
    let cases = [
        (vec![1e308, 1e308], 2.0, vec![1.0, 1.0]),
        (vec![1e-320, 1e-320], 2.0, vec![1.0, 1.0]),
        (vec![1e308, 1e308], 1e-300, vec![5e-301, 5e-301]),
        (vec![3e307, 3e307], 5e-16, vec![2.5e-16, 2.5e-16]),
        (vec![1e-320, 1e-320], 1e308, vec![5e307, 5e307]),
        (vec![0.0, 1e308, 1e308], 2.0, vec![0.0, 1.0, 1.0]),
        (
            vec![1e308, 1e308, 1e-308],
            1e308,
            vec![5e307, 5e307, 5e-309],
        ),
        (vec![1e308; 709], 709.0, vec![1.0; 709]),
    ];
    for (weights, total, expected) in cases {
        let actual = normalized(weights, total);
        assert_eq!(actual.len(), expected.len());
        for (actual, expected) in actual.iter().zip(expected) {
            assert!(actual.is_finite() && *actual >= 0.0);
            assert!(
                (*actual - expected).abs() <= 32.0 * f64::EPSILON * expected.abs(),
                "{actual} != {expected} with total {total}"
            );
        }
        assert!((actual.iter().sum::<f64>() / total - 1.0).abs() <= 32.0 * f64::EPSILON);
    }
}

#[test]
fn ordinary_normalization_keeps_legacy_bits_and_zero_total_stays_zero() {
    // This control is deliberately the legacy arithmetic: it checks binary
    // compatibility, separately from the independent accuracy cases above.
    for (weights, total) in [
        (vec![0.1, 0.2, 0.3], 7.25),
        (vec![1e-10, 4e7, 7e9], 1e14),
        (vec![0.0, 1e7, 1e14], 1e14),
    ] {
        let factor = total / weights.iter().sum::<f64>();
        let expected: Vec<_> = weights.iter().map(|v| (v * factor).to_bits()).collect();
        let actual = normalized(weights, total);
        assert_eq!(
            actual.iter().map(|v| v.to_bits()).collect::<Vec<_>>(),
            expected
        );
    }
    assert_eq!(normalized(vec![1e308, 1e308], 0.0), vec![0.0, 0.0]);
}
