#![allow(non_snake_case)] // eV and K suffixes are part of the wire vocabulary.
//! Displacement physics for the kinematic damage method (P154): the Lindhard partition in Robinson's fit, the
//! exact NRT displacement function and the arc-dpa efficiency of Nordlund et al.
//!
//! The partition reproduces NJOY2016 HEATR's `df`: a recoil of kinetic energy `T` in a lattice of `(Z, A)` deposits
//! the damage energy `T / (1 + F_L * g(eps))`, `eps = T / E_L`, and nothing at all below the displacement
//! threshold. NJOY compares the threshold with the *recoil* energy `T` (`e.lt.break`), not with the damage
//! energy; this module does the same.

/// NRT viable fraction of damage energy deposited as displacements.
pub const NRT_KAPPA: f64 = 0.8;

/// Neutron rest energy `m_n c^2` in eV (CODATA 2018), the unit that converts mass ratios into photon recoil.
pub const NEUTRON_REST_EV: f64 = 939_565_420.52;

/// Citation recorded in every table-2 file that carries arc-dpa parameters.
pub const ARC_DPA_CITATION: &str = "Nordlund, K. et al. Improving atomic displacement and replacement calculations with physically realistic damage models. Nat. Commun. 9, 1084 (2018), Table 1 (arc-dpa constants b and c)";

/// One row of Table 1 of Nordlund et al. (2018): the displacement energy used in the molecular-dynamics fit and the
/// arc-dpa constants.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ArcDpaEntry {
    pub symbol: &'static str,
    /// Displacement energy `E_d` of the paper's fit (eV).
    pub ed_eV: f64,
    pub b: f64,
    pub c: f64,
}

/// Transcribed from Table 1 of Nordlund, K. et al., Nat. Commun. 9, 1084 (2018) ("arc-dpa" columns).
pub const ARC_DPA_TABLE: [ArcDpaEntry; 6] = [
    ArcDpaEntry {
        symbol: "Fe",
        ed_eV: 40.0,
        b: -0.568,
        c: 0.286,
    },
    ArcDpaEntry {
        symbol: "Cu",
        ed_eV: 33.0,
        b: -0.68,
        c: 0.16,
    },
    ArcDpaEntry {
        symbol: "Ni",
        ed_eV: 39.0,
        b: -1.01,
        c: 0.23,
    },
    ArcDpaEntry {
        symbol: "Pd",
        ed_eV: 41.0,
        b: -0.88,
        c: 0.15,
    },
    ArcDpaEntry {
        symbol: "Pt",
        ed_eV: 42.0,
        b: -1.12,
        c: 0.11,
    },
    ArcDpaEntry {
        symbol: "W",
        ed_eV: 70.0,
        b: -0.56,
        c: 0.12,
    },
];

/// The paper's arc-dpa entry for an element symbol, if it published one.
pub fn arc_dpa_entry(symbol: &str) -> Option<&'static ArcDpaEntry> {
    ARC_DPA_TABLE.iter().find(|entry| entry.symbol == symbol)
}

/// Lindhard partition of a recoil's kinetic energy into atomic motion (Robinson's fit), NJOY HEATR `df`.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Partition {
    /// `Z_recoil == 0` yields no damage (NJOY returns zero for it).
    active: bool,
    /// `1 / E_L`.
    rel: f64,
    fl: f64,
    /// Recoil energy below which the damage energy is zero (eV).
    threshold_eV: f64,
}

impl Partition {
    /// Recoil `(zr, ar)` in a lattice `(zl, al)`; masses are atomic-mass ratios as in the evaluation (AWR).
    pub fn new(zr: f64, ar: f64, zl: f64, al: f64, threshold_eV: f64) -> Self {
        if zr <= 0.0 || zl <= 0.0 || ar <= 0.0 || al <= 0.0 {
            return Self {
                active: false,
                rel: 0.0,
                fl: 0.0,
                threshold_eV,
            };
        }
        let twothd = 2.0 / 3.0;
        let zsum = zr.powf(twothd) + zl.powf(twothd);
        let el = 30.724 * zr * zl * zsum.sqrt() * (ar + al) / al;
        let denom = zsum.powf(0.75) * ar.powf(1.5) * al.sqrt();
        let fl = 0.0793 * zr.powf(twothd) * zl.sqrt() * (ar + al).powf(1.5) / denom;
        Self {
            active: true,
            rel: 1.0 / el,
            fl,
            threshold_eV,
        }
    }

    pub fn threshold_eV(&self) -> f64 {
        self.threshold_eV
    }

    /// Damage energy of a recoil of kinetic energy `t` (eV); zero below the threshold.
    pub fn damage_energy(&self, t: f64) -> f64 {
        if !self.active || t < self.threshold_eV || t <= 0.0 {
            return 0.0;
        }
        let ep = t * self.rel;
        t / (1.0 + self.fl * (3.4008 * ep.powf(1.0 / 6.0) + 0.40244 * ep.powf(0.75) + ep))
    }

    /// Smallest recoil energy whose damage energy reaches `target` (eV), ignoring the threshold. The partition
    /// is increasing in `t`; the result is bracketed by bisection to full double precision. `None` when the
    /// partition is inactive.
    pub fn recoil_for_damage(&self, target: f64) -> Option<f64> {
        if !self.active || target <= 0.0 {
            return None;
        }
        let raw = |t: f64| {
            let ep = t * self.rel;
            t / (1.0 + self.fl * (3.4008 * ep.powf(1.0 / 6.0) + 0.40244 * ep.powf(0.75) + ep))
        };
        let mut low = target;
        let mut high = target * 2.0;
        let mut guard = 0;
        while raw(high) < target {
            low = high;
            high *= 2.0;
            guard += 1;
            if guard > 200 {
                return None;
            }
        }
        for _ in 0..200 {
            let middle = 0.5 * (low + high);
            if raw(middle) < target {
                low = middle;
            } else {
                high = middle;
            }
        }
        Some(high)
    }
}

/// Exact NRT displacement count for a damage energy `td`: 0 below `E_d`, 1 up to `2 E_d / 0.8`, then
/// `0.8 td / (2 E_d)`.
pub fn nrt_displacements(td: f64, ed: f64) -> f64 {
    if td < ed {
        0.0
    } else if td < 2.0 * ed / NRT_KAPPA {
        1.0
    } else {
        NRT_KAPPA * td / (2.0 * ed)
    }
}

/// arc-dpa efficiency `xi(td)` of Nordlund et al. (2018): 1 below `2 E_d / 0.8`, then
/// `(1 - c) / (2 E_d / 0.8)^b * td^b + c`.
pub fn arc_efficiency(td: f64, ed: f64, b: f64, c: f64) -> f64 {
    let knee = 2.0 * ed / NRT_KAPPA;
    if td < knee {
        1.0
    } else {
        (1.0 - c) / knee.powf(b) * td.powf(b) + c
    }
}

/// arc-dpa displacement count `N_NRT * xi`.
pub fn arc_displacements(td: f64, ed: f64, b: f64, c: f64) -> f64 {
    nrt_displacements(td, ed) * arc_efficiency(td, ed, b, c)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn partition_matches_hand_values() {
        // Fe-56 recoil in an Fe-56 lattice: Zr = Zl = 26, Ar = Al = 55.45.
        let z = 26.0_f64;
        let a = 55.45_f64;
        let partition = Partition::new(z, a, z, a, 40.0);
        let twothd = 2.0 / 3.0;
        let zsum = 2.0 * z.powf(twothd);
        let el = 30.724 * z * z * zsum.sqrt() * 2.0;
        let fl = 0.0793 * z.powf(twothd) * z.sqrt() * (2.0 * a).powf(1.5)
            / (zsum.powf(0.75) * a.powf(1.5) * a.sqrt());
        let t: f64 = 10_000.0;
        let ep = t / el;
        let expected = t / (1.0 + fl * (3.4008 * ep.powf(1.0 / 6.0) + 0.40244 * ep.powf(0.75) + ep));
        assert!((partition.damage_energy(t) - expected).abs() < 1e-12 * expected);
        // E_L for Fe-on-Fe is about 1.74e5 eV in the Robinson fit; F_L is about 0.157.
        assert!((el - 1.74e5).abs() < 0.1e5, "E_L {el}");
        assert!(fl > 0.15 && fl < 0.17, "F_L {fl}");
        // damage energy is below the recoil energy and about 70 % of it at 10 keV
        let ratio = partition.damage_energy(t) / t;
        assert!(ratio > 0.55 && ratio < 0.85, "ratio {ratio}");
    }

    #[test]
    fn partition_is_zero_below_threshold_on_the_recoil_energy() {
        let partition = Partition::new(26.0, 55.45, 26.0, 55.45, 40.0);
        assert_eq!(partition.damage_energy(39.999), 0.0);
        assert!(partition.damage_energy(40.0) > 0.0);
        // a zero-Z recoil produces no damage
        let none = Partition::new(0.0, 55.45, 26.0, 55.45, 40.0);
        assert_eq!(none.damage_energy(1.0e5), 0.0);
    }

    #[test]
    fn recoil_for_damage_inverts_the_partition() {
        let partition = Partition::new(26.0, 55.45, 26.0, 55.45, 40.0);
        let target = 100.0;
        let t = partition.recoil_for_damage(target).unwrap();
        assert!((partition.damage_energy(t) - target).abs() < 1e-9);
        assert!(t > target);
    }

    #[test]
    fn nrt_function_is_exact() {
        let ed = 40.0;
        assert_eq!(nrt_displacements(39.9, ed), 0.0);
        assert_eq!(nrt_displacements(40.0, ed), 1.0);
        assert_eq!(nrt_displacements(99.9, ed), 1.0);
        let knee = 2.0 * ed / 0.8;
        assert_eq!(nrt_displacements(knee, ed), 0.8 * knee / (2.0 * ed));
        assert!((nrt_displacements(1000.0, ed) - 0.8 * 1000.0 / 80.0).abs() < 1e-15);
    }

    #[test]
    fn arc_function_matches_the_published_form() {
        let entry = arc_dpa_entry("Fe").unwrap();
        let ed = entry.ed_eV;
        let knee = 2.0 * ed / 0.8;
        // continuous at the knee: xi = 1
        assert!((arc_efficiency(knee, ed, entry.b, entry.c) - 1.0).abs() < 1e-12);
        assert_eq!(arc_efficiency(knee * 0.5, ed, entry.b, entry.c), 1.0);
        // a 100 keV damage energy in iron gives an efficiency near 0.3 (the paper's plateau c = 0.286)
        let xi = arc_efficiency(1.0e5, ed, entry.b, entry.c);
        let expected = (1.0 - entry.c) / knee.powf(entry.b) * 1.0e5_f64.powf(entry.b) + entry.c;
        assert!((xi - expected).abs() < 1e-15);
        assert!(xi > entry.c && xi < 0.4, "xi {xi}");
        assert!(
            (arc_displacements(1.0e5, ed, entry.b, entry.c) - nrt_displacements(1.0e5, ed) * xi)
                .abs()
                < 1e-12
        );
    }

    #[test]
    fn arc_table_carries_the_paper_values() {
        let rows: Vec<(&str, f64, f64, f64)> = ARC_DPA_TABLE
            .iter()
            .map(|entry| (entry.symbol, entry.ed_eV, entry.b, entry.c))
            .collect();
        assert_eq!(
            rows,
            vec![
                ("Fe", 40.0, -0.568, 0.286),
                ("Cu", 33.0, -0.68, 0.16),
                ("Ni", 39.0, -1.01, 0.23),
                ("Pd", 41.0, -0.88, 0.15),
                ("Pt", 42.0, -1.12, 0.11),
                ("W", 70.0, -0.56, 0.12),
            ]
        );
        assert!(arc_dpa_entry("Cr").is_none());
    }
}
