use actinv_core::{
    waste::{
        evaluate_component, Coverage, NuclideProperties, RulePack, WasteClass, WasteGeometry,
        WasteType,
    },
    waste_bounds::{evaluate_component_bounds, ActivityInterval},
};
use proptest::prelude::*;
use std::collections::BTreeMap;

fn properties() -> BTreeMap<String, NuclideProperties> {
    [
        ("H3", 1, 12.32),
        ("Sr90", 38, 28.79),
        ("Cs137", 55, 30.05),
        ("C14", 6, 5700.0),
        ("Ni63", 28, 100.0),
    ]
    .into_iter()
    .map(|(name, z, years)| {
        (
            name.to_owned(),
            NuclideProperties {
                z,
                half_life_s: years * 31_557_600.0,
                alpha_emitting: false,
            },
        )
    })
    .collect()
}

proptest! {
    #[test]
    fn interval_envelope_contains_generated_interior_classifications(
        exponent in -6i32..13,
        components in prop::collection::vec((0u16..1000,0u16..1000,0u16..1001),5),
        metal in any::<bool>(),
    ) {
        let scale = 10.0f64.powi(exponent);
        let mut bounds = BTreeMap::new();
        let mut witness = BTreeMap::new();
        for (name,(lower,width,fraction)) in ["H3","Sr90","Cs137","C14","Ni63"].into_iter().zip(components) {
            let lower = f64::from(lower)*scale;
            let upper = lower+f64::from(width)*scale;
            bounds.insert(name.to_owned(),ActivityInterval {lower_bq:lower,upper_bq:upper});
            let sample = lower+(upper-lower)*(f64::from(fraction)/1000.0);
            witness.insert(name.to_owned(),sample.clamp(lower,upper));
        }
        let pack = RulePack::bundled().unwrap();
        let props = properties();
        let geometry = WasteGeometry {mass_g:1.0,displaced_volume_cm3:1.0};
        let kind = if metal {WasteType::ActivatedMetal} else {WasteType::General};
        let envelope = evaluate_component_bounds(&pack,kind,geometry,&bounds,&props,Coverage::Complete).unwrap();
        let sample = evaluate_component(&pack,kind,geometry,&witness,&props).unwrap();
        prop_assert!(envelope.class_envelope.contains(&sample.class),
            "interior class {:?} absent from {:?}",sample.class,envelope.class_envelope);
        prop_assert_eq!(envelope.conservative_superset,Some(true));
        for row in envelope.row_fraction_ranges {
            if let (Some(low),Some(high)) = (row.lower.fraction,row.upper.fraction) {
                prop_assert!(low <= high);
            }
        }
    }

    #[test]
    fn positive_activity_with_missing_properties_fails_closed(activity in 1.0f64..1e12) {
        let pack = RulePack::bundled().unwrap();
        let bounds = BTreeMap::from([("Cs137".to_owned(),ActivityInterval {lower_bq:0.0,upper_bq:activity})]);
        let result = evaluate_component_bounds(&pack,WasteType::General,
            WasteGeometry {mass_g:1.0,displaced_volume_cm3:1.0},&bounds,&BTreeMap::new(),Coverage::Complete).unwrap();
        prop_assert_eq!(result.class_envelope,vec![WasteClass::Unknown]);
        prop_assert_eq!(result.conservative_superset,None);
    }
}
