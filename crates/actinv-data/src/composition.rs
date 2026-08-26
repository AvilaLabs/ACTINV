//! Material composition -> atoms per gram, from the embedded natural-abundance and atomic-mass tables.
//! Mirrors controls/harness/composition.py; the P5-G2 control requires agreement on all 132 FNS compositions.
use crate::decay::Nuclide;
use crate::tables::{ISOTOPES, PROVENANCE};
use std::collections::{BTreeMap, HashMap, HashSet};

pub const NA: f64 = 6.02214076e23;
/// Atomic mass of a neutron in unified atomic mass units, used to convert ENDF AWR.
pub const NEUTRON_MASS_AMU: f64 = 1.00866491595;
pub type IsotopeInventory = BTreeMap<(i32, i32), f64>;

#[derive(Debug, Default)]
pub struct CompositionDiag {
    pub elements: BTreeMap<String, (f64, f64, usize)>,
    /// canonical name -> (ZA, LISO, molar mass in g/mol, atoms/g)
    pub explicit_nuclides: BTreeMap<String, (i32, i32, f64, f64)>,
    pub unknown: Vec<String>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum MaterialKey {
    Element(String),
    Nuclide {
        symbol: String,
        za: i32,
        liso: i32,
        canonical: String,
    },
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

/// Parse a natural-element or explicit-nuclide material key.
pub fn material_key(raw: &str) -> Result<MaterialKey, String> {
    if raw.is_empty() || raw != raw.trim() || !raw.is_ascii() {
        return Err(format!("malformed material composition key '{raw}'"));
    }
    let digit = raw.find(|character: char| character.is_ascii_digit());
    let Some(digit) = digit else {
        if !raw.chars().all(|character| character.is_ascii_alphabetic()) {
            return Err(format!("malformed material composition key '{raw}'"));
        }
        return Ok(MaterialKey::Element(cap(raw)));
    };
    let (symbol_raw, rest) = raw.split_at(digit);
    if !(1..=2).contains(&symbol_raw.len())
        || !symbol_raw
            .chars()
            .all(|character| character.is_ascii_alphabetic())
    {
        return Err(format!("malformed explicit nuclide key '{raw}'"));
    }
    let symbol = cap(symbol_raw);
    let z =
        z_of(&symbol).ok_or_else(|| format!("unknown element symbol in nuclide key '{raw}'"))?;
    let mass_len = rest
        .chars()
        .take_while(|character| character.is_ascii_digit())
        .count();
    if mass_len == 0 {
        return Err(format!("malformed explicit nuclide key '{raw}'"));
    }
    let mass: i32 = rest[..mass_len]
        .parse()
        .map_err(|_| format!("invalid mass number in nuclide key '{raw}'"))?;
    if !(1..=999).contains(&mass) {
        return Err(format!("invalid mass number in nuclide key '{raw}'"));
    }
    let suffix = &rest[mass_len..];
    let liso = if suffix.is_empty() {
        0
    } else if suffix.eq_ignore_ascii_case("m") {
        1
    } else if suffix
        .get(..1)
        .is_some_and(|prefix| prefix.eq_ignore_ascii_case("m"))
    {
        let state: i32 = suffix[1..]
            .parse()
            .map_err(|_| format!("invalid isomer state in nuclide key '{raw}'"))?;
        if state <= 0 {
            return Err(format!("invalid isomer state in nuclide key '{raw}'"));
        }
        state
    } else {
        return Err(format!("malformed explicit nuclide key '{raw}'"));
    };
    let canonical = if liso == 0 {
        format!("{symbol}{mass}")
    } else {
        format!("{symbol}{mass}m{liso}")
    };
    Ok(MaterialKey::Nuclide {
        symbol,
        za: z * 1000 + mass,
        liso,
        canonical,
    })
}

fn checked_keys(elements: &BTreeMap<String, f64>) -> Result<Vec<(MaterialKey, f64)>, String> {
    let mut normalized = HashSet::new();
    let mut natural_elements = HashSet::new();
    let mut explicit_elements = HashSet::new();
    let mut parsed = Vec::with_capacity(elements.len());
    for (raw, value) in elements {
        let key = material_key(raw)?;
        let (canonical, symbol, explicit) = match &key {
            MaterialKey::Element(symbol) => (symbol.clone(), symbol.clone(), false),
            MaterialKey::Nuclide {
                symbol, canonical, ..
            } => (canonical.clone(), symbol.clone(), true),
        };
        if !normalized.insert(canonical.clone()) {
            return Err(format!(
                "material composition keys collide after normalization at '{canonical}'"
            ));
        }
        if explicit {
            explicit_elements.insert(symbol);
        } else {
            natural_elements.insert(symbol);
        }
        parsed.push((key, *value));
    }
    if let Some(symbol) = natural_elements.intersection(&explicit_elements).next() {
        return Err(format!(
            "material composition cannot mix natural element '{symbol}' with its explicit isotopes"
        ));
    }
    Ok(parsed)
}

pub fn validate_material_keys(elements: &BTreeMap<String, f64>) -> Result<(), String> {
    checked_keys(elements).map(|_| ())
}

fn explicit_mass(
    za: i32,
    liso: i32,
    nuclides: &HashMap<(i32, i32), Nuclide>,
) -> Result<f64, String> {
    let nuclide = nuclides.get(&(za, liso)).ok_or_else(|| {
        format!(
            "explicit nuclide {}_{} is absent from the decay library",
            za, liso
        )
    })?;
    let mass = nuclide.awr * NEUTRON_MASS_AMU;
    if !mass.is_finite() || mass <= 0.0 {
        return Err(format!(
            "explicit nuclide {}_{} has invalid evaluated AWR {}",
            za, liso, nuclide.awr
        ));
    }
    Ok(mass)
}

/// Convert a mixed natural-element/explicit-nuclide material to atoms per gram.
pub fn material_atoms_per_gram(
    composition: &BTreeMap<String, f64>,
    basis: &str,
    nuclides: &HashMap<(i32, i32), Nuclide>,
) -> Result<(IsotopeInventory, CompositionDiag), String> {
    let parsed = checked_keys(composition)?;
    let mut masses = Vec::with_capacity(parsed.len());
    for (key, value) in &parsed {
        let mass = match key {
            MaterialKey::Element(symbol) => molar_mass(symbol),
            MaterialKey::Nuclide { za, liso, .. } if basis == "atoms_per_g" => nuclides
                .get(&(*za, *liso))
                .map(|nuclide| nuclide.awr * NEUTRON_MASS_AMU),
            MaterialKey::Nuclide { za, liso, .. } => Some(explicit_mass(*za, *liso, nuclides)?),
        };
        masses.push((*value, mass));
    }
    let atom_fraction_denominator: f64 = if basis == "atom_fraction" {
        masses
            .iter()
            .filter_map(|(value, mass)| mass.map(|mass| value * mass))
            .sum()
    } else {
        0.0
    };

    let mut out = BTreeMap::new();
    let mut diagnostic = CompositionDiag::default();
    for ((key, value), (_, mass)) in parsed.into_iter().zip(masses) {
        match key {
            MaterialKey::Element(symbol) => {
                let isotope_rows = isotopes(&symbol);
                let Some(molar) = mass else {
                    diagnostic.unknown.push(symbol);
                    continue;
                };
                let atoms = match basis {
                    "wt_percent" => NA * ((value / 100.0) / molar),
                    "atom_fraction" if atom_fraction_denominator > 0.0 => {
                        NA * value / atom_fraction_denominator
                    }
                    "atoms_per_g" => value,
                    _ => 0.0,
                };
                let z = z_of(&symbol).expect("element with abundance data has a Z");
                for (_, a, liso, abundance, _) in &isotope_rows {
                    *out.entry((z * 1000 + a, *liso)).or_insert(0.0) += atoms * abundance;
                }
                diagnostic
                    .elements
                    .insert(symbol, (molar, atoms, isotope_rows.len()));
            }
            MaterialKey::Nuclide {
                za,
                liso,
                canonical,
                ..
            } => {
                let molar = mass.unwrap_or(0.0);
                let atoms = match basis {
                    "wt_percent" => NA * ((value / 100.0) / molar),
                    "atom_fraction" if atom_fraction_denominator > 0.0 => {
                        NA * value / atom_fraction_denominator
                    }
                    "atoms_per_g" => value,
                    _ => 0.0,
                };
                *out.entry((za, liso)).or_insert(0.0) += atoms;
                diagnostic
                    .explicit_nuclides
                    .insert(canonical, (za, liso, molar, atoms));
            }
        }
    }
    Ok((out, diagnostic))
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

/// Initial elemental mass fractions, including explicit isotope keys aggregated by element.
pub fn material_mass_fractions(
    composition: &BTreeMap<String, f64>,
    basis: &str,
    nuclides: &HashMap<(i32, i32), Nuclide>,
) -> Result<BTreeMap<String, f64>, String> {
    let parsed = checked_keys(composition)?;
    let mut masses: BTreeMap<String, f64> = BTreeMap::new();
    for (key, value) in parsed {
        let (symbol, molar) = match key {
            MaterialKey::Element(symbol) => {
                let molar = molar_mass(&symbol).ok_or_else(|| {
                    format!("material element '{symbol}' has no natural-isotope mass data")
                })?;
                (symbol, molar)
            }
            MaterialKey::Nuclide {
                symbol, za, liso, ..
            } => (symbol, explicit_mass(za, liso, nuclides)?),
        };
        let mass = match basis {
            "wt_percent" => value,
            "atom_fraction" | "atoms_per_g" => value * molar,
            value => return Err(format!("unknown material basis '{value}'")),
        };
        *masses.entry(symbol).or_insert(0.0) += mass;
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
    use super::{atoms_per_gram_basis, mass_fractions, material_key, MaterialKey, NA};
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

    #[test]
    fn explicit_key_aliases_are_canonical() {
        assert_eq!(
            material_key("ta180M").unwrap(),
            MaterialKey::Nuclide {
                symbol: "Ta".into(),
                za: 73_180,
                liso: 1,
                canonical: "Ta180m1".into(),
            }
        );
        assert_eq!(
            material_key("FE").unwrap(),
            MaterialKey::Element("Fe".into())
        );
        assert!(material_key("Fe56m0").is_err());
        assert!(material_key("Xx56").is_err());
    }
}
