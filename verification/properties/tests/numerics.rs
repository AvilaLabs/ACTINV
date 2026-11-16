use actinv_core::{
    cram::{self, Cram},
    cram_coeffs::*,
    sparse::{lu, Csc},
    spec::Spec,
};
use actinv_data::library::{Library, Row};
use num_complex::Complex64 as C64;
use proptest::prelude::*;

fn coefficients() -> Cram {
    Cram {
        alpha0: CRAM48_ALPHA0,
        theta: CRAM48_THETA.iter().map(|&(r, i)| C64::new(r, i)).collect(),
        alpha: CRAM48_ALPHA.iter().map(|&(r, i)| C64::new(r, i)).collect(),
    }
}

fn close(got: f64, want: f64, scale: f64) -> bool {
    got.is_finite() && (got - want).abs() <= 5e-12 * scale.max(1.0)
}

proptest! {
    #[test]
    fn declared_total_normalizes_equal_shapes_across_all_positive_binary64_magnitudes(
        bits in 1u64..0x7ff0_0000_0000_0000u64,
        groups in 2usize..17,
        total_exponent in -900i32..901,
        first_zero in any::<bool>(),
    ) {
        let weight = f64::from_bits(bits);
        let total = 2.0f64.powi(total_exponent);
        let mut spec = Spec::from_json(include_str!("../../../examples/fns_fe_5min.json")).unwrap();
        let mut weights = vec![weight;groups];
        if first_zero {weights[0] = 0.0;}
        spec.spectrum.structure = "custom".into();
        spec.spectrum.boundaries_eV = Some((0..=groups).map(|i| i as f64+1.0).collect());
        spec.spectrum.flux_per_group = weights;
        spec.spectrum.total = Some(total);
        spec.spectrum.descending = false;
        spec.spectrum.relative_error = None;
        spec.spectrum.rebin = None;
        spec.spectrum.wall_loading_MW_m2 = None;
        spec.validate().unwrap();
        let actual = spec.spectrum.ascending_flux();
        let expected = total / (groups-usize::from(first_zero)) as f64;
        prop_assert_eq!(actual.len(),groups);
        for (index,value) in actual.iter().enumerate() {
            prop_assert!(value.is_finite() && *value >= 0.0);
            if first_zero && index == 0 {prop_assert_eq!(*value,0.0);}
            else {prop_assert!((*value/expected-1.0).abs() <= 32.0*f64::EPSILON);}
        }
        prop_assert!((actual.iter().sum::<f64>()/total-1.0).abs() <= 32.0*f64::EPSILON);
    }

    #[test]
    fn row_permuted_scaled_lu_recovers_an_independently_formed_solution(
        n in 2usize..7,
        rotation in 1usize..6,
        exponent in -400i32..401,
        raw in prop::collection::vec((-4i32..5,-4i32..5),36),
        solution in prop::collection::vec((-10i32..11,-10i32..11),6),
    ) {
        // A row permutation and uniform binary scaling preserve conditioning.
        // The dominant diagonal moves off-diagonal, forcing row pivoting.
        let rotation = 1 + (rotation-1) % (n-1);
        let scale = 2.0f64.powi(exponent);
        let mut dense = vec![vec![C64::new(0.0,0.0);n];n];
        let mut triplets = Vec::new();
        for row in 0..n {
            let permuted = (row+rotation)%n;
            for (column,cell) in dense[permuted].iter_mut().enumerate() {
                let (re,im) = raw[row*6+column];
                let value = if row == column {C64::new(64.0,0.0)}
                    else {C64::new(f64::from(re),f64::from(im))};
                *cell = value*scale;
                triplets.push((permuted,column,value*scale));
            }
        }
        let expected: Vec<_> = solution[..n].iter()
            .map(|&(re,im)| C64::new(f64::from(re),f64::from(im))).collect();
        let rhs: Vec<_> = dense.iter().map(|row| row.iter().zip(&expected).map(|(a,x)| a*x).sum()).collect();
        let matrix = Csc::from_triplets(n,&triplets);
        let factor = lu(&matrix).unwrap();
        prop_assert_ne!(factor.pinv[0],0);
        let actual = factor.solve_refined(&matrix,&rhs).unwrap();
        prop_assert_eq!(actual.len(),n);
        for (actual,expected) in actual.iter().zip(expected) {
            prop_assert!((*actual-expected).norm() <= 2e-12*(1.0+expected.norm()));
        }
    }

    #[test]
    fn branched_decay_scalar_multi_and_log_rate_tangent_match_analytic_oracles(
        log_rate in -10i32..7,
        exposure in 0.0f64..100.0,
        parent in 1.0f64..1e6,
        daughter1 in 0.0f64..1e6,
        daughter2 in 0.0f64..1e6,
        branch in 0u16..1001,
        rotation in 0usize..3,
        reversed in any::<bool>(),
    ) {
        let mut order = [0usize,1,2];
        order.rotate_left(rotation);
        if reversed {order.swap(0,1);}
        let [p,d1,d2] = order;
        let rate = 10.0f64.powi(log_rate);
        let split = f64::from(branch)/1000.0;
        let dt = exposure/rate;
        let matrix = Csc::from_triplets(3,&[
            (p,p,C64::new(-rate,0.0)),
            (d1,p,C64::new(rate*split,0.0)),
            (d2,p,C64::new(rate*(1.0-split),0.0)),
        ]);
        let mut initial = vec![0.0;3];
        initial[p] = parent; initial[d1] = daughter1; initial[d2] = daughter2;
        let mut other = initial.clone();
        other[p] *= 0.5; other[d1] += 7.0; other[d2] += 9.0;
        let c = coefficients();
        let scalar = cram::step(&matrix,&initial,dt,&c).unwrap().0;
        let multi = cram::step_multi(&matrix,&[initial.clone(),other.clone()],dt,&c).unwrap();
        let tangent = cram::step_with_tangents(&matrix,&initial,&[vec![0.0;3]],
            std::slice::from_ref(&matrix),&[1.0],dt,&c).unwrap();
        prop_assert_eq!(multi.len(),2);
        prop_assert_eq!(tangent.tangents.len(),1);
        prop_assert_eq!(tangent.tangents[0].len(),3);
        for (state,source) in [(&scalar,&initial),(&multi[0],&initial),(&multi[1],&other),(&tangent.state,&initial)] {
            let mut expected = source.to_vec();
            let produced = source[p] * -(-exposure).exp_m1();
            expected[p] = source[p]*(-exposure).exp();
            expected[d1] += split*produced;
            expected[d2] += (1.0-split)*produced;
            let scale: f64 = source.iter().sum();
            prop_assert_eq!(state.len(),3);
            for (actual,expected) in state.iter().zip(expected) {prop_assert!(close(*actual,expected,scale));}
            prop_assert!(close(state.iter().sum(),scale,scale));
        }
        // For p=ln(rate), d(parent)/dp=-exposure*parent*exp(-exposure).
        let transferred = exposure*parent*(-exposure).exp();
        let mut expected = [0.0;3];
        expected[p] = -transferred; expected[d1] = split*transferred; expected[d2] = (1.0-split)*transferred;
        let scale = initial.iter().sum();
        for (actual,expected) in tangent.tangents[0].iter().zip(expected) {
            prop_assert!(close(*actual,expected,scale));
        }
    }

    #[test]
    fn closed_decay_matches_analytic_solution_and_conserves_atoms(
        log_rate in -10i32..7,
        exposure in 0.0f64..100.0,
        initial in 1.0f64..1e6,
        daughter in 0.0f64..1e6,
        fraction in 0.0f64..1.0,
        multiplier in 0.125f64..8.0,
    ) {
        let rate = 10.0f64.powi(log_rate);
        let dt = exposure / rate;
        let a = Csc::from_triplets(2, &[(0,0,C64::new(-rate,0.0)),(1,0,C64::new(rate,0.0))]);
        let c = coefficients();
        let n0 = [initial,daughter];
        let scale = initial+daughter;
        let full = cram::step(&a,&n0,dt,&c).unwrap().0;
        let remaining = initial * (-exposure).exp();
        // expm1 avoids cancellation in the independent analytic daughter reference.
        let produced = initial * -(-exposure).exp_m1();
        prop_assert!(close(full[0],remaining,scale));
        prop_assert!(close(full[1],daughter+produced,scale));
        prop_assert!(close(full.iter().sum(),scale,scale));
        prop_assert!(full.iter().all(|&x| x >= -5e-12*scale));
        let first = cram::step(&a,&n0,dt*fraction,&c).unwrap().0;
        let split = cram::step(&a,&first,dt*(1.0-fraction),&c).unwrap().0;
        let scaled = cram::step(&a,&[initial*multiplier,daughter*multiplier],dt,&c).unwrap().0;
        for i in 0..2 {
            prop_assert!(close(split[i],full[i],scale));
            prop_assert!(close(scaled[i],full[i]*multiplier,scale*multiplier));
        }
    }

    #[test]
    fn flux_collapse_is_weighted_average_and_invariant_to_normalization(
        values in prop::collection::vec((0u16..1000, 0u16..1000),1..33),
        factor in 1u16..100,
    ) {
        let sigma: Vec<_> = values.iter().map(|&(s,_)| f64::from(s)).collect();
        let phi: Vec<_> = values.iter().map(|&(_,f)| f64::from(f)).collect();
        let library = Library {
            rows: vec![Row {target:0,mt:102,zap:0,lfs:0,lmf:3}],
            sig: sigma.clone(), ngroups: values.len(),
            bounds: (0..=values.len()).map(|i| i as f64+1.0).collect(),
        };
        library.validate().unwrap();
        let denominator: u64 = values.iter().map(|&(_,f)| u64::from(f)).sum();
        let numerator: u64 = values.iter().map(|&(s,f)| u64::from(s)*u64::from(f)).sum();
        let expected = if denominator == 0 {0.0} else {numerator as f64 / denominator as f64};
        let collapsed = library.one_group(0,&phi);
        prop_assert!(close(collapsed,expected,1000.0));
        let scaled: Vec<_> = phi.iter().map(|x| x*f64::from(factor)).collect();
        prop_assert!(close(library.one_group(0,&scaled),expected,1000.0));
    }
}
