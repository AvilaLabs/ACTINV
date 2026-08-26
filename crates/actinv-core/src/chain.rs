//! Transmutation network: decay matrix from the decay sublibraries, reaction columns from the activation library,
//! and the trace formulation (constant bulk as a source through a unit state). Mirrors controls/chain.py and the
//! trace formulation of controls/run_fns.py, which the P5-G4 control checks to 1e-12 on 132 experiments.
use actinv_data::decay::Nuclide;
use actinv_data::fission::EffectiveYields;
use actinv_data::library::Library;
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
}

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
        let (z, a) = (nu.za / 1000, nu.za % 1000);
        for md in &nu.modes {
            if md.br <= 0.0 {
                continue;
            }
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
                trip.push((leak, k, l * md.br));
                continue;
            }
            let want = (zz * 1000 + aa, md.rfs.round() as i32);
            let j = index.get(&want).or_else(|| index.get(&(zz * 1000 + aa, 0)));
            match j {
                Some(&j) => trip.push((j, k, l * md.br)),
                None => {
                    led.daughters_missing.push((want.0, want.1, l * md.br));
                    trip.push((leak, k, l * md.br));
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

#[derive(Clone, Debug, serde::Serialize)]
pub struct FissionProductLeakage {
    pub parent: String,
    pub product: String,
    pub yield_value: f64,
    pub production_rate_per_parent_s: f64,
}

#[derive(Clone, Debug, serde::Serialize)]
pub struct FissionBalance {
    pub fission_rate_per_parent_s: f64,
    pub raw_yield_sum: f64,
    pub mapped_yield_sum: f64,
    pub leakage_yield_sum: f64,
}

#[derive(Default, Debug)]
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

/// Reaction rates per atom (1/s) for every library target under a group flux, as triplets over the chain's indices.
/// `lib_targets[i]` is the (ZA, LISO) of library target index i.
pub fn reaction_rates(
    lib: &Library,
    lib_targets: &[(i32, i32)],
    phi: &[f64],
    chain: &Chain,
    fission_yields: &HashMap<(i32, i32), EffectiveYields>,
    led: &mut RateLedger,
) -> Vec<(usize, usize, f64)> {
    let mut trip: Vec<(usize, usize, f64)> = Vec::new();
    let mut seen_absent: std::collections::HashSet<(i32, i32)> = Default::default();
    for (i, r) in lib.rows.iter().enumerate() {
        let rate = lib.one_group(i, phi) * 1e-24 * phi.iter().sum::<f64>();
        if rate == 0.0 {
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
            trip.push((col, col, -rate));
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
                            trip.push((row, col, product_rate));
                        }
                        None => {
                            leakage_yield_sum += yield_value;
                            trip.push((chain.leak, col, product_rate));
                            led.fission_product_leakage.push(FissionProductLeakage {
                                parent: parent.clone(),
                                product: format!("{}_{}", product.0, product.1),
                                yield_value,
                                production_rate_per_parent_s: product_rate,
                            });
                        }
                    }
                }
                led.fission_balance.insert(
                    parent,
                    FissionBalance {
                        fission_rate_per_parent_s: rate,
                        raw_yield_sum: yields.sum,
                        mapped_yield_sum,
                        leakage_yield_sum,
                    },
                );
            } else {
                *led.fission_no_yields.entry(parent).or_insert(0.0) += rate;
                trip.push((chain.leak, col, rate));
            }
            continue;
        }
        if r.lmf == -2 {
            // builder could not map the product
            *led.products_unmapped
                .entry(format!("{}_{}_MT{}", tgt.0, tgt.1, r.mt))
                .or_insert(0.0) += rate;
            trip.push((chain.leak, col, rate));
            continue;
        }
        let row = match chain.index.get(&(r.zap, r.lfs)) {
            Some(&j) => j,
            None => match chain.index.get(&(r.zap, 0)) {
                Some(&j) => {
                    if r.lfs != 0 {
                        *led.isomer_fell_back_to_ground
                            .entry(format!("{}_m{}", r.zap, r.lfs))
                            .or_insert(0.0) += rate;
                    }
                    j
                }
                None => {
                    *led.products_no_decay_data
                        .entry(format!("{}_{}", r.zap, r.lfs))
                        .or_insert(0.0) += rate;
                    chain.leak
                }
            },
        };
        trip.push((row, col, rate));
    }
    trip
}
