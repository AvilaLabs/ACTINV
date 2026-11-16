use actinv_verification_kernels::{
    endf, endf_fixtures, quantity,
    sparse::{lu, Csc},
};
use num_complex::Complex64 as C64;
use proptest::prelude::*;

proptest! {
    #[test]
    fn constructors_accept_exactly_their_domains(bits in any::<u64>()) {
        let value = f64::from_bits(bits);
        let accepted = value.is_finite() && value >= 0.0;
        prop_assert_eq!(quantity::Seconds::new(value).is_ok(), accepted);
        prop_assert_eq!(quantity::ParticleFlux::new(value).is_ok(), accepted);
        prop_assert_eq!(quantity::Grams::new(value).is_ok(), value.is_finite() && value > 0.0);
        if let Ok(quantity) = quantity::Seconds::new(value) {
            prop_assert_eq!(quantity.get().to_bits(), bits);
        }
    }

    #[test]
    fn endf_checked_fields_obey_ascii_byte_boundaries(text in ".{0,100}") {
        let fields = endf::checked_fields(&text);
        prop_assert_eq!(fields.is_ok(), text.is_ascii() && text.len() >= 66);
        if let Ok(fields) = fields {
            for (i, field) in fields.iter().enumerate() {
                prop_assert_eq!(*field, &text[i * 11..(i + 1) * 11]);
            }
        }
    }

    #[test]
    fn ascii_records_preserve_fields_and_reject_all_short_prefixes(
        bytes in prop::collection::vec(0u8..128,66),
    ) {
        let text = std::str::from_utf8(&bytes).unwrap();
        let fields = endf::checked_fields(text).unwrap();
        for (index,field) in fields.iter().enumerate() {
            prop_assert_eq!(*field,&text[index*11..(index+1)*11]);
        }
        for length in 0..66 {
            prop_assert!(endf::checked_fields(&text[..length]).is_err());
        }
    }

    #[test]
    fn sparse_triplets_and_shift_match_independent_dense_accumulation(
        n in 1usize..9,
        entries in prop::collection::vec((0usize..64, 0usize..64, -100i32..101), 0..48),
        scale in -8i32..9,
        shift in -8i32..9,
    ) {
        let mut dense = vec![vec![0.0; n]; n];
        let triplets: Vec<_> = entries.into_iter().map(|(i,j,v)| {
            dense[i % n][j % n] += f64::from(v);
            (i % n, j % n, C64::new(f64::from(v), 0.0))
        }).collect();
        let matrix = Csc::from_triplets(n, &triplets)
            .scale_shift(C64::new(f64::from(scale), 0.0), C64::new(f64::from(shift), 0.0));
        prop_assert_eq!(matrix.colptr.len(), n + 1);
        prop_assert_eq!(matrix.colptr[0], 0);
        prop_assert_eq!(matrix.colptr[n], matrix.vals.len());
        prop_assert_eq!(matrix.rowidx.len(), matrix.vals.len());
        let mut actual = vec![vec![0.0; n]; n];
        for j in 0..n {
            let range = matrix.colptr[j]..matrix.colptr[j + 1];
            prop_assert!(matrix.rowidx[range.clone()].windows(2).all(|p| p[0] < p[1]));
            for p in range {
                prop_assert!(matrix.rowidx[p] < n);
                actual[matrix.rowidx[p]][j] = matrix.vals[p].re;
                prop_assert_eq!(matrix.vals[p].im, 0.0);
            }
            for (i, row) in dense.iter_mut().enumerate() {
                row[j] = row[j] * f64::from(scale) + if i == j { f64::from(shift) } else { 0.0 };
            }
        }
        prop_assert_eq!(actual, dense);
    }

    #[test]
    fn complex_lu_recovers_known_solution(
        n in 1usize..7,
        raw in prop::collection::vec((-4i32..5, -4i32..5), 36),
        solution in prop::collection::vec((-10i32..11, -10i32..11), 6),
    ) {
        // Strict diagonal dominance guarantees nonsingularity. The oracle forms
        // b=A*x by an independent dense loop, with small exactly represented inputs.
        let mut dense = vec![vec![C64::new(0.0,0.0); n]; n];
        let mut trip = Vec::new();
        for (i,row) in dense.iter_mut().enumerate() {
            for (j,cell) in row.iter_mut().enumerate() {
                let (re,im) = raw[i*6+j];
                *cell = if i == j { C64::new(64.0,0.0) } else { C64::new(f64::from(re),f64::from(im)) };
                trip.push((i,j,*cell));
            }
        }
        let expected: Vec<_> = solution[..n].iter().map(|&(re,im)| C64::new(f64::from(re),f64::from(im))).collect();
        let rhs: Vec<_> = dense.iter().map(|row| row.iter().zip(&expected).map(|(a,x)| a*x).sum()).collect();
        let matrix = Csc::from_triplets(n,&trip);
        let solved = lu(&matrix).unwrap().solve_refined(&matrix,&rhs).unwrap();
        for (x,want) in solved.iter().zip(expected) {
            prop_assert!((*x-want).norm() <= 2e-12 * (1.0 + want.norm()));
        }
    }
}

#[test]
fn non_ascii_rejection_fixtures_have_an_accepted_full_length_control() {
    assert_eq!(endf_fixtures::VALID_RECORD.len(), 75);
    assert_eq!(
        endf::checked_tail(endf_fixtures::VALID_RECORD).unwrap(),
        (1, 1, 1)
    );
    assert!(endf::checked_fields(endf_fixtures::VALID_RECORD).is_ok());
    for (position, record) in endf_fixtures::NON_ASCII_RECORDS.into_iter().enumerate() {
        assert_eq!(record.len(), 66);
        assert_eq!(record.find('\u{a0}'), Some(position));
        assert_eq!(record.chars().filter(|&c| c == '\u{a0}').count(), 1);
        assert!(endf::checked_fields(record).is_err());
    }
    for (position, record) in endf_fixtures::NON_ASCII_TAIL_RECORDS
        .into_iter()
        .enumerate()
    {
        assert_eq!(record.len(), 75);
        assert_eq!(record.find('\u{a0}'), Some(position));
        assert_eq!(&record[66..], &endf_fixtures::VALID_RECORD[66..]);
        assert!(endf::checked_tail(record).is_err());
    }
}
