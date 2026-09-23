//! Transmutation network: decay matrix from the decay sublibraries, reaction columns from the activation library,
//! and the trace formulation (constant bulk as a source through a unit state). Mirrors controls/chain.py and the
//! trace formulation of controls/run_fns.py, which the P5-G4 control checks to 1e-12 on 132 experiments.
use crate::quantity::{CrossSectionBarns, ParticleFlux, RatePerBarnSecond};
use actinv_data::decay::Nuclide;
use actinv_data::fission::EffectiveYields;
use actinv_data::library::ReactionLibrary;
use std::collections::{BTreeMap, HashMap};

/// Elementary decay steps by ENDF RTYP digit: (dZ, dA).
fn step_of(d: u32) -> Option<(i32, i32)> {
    match d {
        1 => Some((1, 0)),
        2 => Some((-1, 0)),
        3 => Some((0, 0)),
        4 => Some((-2, -4)),
        5 => Some((0, -1)),
        7 => Some((-1, -1)),
        _ => None,
    }
}

/// RTYP is a decimal chain of elementary modes: 1.5 means beta- then neutron emission.
fn rtyp_digits(rtyp: f64) -> Vec<u32> {
    let s = format!("{rtyp:.6}");
    let s = s.trim_end_matches('0').trim_end_matches('.');
    s.chars()
        .filter(|c| c.is_ascii_digit())
        .map(|c| c.to_digit(10).unwrap())
        .collect()
}

pub struct Chain {
    /// (ZA, LISO) -> matrix index
    pub index: HashMap<(i32, i32), usize>,
    pub keys: Vec<(i32, i32)>,
    pub lambda: Vec<f64>,
    /// decay triplets (row, col, value), including the leakage row
    pub decay: Vec<(usize, usize, f64)>,
    pub leak: usize,
    pub unit: usize,
    pub n: usize,
    pub ledger: ChainLedger,
}

#[derive(Default, Debug)]
pub struct ChainLedger {
    pub sf_branches: usize,
    pub unknown_modes: usize,
    pub daughters_missing: Vec<(i32, i32, f64)>,
    /// Radioactive states whose positive branching ratios sum further than `BRANCHING_TOLERANCE`
    /// from 1, with that sum. A shortfall is booked to leakage; an excess is scaled away.
    pub branching_sums: Vec<((i32, i32), f64)>,
}

/// Evaluations round each branching ratio to about six figures, leaving sums within ~1e-6 of 1
/// (ENDF/B-VIII.0 decay: at most 9e-7). Past this a branch is missing or duplicated: JEFF-3.3 and
/// UKDD-2020 each carry states summing to 0.9 or less, and 1.01.
pub const BRANCHING_TOLERANCE: f64 = 1e-5;

/// Build the decay network. Index order is (ZA, LISO) ascending, matching controls/chain.py.
pub fn build(nuclides: &HashMap<(i32, i32), Nuclide>) -> Chain {
    let mut keys: Vec<(i32, i32)> = nuclides.keys().copied().collect();
    keys.sort();
    let index: HashMap<(i32, i32), usize> = keys.iter().enumerate().map(|(i, k)| (*k, i)).collect();
    let n_nuc = keys.len();
    let (leak, unit) = (n_nuc, n_nuc + 1);
    let n = n_nuc + 2;
    let mut lambda = vec![0.0; n_nuc];
    let mut trip: Vec<(usize, usize, f64)> = Vec::with_capacity(4 * n_nuc);
    let mut led = ChainLedger::default();
    for (k, key) in keys.iter().enumerate() {
        let nu = &nuclides[key];
        let l = nu.lambda();
        lambda[k] = l;
        if l == 0.0 {
            continue;
        }
        trip.push((k, k, -l));
        // Branches must carry exactly the loss on the diagonal, or atoms silently vanish or appear.
        let assigned: f64 = nu.modes.iter().map(|md| md.br).filter(|br| *br > 0.0).sum();
        let mut scale = 1.0;
        if (assigned - 1.0).abs() > BRANCHING_TOLERANCE {
            led.branching_sums.push((*key, assigned));
            if assigned < 1.0 {
                trip.push((leak, k, l * (1.0 - assigned)));
            } else {
                scale = 1.0 / assigned;
            }
        }
        let (z, a) = (nu.za / 1000, nu.za % 1000);
        for md in &nu.modes {
            if md.br <= 0.0 {
                continue;
            }
            let rate = l * md.br * scale;
            let digits = rtyp_digits(md.rtyp);
            let (mut zz, mut aa) = (z, a);
            let mut bad = false;
            for d in digits {
                if d == 6 {
                    led.sf_branches += 1;
                    bad = true;
                    break;
                } // spontaneous fission: no yields yet
                match step_of(d) {
                    Some((dz, da)) => {
                        zz += dz;
                        aa += da;
                    }
                    None => {
                        led.unknown_modes += 1;
                        bad = true;
                        break;
                    }
                }
            }
            if bad {
                trip.push((leak, k, rate));
                continue;
            }
            let want = (zz * 1000 + aa, md.rfs.round() as i32);
            let j = index.get(&want).or_else(|| index.get(&(zz * 1000 + aa, 0)));
            match j {
                // An isomeric transition to an absent level falls back to the ground state; from the
                // ground state that is the parent itself, which is no decay at all.
                Some(&j) if j != k => trip.push((j, k, rate)),
                _ => {
                    led.daughters_missing.push((want.0, want.1, rate));
                    trip.push((leak, k, rate));
                }
            }
        }
    }
    Chain {
        index,
        keys,
        lambda,
        decay: trip,
        leak,
        unit,
        n,
        ledger: led,
    }
}

#[derive(Clone, Debug, PartialEq, serde::Serialize)]
pub struct FissionProductLeakage {
    pub parent: String,
    pub product: String,
    pub yield_value: f64,
    pub production_rate_per_parent_s: f64,
}

#[derive(Clone, Debug, PartialEq, serde::Serialize)]
pub struct FissionBalance {
    pub fission_rate_per_parent_s: f64,
    pub raw_yield_sum: f64,
    pub mapped_yield_sum: f64,
    pub leakage_yield_sum: f64,
}

#[derive(Default, Debug, PartialEq)]
pub struct RateLedger {
    pub products_no_decay_data: BTreeMap<String, f64>,
    pub fission_no_yields: BTreeMap<String, f64>,
    pub fission_product_leakage: Vec<FissionProductLeakage>,
    pub fission_balance: BTreeMap<String, FissionBalance>,
    pub products_unmapped: BTreeMap<String, f64>,
    pub isomer_fell_back_to_ground: BTreeMap<String, f64>,
    pub targets_absent_from_decay_lib: Vec<(i32, i32)>,
    pub bulk_production_dropped: Vec<(String, String, f64)>,
    pub burnup_optical_depth_max: f64,
    pub burnup_fraction_max: f64,
    pub burnup_nuclide: Option<(i32, i32)>,
}

#[derive(Clone, Copy, Debug)]
pub struct ReactionDerivative {
    pub library_row: usize,
    pub row: usize,
    pub column: usize,
    /// Matrix derivative with respect to the row's collapsed cross section, in s^-1 barn^-1.
    pub per_barn_s: f64,
}

#[derive(Clone, Copy, Debug)]
pub struct YieldDerivative {
    /// Fission parent (ZA, LISO) whose independent yield is differentiated.
    pub parent: (i32, i32),
    /// Fission product (ZA, state) the yield feeds; the destination row may be
    /// the chain's leak state when the product is absent from the decay library.
    pub product: (i32, i32),
    pub row: usize,
    pub column: usize,
    /// Matrix derivative with respect to the independent yield, in s^-1.
    /// Equals the parent's total fission rate for that library row.
    pub per_yield_s: f64,
}

#[derive(Debug)]
pub struct ReactionAssembly {
    pub triplets: Vec<(usize, usize, f64)>,
    pub derivatives: Vec<ReactionDerivative>,
    /// Per-(parent, product) independent-yield matrix directions, populated
    /// under the same flag as `derivatives`.
    pub yield_derivatives: Vec<YieldDerivative>,
}

/// Reaction rates per atom (1/s) for every library target under a group flux, as triplets over the chain's indices.
/// `lib_targets[i]` is the (ZA, LISO) of library target index i.
#[allow(clippy::too_many_arguments)]
pub fn reaction_rates<L: ReactionLibrary + ?Sized>(
    lib: &L,
    lib_targets: &[(i32, i32)],
    phi: &[f64],
    chain: &Chain,
    fission_yields: &HashMap<(i32, i32), EffectiveYields>,
    led: &mut RateLedger,
    shield: Option<&crate::shielding::ShieldPlan>,
    rate_scale: Option<&HashMap<usize, f64>>,
) -> Vec<(usize, usize, f64)> {
    assemble_reaction_rates(
        lib,
        lib_targets,
        phi,
        chain,
        fission_yields,
        led,
        false,
        shield,
        rate_scale,
    )
    .triplets
}

/// Reaction-rate assembly plus the exact matrix contribution of every activation-library row.
#[allow(clippy::too_many_arguments)]
pub fn reaction_rates_with_derivatives<L: ReactionLibrary + ?Sized>(
    lib: &L,
    lib_targets: &[(i32, i32)],
    phi: &[f64],
    chain: &Chain,
    fission_yields: &HashMap<(i32, i32), EffectiveYields>,
    led: &mut RateLedger,
    shield: Option<&crate::shielding::ShieldPlan>,
    rate_scale: Option<&HashMap<usize, f64>>,
) -> ReactionAssembly {
    assemble_reaction_rates(
        lib,
        lib_targets,
        phi,
        chain,
        fission_yields,
        led,
        true,
        shield,
        rate_scale,
    )
}

#[allow(clippy::too_many_arguments)]
fn assemble_reaction_rates<L: ReactionLibrary + ?Sized>(
    lib: &L,
    lib_targets: &[(i32, i32)],
    phi: &[f64],
    chain: &Chain,
    fission_yields: &HashMap<(i32, i32), EffectiveYields>,
    led: &mut RateLedger,
    include_derivatives: bool,
    shield: Option<&crate::shielding::ShieldPlan>,
    rate_scale: Option<&HashMap<usize, f64>>,
) -> ReactionAssembly {
    let mut trip: Vec<(usize, usize, f64)> = Vec::new();
    let mut derivatives = include_derivatives.then(Vec::new);
    let mut yield_derivatives = include_derivatives.then(Vec::new);
    let mut seen_absent: std::collections::HashSet<(i32, i32)> = Default::default();
    let rate_per_barn = RatePerBarnSecond::from_particle_flux(ParticleFlux::sum_groups(phi));
    let rate_per_barn_s = rate_per_barn.get();
    let mut flux_denominator = 0.0;
    let group_count = lib.group_count();
    for flux in &phi[..group_count] {
        flux_denominator += *flux;
    }
    let first_flux_group = phi[..group_count]
        .iter()
        .position(|flux| *flux != 0.0)
        .unwrap_or(group_count);
    let last_flux_group = phi[..group_count]
        .iter()
        .rposition(|flux| *flux != 0.0)
        .map(|group| group + 1)
        .unwrap_or(first_flux_group);
    for (i, r) in lib.rows().iter().enumerate() {
        let shielded_target = lib_targets.get(r.target).copied();
        let scale = shield.and_then(|plan| {
            shielded_target.and_then(|(za, liso)| plan.row_scales(za, liso, r.mt))
        });
        let collapsed = match scale {
            Some(scales) => lib.collapse_row_scaled(
                i,
                phi,
                flux_denominator,
                first_flux_group,
                last_flux_group,
                &|group| scales.get(&group).copied().unwrap_or(1.0),
            ),
            None => lib.collapse_row(i, phi, flux_denominator, first_flux_group, last_flux_group),
        };
        let mut rate = (CrossSectionBarns::from_collapsed_kernel(collapsed) * rate_per_barn).get();
        if let Some(factor) = rate_scale.and_then(|m| m.get(&i)) {
            rate *= factor;
        }
        if rate == 0.0 && rate_per_barn_s == 0.0 {
            continue;
        }
        let tgt = match lib_targets.get(r.target) {
            Some(t) => *t,
            None => continue,
        };
        let col = match chain.index.get(&tgt) {
            Some(&c) => c,
            None => {
                if seen_absent.insert(tgt) {
                    led.targets_absent_from_decay_lib.push(tgt);
                }
                continue;
            }
        };
        if r.zap == -1 {
            if rate != 0.0 {
                trip.push((col, col, -rate));
            }
            if let Some(derivatives) = derivatives.as_mut() {
                derivatives.push(ReactionDerivative {
                    library_row: i,
                    row: col,
                    column: col,
                    per_barn_s: -rate_per_barn_s,
                });
            }
            continue;
        } // loss term
        if r.mt == 18 && r.zap == 0 {
            let parent = format!("{}_{}", tgt.0, tgt.1);
            if let Some(yields) = fission_yields.get(&tgt) {
                let mut mapped_yield_sum = 0.0;
                let mut leakage_yield_sum = 0.0;
                for (&product, &yield_value) in &yields.products {
                    if yield_value == 0.0 {
                        continue;
                    }
                    let product_rate = yield_value * rate;
                    match chain.index.get(&product) {
                        Some(&row) => {
                            mapped_yield_sum += yield_value;
                            if product_rate != 0.0 {
                                trip.push((row, col, product_rate));
                            }
                            if let Some(derivatives) = derivatives.as_mut() {
                                derivatives.push(ReactionDerivative {
                                    library_row: i,
                                    row,
                                    column: col,
                                    per_barn_s: yield_value * rate_per_barn_s,
                                });
                            }
                            if let Some(yield_derivatives) = yield_derivatives.as_mut() {
                                if product_rate != 0.0 {
                                    yield_derivatives.push(YieldDerivative {
                                        parent: tgt,
                                        product,
                                        row,
                                        column: col,
                                        per_yield_s: rate,
                                    });
                                }
                            }
                        }
                        None => {
                            leakage_yield_sum += yield_value;
                            if product_rate != 0.0 {
                                trip.push((chain.leak, col, product_rate));
                                led.fission_product_leakage.push(FissionProductLeakage {
                                    parent: parent.clone(),
                                    product: format!("{}_{}", product.0, product.1),
                                    yield_value,
                                    production_rate_per_parent_s: product_rate,
                                });
                            }
                            if let Some(derivatives) = derivatives.as_mut() {
                                derivatives.push(ReactionDerivative {
                                    library_row: i,
                                    row: chain.leak,
                                    column: col,
                                    per_barn_s: yield_value * rate_per_barn_s,
                                });
                            }
                            if let Some(yield_derivatives) = yield_derivatives.as_mut() {
                                if product_rate != 0.0 {
                                    yield_derivatives.push(YieldDerivative {
                                        parent: tgt,
                                        product,
                                        row: chain.leak,
                                        column: col,
                                        per_yield_s: rate,
                                    });
                                }
                            }
                        }
                    }
                }
                if rate != 0.0 {
                    led.fission_balance.insert(
                        parent,
                        FissionBalance {
                            fission_rate_per_parent_s: rate,
                            raw_yield_sum: yields.sum,
                            mapped_yield_sum,
                            leakage_yield_sum,
                        },
                    );
                }
            } else {
                if rate != 0.0 {
                    *led.fission_no_yields.entry(parent).or_insert(0.0) += rate;
                    trip.push((chain.leak, col, rate));
                }
                if let Some(derivatives) = derivatives.as_mut() {
                    derivatives.push(ReactionDerivative {
                        library_row: i,
                        row: chain.leak,
                        column: col,
                        per_barn_s: rate_per_barn_s,
                    });
                }
            }
            continue;
        }
        if r.lmf == -2 {
            // builder could not map the product
            if rate != 0.0 {
                *led.products_unmapped
                    .entry(format!("{}_{}_MT{}", tgt.0, tgt.1, r.mt))
                    .or_insert(0.0) += rate;
                trip.push((chain.leak, col, rate));
            }
            if let Some(derivatives) = derivatives.as_mut() {
                derivatives.push(ReactionDerivative {
                    library_row: i,
                    row: chain.leak,
                    column: col,
                    per_barn_s: rate_per_barn_s,
                });
            }
            continue;
        }
        let row = match chain.index.get(&(r.zap, r.lfs)) {
            Some(&j) => j,
            None => match chain.index.get(&(r.zap, 0)) {
                Some(&j) => {
                    if r.lfs != 0 && rate != 0.0 {
                        *led.isomer_fell_back_to_ground
                            .entry(format!("{}_m{}", r.zap, r.lfs))
                            .or_insert(0.0) += rate;
                    }
                    j
                }
                None => {
                    if rate != 0.0 {
                        *led.products_no_decay_data
                            .entry(format!("{}_{}", r.zap, r.lfs))
                            .or_insert(0.0) += rate;
                    }
                    chain.leak
                }
            },
        };
        if rate != 0.0 {
            trip.push((row, col, rate));
        }
        if let Some(derivatives) = derivatives.as_mut() {
            derivatives.push(ReactionDerivative {
                library_row: i,
                row,
                column: col,
                per_barn_s: rate_per_barn_s,
            });
        }
    }
    ReactionAssembly {
        triplets: trip,
        derivatives: derivatives.unwrap_or_default(),
        yield_derivatives: yield_derivatives.unwrap_or_default(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use actinv_data::decay::Mode;
    use actinv_data::library::{Library, Row};

    fn nuclide(za: i32, liso: i32, half_life: f64, modes: &[(f64, f64, f64)]) -> Nuclide {
        Nuclide {
            mat: 0,
            za,
            awr: 0.0,
            liso,
            nst: if half_life > 0.0 { 0 } else { 1 },
            half_life,
            d_half_life: 0.0,
            energies: Vec::new(),
            modes: modes
                .iter()
                .map(|&(rtyp, rfs, br)| Mode {
                    rtyp,
                    rfs,
                    q: 0.0,
                    dq: 0.0,
                    br,
                    dbr: 0.0,
                })
                .collect(),
            spectra: Vec::new(),
        }
    }

    fn network(nuclides: Vec<Nuclide>) -> Chain {
        build(&nuclides.into_iter().map(|n| ((n.za, n.liso), n)).collect())
    }

    fn entries(chain: &Chain, column: (i32, i32)) -> Vec<(usize, f64)> {
        let k = chain.index[&column];
        let mut out: Vec<_> = chain
            .decay
            .iter()
            .filter(|t| t.1 == k)
            .map(|t| (t.0, t.2))
            .collect();
        out.sort_by_key(|entry| entry.0);
        out
    }

    #[test]
    fn branching_shortfall_goes_to_leakage_and_excess_is_scaled_away() {
        let l = std::f64::consts::LN_2 / 10.0;
        // JEFF-3.3 Ir-169 lists only its 45% alpha branch; UKDD-2020 Er-152 sums to 1.01.
        let chain = network(vec![
            nuclide(77_169, 0, 10.0, &[(4.0, 0.0, 0.45)]),
            nuclide(75_165, 0, 0.0, &[]),
            nuclide(68_152, 0, 10.0, &[(1.0, 0.0, 0.61), (2.0, 0.0, 0.40)]),
            nuclide(69_152, 0, 0.0, &[]),
            nuclide(67_152, 0, 0.0, &[]),
        ]);
        let (ir, re) = (chain.index[&(77_169, 0)], chain.index[&(75_165, 0)]);
        assert_eq!(
            entries(&chain, (77_169, 0)),
            vec![(re, l * 0.45), (ir, -l), (chain.leak, l * (1.0 - 0.45))]
        );
        for column in [(77_169, 0), (68_152, 0)] {
            let net: f64 = entries(&chain, column).iter().map(|entry| entry.1).sum();
            assert!(net.abs() <= 1e-15 * l, "{column:?} loses {net}");
        }
        assert!(entries(&chain, (68_152, 0))
            .iter()
            .all(|entry| entry.0 != chain.leak));
        assert_eq!(
            chain.ledger.branching_sums,
            vec![((68_152, 0), 0.61 + 0.40), ((77_169, 0), 0.45)]
        );
    }

    #[test]
    fn rounding_level_branching_sums_are_left_exact() {
        let l = std::f64::consts::LN_2 / 10.0;
        let chain = network(vec![
            nuclide(27_060, 0, 10.0, &[(1.0, 0.0, 0.9999995)]),
            nuclide(28_060, 0, 0.0, &[]),
        ]);
        let ni = chain.index[&(28_060, 0)];
        let produced = entries(&chain, (27_060, 0))
            .into_iter()
            .find(|entry| entry.0 == ni)
            .unwrap();
        assert_eq!(produced.1.to_bits(), (l * 0.9999995).to_bits());
        assert!(chain.ledger.branching_sums.is_empty());
        assert!(chain.decay.iter().all(|t| t.0 != chain.leak));
    }

    #[test]
    fn a_transition_resolving_to_its_own_parent_is_booked_to_leakage() {
        let l = std::f64::consts::LN_2 / 10.0;
        // An isomeric transition from the ground state to an absent isomer falls back to the ground state.
        let chain = network(vec![nuclide(49_115, 0, 10.0, &[(3.0, 1.0, 1.0)])]);
        let k = chain.index[&(49_115, 0)];
        assert_eq!(entries(&chain, (49_115, 0)), vec![(k, -l), (chain.leak, l)]);
        assert_eq!(chain.ledger.daughters_missing.len(), 1);
    }

    #[test]
    fn derivative_free_assembly_preserves_rates_and_ledger() {
        let library = Library {
            rows: vec![
                Row {
                    target: 0,
                    mt: 102,
                    zap: 26_057,
                    lfs: 0,
                    lmf: 3,
                },
                Row {
                    target: 0,
                    mt: 1,
                    zap: -1,
                    lfs: 0,
                    lmf: 3,
                },
            ],
            sig: vec![9.0, 1.0, 3.0, 8.0, 7.0, 2.0, 4.0, 6.0],
            ngroups: 4,
            bounds: vec![1.0, 2.0, 3.0, 4.0, 5.0],
        };
        let chain = Chain {
            index: HashMap::from([((26_056, 0), 0), ((26_057, 0), 1)]),
            keys: vec![(26_056, 0), (26_057, 0)],
            lambda: vec![0.0, 0.0],
            decay: Vec::new(),
            leak: 2,
            unit: 3,
            n: 4,
            ledger: ChainLedger::default(),
        };
        let flux = [0.0, 5.0, 7.0, 0.0];
        let targets = [(26_056, 0)];
        let yields = HashMap::new();
        let mut plain_ledger = RateLedger::default();
        let plain = reaction_rates(
            &library,
            &targets,
            &flux,
            &chain,
            &yields,
            &mut plain_ledger,
            None,
            None,
        );
        let mut derivative_ledger = RateLedger::default();
        let with_derivatives = reaction_rates_with_derivatives(
            &library,
            &targets,
            &flux,
            &chain,
            &yields,
            &mut derivative_ledger,
            None,
            None,
        );

        assert_eq!(plain, with_derivatives.triplets);
        assert_eq!(plain_ledger, derivative_ledger);
        assert_eq!(with_derivatives.derivatives.len(), library.rows.len());
        let rate_per_barn =
            RatePerBarnSecond::from_particle_flux(ParticleFlux::sum_groups(&flux)).get();
        assert_eq!(
            plain[0].2.to_bits(),
            (library.one_group(0, &flux) * rate_per_barn).to_bits()
        );
        assert_eq!(
            plain[1].2.to_bits(),
            (-library.one_group(1, &flux) * rate_per_barn).to_bits()
        );
    }
}
