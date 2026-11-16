//! Bounds and assumptions are part of each claim. Do not disable unwinding checks.
use crate::{endf, endf_fixtures::*, quantity, sparse::Csc};
use num_complex::Complex64 as C64;

#[kani::proof]
fn quantity_constructors_cover_every_binary64_encoding() {
    let x = f64::from_bits(kani::any());
    let nonnegative = x.is_finite() && x >= 0.0;
    macro_rules! constructor {
        ($ty:ty,$domain:expr) => {{
            let result = <$ty>::new(x);
            assert_eq!(result.is_ok(), $domain);
            if let Ok(value) = result {
                assert_eq!(value.get().to_bits(), x.to_bits());
            }
        }};
    }
    constructor!(quantity::Seconds, nonnegative);
    constructor!(quantity::ElectronVolts, nonnegative);
    constructor!(quantity::Kelvin, nonnegative);
    constructor!(quantity::AtomsPerGram, nonnegative);
    constructor!(quantity::ParticleFlux, nonnegative);
    constructor!(quantity::FluxMultiplier, nonnegative);
    constructor!(quantity::CrossSectionBarns, nonnegative);
    constructor!(quantity::Grams, x.is_finite() && x > 0.0);
}

#[kani::proof]
fn flux_to_rate_conversion_preserves_finite_nonnegative_domain() {
    let x = f64::from_bits(kani::any());
    if let Ok(flux) = quantity::ParticleFlux::new(x) {
        let converted = quantity::RatePerBarnSecond::from_particle_flux(flux).get();
        assert!(converted.is_finite());
        assert!(converted >= 0.0);
    }
}

#[kani::proof]
#[kani::unwind(12)]
fn endf_full_ascii_record_preserves_field_boundaries() {
    let ascii = ascii_record();
    let text = ascii.as_str();
    let fields = endf::checked_fields(text).unwrap();
    for (i, field) in fields.iter().enumerate() {
        let expected = &text[i * 11..(i + 1) * 11];
        assert_eq!(field.len(), expected.len());
        assert!(std::ptr::eq(field.as_ptr(), expected.as_ptr()));
    }
}

fn ascii_record() -> [std::ascii::Char; 66] {
    // Six fixed-size fields cover every 66-byte ASCII record, including NULs.
    // The safe ASCII view avoids general UTF-8 decoding. Input-generation
    // loops need at most 11 iterations, keeping library unwind bounds small.
    let mut ascii = [std::ascii::Char::from_u8(0).unwrap(); 66];
    for field in ascii.chunks_mut(11) {
        for character in field {
            *character = std::ascii::Char::from_u8(kani::any::<u8>() & 0x7f).unwrap();
        }
    }
    ascii
}

fn short_ascii_records<const LOW: usize>() {
    let bytes: [u8; 66] = kani::any();
    let ascii = bytes.as_ascii();
    // This is exactly the original ASCII input domain, not an assumption
    // about the production parser's result. Unused suffix bytes are irrelevant.
    kani::assume(ascii.is_some());
    let text = ascii.unwrap().as_str();
    // Exhaust all 11 lengths concretely, keeping error formatting out of a
    // symbolic-length search. ASCII content remains arbitrary in every case.
    for offset in 0..11 {
        assert!(endf::checked_fields(&text[..LOW + offset]).is_err());
    }
}

macro_rules! short_record_proof {
    ($name:ident, $low:expr) => {
        #[kani::proof]
        #[kani::unwind(12)]
        fn $name() {
            short_ascii_records::<$low>();
        }
    };
}

short_record_proof!(endf_short_ascii_records_00_10, 0);
short_record_proof!(endf_short_ascii_records_11_21, 11);
short_record_proof!(endf_short_ascii_records_22_32, 22);
short_record_proof!(endf_short_ascii_records_33_43, 33);
short_record_proof!(endf_short_ascii_records_44_54, 44);
short_record_proof!(endf_short_ascii_records_55_65, 55);

#[kani::proof]
#[kani::unwind(14)]
fn endf_non_ascii_record_is_rejected() {
    for group in NON_ASCII_RECORDS.chunks(13) {
        for text in group {
            assert_eq!(text.len(), 66);
            assert!(endf::checked_fields(text).is_err());
            assert!(endf::checked_tail(text).is_err());
        }
    }
}

#[kani::proof]
#[kani::unwind(14)]
fn endf_non_ascii_with_valid_tail_is_rejected() {
    // The shared executable regression accepts VALID_RECORD with the same
    // numeric tail. Keep std integer parsing outside this non-ASCII claim.
    for group in NON_ASCII_TAIL_RECORDS.chunks(13) {
        for text in group {
            assert_eq!(text.len(), 75);
            assert!(endf::checked_tail(text).is_err());
        }
    }
}

fn csc_shift_pattern<const MASK: u8>() {
    // The 16 entry points below partition the original four symbolic booleans
    // exhaustively. Each invocation gives the heap a concrete input shape.
    let mut matrix = Csc {
        n: 2,
        colptr: vec![0],
        rowidx: Vec::new(),
        vals: Vec::new(),
    };
    for column in 0..2 {
        for row in 0..2 {
            if MASK & (1 << (column * 2 + row)) != 0 {
                matrix.rowidx.push(row);
                matrix.vals.push(C64::new(0.0, 0.0));
            }
        }
        matrix.colptr.push(matrix.vals.len());
    }
    let shifted = matrix.scale_shift(C64::new(1.0, 0.0), C64::new(1.0, 0.0));
    assert_eq!(shifted.n, 2);
    assert_eq!(shifted.colptr.len(), 3);
    assert_eq!(shifted.colptr[0], 0);
    assert_eq!(shifted.colptr[2], shifted.vals.len());
    assert_eq!(shifted.rowidx.len(), shifted.vals.len());
    for column in 0..2 {
        // Compare the complete output row set with the input mask plus its
        // diagonal. Ordering alone would also accept silently dropped entries.
        let mut expected_count = 0;
        for row in 0..2 {
            if row == column || MASK & (1 << (column * 2 + row)) != 0 {
                assert_eq!(shifted.rowidx[shifted.colptr[column] + expected_count], row);
                expected_count += 1;
            }
        }
        assert_eq!(
            shifted.colptr[column + 1] - shifted.colptr[column],
            expected_count
        );
        let mut diagonal_count = 0;
        let mut previous = None;
        for p in shifted.colptr[column]..shifted.colptr[column + 1] {
            let row = shifted.rowidx[p];
            assert!(row < 2);
            assert_eq!(shifted.vals[p].im, 0.0);
            assert_eq!(shifted.vals[p].re, if row == column { 1.0 } else { 0.0 });
            if let Some(before) = previous {
                assert!(before < row);
            }
            previous = Some(row);
            if row == column {
                diagonal_count += 1;
                assert_eq!(shifted.vals[p].re, 1.0);
            }
        }
        assert_eq!(diagonal_count, 1);
    }
}

macro_rules! csc_pattern_proof {
    ($name:ident, $mask:expr) => {
        #[kani::proof]
        #[kani::unwind(12)]
        fn $name() {
            csc_shift_pattern::<$mask>();
        }
    };
}

csc_pattern_proof!(csc_shift_pattern_00, 0x00);
csc_pattern_proof!(csc_shift_pattern_01, 0x01);
csc_pattern_proof!(csc_shift_pattern_02, 0x02);
csc_pattern_proof!(csc_shift_pattern_03, 0x03);
csc_pattern_proof!(csc_shift_pattern_04, 0x04);
csc_pattern_proof!(csc_shift_pattern_05, 0x05);
csc_pattern_proof!(csc_shift_pattern_06, 0x06);
csc_pattern_proof!(csc_shift_pattern_07, 0x07);
csc_pattern_proof!(csc_shift_pattern_08, 0x08);
csc_pattern_proof!(csc_shift_pattern_09, 0x09);
csc_pattern_proof!(csc_shift_pattern_0a, 0x0a);
csc_pattern_proof!(csc_shift_pattern_0b, 0x0b);
csc_pattern_proof!(csc_shift_pattern_0c, 0x0c);
csc_pattern_proof!(csc_shift_pattern_0d, 0x0d);
csc_pattern_proof!(csc_shift_pattern_0e, 0x0e);
csc_pattern_proof!(csc_shift_pattern_0f, 0x0f);
