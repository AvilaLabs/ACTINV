#![allow(non_snake_case)] // field names are the JSON wire format (Z, A, LISO, activity_Bq)
//! P122 complete step summary: FISPACT-II inventory-report parity. Every quantity is per gram of
//! initial material, except the `absolute` and `per_cm3` totals, which scale the per-gram totals by
//! `material.mass_g` and `material.density_g_cm3`. Emitted only when the spec carries a `summary`
//! section; absence leaves results byte-identical.
use crate::chain::BRANCHING_TOLERANCE;
use crate::run::{name_of, EV};
use crate::spec::SummaryOptions;
use actinv_data::{composition, decay::Nuclide};
use serde::Serialize;
use std::collections::{BTreeMap, HashMap};

/// Unified atomic mass constant in grams: 1.66053906660e-27 kg (CODATA 2018).
const AMU_G: f64 = 1.66053906660e-24;
/// Becquerel per curie, exact by definition (1 Ci = 3.7e10 s^-1).
const BQ_PER_CI: f64 = 3.7e10;
/// A cumulative share reaches a threshold when it is within this relative slack of it, so a
/// ranking that lands exactly on 90 % in exact arithmetic is not pushed one entry further by
/// the last bit of the running sum.
const CUMULATIVE_SLACK: f64 = 1e-12;

/// One state's population, as the run loop resolves it for the current step.
#[derive(Debug, Clone, Copy)]
pub struct StateAtoms {
    /// (ZA, LISO).
    pub key: (i32, i32),
    /// Atoms per gram of initial material.
    pub atoms: f64,
    /// True when the state is part of the initial material (present at t = 0).
    pub initial: bool,
}

/// One nuclide row of the step summary.
#[derive(Debug, Clone, Serialize)]
pub struct SummaryNuclide {
    pub nuclide: String,
    pub Z: i32,
    pub A: i32,
    pub LISO: i32,
    pub atoms: f64,
    /// `atoms` times the nuclide mass: decay-record AWR x neutron mass in u x the atomic mass
    /// constant, or A x u when there is no AWR (`mass_from_a`).
    pub grams: f64,
    pub mass_from_a: bool,
    /// lambda * N.
    pub activity_Bq: f64,
    /// Mean heavy-particle (alpha) decay energy times activity, the same split the heat output uses.
    pub alpha_W: f64,
    /// Mean light-particle (beta) decay energy times activity.
    pub beta_W: f64,
    /// Mean electromagnetic (gamma) decay energy times activity.
    pub gamma_W: f64,
    /// Null when the nuclide is stable or has no decay record.
    pub half_life_s: Option<f64>,
    /// A decay record exists and gives no decay constant. A state without a decay record is not
    /// reported stable: its stability is unknown, though the run treats it as non-decaying.
    pub stable: bool,
    /// Present in the initial material.
    pub initial: bool,
    /// False for a state the run carries without a decay record (its activity and heat are zero).
    pub decay_data: bool,
}

/// One element row (all nuclides of a Z summed).
#[derive(Debug, Clone, Serialize)]
pub struct SummaryElement {
    pub Z: i32,
    pub symbol: String,
    pub atoms: f64,
    pub grams: f64,
    pub activity_Bq: f64,
    pub alpha_W: f64,
    pub beta_W: f64,
    pub gamma_W: f64,
}

/// Activity partitioned by decay mode, the first digit of the decay record's RTYP: 1 beta-,
/// 2 electron capture / beta+, 3 isomeric transition, 4 alpha, 5 neutron, 6 spontaneous fission,
/// 7 proton, anything else `other`. Each nuclide's lambda * N is split by its mode branching
/// ratios, normalised to sum to one (a record whose positive ratios fall short of one by more
/// than the chain tolerance books the shortfall, like a record with no modes, to `other`), so the
/// eight entries sum to the total activity to rounding.
#[derive(Debug, Clone, Default, Serialize)]
pub struct ActivityByMode {
    pub beta_minus_Bq: f64,
    pub ec_beta_plus_Bq: f64,
    pub isomeric_transition_Bq: f64,
    pub alpha_Bq: f64,
    pub neutron_Bq: f64,
    pub spontaneous_fission_Bq: f64,
    pub proton_Bq: f64,
    pub other_Bq: f64,
}

/// The per-gram totals scaled by one factor: `material.mass_g` for `absolute`, the material
/// density (g/cm^3) for `per_cm3`.
#[derive(Debug, Clone, Serialize)]
pub struct SummaryScaled {
    pub multiplier: f64,
    pub activity_Bq: f64,
    pub activity_Ci: f64,
    pub alpha_W: f64,
    pub beta_W: f64,
    pub gamma_W: f64,
    pub heat_W: f64,
    pub activity_excl_H3_Bq: f64,
    pub heat_excl_H3_W: f64,
}

/// Step totals. They always include every state, whatever `min_atoms` / `include_stable` trim
/// from the nuclide table.
#[derive(Debug, Clone, Serialize)]
pub struct SummaryTotals {
    pub activity_Bq: f64,
    /// `activity_Bq` / 3.7e10 (1 Ci exactly).
    pub activity_Ci: f64,
    pub alpha_W: f64,
    pub beta_W: f64,
    pub gamma_W: f64,
    pub heat_W: f64,
    pub activity_by_mode: ActivityByMode,
    /// FISPACT-compatible alpha activity: the alpha-mode activity.
    pub alpha_Bq: f64,
    /// FISPACT-compatible beta activity: beta-, EC/beta+ and isomeric-transition mode activity.
    pub beta_Bq: f64,
    /// FISPACT-compatible gamma activity: the activity of every nuclide whose mean
    /// electromagnetic decay energy is positive (gamma_W > 0).
    pub gamma_Bq: f64,
    /// Activity with H-3 removed.
    pub activity_excl_H3_Bq: f64,
    /// Total heat with H-3 removed.
    pub heat_excl_H3_W: f64,
    /// Current total mass in grams per gram of initial material: a mass-closure diagnostic.
    pub mass_g: f64,
    /// Atoms per gram of initial material accumulated in the leakage sink.
    pub leakage_atoms: f64,
    /// States with a positive population (before any nuclide-table filter).
    pub n_nuclides: usize,
    /// The totals times `material.mass_g`.
    pub absolute: SummaryScaled,
    /// The totals times `material.density_g_cm3`; present only when a density is given.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub per_cm3: Option<SummaryScaled>,
}

#[derive(Debug, Clone, Serialize)]
pub struct DominantEntry {
    pub nuclide: String,
    pub value: f64,
    pub percent: f64,
    pub cumulative_percent: f64,
}

/// One ranked contribution list: the top `dominant_top` entries, plus how many nuclides of the
/// full ranking are needed to reach 90 % and 99 % of the total.
#[derive(Debug, Clone, Serialize)]
pub struct DominantList {
    /// The total the percentages are relative to (sum over the full ranking).
    pub total: f64,
    pub top: Vec<DominantEntry>,
    pub n_for_90_percent: usize,
    pub n_for_99_percent: usize,
}

#[derive(Debug, Clone, Serialize)]
pub struct SummaryDominant {
    pub activity: DominantList,
    pub heat: DominantList,
    pub alpha_heat: DominantList,
    pub beta_heat: DominantList,
    pub gamma_heat: DominantList,
}

/// P122 complete step summary.
#[derive(Debug, Clone, Serialize)]
pub struct StepSummary {
    /// Nuclide table, descending by atoms; absent when `summary.nuclides` is false.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub nuclides: Option<Vec<SummaryNuclide>>,
    pub totals: SummaryTotals,
    /// Element table, ascending by Z; absent when `summary.elements` is false.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub elements: Option<Vec<SummaryElement>>,
    pub dominant: SummaryDominant,
}

impl StepSummary {
    /// A summary with every optional block present, used only to learn the static JSON shape of
    /// `steps.summary.<key>` paths (see `mesh::maximal_step_out`).
    pub(crate) fn shape_placeholder() -> Self {
        build(
            &SummaryOptions::default(),
            &HashMap::new(),
            &[],
            0.0,
            1.0,
            Some(1.0),
        )
    }
}

const BETA_MINUS: usize = 0;
const EC_BETA_PLUS: usize = 1;
const ISOMERIC_TRANSITION: usize = 2;
const ALPHA: usize = 3;
const NEUTRON: usize = 4;
const SPONTANEOUS_FISSION: usize = 5;
const PROTON: usize = 6;
const OTHER: usize = 7;
const MODE_CLASSES: usize = 8;

/// Mode class of an ENDF RTYP: its first digit (1.5, beta- then neutron, is a beta- decay).
fn mode_class(rtyp: f64) -> usize {
    let leading = ((rtyp * 1e6).round() / 1e6).floor();
    if !(1.0..8.0).contains(&leading) {
        return OTHER;
    }
    match leading as i32 {
        1 => BETA_MINUS,
        2 => EC_BETA_PLUS,
        3 => ISOMERIC_TRANSITION,
        4 => ALPHA,
        5 => NEUTRON,
        6 => SPONTANEOUS_FISSION,
        7 => PROTON,
        _ => OTHER,
    }
}

/// Fraction of a decaying nuclide's activity going through each mode class; sums to one.
fn mode_fractions(nuclide: &Nuclide) -> [f64; MODE_CLASSES] {
    let mut fractions = [0.0f64; MODE_CLASSES];
    let assigned = nuclide
        .modes
        .iter()
        .map(|mode| mode.br)
        .filter(|br| *br > 0.0)
        .fold(0.0, |sum, br| sum + br);
    if assigned.is_nan() || assigned <= 0.0 {
        fractions[OTHER] = 1.0;
        return fractions;
    }
    // The chain books a shortfall past the tolerance to leakage and scales an excess away; here
    // the shortfall is the part with no mode, and a near-unit sum is normalised exactly.
    let (scale, shortfall) = if assigned < 1.0 && (assigned - 1.0).abs() > BRANCHING_TOLERANCE {
        (1.0, 1.0 - assigned)
    } else {
        (1.0 / assigned, 0.0)
    };
    for mode in nuclide.modes.iter().filter(|mode| mode.br > 0.0) {
        fractions[mode_class(mode.rtyp)] += mode.br * scale;
    }
    fractions[OTHER] += shortfall;
    fractions
}

fn is_h3(row: &SummaryNuclide) -> bool {
    row.Z == 1 && row.A == 3 && row.LISO == 0
}

fn scaled(
    multiplier: f64,
    activity: f64,
    alpha: f64,
    beta: f64,
    gamma: f64,
    activity_excl_h3: f64,
    heat_excl_h3: f64,
) -> SummaryScaled {
    SummaryScaled {
        multiplier,
        activity_Bq: activity * multiplier,
        activity_Ci: activity * multiplier / BQ_PER_CI,
        alpha_W: alpha * multiplier,
        beta_W: beta * multiplier,
        gamma_W: gamma * multiplier,
        heat_W: (alpha + beta + gamma) * multiplier,
        activity_excl_H3_Bq: activity_excl_h3 * multiplier,
        heat_excl_H3_W: heat_excl_h3 * multiplier,
    }
}

/// Rank `(name, value)` pairs by descending value and report the top entries and the 90 % / 99 %
/// counts over the whole ranking. Non-positive values do not rank.
fn dominant_list(mut entries: Vec<(String, f64)>, top: usize) -> DominantList {
    entries.retain(|(_, value)| *value > 0.0);
    entries.sort_by(|a, b| b.1.total_cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    let total: f64 = entries.iter().map(|(_, value)| value).sum();
    let mut list = DominantList {
        total,
        top: Vec::new(),
        n_for_90_percent: 0,
        n_for_99_percent: 0,
    };
    if total <= 0.0 {
        return list;
    }
    let reaches =
        |cumulative: f64, share: f64| cumulative >= share * total * (1.0 - CUMULATIVE_SLACK);
    let (mut cumulative, mut n90, mut n99) = (0.0f64, None, None);
    for (rank, (name, value)) in entries.iter().enumerate() {
        cumulative += value;
        if n90.is_none() && reaches(cumulative, 0.90) {
            n90 = Some(rank + 1);
        }
        if n99.is_none() && reaches(cumulative, 0.99) {
            n99 = Some(rank + 1);
        }
        if rank < top {
            list.top.push(DominantEntry {
                nuclide: name.clone(),
                value: *value,
                percent: 100.0 * value / total,
                cumulative_percent: 100.0 * cumulative / total,
            });
        }
    }
    list.n_for_90_percent = n90.unwrap_or(entries.len());
    list.n_for_99_percent = n99.unwrap_or(entries.len());
    list
}

/// Build the step summary from the resolved per-state populations. States with no positive
/// population are ignored; a state without a decay record is included with `decay_data: false`.
/// `mass_g` is `material.mass_g` (the `absolute` multiplier and the `min_atoms` scale) and
/// `density_g_cm3` is `material.density_g_cm3`.
pub fn build(
    options: &SummaryOptions,
    nuclides: &HashMap<(i32, i32), Nuclide>,
    states: &[StateAtoms],
    leakage_atoms: f64,
    mass_g: f64,
    density_g_cm3: Option<f64>,
) -> StepSummary {
    let mut rows: Vec<(SummaryNuclide, [f64; MODE_CLASSES])> = Vec::with_capacity(states.len());
    for state in states.iter().filter(|state| state.atoms > 0.0) {
        let (za, liso) = state.key;
        let record = nuclides.get(&state.key);
        let awr = record
            .map(|nuclide| nuclide.awr)
            .filter(|awr| awr.is_finite() && *awr > 0.0);
        let (atom_mass_g, mass_from_a) = match awr {
            Some(awr) => (awr * composition::NEUTRON_MASS_AMU * AMU_G, false),
            None => (f64::from(za % 1000) * AMU_G, true),
        };
        let lambda = record.map_or(0.0, Nuclide::lambda);
        let activity = lambda * state.atoms;
        let (alpha, beta, gamma) = match record {
            Some(nuclide) if lambda > 0.0 => (
                activity * nuclide.e_heavy() * EV,
                activity * nuclide.e_light() * EV,
                activity * nuclide.e_em() * EV,
            ),
            _ => (0.0, 0.0, 0.0),
        };
        let mut modes = [0.0f64; MODE_CLASSES];
        if let Some(nuclide) = record.filter(|_| lambda > 0.0) {
            for (slot, fraction) in modes.iter_mut().zip(mode_fractions(nuclide)) {
                *slot = activity * fraction;
            }
        }
        rows.push((
            SummaryNuclide {
                nuclide: name_of(za, liso),
                Z: za / 1000,
                A: za % 1000,
                LISO: liso,
                atoms: state.atoms,
                grams: state.atoms * atom_mass_g,
                mass_from_a,
                activity_Bq: activity,
                alpha_W: alpha,
                beta_W: beta,
                gamma_W: gamma,
                half_life_s: record.filter(|_| lambda > 0.0).map(|n| n.half_life),
                stable: record.is_some() && lambda == 0.0,
                initial: state.initial,
                decay_data: record.is_some(),
            },
            modes,
        ));
    }
    // Descending atoms, ties by (ZA, LISO): a fixed order makes every sum below reproducible.
    rows.sort_by(|a, b| {
        b.0.atoms
            .total_cmp(&a.0.atoms)
            .then_with(|| (a.0.Z, a.0.A, a.0.LISO).cmp(&(b.0.Z, b.0.A, b.0.LISO)))
    });

    let (mut activity, mut alpha, mut beta, mut gamma) = (0.0f64, 0.0f64, 0.0f64, 0.0f64);
    let (mut activity_excl_h3, mut heat_excl_h3, mut grams, mut gamma_activity) =
        (0.0f64, 0.0f64, 0.0f64, 0.0f64);
    let mut by_mode = [0.0f64; MODE_CLASSES];
    let mut element_rows: BTreeMap<i32, SummaryElement> = BTreeMap::new();
    for (row, modes) in &rows {
        activity += row.activity_Bq;
        alpha += row.alpha_W;
        beta += row.beta_W;
        gamma += row.gamma_W;
        grams += row.grams;
        if row.gamma_W > 0.0 {
            gamma_activity += row.activity_Bq;
        }
        if !is_h3(row) {
            activity_excl_h3 += row.activity_Bq;
            heat_excl_h3 += row.alpha_W + row.beta_W + row.gamma_W;
        }
        for (total, part) in by_mode.iter_mut().zip(modes) {
            *total += part;
        }
        let element = element_rows.entry(row.Z).or_insert_with(|| SummaryElement {
            Z: row.Z,
            symbol: composition::symbol_of(row.Z).to_string(),
            atoms: 0.0,
            grams: 0.0,
            activity_Bq: 0.0,
            alpha_W: 0.0,
            beta_W: 0.0,
            gamma_W: 0.0,
        });
        element.atoms += row.atoms;
        element.grams += row.grams;
        element.activity_Bq += row.activity_Bq;
        element.alpha_W += row.alpha_W;
        element.beta_W += row.beta_W;
        element.gamma_W += row.gamma_W;
    }

    let top = options.dominant_top;
    let ranked = |value: fn(&SummaryNuclide) -> f64| -> DominantList {
        dominant_list(
            rows.iter()
                .map(|(row, _)| (row.nuclide.clone(), value(row)))
                .collect(),
            top,
        )
    };
    let dominant = SummaryDominant {
        activity: ranked(|row| row.activity_Bq),
        heat: ranked(|row| row.alpha_W + row.beta_W + row.gamma_W),
        alpha_heat: ranked(|row| row.alpha_W),
        beta_heat: ranked(|row| row.beta_W),
        gamma_heat: ranked(|row| row.gamma_W),
    };

    let totals = SummaryTotals {
        activity_Bq: activity,
        activity_Ci: activity / BQ_PER_CI,
        alpha_W: alpha,
        beta_W: beta,
        gamma_W: gamma,
        heat_W: alpha + beta + gamma,
        activity_by_mode: ActivityByMode {
            beta_minus_Bq: by_mode[BETA_MINUS],
            ec_beta_plus_Bq: by_mode[EC_BETA_PLUS],
            isomeric_transition_Bq: by_mode[ISOMERIC_TRANSITION],
            alpha_Bq: by_mode[ALPHA],
            neutron_Bq: by_mode[NEUTRON],
            spontaneous_fission_Bq: by_mode[SPONTANEOUS_FISSION],
            proton_Bq: by_mode[PROTON],
            other_Bq: by_mode[OTHER],
        },
        alpha_Bq: by_mode[ALPHA],
        beta_Bq: by_mode[BETA_MINUS] + by_mode[EC_BETA_PLUS] + by_mode[ISOMERIC_TRANSITION],
        gamma_Bq: gamma_activity,
        activity_excl_H3_Bq: activity_excl_h3,
        heat_excl_H3_W: heat_excl_h3,
        mass_g: grams,
        leakage_atoms,
        n_nuclides: rows.len(),
        absolute: scaled(
            mass_g,
            activity,
            alpha,
            beta,
            gamma,
            activity_excl_h3,
            heat_excl_h3,
        ),
        per_cm3: density_g_cm3.map(|density| {
            scaled(
                density,
                activity,
                alpha,
                beta,
                gamma,
                activity_excl_h3,
                heat_excl_h3,
            )
        }),
    };

    // The filters trim the nuclide table only; the totals above already include every row.
    let nuclide_table = options.nuclides.then(|| {
        rows.into_iter()
            .map(|(row, _)| row)
            .filter(|row| options.include_stable || !row.stable)
            .filter(|row| row.atoms * mass_g >= options.min_atoms)
            .collect()
    });
    let elements = options
        .elements
        .then(|| element_rows.into_values().collect());
    StepSummary {
        nuclides: nuclide_table,
        totals,
        elements,
        dominant,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use actinv_data::decay::Mode;

    fn mode(rtyp: f64, br: f64) -> Mode {
        Mode {
            rtyp,
            rfs: 0.0,
            q: 0.0,
            dq: 0.0,
            br,
            dbr: 0.0,
        }
    }

    /// `energies` = [light, dlight, em, dem, heavy, dheavy] in eV.
    fn record(za: i32, awr: f64, half_life: f64, energies: [f64; 6], modes: Vec<Mode>) -> Nuclide {
        Nuclide {
            mat: 0,
            za,
            awr,
            liso: 0,
            nst: i32::from(half_life <= 0.0),
            half_life,
            d_half_life: 0.0,
            energies: energies.to_vec(),
            modes,
            spectra: Vec::new(),
        }
    }

    /// Co-60 (beta- 100 %), Am-241 (alpha 99 %, SF 1 %), H-3 (beta-) and stable Fe-56.
    fn library() -> HashMap<(i32, i32), Nuclide> {
        let co60 = record(
            27060,
            59.3383,
            1.6632e8,
            [9.6e4, 0.0, 2.5e6, 0.0, 0.0, 0.0],
            vec![mode(1.0, 1.0)],
        );
        let am241 = record(
            95241,
            238.6,
            1.364e10,
            [5.2e4, 0.0, 3.2e4, 0.0, 5.5e6, 0.0],
            vec![mode(4.0, 0.99), mode(6.0, 0.01)],
        );
        let h3 = record(
            1003,
            2.9901,
            3.8878e8,
            [5.7e3, 0.0, 0.0, 0.0, 0.0, 0.0],
            vec![mode(1.0, 1.0)],
        );
        let fe56 = record(26056, 55.4545, 0.0, [0.0; 6], Vec::new());
        HashMap::from([
            ((27060, 0), co60),
            ((95241, 0), am241),
            ((1003, 0), h3),
            ((26056, 0), fe56),
        ])
    }

    fn states() -> Vec<StateAtoms> {
        vec![
            StateAtoms {
                key: (26056, 0),
                atoms: 1.0e22,
                initial: true,
            },
            StateAtoms {
                key: (27060, 0),
                atoms: 1.0e15,
                initial: false,
            },
            StateAtoms {
                key: (95241, 0),
                atoms: 1.0e12,
                initial: false,
            },
            StateAtoms {
                key: (1003, 0),
                atoms: 1.0e13,
                initial: false,
            },
        ]
    }

    fn close(a: f64, b: f64) -> bool {
        (a - b).abs() <= 1e-12 * a.abs().max(b.abs()).max(f64::MIN_POSITIVE)
    }

    #[test]
    fn totals_follow_the_decay_records() {
        let nuclides = library();
        let summary = build(
            &SummaryOptions::default(),
            &nuclides,
            &states(),
            2.5,
            4.0,
            Some(7.8),
        );
        let lambda = |key: (i32, i32)| nuclides[&key].lambda();
        let co = lambda((27060, 0)) * 1.0e15;
        let am = lambda((95241, 0)) * 1.0e12;
        let h3 = lambda((1003, 0)) * 1.0e13;
        let totals = &summary.totals;
        assert!(close(totals.activity_Bq, co + am + h3));
        assert!(close(totals.activity_Ci, (co + am + h3) / 3.7e10));
        assert!(close(totals.activity_excl_H3_Bq, co + am));
        assert!(close(totals.alpha_W, am * 5.5e6 * EV));
        assert!(close(
            totals.beta_W,
            (co * 9.6e4 + am * 5.2e4 + h3 * 5.7e3) * EV
        ));
        assert!(close(totals.gamma_W, (co * 2.5e6 + am * 3.2e4) * EV));
        assert!(close(
            totals.heat_W,
            totals.alpha_W + totals.beta_W + totals.gamma_W
        ));
        assert!(close(
            totals.heat_excl_H3_W,
            totals.heat_W - h3 * 5.7e3 * EV
        ));
        // FISPACT-style splits: alpha is the alpha mode, beta the beta-/EC/IT modes, gamma the
        // activity of nuclides with a positive electromagnetic energy (Co-60 and Am-241).
        assert!(close(totals.alpha_Bq, am * 0.99));
        assert!(close(totals.beta_Bq, co + h3));
        assert!(close(totals.gamma_Bq, co + am));
        assert_eq!(totals.leakage_atoms, 2.5);
        assert_eq!(totals.n_nuclides, 4);
        assert_eq!(totals.absolute.multiplier, 4.0);
        assert!(close(totals.absolute.activity_Bq, 4.0 * totals.activity_Bq));
        assert!(close(totals.absolute.heat_W, 4.0 * totals.heat_W));
        let per_cm3 = totals.per_cm3.as_ref().expect("density was given");
        assert!(close(per_cm3.activity_Bq, 7.8 * totals.activity_Bq));
        assert!(close(per_cm3.heat_excl_H3_W, 7.8 * totals.heat_excl_H3_W));
        let no_density = build(
            &SummaryOptions::default(),
            &nuclides,
            &states(),
            0.0,
            1.0,
            None,
        );
        assert!(no_density.totals.per_cm3.is_none());
        let json = serde_json::to_value(&no_density).unwrap();
        assert!(json["totals"].get("per_cm3").is_none());
    }

    #[test]
    fn activity_by_mode_partitions_the_total() {
        let nuclides = library();
        let summary = build(
            &SummaryOptions::default(),
            &nuclides,
            &states(),
            0.0,
            1.0,
            None,
        );
        let modes = &summary.totals.activity_by_mode;
        let parts = modes.beta_minus_Bq
            + modes.ec_beta_plus_Bq
            + modes.isomeric_transition_Bq
            + modes.alpha_Bq
            + modes.neutron_Bq
            + modes.spontaneous_fission_Bq
            + modes.proton_Bq
            + modes.other_Bq;
        assert!(close(parts, summary.totals.activity_Bq));
        let am = nuclides[&(95241, 0)].lambda() * 1.0e12;
        assert!(close(modes.spontaneous_fission_Bq, am * 0.01));
        assert_eq!(modes.other_Bq, 0.0);
    }

    #[test]
    fn mode_partition_handles_chained_codes_and_short_branching() {
        // beta- then neutron (1.5) is a beta- decay; 90 % assigned leaves 10 % in `other`
        let short = record(
            36090,
            89.0,
            32.3,
            [0.0; 6],
            vec![mode(1.5, 0.6), mode(2.0, 0.3)],
        );
        let fractions = mode_fractions(&short);
        assert!(close(fractions[BETA_MINUS], 0.6));
        assert!(close(fractions[EC_BETA_PLUS], 0.3));
        assert!(close(fractions[OTHER], 0.1));
        assert!(close(fractions.iter().sum::<f64>(), 1.0));
        // a sum within the chain tolerance is renormalised, an excess scaled away
        let near = record(
            1,
            1.0,
            1.0,
            [0.0; 6],
            vec![mode(1.0, 0.5), mode(4.0, 0.500001)],
        );
        let fractions = mode_fractions(&near);
        assert_eq!(fractions[OTHER], 0.0);
        assert!(close(fractions.iter().sum::<f64>(), 1.0));
        let excess = record(1, 1.0, 1.0, [0.0; 6], vec![mode(1.0, 1.0), mode(4.0, 1.0)]);
        let fractions = mode_fractions(&excess);
        assert!(close(fractions[BETA_MINUS], 0.5));
        assert!(close(fractions[ALPHA], 0.5));
        // no mode records at all: everything is `other`
        let bare = record(1, 1.0, 1.0, [0.0; 6], Vec::new());
        assert_eq!(mode_fractions(&bare)[OTHER], 1.0);
        assert_eq!(mode_class(10.0), OTHER);
        assert_eq!(mode_class(0.0), OTHER);
        assert_eq!(mode_class(7.0), PROTON);
    }

    #[test]
    fn min_atoms_and_nostable_trim_only_the_table() {
        let nuclides = library();
        let full = build(
            &SummaryOptions::default(),
            &nuclides,
            &states(),
            0.0,
            1.0,
            None,
        );
        assert_eq!(full.nuclides.as_ref().unwrap().len(), 4);
        // atoms are sorted descending
        let names: Vec<_> = full
            .nuclides
            .as_ref()
            .unwrap()
            .iter()
            .map(|row| row.nuclide.as_str())
            .collect();
        assert_eq!(names, ["Fe56", "Co60", "H3", "Am241"]);

        // MIND scales by the material mass: 1e13 atoms/g x 0.5 g = 5e12 < 6e12 drops H-3 and Am-241
        let trimmed = build(
            &SummaryOptions {
                min_atoms: 6.0e12,
                include_stable: false,
                ..SummaryOptions::default()
            },
            &nuclides,
            &states(),
            0.0,
            0.5,
            None,
        );
        let kept: Vec<_> = trimmed
            .nuclides
            .as_ref()
            .unwrap()
            .iter()
            .map(|row| row.nuclide.as_str())
            .collect();
        // Fe-56 (stable) and the rows under the threshold are gone
        assert_eq!(kept, ["Co60"]);
        assert_eq!(trimmed.totals.n_nuclides, 4);
        assert_eq!(trimmed.totals.activity_Bq, full.totals.activity_Bq);
        assert_eq!(trimmed.totals.heat_W, full.totals.heat_W);
        assert_eq!(trimmed.totals.mass_g, full.totals.mass_g);
        assert_eq!(
            trimmed.dominant.activity.total,
            full.dominant.activity.total
        );
        assert_eq!(trimmed.elements.as_ref().unwrap().len(), 4);

        let none = build(
            &SummaryOptions {
                nuclides: false,
                elements: false,
                ..SummaryOptions::default()
            },
            &nuclides,
            &states(),
            0.0,
            1.0,
            None,
        );
        assert!(none.nuclides.is_none());
        assert!(none.elements.is_none());
        let json = serde_json::to_value(&none).unwrap();
        assert!(json.get("nuclides").is_none() && json.get("elements").is_none());
    }

    #[test]
    fn dominant_lists_rank_with_cumulative_percent() {
        let nuclides = library();
        let summary = build(
            &SummaryOptions::default(),
            &nuclides,
            &states(),
            0.0,
            1.0,
            None,
        );
        let activity = &summary.dominant.activity;
        let lambda = |key: (i32, i32)| nuclides[&key].lambda();
        let mut values = [
            ("Co60", lambda((27060, 0)) * 1.0e15),
            ("H3", lambda((1003, 0)) * 1.0e13),
            ("Am241", lambda((95241, 0)) * 1.0e12),
        ];
        values.sort_by(|a, b| b.1.total_cmp(&a.1));
        let total: f64 = values.iter().map(|v| v.1).sum();
        assert!(close(activity.total, total));
        // Fe-56 is stable: it does not rank
        assert_eq!(activity.top.len(), 3);
        assert_eq!(activity.top[0].nuclide, values[0].0);
        assert!(close(activity.top[0].percent, 100.0 * values[0].1 / total));
        assert!(close(
            activity.top[1].cumulative_percent,
            100.0 * (values[0].1 + values[1].1) / total
        ));
        assert!(close(activity.top[2].cumulative_percent, 100.0));
        assert!(activity.top.windows(2).all(|w| w[0].value >= w[1].value));
        // Co-60 alone carries almost all the activity: one nuclide reaches 90 % and 99 %
        assert!(values[0].1 / total > 0.99);
        assert_eq!(activity.n_for_90_percent, 1);
        assert_eq!(activity.n_for_99_percent, 1);
        // alpha heat: only Am-241
        assert_eq!(summary.dominant.alpha_heat.top.len(), 1);
        assert_eq!(summary.dominant.alpha_heat.top[0].nuclide, "Am241");
        assert!(close(summary.dominant.alpha_heat.top[0].percent, 100.0));
        // gamma heat: Co-60 and Am-241
        assert_eq!(summary.dominant.gamma_heat.top.len(), 2);

        // truncation keeps the counts over the full ranking
        let capped = build(
            &SummaryOptions {
                dominant_top: 1,
                ..SummaryOptions::default()
            },
            &nuclides,
            &states(),
            0.0,
            1.0,
            None,
        );
        assert_eq!(capped.dominant.heat.top.len(), 1);
        assert_eq!(
            capped.dominant.heat.n_for_99_percent,
            summary.dominant.heat.n_for_99_percent
        );
    }

    #[test]
    fn ninety_percent_counts_use_the_full_ranking() {
        // 50, 30, 15, 4, 1: cumulative 50, 80, 95, 99, 100
        let entries = ["a", "b", "c", "d", "e"]
            .into_iter()
            .zip([50.0, 30.0, 15.0, 4.0, 1.0])
            .map(|(name, value)| (name.to_string(), value))
            .collect();
        let list = dominant_list(entries, 2);
        assert_eq!(list.top.len(), 2);
        assert_eq!(list.n_for_90_percent, 3);
        assert_eq!(list.n_for_99_percent, 4);
        assert!(close(list.top[1].cumulative_percent, 80.0));
        // an exact 90 % boundary is reached at that entry, not the next
        let exact = dominant_list(vec![("x".into(), 9.0), ("y".into(), 1.0)], 5);
        assert_eq!(exact.n_for_90_percent, 1);
        assert_eq!(exact.n_for_99_percent, 2);
        // nothing positive: empty list, zero counts
        let empty = dominant_list(vec![("x".into(), 0.0)], 5);
        assert!(empty.top.is_empty());
        assert_eq!((empty.n_for_90_percent, empty.n_for_99_percent), (0, 0));
    }

    #[test]
    fn elements_sum_their_isotopes_in_z_order() {
        let mut nuclides = library();
        nuclides.insert(
            (26055, 0),
            record(
                26055,
                54.4,
                8.6e7,
                [4.2e3, 0.0, 1.6e3, 0.0, 0.0, 0.0],
                vec![mode(2.0, 1.0)],
            ),
        );
        let mut states = states();
        states.push(StateAtoms {
            key: (26055, 0),
            atoms: 3.0e14,
            initial: false,
        });
        let summary = build(
            &SummaryOptions::default(),
            &nuclides,
            &states,
            0.0,
            1.0,
            None,
        );
        let elements = summary.elements.as_ref().unwrap();
        let z: Vec<_> = elements.iter().map(|e| e.Z).collect();
        assert_eq!(z, [1, 26, 27, 95]);
        let fe = &elements[1];
        assert_eq!(fe.symbol, "Fe");
        assert!(close(fe.atoms, 1.0e22 + 3.0e14));
        let fe55 = nuclides[&(26055, 0)].lambda() * 3.0e14;
        assert!(close(fe.activity_Bq, fe55));
        assert!(close(fe.beta_W, fe55 * 4.2e3 * EV));
        assert!(close(fe.gamma_W, fe55 * 1.6e3 * EV));
        let nuclide_grams = summary
            .nuclides
            .as_ref()
            .unwrap()
            .iter()
            .filter(|row| row.Z == 26)
            .map(|row| row.grams)
            .sum::<f64>();
        assert!(close(fe.grams, nuclide_grams));
        // element rows add up to the totals
        let element_activity: f64 = elements.iter().map(|e| e.activity_Bq).sum();
        assert!(close(element_activity, summary.totals.activity_Bq));
        let element_grams: f64 = elements.iter().map(|e| e.grams).sum();
        assert!(close(element_grams, summary.totals.mass_g));
        // the EC branch of Fe-55 lands in the EC/beta+ bin
        assert!(close(summary.totals.activity_by_mode.ec_beta_plus_Bq, fe55));
    }

    #[test]
    fn mass_uses_awr_and_falls_back_to_the_mass_number() {
        let nuclides = library();
        let states = [
            StateAtoms {
                key: (27060, 0),
                atoms: 2.0e20,
                initial: true,
            },
            // no decay record: H-1 stand-in or a bulk nuclide absent from the chain
            StateAtoms {
                key: (1001, 0),
                atoms: 5.0e20,
                initial: true,
            },
            // zero population never appears
            StateAtoms {
                key: (95241, 0),
                atoms: 0.0,
                initial: false,
            },
        ];
        let summary = build(
            &SummaryOptions::default(),
            &nuclides,
            &states,
            0.0,
            1.0,
            None,
        );
        let rows = summary.nuclides.as_ref().unwrap();
        assert_eq!(rows.len(), 2);
        let h1 = rows.iter().find(|row| row.nuclide == "H1").unwrap();
        assert!(h1.mass_from_a && !h1.decay_data && !h1.stable);
        assert!(h1.half_life_s.is_none());
        assert_eq!(h1.activity_Bq, 0.0);
        assert!(h1.initial);
        assert!(close(h1.grams, 5.0e20 * 1.0 * 1.66053906660e-24));
        let co = rows.iter().find(|row| row.nuclide == "Co60").unwrap();
        assert!(!co.mass_from_a && co.decay_data && !co.stable);
        assert_eq!(co.half_life_s, Some(1.6632e8));
        assert!(close(
            co.grams,
            2.0e20 * 59.3383 * 1.00866491595 * 1.66053906660e-24
        ));
        assert!(close(summary.totals.mass_g, h1.grams + co.grams));
        assert_eq!(summary.totals.n_nuclides, 2);
        // a zero or non-finite AWR also falls back to A x u
        let mut odd = library();
        odd.get_mut(&(27060, 0)).unwrap().awr = 0.0;
        let summary = build(&SummaryOptions::default(), &odd, &states, 0.0, 1.0, None);
        let co = summary
            .nuclides
            .as_ref()
            .unwrap()
            .iter()
            .find(|row| row.nuclide == "Co60")
            .unwrap();
        assert!(co.mass_from_a);
        assert!(close(co.grams, 2.0e20 * 60.0 * 1.66053906660e-24));
    }

    #[test]
    fn stable_records_are_flagged_and_have_no_half_life() {
        let summary = build(
            &SummaryOptions::default(),
            &library(),
            &states(),
            0.0,
            1.0,
            None,
        );
        let fe = summary
            .nuclides
            .as_ref()
            .unwrap()
            .iter()
            .find(|row| row.nuclide == "Fe56")
            .unwrap();
        assert!(fe.stable && fe.decay_data && fe.initial);
        assert!(fe.half_life_s.is_none());
        let json = serde_json::to_value(fe).unwrap();
        assert!(json["half_life_s"].is_null());
        assert_eq!(fe.activity_Bq, 0.0);
    }

    #[test]
    fn shape_placeholder_carries_every_optional_block() {
        let json = serde_json::to_value(StepSummary::shape_placeholder()).unwrap();
        for key in ["nuclides", "totals", "elements", "dominant"] {
            assert!(json.get(key).is_some(), "{key}");
        }
        assert!(json["totals"].get("per_cm3").is_some());
        assert!(json["dominant"]["activity"].get("top").is_some());
    }
}
