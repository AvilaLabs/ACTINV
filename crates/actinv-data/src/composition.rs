//! Material composition -> atoms per gram, from the embedded natural-abundance and atomic-mass tables.
//! Mirrors controls/harness/composition.py; the P5-G2 control requires agreement on all 132 FNS compositions.
use std::collections::BTreeMap;
use crate::tables::{ISOTOPES, PROVENANCE};

pub const NA: f64 = 6.02214076e23;

#[derive(Debug, Default)]
pub struct CompositionDiag { pub elements: BTreeMap<String, (f64, f64, usize)>, pub unknown: Vec<String> }

fn cap(el: &str) -> String {
    let mut c = el.chars();
    match c.next() { Some(f) => f.to_uppercase().collect::<String>() + &c.as_str().to_lowercase(), None => String::new() }
}

/// Element weight-percent -> atoms per gram keyed by (ZA, LISO). Elements are expanded over natural abundance.
/// Returns the inventory and a diagnostic (molar mass, atoms per gram and isotope count per element; unknown elements).
pub fn atoms_per_gram(elements_wt: &BTreeMap<String, f64>) -> (BTreeMap<(i32, i32), f64>, CompositionDiag) {
    let mut out: BTreeMap<(i32, i32), f64> = BTreeMap::new();
    let mut diag = CompositionDiag::default();
    for (el, w) in elements_wt {
        let e = cap(el);
        let iso: Vec<_> = ISOTOPES.iter().filter(|(s, ..)| *s == e).collect();
        if iso.is_empty() { diag.unknown.push(el.clone()); continue; }
        let z = z_of(&e).expect("element with abundance data has a Z");
        let molar: f64 = iso.iter().map(|(_, _, _, ab, m)| ab * m).sum();     // g/mol
        let moles = (w / 100.0) / molar;
        for (_, a, liso, ab, _) in &iso { *out.entry((z * 1000 + a, *liso)).or_insert(0.0) += NA * moles * ab; }
        diag.elements.insert(e, (molar, NA * moles, iso.len()));
    }
    (out, diag)
}

pub fn provenance() -> &'static str { PROVENANCE }

pub const SYMBOLS: [&str; 118] = ["H","He","Li","Be","B","C","N","O","F","Ne","Na","Mg","Al","Si","P","S","Cl","Ar","K","Ca","Sc","Ti","V","Cr","Mn","Fe","Co","Ni","Cu","Zn","Ga","Ge","As","Se","Br","Kr","Rb","Sr","Y","Zr","Nb","Mo","Tc","Ru","Rh","Pd","Ag","Cd","In","Sn","Sb","Te","I","Xe","Cs","Ba","La","Ce","Pr","Nd","Pm","Sm","Eu","Gd","Tb","Dy","Ho","Er","Tm","Yb","Lu","Hf","Ta","W","Re","Os","Ir","Pt","Au","Hg","Tl","Pb","Bi","Po","At","Rn","Fr","Ra","Ac","Th","Pa","U","Np","Pu","Am","Cm","Bk","Cf","Es","Fm","Md","No","Lr","Rf","Db","Sg","Bh","Hs","Mt","Ds","Rg","Cn","Nh","Fl","Mc","Lv","Ts","Og"];
pub fn z_of(sym: &str) -> Option<i32> { SYMBOLS.iter().position(|s| *s == sym).map(|i| i as i32 + 1) }
pub fn symbol_of(z: i32) -> &'static str { SYMBOLS.get((z - 1) as usize).copied().unwrap_or("?") }
