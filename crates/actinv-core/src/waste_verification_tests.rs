//! Public-API boundary regressions identified by the verification mutation campaign.
use crate::waste::{
    evaluate_component, Evaluation, NuclideProperties, RulePack, WasteClass, WasteGeometry,
    WasteType,
};
use std::collections::BTreeMap;

fn evaluate_one(
    pack: &RulePack,
    key: &str,
    properties: NuclideProperties,
    activity: f64,
) -> Evaluation {
    evaluate_component(
        pack,
        WasteType::General,
        WasteGeometry {
            mass_g: 1.0,
            displaced_volume_cm3: 1.0,
        },
        &BTreeMap::from([(key.to_owned(), activity)]),
        &BTreeMap::from([(key.to_owned(), properties)]),
    )
    .unwrap()
}

#[test]
fn alpha_listing_and_evaluation_keep_both_strict_cutoffs() {
    let pack = RulePack::bundled().unwrap();
    // Synthetic metadata exercises the policy boundaries independently of the
    // evaluated half-lives of these nuclides in any particular data library.
    for (key, z, half_life_s, member) in [
        ("U235", 92, 100.0 * pack.year_s, false),
        ("Np237", 93, 5.0 * pack.year_s, false),
        ("Np237", 93, 5.0 * pack.year_s + 1.0, true),
    ] {
        let properties = NuclideProperties {
            z,
            half_life_s,
            alpha_emitting: true,
        };
        assert_eq!(
            pack.nuclide_listed(WasteType::General, key, Some(properties))
                .unwrap(),
            Some(member)
        );
        let result = evaluate_one(&pack, key, properties, 1.0);
        assert_eq!(
            result.row_fractions.iter().any(|row| row.table == 1),
            member
        );
        assert_eq!(result.unlisted_activity_bq, if member { 0.0 } else { 1.0 });
    }
}

#[test]
fn positive_subnormal_activity_accepts_finite_zero_concentration_and_sums() {
    let pack = RulePack::bundled().unwrap();
    let result = evaluate_one(
        &pack,
        "Cs137",
        NuclideProperties {
            z: 55,
            half_life_s: 30.0 * pack.year_s,
            alpha_emitting: false,
        },
        f64::from_bits(1),
    );
    assert_eq!(result.class, WasteClass::A);
    assert!(!result.row_fractions.is_empty());
    for row in result.row_fractions {
        assert_eq!(row.concentration, 0.0);
        assert_eq!(row.fraction, Some(0.0));
    }
    assert!(result
        .constraints
        .iter()
        .all(|constraint| constraint.source_sum_fraction == 0.0));
}
