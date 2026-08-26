//! Material composition -> atoms per gram, from the embedded natural-abundance and atomic-mass tables.
//! Mirrors controls/harness/composition.py; the P5-G2 control requires agreement on all 132 FNS compositions.
use crate::tables::{ISOTOPES, PROVENANCE};
use std::collections::BTreeMap;

pub const NA: f64 = 6.02214076e23;

#[derive(Debug, Default)]
pub struct CompositionDiag {
    pub elements: BTreeMap<String, (f64, f64, usize)>,
    pub unknown: Vec<String>,
}

fn isotopes(element: &str) -> Vec<&'static (&'static str, i32, i32, f64, f64)> {
    ISOTOPES
        .iter()
        .filter(|(symbol, ..)| *symbol == element)
        .collect()
}

pub fn molar_mass(element: &str) -> Option<f64> {
    let element = cap(element);
    let values = isotopes(&element);
    (!values.is_empty()).then(|| {
        values
            .iter()
            .map(|(_, _, _, abundance, mass)| abundance * mass)
            .sum()
    })
}

fn cap(el: &str) -> String {
    let mut c = el.chars();
    match c.next() {
        Some(f) => f.to_uppercase().collect::<String>() + &c.as_str().to_lowercase(),
        None => String::new(),
    }
}

/// Element weight-percent -> atoms per gram keyed by (ZA, LISO). Elements are expanded over natural abundance.
/// Returns the inventory and a diagnostic (molar mass, atoms per gram and isotope count per element; unknown elements).
pub fn atoms_per_gram(
    elements_wt: &BTreeMap<String, f64>,
) -> (BTreeMap<(i32, i32), f64>, CompositionDiag) {
    atoms_per_gram_basis(elements_wt, "wt_percent")
}

/// Convert an elemental composition to isotope atoms per gram.
///
/// `wt_percent` values are grams per 100 g, `atom_fraction` values are normalized ratios,
/// and `atoms_per_g` values are elemental atom densities which are expanded by natural
/// isotopic abundance. Unknown elements are returned in the diagnostic.
pub fn atoms_per_gram_basis(
    elements: &BTreeMap<String, f64>,
    basis: &str,
) -> (BTreeMap<(i32, i32), f64>, CompositionDiag) {
    let mut out: BTreeMap<(i32, i32), f64> = BTreeMap::new();
    let mut diag = CompositionDiag::default();
    let atom_fraction_denominator: f64 = if basis == "atom_fraction" {
        elements
            .iter()
            .filter_map(|(element, value)| molar_mass(element).map(|mass| value * mass))
            .sum()
    } else {
        0.0
    };
    for (el, value) in elements {
        let e = cap(el);
        let iso = isotopes(&e);
        if iso.is_empty() {
            diag.unknown.push(el.clone());
            continue;
        }
        let z = z_of(&e).expect("element with abundance data has a Z");
        let molar: f64 = iso.iter().map(|(_, _, _, ab, m)| ab * m).sum(); // g/mol
        let atoms = match basis {
            // Preserve the v0.1 operation order: its exact scalar regression is a gate.
            "wt_percent" => NA * ((value / 100.0) / molar),
            "atom_fraction" if atom_fraction_denominator > 0.0 => {
                NA * value / atom_fraction_denominator
            }
            "atoms_per_g" => *value,
            _ => 0.0,
        };
        for (_, a, liso, ab, _) in &iso {
            *out.entry((z * 1000 + a, *liso)).or_insert(0.0) += atoms * ab;
        }
        diag.elements.insert(e, (molar, atoms, iso.len()));
    }
    (out, diag)
}

/// Initial elemental mass fractions for response-function mixing.
pub fn mass_fractions(
    elements: &BTreeMap<String, f64>,
    basis: &str,
) -> Result<BTreeMap<String, f64>, String> {
    let mut masses = BTreeMap::new();
    for (element, value) in elements {
        let canonical = cap(element);
        let molar = molar_mass(&canonical).ok_or_else(|| {
            format!("material element '{element}' has no natural-isotope mass data")
        })?;
        let mass = match basis {
            "wt_percent" => *value,
            "atom_fraction" | "atoms_per_g" => value * molar,
            value => return Err(format!("unknown material basis '{value}'")),
        };
        masses.insert(canonical, mass);
    }
    let total: f64 = masses.values().sum();
    if total <= 0.0 {
        return Err("material composition has no positive mass".into());
    }
    for value in masses.values_mut() {
        *value /= total;
    }
    Ok(masses)
}

pub fn provenance() -> &'static str {
    PROVENANCE
}

pub const SYMBOLS: [&str; 118] = [
    "H", "He", "Li", "Be", "B", "C", "N", "O", "F", "Ne", "Na", "Mg", "Al", "Si", "P", "S", "Cl",
    "Ar", "K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn", "Ga", "Ge", "As",
    "Se", "Br", "Kr", "Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In",
    "Sn", "Sb", "Te", "I", "Xe", "Cs", "Ba", "La", "Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb",
    "Dy", "Ho", "Er", "Tm", "Yb", "Lu", "Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg", "Tl",
    "Pb", "Bi", "Po", "At", "Rn", "Fr", "Ra", "Ac", "Th", "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk",
    "Cf", "Es", "Fm", "Md", "No", "Lr", "Rf", "Db", "Sg", "Bh", "Hs", "Mt", "Ds", "Rg", "Cn", "Nh",
    "Fl", "Mc", "Lv", "Ts", "Og",
];
pub fn z_of(sym: &str) -> Option<i32> {
    SYMBOLS.iter().position(|s| *s == sym).map(|i| i as i32 + 1)
}
pub fn symbol_of(z: i32) -> &'static str {
    SYMBOLS.get((z - 1) as usize).copied().unwrap_or("?")
}

#[cfg(test)]
mod tests {
    use super::{atoms_per_gram_basis, mass_fractions, NA};
    use std::collections::BTreeMap;

    #[test]
    fn atom_fraction_and_atoms_per_gram_have_declared_meaning() {
        let equiatomic = BTreeMap::from([("Fe".into(), 1.0), ("C".into(), 1.0)]);
        let (atoms, diagnostic) = atoms_per_gram_basis(&equiatomic, "atom_fraction");
        let fe_atoms = diagnostic.elements["Fe"].1;
        let c_atoms = diagnostic.elements["C"].1;
        assert_eq!(fe_atoms, c_atoms);
        let mass =
            fe_atoms * diagnostic.elements["Fe"].0 / NA + c_atoms * diagnostic.elements["C"].0 / NA;
        assert!((mass - 1.0).abs() < 2e-16);
        assert!(!atoms.is_empty());

        let stated = BTreeMap::from([("Fe".into(), 2.5e20)]);
        let (_, diagnostic) = atoms_per_gram_basis(&stated, "atoms_per_g");
        assert_eq!(diagnostic.elements["Fe"].1, 2.5e20);
    }

    #[test]
    fn response_mass_fractions_follow_basis() {
        let equiatomic = BTreeMap::from([("Fe".into(), 1.0), ("C".into(), 1.0)]);
        let fractions = mass_fractions(&equiatomic, "atom_fraction").unwrap();
        assert!(fractions["Fe"] > fractions["C"]);
        assert!((fractions.values().sum::<f64>() - 1.0).abs() < 1e-15);
    }
}
