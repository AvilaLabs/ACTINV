//! Transmutation network: decay matrix from the decay sublibraries, reaction columns from the activation library,
//! and the trace formulation (constant bulk as a source through a unit state). Mirrors controls/chain.py and the
//! trace formulation of controls/run_fns.py, which the P5-G4 control checks to 1e-12 on 132 experiments.
use crate::gas;
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
        // Folded from +0.0: `sum` starts at -0.0, which a state with no modes would print as.
        let assigned = nu
            .modes
            .iter()
            .map(|md| md.br)
            .filter(|br| *br > 0.0)
            .fold(0.0, |sum, br| sum + br);
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

/// P92 gas: ensures the chain contains the five light ground states (H1, H2, H3, He3, He4),
/// appending a stable stand-in (lambda = 0, no decay modes) for any absent from the decay
/// library used to build `chain`. A state already present (real decay data) is untouched.
/// No-op, byte-for-byte, when all five are already present (the common case for real decay
/// libraries). Returns the (possibly augmented) chain and the ZA of any stand-ins inserted.
///
/// New states are appended after the existing nuclide rows, and `leak`/`unit` are renumbered
/// to stay last; every existing triplet referencing the old `leak`/`unit` index is remapped to
/// the new one. This never reorders or renumbers an existing nuclide row, so callers that do
/// not enable gas never call this and see byte-identical chains (G3).
pub fn ensure_light_states(chain: Chain) -> (Chain, Vec<(i32, i32)>) {
    let missing: Vec<(i32, i32)> = gas::LIGHT_STATES
        .iter()
        .copied()
        .filter(|za| !chain.index.contains_key(za))
        .collect();
    if missing.is_empty() {
        return (chain, missing);
    }
    let Chain {
        mut index,
        mut keys,
        mut lambda,
        mut decay,
        leak: old_leak,
        unit: old_unit,
        ledger,
        ..
    } = chain;
    let old_n_nuc = keys.len();
    let new_leak = old_n_nuc + missing.len();
    let new_unit = new_leak + 1;
    let remap = |i: usize| -> usize {
        if i == old_leak {
            new_leak
        } else if i == old_unit {
            new_unit
        } else {
            i
        }
    };
    for t in decay.iter_mut() {
        t.0 = remap(t.0);
        t.1 = remap(t.1);
    }
    for (offset, za) in missing.iter().enumerate() {
        let idx = old_n_nuc + offset;
        index.insert(*za, idx);
        keys.push(*za);
        lambda.push(0.0); // stable stand-in: no decay library entry for this ZA
    }
    let n = new_unit + 1;
    (
        Chain {
            index,
            keys,
            lambda,
            decay,
            leak: new_leak,
            unit: new_unit,
            n,
            ledger,
        },
        missing,
    )
}

/// P92 gas: for every decaying nuclide's mode, adds `branching * lambda` into He4 for each RTYP
/// digit 4 (alpha emission) and into H1 for each digit 7 (proton emission), mirroring the same
/// per-mode branching normalization `build` applies to its own daughter edges (excess branching
/// sums are rescaled to 1; a shortfall is left as-is — its remainder already leaks in `build`'s
/// own edges). Multi-digit RTYP contributes once per matching digit (e.g. 4.4 gives two alphas).
/// No-op if He4 or H1 is absent from `chain.index` (gas is disabled, or `ensure_light_states`
/// was not called first).
pub fn add_gas_decay_edges(nuclides: &HashMap<(i32, i32), Nuclide>, chain: &mut Chain) {
    let (Some(&he4), Some(&h1)) = (chain.index.get(&gas::HE4), chain.index.get(&gas::H1)) else {
        return;
    };
    let mut extra: Vec<(usize, usize, f64)> = Vec::new();
    for (k, key) in chain.keys.iter().enumerate() {
        let nu = match nuclides.get(key) {
            Some(nu) => nu,
            None => continue,
        };
        let l = nu.lambda();
        if l == 0.0 {
            continue;
        }
        let assigned = nu
            .modes
            .iter()
            .map(|md| md.br)
            .filter(|br| *br > 0.0)
            .fold(0.0, |sum, br| sum + br);
        let scale = if (assigned - 1.0).abs() > BRANCHING_TOLERANCE && assigned > 1.0 {
            1.0 / assigned
        } else {
            1.0
        };
        for md in &nu.modes {
            if md.br <= 0.0 {
                continue;
            }
            let rate = l * md.br * scale;
            if rate == 0.0 {
                continue;
            }
            for d in rtyp_digits(md.rtyp) {
                match d {
                    4 => extra.push((he4, k, rate)),
                    7 => extra.push((h1, k, rate)),
                    _ => {}
                }
            }
        }
    }
    chain.decay.extend(extra);
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
    /// P92 gas: reaction rate of product rows whose MT has no ejectile-table entry (for
    /// example MT 18, fission), keyed by MT. Populated only when gas is enabled.
    pub gas_uncovered: BTreeMap<String, f64>,
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
    gas: bool,
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
        gas,
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
    gas: bool,
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
        gas,
    )
}

/// P92 gas: for a product row's own rate, either add `mult * rate` triplets (and matching
/// derivatives) into the light-nuclide rows the row's MT emits, or — if the MT has no
/// ejectile-table entry — book the rate to the ledger's `gas.uncovered`, keyed by MT. Called
/// once per product row (the loss row and, within the fission branch, the fission row itself
/// are not product rows and never reach this function), so a reaction sharing several product
/// rows (isomer branches) is still counted once per row, exactly as its own rate already is.
#[allow(clippy::too_many_arguments)]
fn add_gas_ejectiles(
    chain: &Chain,
    mt: i32,
    col: usize,
    rate: f64,
    rate_per_barn_s: f64,
    library_row: usize,
    trip: &mut Vec<(usize, usize, f64)>,
    derivatives: &mut Option<Vec<ReactionDerivative>>,
    led: &mut RateLedger,
) {
    if rate == 0.0 {
        return;
    }
    match gas::table(mt) {
        Some(ejectiles) => {
            for (za, mult) in ejectiles.gas_products() {
                if mult == 0 {
                    continue;
                }
                if let Some(&row) = chain.index.get(&za) {
                    let value = f64::from(mult) * rate;
                    if value != 0.0 {
                        trip.push((row, col, value));
                    }
                    if let Some(derivatives) = derivatives.as_mut() {
                        derivatives.push(ReactionDerivative {
                            library_row,
                            row,
                            column: col,
                            per_barn_s: f64::from(mult) * rate_per_barn_s,
                        });
                    }
                }
            }
        }
        None => {
            *led.gas_uncovered.entry(mt.to_string()).or_insert(0.0) += rate;
        }
    }
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
    gas: bool,
) -> ReactionAssembly {
    let mut trip: Vec<(usize, usize, f64)> = Vec::new();
    let mut derivatives = include_derivatives.then(Vec::new);
    let mut yield_derivatives = include_derivatives.then(Vec::new);
    let mut seen_absent: std::collections::HashSet<(i32, i32)> = Default::default();
    let rate_per_barn = RatePerBarnSecond::from_particle_flux(ParticleFlux::sum_groups(phi));
    let rate_per_barn_s = rate_per_barn.get();
    let (flux_denominator, first_flux_group, last_flux_group) =
        actinv_data::library::flux_window(phi, lib.group_count());
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
            // Ternary-fission gas is not modelled: fission is always uncovered.
            if gas {
                add_gas_ejectiles(
                    chain,
                    r.mt,
                    col,
                    rate,
                    rate_per_barn_s,
                    i,
                    &mut trip,
                    &mut derivatives,
                    led,
                );
            }
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
            // builder could not map the product; the reaction still happened, so gas ejectiles
            // (if the MT is covered) are still added even though the residual itself leaks.
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
            if gas {
                add_gas_ejectiles(
                    chain,
                    r.mt,
                    col,
                    rate,
                    rate_per_barn_s,
                    i,
                    &mut trip,
                    &mut derivatives,
                    led,
                );
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
        // A normal product row, including one whose product fell back to chain.leak above,
        // still adds gas ejectiles: the reaction happened regardless of where its residual went.
        if gas {
            add_gas_ejectiles(
                chain,
                r.mt,
                col,
                rate,
                rate_per_barn_s,
                i,
                &mut trip,
                &mut derivatives,
                led,
            );
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
    fn a_radioactive_state_without_decay_modes_decays_into_leakage() {
        let l = std::f64::consts::LN_2 / 2.0;
        // The P11 fixture's Mn-56 and Mn-57 carry a half-life and NDK = 0.
        let chain = network(vec![nuclide(25_056, 0, 2.0, &[])]);
        let k = chain.index[&(25_056, 0)];
        assert_eq!(entries(&chain, (25_056, 0)), vec![(k, -l), (chain.leak, l)]);
        assert_eq!(chain.ledger.branching_sums, vec![((25_056, 0), 0.0)]);
        assert!(chain.ledger.branching_sums[0].1.is_sign_positive());
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
            false,
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
            false,
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

    // ---- P92 gas ----

    #[test]
    fn ensure_light_states_is_a_no_op_when_all_present() {
        let mut nuclides = vec![nuclide(25_056, 0, 2.0, &[])];
        for za in gas::LIGHT_STATES {
            nuclides.push(nuclide(za.0, za.1, 0.0, &[]));
        }
        let chain = network(nuclides);
        let (before_n, before_leak, before_unit) = (chain.n, chain.leak, chain.unit);
        let before_decay = chain.decay.clone();
        let (same, missing) = ensure_light_states(chain);
        assert!(missing.is_empty());
        assert_eq!(same.n, before_n);
        assert_eq!(same.leak, before_leak);
        assert_eq!(same.unit, before_unit);
        assert_eq!(same.decay, before_decay);
    }

    #[test]
    fn ensure_light_states_appends_stand_ins_and_remaps_leak_references() {
        // Mn-56 has a half-life but no decay modes in this fixture, so `build` books its whole
        // decay rate to leakage (mirrors the P11 fixture case above).
        let chain = network(vec![nuclide(25_056, 0, 2.0, &[])]);
        let old_leak = chain.leak;
        let k = chain.index[&(25_056, 0)];
        let (augmented, missing) = ensure_light_states(chain);
        assert_eq!(missing, gas::LIGHT_STATES.to_vec());
        assert_eq!(augmented.n, old_leak + 5 + 2);
        assert_eq!(augmented.leak, old_leak + 5);
        assert_eq!(augmented.unit, old_leak + 6);
        for za in gas::LIGHT_STATES {
            let idx = augmented.index[&za];
            assert_eq!(augmented.keys[idx], za);
            assert_eq!(augmented.lambda[idx], 0.0);
        }
        let l = std::f64::consts::LN_2 / 2.0;
        assert!(augmented.decay.contains(&(augmented.leak, k, l)));
        assert!(!augmented
            .decay
            .iter()
            .any(|&(r, c, _)| r == old_leak && c == k));
    }

    #[test]
    fn add_gas_decay_edges_tallies_one_digit_per_alpha_or_proton() {
        let l = std::f64::consts::LN_2 / 10.0;
        let nuclides: HashMap<(i32, i32), Nuclide> = vec![
            // one alpha branch (RTYP 1.4, digit 4 once) and one proton branch (RTYP 7).
            nuclide(94_238, 0, 10.0, &[(1.4, 0.0, 0.5), (7.0, 0.0, 0.5)]),
            // RTYP 4.4: two alphas from a single decay mode.
            nuclide(96_242, 0, 10.0, &[(4.4, 0.0, 1.0)]),
            // shortfall branching (assigned 0.4 < 1): build() leaves it unscaled, and so does
            // add_gas_decay_edges — the He4 edge carries the raw br, not a rescaled one.
            nuclide(94_239, 0, 10.0, &[(4.0, 0.0, 0.4)]),
        ]
        .into_iter()
        .map(|n| ((n.za, n.liso), n))
        .collect();
        let chain = build(&nuclides);
        let (mut chain, missing) = ensure_light_states(chain);
        assert_eq!(missing.len(), 5);
        add_gas_decay_edges(&nuclides, &mut chain);
        let he4 = chain.index[&gas::HE4];
        let h1 = chain.index[&gas::H1];
        let p1 = chain.index[&(94_238, 0)];
        let p2 = chain.index[&(96_242, 0)];
        let p3 = chain.index[&(94_239, 0)];

        let sum_edges = |row: usize, col: usize| -> Vec<f64> {
            chain
                .decay
                .iter()
                .filter(|&&(r, c, _)| r == row && c == col)
                .map(|t| t.2)
                .collect()
        };
        assert_eq!(sum_edges(he4, p1), vec![l * 0.5]);
        assert_eq!(sum_edges(h1, p1), vec![l * 0.5]);
        let p2_alphas = sum_edges(he4, p2);
        assert_eq!(p2_alphas, vec![l, l]); // two pushes, one per matching RTYP digit
        assert_eq!(sum_edges(he4, p3), vec![l * 0.4]); // unscaled shortfall branching
        assert!(sum_edges(h1, p2).is_empty());
    }

    #[test]
    fn add_gas_decay_edges_is_a_no_op_without_light_states() {
        let nuclides: HashMap<(i32, i32), Nuclide> =
            vec![nuclide(94_238, 0, 10.0, &[(1.4, 0.0, 1.0)])]
                .into_iter()
                .map(|n| ((n.za, n.liso), n))
                .collect();
        let mut chain = build(&nuclides); // no ensure_light_states call: He4/H1 absent
        let before = chain.decay.clone();
        add_gas_decay_edges(&nuclides, &mut chain);
        assert_eq!(chain.decay, before);
    }

    fn gas_test_fixture() -> (Library, [(i32, i32); 1], Chain) {
        let library = Library {
            rows: vec![
                // (n,p): H1, normal mapped product row.
                Row {
                    target: 0,
                    mt: 103,
                    zap: 27_057,
                    lfs: 0,
                    lmf: 3,
                },
                // (n,t): H3, product unmapped -> leak, gas still applies.
                Row {
                    target: 0,
                    mt: 105,
                    zap: 999_999,
                    lfs: 0,
                    lmf: -2,
                },
                // fission: always uncovered, no ejectiles.
                Row {
                    target: 0,
                    mt: 18,
                    zap: 0,
                    lfs: 0,
                    lmf: 3,
                },
            ],
            sig: vec![1.0; 3 * 4],
            ngroups: 4,
            bounds: vec![1.0, 2.0, 3.0, 4.0, 5.0],
        };
        let targets = [(26_056, 0)];
        let chain = Chain {
            index: HashMap::from([
                ((26_056, 0), 0),
                ((27_057, 0), 1),
                (gas::H1, 2),
                (gas::H3, 3),
            ]),
            keys: vec![(26_056, 0), (27_057, 0), gas::H1, gas::H3],
            lambda: vec![0.0; 4],
            decay: Vec::new(),
            leak: 4,
            unit: 5,
            n: 6,
            ledger: ChainLedger::default(),
        };
        (library, targets, chain)
    }

    #[test]
    fn gas_ejectiles_are_added_for_normal_and_unmapped_rows_and_fission_is_uncovered() {
        let (library, targets, chain) = gas_test_fixture();
        let flux = [0.0, 5.0, 7.0, 0.0];
        let yields = HashMap::new();
        let mut led = RateLedger::default();
        let trip = reaction_rates(
            &library, &targets, &flux, &chain, &yields, &mut led, None, None, true,
        );
        let edges = |row: usize, col: usize| -> Vec<f64> {
            trip.iter()
                .filter(|&&(r, c, _)| r == row && c == col)
                .map(|t| t.2)
                .collect()
        };
        let residual_rate = edges(1, 0)[0]; // the mt103 product (Co-57) row
        assert_eq!(edges(2, 0), vec![residual_rate]); // H1 gas: same row's rate, mult 1
        let leak_edges = edges(4, 0); // mt105 unmapped product + mt18 fission-no-yields
        assert_eq!(leak_edges.len(), 2);
        let h3_rate = edges(3, 0)[0];
        assert!(leak_edges.contains(&h3_rate)); // H3 gas from the lmf=-2 row
        assert_eq!(led.gas_uncovered.len(), 1);
        assert!(led.gas_uncovered.contains_key("18"));
    }

    #[test]
    fn gas_off_adds_no_ejectiles_or_uncovered_ledger_entries() {
        let (library, targets, chain) = gas_test_fixture();
        let flux = [0.0, 5.0, 7.0, 0.0];
        let yields = HashMap::new();
        let mut led = RateLedger::default();
        let trip = reaction_rates(
            &library, &targets, &flux, &chain, &yields, &mut led, None, None, false,
        );
        assert!(trip.iter().all(|&(r, _, _)| r != 2 && r != 3));
        assert!(led.gas_uncovered.is_empty());
    }

    /// One CRAM-16 step of a chain's decay triplets alone (no reactions), for the analytic-integral
    /// tests below: real matrix exponential math over a hand-built `Chain`, no files or Spec.
    fn decay_step(chain: &Chain, y0: &[f64], dt: f64) -> Vec<f64> {
        use crate::cram::{step, Cram};
        use crate::cram_coeffs::{CRAM16_ALPHA, CRAM16_ALPHA0, CRAM16_THETA};
        use crate::sparse::Csc;
        use num_complex::Complex64 as C64;
        let c = Cram {
            alpha0: CRAM16_ALPHA0,
            theta: CRAM16_THETA.iter().map(|(r, i)| C64::new(*r, *i)).collect(),
            alpha: CRAM16_ALPHA.iter().map(|(r, i)| C64::new(*r, *i)).collect(),
        };
        let trip: Vec<(usize, usize, C64)> = chain
            .decay
            .iter()
            .map(|&(r, c, v)| (r, c, C64::new(v, 0.0)))
            .collect();
        let a = Csc::from_triplets(chain.n, &trip);
        step(&a, y0, dt, &c).expect("CRAM step succeeds").0
    }

    #[test]
    fn gas_produced_from_a_constant_reservoir_matches_the_exact_linear_integral() {
        // T has no decay: its only edge is a hand-added constant-rate feed into He4, mirroring
        // the matrix a reaction triplet would produce for a target with no library loss row
        // (chain.rs's own gas_test_fixture has none). With T's diagonal exactly zero the 2x2
        // generator is nilpotent (A^2 = 0), so the true solution is the exact affine integral
        // He4(t) = He4_0 + R*T0*t, and CRAM (a rational approximation to exp matching its low-order
        // Taylor terms) reproduces it to machine precision, not just approximately.
        let nuclides: HashMap<(i32, i32), Nuclide> = vec![nuclide(26_056, 0, 0.0, &[])] // T: stable stand-in, no decay
            .into_iter()
            .map(|n| ((n.za, n.liso), n))
            .collect();
        let chain = build(&nuclides);
        let (mut chain, missing) = ensure_light_states(chain);
        assert_eq!(missing.len(), 5);
        let t = chain.index[&(26_056, 0)];
        let he4 = chain.index[&gas::HE4];
        let rate = 3.0e-7; // R, s^-1 per target atom; arbitrary, just not zero
        chain.decay.push((he4, t, rate));

        let t0 = 1.0e10; // T0, atoms/g
        let mut y0 = vec![0.0; chain.n];
        y0[t] = t0;
        let dt = 100.0;
        let y1 = decay_step(&chain, &y0, dt);

        let want_he4 = rate * t0 * dt;
        let rel = (y1[he4] - want_he4).abs() / want_he4;
        assert!(
            rel < 1e-9,
            "He4 {} vs analytic {want_he4}, rel {rel}",
            y1[he4]
        );
        // T itself must be untouched: A's (t,t) entry is exactly zero, so only CRAM's own
        // floating-point solve error (not any depletion) can move it.
        assert!((y1[t] - t0).abs() / t0 < 1e-9, "T {} vs {t0}", y1[t]);
    }

    #[test]
    fn h3_ejectiles_decay_to_he3_at_the_tabulated_lambda() {
        // H3 already real (as P92 requires once gas is on) with tritium's actual half-life
        // (12.32 y) and its real beta-minus branch (RTYP 1: Z -> Z+1, A unchanged); He3 already
        // present as its stable daughter. `ensure_light_states` must leave both alone — they are
        // not among its 5 missing stand-ins — and ordinary decay integration must reproduce the
        // textbook exponential to high precision.
        let half_life_s = 12.32 * 365.25 * 86_400.0; // tabulated tritium half-life
        let nuclides: HashMap<(i32, i32), Nuclide> = vec![
            nuclide(1_003, 0, half_life_s, &[(1.0, 0.0, 1.0)]), // H3 -> He3, 100% beta-minus
            nuclide(2_003, 0, 0.0, &[]),                        // He3: stable
        ]
        .into_iter()
        .map(|n| ((n.za, n.liso), n))
        .collect();
        let chain = build(&nuclides);
        assert_eq!(chain.index[&gas::H3], chain.index[&(1_003, 0)]);
        let (chain, missing) = ensure_light_states(chain);
        assert_eq!(missing.len(), 3); // H1, H2, He4 stand in; H3 and He3 were already real
        assert!(!missing.contains(&gas::H3) && !missing.contains(&gas::HE3));
        let h3 = chain.index[&gas::H3];
        let he3 = chain.index[&gas::HE3];

        let lambda = std::f64::consts::LN_2 / half_life_s;
        let h3_0 = 1.0e12;
        let mut y0 = vec![0.0; chain.n];
        y0[h3] = h3_0;
        let dt = half_life_s * 0.37; // an arbitrary fraction of a half-life
        let y1 = decay_step(&chain, &y0, dt);

        let want_h3 = h3_0 * (-lambda * dt).exp();
        let want_he3 = h3_0 - want_h3;
        assert!((y1[h3] - want_h3).abs() / want_h3 < 1e-9);
        assert!((y1[he3] - want_he3).abs() / want_he3 < 1e-9);
    }

    #[test]
    fn decay_alpha_case_feeds_he4_at_the_parents_lambda() {
        // A pure-alpha decaying nuclide (P92's gas-decay path, not the ordinary daughter-mapping
        // path H3->He3 exercises above): He4 comes from `add_gas_decay_edges`, at the parent's own
        // lambda, exactly matching the parent's own depletion curve.
        let half_life_s = 87.7 * 365.25 * 86_400.0; // Pu-238-like, arbitrary but realistic scale
        let nuclides: HashMap<(i32, i32), Nuclide> =
            vec![nuclide(94_238, 0, half_life_s, &[(4.0, 0.0, 1.0)])] // 100% alpha, daughter absent (leaks)
                .into_iter()
                .map(|n| ((n.za, n.liso), n))
                .collect();
        let chain = build(&nuclides);
        let (mut chain, missing) = ensure_light_states(chain);
        assert_eq!(missing.len(), 5);
        add_gas_decay_edges(&nuclides, &mut chain);
        let p = chain.index[&(94_238, 0)];
        let he4 = chain.index[&gas::HE4];

        let lambda = std::f64::consts::LN_2 / half_life_s;
        let p0 = 5.0e9;
        let mut y0 = vec![0.0; chain.n];
        y0[p] = p0;
        let dt = half_life_s * 1.5;
        let y1 = decay_step(&chain, &y0, dt);

        let want_p = p0 * (-lambda * dt).exp();
        let want_he4 = p0 - want_p;
        assert!((y1[p] - want_p).abs() / want_p < 1e-9);
        assert!((y1[he4] - want_he4).abs() / want_he4 < 1e-9);
    }
}
