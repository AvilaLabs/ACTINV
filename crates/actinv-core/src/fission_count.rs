#![allow(non_snake_case)] // field names are the JSON wire format (energy_per_fission_MeV)
//! Fission count, burn-up and spontaneous-fission neutron source: the arithmetic behind the opt-in
//! `options.fission` and `fission_yields.spontaneous` outputs. The run supplies the cumulative fission
//! counts (integrated in the same CRAM solve as the inventory) and the populations; this module turns
//! them into reported quantities and holds nothing that reads a file except the nu-bar table.
use crate::run::{name_of, EV};
use actinv_data::composition::{MaterialKey, NA, NEUTRON_MASS_AMU};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// Energy per fission when the spec does not set `options.fission.energy_per_fission_MeV`: the
/// conventional round 200 MeV, a stated default and not a nuclide-specific Q value or an evaluated
/// recoverable energy. Burn-up in MWd/kg scales linearly with whatever is used.
pub const DEFAULT_ENERGY_PER_FISSION_MEV: f64 = 200.0;

/// Smallest atomic number counted as heavy metal in the burn-up denominator (the actinides, Th on).
pub const HEAVY_METAL_MIN_Z: i32 = 90;

/// Joules in one megawatt-day.
const J_PER_MWD: f64 = 86_400.0e6;

/// Cumulative fissions and burn-up after a step; present only when `options.fission` is declared.
#[derive(Serialize)]
pub struct FissionCountOut {
    /// Neutron-induced (MT=18 reaction rate) plus spontaneous fissions since the start of the
    /// schedule, per gram of initial material.
    pub fissions_per_g: f64,
    pub neutron_induced_fissions_per_g: f64,
    /// One per spontaneous-fission decay (lambda times the SF branching), whether or not the
    /// state has yield data.
    pub spontaneous_fissions_per_g: f64,
    /// Atoms of Z >= 90 per gram of the declared material: the burn-up denominator.
    pub initial_heavy_metal_atoms_per_g: f64,
    /// 100 * fissions / initial heavy-metal atoms (fissions per initial heavy-metal atom, FIMA);
    /// absent when the material holds no heavy metal.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub burnup_percent: Option<f64>,
    pub energy_per_fission_MeV: f64,
    /// Fission energy released per kilogram of initial heavy metal, in MWd/kg; absent when the
    /// material has no heavy metal or a heavy-metal isotope has no mass in the decay data.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub burnup_MWd_per_kg_hm: Option<f64>,
}

/// Spontaneous-fission neutron emission rate after a step; present only when
/// `fission_yields.spontaneous` supplies nu-bar data.
#[derive(Serialize)]
pub struct SfNeutronSourceOut {
    /// Sum over states of lambda * BR_SF * N * nu-bar, in neutrons per second per gram.
    pub neutrons_per_s_per_g: f64,
    pub by_nuclide: BTreeMap<String, f64>,
    /// Spontaneous-fission decays per second per gram of populated states with no nu-bar: their
    /// neutrons are missing from the total, never estimated.
    pub uncovered_sf_decays_per_s_per_g: f64,
}

#[derive(Serialize)]
pub struct FissionStepOut {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub count: Option<FissionCountOut>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub spontaneous_neutron_source: Option<SfNeutronSourceOut>,
}

/// Heavy-metal content of the declared material.
#[derive(Debug, PartialEq)]
pub struct HeavyMetal {
    pub atoms_per_g: f64,
    /// Grams of heavy metal per gram of material; `None` when an isotope has no mass.
    pub mass_g_per_g: Option<f64>,
    /// Heavy-metal isotopes with no atomic mass in the decay data.
    pub without_mass: Vec<(i32, i32)>,
}

/// Sum the Z >= `HEAVY_METAL_MIN_Z` part of an inventory (atoms per gram by (ZA, LISO)). `awr` is
/// the isotope's mass in neutron masses (the decay evaluation's AWR), as the composition parser uses
/// for explicit isotopes.
pub fn heavy_metal(
    inventory: &BTreeMap<(i32, i32), f64>,
    awr: impl Fn((i32, i32)) -> Option<f64>,
) -> HeavyMetal {
    let mut atoms = 0.0;
    let mut mass = 0.0;
    let mut without_mass = Vec::new();
    for (&key, &count) in inventory {
        if key.0 / 1000 < HEAVY_METAL_MIN_Z || count <= 0.0 {
            continue;
        }
        atoms += count;
        match awr(key) {
            Some(awr) => mass += count * awr * NEUTRON_MASS_AMU / NA,
            None => without_mass.push(key),
        }
    }
    HeavyMetal {
        atoms_per_g: atoms,
        mass_g_per_g: without_mass.is_empty().then_some(mass),
        without_mass,
    }
}

/// Burn-up observables from cumulative fission counts per gram of initial material.
pub fn fission_count(
    induced: f64,
    spontaneous: f64,
    heavy_metal: &HeavyMetal,
    energy_per_fission_mev: f64,
) -> FissionCountOut {
    let fissions = induced + spontaneous;
    let burnup_percent =
        (heavy_metal.atoms_per_g > 0.0).then(|| 100.0 * fissions / heavy_metal.atoms_per_g);
    let burnup_mwd_per_kg_hm = heavy_metal
        .mass_g_per_g
        .filter(|mass| *mass > 0.0)
        .map(|mass_g| {
            fissions * energy_per_fission_mev * 1.0e6 * EV / J_PER_MWD / (mass_g / 1.0e3)
        });
    FissionCountOut {
        fissions_per_g: fissions,
        neutron_induced_fissions_per_g: induced,
        spontaneous_fissions_per_g: spontaneous,
        initial_heavy_metal_atoms_per_g: heavy_metal.atoms_per_g,
        burnup_percent,
        energy_per_fission_MeV: energy_per_fission_mev,
        burnup_MWd_per_kg_hm: burnup_mwd_per_kg_hm,
    }
}

/// Spontaneous-fission neutron source from `(state, SF decays per second per gram)` pairs: the sum of
/// decays times nu-bar. A state without a nu-bar adds to the uncovered decay rate, not to the total.
pub fn sf_neutron_source(
    decays: &[((i32, i32), f64)],
    nubar: &BTreeMap<(i32, i32), (f64, String)>,
) -> SfNeutronSourceOut {
    let mut out = SfNeutronSourceOut {
        neutrons_per_s_per_g: 0.0,
        by_nuclide: BTreeMap::new(),
        uncovered_sf_decays_per_s_per_g: 0.0,
    };
    for &(key, decays_per_s_g) in decays {
        if decays_per_s_g <= 0.0 {
            continue;
        }
        match nubar.get(&key) {
            Some((value, _)) => {
                let neutrons = decays_per_s_g * value;
                *out.by_nuclide.entry(name_of(key.0, key.1)).or_insert(0.0) += neutrons;
                out.neutrons_per_s_per_g += neutrons;
            }
            None => out.uncovered_sf_decays_per_s_per_g += decays_per_s_g,
        }
    }
    out
}

/// A user-supplied nu-bar table: `{"source": "<citation>", "nubar": {"Cf252": <value>, ...}}`. The
/// citation is mandatory; no multiplicity is built in.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct NubarTable {
    source: String,
    nubar: BTreeMap<String, f64>,
}

/// (ZA, LISO) -> nu-bar.
pub type NubarMap = BTreeMap<(i32, i32), f64>;

/// Parse a nu-bar table into its citation and (ZA, LISO) -> nu-bar map.
pub fn parse_nubar_table(text: &str) -> Result<(String, NubarMap), String> {
    let table: NubarTable =
        serde_json::from_str(text).map_err(|error| format!("nubar table: {error}"))?;
    if table.source.trim().is_empty() {
        return Err("nubar table requires a non-empty `source` citation".into());
    }
    if table.nubar.is_empty() {
        return Err("nubar table lists no nuclides".into());
    }
    let mut out = BTreeMap::new();
    for (name, value) in &table.nubar {
        let key = match actinv_data::composition::material_key(name) {
            Ok(MaterialKey::Nuclide { za, liso, .. }) => (za, liso),
            _ => {
                return Err(format!(
                    "nubar table key '{name}' must be an explicit nuclide such as 'Cf252'"
                ))
            }
        };
        if !value.is_finite() || *value <= 0.0 {
            return Err(format!(
                "nubar table value for '{name}' must be finite and positive"
            ));
        }
        if out.insert(key, *value).is_some() {
            return Err(format!("nubar table lists '{name}' twice"));
        }
    }
    Ok((table.source, out))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn close(got: f64, want: f64, rel: f64) {
        assert!(
            (got - want).abs() <= rel * want.abs(),
            "got {got}, want {want}"
        );
    }

    #[test]
    fn burnup_percent_and_mwd_per_kg_follow_from_the_fission_count() {
        // 1e22 heavy-metal atoms weighing 1 g per gram of material (pure heavy metal), 1e20 fissions.
        let heavy = HeavyMetal {
            atoms_per_g: 1.0e22,
            mass_g_per_g: Some(1.0),
            without_mass: Vec::new(),
        };
        let out = fission_count(6.0e19, 4.0e19, &heavy, 200.0);
        assert_eq!(out.fissions_per_g, 1.0e20);
        assert_eq!(out.neutron_induced_fissions_per_g, 6.0e19);
        assert_eq!(out.spontaneous_fissions_per_g, 4.0e19);
        close(out.burnup_percent.unwrap(), 1.0, 1e-14);
        // 1e20 * 200 MeV * 1.602176634e-13 J/MeV = 3.204353268e9 J; / 8.64e10 J/MWd = 0.0370874 MWd
        // in the gram, which is 1e-3 kg of heavy metal.
        close(out.burnup_MWd_per_kg_hm.unwrap(), 37.08742208333333, 1e-12);
        // Linear in the stated fission energy.
        let doubled = fission_count(6.0e19, 4.0e19, &heavy, 400.0);
        close(
            doubled.burnup_MWd_per_kg_hm.unwrap(),
            2.0 * out.burnup_MWd_per_kg_hm.unwrap(),
            1e-14,
        );
        assert_eq!(doubled.burnup_percent, out.burnup_percent);
    }

    #[test]
    fn burnup_is_absent_without_heavy_metal_or_a_heavy_metal_mass() {
        let none = HeavyMetal {
            atoms_per_g: 0.0,
            mass_g_per_g: Some(0.0),
            without_mass: Vec::new(),
        };
        let out = fission_count(1.0, 0.0, &none, DEFAULT_ENERGY_PER_FISSION_MEV);
        assert_eq!(out.fissions_per_g, 1.0);
        assert!(out.burnup_percent.is_none() && out.burnup_MWd_per_kg_hm.is_none());
        let massless = HeavyMetal {
            atoms_per_g: 1.0e20,
            mass_g_per_g: None,
            without_mass: vec![(92_235, 0)],
        };
        let out = fission_count(1.0e18, 0.0, &massless, 200.0);
        close(out.burnup_percent.unwrap(), 1.0, 1e-14);
        assert!(out.burnup_MWd_per_kg_hm.is_none());
    }

    #[test]
    fn heavy_metal_counts_only_z_90_and_above_and_sums_their_mass() {
        // Arbitrary synthetic atoms per gram; the masses are the AWR times the neutron mass.
        let inventory = BTreeMap::from([
            ((26_056, 0), 5.0e21),
            ((92_235, 0), 1.0e20),
            ((92_238, 0), 3.0e20),
            ((89_227, 0), 7.0e19),
        ]);
        let awr = |key: (i32, i32)| Some(f64::from(key.0 % 1000) / NEUTRON_MASS_AMU);
        let heavy = heavy_metal(&inventory, awr);
        close(heavy.atoms_per_g, 4.0e20, 1e-14);
        // With AWR = A / m_n the mass of N atoms is N * A / N_A grams.
        close(
            heavy.mass_g_per_g.unwrap(),
            (1.0e20 * 235.0 + 3.0e20 * 238.0) / NA,
            1e-13,
        );
        assert!(heavy.without_mass.is_empty());
        let missing = heavy_metal(&inventory, |key| (key.0 != 92_238).then_some(235.0));
        assert_eq!(missing.mass_g_per_g, None);
        assert_eq!(missing.without_mass, vec![(92_238, 0)]);
        assert_eq!(missing.atoms_per_g, heavy.atoms_per_g);
    }

    #[test]
    fn sf_neutron_source_sums_decays_times_nubar_and_reports_uncovered_states() {
        // Synthetic decay rates and multiplicities: only the arithmetic is under test.
        let nubar = BTreeMap::from([
            ((98_252, 0), (3.0, "test table".to_string())),
            ((96_244, 0), (2.0, "test table".to_string())),
        ]);
        let decays = [
            ((98_252, 0), 1.0e6),
            ((96_244, 0), 5.0e5),
            ((92_238, 0), 40.0), // no nu-bar
            ((98_250, 0), 0.0),  // unpopulated
        ];
        let out = sf_neutron_source(&decays, &nubar);
        assert_eq!(out.neutrons_per_s_per_g, 1.0e6 * 3.0 + 5.0e5 * 2.0);
        assert_eq!(out.by_nuclide.len(), 2);
        assert_eq!(out.by_nuclide["Cf252"], 3.0e6);
        assert_eq!(out.by_nuclide["Cm244"], 1.0e6);
        assert_eq!(out.uncovered_sf_decays_per_s_per_g, 40.0);
        // A repeated state (two SF modes) accumulates into one entry.
        let twice = sf_neutron_source(&[((98_252, 0), 1.0), ((98_252, 0), 2.0)], &nubar);
        assert_eq!(twice.by_nuclide["Cf252"], 9.0);
    }

    #[test]
    fn nubar_table_requires_a_citation_and_explicit_nuclides() {
        let (source, table) = parse_nubar_table(
            r#"{"source": "synthetic test table", "nubar": {"Cf252": 3.5, "Am242m1": 2.5}}"#,
        )
        .expect("valid table");
        assert_eq!(source, "synthetic test table");
        assert_eq!(table[&(98_252, 0)], 3.5);
        assert_eq!(table[&(95_242, 1)], 2.5);
        for bad in [
            r#"{"source": "", "nubar": {"Cf252": 3.5}}"#,
            r#"{"nubar": {"Cf252": 3.5}}"#,
            r#"{"source": "x", "nubar": {}}"#,
            r#"{"source": "x", "nubar": {"Cf": 3.5}}"#,
            r#"{"source": "x", "nubar": {"Cf252": 0.0}}"#,
            r#"{"source": "x", "nubar": {"Cf252": 3.5}, "extra": 1}"#,
        ] {
            assert!(parse_nubar_table(bad).is_err(), "{bad}");
        }
    }
}
