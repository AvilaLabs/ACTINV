//! ACTINV-P92: gas production (H and He isotopes, appm).
//!
//! Light-particle multiplicities emitted per reaction, keyed by ENDF-6 neutron MT, from the
//! ENDF-6 manual's reaction definitions. Verified (by the caller, and re-verified in this
//! module's unit test) to Z/A-balance against `crates/actinv-data/data/mt_products.json`.

/// Version tag for the ejectile table, recorded in the run ledger under `gas.table_version`.
pub const TABLE_VERSION: &str = "endf6-mt-ejectiles-v1";

/// Ground-state ZA/LISO of the five tracked light nuclides.
pub const H1: (i32, i32) = (1001, 0);
pub const H2: (i32, i32) = (1002, 0);
pub const H3: (i32, i32) = (1003, 0);
pub const HE3: (i32, i32) = (2003, 0);
pub const HE4: (i32, i32) = (2004, 0);

/// The five ground states gas tracks, in a fixed order used throughout the module.
pub const LIGHT_STATES: [(i32, i32); 5] = [H1, H2, H3, HE3, HE4];

/// Light-particle multiplicities emitted per reaction: (n, p, d, t, he3, alpha).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Ejectiles {
    pub n: u32,
    pub p: u32,
    pub d: u32,
    pub t: u32,
    pub he3: u32,
    pub alpha: u32,
}

impl Ejectiles {
    const fn new(n: u32, p: u32, d: u32, t: u32, he3: u32, alpha: u32) -> Self {
        Self {
            n,
            p,
            d,
            t,
            he3,
            alpha,
        }
    }

    /// (dZ, dA) of the residual relative to target + incident neutron, i.e. the same convention
    /// as `crates/actinv-data/data/mt_products.json`: residual = target + neutron - emitted.
    pub fn za_delta(self) -> (i32, i32) {
        let dz = -(self.p as i32
            + self.d as i32
            + self.t as i32
            + 2 * self.he3 as i32
            + 2 * self.alpha as i32);
        let da = 1
            - (self.n as i32
                + self.p as i32
                + 2 * self.d as i32
                + 3 * self.t as i32
                + 3 * self.he3 as i32
                + 4 * self.alpha as i32);
        (dz, da)
    }

    /// (ZA, multiplicity) pairs for the light particles this reaction feeds into the inventory.
    /// Neutrons are excluded: they are not a tracked gas species.
    pub const fn gas_products(self) -> [((i32, i32), u32); 5] {
        [
            (H1, self.p),
            (H2, self.d),
            (H3, self.t),
            (HE3, self.he3),
            (HE4, self.alpha),
        ]
    }
}

/// No light-particle ejectiles: one re-emitted neutron only (inelastic scatter).
const INELASTIC: Ejectiles = Ejectiles::new(1, 0, 0, 0, 0, 0);

/// Ejectile multiplicities for a neutron MT, or `None` if the MT is uncovered (its reaction
/// rate is booked to the run ledger's `gas.uncovered`, keyed by MT, and no ejectiles are added).
///
/// Covers MT 4 and 50-91 (inelastic to a discrete or continuum level: one re-emitted neutron,
/// residual Z/A unchanged), 11-45 (excluding the 18-21 and 38 fission MTs), 102-117 and
/// 152-200. MT 18 (fission) and any other MT are uncovered.
pub const fn table(mt: i32) -> Option<Ejectiles> {
    Some(match mt {
        4 => INELASTIC,
        50..=91 => INELASTIC,
        11 => Ejectiles::new(2, 0, 1, 0, 0, 0),
        16 => Ejectiles::new(2, 0, 0, 0, 0, 0),
        17 => Ejectiles::new(3, 0, 0, 0, 0, 0),
        22 => Ejectiles::new(1, 0, 0, 0, 0, 1),
        23 => Ejectiles::new(1, 0, 0, 0, 0, 3),
        24 => Ejectiles::new(2, 0, 0, 0, 0, 1),
        25 => Ejectiles::new(3, 0, 0, 0, 0, 1),
        28 => Ejectiles::new(1, 1, 0, 0, 0, 0),
        29 => Ejectiles::new(1, 0, 0, 0, 0, 2),
        30 => Ejectiles::new(2, 0, 0, 0, 0, 2),
        32 => Ejectiles::new(1, 0, 1, 0, 0, 0),
        33 => Ejectiles::new(1, 0, 0, 1, 0, 0),
        34 => Ejectiles::new(1, 0, 0, 0, 1, 0),
        35 => Ejectiles::new(1, 0, 1, 0, 0, 2),
        36 => Ejectiles::new(1, 0, 0, 1, 0, 2),
        37 => Ejectiles::new(4, 0, 0, 0, 0, 0),
        41 => Ejectiles::new(2, 1, 0, 0, 0, 0),
        42 => Ejectiles::new(3, 1, 0, 0, 0, 0),
        44 => Ejectiles::new(1, 2, 0, 0, 0, 0),
        45 => Ejectiles::new(1, 1, 0, 0, 0, 1),
        102 => Ejectiles::new(0, 0, 0, 0, 0, 0),
        103 => Ejectiles::new(0, 1, 0, 0, 0, 0),
        104 => Ejectiles::new(0, 0, 1, 0, 0, 0),
        105 => Ejectiles::new(0, 0, 0, 1, 0, 0),
        106 => Ejectiles::new(0, 0, 0, 0, 1, 0),
        107 => Ejectiles::new(0, 0, 0, 0, 0, 1),
        108 => Ejectiles::new(0, 0, 0, 0, 0, 2),
        109 => Ejectiles::new(0, 0, 0, 0, 0, 3),
        111 => Ejectiles::new(0, 2, 0, 0, 0, 0),
        112 => Ejectiles::new(0, 1, 0, 0, 0, 1),
        113 => Ejectiles::new(0, 0, 0, 1, 0, 2),
        114 => Ejectiles::new(0, 0, 1, 0, 0, 2),
        115 => Ejectiles::new(0, 1, 1, 0, 0, 0),
        116 => Ejectiles::new(0, 1, 0, 1, 0, 0),
        117 => Ejectiles::new(0, 0, 1, 0, 0, 1),
        152 => Ejectiles::new(5, 0, 0, 0, 0, 0),
        153 => Ejectiles::new(6, 0, 0, 0, 0, 0),
        154 => Ejectiles::new(2, 0, 0, 1, 0, 0),
        155 => Ejectiles::new(0, 0, 0, 1, 0, 1),
        156 => Ejectiles::new(4, 1, 0, 0, 0, 0),
        157 => Ejectiles::new(3, 0, 1, 0, 0, 0),
        158 => Ejectiles::new(1, 0, 1, 0, 0, 1),
        159 => Ejectiles::new(2, 1, 0, 0, 0, 1),
        160 => Ejectiles::new(7, 0, 0, 0, 0, 0),
        161 => Ejectiles::new(8, 0, 0, 0, 0, 0),
        162 => Ejectiles::new(5, 1, 0, 0, 0, 0),
        163 => Ejectiles::new(6, 1, 0, 0, 0, 0),
        164 => Ejectiles::new(7, 1, 0, 0, 0, 0),
        165 => Ejectiles::new(4, 0, 0, 0, 0, 1),
        166 => Ejectiles::new(5, 0, 0, 0, 0, 1),
        167 => Ejectiles::new(6, 0, 0, 0, 0, 1),
        168 => Ejectiles::new(7, 0, 0, 0, 0, 1),
        169 => Ejectiles::new(4, 0, 1, 0, 0, 0),
        170 => Ejectiles::new(5, 0, 1, 0, 0, 0),
        171 => Ejectiles::new(6, 0, 1, 0, 0, 0),
        172 => Ejectiles::new(3, 0, 0, 1, 0, 0),
        173 => Ejectiles::new(4, 0, 0, 1, 0, 0),
        174 => Ejectiles::new(5, 0, 0, 1, 0, 0),
        175 => Ejectiles::new(6, 0, 0, 1, 0, 0),
        176 => Ejectiles::new(2, 0, 0, 0, 1, 0),
        177 => Ejectiles::new(3, 0, 0, 0, 1, 0),
        178 => Ejectiles::new(4, 0, 0, 0, 1, 0),
        179 => Ejectiles::new(3, 2, 0, 0, 0, 0),
        180 => Ejectiles::new(3, 0, 0, 0, 0, 2),
        181 => Ejectiles::new(3, 1, 0, 0, 0, 1),
        182 => Ejectiles::new(0, 0, 1, 1, 0, 0),
        183 => Ejectiles::new(1, 1, 1, 0, 0, 0),
        184 => Ejectiles::new(1, 1, 0, 1, 0, 0),
        185 => Ejectiles::new(1, 0, 1, 1, 0, 0),
        186 => Ejectiles::new(1, 1, 0, 0, 1, 0),
        187 => Ejectiles::new(1, 0, 1, 0, 1, 0),
        188 => Ejectiles::new(1, 0, 0, 1, 1, 0),
        189 => Ejectiles::new(1, 0, 0, 1, 0, 1),
        190 => Ejectiles::new(2, 2, 0, 0, 0, 0),
        191 => Ejectiles::new(0, 1, 0, 0, 1, 0),
        192 => Ejectiles::new(0, 0, 1, 0, 1, 0),
        193 => Ejectiles::new(0, 0, 0, 0, 1, 1),
        194 => Ejectiles::new(4, 2, 0, 0, 0, 0),
        195 => Ejectiles::new(4, 0, 0, 0, 0, 2),
        196 => Ejectiles::new(4, 1, 0, 0, 0, 1),
        197 => Ejectiles::new(0, 3, 0, 0, 0, 0),
        198 => Ejectiles::new(1, 3, 0, 0, 0, 0),
        199 => Ejectiles::new(3, 2, 0, 0, 0, 1),
        200 => Ejectiles::new(5, 2, 0, 0, 0, 0),
        _ => return None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Vendored residual (dZ, dA) offsets, keyed by MT: residual = target + neutron - emitted.
    /// This is the same file `assemble_reaction_rates`'s G2 balance check runs against on real
    /// libraries; here it is re-verified for every table MT the file also covers.
    const MT_PRODUCTS_JSON: &str = include_str!("../../actinv-data/data/mt_products.json");

    fn vendored_table() -> std::collections::BTreeMap<i32, (i32, i32)> {
        let doc: serde_json::Value = serde_json::from_str(MT_PRODUCTS_JSON).unwrap();
        doc["table"]
            .as_object()
            .expect("mt_products.json table is an object")
            .iter()
            .map(|(mt, delta)| {
                let mt: i32 = mt.parse().expect("MT key parses as i32");
                let arr = delta.as_array().expect("delta is a two-element array");
                assert_eq!(arr.len(), 2, "MT {mt} delta has {} elements", arr.len());
                let dz = arr[0].as_i64().expect("dZ is an integer") as i32;
                let da = arr[1].as_i64().expect("dA is an integer") as i32;
                (mt, (dz, da))
            })
            .collect()
    }

    #[test]
    fn table_za_balances_against_vendored_mt_products() {
        let vendored = vendored_table();
        assert!(!vendored.is_empty());
        let mut checked = 0;
        for (&mt, &expected) in &vendored {
            let ejectiles =
                table(mt).unwrap_or_else(|| panic!("gas::table has no entry for vendored MT {mt}"));
            let got = ejectiles.za_delta();
            assert_eq!(
                got, expected,
                "MT {mt}: gas table gives (dZ, dA) = {got:?}, mt_products.json gives {expected:?}"
            );
            checked += 1;
        }
        assert_eq!(checked, vendored.len());
    }

    #[test]
    fn inelastic_mts_balance_to_no_za_change() {
        for mt in [4, 50, 75, 91] {
            let ejectiles = table(mt).unwrap();
            assert_eq!(ejectiles.za_delta(), (0, 0));
        }
        assert!(table(49).is_none());
        assert!(table(92).is_none());
    }

    #[test]
    fn uncovered_mts_are_none() {
        // MT 18 (fission) and MT 1/2/102-family gaps are deliberately not in the table.
        for mt in [1, 2, 3, 18, 19, 20, 21, 38, 110, 150, 151, 201, 891] {
            assert!(table(mt).is_none(), "MT {mt} unexpectedly covered");
        }
    }

    #[test]
    fn gas_products_excludes_neutrons_and_orders_h_then_he() {
        // MT 11 = (n,2nd): 2 neutrons + 1 deuteron.
        let e = table(11).unwrap();
        assert_eq!(e, Ejectiles::new(2, 0, 1, 0, 0, 0));
        let products = e.gas_products();
        assert_eq!(products[0], (H1, 0));
        assert_eq!(products[1], (H2, 1));
        assert_eq!(products[2], (H3, 0));
        assert_eq!(products[3], (HE3, 0));
        assert_eq!(products[4], (HE4, 0));
    }
}
