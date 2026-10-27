#![allow(non_snake_case)] // field names are the JSON wire format (boundaries_eV, temperature_K)
//! `actinv-spec-1`: the one problem description the CLI, the Python API and the harness all consume.
//! Unknown fields are an error (`deny_unknown_fields`) — a misspelt option must never be silently ignored.
use crate::pathways::PathwayOptions;
use crate::quantity::{
    AtomsPerGram, ElectronVolts, EnergyBoundaries, FluxMultiplier, Grams, GroupFluxes, Kelvin,
    Seconds,
};
pub use actinv_data::activation::Projectile;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Spec {
    pub spec: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub projectile: Projectile,
    /// Omitted (or an empty `path`) only for a decay-only run: every schedule step must then have
    /// zero flux, and chain building uses the decay data alone.
    #[serde(default)]
    pub library: LibraryRef,
    #[serde(default)]
    pub decay: DecayRef,
    pub material: Material,
    /// May be omitted for a decay-only run; the zero-flux placeholder is never read.
    #[serde(default = "placeholder_spectrum")]
    pub spectrum: Spectrum,
    pub schedule: Vec<Step>,
    #[serde(default)]
    pub options: Options,
    #[serde(default)]
    pub photon: PhotonOptions,
    #[serde(default)]
    pub fission_yields: FissionYieldOptions,
    #[serde(default)]
    pub uncertainty: Option<UncertaintyOptions>,
    #[serde(default)]
    pub radiological: Option<RadiologicalOptions>,
    #[serde(default)]
    pub damage: Option<DamageOptions>,
    #[serde(default)]
    pub self_shielding: Option<SelfShieldingOptions>,
    /// Complete per-step inventory report (`steps[].summary`); absent emits nothing and is
    /// byte-identical to pre-P122 output.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub summary: Option<SummaryOptions>,
    /// Opt-in full pathways/routes analysis (P127). Absence is byte-identical to pre-P127 output.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pathways: Option<PathwayOptions>,
    /// P126: groupwise kerma table; required by power-driven steps and, when present, adds a
    /// per-step `kerma` block to the result. Absent from any spec echo when not declared.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub kerma: Option<KermaOptions>,
    /// FISPACT-II `OVER` parity (P124): named per-nuclide replacements of one-group cross sections
    /// and half-lives, and of their declared uncertainties. Absent = the library data as built.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub overrides: Vec<NamedOverride>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LibraryRef {
    pub path: String,
    #[serde(default)]
    pub sha256: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HashedFileRef {
    pub path: String,
    pub sha256: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct UncertaintyOptions {
    /// MF=33 cross-section covariance. Required unless `channels` is exactly
    /// `["flux"]` (P93 flux-only mode), in which case it may be omitted:
    /// then MF=33 is not propagated and the band is the flux channel alone.
    /// When present, MF=33 is propagated exactly as before regardless of
    /// what else `channels` requests.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub covariance: Option<HashedFileRef>,
    #[serde(default)]
    pub responses: Vec<String>,
    /// Uncertainty channels propagated into the band. Absent or empty selects
    /// `cross_section_mf33` only (the P11/G2 behavior); `decay_constants` and
    /// `fission_yields` add first-order MF=8/MT=457 half-life and MF=8/MT=454
    /// independent-yield channels, each reported with its own coverage.
    /// `flux` (P93) adds the first-order transport-tally statistical-error
    /// channel from `spectrum.relative_error`.
    #[serde(default)]
    pub channels: Vec<String>,
    #[serde(default = "confidence_95")]
    pub confidence_level: f64,
    #[serde(default)]
    pub require_complete: bool,
    /// Optional value-of-information reporting: emit a ranked per-parameter
    /// variance-share table inside each requested response band (P50).
    /// Absence leaves the emitted record byte-identical to pre-P50 output.
    #[serde(default)]
    pub voi: Option<VoiOptions>,
    /// Optional isomer-resolved variance partition: each requested response
    /// band gains an `isomer` block splitting the propagated variance into
    /// isomer-product, isomer-target, isomer-decay and ground classes plus a
    /// ranked isomer-channel table (P58). Step-level pathway data gains an
    /// `isomer_pathway_shares` summary. Absence is byte-identical to pre-P58
    /// output.
    #[serde(default)]
    pub isomer: Option<IsomerOptions>,
    /// Optional measurement-design reporting: rank parameters and reaction
    /// blocks by the variance a perfect measurement would *remove*
    /// (Kalman-gain / Schur-complement reduction), not the share they carry
    /// (P60). Absence is byte-identical to pre-P60 output.
    #[serde(default)]
    pub design: Option<DesignOptions>,
    /// Optional declared unmodeled relative error u ≥ 0 (P63): every banded
    /// response's total variance gains `(u · nominal)²` in quadrature before
    /// intervals are formed. Declared, emitted, and re-derivable — never
    /// silently folded. A relative term cannot cover zero or missing-channel
    /// predictions; those are completeness defects, not uncertainty.
    /// Absence is byte-identical to pre-P63 output.
    #[serde(default)]
    pub unmodeled_relative: Option<f64>,
    /// Optional calibrated-error table (P72): resolves `unmodeled_relative`
    /// from an `actinv-unmodeled-table-1` artifact — `key` names the
    /// material family, else the dominant composition element is looked
    /// up; a miss falls to `fallback` if declared, else the table's own
    /// `default`. Mutually exclusive with `unmodeled_relative` — one
    /// declared source for the unmodeled term.
    #[serde(default)]
    pub unmodeled_table: Option<UnmodeledTableRef>,
    /// Optional D6a fold-in (P77): resolves `unmodeled_relative` from an
    /// `actinv-eval-spread-1` artifact — the sha-bound output of
    /// `actinv eval-spread`. The artifact's `suggested_unmodeled_relative`
    /// becomes the declared term. Mutually exclusive with
    /// `unmodeled_relative` and `unmodeled_table`.
    #[serde(default)]
    pub unmodeled_evalspread: Option<HashedFileRef>,
    /// Internal: integrate tangents for covariance-covered cross-section
    /// parameters only. Response standard deviations are unchanged —
    /// uncovered parameters contribute nothing to the covered contraction —
    /// but per-parameter sensitivity records for uncovered rows read zero,
    /// so this is for internal diagnostics (the study robustness
    /// first_order_comparison) whose consumption is the response std only.
    /// Never serialized; never valid in a user spec.
    #[serde(skip)]
    pub restrict_tangents_to_covered: bool,
}

/// A sha-bound reference to an `actinv-unmodeled-table-1` artifact.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct UnmodeledTableRef {
    pub path: String,
    pub sha256: String,
    /// material-family key into `per_material`; absent → the dominant
    /// composition element is looked up (title-cased).
    #[serde(default)]
    pub key: Option<String>,
    /// u used when the key is absent from `per_material` — else the
    /// table's own `default` applies.
    #[serde(default)]
    pub fallback: Option<f64>,
}

/// Reporting knob for `uncertainty.isomer` (P58): optional cap on the ranked
/// isomer-channel table per (step, response); defaults to `voi.top` when set,
/// else 20.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct IsomerOptions {
    #[serde(default)]
    pub top: Option<usize>,
}

/// Reporting knob for `uncertainty.design` (P60): optional cap on the ranked
/// parameter and reaction tables per (step, response); defaults to `voi.top`
/// when set, else 20.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DesignOptions {
    #[serde(default)]
    pub top: Option<usize>,
}

/// Reporting knob for `uncertainty.voi` (P50): how many ranked parameters to
/// emit per (step, response), in `|variance_share|` descending order.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VoiOptions {
    pub top: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RadiologicalOptions {
    pub table: HashedFileRef,
    #[serde(default)]
    pub responses: Vec<String>,
    #[serde(default)]
    pub require_complete: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DamageOptions {
    pub table: HashedFileRef,
    /// Per-element displacement energies in eV, keyed by canonical element symbol ("Fe").
    /// Mandatory for every element the table covers in the material.
    #[serde(default)]
    pub displacement_energy_eV: BTreeMap<String, f64>,
    #[serde(default)]
    pub require_complete: bool,
}

/// P126 kerma and power normalization: a hash-pinned `actinv-kerma-table-1` supplies per-group
/// heating kerma factors (NJOY HEATR MT=301, optionally MT=444 damage energy). A step that declares
/// `power_W_cm3` or `power_W_per_g` instead of `flux` takes its flux amplitude from the table and
/// the evolving composition, renormalized at sub-step boundaries until the power drift inside a
/// sub-interval is at most `power_tolerance`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct KermaOptions {
    pub table: HashedFileRef,
    /// Fail closed when a material nuclide lacks a heating row. Default false: uncovered
    /// nuclides contribute no heating and are named in the ledger.
    #[serde(default)]
    pub require_complete: bool,
    /// Largest relative departure of a sub-interval's end-of-interval power from the requested
    /// power before the step is split further. Open interval (0, 1).
    #[serde(default = "default_power_tolerance")]
    pub power_tolerance: f64,
    /// Cap on the equal sub-intervals one power-driven step is split into (1..=4096).
    #[serde(default = "default_max_substeps")]
    pub max_substeps: usize,
    /// How many of the largest heating contributors the per-step `kerma` block lists.
    #[serde(default = "default_kerma_top")]
    pub top: usize,
}

fn default_power_tolerance() -> f64 {
    1.0e-3
}

fn default_max_substeps() -> usize {
    64
}

fn default_kerma_top() -> usize {
    10
}

/// Finite-dilution self-shielding (P19): a hash-pinned `actinv-shield-table-1`
/// supplies per-(nuclide, group, channel) Bondarenko factors on the frozen
/// sigma0 x temperature grid. At run time the covered nuclide's effective
/// dilution selects the factor; each group's unresolved-range overlap fraction
/// blends it in. Missing coverage is named in the ledger, never silently
/// treated as factor one.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SelfShieldingOptions {
    /// The Bondarenko table. Required unless `geometry` selects the
    /// resolved-resonance sigmoid-curve model instead (the two are exclusive,
    /// as FISPACT-II's PROBTABLE and SSFGEOMETRY are).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub table: Option<HashedFileRef>,
    /// "composition" (default) computes per-nuclide sigma0 from the material
    /// composition; "fixed" applies `sigma0_b` to every covered nuclide.
    #[serde(default = "dilution_composition")]
    pub dilution: String,
    /// Required positive sigma0 in barns for dilution "fixed"; must be null
    /// (or absent) for "composition".
    #[serde(default)]
    pub sigma0_b: Option<f64>,
    /// Per-nuclide, per-group dilution cross sections in barns (FISPACT-II
    /// SSFDILUTION) that replace the computed background dilution of the
    /// Bondarenko path: nuclide name -> library group index (0-based,
    /// ascending energy) -> sigma0. Table mode only.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub dilution_overrides: BTreeMap<String, BTreeMap<usize, f64>>,
    /// Universal-sigmoid-curve self-shielding of the resolved resonances for a
    /// foil, wire, sphere or cylinder target (FISPACT-II SSFGEOMETRY).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub geometry: Option<SelfShieldingGeometry>,
}

fn dilution_composition() -> String {
    "composition".into()
}

/// Step-summary report (P122): FISPACT-II inventory-report parity. The filters only trim the
/// nuclide table; every total, element row and dominant ranking includes all states.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SummaryOptions {
    /// Emit the per-nuclide table (`summary.nuclides`).
    #[serde(default = "summary_true")]
    pub nuclides: bool,
    /// Emit the per-element table (`summary.elements`).
    #[serde(default = "summary_true")]
    pub elements: bool,
    /// Entries kept in each `summary.dominant` list, 1..=256.
    #[serde(default = "summary_dominant_top")]
    pub dominant_top: usize,
    /// FISPACT MIND: nuclide-table rows whose absolute atoms (`atoms_per_g * material.mass_g`)
    /// fall below this are omitted. Zero keeps every row.
    #[serde(default)]
    pub min_atoms: f64,
    /// False is FISPACT NOSTABLE: stable rows are omitted from the nuclide table.
    #[serde(default = "summary_true")]
    pub include_stable: bool,
    /// FISPACT-II DEPLETION: add `summary.depletion`, the per-step depletion table of every
    /// initial-material nuclide with its removal rate split into decay and reaction channels.
    /// Absent from the serialized options when false, so existing specs keep their bytes.
    #[serde(default, skip_serializing_if = "is_false")]
    pub depletion: bool,
}

fn summary_true() -> bool {
    true
}

fn summary_dominant_top() -> usize {
    10
}

impl Default for SummaryOptions {
    fn default() -> Self {
        Self {
            nuclides: true,
            elements: true,
            dominant_top: summary_dominant_top(),
            min_atoms: 0.0,
            include_stable: true,
            depletion: false,
        }
    }
}

/// Geometry self-shielding (P129): target shape and size, the material
/// density that turns the declared composition into number densities, and the
/// ENDF-6 evaluations whose MF=2 resolved resonance parameters drive the
/// universal sigmoid curve (Martinho, Goncalves and Salgado).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SelfShieldingGeometry {
    /// "foil", "wire", "sphere" or "cylinder".
    pub shape: String,
    /// Foil thickness, or wire/sphere/cylinder radius, in cm.
    pub length_cm: f64,
    /// Cylinder height in cm; required for "cylinder", rejected otherwise.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub height_cm: Option<f64>,
    /// Material density in g/cm^3: number density = atoms_per_gram * density. May be omitted when
    /// `material.density_g_cm3` is declared (the geometry then takes that value); when both are given they
    /// must agree to 1e-9 relative.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub density_g_cm3: Option<f64>,
    /// Hash-pinned ENDF-6 neutron evaluations (one nuclide each) carrying
    /// MF=2/MT=151 resolved resonance parameters.
    pub resonance_files: Vec<HashedFileRef>,
    /// Restrict the correction to these nuclides or elements (SSFCHOOSE);
    /// absent means every material nuclide the resonance files supply.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub choose: Option<Vec<String>>,
    /// "all" (default, as FISPACT-II applies the factor to every reaction of a
    /// shielded nuclide) or "capture" (radiative capture, MT 102, only).
    #[serde(default = "geometry_reactions_all")]
    pub reactions: String,
}

fn geometry_reactions_all() -> String {
    "all".into()
}

/// The target density of geometry self-shielding: the geometry's own value, else the material's.
/// Both given must agree to 1e-9 relative; one of the two is required.
pub fn resolve_geometry_density(own: Option<f64>, material: Option<f64>) -> Result<f64, String> {
    let valid = |value: f64| value.is_finite() && value > 0.0;
    match (own, material) {
        (Some(own), _) if !valid(own) => {
            Err("self_shielding.geometry.density_g_cm3 must be a positive finite number".into())
        }
        (None, Some(material)) if !valid(material) => {
            Err("material.density_g_cm3 must be finite and positive".into())
        }
        (Some(own), Some(material)) => {
            if (own - material).abs() > 1e-9 * own.abs().max(material.abs()) {
                Err(format!(
                    "self_shielding.geometry.density_g_cm3 {own} disagrees with material.density_g_cm3 {material}; give one of them or make them equal"
                ))
            } else {
                Ok(own)
            }
        }
        (Some(value), None) | (None, Some(value)) => Ok(value),
        (None, None) => {
            Err("self_shielding.geometry needs density_g_cm3 (or material.density_g_cm3)".into())
        }
    }
}

impl SelfShieldingGeometry {
    /// The density that turns the declared composition into number densities: the geometry's own,
    /// else the material's. Both given must agree to 1e-9 relative; one of the two is required.
    pub fn effective_density(&self, material_density_g_cm3: Option<f64>) -> Result<f64, String> {
        resolve_geometry_density(self.density_g_cm3, material_density_g_cm3)
    }

    fn validate(&self, material_density_g_cm3: Option<f64>) -> Result<(), String> {
        let shape = crate::shielding::GeometryShape::parse(&self.shape)?;
        // Dimensions: finite and positive, height only for a cylinder.
        shape.effective_length_cm(self.length_cm, self.height_cm)?;
        self.effective_density(material_density_g_cm3)?;
        if self.resonance_files.is_empty() {
            return Err(
                "self_shielding.geometry needs resonance data: resonance_files is empty".into(),
            );
        }
        match self.reactions.as_str() {
            "all" | "capture" => {}
            value => {
                return Err(format!(
                    "unknown self_shielding.geometry.reactions '{value}' (expected all or capture)"
                ));
            }
        }
        if let Some(choose) = &self.choose {
            if choose.is_empty() {
                return Err(
                    "self_shielding.geometry.choose must name at least one nuclide or element when present"
                        .into(),
                );
            }
            for raw in choose {
                actinv_data::composition::material_key(raw)
                    .map_err(|error| format!("self_shielding.geometry.choose: {error}"))?;
            }
        }
        Ok(())
    }
}

fn confidence_95() -> f64 {
    0.95
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FissionYieldOptions {
    #[serde(default)]
    pub files: Vec<HashedFileRef>,
    #[serde(default = "spectrum_average")]
    pub energy: String,
    #[serde(default)]
    pub fixed_energy_eV: Option<f64>,
    /// P153: which yield set the matrix uses, `independent` (MT=454, the default) or `cumulative` (MT=459). The
    /// energy selection applies to either. Absent = `independent`, and the spec echo is unchanged.
    #[serde(
        default = "independent_yields",
        skip_serializing_if = "is_independent_yields"
    )]
    pub yields: String,
    /// P153: restrict which parents' fission feeds products. A parent outside the selection keeps its fission
    /// reaction and sends its whole fission rate to leakage, booked per parent in the ledger. Absent = every
    /// parent with a yield file is selected.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub parents: Option<FissionParentSelection>,
    /// Spontaneous-fission inputs: independent-yield files that route each state's SF branch to
    /// products instead of leakage, and neutron multiplicities for the SF neutron source. Absent =
    /// SF branches stay on leakage and the spec echo is byte-identical to a pre-P125 spec.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub spontaneous: Option<SpontaneousFissionOptions>,
}

fn spectrum_average() -> String {
    "spectrum_average".into()
}

fn independent_yields() -> String {
    "independent".into()
}

#[allow(clippy::ptr_arg)] // the signature serde's skip_serializing_if requires
fn is_independent_yields(value: &String) -> bool {
    value == "independent"
}

impl Default for FissionYieldOptions {
    fn default() -> Self {
        Self {
            files: Vec::new(),
            energy: spectrum_average(),
            fixed_energy_eV: None,
            yields: independent_yields(),
            parents: None,
            spontaneous: None,
        }
    }
}

/// P153 `fission_yields.parents`: exactly one of `include` or `exclude`, each a nonempty list of nuclide names.
#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FissionParentSelection {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub include: Option<Vec<String>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub exclude: Option<Vec<String>>,
}

/// `(ZA, LISO)` of an explicit nuclide name, the naming the inventory uses.
pub(crate) fn nuclide_key(raw: &str, field: &str) -> Result<(i32, i32), String> {
    match actinv_data::composition::material_key(raw) {
        Ok(actinv_data::composition::MaterialKey::Nuclide { za, liso, .. }) => Ok((za, liso)),
        Ok(_) => Err(format!(
            "{field} '{raw}' must be an explicit nuclide, e.g. 'U235'"
        )),
        Err(error) => Err(format!("{field} '{raw}': {error}")),
    }
}

/// A fission parent selection: whether the named parents are included (else excluded), and their (ZA, LISO) keys.
pub type ParentSelection = (bool, std::collections::BTreeSet<(i32, i32)>);

impl FissionYieldOptions {
    /// The yield set the matrix reads. Spec validation guarantees `yields` is one of the two names.
    pub fn yield_kind(&self) -> actinv_data::fission::YieldKind {
        if self.yields == "cumulative" {
            actinv_data::fission::YieldKind::Cumulative
        } else {
            actinv_data::fission::YieldKind::Independent
        }
    }

    /// The parent selection as `(is_include, parents)`, or `None` when every parent is selected.
    pub fn parent_selection(&self) -> Result<Option<ParentSelection>, String> {
        let Some(selection) = &self.parents else {
            return Ok(None);
        };
        let (include, names) = match (&selection.include, &selection.exclude) {
            (Some(_), Some(_)) => {
                return Err("fission_yields.parents takes include or exclude, not both".into())
            }
            (None, None) => return Err("fission_yields.parents needs include or exclude".into()),
            (Some(names), None) => (true, names),
            (None, Some(names)) => (false, names),
        };
        let field = if include {
            "fission_yields.parents.include"
        } else {
            "fission_yields.parents.exclude"
        };
        if names.is_empty() {
            return Err(format!("{field} must not be empty"));
        }
        let keys = names
            .iter()
            .map(|raw| nuclide_key(raw, field))
            .collect::<Result<std::collections::BTreeSet<_>, String>>()?;
        Ok(Some((include, keys)))
    }

    /// Whether `parent`'s fission feeds products under the declared selection.
    pub fn selects(&self, parent: (i32, i32)) -> bool {
        match self.parent_selection() {
            Ok(Some((true, keys))) => keys.contains(&parent),
            Ok(Some((false, keys))) => !keys.contains(&parent),
            _ => true,
        }
    }
}

/// P125 spontaneous fission, all hash-pinned. A state with an SF branch and no yield data keeps
/// that branch on leakage and is named in the ledger; a state with an SF branch and no nu-bar adds
/// to the neutron source's uncovered decay rate. Nothing is inferred for either.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SpontaneousFissionOptions {
    /// ENDF-6 MF=8/MT=454 spontaneous-fission yield files (the ENDF/B `sfy` sublibrary). A file may
    /// hold one material or the whole sublibrary; the yields are read at the lowest tabulated
    /// incident energy (E = 0 for spontaneous fission).
    #[serde(default)]
    pub files: Vec<HashedFileRef>,
    /// ENDF file whose MF=1/MT=452 (else MT=456) gives each state's spontaneous-fission nu-bar: the
    /// ENDF/B decay sublibrary. Do not name a neutron sublibrary (its MF=1 is the induced value).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub nubar_endf: Option<HashedFileRef>,
    /// JSON `{"source": "<citation>", "nubar": {"Cf252": <value>, ...}}`. Entries override
    /// `nubar_endf` for the same state; the citation is required and recorded in the certificate.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub nubar_table: Option<HashedFileRef>,
}

/// Opt-in cumulative fission count and burn-up per step (P125): neutron-induced fissions from the
/// MT=18 reaction rate plus one per spontaneous-fission decay, as a `fission.count` block.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FissionCountOptions {
    /// Energy per fission for burn-up in MWd/kg. The default is a conventional 200 MeV, not a
    /// nuclide-specific value; supply the figure your comparison uses.
    #[serde(default = "fission_energy_mev")]
    pub energy_per_fission_MeV: f64,
}

fn fission_energy_mev() -> f64 {
    crate::fission_count::DEFAULT_ENERGY_PER_FISSION_MEV
}

impl Default for FissionCountOptions {
    fn default() -> Self {
        Self {
            energy_per_fission_MeV: fission_energy_mev(),
        }
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecayRef {
    pub primary: String,
    #[serde(default)]
    pub fallback: Option<String>,
    /// Path to an `actinv-decay-overrides-1` document: curated,
    /// source-attributed per-nuclide corrections applied after the
    /// primary+fallback merge, recorded in the ledger.
    #[serde(default)]
    pub overrides: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PhotonOptions {
    #[serde(default = "photon_groups")]
    pub group_structure: String,
    #[serde(default)]
    pub group_boundaries_eV: Option<Vec<f64>>,
    #[serde(default)]
    pub response: Option<HashedFileRef>,
    #[serde(default = "build_up_two")]
    pub build_up_factor: f64,
    #[serde(default = "gamma_cutoff")]
    pub gamma_constant_cutoff_eV: f64,
    /// FISPACT-II `DOSE 2` analog: also report the dose rate at a distance from the step's
    /// photon source treated as a point source. Absent leaves every output unchanged.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub point_source: Option<PointSourceOptions>,
    /// FISPACT-II `BREMSSTRAHLUNG` analog: a separately reported bremsstrahlung photon
    /// component for the listed beta emitters. Absent leaves every output unchanged.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub bremsstrahlung: Option<BremsstrahlungOptions>,
    /// FISPACT-II `SPEK` analog: what to do with a nuclide whose decay record gives a mean
    /// electromagnetic energy but no photon spectrum. `"report"` (default) books its power as
    /// unrepresented; `"estimate"` places an exponential estimate in the groups and reports
    /// dose bounds. The default leaves every output unchanged.
    #[serde(default, skip_serializing_if = "MissingSpectra::is_report")]
    pub missing_spectra: MissingSpectra,
    /// FISPACT-II `NOXRAY` analog: drop the decay records' X-ray spectra (STYP 9) from the photon
    /// source after scaling each nuclide's spectrum to its mean electromagnetic energy.
    #[serde(default, skip_serializing_if = "is_false")]
    pub exclude_xrays: bool,
}

/// Treatment of nuclides with a mean electromagnetic energy and no photon spectrum.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum MissingSpectra {
    #[default]
    Report,
    Estimate,
}

impl MissingSpectra {
    fn is_report(&self) -> bool {
        *self == Self::Report
    }
}
fn photon_groups() -> String {
    "fispact-24".into()
}
fn build_up_two() -> f64 {
    2.0
}
fn gamma_cutoff() -> f64 {
    2.0e4
}
impl Default for PhotonOptions {
    fn default() -> Self {
        Self {
            group_structure: photon_groups(),
            group_boundaries_eV: None,
            response: None,
            build_up_factor: build_up_two(),
            gamma_constant_cutoff_eV: gamma_cutoff(),
            point_source: None,
            bremsstrahlung: None,
            missing_spectra: MissingSpectra::Report,
            exclude_xrays: false,
        }
    }
}

/// Smallest source-to-detector distance FISPACT-II accepts for `DOSE 2` (manual section 4.14:
/// a smaller `dist` is reset to 0.3 m). ACTINV rejects it instead of clamping.
pub const POINT_SOURCE_MIN_DISTANCE_M: f64 = 0.3;

/// Dry air near sea level, density 1.20479e-3 g/cm3: NIST Hubbell & Seltzer, X-Ray Mass
/// Attenuation Coefficients (NISTIR 5632), Table 2 (the material of the table-4 air curve).
const NIST_DRY_AIR_DENSITY_G_CM3: f64 = 1.20479e-3;

fn point_source_air_attenuation() -> bool {
    true
}
fn point_source_air_density() -> f64 {
    NIST_DRY_AIR_DENSITY_G_CM3
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PointSourceOptions {
    /// Source-to-detector distance in metres; at least `POINT_SOURCE_MIN_DISTANCE_M`.
    pub distance_m: f64,
    /// Apply the air transmission `exp(-mu r)` of manual Eq. (63). Needs an
    /// `air_mass_attenuation` curve in the photon response.
    #[serde(default = "point_source_air_attenuation")]
    pub air_attenuation: bool,
    /// Air density converting the response's air mass attenuation to a linear coefficient.
    #[serde(default = "point_source_air_density")]
    pub air_density_g_cm3: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BremsstrahlungOptions {
    /// Canonical nuclide names (`Cl36`, `Ar42`, `K42`), as FISPACT-II's `BREMSSTRAHLUNG` list.
    pub nuclides: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Material {
    #[serde(default = "one")]
    pub mass_g: f64,
    #[serde(default = "wt")]
    pub basis: String,
    pub composition: BTreeMap<String, f64>,
    /// Material mass density in g/cm^3. Converts a step's `power_W_cm3` to W per gram (P126) and
    /// supplies the step summary's per-cm^3 totals (P122).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub density_g_cm3: Option<f64>,
}
fn one() -> f64 {
    1.0
}
fn wt() -> String {
    "wt_percent".into()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Spectrum {
    #[serde(default = "g709")]
    pub structure: String,
    pub flux_per_group: Vec<f64>,
    /// total projectile flux (particles cm^-2 s^-1); the group values are scaled to it when given
    #[serde(default)]
    pub total: Option<f64>,
    /// ascending group boundaries in eV; required only when `structure` is "custom"
    #[serde(default)]
    pub boundaries_eV: Option<Vec<f64>>,
    /// true when `flux_per_group` is listed highest-energy first, as FISPACT fluxes files are
    #[serde(default)]
    pub descending: bool,
    /// P93: relative statistical error of each group's transport tally, same
    /// length and order as `flux_per_group` (honouring `descending`). Used
    /// only when `uncertainty.channels` requests `"flux"`; accepted and
    /// unused otherwise.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub relative_error: Option<Vec<f64>>,
    /// P124 (FISPACT-II `GRPCONVERT`): when present, `flux_per_group` and `boundaries_eV` describe
    /// the spectrum in its *own* groups and `structure` names the library structure it is
    /// converted to inside the run.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rebin: Option<SpectrumRebin>,
    /// First-wall loading in MW m^-2 that the group fluxes correspond to, as recorded in a
    /// FISPACT-II fluxes file. A step's `wall_loading_MW_m2` is scaled against it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub wall_loading_MW_m2: Option<f64>,
}

/// Spectrum conversion options (FISPACT-II `GRPCONVERT`, `CNVTYPE`, `LIMGRP`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SpectrumRebin {
    /// How flux is distributed inside a source group: `"equal_lethargy"` (default, `CNVTYPE 0`)
    /// or `"equal_energy"` (`CNVTYPE 1`).
    #[serde(default = "equal_lethargy")]
    pub convention: String,
    /// `LIMGRP` lower limit: source flux below it is folded into the first group above it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub emin_eV: Option<f64>,
    /// `LIMGRP` upper limit: source flux above it is folded into the last group below it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub emax_eV: Option<f64>,
}
fn equal_lethargy() -> String {
    "equal_lethargy".into()
}

/// One named data replacement (FISPACT-II `OVER`). `kind` selects the sub-keyword:
/// `cross_section` (`ACROSS`: the reaction's one-group collapsed cross section in barns),
/// `cross_section_scale` (a multiplier on that collapsed value), `half_life` (`ALAM`),
/// `cross_section_uncertainty` (`ADCROSS`) and `half_life_uncertainty` (`ADLAM`). The last two
/// take a relative standard uncertainty rather than FISPACT-II's error factor.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NamedOverride {
    /// Explicit nuclide, e.g. `Pu239`; for the cross-section kinds the reaction's target.
    pub nuclide: String,
    pub kind: String,
    /// ENDF reaction number selecting the library rows; required by the cross-section kinds and
    /// forbidden for the half-life kinds. (FISPACT-II selects by daughter; a reaction's MT is the
    /// unambiguous equivalent, and MT 18 is its `Fission`.)
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mt: Option<i32>,
    pub value: f64,
    /// Time unit of a `half_life` value (`s` by default; any `schedule` duration unit, including
    /// FISPACT-II's `SECS`, `MINS`, `HOURS`, `DAYS`, `YEARS`).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub units: Option<String>,
}

impl NamedOverride {
    pub const KINDS: [&'static str; 5] = [
        "cross_section",
        "cross_section_scale",
        "half_life",
        "cross_section_uncertainty",
        "half_life_uncertainty",
    ];

    pub(crate) fn is_cross_section_value(&self) -> bool {
        matches!(self.kind.as_str(), "cross_section" | "cross_section_scale")
    }

    /// The `half_life` value in seconds.
    pub fn half_life_seconds(&self) -> Result<f64, String> {
        let units = self.units.as_deref().unwrap_or("s");
        let seconds = self.value * parse_duration(&format!("1 {units}"))?;
        if !seconds.is_finite() || seconds <= 0.0 {
            return Err(format!(
                "override half_life for '{}' must be a finite positive time",
                self.nuclide
            ));
        }
        Ok(seconds)
    }
}

/// The zero-flux, single-group spectrum a decay-only spec may leave out.
fn placeholder_spectrum() -> Spectrum {
    Spectrum {
        structure: "custom".into(),
        flux_per_group: vec![0.0],
        total: None,
        boundaries_eV: Some(vec![1.0, 2.0]),
        descending: false,
        relative_error: None,
        rebin: None,
        wall_loading_MW_m2: None,
    }
}

impl Spectrum {
    /// Whether this is the zero-flux stand-in an omitted `spectrum` deserializes to.
    pub(crate) fn is_placeholder(&self) -> bool {
        let stand_in = placeholder_spectrum();
        self.structure == stand_in.structure
            && self.flux_per_group == stand_in.flux_per_group
            && self.boundaries_eV == stand_in.boundaries_eV
            && self.total.is_none()
            && !self.descending
            && self.relative_error.is_none()
            && self.rebin.is_none()
            && self.wall_loading_MW_m2.is_none()
    }

    /// A positive declared `total` needs a group shape to scale; an all-zero shape
    /// would silently discard it and turn the run into pure decay.
    fn check_total_has_shape(&self, label: &str) -> Result<(), String> {
        if self.total.is_some_and(|total| total > 0.0)
            && self.flux_per_group.iter().sum::<f64>() <= 0.0
        {
            return Err(format!(
                "{label}.total is positive but flux_per_group sums to zero: there is no shape to scale"
            ));
        }
        Ok(())
    }

    /// Structure check of a schedule-step spectrum against the projectile of the library the step runs on:
    /// the named structure must suit the projectile (the FISPACT-II standard structures such as fispact-709 are
    /// neutron grids, fispact-162 every other projectile's), the flux count must match it, and a custom
    /// structure needs matching ascending boundaries.
    fn check_structure_for(&self, projectile: Projectile, label: &str) -> Result<(), String> {
        if self.structure == "custom" {
            let boundaries = self
                .boundaries_eV
                .as_ref()
                .ok_or_else(|| format!("{label} custom structure needs boundaries_eV"))?;
            if boundaries.len() != self.flux_per_group.len() + 1 {
                return Err(format!(
                    "{label} boundaries_eV has {} entries; {} groups need {}",
                    boundaries.len(),
                    self.flux_per_group.len(),
                    self.flux_per_group.len() + 1
                ));
            }
            if boundaries.iter().any(|value| !value.is_finite())
                || boundaries.windows(2).any(|w| w[1] <= w[0])
            {
                return Err(format!(
                    "{label} boundaries_eV must be finite and strictly ascending"
                ));
            }
            return Ok(());
        }
        let (base_name, refine) = match self.structure.rsplit_once('x') {
            Some((base, count)) => match count.parse::<usize>() {
                Ok(count) => (base, Some(count)),
                Err(_) => (self.structure.as_str(), None),
            },
            None => (self.structure.as_str(), None),
        };
        if let Some(count) = refine {
            if !(2..=64).contains(&count) {
                return Err(format!(
                    "{label} structure refinement must be in 2..=64, got {count}"
                ));
            }
        }
        let factor = refine.unwrap_or(1);
        // The same registry as the base spectrum: every FISPACT-II standard neutron structure, and the
        // CCFE-162 grid for the other projectiles.
        let (groups, neutron_grid) = match base_name {
            "fispact-162" => (162 * factor, false),
            value => match actinv_data::groups::GroupStructure::named(value)? {
                Some(base) if actinv_data::groups::GroupStructure::is_neutron_named(value) => {
                    (base.groups() * factor, true)
                }
                _ => return Err(format!("unknown {label} structure '{value}'")),
            },
        };
        if neutron_grid != projectile.is_neutron() {
            return Err(format!(
                "{label} structure '{}' does not suit the {} projectile of the step's library",
                self.structure,
                projectile.name()
            ));
        }
        if self.flux_per_group.len() != groups {
            return Err(format!(
                "{label} structure '{}' requires {groups} flux values, got {}",
                self.structure,
                self.flux_per_group.len()
            ));
        }
        Ok(())
    }

    /// Group fluxes in ascending-energy order, scaled to `total` when given.
    pub fn ascending_flux(&self) -> Vec<f64> {
        let mut f = self.flux_per_group.clone();
        if self.descending {
            f.reverse();
        }
        if let Some(t) = self.total {
            let s: f64 = f.iter().sum();
            if s > 0.0 {
                let k = t / s;
                for v in f.iter_mut() {
                    *v *= k;
                }
            }
        }
        f
    }

    /// P93: `relative_error` in ascending-energy order (honouring
    /// `descending`, like `ascending_flux`). `total` scaling never touches a
    /// *relative* error, so it is not rescaled here.
    pub fn ascending_relative_error(&self) -> Option<Vec<f64>> {
        self.relative_error.as_ref().map(|errors| {
            let mut e = errors.clone();
            if self.descending {
                e.reverse();
            }
            e
        })
    }

    /// The flux the solver uses, in ascending library-group order. Without `rebin` this is
    /// `ascending_flux()`; with it, the supplied spectrum is first clipped to the `LIMGRP`
    /// limits, then distributed onto the named destination structure under the declared
    /// convention. The second element is the conversion record the ledger carries.
    pub fn resolved_flux(
        &self,
        scope: &str,
    ) -> Result<(Vec<f64>, Option<serde_json::Value>), String> {
        let Some(rebin) = &self.rebin else {
            return Ok((self.ascending_flux(), None));
        };
        let destination = actinv_data::groups::GroupStructure::resolve_named(&self.structure)?
            .ok_or_else(|| {
                format!(
                    "{scope}.rebin needs a named library structure in {scope}.structure, got '{}'",
                    self.structure
                )
            })?;
        let source_boundaries = self.boundaries_eV.as_ref().ok_or_else(|| {
            format!("{scope}.rebin needs the source groups in {scope}.boundaries_eV")
        })?;
        let clipped = crate::flux::clip_spectrum_range(
            source_boundaries,
            &self.ascending_flux(),
            rebin.emin_eV,
            rebin.emax_eV,
        )
        .map_err(|error| format!("{scope}.rebin: {error}"))?;
        let converted = match rebin.convention.as_str() {
            "equal_lethargy" => crate::flux::rebin_equal_lethargy(
                &clipped.boundaries,
                &clipped.flux_per_group,
                &destination.boundaries_ev,
            ),
            "equal_energy" => crate::flux::rebin_equal_energy(
                &clipped.boundaries,
                &clipped.flux_per_group,
                &destination.boundaries_ev,
            ),
            other => return Err(format!("unknown {scope}.rebin.convention '{other}'")),
        }
        .map_err(|error| format!("{scope}.rebin: {error}"))?;
        if converted.source_total > 0.0 && converted.destination_total <= 0.0 {
            return Err(format!(
                "{scope}.rebin: none of the supplied flux lands in the destination structure '{}' \
                 ({} below it, {} above it); the run would be pure decay",
                self.structure, converted.underflow, converted.overflow
            ));
        }
        let record = serde_json::json!({
            "scope": scope,
            "convention": rebin.convention,
            "cnvtype": if rebin.convention == "equal_energy" { 1 } else { 0 },
            "destination_structure": self.structure,
            "source_groups": self.flux_per_group.len(),
            "destination_groups": converted.flux_per_group.len(),
            "emin_eV": rebin.emin_eV,
            "emax_eV": rebin.emax_eV,
            "limit_flux_moved_from_below": clipped.moved_from_below,
            "limit_flux_moved_from_above": clipped.moved_from_above,
            "source_total": converted.source_total,
            "destination_total": converted.destination_total,
            "outside_destination_below": converted.underflow,
            "outside_destination_above": converted.overflow,
            "flux_conservation_residual": converted.relative_closure,
            "exact_grid": converted.exact_grid,
        });
        Ok((converted.flux_per_group, Some(record)))
    }

    /// Structural checks of the P124 spectrum fields (`wall_loading_MW_m2`, `rebin`) that do not
    /// need the destination's flux values.
    fn check_rebin(&self, label: &str) -> Result<(), String> {
        if self
            .wall_loading_MW_m2
            .is_some_and(|wall| !wall.is_finite() || wall <= 0.0)
        {
            return Err(format!(
                "{label}.wall_loading_MW_m2 must be finite and positive"
            ));
        }
        let Some(rebin) = &self.rebin else {
            return Ok(());
        };
        match rebin.convention.as_str() {
            "equal_lethargy" | "equal_energy" => {}
            other => return Err(format!("unknown {label}.rebin.convention '{other}'")),
        }
        for (name, limit) in [("emin_eV", rebin.emin_eV), ("emax_eV", rebin.emax_eV)] {
            if limit.is_some_and(|value| !value.is_finite() || value <= 0.0) {
                return Err(format!("{label}.rebin.{name} must be finite and positive"));
            }
        }
        if let (Some(low), Some(high)) = (rebin.emin_eV, rebin.emax_eV) {
            if low >= high {
                return Err(format!("{label}.rebin.emin_eV must be below emax_eV"));
            }
        }
        if self.structure == "custom" {
            return Err(format!(
                "{label}.rebin converts to a library structure: {label}.structure must name one, not 'custom'"
            ));
        }
        if self.relative_error.is_some() {
            return Err(format!(
                "{label}.relative_error cannot accompany {label}.rebin: it is indexed by the \
                 supplied groups, not the library's"
            ));
        }
        let boundaries = self.boundaries_eV.as_ref().ok_or_else(|| {
            format!("{label}.rebin needs the source groups in {label}.boundaries_eV")
        })?;
        if boundaries.len() != self.flux_per_group.len() + 1 {
            return Err(format!(
                "{label}.boundaries_eV has {} entries; {} source groups need {}",
                boundaries.len(),
                self.flux_per_group.len(),
                self.flux_per_group.len() + 1
            ));
        }
        if boundaries
            .iter()
            .any(|value| !value.is_finite() || *value <= 0.0)
            || boundaries.windows(2).any(|pair| pair[1] <= pair[0])
        {
            return Err(format!(
                "{label}.boundaries_eV must be finite, positive and strictly ascending"
            ));
        }
        // the limits are a pure function of the declared source groups: a limit that lies at or
        // beyond the whole spectrum is known now, not at run time
        crate::flux::clip_spectrum_range(
            boundaries,
            &self.flux_per_group,
            rebin.emin_eV,
            rebin.emax_eV,
        )
        .map_err(|error| format!("{label}.rebin: {error}"))?;
        Ok(())
    }
}
fn g709() -> String {
    "fispact-709".into()
}

/// 14 MeV neutron current, in n cm^-2 s^-1, that FISPACT-II documents as equivalent to a first-wall
/// loading of 1 MW m^-2: "a current C, of 4.44 x 10^13 n cm^-2 s^-1 is equivalent to 1 MW m^-2"
/// (FISPACT-II User Manual UKAEA-R(18)001, section 4.91 WALL, closing paragraph). FISPACT-II's own
/// `WALL` conversion scales the flux of the supplied spectrum by wall / (the file's wall loading)
/// instead; this constant is only the stand-in when the spectrum declares no wall loading.
pub const WALL_CURRENT_PER_MW_M2: f64 = 4.44e13;

/// The flux multiplier a `wall_loading_MW_m2` step stands for, with the basis it was derived on.
///
/// With the spectrum's own `reference` loading, FISPACT-II section 4.91 applies: the step flux is
/// `(wall / reference)` times the sum of the spectrum's group fluxes, so the multiplier on the
/// spectrum is `wall / reference` ("If a wall loading of 2.0 MW m-2 was input then a flux value of
/// (2.0/4.15) x 1.80 x 10^15 n cm-2 s-1 would be used"). Without one, the step flux is
/// `wall * WALL_CURRENT_PER_MW_M2` and the multiplier is that over the spectrum's group sum, so the
/// step flux no longer depends on the spectrum's `total` (only its shape matters), and flux the
/// `rebin` places outside the destination structure is renormalised away.
pub(crate) fn wall_flux_multiplier(
    wall: f64,
    reference: Option<f64>,
    total: f64,
) -> Result<(f64, &'static str), String> {
    // a zero loading is a cooling step whatever the spectrum
    if wall == 0.0 {
        return Ok((
            0.0,
            if reference.is_some() {
                "spectrum_reference_wall_loading"
            } else {
                "14_MeV_current_equivalence"
            },
        ));
    }
    match reference {
        Some(reference) => Ok((wall / reference, "spectrum_reference_wall_loading")),
        None if total > 0.0 => Ok((
            wall * WALL_CURRENT_PER_MW_M2 / total,
            "14_MeV_current_equivalence",
        )),
        None => Err(
            "a wall-loading step needs a spectrum with positive total flux or a declared \
             wall_loading_MW_m2 reference"
                .into(),
        ),
    }
}

/// One schedule segment. Exactly one irradiation driver is given on the wire: `flux`,
/// `wall_loading_MW_m2` or a power target (`power_W_cm3` / `power_W_per_g`; an explicit `flux: 0`
/// beside the target is accepted). A wall-loading step carries `flux: 0.0` here and its multiplier
/// is derived against the step's spectrum by `Spec::physical_inputs`; a power-driven step's
/// multipliers are solved at run time.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(try_from = "StepWire", into = "StepWire")]
pub struct Step {
    /// duration with a unit: "300 s", "5 min", "7 h", "1 d", "100 y"
    pub dt: String,
    /// multiplier on the spectrum's total during this step; 0 is cooling. A power-driven step
    /// (`power_W_cm3` / `power_W_per_g`) or a wall-loading step carries 0 here: its multipliers are
    /// derived at run time.
    pub flux: f64,
    /// optional per-step spectrum replacing the base `spectrum` for this step's duration;
    /// the `flux` multiplier scales this spectrum's total. Must share the base spectrum's
    /// structure and group count.
    pub spectrum: Option<Spectrum>,
    /// optional constant feed during this step: explicit nuclide key -> atoms s^-1 g^-1 of material
    pub feed: Option<BTreeMap<String, f64>>,
    /// optional first-order removal during this step: nuclide or element key -> s^-1
    pub removal: Option<BTreeMap<String, f64>>,
    /// optional activation-library override from this step onward (until a later step names another
    /// library, or names the base `library` again to return to it). The step must also declare its own
    /// `spectrum`, in the new library's group structure; later steps keep using that spectrum unless they
    /// declare theirs. The inventory carries across the switch; only the collapsed reaction rates change.
    pub library: Option<LibraryRef>,
    /// the projectile of the overriding `library`; required when it differs from the spec's `projectile`
    /// and rejected without a `library`
    pub projectile: Option<Projectile>,
    /// P126: irradiate at the flux amplitude that makes the material's kerma heating equal this
    /// power density (W cm^-3, needs `material.density_g_cm3`). Alternative to `flux`.
    pub power_W_cm3: Option<f64>,
    /// P126: as `power_W_cm3`, in W per gram of material. Alternative to `flux`.
    pub power_W_per_g: Option<f64>,
    /// alternative to `flux` (FISPACT-II `WALL`): first-wall loading in MW m^-2 for this step
    pub wall_loading_MW_m2: Option<f64>,
    /// optional instantaneous partition (FISPACT-II `PARTITION`) at the start of this step:
    /// element symbol -> fraction of that element's atoms kept (0 removes it all, 1 keeps it all)
    pub partition: Option<BTreeMap<String, f64>>,
}

/// The wire form of [`Step`]: `flux` is optional so that "exactly one irradiation driver" (flux,
/// `wall_loading_MW_m2`, or a power target) is decidable. An explicit `flux: 0` beside a power
/// target is accepted, because a power-driven step serializes that zero.
#[doc(hidden)]
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StepWire {
    dt: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    flux: Option<f64>,
    #[serde(default)]
    spectrum: Option<Spectrum>,
    #[serde(default)]
    feed: Option<BTreeMap<String, f64>>,
    #[serde(default)]
    removal: Option<BTreeMap<String, f64>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    library: Option<LibraryRef>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    projectile: Option<Projectile>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    power_W_cm3: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    power_W_per_g: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    wall_loading_MW_m2: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    partition: Option<BTreeMap<String, f64>>,
}

impl TryFrom<StepWire> for Step {
    type Error = String;

    fn try_from(wire: StepWire) -> Result<Self, String> {
        let power_driven = wire.power_W_cm3.is_some() || wire.power_W_per_g.is_some();
        if wire.power_W_cm3.is_some() && wire.power_W_per_g.is_some() {
            return Err(
                "schedule step declares both power_W_cm3 and power_W_per_g; they are mutually exclusive"
                    .into(),
            );
        }
        if power_driven && wire.wall_loading_MW_m2.is_some() {
            return Err(
                "schedule step declares both wall_loading_MW_m2 and a power target (power_W_cm3 / power_W_per_g); they are mutually exclusive"
                    .into(),
            );
        }
        // A power-driven step serializes `flux: 0`, so an explicit zero beside a power target is
        // accepted; any other flux contradicts it.
        let flux = match (wire.flux, wire.wall_loading_MW_m2) {
            (Some(_), Some(_)) => {
                return Err(
                    "a schedule step declares both flux and wall_loading_MW_m2; give exactly one"
                        .into(),
                );
            }
            (Some(flux), None) if power_driven && flux != 0.0 => {
                return Err(
                    "schedule step declares both flux and a power target (power_W_cm3 / power_W_per_g); they are mutually exclusive"
                        .into(),
                );
            }
            (Some(flux), None) => flux,
            (None, Some(_)) => 0.0,
            (None, None) if power_driven => 0.0,
            (None, None) => {
                return Err(
                    "a schedule step needs one of flux and wall_loading_MW_m2, or a power target (power_W_cm3 / power_W_per_g)"
                        .into(),
                );
            }
        };
        Ok(Step {
            dt: wire.dt,
            flux,
            spectrum: wire.spectrum,
            feed: wire.feed,
            removal: wire.removal,
            library: wire.library,
            projectile: wire.projectile,
            power_W_cm3: wire.power_W_cm3,
            power_W_per_g: wire.power_W_per_g,
            wall_loading_MW_m2: wire.wall_loading_MW_m2,
            // an empty map names no element: the step has no partition
            partition: wire.partition.filter(|partition| !partition.is_empty()),
        })
    }
}

impl From<Step> for StepWire {
    fn from(step: Step) -> Self {
        StepWire {
            dt: step.dt,
            flux: step.wall_loading_MW_m2.is_none().then_some(step.flux),
            spectrum: step.spectrum,
            feed: step.feed,
            removal: step.removal,
            library: step.library,
            projectile: step.projectile,
            power_W_cm3: step.power_W_cm3,
            power_W_per_g: step.power_W_per_g,
            wall_loading_MW_m2: step.wall_loading_MW_m2,
            partition: step.partition,
        }
    }
}

impl Step {
    /// True when the step's flux amplitude is solved from a power target rather than given.
    pub fn is_power_driven(&self) -> bool {
        self.power_W_cm3.is_some() || self.power_W_per_g.is_some()
    }

    /// Whether the step irradiates: a positive flux multiplier, a positive wall loading or a power
    /// target (validated positive).
    pub fn irradiates(&self) -> bool {
        self.flux > 0.0
            || self.wall_loading_MW_m2.is_some_and(|wall| wall > 0.0)
            || self.is_power_driven()
    }

    /// Whether the step splits any element off: an empty `partition` map is no partition.
    pub fn has_partition(&self) -> bool {
        self.partition
            .as_ref()
            .is_some_and(|partition| !partition.is_empty())
    }
}

/// An activation library a schedule step switches to, with the projectile its index must declare.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StepLibrary {
    pub library: LibraryRef,
    pub projectile: Projectile,
}

/// What one schedule step runs against: `library` is an index into [`Spec::step_libraries`] (`None` is the
/// base library), `spectrum` is the spectrum replacing the base one for this step (`None` is the base spectrum).
#[derive(Debug, Clone, Copy)]
pub(crate) struct StepContext<'a> {
    pub(crate) library: Option<usize>,
    pub(crate) spectrum: Option<&'a Spectrum>,
}

/// P65 certified screening options.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ScreenOptions {
    /// The raised atom bound used for rate pruning.
    pub bmin_atoms_per_g: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Options {
    #[serde(default = "auto")]
    pub mode: String,
    #[serde(default = "rate")]
    pub prune: String,
    #[serde(default = "bmin")]
    pub bmin_atoms_per_g: f64,
    #[serde(default = "t293")]
    pub temperature_K: f64,
    #[serde(default = "cram16_order")]
    pub cram_order: u8,
    #[serde(default)]
    pub outputs: Option<Vec<String>>,
    /// Fail closed when a material nuclide lacks shielding-table coverage
    /// (P19). Default false: uncovered nuclides are named in the ledger and
    /// their rates pass through unmodified.
    #[serde(default)]
    pub require_shielding_complete: bool,
    /// P65 certified screening: prune at this raised atom bound and emit a
    /// `screen` certificate mapping the dropped-state bounds onto every
    /// banded response edge. Requires `prune: "rate"` — a zero bound from
    /// disabled pruning is not a certificate.
    #[serde(default)]
    pub screen: Option<ScreenOptions>,
    /// Optional per-library-row multiplicative perturbation of collapsed
    /// reaction rates, keyed by activation-library row index (P30
    /// nonlinear sampling). Absent = unperturbed. The applied factors are
    /// named in the run ledger.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rate_scale: Option<BTreeMap<String, f64>>,
    /// Optional per-nuclide multiplicative perturbation of decay
    /// constants, keyed by explicit-nuclide name such as "Mn56"
    /// (P43 nonlinear sampling). Scaling a nuclide's decay constant
    /// scales its decay edges, activity, decay heat, photon source and
    /// dose responses consistently. Absent or stable nuclides are
    /// named errors at run time. The applied factors are named in the
    /// run ledger.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub decay_scale: Option<BTreeMap<String, f64>>,
    /// Optional per-(parent, product) multiplicative perturbation of
    /// independent fission yields, keyed "<parent>:<product>"
    /// explicit-nuclide names such as "U235:I135" (P43 nonlinear
    /// sampling). The declared yield uncertainty scales with the
    /// yield so relative uncertainty is preserved. A pair absent
    /// from the effective yields is a named error at run time.
    /// The applied factors are named in the run ledger.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub yield_scale: Option<BTreeMap<String, f64>>,
    /// P92: gas production (H and He isotopes, appm). Default false; absent from any spec
    /// echo or fingerprint when false, so gas-off output is byte-identical to pre-P92 output.
    #[serde(default, skip_serializing_if = "is_false")]
    pub gas: bool,
    /// P125: cumulative fission count and burn-up per step. Absent from any spec echo or
    /// fingerprint when unset, so default output is byte-identical to pre-P125 output.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fission: Option<FissionCountOptions>,
}
fn is_false(b: &bool) -> bool {
    !*b
}
fn auto() -> String {
    "auto".into()
}
fn rate() -> String {
    "rate".into()
}
fn bmin() -> f64 {
    1e-8
}
fn t293() -> f64 {
    293.6
}
fn cram16_order() -> u8 {
    16
}
impl Default for Options {
    fn default() -> Self {
        Self {
            mode: auto(),
            prune: rate(),
            bmin_atoms_per_g: bmin(),
            temperature_K: t293(),
            cram_order: cram16_order(),
            outputs: None,
            require_shielding_complete: false,
            screen: None,
            rate_scale: None,
            decay_scale: None,
            yield_scale: None,
            gas: false,
            fission: None,
        }
    }
}

/// A hash-pinned file reference needs a path and a 64-hex-digit sha256.
fn check_hashed_ref(reference: &HashedFileRef, what: &str) -> Result<(), String> {
    if reference.path.is_empty()
        || reference.sha256.len() != 64
        || !reference
            .sha256
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit())
    {
        return Err(format!("{what} requires a path and a 64-hex-digit sha256"));
    }
    Ok(())
}

/// Parse a scale-map key as an explicit nuclide (an element key like "Fe"
/// is rejected); returns the (za, liso) identity for solver use.
fn scale_nuclide_key(raw: &str, field: &str) -> Result<(i32, i32), String> {
    match actinv_data::composition::material_key(raw)
        .map_err(|error| format!("{field} key '{raw}': {error}"))?
    {
        actinv_data::composition::MaterialKey::Nuclide { za, liso, .. } => Ok((za, liso)),
        _ => Err(format!(
            "{field} key '{raw}' must be an explicit nuclide, e.g. 'Mn56'"
        )),
    }
}

/// Which tracked states a schedule removal key selects: one nuclide or every state of an element.
#[derive(Debug, Clone, Copy)]
pub(crate) enum RemovalSelector {
    Nuclide(i32, i32),
    Element(i32),
}

#[derive(Debug, Clone)]
pub(crate) struct PhysicalStep {
    duration: Seconds,
    multiplier: FluxMultiplier,
    /// per-step spectrum (ascending group fluxes); None = base spectrum
    pub(crate) spectrum_flux: Option<GroupFluxes>,
    /// index into [`Spec::step_libraries`] of the library this step collapses against; None = base library
    pub(crate) library: Option<usize>,
    /// ((ZA, LISO), atoms s^-1 g^-1) constant feed during this step
    pub(crate) feed: Vec<((i32, i32), f64)>,
    /// (selector, s^-1) first-order removal during this step
    pub(crate) removal: Vec<(RemovalSelector, f64)>,
    /// P126: requested kerma heating power in W per gram of material. When set, `multiplier` is
    /// only a placeholder until the run solves the flux amplitude.
    pub(crate) power_w_per_g: Option<f64>,
    /// (atomic number Z, fraction of the element's atoms kept) instantaneous partition applied
    /// before this step evolves
    pub(crate) partition: Vec<(i32, f64)>,
}

impl PhysicalStep {
    /// This step with a different flux multiplier (the run's estimate or achieved value for a
    /// power-driven step).
    pub(crate) fn with_multiplier(&self, multiplier: FluxMultiplier) -> Self {
        Self {
            multiplier,
            ..self.clone()
        }
    }

    pub(crate) const fn duration(&self) -> Seconds {
        self.duration
    }

    pub(crate) const fn multiplier(&self) -> FluxMultiplier {
        self.multiplier
    }
}

#[derive(Debug, Clone)]
pub(crate) struct PhysicalInputs {
    pub(crate) mass: Grams,
    pub(crate) temperature: Kelvin,
    pub(crate) bmin: AtomsPerGram,
    pub(crate) flux: GroupFluxes,
    pub(crate) schedule: Vec<PhysicalStep>,
    pub(crate) photon_boundaries: EnergyBoundaries,
    pub(crate) gamma_cutoff: ElectronVolts,
    pub(crate) fixed_fission_energy: Option<ElectronVolts>,
    /// One ledger record per `spectrum.rebin` conversion (the base spectrum, then any step's)
    pub(crate) spectrum_conversions: Vec<serde_json::Value>,
    /// One ledger record per `wall_loading_MW_m2` step
    pub(crate) wall_conversions: Vec<serde_json::Value>,
}

/// Parse a duration like "5 min" (also "5min", "300", "1 y"). Seconds when no unit is given.
pub fn parse_duration(s: &str) -> Result<f64, String> {
    let t = s.trim();
    // The split search below is quadratic in the length; no real duration is long.
    if t.len() > 64 {
        return Err(format!(
            "duration '{}...' is longer than 64 characters",
            t.chars().take(24).collect::<String>()
        ));
    }
    let split = (0..=t.len())
        .rev()
        .find(|index| {
            t.is_char_boundary(*index)
                && t[..*index].trim().parse::<f64>().is_ok()
                && t[*index..]
                    .trim()
                    .chars()
                    .all(|character| character.is_ascii_alphabetic())
        })
        .ok_or_else(|| format!("bad duration '{s}'"))?;
    let (num, unit) = t.split_at(split);
    let v: f64 = num
        .trim()
        .parse()
        .map_err(|_| format!("bad duration '{s}'"))?;
    let f = match unit.trim().to_lowercase().as_str() {
        "" | "s" | "sec" | "secs" | "second" | "seconds" => 1.0,
        "m" | "min" | "mins" | "minute" | "minutes" => 60.0,
        "h" | "hr" | "hrs" | "hour" | "hours" => 3600.0,
        "d" | "day" | "days" => 86400.0,
        "y" | "yr" | "yrs" | "year" | "years" => 365.25 * 86400.0,
        u => return Err(format!("unknown time unit '{u}' in '{s}'")),
    };
    Ok(v * f)
}

impl Spec {
    pub fn from_json(text: &str) -> Result<Spec, String> {
        let s: Spec = serde_json::from_str(text).map_err(|e| format!("spec: {e}"))?;
        s.validate()?;
        Ok(s)
    }
    pub fn validate(&self) -> Result<(), String> {
        if self.spec != "actinv-spec-1" {
            return Err(format!("unsupported spec version '{}'", self.spec));
        }
        if self.material.composition.is_empty() {
            return Err("material.composition is empty".into());
        }
        if !self.material.mass_g.is_finite() || self.material.mass_g <= 0.0 {
            return Err("material.mass_g must be positive".into());
        }
        if let Some(density) = self.material.density_g_cm3 {
            if !density.is_finite() || density <= 0.0 {
                return Err("material.density_g_cm3 must be finite and positive".into());
            }
        }
        if let Some(summary) = &self.summary {
            if !(1..=256).contains(&summary.dominant_top) {
                return Err("summary.dominant_top must be between 1 and 256".into());
            }
            if !summary.min_atoms.is_finite() || summary.min_atoms < 0.0 {
                return Err("summary.min_atoms must be a finite value >= 0".into());
            }
        }
        if self.library.path.is_empty() {
            if self.schedule.iter().any(Step::irradiates) {
                return Err(
                    "library.path is empty: an activation library may be omitted only when \
                     every schedule step has zero flux (a decay-only run)"
                        .into(),
                );
            }
            self.validate_decay_only()?;
        }
        if let Some(hash) = &self.library.sha256 {
            if hash.len() != 64 || !hash.bytes().all(|byte| byte.is_ascii_hexdigit()) {
                return Err("library.sha256 must contain 64 hexadecimal digits".into());
            }
        }
        if let Some(uncertainty) = &self.uncertainty {
            let flux_only = uncertainty.channels.len() == 1 && uncertainty.channels[0] == "flux";
            match &uncertainty.covariance {
                Some(covariance) => {
                    if covariance.path.is_empty()
                        || covariance.sha256.len() != 64
                        || !covariance
                            .sha256
                            .bytes()
                            .all(|byte| byte.is_ascii_hexdigit())
                    {
                        return Err(
                            "uncertainty.covariance requires a path and a 64-hex-digit sha256"
                                .into(),
                        );
                    }
                }
                None if flux_only => {}
                None => {
                    return Err(
                        "uncertainty.covariance is required unless channels is exactly [\"flux\"]"
                            .into(),
                    );
                }
            }
            if !uncertainty.confidence_level.is_finite()
                || !(0.0..1.0).contains(&uncertainty.confidence_level)
                || uncertainty.confidence_level == 0.0
            {
                return Err(
                    "uncertainty.confidence_level must be strictly between zero and one".into(),
                );
            }
            let mut selectors = std::collections::HashSet::new();
            let mut seen_channels = std::collections::HashSet::new();
            for channel in &uncertainty.channels {
                match channel.as_str() {
                    "cross_section_mf33" | "decay_constants" | "fission_yields" | "flux" => {}
                    "uncovered_remainder" => {
                        return Err(
                            "uncertainty.channels cannot request uncovered_remainder; it is always named, never propagated"
                                .into(),
                        );
                    }
                    other => {
                        return Err(format!("unknown uncertainty channel '{other}'"));
                    }
                }
                if !seen_channels.insert(channel.as_str()) {
                    return Err(format!("duplicate uncertainty channel '{channel}'"));
                }
            }
            for selector in &uncertainty.responses {
                let valid = matches!(
                    selector.as_str(),
                    "heat.total"
                        | "heat.alpha"
                        | "heat.beta"
                        | "heat.gamma"
                        | "activity:*"
                        | "activity.total"
                ) || selector
                    .strip_prefix("activity:")
                    .is_some_and(|nuclide| !nuclide.is_empty() && nuclide != "*");
                if !valid {
                    return Err(format!(
                        "unknown uncertainty response selector '{selector}'"
                    ));
                }
                if !selectors.insert(selector) {
                    return Err(format!(
                        "duplicate uncertainty response selector '{selector}'"
                    ));
                }
            }
            if let Some(voi) = &uncertainty.voi {
                if !(1..=256).contains(&voi.top) {
                    return Err("uncertainty.voi.top must be between 1 and 256".into());
                }
            }
            if let Some(isomer) = &uncertainty.isomer {
                if let Some(top) = isomer.top {
                    if !(1..=256).contains(&top) {
                        return Err("uncertainty.isomer.top must be between 1 and 256".into());
                    }
                }
            }
            if let Some(design) = &uncertainty.design {
                if let Some(top) = design.top {
                    if !(1..=256).contains(&top) {
                        return Err("uncertainty.design.top must be between 1 and 256".into());
                    }
                }
            }
            if let Some(u) = uncertainty.unmodeled_relative {
                if !u.is_finite() || u < 0.0 {
                    return Err("uncertainty.unmodeled_relative must be a finite value >= 0".into());
                }
            }
            if let Some(table) = &uncertainty.unmodeled_table {
                if uncertainty.unmodeled_relative.is_some() {
                    return Err("uncertainty.unmodeled_table and unmodeled_relative are mutually exclusive — declare one source for the unmodeled term".into());
                }
                if table.path.is_empty()
                    || table.sha256.len() != 64
                    || !table.sha256.bytes().all(|b| b.is_ascii_hexdigit())
                {
                    return Err(
                        "uncertainty.unmodeled_table requires a path and a 64-hex-digit sha256"
                            .into(),
                    );
                }
                if table.key.as_deref().is_some_and(|k| k.is_empty()) {
                    return Err(
                        "uncertainty.unmodeled_table.key must be non-empty when present".into(),
                    );
                }
                if let Some(u) = table.fallback {
                    if !u.is_finite() || u < 0.0 {
                        return Err(
                            "uncertainty.unmodeled_table.fallback must be a finite value >= 0"
                                .into(),
                        );
                    }
                }
            }
            if let Some(spread) = &uncertainty.unmodeled_evalspread {
                if uncertainty.unmodeled_relative.is_some() || uncertainty.unmodeled_table.is_some()
                {
                    return Err("uncertainty.unmodeled_evalspread is mutually exclusive with unmodeled_relative and unmodeled_table — declare one source for the unmodeled term".into());
                }
                if spread.path.is_empty()
                    || spread.sha256.len() != 64
                    || !spread.sha256.bytes().all(|b| b.is_ascii_hexdigit())
                {
                    return Err(
                        "uncertainty.unmodeled_evalspread requires a path and a 64-hex-digit sha256"
                            .into(),
                    );
                }
            }
            if !self.projectile.is_neutron() {
                return Err("MF=33 uncertainty is supported only for neutron activation".into());
            }
        }
        if let Some(radiological) = &self.radiological {
            if radiological.table.path.is_empty()
                || radiological.table.sha256.len() != 64
                || !radiological
                    .table
                    .sha256
                    .bytes()
                    .all(|byte| byte.is_ascii_hexdigit())
            {
                return Err("radiological.table requires a path and a 64-hex-digit sha256".into());
            }
            if let Some(name) = crate::radiological::bundled_name(&radiological.table.path) {
                crate::radiological::bundled_table(name)?;
            }
            let mut selectors = std::collections::HashSet::new();
            for selector in &radiological.responses {
                if selector.trim().is_empty() {
                    return Err("radiological response selectors must be nonempty".into());
                }
                if !selectors.insert(selector) {
                    return Err(format!(
                        "duplicate radiological response selector '{selector}'"
                    ));
                }
            }
            // The bundled table is in memory, so a mistyped selector fails here, not mid-run.
            if let Some(name) = crate::radiological::bundled_name(&radiological.table.path) {
                crate::radiological::validate_bundled_selectors(name, &radiological.responses)?;
            }
        }
        if let Some(damage) = &self.damage {
            if damage.table.path.is_empty()
                || damage.table.sha256.len() != 64
                || !damage
                    .table
                    .sha256
                    .bytes()
                    .all(|byte| byte.is_ascii_hexdigit())
            {
                return Err("damage.table requires a path and a 64-hex-digit sha256".into());
            }
            for (key, value) in &damage.displacement_energy_eV {
                match actinv_data::composition::material_key(key) {
                    Ok(actinv_data::composition::MaterialKey::Element(symbol))
                        if *key == symbol => {}
                    Ok(_) => {
                        return Err(format!(
                            "damage.displacement_energy_eV key '{key}' is not an element symbol"
                        ));
                    }
                    Err(error) => {
                        return Err(format!(
                            "damage.displacement_energy_eV key '{key}': {error}"
                        ));
                    }
                }
                if !value.is_finite() || *value <= 0.0 {
                    return Err(format!(
                        "damage.displacement_energy_eV for '{key}' must be finite and positive"
                    ));
                }
            }
        }
        if let Some(kerma) = &self.kerma {
            if kerma.table.path.is_empty()
                || kerma.table.sha256.len() != 64
                || !kerma
                    .table
                    .sha256
                    .bytes()
                    .all(|byte| byte.is_ascii_hexdigit())
            {
                return Err("kerma.table requires a path and a 64-hex-digit sha256".into());
            }
            if !kerma.power_tolerance.is_finite()
                || kerma.power_tolerance <= 0.0
                || kerma.power_tolerance >= 1.0
            {
                return Err("kerma.power_tolerance must be in the open interval (0, 1)".into());
            }
            if !(1..=4096).contains(&kerma.max_substeps) {
                return Err("kerma.max_substeps must be between 1 and 4096".into());
            }
            if kerma.top == 0 {
                return Err("kerma.top must be at least 1".into());
            }
        }
        if self.schedule.iter().any(Step::is_power_driven) {
            if self.kerma.is_none() {
                return Err("a power-driven schedule step requires a kerma section".into());
            }
            if self.uncertainty.is_some() {
                return Err(
                    "power-driven schedule steps cannot be combined with uncertainty".into(),
                );
            }
            if self.options.screen.is_some() {
                return Err(
                    "power-driven schedule steps cannot be combined with options.screen".into(),
                );
            }
            if self.options.fission.is_some() {
                return Err(
                    "power-driven schedule steps cannot be combined with options.fission: the \
                     fission count integrates one flux multiplier per step, not the solved \
                     sub-interval multipliers"
                        .into(),
                );
            }
            if self.pathways.is_some() {
                return Err(
                    "power-driven schedule steps cannot be combined with pathways: the routes \
                     analysis records one matrix per step, not one per solved sub-interval"
                        .into(),
                );
            }
        }
        if self.pathways.is_some() && self.schedule.iter().any(Step::has_partition) {
            return Err(
                "schedule partition cannot be combined with pathways: the routes analysis \
                 integrates the step matrices and does not see the instantaneous split"
                    .into(),
            );
        }
        if self.decay.primary.is_empty() {
            return Err("decay.primary is empty".into());
        }
        if self
            .decay
            .overrides
            .as_deref()
            .map(str::is_empty)
            .unwrap_or(false)
        {
            return Err("decay.overrides is present but empty".into());
        }
        if self
            .material
            .composition
            .values()
            .any(|value| !value.is_finite() || *value < 0.0)
            || self.material.composition.values().sum::<f64>() <= 0.0
        {
            return Err(
                "material.composition values must be finite and nonnegative with a positive total"
                    .into(),
            );
        }
        actinv_data::composition::validate_material_keys(&self.material.composition)?;
        match self.material.basis.as_str() {
            "wt_percent" | "atom_fraction" | "atoms_per_g" => {}
            b => return Err(format!("unknown material.basis '{b}'")),
        }
        if self.schedule.is_empty() {
            return Err("schedule is empty".into());
        }
        for st in &self.schedule {
            let d = parse_duration(&st.dt)?;
            if !d.is_finite() || d < 0.0 {
                return Err(format!("negative duration '{}'", st.dt));
            }
            if !st.flux.is_finite() || st.flux < 0.0 {
                return Err("flux multiplier must be finite and nonnegative".into());
            }
            if st.power_W_cm3.is_some() && st.power_W_per_g.is_some() {
                return Err(
                    "schedule step declares both power_W_cm3 and power_W_per_g; they are mutually exclusive"
                        .into(),
                );
            }
            if st.is_power_driven() && st.flux != 0.0 {
                return Err(
                    "schedule step declares both flux and a power target (power_W_cm3 / power_W_per_g); they are mutually exclusive"
                        .into(),
                );
            }
            for (name, power) in [
                ("power_W_cm3", st.power_W_cm3),
                ("power_W_per_g", st.power_W_per_g),
            ] {
                if power.is_some_and(|value| !value.is_finite() || value <= 0.0) {
                    return Err(format!(
                        "schedule step {name} must be finite and positive; cool with flux 0"
                    ));
                }
            }
            if st.power_W_cm3.is_some() && self.material.density_g_cm3.is_none() {
                return Err(
                    "schedule step power_W_cm3 requires material.density_g_cm3 (or use power_W_per_g)"
                        .into(),
                );
            }
            if let Some(wall) = st.wall_loading_MW_m2 {
                if st.is_power_driven() {
                    return Err(
                        "schedule step declares both wall_loading_MW_m2 and a power target (power_W_cm3 / power_W_per_g); they are mutually exclusive"
                            .into(),
                    );
                }
                if st.flux != 0.0 {
                    return Err(
                        "a schedule step declares both flux and wall_loading_MW_m2; give exactly one"
                            .into(),
                    );
                }
                if !wall.is_finite() || wall < 0.0 {
                    return Err("wall_loading_MW_m2 must be finite and nonnegative".into());
                }
            }
            if let Some(step_spectrum) = &st.spectrum {
                step_spectrum.check_rebin("schedule step spectrum")?;
            }
            if let Some(partition) = st
                .partition
                .as_ref()
                .filter(|partition| !partition.is_empty())
            {
                let mut elements = std::collections::HashSet::new();
                for (key, kept) in partition {
                    match actinv_data::composition::material_key(key) {
                        Ok(actinv_data::composition::MaterialKey::Element(symbol)) => {
                            if !elements.insert(symbol) {
                                return Err(format!(
                                    "schedule partition names element '{key}' more than once"
                                ));
                            }
                        }
                        Ok(actinv_data::composition::MaterialKey::Nuclide { .. }) => {
                            return Err(format!(
                                "schedule partition key '{key}' must name an element"
                            ));
                        }
                        Err(error) => {
                            return Err(format!("schedule partition key '{key}': {error}"));
                        }
                    }
                    if !kept.is_finite() || !(0.0..=1.0).contains(kept) {
                        return Err(format!(
                            "schedule partition fraction for '{key}' must lie in [0, 1]"
                        ));
                    }
                }
                if self.options.mode == "trace" {
                    return Err(
                        "schedule partition is not supported with options.mode 'trace': the trace \
                         formulation holds the bulk material undepleted; use 'coupled' or 'auto'"
                            .into(),
                    );
                }
            }
            if let Some(feed) = &st.feed {
                for (key, rate) in feed {
                    match actinv_data::composition::material_key(key) {
                        Ok(actinv_data::composition::MaterialKey::Nuclide { .. }) => {}
                        Ok(actinv_data::composition::MaterialKey::Element(_)) => {
                            return Err(format!(
                                "schedule feed key '{key}' must name an explicit nuclide"
                            ));
                        }
                        Err(error) => {
                            return Err(format!("schedule feed key '{key}': {error}"));
                        }
                    }
                    if !rate.is_finite() || *rate < 0.0 {
                        return Err(format!(
                            "schedule feed rate for '{key}' must be finite and nonnegative"
                        ));
                    }
                }
            }
            if let Some(removal) = &st.removal {
                for (key, rate) in removal {
                    if let Err(error) = actinv_data::composition::material_key(key) {
                        return Err(format!("schedule removal key '{key}': {error}"));
                    }
                    if !rate.is_finite() || *rate < 0.0 {
                        return Err(format!(
                            "schedule removal rate for '{key}' must be finite and nonnegative"
                        ));
                    }
                }
            }
        }
        self.resolve_step_libraries()?;
        if let Some(feature) = self.step_library_conflict() {
            return Err(format!(
                "schedule step library overrides cannot be combined with {feature}"
            ));
        }
        match self.options.mode.as_str() {
            "auto" | "trace" | "coupled" => {}
            m => return Err(format!("unknown options.mode '{m}'")),
        }
        match self.options.prune.as_str() {
            "rate" | "reach" | "none" => {}
            p => return Err(format!("unknown options.prune '{p}'")),
        }
        if let Some(screen) = &self.options.screen {
            if !screen.bmin_atoms_per_g.is_finite() || screen.bmin_atoms_per_g < 0.0 {
                return Err(
                    "options.screen.bmin_atoms_per_g must be finite and nonnegative".into(),
                );
            }
            if self.options.prune != "rate" {
                return Err(
                    "options.screen requires prune 'rate' — a zero bound from disabled \
                     pruning is not a certificate"
                        .into(),
                );
            }
        }
        if !self.options.bmin_atoms_per_g.is_finite() || self.options.bmin_atoms_per_g < 0.0 {
            return Err("options.bmin_atoms_per_g must be finite and nonnegative".into());
        }
        if self.options.gas {
            if self.uncertainty.is_some() {
                return Err("options.gas cannot be combined with uncertainty".into());
            }
            if !self.projectile.is_neutron() {
                return Err(format!(
                    "options.gas is supported only for neutron activation, not {}",
                    self.projectile.name()
                ));
            }
        }
        if let Some(fission) = &self.options.fission {
            if !fission.energy_per_fission_MeV.is_finite() || fission.energy_per_fission_MeV <= 0.0
            {
                return Err(
                    "options.fission.energy_per_fission_MeV must be finite and positive".into(),
                );
            }
        }
        if !self.options.temperature_K.is_finite() || self.options.temperature_K < 0.0 {
            return Err("options.temperature_K must be finite and nonnegative".into());
        }
        if !matches!(self.options.cram_order, 16 | 48) {
            return Err("options.cram_order must be 16 or 48".into());
        }
        if let Some(scales) = &self.options.rate_scale {
            for (key, factor) in scales {
                if key.parse::<usize>().is_err() {
                    return Err(format!(
                        "options.rate_scale key '{key}' is not a library row index"
                    ));
                }
                if !factor.is_finite() || *factor <= 0.0 {
                    return Err(format!(
                        "options.rate_scale['{key}'] must be finite and positive"
                    ));
                }
            }
        }
        if let Some(scales) = &self.options.decay_scale {
            for (key, factor) in scales {
                scale_nuclide_key(key, "options.decay_scale")?;
                if !factor.is_finite() || *factor <= 0.0 {
                    return Err(format!(
                        "options.decay_scale['{key}'] must be finite and positive"
                    ));
                }
            }
        }
        if let Some(scales) = &self.options.yield_scale {
            for (key, factor) in scales {
                let (parent, product) = key.split_once(':').ok_or_else(|| {
                    format!(
                        "options.yield_scale key '{key}' must be \
                         '<parent>:<product>' nuclide names"
                    )
                })?;
                scale_nuclide_key(parent, "options.yield_scale")?;
                scale_nuclide_key(product, "options.yield_scale")?;
                if !factor.is_finite() || *factor <= 0.0 {
                    return Err(format!(
                        "options.yield_scale['{key}'] must be finite and positive"
                    ));
                }
            }
        }
        if !self.projectile.is_neutron() && self.options.temperature_K != 0.0 {
            return Err(format!(
                "{} specs require options.temperature_K: 0",
                self.projectile.name()
            ));
        }
        if !self.projectile.is_neutron() && !self.fission_yields.files.is_empty() {
            return Err(format!(
                "fission-yield files are not supported for {} activation",
                self.projectile.name()
            ));
        }
        if let Some(outputs) = &self.options.outputs {
            for output in outputs {
                match output.as_str() {
                    "inventory" | "activity" | "heat" | "photons" | "dose" | "pathways"
                    | "radiological" | "damage" | "ledger" | "certificate" | "audit" => {}
                    value => return Err(format!("unknown options.outputs value '{value}'")),
                }
            }
            if outputs.iter().any(|o| o == "damage") && self.damage.is_none() {
                return Err("options.outputs 'damage' requires a damage section".into());
            }
            // The photon source (and with it every photon extra) is built only for these outputs.
            if !outputs.iter().any(|o| o == "photons" || o == "dose") {
                if self.photon.point_source.is_some() {
                    return Err(
                        "photon.point_source needs options.outputs to include 'photons' or 'dose'"
                            .into(),
                    );
                }
                if self.photon.bremsstrahlung.is_some() {
                    return Err("photon.bremsstrahlung needs options.outputs to include 'photons' or 'dose'".into());
                }
                if self.photon.missing_spectra == MissingSpectra::Estimate {
                    return Err("photon.missing_spectra 'estimate' needs options.outputs to include 'photons' or 'dose'".into());
                }
                if self.photon.exclude_xrays {
                    return Err(
                        "photon.exclude_xrays needs options.outputs to include 'photons' or 'dose'"
                            .into(),
                    );
                }
            }
        }
        if let Some(pathways) = &self.pathways {
            pathways.validate()?;
        }
        if let Some(shielding) = &self.self_shielding {
            // Checked here, not only at prepare time, so validate-only tooling agrees.
            if !self.projectile.is_neutron() {
                return Err(format!(
                    "self_shielding is neutron-only; {} specs cannot declare it",
                    self.projectile.name()
                ));
            }
            match (&shielding.table, &shielding.geometry) {
                (Some(_), Some(_)) => {
                    return Err(
                        "self_shielding takes either a table (Bondarenko) or a geometry (sigmoid curve), not both"
                            .into(),
                    );
                }
                (None, None) => {
                    return Err("self_shielding requires a table or a geometry section".into());
                }
                _ => {}
            }
            match shielding.dilution.as_str() {
                "composition" => {
                    if shielding.sigma0_b.is_some() {
                        return Err(
                            "self_shielding dilution 'composition' takes sigma0_b: null".into()
                        );
                    }
                }
                "fixed" => match shielding.sigma0_b {
                    Some(value) if value.is_finite() && value > 0.0 => {}
                    _ => {
                        return Err(
                            "self_shielding dilution 'fixed' requires a positive finite sigma0_b"
                                .into(),
                        );
                    }
                },
                value => {
                    return Err(format!("unknown self_shielding.dilution '{value}'"));
                }
            }
            if let Some(geometry) = &shielding.geometry {
                if shielding.dilution != "composition" || !shielding.dilution_overrides.is_empty() {
                    return Err(
                        "self_shielding geometry does not use dilution, sigma0_b or dilution_overrides"
                            .into(),
                    );
                }
                geometry.validate(self.material.density_g_cm3)?;
            }
            for (name, groups) in &shielding.dilution_overrides {
                match actinv_data::composition::material_key(name)
                    .map_err(|error| format!("self_shielding.dilution_overrides: {error}"))?
                {
                    actinv_data::composition::MaterialKey::Nuclide { canonical, .. }
                        if canonical == *name => {}
                    actinv_data::composition::MaterialKey::Nuclide { canonical, .. } => {
                        return Err(format!(
                            "self_shielding.dilution_overrides nuclide '{name}' is not canonical; use '{canonical}'"
                        ));
                    }
                    actinv_data::composition::MaterialKey::Element(_) => {
                        return Err(format!(
                            "self_shielding.dilution_overrides key '{name}' is elemental; expected a nuclide"
                        ));
                    }
                }
                if groups.is_empty() {
                    return Err(format!(
                        "self_shielding.dilution_overrides for '{name}' lists no groups"
                    ));
                }
                if groups
                    .values()
                    .any(|value| !value.is_finite() || *value <= 0.0)
                {
                    return Err(format!(
                        "self_shielding.dilution_overrides for '{name}' needs positive finite dilutions"
                    ));
                }
            }
        }
        self.spectrum.check_rebin("spectrum")?;
        if self.spectrum.rebin.is_some() {
            self.named_structure_groups()?;
        } else if self.spectrum.structure == "custom" {
            let b = self
                .spectrum
                .boundaries_eV
                .as_ref()
                .ok_or("custom spectrum needs boundaries_eV")?;
            if b.len() != self.spectrum.flux_per_group.len() + 1 {
                return Err(format!(
                    "boundaries_eV has {} entries; {} groups need {}",
                    b.len(),
                    self.spectrum.flux_per_group.len(),
                    self.spectrum.flux_per_group.len() + 1
                ));
            }
            if b.iter().any(|value| !value.is_finite()) || b.windows(2).any(|w| w[1] <= w[0]) {
                return Err("boundaries_eV must be finite and strictly ascending".into());
            }
        } else {
            let groups = self.named_structure_groups()?;
            if self.spectrum.flux_per_group.len() != groups {
                return Err(format!(
                    "{} requires {groups} flux values, got {}",
                    self.spectrum.structure,
                    self.spectrum.flux_per_group.len()
                ));
            }
        }
        if self
            .spectrum
            .flux_per_group
            .iter()
            .any(|f| !f.is_finite() || *f < 0.0)
        {
            return Err("group fluxes must be finite and nonnegative".into());
        }
        if let Some(errors) = &self.spectrum.relative_error {
            if errors.len() != self.spectrum.flux_per_group.len() {
                return Err(format!(
                    "spectrum.relative_error has {} entries; flux_per_group has {}",
                    errors.len(),
                    self.spectrum.flux_per_group.len()
                ));
            }
            if errors
                .iter()
                .any(|value| !value.is_finite() || *value < 0.0)
            {
                return Err("spectrum.relative_error must be finite and nonnegative".into());
            }
        }
        if self
            .spectrum
            .total
            .is_some_and(|total| !total.is_finite() || total < 0.0)
        {
            return Err("spectrum.total must be finite and nonnegative".into());
        }
        self.spectrum.check_total_has_shape("spectrum")?;
        match self.photon.group_structure.as_str() {
            "fispact-24" | "steiner-22" => {
                if self.photon.group_boundaries_eV.is_some() {
                    return Err(
                        "photon.group_boundaries_eV is only valid with group_structure 'custom'"
                            .into(),
                    );
                }
            }
            "custom" => {
                let b = self
                    .photon
                    .group_boundaries_eV
                    .as_ref()
                    .ok_or("custom photon group structure needs group_boundaries_eV")?;
                if b.len() < 2
                    || b[0] < 0.0
                    || b.iter().any(|v| !v.is_finite())
                    || b.windows(2).any(|w| w[1] <= w[0])
                {
                    return Err("photon.group_boundaries_eV must be finite, nonnegative and strictly increasing".into());
                }
            }
            value => return Err(format!("unknown photon.group_structure '{value}'")),
        }
        if !self.photon.build_up_factor.is_finite() || self.photon.build_up_factor <= 0.0 {
            return Err("photon.build_up_factor must be positive".into());
        }
        if !self.photon.gamma_constant_cutoff_eV.is_finite()
            || self.photon.gamma_constant_cutoff_eV < 0.0
        {
            return Err("photon.gamma_constant_cutoff_eV must be nonnegative".into());
        }
        if let Some(point) = &self.photon.point_source {
            if !point.distance_m.is_finite() || point.distance_m < POINT_SOURCE_MIN_DISTANCE_M {
                return Err(format!(
                    "photon.point_source.distance_m must be finite and at least {POINT_SOURCE_MIN_DISTANCE_M} m"
                ));
            }
            if !point.air_density_g_cm3.is_finite() || point.air_density_g_cm3 <= 0.0 {
                return Err("photon.point_source.air_density_g_cm3 must be positive".into());
            }
        }
        if let Some(brems) = &self.photon.bremsstrahlung {
            if brems.nuclides.is_empty() {
                return Err("photon.bremsstrahlung.nuclides must not be empty".into());
            }
            let mut listed = std::collections::HashSet::new();
            for raw in &brems.nuclides {
                match actinv_data::composition::material_key(raw) {
                    Ok(actinv_data::composition::MaterialKey::Nuclide { canonical, .. })
                        if canonical == *raw => {}
                    Ok(actinv_data::composition::MaterialKey::Nuclide { canonical, .. }) => {
                        return Err(format!(
                            "photon.bremsstrahlung.nuclides entry '{raw}' is not canonical; use '{canonical}'"
                        ));
                    }
                    Ok(_) => {
                        return Err(format!(
                            "photon.bremsstrahlung.nuclides entry '{raw}' is an element, not a nuclide"
                        ));
                    }
                    Err(error) => {
                        return Err(format!(
                            "photon.bremsstrahlung.nuclides entry '{raw}': {error}"
                        ));
                    }
                }
                if !listed.insert(raw.as_str()) {
                    return Err(format!(
                        "photon.bremsstrahlung.nuclides lists '{raw}' more than once"
                    ));
                }
            }
        }
        if let Some(r) = &self.photon.response {
            if r.path.is_empty()
                || r.sha256.len() != 64
                || !r.sha256.bytes().all(|b| b.is_ascii_hexdigit())
            {
                return Err("photon.response requires a path and a 64-hex-digit sha256".into());
            }
        }
        let mut yield_paths = std::collections::HashSet::new();
        for reference in &self.fission_yields.files {
            if reference.path.is_empty()
                || reference.sha256.len() != 64
                || !reference
                    .sha256
                    .bytes()
                    .all(|byte| byte.is_ascii_hexdigit())
            {
                return Err(
                    "every fission_yields file requires a path and a 64-hex-digit sha256".into(),
                );
            }
            if !yield_paths.insert(&reference.path) {
                return Err(format!(
                    "duplicate fission_yields file path '{}'",
                    reference.path
                ));
            }
        }
        match self.fission_yields.energy.as_str() {
            "spectrum_average" => {
                if self.fission_yields.fixed_energy_eV.is_some() {
                    return Err(
                        "fission_yields.fixed_energy_eV is forbidden with spectrum_average".into(),
                    );
                }
            }
            "fixed" => {
                let energy = self
                    .fission_yields
                    .fixed_energy_eV
                    .ok_or("fission_yields.energy 'fixed' requires fixed_energy_eV")?;
                if !energy.is_finite() || energy < 0.0 {
                    return Err(
                        "fission_yields.fixed_energy_eV must be finite and nonnegative".into(),
                    );
                }
            }
            "rate_weighted" => {
                if self.fission_yields.fixed_energy_eV.is_some() {
                    return Err(
                        "fission_yields.fixed_energy_eV is forbidden with rate_weighted".into(),
                    );
                }
                if self.uncertainty.as_ref().is_some_and(|uncertainty| {
                    uncertainty
                        .channels
                        .iter()
                        .any(|channel| channel == "fission_yields")
                }) {
                    return Err(
                        "fission_yields.energy 'rate_weighted' cannot be combined with the \
                         fission_yields uncertainty channel: the mixed yields differ per spectrum"
                            .into(),
                    );
                }
            }
            value => return Err(format!("unknown fission_yields.energy '{value}'")),
        }
        match self.fission_yields.yields.as_str() {
            "independent" | "cumulative" => {}
            value => return Err(format!("unknown fission_yields.yields '{value}'")),
        }
        self.fission_yields.parent_selection()?;
        if self.fission_yields.files.is_empty()
            && (self.fission_yields.energy != "spectrum_average"
                || self.fission_yields.fixed_energy_eV.is_some()
                || self.fission_yields.yields != "independent"
                || self.fission_yields.parents.is_some())
        {
            return Err("empty fission_yields.files requires the default energy settings".into());
        }
        if let Some(spontaneous) = &self.fission_yields.spontaneous {
            if spontaneous.files.is_empty()
                && spontaneous.nubar_endf.is_none()
                && spontaneous.nubar_table.is_none()
            {
                return Err(
                    "fission_yields.spontaneous declares no files, nubar_endf or nubar_table"
                        .into(),
                );
            }
            let mut sf_paths = std::collections::HashSet::new();
            for reference in &spontaneous.files {
                check_hashed_ref(reference, "every fission_yields.spontaneous file")?;
                if !sf_paths.insert(&reference.path) {
                    return Err(format!(
                        "duplicate fission_yields.spontaneous file path '{}'",
                        reference.path
                    ));
                }
            }
            if let Some(reference) = &spontaneous.nubar_endf {
                check_hashed_ref(reference, "fission_yields.spontaneous.nubar_endf")?;
            }
            if let Some(reference) = &spontaneous.nubar_table {
                check_hashed_ref(reference, "fission_yields.spontaneous.nubar_table")?;
            }
        }
        self.validate_overrides()?;
        Ok(())
    }

    /// Group count of the named `spectrum.structure`, after checking it against the projectile.
    fn named_structure_groups(&self) -> Result<usize, String> {
        let (base_name, refine) = match self.spectrum.structure.rsplit_once('x') {
            Some((b, n)) => match n.parse::<usize>() {
                Ok(n) => (b, Some(n)),
                Err(_) => (self.spectrum.structure.as_str(), None),
            },
            None => (self.spectrum.structure.as_str(), None),
        };
        if let Some(n) = refine {
            if !(2..=64).contains(&n) {
                return Err(format!(
                    "spectrum.structure refinement must be in 2..=64, got {n}"
                ));
            }
        }
        let factor = refine.unwrap_or(1);
        let (groups, expected_projectile) = match base_name {
            "fispact-709" => (709 * factor, Projectile::Neutron),
            "fispact-162" if !self.projectile.is_neutron() => (162 * factor, self.projectile),
            "fispact-162" => {
                return Err("fispact-162 is reserved for charged-particle spectra".into());
            }
            // The remaining FISPACT-II standard structures (66 ... 1102) are neutron grids; the group
            // count comes from the vendored boundary file, never from a hardcoded table.
            value => match actinv_data::groups::GroupStructure::named(value)? {
                Some(base) if actinv_data::groups::GroupStructure::is_neutron_named(value) => {
                    (base.groups() * factor, Projectile::Neutron)
                }
                _ => return Err(format!("unknown spectrum.structure '{value}'")),
            },
        };
        if self.projectile != expected_projectile {
            return Err(format!(
                "{} requires projectile '{}', got '{}'",
                self.spectrum.structure,
                expected_projectile.name(),
                self.projectile.name()
            ));
        }
        Ok(groups)
    }

    /// What a run without an activation library cannot carry: every input that reads library
    /// rows. The caller has already established that no step irradiates.
    fn validate_decay_only(&self) -> Result<(), String> {
        const NOTE: &str = "a decay-only run (no library.path) has no activation library to read";
        if self.library.sha256.is_some() {
            return Err("library.sha256 is declared without a library.path".into());
        }
        if self.uncertainty.is_some() {
            return Err(format!("uncertainty is not available: {NOTE}"));
        }
        if self.schedule.iter().any(|step| step.library.is_some()) {
            return Err(format!(
                "schedule step library overrides are not available: {NOTE}"
            ));
        }
        if self.self_shielding.is_some() {
            return Err(format!("self_shielding is not available: {NOTE}"));
        }
        if self.damage.is_some() {
            return Err(format!("damage is not available: {NOTE}"));
        }
        if !self.fission_yields.files.is_empty() {
            return Err(format!("fission_yields files are not available: {NOTE}"));
        }
        if self.options.rate_scale.is_some() || self.options.yield_scale.is_some() {
            return Err(format!(
                "options.rate_scale and options.yield_scale are not available: {NOTE}"
            ));
        }
        if self.kerma.is_some() {
            return Err(format!("kerma is not available: {NOTE}"));
        }
        if let Some(item) = self.overrides.iter().find(|item| item.kind != "half_life") {
            if item.kind == "half_life_uncertainty" {
                return Err(
                    "override kind 'half_life_uncertainty' needs an uncertainty section, which \
                     a decay-only run does not support"
                        .into(),
                );
            }
            return Err(format!(
                "override kind '{}' is not available: {NOTE}",
                item.kind
            ));
        }
        Ok(())
    }

    /// Structural checks of `overrides`. Which library rows or decay records they name is only
    /// known to a run, where unknown names are errors.
    fn validate_overrides(&self) -> Result<(), String> {
        let mut seen = std::collections::HashSet::new();
        let mut value_targets = std::collections::HashSet::new();
        for item in &self.overrides {
            let (za, liso) = scale_nuclide_key(&item.nuclide, "overrides nuclide")?;
            if !NamedOverride::KINDS.contains(&item.kind.as_str()) {
                return Err(format!(
                    "unknown override kind '{}' for '{}'",
                    item.kind, item.nuclide
                ));
            }
            let label = format!("override {} '{}'", item.kind, item.nuclide);
            if !item.value.is_finite() {
                return Err(format!("{label}: value must be finite"));
            }
            let cross_section = item.kind.starts_with("cross_section");
            match (cross_section, item.mt) {
                (true, None) => return Err(format!("{label}: mt is required")),
                (true, Some(mt)) if mt < 1 => {
                    return Err(format!(
                        "{label}: mt must be a positive ENDF reaction number"
                    ));
                }
                (false, Some(_)) => {
                    return Err(format!(
                        "{label}: mt applies to cross-section overrides only"
                    ));
                }
                _ => {}
            }
            if item.units.is_some() && item.kind != "half_life" {
                return Err(format!("{label}: units apply to half_life only"));
            }
            match item.kind.as_str() {
                "cross_section" if item.value < 0.0 => {
                    return Err(format!("{label}: value must be nonnegative barns"));
                }
                "cross_section_scale" if item.value <= 0.0 => {
                    return Err(format!("{label}: value must be a positive multiplier"));
                }
                "half_life" => {
                    item.half_life_seconds()?;
                }
                "cross_section_uncertainty" | "half_life_uncertainty" if item.value < 0.0 => {
                    return Err(format!(
                        "{label}: value must be a nonnegative relative standard uncertainty"
                    ));
                }
                _ => {}
            }
            if !seen.insert((za, liso, item.kind.clone(), item.mt)) {
                return Err(format!("duplicate {label}"));
            }
            if item.is_cross_section_value() && !value_targets.insert((za, liso, item.mt)) {
                return Err(format!(
                    "{label}: cross_section and cross_section_scale name the same reaction"
                ));
            }
        }
        if self
            .overrides
            .iter()
            .any(NamedOverride::is_cross_section_value)
            && self.uncertainty.is_some()
        {
            return Err(
                "cross-section overrides cannot be combined with uncertainty: the MF=33 \
                 collapse is anchored to the library value"
                    .into(),
            );
        }
        let channel_requested = |channel: &str| {
            self.uncertainty.as_ref().is_some_and(|options| {
                (options.channels.is_empty() && channel == "cross_section_mf33")
                    || options.channels.iter().any(|name| name == channel)
            })
        };
        for item in &self.overrides {
            let (channel, covariance) = match item.kind.as_str() {
                "cross_section_uncertainty" => ("cross_section_mf33", true),
                "half_life_uncertainty" => ("decay_constants", false),
                _ => continue,
            };
            if !channel_requested(channel)
                || (covariance
                    && self
                        .uncertainty
                        .as_ref()
                        .is_some_and(|options| options.covariance.is_none()))
            {
                return Err(format!(
                    "override {} '{}' needs an uncertainty section requesting the {channel} channel",
                    item.kind, item.nuclide
                ));
            }
        }
        Ok(())
    }

    pub(crate) fn physical_inputs(&self) -> Result<PhysicalInputs, String> {
        let mass = Grams::new(self.material.mass_g)
            .map_err(|_| "material.mass_g must be positive".to_string())?;
        let temperature = Kelvin::new(self.options.temperature_K)
            .map_err(|_| "options.temperature_K must be finite and nonnegative".to_string())?;
        let bmin = AtomsPerGram::new(self.options.bmin_atoms_per_g)
            .map_err(|_| "options.bmin_atoms_per_g must be finite and nonnegative".to_string())?;
        let (base_flux, base_conversion) = self.spectrum.resolved_flux("spectrum")?;
        let flux = GroupFluxes::new(base_flux)
            .map_err(|_| "group fluxes must be finite and nonnegative".to_string())?;
        let (step_libraries, contexts) = self.resolve_step_libraries()?;
        let mut spectrum_conversions: Vec<serde_json::Value> =
            base_conversion.into_iter().collect();
        let mut wall_conversions: Vec<serde_json::Value> = Vec::new();
        let schedule = self
            .schedule
            .iter()
            .zip(&contexts)
            .enumerate()
            .map(|(index, (step, context))| {
                let parsed = parse_duration(&step.dt)?;
                let duration =
                    Seconds::new(parsed).map_err(|_| format!("negative duration '{}'", step.dt))?;
                let power_w_per_g = match (step.power_W_per_g, step.power_W_cm3) {
                    (Some(power), None) => Some(power),
                    (None, Some(power)) => {
                        let density = self.material.density_g_cm3.ok_or_else(|| {
                            "schedule step power_W_cm3 requires material.density_g_cm3".to_string()
                        })?;
                        Some(power / density)
                    }
                    (None, None) => None,
                    (Some(_), Some(_)) => {
                        return Err(
                            "schedule step declares both power_W_cm3 and power_W_per_g".to_string()
                        );
                    }
                };
                let feed = step
                    .feed
                    .iter()
                    .flatten()
                    .map(
                        |(key, rate)| match actinv_data::composition::material_key(key) {
                            Ok(actinv_data::composition::MaterialKey::Nuclide {
                                za, liso, ..
                            }) => Ok(((za, liso), *rate)),
                            Ok(actinv_data::composition::MaterialKey::Element(_)) => Err(format!(
                                "schedule feed key '{key}' must name an explicit nuclide"
                            )),
                            Err(error) => Err(format!("schedule feed key '{key}': {error}")),
                        },
                    )
                    .collect::<Result<Vec<_>, String>>()?;
                let removal = step
                    .removal
                    .iter()
                    .flatten()
                    .map(
                        |(key, rate)| match actinv_data::composition::material_key(key) {
                            Ok(actinv_data::composition::MaterialKey::Nuclide {
                                za, liso, ..
                            }) => Ok((RemovalSelector::Nuclide(za, liso), *rate)),
                            Ok(actinv_data::composition::MaterialKey::Element(symbol)) => {
                                let z =
                                    actinv_data::composition::z_of(&symbol).ok_or_else(|| {
                                        format!("unknown element symbol in removal key '{key}'")
                                    })?;
                                Ok((RemovalSelector::Element(z), *rate))
                            }
                            Err(error) => Err(format!("schedule removal key '{key}': {error}")),
                        },
                    )
                    .collect::<Result<Vec<_>, String>>()?;
                let spectrum_flux = context
                    .spectrum
                    .map(|s| {
                        s.check_total_has_shape("schedule step spectrum")?;
                        match context.library {
                            // A step on an overriding library carries a spectrum in that library's own
                            // group structure, not the base spectrum's.
                            Some(slot) => s.check_structure_for(
                                step_libraries[slot].projectile,
                                "schedule step spectrum",
                            )?,
                            None => {
                                if self.spectrum.is_placeholder() {
                                    return Err(format!(
                                        "schedule step {} declares a spectrum but the spec has no \
                                         base spectrum: declare spectrum with structure '{}'",
                                        index + 1,
                                        s.structure
                                    ));
                                }
                                if s.structure != self.spectrum.structure {
                                    return Err(format!(
                                        "schedule step spectrum.structure '{}' must match the base spectrum's '{}'",
                                        s.structure, self.spectrum.structure
                                    ));
                                }
                            }
                        }
                        // the group count is compared after any `rebin` conversion
                        let (f, conversion) =
                            s.resolved_flux(&format!("schedule[{index}].spectrum"))?;
                        if step.spectrum.is_some() {
                            spectrum_conversions.extend(conversion);
                        }
                        if context.library.is_none() && f.len() != flux.values().len() {
                            return Err(format!(
                                "schedule step spectrum has {} groups; the base spectrum declares {}",
                                f.len(),
                                flux.values().len()
                            ));
                        }
                        GroupFluxes::new(f)
                            .map_err(|_| "schedule step spectrum group fluxes must be finite and nonnegative".to_string())
                    })
                    .transpose()?;
                let multiplier = match step.wall_loading_MW_m2 {
                    None => step.flux,
                    Some(wall) => {
                        let total = spectrum_flux.as_ref().unwrap_or(&flux).total().get();
                        let reference = context
                            .spectrum
                            .unwrap_or(&self.spectrum)
                            .wall_loading_MW_m2;
                        let projectile = match context.library {
                            Some(slot) => step_libraries[slot].projectile,
                            None => self.projectile,
                        };
                        // the 4.44e13 cm^-2 s^-1 per MW m^-2 stand-in is a 14 MeV neutron current
                        if wall > 0.0 && reference.is_none() && !projectile.is_neutron() {
                            return Err(format!(
                                "schedule step {}: wall_loading_MW_m2 without a reference \
                                 spectrum.wall_loading_MW_m2 assumes a 14 MeV neutron current, \
                                 which does not describe a {} projectile; declare \
                                 spectrum.wall_loading_MW_m2",
                                index + 1,
                                projectile.name()
                            ));
                        }
                        let (multiplier, basis) = wall_flux_multiplier(wall, reference, total)?;
                        wall_conversions.push(serde_json::json!({
                            "step": index + 1,
                            "wall_loading_MW_m2": wall,
                            "basis": basis,
                            "reference_wall_loading_MW_m2": reference,
                            "current_per_MW_m2_cm2_s": reference
                                .is_none()
                                .then_some(WALL_CURRENT_PER_MW_M2),
                            "flux_multiplier": multiplier,
                            "step_total_flux_cm2_s": multiplier * total,
                        }));
                        multiplier
                    }
                };
                let multiplier = FluxMultiplier::new(multiplier)
                    .map_err(|_| "flux multiplier must be finite and nonnegative".to_string())?;
                let partition = step
                    .partition
                    .iter()
                    .flatten()
                    .map(
                        |(key, kept)| match actinv_data::composition::material_key(key) {
                            Ok(actinv_data::composition::MaterialKey::Element(symbol)) => {
                                let z =
                                    actinv_data::composition::z_of(&symbol).ok_or_else(|| {
                                        format!("unknown element symbol in partition key '{key}'")
                                    })?;
                                Ok((z, *kept))
                            }
                            Ok(actinv_data::composition::MaterialKey::Nuclide { .. }) => Err(
                                format!("schedule partition key '{key}' must name an element"),
                            ),
                            Err(error) => Err(format!("schedule partition key '{key}': {error}")),
                        },
                    )
                    .collect::<Result<Vec<_>, String>>()?;
                Ok(PhysicalStep {
                    duration,
                    multiplier,
                    spectrum_flux,
                    library: context.library,
                    feed,
                    removal,
                    power_w_per_g,
                    partition,
                })
            })
            .collect::<Result<Vec<_>, String>>()?;
        let photon_boundaries = EnergyBoundaries::new(self.photon_boundaries()).map_err(|_| {
            "photon.group_boundaries_eV must be finite, nonnegative and strictly increasing"
                .to_string()
        })?;
        let gamma_cutoff = ElectronVolts::new(self.photon.gamma_constant_cutoff_eV)
            .map_err(|_| "photon.gamma_constant_cutoff_eV must be nonnegative".to_string())?;
        let fixed_fission_energy = self
            .fission_yields
            .fixed_energy_eV
            .map(ElectronVolts::new)
            .transpose()
            .map_err(|_| {
                "fission_yields.fixed_energy_eV must be finite and nonnegative".to_string()
            })?;
        Ok(PhysicalInputs {
            mass,
            temperature,
            bmin,
            flux,
            schedule,
            photon_boundaries,
            gamma_cutoff,
            fixed_fission_energy,
            spectrum_conversions,
            wall_conversions,
        })
    }
    /// Group fluxes in ascending-energy order, scaled to `total` when given: the spectrum as
    /// supplied, before any `rebin` conversion onto the library's groups.
    pub fn flux_ascending(&self) -> Vec<f64> {
        self.spectrum.ascending_flux()
    }

    /// The distinct libraries the schedule's `library` overrides switch to, in order of first use. Empty for
    /// every spec without an override. A step that names the base library and projectile again is a return to
    /// the base, not an entry.
    pub fn step_libraries(&self) -> Result<Vec<StepLibrary>, String> {
        Ok(self.resolve_step_libraries()?.0)
    }

    /// The first feature a schedule with library overrides cannot be combined with (None for a schedule without
    /// overrides). The switch is a rate-set change on one shared decay chain; these features are built on a single
    /// library's rows, flux basis or parameter set, so they are refused rather than half-applied.
    pub(crate) fn step_library_conflict(&self) -> Option<&'static str> {
        if self.schedule.iter().all(|step| step.library.is_none()) {
            None
        } else if self.uncertainty.is_some() {
            Some("uncertainty")
        } else if self.options.gas {
            Some("options.gas")
        } else if self.self_shielding.is_some() {
            Some("self_shielding")
        } else if self.damage.is_some() {
            Some("damage")
        } else if !self.fission_yields.files.is_empty() {
            Some("fission_yields")
        } else if self.options.rate_scale.is_some() {
            Some("options.rate_scale")
        } else if self.schedule.iter().any(Step::is_power_driven) {
            // the flux amplitude is solved from the base library's kerma and rates
            Some("power-driven schedule steps")
        } else if self.kerma.is_some() {
            // the kerma table is on the base spectrum's group structure
            Some("kerma")
        } else if self.schedule.iter().any(Step::has_partition) {
            Some("schedule partition")
        } else if self.options.fission.is_some() {
            // the fission count integrates the base library's MT=18 rates
            Some("options.fission")
        } else if self
            .overrides
            .iter()
            .any(|item| item.kind.starts_with("cross_section"))
        {
            // the named rows belong to the base library
            Some("cross-section overrides")
        } else if self.pathways.is_some() {
            Some("pathways")
        } else {
            None
        }
    }

    /// Resolve the schedule's library overrides. A step's `library` is sticky: later steps keep it until another
    /// step names a library. Its spectrum is sticky in the same way, but only as the default: a step that
    /// declares its own spectrum uses it for that step alone (the base-spectrum rule of `Step.spectrum`).
    pub(crate) fn resolve_step_libraries(
        &self,
    ) -> Result<(Vec<StepLibrary>, Vec<StepContext<'_>>), String> {
        let mut libraries: Vec<StepLibrary> = Vec::new();
        let mut contexts = Vec::with_capacity(self.schedule.len());
        let mut active: Option<usize> = None;
        let mut active_spectrum: Option<&Spectrum> = None;
        for (index, step) in self.schedule.iter().enumerate() {
            let number = index + 1;
            match (&step.library, step.projectile) {
                (None, Some(_)) => {
                    return Err(format!(
                        "schedule step {number}: projectile is only valid together with a library override"
                    ));
                }
                (Some(library), projectile) => {
                    if library.path.is_empty() {
                        return Err(format!("schedule step {number}: library.path is empty"));
                    }
                    if let Some(hash) = &library.sha256 {
                        if hash.len() != 64 || !hash.bytes().all(|byte| byte.is_ascii_hexdigit()) {
                            return Err(format!(
                                "schedule step {number}: library.sha256 must contain 64 hexadecimal digits"
                            ));
                        }
                    }
                    let projectile = projectile.unwrap_or(self.projectile);
                    if *library == self.library && projectile == self.projectile {
                        active = None;
                        active_spectrum = None;
                    } else {
                        let candidate = StepLibrary {
                            library: library.clone(),
                            projectile,
                        };
                        let slot = match libraries.iter().position(|known| *known == candidate) {
                            Some(slot) => slot,
                            None => {
                                libraries.push(candidate);
                                libraries.len() - 1
                            }
                        };
                        let spectrum = step.spectrum.as_ref().ok_or_else(|| {
                            format!(
                                "schedule step {number} switches to library '{}' and must declare a spectrum in its group structure",
                                library.path
                            )
                        })?;
                        active = Some(slot);
                        active_spectrum = Some(spectrum);
                    }
                }
                (None, None) => {}
            }
            if active.is_some() && step.spectrum.as_ref().is_some_and(|s| s.rebin.is_some()) {
                return Err(format!(
                    "schedule step {number}: spectrum.rebin cannot accompany a step library override; \
                     give the spectrum in the new library's own groups"
                ));
            }
            contexts.push(StepContext {
                library: active,
                spectrum: step.spectrum.as_ref().or(active_spectrum),
            });
        }
        Ok((libraries, contexts))
    }
    pub fn schedule_seconds(&self) -> Vec<(f64, f64)> {
        self.schedule
            .iter()
            .map(|s| (parse_duration(&s.dt).unwrap_or(0.0), s.flux))
            .collect()
    }

    pub fn photon_boundaries(&self) -> Vec<f64> {
        match (
            &self.photon.group_boundaries_eV,
            self.photon.group_structure.as_str(),
        ) {
            (Some(v), _) => v.clone(),
            (None, "steiner-22") => crate::photon::STEINER_22_BOUNDARIES_EV.to_vec(),
            (None, _) => crate::photon::FISPACT_24_BOUNDARIES_EV.to_vec(),
        }
    }
}

#[cfg(test)]
mod duration_tests {
    use super::{parse_duration, Projectile, Spec};

    fn minimal_spec() -> serde_json::Value {
        serde_json::json!({
            "spec": "actinv-spec-1",
            "library": {"path": "activation.npz"},
            "decay": {"primary": "decay.endf"},
            "material": {
                "basis": "wt_percent",
                "composition": {"FE": 100.0},
            },
            "spectrum": {
                "structure": "custom",
                "boundaries_eV": [1.0, 2.0],
                "flux_per_group": [1.0],
            },
            "schedule": [{"dt": "1 s", "flux": 1.0}],
        })
    }

    #[test]
    fn scientific_notation_is_not_a_unit_suffix() {
        assert_eq!(parse_duration("1e-8 s").unwrap(), 1e-8);
        assert_eq!(
            parse_duration("2.5E+3ms").unwrap_err(),
            "unknown time unit 'ms' in '2.5E+3ms'"
        );
        assert_eq!(parse_duration("3e2").unwrap(), 300.0);
    }

    #[test]
    fn equivalent_duration_spellings_share_the_physical_boundary() {
        let spellings = ["300 s", "300s", "5 min", "5min", "0.08333333333333333 h"];
        for spelling in spellings {
            let seconds = parse_duration(spelling).unwrap();
            assert!((seconds - 300.0).abs() <= f64::EPSILON * 300.0);
        }

        let mut value = minimal_spec();
        for invalid in ["-1 s", "nan s", "inf s", "1 fortnight"] {
            value["schedule"][0]["dt"] = serde_json::Value::String(invalid.into());
            assert!(Spec::from_json(&value.to_string()).is_err(), "{invalid}");
        }
    }

    #[test]
    fn contradictory_or_unbounded_inputs_fail_validation() {
        // A positive total with no shape used to be dropped silently (pure-decay run).
        let mut value = minimal_spec();
        value["spectrum"]["flux_per_group"] = serde_json::json!([0.0]);
        value["spectrum"]["total"] = serde_json::json!(1.0e10);
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("no shape to scale"));
        // Duration parsing is quadratic in length; a long string is refused up front.
        let mut value = minimal_spec();
        value["schedule"][0]["dt"] = serde_json::json!("x".repeat(100_000));
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("longer than 64"));
        // Self-shielding is neutron-only at validation, not only at prepare time.
        let mut value = minimal_spec();
        value["projectile"] = serde_json::json!("proton");
        value["options"] = serde_json::json!({"temperature_K": 0.0});
        value["self_shielding"] = serde_json::json!({
            "table": {"path": "shield.json", "sha256": "0".repeat(64)},
            "dilution": "composition"
        });
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("neutron-only"));
    }

    #[test]
    fn misspelt_composition_element_fails_validation() {
        let mut value = minimal_spec();
        value["material"]["composition"] = serde_json::json!({"Fe": 99.0, "Coo": 1.0});
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("unknown element symbol"));
        value["material"]["composition"] = serde_json::json!({"Fe": 99.0, "Tc": 1.0});
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("no natural isotopic abundance"));
    }

    #[test]
    fn omitted_projectile_is_backward_compatible_neutron() {
        let spec = Spec::from_json(&minimal_spec().to_string()).unwrap();
        assert_eq!(spec.projectile, Projectile::Neutron);
        assert_eq!(spec.options.temperature_K, 293.6);
    }

    #[test]
    fn gas_false_is_absent_from_the_serialized_options_others_default_present() {
        // G3: options.gas must vanish from the spec echo/fingerprint when false, exactly like
        // pre-P92 output, and appear only when explicitly turned on.
        let spec = Spec::from_json(&minimal_spec().to_string()).unwrap();
        assert!(!spec.options.gas);
        let echoed = serde_json::to_value(&spec.options).unwrap();
        assert!(echoed.get("gas").is_none(), "{echoed}");

        let mut value = minimal_spec();
        value["options"] = serde_json::json!({"gas": true});
        let spec = Spec::from_json(&value.to_string()).unwrap();
        assert!(spec.options.gas);
        let echoed = serde_json::to_value(&spec.options).unwrap();
        assert_eq!(echoed["gas"], serde_json::json!(true));
    }

    #[test]
    fn gas_is_refused_with_uncertainty_and_with_non_neutron_projectiles() {
        let mut value = minimal_spec();
        value["options"] = serde_json::json!({"gas": true});
        value["uncertainty"] = serde_json::json!({
            "covariance": {"path": "cov.npz", "sha256": "0".repeat(64)},
        });
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("options.gas cannot be combined with uncertainty"));

        let mut value = minimal_spec();
        value["projectile"] = serde_json::json!("proton");
        value["options"] = serde_json::json!({"gas": true, "temperature_K": 0.0});
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("options.gas is supported only for neutron activation"));

        // gas alone (neutron, no uncertainty) is a valid spec.
        let mut value = minimal_spec();
        value["options"] = serde_json::json!({"gas": true});
        assert!(Spec::from_json(&value.to_string()).is_ok());
    }

    #[test]
    fn spontaneous_fission_and_fission_count_options_are_opt_in_and_validated() {
        // Absent options vanish from the echoed spec, so default output is unchanged.
        let spec = Spec::from_json(&minimal_spec().to_string()).unwrap();
        let echoed = serde_json::to_value(&spec).unwrap();
        assert!(echoed["fission_yields"].get("spontaneous").is_none());
        assert!(echoed["options"].get("fission").is_none());

        let mut value = minimal_spec();
        value["options"] = serde_json::json!({"fission": {}});
        let spec = Spec::from_json(&value.to_string()).unwrap();
        assert_eq!(
            spec.options
                .fission
                .as_ref()
                .unwrap()
                .energy_per_fission_MeV,
            200.0
        );
        value["options"] = serde_json::json!({"fission": {"energy_per_fission_MeV": 0.0}});
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("energy_per_fission_MeV"));

        let mut value = minimal_spec();
        value["fission_yields"] = serde_json::json!({"spontaneous": {}});
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("declares no files"));
        value["fission_yields"] = serde_json::json!({"spontaneous": {
            "files": [{"path": "sfy.endf", "sha256": "0".repeat(64)}],
            "nubar_table": {"path": "nubar.json", "sha256": "short"},
        }});
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("nubar_table requires a path"));
        value["fission_yields"] = serde_json::json!({"spontaneous": {
            "files": [
                {"path": "sfy.endf", "sha256": "0".repeat(64)},
                {"path": "sfy.endf", "sha256": "1".repeat(64)},
            ],
        }});
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("duplicate fission_yields.spontaneous file path"));
        value["fission_yields"] = serde_json::json!({"spontaneous": {
            "files": [{"path": "sfy.endf", "sha256": "0".repeat(64)}],
            "nubar_endf": {"path": "decay.endf", "sha256": "1".repeat(64)},
            "nubar_table": {"path": "nubar.json", "sha256": "2".repeat(64)},
        }});
        let spec = Spec::from_json(&value.to_string()).unwrap();
        let echoed = serde_json::to_value(&spec).unwrap();
        assert_eq!(
            echoed["fission_yields"]["spontaneous"]["nubar_table"]["path"],
            "nubar.json"
        );
    }

    #[test]
    fn fission_yield_selection_options_are_opt_in_and_validated() {
        // absent: nothing new in the echoed spec
        let spec = Spec::from_json(&minimal_spec().to_string()).unwrap();
        let echoed = serde_json::to_value(&spec).unwrap();
        assert!(echoed["fission_yields"].get("yields").is_none());
        assert!(echoed["fission_yields"].get("parents").is_none());

        let files = serde_json::json!([{"path": "u235.endf", "sha256": "0".repeat(64)}]);
        let with = |extra: serde_json::Value| {
            let mut value = minimal_spec();
            let mut block = serde_json::json!({"files": files});
            for (key, item) in extra.as_object().unwrap() {
                block[key] = item.clone();
            }
            value["fission_yields"] = block;
            Spec::from_json(&value.to_string())
        };
        let spec = with(serde_json::json!({
            "energy": "rate_weighted",
            "yields": "cumulative",
            "parents": {"exclude": ["U238"]},
        }))
        .unwrap();
        assert_eq!(
            spec.fission_yields.yield_kind(),
            actinv_data::fission::YieldKind::Cumulative
        );
        assert!(spec.fission_yields.selects((92_235, 0)));
        assert!(!spec.fission_yields.selects((92_238, 0)));
        let echoed = serde_json::to_value(&spec).unwrap();
        assert_eq!(echoed["fission_yields"]["yields"], "cumulative");
        let spec = with(serde_json::json!({"parents": {"include": ["U235", "Am242m1"]}})).unwrap();
        assert!(spec.fission_yields.selects((92_235, 0)));
        assert!(spec.fission_yields.selects((95_242, 1)));
        assert!(!spec.fission_yields.selects((92_238, 0)));

        for (extra, expected) in [
            (
                serde_json::json!({"parents": {"include": ["U235"], "exclude": ["U238"]}}),
                "include or exclude, not both",
            ),
            (
                serde_json::json!({"parents": {}}),
                "needs include or exclude",
            ),
            (
                serde_json::json!({"parents": {"include": []}}),
                "must not be empty",
            ),
            (
                serde_json::json!({"parents": {"include": ["Uranium"]}}),
                "fission_yields.parents.include",
            ),
            (
                serde_json::json!({"yields": "total"}),
                "unknown fission_yields.yields",
            ),
            (
                serde_json::json!({"energy": "rate_weighted", "fixed_energy_eV": 1.0}),
                "forbidden with rate_weighted",
            ),
        ] {
            let message = with(extra).unwrap_err();
            assert!(message.contains(expected), "{message}");
        }
        // selection without a yield file is the default-settings refusal
        let mut value = minimal_spec();
        value["fission_yields"] = serde_json::json!({"yields": "cumulative"});
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("empty fission_yields.files"));
    }

    #[test]
    fn charged_projectiles_require_zero_kelvin_and_no_fission_yields() {
        let mut value = minimal_spec();
        value["projectile"] = serde_json::json!("proton");
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("temperature_K: 0"));

        value["options"] = serde_json::json!({"temperature_K": 0.0});
        assert_eq!(
            Spec::from_json(&value.to_string()).unwrap().projectile,
            Projectile::Proton
        );

        value["fission_yields"] = serde_json::json!({
            "files": [{"path": "yield.endf", "sha256": "0".repeat(64)}],
        });
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("not supported for proton"));
    }

    #[test]
    fn gamma_spec_requires_zero_kelvin_and_no_fission_yields() {
        let mut value = minimal_spec();
        value["projectile"] = serde_json::json!("gamma");
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("temperature_K: 0"));

        value["options"] = serde_json::json!({"temperature_K": 0.0});
        assert_eq!(
            Spec::from_json(&value.to_string()).unwrap().projectile,
            Projectile::Gamma
        );

        value["fission_yields"] = serde_json::json!({
            "files": [{"path": "yield.endf", "sha256": "0".repeat(64)}],
        });
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("not supported for gamma"));
    }

    #[test]
    fn feed_and_removal_are_optional_and_validated() {
        let spec = Spec::from_json(&minimal_spec().to_string()).unwrap();
        assert!(spec.schedule[0].feed.is_none() && spec.schedule[0].removal.is_none());

        let mut value = minimal_spec();
        value["schedule"][0]["feed"] = serde_json::json!({"Co60": 1e10, "Ta180m": 2.0});
        value["schedule"][0]["removal"] = serde_json::json!({"Co60": 1e-9, "Ni": 1e-8});
        let spec = Spec::from_json(&value.to_string()).unwrap();
        let feed = spec.schedule[0].feed.as_ref().unwrap();
        assert_eq!(feed.len(), 2);
        assert!(spec.schedule[0]
            .removal
            .as_ref()
            .unwrap()
            .contains_key("Ni"));
    }

    #[test]
    fn feed_requires_explicit_nuclides() {
        let mut value = minimal_spec();
        value["schedule"][0]["feed"] = serde_json::json!({"Fe": 1.0});
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("must name an explicit nuclide"));
        value["schedule"][0]["feed"] = serde_json::json!({"Xx99": 1.0});
        assert!(Spec::from_json(&value.to_string()).is_err());
        value["schedule"][0]["feed"] = serde_json::json!({"Co60": -1.0});
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("finite and nonnegative"));
        value["schedule"][0]["feed"] = serde_json::json!({"Co60": null});
        assert!(Spec::from_json(&value.to_string()).is_err());
    }

    #[test]
    fn per_step_spectrum_parses_and_validates() {
        // accepted when structure and group count match the base spectrum
        let mut value = minimal_spec();
        value["schedule"][0]["spectrum"] = serde_json::json!({
            "structure": "custom",
            "boundaries_eV": [1.0, 2.0],
            "flux_per_group": [2.0],
        });
        let spec = Spec::from_json(&value.to_string()).unwrap();
        let physical = spec.physical_inputs().unwrap();
        assert_eq!(
            physical.schedule[0]
                .spectrum_flux
                .as_ref()
                .unwrap()
                .values(),
            &[2.0]
        );
    }

    #[test]
    fn per_step_spectrum_requires_matching_structure_and_groups() {
        // structure mismatch is rejected
        let mut value = minimal_spec();
        value["schedule"][0]["spectrum"] = serde_json::json!({
            "structure": "fispact-709",
            "flux_per_group": [1.0],
        });
        assert!(Spec::from_json(&value.to_string())
            .and_then(|s| s.physical_inputs())
            .unwrap_err()
            .contains("must match the base spectrum"));

        // group-count mismatch is rejected
        let mut value = minimal_spec();
        value["schedule"][0]["spectrum"] = serde_json::json!({
            "structure": "custom",
            "boundaries_eV": [1.0, 2.0, 3.0],
            "flux_per_group": [1.0, 1.0],
        });
        assert!(Spec::from_json(&value.to_string())
            .and_then(|s| s.physical_inputs())
            .unwrap_err()
            .contains("base spectrum declares 1"));
    }

    #[test]
    fn per_step_spectrum_accepts_uncertainty() {
        // The joint MF=33 collapse covers (spectrum, row) parameters, so a
        // per-step spectrum combined with uncertainty is a valid spec.
        let mut value = minimal_spec();
        value["schedule"][0]["spectrum"] = serde_json::json!({
            "structure": "custom",
            "boundaries_eV": [1.0, 2.0],
            "flux_per_group": [1.0],
        });
        value["uncertainty"] = serde_json::json!({
            "covariance": {"path": "cov.npz", "sha256": "0".repeat(64)},
        });
        assert!(Spec::from_json(&value.to_string()).is_ok());
    }

    #[test]
    fn removal_rates_are_finite_and_nonnegative() {
        let mut value = minimal_spec();
        value["schedule"][0]["removal"] = serde_json::json!({"Co60": -1e-9});
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("finite and nonnegative"));
        value["schedule"][0]["removal"] = serde_json::json!({"Co60": "1e-9"});
        assert!(Spec::from_json(&value.to_string()).is_err());
    }

    #[test]
    fn self_shielding_dilution_modes_are_validated() {
        let table = || serde_json::json!({"path": "t.json", "sha256": "0".repeat(64)});
        let mut value = minimal_spec();
        value["self_shielding"] = serde_json::json!({"table": table(), "dilution": "fixed"});
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("positive finite sigma0_b"));

        value["self_shielding"]["sigma0_b"] = serde_json::json!(-0.5);
        assert!(Spec::from_json(&value.to_string()).is_err());

        value["self_shielding"]["sigma0_b"] = serde_json::json!(0.1);
        assert!(Spec::from_json(&value.to_string()).is_ok());

        value["self_shielding"]["dilution"] = serde_json::json!("composition");
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("sigma0_b: null"));

        value["self_shielding"]["sigma0_b"] = serde_json::Value::Null;
        let spec = Spec::from_json(&value.to_string()).unwrap();
        assert_eq!(spec.self_shielding.unwrap().dilution, "composition");

        value["self_shielding"]["dilution"] = serde_json::json!("bogus");
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("unknown self_shielding.dilution"));
    }

    #[test]
    fn self_shielding_defaults_serialize_without_the_new_sections() {
        let table = serde_json::json!({"path": "t.json", "sha256": "0".repeat(64)});
        let mut value = minimal_spec();
        value["self_shielding"] = serde_json::json!({"table": table.clone()});
        let spec = Spec::from_json(&value.to_string()).unwrap();
        // The serialized form (and so the spec fingerprint) carries no
        // geometry or dilution_overrides key.
        assert_eq!(
            serde_json::to_value(&spec.self_shielding).unwrap(),
            serde_json::json!({"table": table, "dilution": "composition", "sigma0_b": null})
        );
    }

    #[test]
    fn self_shielding_geometry_is_validated() {
        let geometry = || {
            serde_json::json!({
                "shape": "foil",
                "length_cm": 0.01,
                "density_g_cm3": 19.3,
                "resonance_files": [{"path": "w184.endf", "sha256": "0".repeat(64)}],
            })
        };
        let spec_with = |section: serde_json::Value| {
            let mut value = minimal_spec();
            value["self_shielding"] = section;
            Spec::from_json(&value.to_string())
        };
        let with_geometry = |edit: &dyn Fn(&mut serde_json::Value)| {
            let mut geometry = geometry();
            edit(&mut geometry);
            spec_with(serde_json::json!({ "geometry": geometry }))
        };
        let spec = with_geometry(&|_| {}).unwrap();
        let section = serde_json::to_value(&spec.self_shielding).unwrap();
        assert!(section.get("table").is_none());
        assert_eq!(section["geometry"]["reactions"], "all");

        for (edit, want) in [
            (
                serde_json::json!({"length_cm": 0.0}),
                "length_cm must be a positive finite",
            ),
            (
                serde_json::json!({"length_cm": -1.0}),
                "length_cm must be a positive finite",
            ),
            (
                serde_json::json!({"shape": "cylinder"}),
                "requires height_cm",
            ),
            (
                serde_json::json!({"shape": "cylinder", "height_cm": 0.0}),
                "height_cm must be a positive finite",
            ),
            (
                serde_json::json!({"height_cm": 1.0}),
                "applies only to a cylinder",
            ),
            (
                serde_json::json!({"shape": "slab"}),
                "unknown self_shielding.geometry.shape",
            ),
            (
                serde_json::json!({"density_g_cm3": 0.0}),
                "density_g_cm3 must be a positive finite",
            ),
            (
                serde_json::json!({"resonance_files": []}),
                "needs resonance data",
            ),
            (
                serde_json::json!({"reactions": "elastic"}),
                "unknown self_shielding.geometry.reactions",
            ),
            (serde_json::json!({"choose": []}), "at least one"),
            (serde_json::json!({"choose": ["Qq9"]}), "choose"),
        ] {
            let error = with_geometry(&|geometry| {
                for (key, value) in edit.as_object().unwrap() {
                    geometry[key.as_str()] = value.clone();
                }
            })
            .unwrap_err();
            assert!(error.contains(want), "{edit}: {error}");
        }
        // A valid cylinder, restricted to capture and a chosen element.
        let spec = with_geometry(&|geometry| {
            geometry["shape"] = serde_json::json!("cylinder");
            geometry["height_cm"] = serde_json::json!(2.0);
            geometry["reactions"] = serde_json::json!("capture");
            geometry["choose"] = serde_json::json!(["W", "Mn55"]);
        })
        .unwrap();
        assert_eq!(
            spec.self_shielding.unwrap().geometry.unwrap().reactions,
            "capture"
        );

        let table = serde_json::json!({"path": "t.json", "sha256": "0".repeat(64)});
        let error =
            spec_with(serde_json::json!({"table": table, "geometry": geometry()})).unwrap_err();
        assert!(error.contains("not both"), "{error}");
        let error = spec_with(serde_json::json!({"dilution": "composition"})).unwrap_err();
        assert!(error.contains("requires a table or a geometry"), "{error}");
        let error = spec_with(serde_json::json!({
            "geometry": geometry(), "dilution": "fixed", "sigma0_b": 1.0
        }))
        .unwrap_err();
        assert!(error.contains("does not use dilution"), "{error}");
        let error = spec_with(serde_json::json!({
            "geometry": geometry(), "dilution_overrides": {"W184": {"3": 10.0}}
        }))
        .unwrap_err();
        assert!(error.contains("does not use dilution"), "{error}");
        // Unknown geometry keys are errors.
        let error =
            with_geometry(&|geometry| geometry["thickness"] = serde_json::json!(1.0)).unwrap_err();
        assert!(error.contains("thickness"), "{error}");
    }

    #[test]
    fn self_shielding_dilution_overrides_are_validated() {
        let table = || serde_json::json!({"path": "t.json", "sha256": "0".repeat(64)});
        let spec_with = |overrides: serde_json::Value| {
            let mut value = minimal_spec();
            value["self_shielding"] =
                serde_json::json!({"table": table(), "dilution_overrides": overrides});
            Spec::from_json(&value.to_string())
        };
        let spec = spec_with(serde_json::json!({"W182": {"300": 100.0, "301": 80.0}})).unwrap();
        let overrides = spec.self_shielding.unwrap().dilution_overrides;
        assert_eq!(overrides["W182"][&300], 100.0);
        assert_eq!(overrides["W182"][&301], 80.0);
        for (overrides, want) in [
            (serde_json::json!({"W": {"3": 1.0}}), "elemental"),
            (serde_json::json!({"W184m": {"3": 1.0}}), "use 'W184m1'"),
            (serde_json::json!({"W184": {}}), "lists no groups"),
            (serde_json::json!({"W184": {"3": 0.0}}), "positive finite"),
            (serde_json::json!({"W184": {"3": -2.0}}), "positive finite"),
        ] {
            let error = spec_with(overrides.clone()).unwrap_err();
            assert!(error.contains(want), "{overrides}: {error}");
        }
    }

    #[test]
    fn pathways_section_is_opt_in_validated_and_absent_from_the_echo() {
        let spec = Spec::from_json(&minimal_spec().to_string()).unwrap();
        assert!(spec.pathways.is_none());
        assert!(serde_json::to_value(&spec)
            .unwrap()
            .get("pathways")
            .is_none());

        let mut value = minimal_spec();
        value["pathways"] = serde_json::json!({"targets": ["Mn56"], "max_depth": 6});
        let spec = Spec::from_json(&value.to_string()).unwrap();
        let options = spec.pathways.unwrap();
        assert_eq!(options.max_depth, 6);
        assert_eq!(options.path_floor, 0.005);
        assert_eq!(options.loop_floor, 0.01);

        value["pathways"] = serde_json::json!({"max_depth": 0});
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("pathways.max_depth"));
        value["pathways"] = serde_json::json!({"targets": ["Fe"]});
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("explicit nuclide"));
        value["pathways"] = serde_json::json!({"depth": 3});
        assert!(Spec::from_json(&value.to_string()).is_err());
    }

    #[test]
    fn self_shielding_accepts_uncertainty_combination() {
        let mut value = minimal_spec();
        value["self_shielding"] = serde_json::json!({
            "table": {"path": "t.json", "sha256": "0".repeat(64)},
            "dilution": "fixed",
            "sigma0_b": 0.1,
        });
        value["uncertainty"] = serde_json::json!({
            "covariance": {"path": "c.npy", "sha256": "0".repeat(64)}
        });
        assert!(Spec::from_json(&value.to_string()).is_ok());
    }

    #[test]
    fn decay_scale_requires_explicit_nuclide_positive_factors() {
        let mut value = minimal_spec();
        value["options"] = serde_json::json!({"decay_scale": {"Mn56": 1.5, "Co60m1": 0.8}});
        let spec = Spec::from_json(&value.to_string()).unwrap();
        assert_eq!(spec.options.decay_scale.as_ref().unwrap()["Mn56"], 1.5);

        for (key, want) in [
            ("Fe", "must be an explicit nuclide"),
            ("Mn56:Co60", "malformed explicit nuclide key"),
            ("", "malformed material composition key"),
        ] {
            let mut v = minimal_spec();
            v["options"] = serde_json::json!({"decay_scale": {key: 1.0}});
            let e = Spec::from_json(&v.to_string()).unwrap_err();
            assert!(e.contains(want), "{key}: {e}");
        }
        for bad in [
            serde_json::json!(0.0),
            serde_json::json!(-1.0),
            serde_json::json!("x"),
        ] {
            let mut v = minimal_spec();
            v["options"] = serde_json::json!({"decay_scale": {"Mn56": bad}});
            assert!(Spec::from_json(&v.to_string()).is_err());
        }
    }

    #[test]
    fn yield_scale_requires_parent_product_nuclide_pairs() {
        let mut value = minimal_spec();
        value["options"] =
            serde_json::json!({"yield_scale": {"U235:I135": 2.0, "U235:Xe135m1": 1.1}});
        let spec = Spec::from_json(&value.to_string()).unwrap();
        assert_eq!(spec.options.yield_scale.as_ref().unwrap()["U235:I135"], 2.0);

        for key in ["U235", "U235:", ":I135", "U235:I135:Xe135", "U235-I135"] {
            let mut v = minimal_spec();
            v["options"] = serde_json::json!({"yield_scale": {key: 2.0}});
            assert!(
                Spec::from_json(&v.to_string()).is_err(),
                "key '{key}' must be refused"
            );
        }
        for key in ["U235:Fe", "Fe:I135"] {
            let mut v = minimal_spec();
            v["options"] = serde_json::json!({"yield_scale": {key: 2.0}});
            let e = Spec::from_json(&v.to_string()).unwrap_err();
            assert!(e.contains("explicit nuclide"), "{key}: {e}");
        }
        let mut v = minimal_spec();
        v["options"] = serde_json::json!({"yield_scale": {"U235:I135": 0.0}});
        assert!(Spec::from_json(&v.to_string()).is_err());
    }

    #[test]
    fn all_scale_maps_coexist_on_one_spec() {
        let mut value = minimal_spec();
        value["options"] = serde_json::json!({
            "rate_scale": {"0": 1.1},
            "decay_scale": {"Mn56": 1.5},
            "yield_scale": {"U235:I135": 2.0},
        });
        let spec = Spec::from_json(&value.to_string()).unwrap();
        assert!(spec.options.rate_scale.is_some());
        assert!(spec.options.decay_scale.is_some());
        assert!(spec.options.yield_scale.is_some());
    }

    // P93 G1: the flux channel's own validation (uncertainty.covariance's
    // flux-only exemption, spectrum.relative_error's shape/sign checks, and
    // that descending reorder keeps a group's error paired with its flux).

    #[test]
    fn covariance_is_required_unless_channels_is_exactly_flux() {
        // channels == ["flux"] alone: covariance may be omitted.
        let mut value = minimal_spec();
        value["spectrum"]["relative_error"] = serde_json::json!([0.1]);
        value["uncertainty"] = serde_json::json!({"channels": ["flux"]});
        assert!(Spec::from_json(&value.to_string()).is_ok());

        // Any other channel set still requires covariance, flux included or not.
        for channels in [
            serde_json::json!(["decay_constants"]),
            serde_json::json!(["flux", "decay_constants"]),
            serde_json::json!([]),
        ] {
            let mut value = minimal_spec();
            value["spectrum"]["relative_error"] = serde_json::json!([0.1]);
            value["uncertainty"] = serde_json::json!({"channels": channels});
            let err = Spec::from_json(&value.to_string()).unwrap_err();
            assert!(
                err.contains("uncertainty.covariance is required unless channels is exactly"),
                "{err}"
            );
        }

        // With covariance present, channels == ["flux"] still validates (MF=33
        // still runs; flux-only mode is about what's *required*, not what's allowed).
        let mut value = minimal_spec();
        value["spectrum"]["relative_error"] = serde_json::json!([0.1]);
        value["uncertainty"] = serde_json::json!({
            "channels": ["flux"],
            "covariance": {"path": "cov.npz", "sha256": "0".repeat(64)},
        });
        assert!(Spec::from_json(&value.to_string()).is_ok());
    }

    #[test]
    fn spectrum_relative_error_length_must_match_flux_per_group() {
        let mut value = minimal_spec();
        value["spectrum"]["relative_error"] = serde_json::json!([0.1, 0.2]);
        let err = Spec::from_json(&value.to_string()).unwrap_err();
        assert!(
            err.contains("spectrum.relative_error has 2 entries; flux_per_group has 1"),
            "{err}"
        );
    }

    #[test]
    fn spectrum_relative_error_must_be_finite_and_nonnegative() {
        // A negative value is expressible in JSON directly.
        let mut value = minimal_spec();
        value["spectrum"]["relative_error"] = serde_json::json!([-0.1]);
        let err = Spec::from_json(&value.to_string()).unwrap_err();
        assert!(err.contains("must be finite and nonnegative"), "{err}");

        // NaN/infinity have no JSON literal; exercise the check directly on a
        // parsed, otherwise-valid Spec (mirrors how other *_must_be_finite
        // checks in this file can only be reached this way).
        let base = Spec::from_json(&minimal_spec().to_string()).unwrap();
        for bad in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            let mut spec = base.clone();
            spec.spectrum.relative_error = Some(vec![bad]);
            let err = spec.validate().unwrap_err();
            assert!(
                err.contains("must be finite and nonnegative"),
                "{bad}: {err}"
            );
        }
    }

    #[test]
    fn relative_error_is_optional_and_unused_without_the_flux_channel() {
        // No relative_error, no flux channel requested: unaffected (pre-P93 behavior).
        let value = minimal_spec();
        assert!(Spec::from_json(&value.to_string()).is_ok());

        // relative_error present but the flux channel not requested: accepted,
        // simply carried and unused (documented in the field's doc comment).
        let mut value = minimal_spec();
        value["spectrum"]["relative_error"] = serde_json::json!([0.1]);
        let spec = Spec::from_json(&value.to_string()).unwrap();
        assert_eq!(spec.spectrum.relative_error, Some(vec![0.1]));
    }

    #[test]
    fn descending_reorder_keeps_a_groups_error_paired_with_its_flux() {
        let mut value = minimal_spec();
        value["spectrum"] = serde_json::json!({
            "structure": "custom",
            "boundaries_eV": [1.0, 2.0, 3.0],
            "flux_per_group": [2.0, 5.0],
            "relative_error": [0.02, 0.05],
            "descending": true,
        });
        let spec = Spec::from_json(&value.to_string()).unwrap();
        let ascending_flux = spec.spectrum.ascending_flux();
        let ascending_error = spec.spectrum.ascending_relative_error().unwrap();
        assert_eq!(ascending_flux, vec![5.0, 2.0]);
        assert_eq!(ascending_error, vec![0.05, 0.02]);
        // Every group's error stays attached to that same group's flux under reorder.
        let declared: Vec<(f64, f64)> = value["spectrum"]["flux_per_group"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_f64().unwrap())
            .zip(
                value["spectrum"]["relative_error"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|v| v.as_f64().unwrap()),
            )
            .collect();
        let mut reordered: Vec<(f64, f64)> =
            ascending_flux.into_iter().zip(ascending_error).collect();
        reordered.reverse();
        assert_eq!(declared, reordered);
    }

    #[test]
    fn fispact_neutron_structures_validate_flux_length_and_projectile() {
        for (name, groups) in [
            ("fispact-66", 66usize),
            ("fispact-175", 175),
            ("fispact-709", 709),
            ("fispact-1102", 1102),
        ] {
            let mut value = minimal_spec();
            value["spectrum"] =
                serde_json::json!({"structure": name, "flux_per_group": vec![1.0; groups]});
            assert!(Spec::from_json(&value.to_string()).is_ok(), "{name}");
            value["spectrum"]["flux_per_group"] = serde_json::json!(vec![1.0; groups - 1]);
            let error = Spec::from_json(&value.to_string()).unwrap_err();
            assert!(
                error.contains(&format!("requires {groups} flux values")),
                "{name}: {error}"
            );
        }
        // An `xN` refinement multiplies the vendored group count.
        let mut value = minimal_spec();
        value["spectrum"] =
            serde_json::json!({"structure": "fispact-1102x2", "flux_per_group": vec![1.0; 2204]});
        assert!(Spec::from_json(&value.to_string()).is_ok());
        // The neutron grids reject other projectiles; the CCFE-162 rule is unchanged.
        let mut value = minimal_spec();
        value["projectile"] = serde_json::json!("proton");
        value["options"]["temperature_K"] = serde_json::json!(0.0);
        value["spectrum"] =
            serde_json::json!({"structure": "fispact-1102", "flux_per_group": vec![1.0; 1102]});
        let error = Spec::from_json(&value.to_string()).unwrap_err();
        assert!(error.contains("requires projectile 'neutron'"), "{error}");
        let mut value = minimal_spec();
        value["spectrum"] =
            serde_json::json!({"structure": "fispact-162", "flux_per_group": vec![1.0; 162]});
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("reserved for charged-particle"));
        value["spectrum"] =
            serde_json::json!({"structure": "fispact-24", "flux_per_group": vec![1.0; 24]});
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("unknown spectrum.structure"));
    }

    #[test]
    fn steiner_22_selects_the_manual_table_and_rejects_custom_boundaries() {
        let mut value = minimal_spec();
        value["photon"] = serde_json::json!({"group_structure": "steiner-22"});
        let spec = Spec::from_json(&value.to_string()).unwrap();
        assert_eq!(
            spec.photon_boundaries(),
            crate::photon::STEINER_22_BOUNDARIES_EV.to_vec()
        );
        assert_eq!(spec.photon_boundaries().len(), 23);
        // The default structure is unchanged.
        let spec = Spec::from_json(&minimal_spec().to_string()).unwrap();
        assert_eq!(
            spec.photon_boundaries(),
            crate::photon::FISPACT_24_BOUNDARIES_EV.to_vec()
        );
        value["photon"]["group_boundaries_eV"] = serde_json::json!([0.0, 1.0]);
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("only valid with group_structure 'custom'"));
    }

    #[test]
    fn photon_parity_options_are_opt_in_and_absent_from_the_default_echo() {
        let spec = Spec::from_json(&minimal_spec().to_string()).unwrap();
        assert!(spec.photon.point_source.is_none());
        assert!(spec.photon.bremsstrahlung.is_none());
        let echoed = serde_json::to_value(&spec.photon).unwrap();
        assert!(echoed.get("point_source").is_none(), "{echoed}");
        assert!(echoed.get("bremsstrahlung").is_none(), "{echoed}");
    }

    #[test]
    fn point_source_distance_and_air_density_are_validated() {
        let mut value = minimal_spec();
        value["photon"] = serde_json::json!({"point_source": {"distance_m": 1.0}});
        let spec = Spec::from_json(&value.to_string()).unwrap();
        let point = spec.photon.point_source.unwrap();
        assert_eq!(point.distance_m, 1.0);
        assert!(point.air_attenuation);
        assert_eq!(point.air_density_g_cm3, 1.20479e-3);
        // FISPACT-II raises a shorter distance to 0.3 m; ACTINV refuses it.
        for bad in [0.29, 0.0, -1.0] {
            value["photon"] = serde_json::json!({"point_source": {"distance_m": bad}});
            assert!(Spec::from_json(&value.to_string())
                .unwrap_err()
                .contains("at least 0.3 m"));
        }
        value["photon"] = serde_json::json!({"point_source": {"distance_m": 0.3}});
        assert!(Spec::from_json(&value.to_string()).is_ok());
        value["photon"] =
            serde_json::json!({"point_source": {"distance_m": 1.0, "air_density_g_cm3": 0.0}});
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("air_density_g_cm3"));
        value["photon"] = serde_json::json!({"point_source": {"distance_m": 1.0, "typo": 1}});
        assert!(Spec::from_json(&value.to_string()).is_err());
    }

    #[test]
    fn bremsstrahlung_nuclides_must_be_canonical_unique_nuclides() {
        let mut value = minimal_spec();
        value["photon"] = serde_json::json!({"bremsstrahlung": {"nuclides": ["Cl36", "K42"]}});
        assert!(Spec::from_json(&value.to_string()).is_ok());
        for (bad, message) in [
            (serde_json::json!([]), "must not be empty"),
            (serde_json::json!(["cl36"]), "not canonical"),
            (serde_json::json!(["Cl"]), "element"),
            (serde_json::json!(["Cl36", "Cl36"]), "more than once"),
        ] {
            value["photon"] = serde_json::json!({"bremsstrahlung": {"nuclides": bad}});
            let error = Spec::from_json(&value.to_string()).unwrap_err();
            assert!(error.contains(message), "{error}");
        }
    }

    #[test]
    fn triton_and_helion_specs_use_the_charged_particle_grid() {
        for name in ["triton", "helion"] {
            let mut value = minimal_spec();
            value["projectile"] = serde_json::json!(name);
            value["options"] = serde_json::json!({"temperature_K": 0.0});
            value["spectrum"] = serde_json::json!({
                "structure": "fispact-162",
                "flux_per_group": vec![1.0; 162],
            });
            let spec = Spec::from_json(&value.to_string()).unwrap();
            assert_eq!(spec.projectile.name(), name);

            // The restrictions that hold for every charged projectile hold here.
            let mut hot = value.clone();
            hot["options"] = serde_json::json!({});
            assert!(Spec::from_json(&hot.to_string())
                .unwrap_err()
                .contains("temperature_K: 0"));
            let mut gas = value.clone();
            gas["options"] = serde_json::json!({"temperature_K": 0.0, "gas": true});
            assert!(Spec::from_json(&gas.to_string())
                .unwrap_err()
                .contains("options.gas is supported only for neutron activation"));
            let mut yields = value.clone();
            yields["fission_yields"] = serde_json::json!({
                "files": [{"path": "yield.endf", "sha256": "0".repeat(64)}],
            });
            assert!(Spec::from_json(&yields.to_string())
                .unwrap_err()
                .contains(&format!("not supported for {name}")));
            let mut neutron_grid = value.clone();
            neutron_grid["spectrum"] = serde_json::json!({
                "structure": "fispact-709",
                "flux_per_group": vec![1.0; 709],
            });
            assert!(Spec::from_json(&neutron_grid.to_string())
                .unwrap_err()
                .contains("requires projectile 'neutron'"));
        }
    }

    #[test]
    fn projectile_names_round_trip_through_the_spec() {
        for name in [
            "neutron", "proton", "deuteron", "triton", "helion", "alpha", "gamma",
        ] {
            let mut value = minimal_spec();
            value["projectile"] = serde_json::json!(name);
            value["options"] = serde_json::json!({"temperature_K": 0.0});
            // A custom structure suits every projectile, so only the name is under test.
            let spec = Spec::from_json(&value.to_string()).unwrap();
            let echoed = serde_json::to_value(&spec).unwrap();
            assert_eq!(echoed["projectile"], serde_json::json!(name));
            assert_eq!(spec.projectile.name(), name);
        }
    }

    fn library_switch_spec() -> serde_json::Value {
        let mut value = minimal_spec();
        value["schedule"] = serde_json::json!([
            {"dt": "1 s", "flux": 1.0},
            {
                "dt": "2 s",
                "flux": 1.0,
                "library": {"path": "second.npz"},
                "spectrum": {
                    "structure": "custom",
                    "boundaries_eV": [1.0, 3.0, 5.0],
                    "flux_per_group": [1.0, 2.0],
                },
            },
            {"dt": "3 s", "flux": 0.0},
        ]);
        value
    }

    #[test]
    fn schedule_without_overrides_serializes_without_library_fields() {
        let spec = Spec::from_json(&minimal_spec().to_string()).unwrap();
        assert!(spec.step_libraries().unwrap().is_empty());
        let echoed = serde_json::to_value(&spec.schedule[0]).unwrap();
        assert!(echoed.get("library").is_none(), "{echoed}");
        assert!(echoed.get("projectile").is_none(), "{echoed}");
        let physical = spec.physical_inputs().unwrap();
        assert!(physical.schedule[0].library.is_none());
    }

    #[test]
    fn schedule_library_override_is_sticky_and_carries_its_spectrum() {
        let spec = Spec::from_json(&library_switch_spec().to_string()).unwrap();
        let libraries = spec.step_libraries().unwrap();
        assert_eq!(libraries.len(), 1);
        assert_eq!(libraries[0].library.path, "second.npz");
        assert_eq!(libraries[0].projectile, Projectile::Neutron);
        let physical = spec.physical_inputs().unwrap();
        let slots: Vec<Option<usize>> = physical.schedule.iter().map(|step| step.library).collect();
        assert_eq!(slots, vec![None, Some(0), Some(0)]);
        assert!(physical.schedule[0].spectrum_flux.is_none());
        // the cooling step keeps the library and the spectrum it was switched in with
        assert_eq!(
            physical.schedule[2]
                .spectrum_flux
                .as_ref()
                .unwrap()
                .values(),
            &[1.0, 2.0]
        );
    }

    #[test]
    fn schedule_library_override_can_return_to_the_base_library() {
        let mut value = library_switch_spec();
        let steps = value["schedule"].as_array_mut().unwrap();
        steps.push(serde_json::json!({
            "dt": "4 s", "flux": 1.0, "library": {"path": "activation.npz"},
        }));
        steps.push(serde_json::json!({
            "dt": "5 s",
            "flux": 1.0,
            "library": {"path": "second.npz"},
            "spectrum": {
                "structure": "custom",
                "boundaries_eV": [1.0, 3.0, 5.0],
                "flux_per_group": [3.0, 4.0],
            },
        }));
        let spec = Spec::from_json(&value.to_string()).unwrap();
        // the repeated library is one entry, not two
        assert_eq!(spec.step_libraries().unwrap().len(), 1);
        let physical = spec.physical_inputs().unwrap();
        let slots: Vec<Option<usize>> = physical.schedule.iter().map(|step| step.library).collect();
        assert_eq!(slots, vec![None, Some(0), Some(0), None, Some(0)]);
        // the returning step has no spectrum of its own, so it runs on the base spectrum
        assert!(physical.schedule[3].spectrum_flux.is_none());
        assert_eq!(
            physical.schedule[4]
                .spectrum_flux
                .as_ref()
                .unwrap()
                .values(),
            &[3.0, 4.0]
        );
    }

    #[test]
    fn schedule_library_override_declares_its_projectile() {
        let mut value = library_switch_spec();
        value["schedule"][1]["projectile"] = serde_json::json!("triton");
        value["schedule"][1]["spectrum"] = serde_json::json!({
            "structure": "fispact-162",
            "flux_per_group": vec![1.0; 162],
        });
        let spec = Spec::from_json(&value.to_string()).unwrap();
        let libraries = spec.step_libraries().unwrap();
        assert_eq!(libraries[0].projectile, Projectile::Triton);
        spec.physical_inputs().unwrap();

        // The neutron grid does not suit a triton library.
        value["schedule"][1]["spectrum"] = serde_json::json!({
            "structure": "fispact-709",
            "flux_per_group": vec![1.0; 709],
        });
        let error = Spec::from_json(&value.to_string())
            .and_then(|spec| spec.physical_inputs().map(|_| ()))
            .unwrap_err();
        assert!(
            error.contains("does not suit the triton projectile"),
            "{error}"
        );

        // A projectile with no library to describe is a mistake, not a no-op.
        let mut value = minimal_spec();
        value["schedule"][0]["projectile"] = serde_json::json!("proton");
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("only valid together with a library override"));
    }

    #[test]
    fn schedule_library_override_needs_a_spectrum_in_the_new_structure() {
        let mut value = library_switch_spec();
        value["schedule"][1]
            .as_object_mut()
            .unwrap()
            .remove("spectrum");
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("must declare a spectrum"));

        let mut value = library_switch_spec();
        value["schedule"][1]["spectrum"]["boundaries_eV"] = serde_json::json!([1.0, 3.0]);
        let error = Spec::from_json(&value.to_string())
            .and_then(|spec| spec.physical_inputs().map(|_| ()))
            .unwrap_err();
        assert!(error.contains("groups need 3"), "{error}");
    }

    #[test]
    fn schedule_library_override_refuses_single_library_features() {
        let mut value = library_switch_spec();
        value["uncertainty"] = serde_json::json!({
            "covariance": {"path": "cov.npz", "sha256": "0".repeat(64)},
        });
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("cannot be combined with uncertainty"));

        let mut value = library_switch_spec();
        value["options"] = serde_json::json!({"gas": true});
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("cannot be combined with options.gas"));

        let mut value = library_switch_spec();
        value["options"] = serde_json::json!({"rate_scale": {"0": 2.0}});
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("cannot be combined with options.rate_scale"));

        // The same spec without the conflict is valid.
        assert!(Spec::from_json(&library_switch_spec().to_string()).is_ok());
    }

    fn power_spec(step: serde_json::Value) -> serde_json::Value {
        let mut value = minimal_spec();
        value["schedule"] = serde_json::json!([step]);
        value["kerma"] = serde_json::json!({
            "table": {"path": "kerma.json", "sha256": "0".repeat(64)},
        });
        value["material"]["density_g_cm3"] = serde_json::json!(7.874);
        value
    }

    #[test]
    fn power_target_and_flux_are_mutually_exclusive() {
        let both = power_spec(serde_json::json!({"dt": "1 s", "flux": 2.0, "power_W_per_g": 1.0}));
        assert!(Spec::from_json(&both.to_string())
            .unwrap_err()
            .contains("mutually exclusive"));
        let both_powers =
            power_spec(serde_json::json!({"dt": "1 s", "power_W_per_g": 1.0, "power_W_cm3": 7.0}));
        assert!(Spec::from_json(&both_powers.to_string())
            .unwrap_err()
            .contains("mutually exclusive"));
        // neither flux nor power is still an error
        let neither = power_spec(serde_json::json!({"dt": "1 s"}));
        assert!(Spec::from_json(&neither.to_string())
            .unwrap_err()
            .contains("needs one of flux"));
        // a power step on its own parses and carries the zero multiplier
        let power_only = power_spec(serde_json::json!({"dt": "1 s", "power_W_per_g": 1.0}));
        let spec = Spec::from_json(&power_only.to_string()).unwrap();
        assert!(spec.schedule[0].is_power_driven());
        assert_eq!(spec.schedule[0].flux, 0.0);
        // a hand-built contradictory step fails validation too
        let mut spec = spec;
        spec.schedule[0].flux = 3.0;
        assert!(spec.validate().unwrap_err().contains("mutually exclusive"));
    }

    #[test]
    fn power_steps_serialize_and_reparse_and_flux_steps_are_unchanged() {
        let power_only = power_spec(serde_json::json!({"dt": "1 s", "power_W_cm3": 7.0}));
        let spec = Spec::from_json(&power_only.to_string()).unwrap();
        let echoed = serde_json::to_string(&spec).unwrap();
        assert!(Spec::from_json(&echoed).is_ok());
        // a flux-driven step echoes exactly its four historical keys
        let plain = Spec::from_json(&minimal_spec().to_string()).unwrap();
        let echoed = serde_json::to_value(&plain).unwrap();
        let step = echoed["schedule"][0].as_object().unwrap();
        let mut keys: Vec<&str> = step.keys().map(String::as_str).collect();
        keys.sort_unstable();
        assert_eq!(keys, vec!["dt", "feed", "flux", "removal", "spectrum"]);
        assert!(echoed.get("kerma").is_none());
        assert!(echoed["material"].get("density_g_cm3").is_none());
    }

    #[test]
    fn power_steps_need_kerma_density_and_a_compatible_run() {
        let step = serde_json::json!({"dt": "1 s", "power_W_cm3": 7.0});
        let mut value = power_spec(step.clone());
        assert!(Spec::from_json(&value.to_string()).is_ok());
        value["material"]
            .as_object_mut()
            .unwrap()
            .remove("density_g_cm3");
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("density_g_cm3"));
        // power per gram needs no density
        let per_gram = {
            let mut v = power_spec(serde_json::json!({"dt": "1 s", "power_W_per_g": 1.0}));
            v["material"]
                .as_object_mut()
                .unwrap()
                .remove("density_g_cm3");
            v
        };
        assert!(Spec::from_json(&per_gram.to_string()).is_ok());
        let mut no_kerma = power_spec(step);
        no_kerma.as_object_mut().unwrap().remove("kerma");
        assert!(Spec::from_json(&no_kerma.to_string())
            .unwrap_err()
            .contains("kerma section"));
        for bad in [0.0, -1.0] {
            let invalid = power_spec(serde_json::json!({"dt": "1 s", "power_W_per_g": bad}));
            assert!(Spec::from_json(&invalid.to_string()).is_err(), "{bad}");
        }
        let mut with_uncertainty =
            power_spec(serde_json::json!({"dt": "1 s", "power_W_per_g": 1.0}));
        with_uncertainty["uncertainty"] = serde_json::json!({
            "covariance": {"path": "cov.npz", "sha256": "0".repeat(64)},
        });
        assert!(Spec::from_json(&with_uncertainty.to_string())
            .unwrap_err()
            .contains("uncertainty"));
    }

    #[test]
    fn kerma_options_are_validated() {
        let mut value = power_spec(serde_json::json!({"dt": "1 s", "power_W_per_g": 1.0}));
        let spec = Spec::from_json(&value.to_string()).unwrap();
        let kerma = spec.kerma.as_ref().unwrap();
        assert_eq!(kerma.power_tolerance, 1.0e-3);
        assert_eq!(kerma.max_substeps, 64);
        assert_eq!(kerma.top, 10);
        for (key, bad) in [
            ("power_tolerance", serde_json::json!(0.0)),
            ("power_tolerance", serde_json::json!(1.0)),
            ("max_substeps", serde_json::json!(0)),
            ("max_substeps", serde_json::json!(5000)),
            ("top", serde_json::json!(0)),
        ] {
            value["kerma"][key] = bad.clone();
            assert!(Spec::from_json(&value.to_string()).is_err(), "{key}={bad}");
            value["kerma"].as_object_mut().unwrap().remove(key);
        }
        value["kerma"]["table"]["sha256"] = serde_json::json!("abc");
        assert!(Spec::from_json(&value.to_string()).is_err());
        value["material"]["density_g_cm3"] = serde_json::json!(-1.0);
        assert!(Spec::from_json(&value.to_string())
            .unwrap_err()
            .contains("density"));
    }

    #[test]
    fn step_driver_wire_accepts_exactly_one_of_flux_wall_and_power() {
        let error_of =
            |step: serde_json::Value| Spec::from_json(&power_spec(step).to_string()).unwrap_err();
        // flux 0 beside a power target is the form a power step serializes
        let zero_flux =
            power_spec(serde_json::json!({"dt": "1 s", "flux": 0.0, "power_W_per_g": 1.0}));
        let spec = Spec::from_json(&zero_flux.to_string()).unwrap();
        assert!(spec.schedule[0].is_power_driven());
        assert!(spec.schedule[0].irradiates());
        assert!(
            error_of(serde_json::json!({"dt": "1 s", "flux": 1.0, "power_W_cm3": 7.0}))
                .contains("mutually exclusive")
        );
        assert!(error_of(
            serde_json::json!({"dt": "1 s", "wall_loading_MW_m2": 1.0, "power_W_per_g": 1.0})
        )
        .contains("wall_loading_MW_m2 and a power target"));
        assert!(
            error_of(serde_json::json!({"dt": "1 s", "flux": 0.0, "wall_loading_MW_m2": 1.0}))
                .contains("both flux and wall_loading_MW_m2")
        );
        assert!(error_of(serde_json::json!({"dt": "1 s"})).contains("needs one of flux"));
        // a hand-built wall plus power step fails validation too
        let mut spec = Spec::from_json(&zero_flux.to_string()).unwrap();
        spec.schedule[0].wall_loading_MW_m2 = Some(1.0);
        assert!(spec.validate().unwrap_err().contains("mutually exclusive"));
        // a power step with a partition and a flux step with a library echo and reparse
        let mut value = power_spec(
            serde_json::json!({"dt": "1 s", "power_W_per_g": 1.0, "partition": {"Fe": 0.5}}),
        );
        value["options"] = serde_json::json!({"mode": "coupled"});
        let spec = Spec::from_json(&value.to_string()).unwrap();
        let echoed = serde_json::to_string(&spec).unwrap();
        assert!(Spec::from_json(&echoed).is_ok());
        let spec = Spec::from_json(&library_switch_spec().to_string()).unwrap();
        let echoed = serde_json::to_string(&spec).unwrap();
        assert!(Spec::from_json(&echoed).is_ok());
    }

    #[test]
    fn step_libraries_refuse_the_features_built_on_one_library() {
        let refused = |edit: &dyn Fn(&mut serde_json::Value), feature: &str| {
            let mut value = library_switch_spec();
            edit(&mut value);
            let error = Spec::from_json(&value.to_string()).unwrap_err();
            assert!(
                error.contains(&format!(
                    "schedule step library overrides cannot be combined with {feature}"
                )),
                "{feature}: {error}"
            );
        };
        let kerma = serde_json::json!({
            "table": {"path": "kerma.json", "sha256": "0".repeat(64)},
        });
        refused(
            &|value: &mut serde_json::Value| {
                value["kerma"] = kerma.clone();
                value["schedule"][2] = serde_json::json!({"dt": "3 s", "power_W_per_g": 1.0});
            },
            "power-driven schedule steps",
        );
        refused(
            &|value: &mut serde_json::Value| value["kerma"] = kerma.clone(),
            "kerma",
        );
        refused(
            &|value: &mut serde_json::Value| {
                value["schedule"][2] =
                    serde_json::json!({"dt": "3 s", "flux": 0.0, "partition": {"Fe": 0.5}});
            },
            "schedule partition",
        );
        refused(
            &|value: &mut serde_json::Value| value["options"] = serde_json::json!({"fission": {}}),
            "options.fission",
        );
        refused(
            &|value: &mut serde_json::Value| {
                value["overrides"] = serde_json::json!([
                    {"nuclide": "Fe56", "kind": "cross_section", "mt": 102, "value": 1.0}
                ]);
            },
            "cross-section overrides",
        );
        refused(
            &|value: &mut serde_json::Value| {
                value["pathways"] = serde_json::json!({"targets": ["Fe57"]})
            },
            "pathways",
        );
        // Spontaneous-fission routing reads decay data only, so it composes with a library switch.
        let mut value = library_switch_spec();
        value["fission_yields"] = serde_json::json!({
            "spontaneous": {"files": [{"path": "sfy.endf", "sha256": "0".repeat(64)}]},
        });
        assert!(Spec::from_json(&value.to_string()).is_ok());
        // A named half-life override is decay data too.
        let mut value = library_switch_spec();
        value["overrides"] =
            serde_json::json!([{"nuclide": "Fe56", "kind": "half_life", "value": 10.0}]);
        assert!(Spec::from_json(&value.to_string()).is_ok());
    }

    #[test]
    fn step_library_spectra_cannot_be_rebinned_and_decay_only_runs_have_no_libraries() {
        let mut value = library_switch_spec();
        value["schedule"][1]["spectrum"]["structure"] = serde_json::json!("fispact-709");
        value["schedule"][1]["spectrum"]["rebin"] = serde_json::json!({});
        let error = Spec::from_json(&value.to_string()).unwrap_err();
        assert!(
            error.contains("spectrum.rebin cannot accompany a step library override"),
            "{error}"
        );
        let decay_only = serde_json::json!({
            "spec": "actinv-spec-1",
            "decay": {"primary": "decay.endf"},
            "material": {"basis": "wt_percent", "composition": {"FE": 100.0}},
            "schedule": [{
                "dt": "1 s",
                "flux": 0.0,
                "library": {"path": "second.npz"},
                "spectrum": {
                    "structure": "custom",
                    "boundaries_eV": [1.0, 3.0, 5.0],
                    "flux_per_group": [1.0, 2.0],
                },
            }],
        });
        let error = Spec::from_json(&decay_only.to_string()).unwrap_err();
        assert!(
            error.contains("schedule step library overrides are not available"),
            "{error}"
        );
    }

    #[test]
    fn power_steps_and_partitions_refuse_the_per_step_matrix_analyses() {
        let step = serde_json::json!({"dt": "1 s", "power_W_per_g": 1.0});
        let mut value = power_spec(step.clone());
        value["options"] = serde_json::json!({"fission": {}});
        let error = Spec::from_json(&value.to_string()).unwrap_err();
        assert!(
            error.contains("power-driven schedule steps cannot be combined with options.fission"),
            "{error}"
        );
        let mut value = power_spec(step);
        value["pathways"] = serde_json::json!({"targets": ["Fe57"]});
        let error = Spec::from_json(&value.to_string()).unwrap_err();
        assert!(
            error.contains("power-driven schedule steps cannot be combined with pathways"),
            "{error}"
        );
        let mut value = minimal_spec();
        value["schedule"] =
            serde_json::json!([{"dt": "1 s", "flux": 1.0, "partition": {"Fe": 0.5}}]);
        value["pathways"] = serde_json::json!({"targets": ["Fe57"]});
        let error = Spec::from_json(&value.to_string()).unwrap_err();
        assert!(
            error.contains("schedule partition cannot be combined with pathways"),
            "{error}"
        );
        // a power step and a partition, and a fission count with a partition, are allowed together
        let mut value = power_spec(serde_json::json!({
            "dt": "1 s", "power_W_per_g": 1.0, "partition": {"Fe": 0.5}
        }));
        value["options"] = serde_json::json!({"mode": "coupled"});
        assert!(Spec::from_json(&value.to_string()).is_ok());
        let mut value = minimal_spec();
        value["schedule"] =
            serde_json::json!([{"dt": "1 s", "flux": 1.0, "partition": {"Fe": 0.5}}]);
        value["options"] = serde_json::json!({"mode": "coupled", "fission": {}});
        assert!(Spec::from_json(&value.to_string()).is_ok());
    }
}

#[cfg(test)]
mod input_parity_tests {
    use super::{Spec, Step, WALL_CURRENT_PER_MW_M2};
    use serde_json::{json, Value};

    fn library_spec() -> Value {
        json!({
            "spec": "actinv-spec-1",
            "library": {"path": "activation.npz"},
            "decay": {"primary": "decay.endf"},
            "material": {"basis": "wt_percent", "composition": {"FE": 100.0}},
            "spectrum": {
                "structure": "custom",
                "boundaries_eV": [1.0, 2.0],
                "flux_per_group": [1.0],
                "total": 1.0e14,
            },
            "schedule": [{"dt": "1 s", "flux": 1.0}],
        })
    }

    fn rebin_spec(convention: &str) -> Value {
        let mut value = library_spec();
        value["spectrum"] = json!({
            "structure": "fispact-709",
            "boundaries_eV": [1.0, 10.0, 100.0, 1000.0],
            "flux_per_group": [1.0, 2.0, 3.0],
            "rebin": {"convention": convention},
        });
        value
    }

    fn decay_only_spec() -> Value {
        json!({
            "spec": "actinv-spec-1",
            "decay": {"primary": "decay.endf"},
            "material": {"basis": "wt_percent", "composition": {"FE": 100.0}},
            "schedule": [{"dt": "1 d", "flux": 0.0}],
        })
    }

    pub(super) fn error_of(value: &Value) -> String {
        match Spec::from_json(&value.to_string()) {
            Err(error) => error,
            Ok(_) => panic!("spec unexpectedly validated: {value}"),
        }
    }

    #[test]
    fn rebin_conserves_total_flux_under_both_conventions() {
        let mut distributions = Vec::new();
        for (convention, cnvtype) in [("equal_lethargy", 0), ("equal_energy", 1)] {
            let spec = Spec::from_json(&rebin_spec(convention).to_string()).unwrap();
            let physical = spec.physical_inputs().unwrap();
            let flux = physical.flux.values().to_vec();
            assert_eq!(flux.len(), 709, "{convention}");
            let total: f64 = flux.iter().sum();
            assert!((total - 6.0).abs() <= 1e-12 * 6.0, "{convention}: {total}");
            let record = &physical.spectrum_conversions[0];
            assert_eq!(record["convention"], convention);
            assert_eq!(record["cnvtype"], cnvtype);
            assert_eq!(record["destination_groups"], 709);
            assert!(record["flux_conservation_residual"].as_f64().unwrap() <= 1e-12);
            assert!((record["destination_total"].as_f64().unwrap() - 6.0).abs() <= 1e-12 * 6.0);
            distributions.push(flux);
        }
        // the two conventions split a source group differently
        assert_ne!(distributions[0], distributions[1]);
    }

    #[test]
    fn rebin_default_convention_is_equal_lethargy_and_limgrp_is_recorded() {
        let mut value = rebin_spec("equal_lethargy");
        value["spectrum"]["rebin"] = json!({"emin_eV": 5.0, "emax_eV": 500.0});
        let spec = Spec::from_json(&value.to_string()).unwrap();
        assert_eq!(
            spec.spectrum.rebin.as_ref().unwrap().convention,
            "equal_lethargy"
        );
        let physical = spec.physical_inputs().unwrap();
        let record = &physical.spectrum_conversions[0];
        assert_eq!(record["emin_eV"], 5.0);
        assert_eq!(record["emax_eV"], 500.0);
        let total: f64 = physical.flux.values().iter().sum();
        assert!((total - 6.0).abs() <= 1e-12 * 6.0, "{total}");
    }

    #[test]
    fn plain_spectrum_has_no_conversion_record() {
        let spec = Spec::from_json(&library_spec().to_string()).unwrap();
        let physical = spec.physical_inputs().unwrap();
        assert!(physical.spectrum_conversions.is_empty());
        assert!(physical.wall_conversions.is_empty());
    }

    #[test]
    fn rebin_structural_errors() {
        let mut value = rebin_spec("equal_lethargy");
        value["spectrum"]["structure"] = json!("custom");
        assert!(error_of(&value).contains("must name one"));

        let mut value = rebin_spec("equal_lethargy");
        value["spectrum"]["relative_error"] = json!([0.1, 0.1, 0.1]);
        assert!(error_of(&value).contains("relative_error cannot accompany"));

        let mut value = rebin_spec("equal_lethargy");
        value["spectrum"]["boundaries_eV"] = json!([1.0, 100.0, 10.0, 1000.0]);
        assert!(error_of(&value).contains("strictly ascending"));

        let mut value = rebin_spec("equal_lethargy");
        value["spectrum"]["rebin"] = json!({"emin_eV": 100.0, "emax_eV": 10.0});
        assert!(error_of(&value).contains("emin_eV must be below emax_eV"));

        let value = rebin_spec("equal_width");
        assert!(error_of(&value).contains("unknown spectrum.rebin.convention"));
    }

    #[test]
    fn wall_loading_uses_the_manual_current_equivalence_without_a_reference() {
        assert_eq!(WALL_CURRENT_PER_MW_M2, 4.44e13);
        let mut value = library_spec();
        value["schedule"] = json!([
            {"dt": "1 s", "wall_loading_MW_m2": 2.0},
            {"dt": "1 s", "flux": 0.5},
        ]);
        let spec = Spec::from_json(&value.to_string()).unwrap();
        let physical = spec.physical_inputs().unwrap();
        // the spectrum total is 1e14 n cm^-2 s^-1, so 2 MW m^-2 is a multiplier of 0.888
        let multiplier = physical.schedule[0].multiplier().get();
        assert!((multiplier - 2.0 * 4.44e13 / 1.0e14).abs() <= 1e-15);
        assert_eq!(physical.schedule[1].multiplier().get(), 0.5);
        assert_eq!(physical.wall_conversions.len(), 1);
        let record = &physical.wall_conversions[0];
        assert_eq!(record["step"], 1);
        assert_eq!(record["basis"], "14_MeV_current_equivalence");
        assert_eq!(record["current_per_MW_m2_cm2_s"], 4.44e13);
        let step_flux = record["step_total_flux_cm2_s"].as_f64().unwrap();
        assert!((step_flux - 8.88e13).abs() <= 1e-9 * 8.88e13, "{step_flux}");
    }

    #[test]
    fn wall_loading_scales_against_the_spectrum_reference_when_declared() {
        let mut value = library_spec();
        value["spectrum"]["total"] = json!(1.8e15);
        value["spectrum"]["wall_loading_MW_m2"] = json!(4.15);
        value["schedule"] = json!([{"dt": "1 s", "wall_loading_MW_m2": 2.0}]);
        let spec = Spec::from_json(&value.to_string()).unwrap();
        let physical = spec.physical_inputs().unwrap();
        let multiplier = physical.schedule[0].multiplier().get();
        assert!((multiplier - 2.0 / 4.15).abs() <= 1e-15);
        let record = &physical.wall_conversions[0];
        assert_eq!(record["basis"], "spectrum_reference_wall_loading");
        assert_eq!(record["reference_wall_loading_MW_m2"], 4.15);
        assert!(record["current_per_MW_m2_cm2_s"].is_null());
        let step_flux = record["step_total_flux_cm2_s"].as_f64().unwrap();
        assert!((step_flux - 2.0 / 4.15 * 1.8e15).abs() <= 1e-9 * step_flux);
    }

    #[test]
    fn a_step_declares_exactly_one_of_flux_and_wall_loading() {
        let mut value = library_spec();
        value["schedule"] = json!([{"dt": "1 s", "flux": 1.0, "wall_loading_MW_m2": 2.0}]);
        assert!(error_of(&value).contains("both flux and wall_loading_MW_m2"));

        let mut value = library_spec();
        value["schedule"] = json!([{"dt": "1 s"}]);
        assert!(error_of(&value).contains("needs one of flux and wall_loading_MW_m2"));

        // a hand-built step with both set is also refused by validate()
        let mut spec = Spec::from_json(&library_spec().to_string()).unwrap();
        spec.schedule[0].wall_loading_MW_m2 = Some(2.0);
        assert!(spec
            .validate()
            .unwrap_err()
            .contains("both flux and wall_loading_MW_m2"));

        let mut value = library_spec();
        value["schedule"] = json!([{"dt": "1 s", "wall_loading_MW_m2": -1.0}]);
        assert!(error_of(&value).contains("wall_loading_MW_m2 must be finite and nonnegative"));
    }

    #[test]
    fn step_serialisation_adds_nothing_unless_a_new_field_is_used() {
        let spec = Spec::from_json(&library_spec().to_string()).unwrap();
        let plain = serde_json::to_value(&spec.schedule[0]).unwrap();
        let keys: Vec<&str> = plain
            .as_object()
            .unwrap()
            .keys()
            .map(String::as_str)
            .collect();
        assert_eq!(keys, ["dt", "feed", "flux", "removal", "spectrum"]);

        let mut value = library_spec();
        value["schedule"] = json!([{"dt": "1 s", "wall_loading_MW_m2": 2.0}]);
        let spec = Spec::from_json(&value.to_string()).unwrap();
        let wall = serde_json::to_value(&spec.schedule[0]).unwrap();
        assert!(wall.get("flux").is_none());
        assert_eq!(wall["wall_loading_MW_m2"], 2.0);
        // the written form reads back to the same step
        let again: Step = serde_json::from_value(wall).unwrap();
        assert_eq!(again.wall_loading_MW_m2, Some(2.0));
    }

    #[test]
    fn partition_keeps_the_stated_fraction_and_validates_its_range() {
        let mut value = library_spec();
        value["schedule"] = json!([
            {"dt": "1 s", "flux": 1.0},
            {"dt": "1 s", "flux": 0.0, "partition": {"Fe": 0.01, "Ar": 0.0}},
        ]);
        let spec = Spec::from_json(&value.to_string()).unwrap();
        let physical = spec.physical_inputs().unwrap();
        assert!(physical.schedule[0].partition.is_empty());
        let mut kept = physical.schedule[1].partition.clone();
        kept.sort_by_key(|(z, _)| *z);
        assert_eq!(kept, vec![(18, 0.0), (26, 0.01)]);

        for bad in [1.5, -0.1] {
            let mut value = library_spec();
            value["schedule"] = json!([{"dt": "1 s", "flux": 0.0, "partition": {"Fe": bad}}]);
            assert!(error_of(&value).contains("must lie in [0, 1]"), "{bad}");
        }
        let mut value = library_spec();
        value["schedule"] = json!([{"dt": "1 s", "flux": 0.0, "partition": {"Fe56": 0.5}}]);
        assert!(error_of(&value).contains("must name an element"));

        let mut value = library_spec();
        value["options"] = json!({"mode": "trace"});
        value["schedule"] = json!([{"dt": "1 s", "flux": 0.0, "partition": {"Fe": 0.5}}]);
        assert!(error_of(&value).contains("not supported with options.mode 'trace'"));
    }

    #[test]
    fn decay_only_spec_omits_library_and_spectrum() {
        let spec = Spec::from_json(&decay_only_spec().to_string()).unwrap();
        assert!(spec.library.path.is_empty());
        assert!(!spec.schedule[0].irradiates());
        // the placeholder spectrum is a valid zero-flux input
        let physical = spec.physical_inputs().unwrap();
        assert_eq!(physical.flux.values(), [0.0]);
    }

    #[test]
    fn decay_only_spec_rejects_anything_that_needs_an_activation_library() {
        let mut value = decay_only_spec();
        value["schedule"] = json!([{"dt": "1 d", "flux": 1.0}]);
        assert!(error_of(&value).contains("decay-only run"));

        let mut value = decay_only_spec();
        value["schedule"] = json!([{"dt": "1 d", "wall_loading_MW_m2": 1.0}]);
        assert!(error_of(&value).contains("decay-only run"));

        let mut value = decay_only_spec();
        value["library"] = json!({"path": "", "sha256": "0".repeat(64)});
        assert!(error_of(&value).contains("without a library.path"));

        let mut value = decay_only_spec();
        value["self_shielding"] =
            json!({"table": {"path": "table.json", "sha256": "0".repeat(64)}});
        assert!(error_of(&value).starts_with("self_shielding is not available"));

        let mut value = decay_only_spec();
        value["options"] = json!({"rate_scale": {"0": 1.1}});
        assert!(error_of(&value).contains("rate_scale"));

        let mut value = decay_only_spec();
        value["overrides"] =
            json!([{"nuclide": "Fe56", "kind": "cross_section", "mt": 102, "value": 1.0}]);
        assert!(error_of(&value).contains("override kind 'cross_section' is not available"));

        // a half-life override is a decay datum and stays allowed
        let mut value = decay_only_spec();
        value["overrides"] =
            json!([{"nuclide": "Co60", "kind": "half_life", "value": 5.0, "units": "y"}]);
        Spec::from_json(&value.to_string()).unwrap();
    }

    #[test]
    fn library_runs_still_need_the_library_path() {
        let mut value = library_spec();
        value["library"] = json!({"path": ""});
        assert!(error_of(&value).contains("decay-only run"));
    }

    #[test]
    fn override_validation_errors() {
        let cases = [
            (
                json!({"nuclide": "Fe56", "kind": "cross_section", "value": 1.0}),
                "mt is required",
            ),
            (
                json!({"nuclide": "Co60", "kind": "half_life", "mt": 102, "value": 1.0}),
                "mt applies to cross-section overrides only",
            ),
            (
                json!({"nuclide": "Fe56", "kind": "cross_section", "mt": 102, "value": -1.0}),
                "nonnegative barns",
            ),
            (
                json!({"nuclide": "Fe56", "kind": "cross_section_scale", "mt": 102, "value": 0.0}),
                "positive multiplier",
            ),
            (
                json!({"nuclide": "Fe56", "kind": "cross_section", "mt": 102, "value": 1.0,
                       "units": "s"}),
                "units apply to half_life only",
            ),
            (
                json!({"nuclide": "Co60", "kind": "half_life", "value": 1.0, "units": "fortnight"}),
                "unknown time unit",
            ),
            (
                json!({"nuclide": "Fe56", "kind": "resonance", "mt": 102, "value": 1.0}),
                "unknown override kind",
            ),
            (
                json!({"nuclide": "Fe56", "kind": "cross_section_uncertainty", "mt": 102,
                       "value": 0.1}),
                "needs an uncertainty section",
            ),
        ];
        for (item, expected) in cases {
            let mut value = library_spec();
            value["overrides"] = json!([item]);
            let error = error_of(&value);
            assert!(error.contains(expected), "{expected}: {error}");
        }

        let mut value = library_spec();
        value["overrides"] = json!([
            {"nuclide": "Fe56", "kind": "cross_section", "mt": 102, "value": 1.0},
            {"nuclide": "Fe56", "kind": "cross_section_scale", "mt": 102, "value": 2.0},
        ]);
        assert!(error_of(&value).contains("name the same reaction"));

        let mut value = library_spec();
        let item = json!({"nuclide": "Co60", "kind": "half_life", "value": 1.0});
        value["overrides"] = json!([item, item]);
        assert!(error_of(&value).contains("duplicate override"));

        let mut value = library_spec();
        value["overrides"] = json!([
            {"nuclide": "Fe56", "kind": "cross_section", "mt": 102, "value": 1.0},
            {"nuclide": "Co60", "kind": "half_life", "value": 5.0, "units": "y"},
            {"nuclide": "Fe56", "kind": "cross_section_scale", "mt": 16, "value": 0.5},
        ]);
        let spec = Spec::from_json(&value.to_string()).unwrap();
        assert_eq!(spec.overrides.len(), 3);
        assert_eq!(
            spec.overrides[1].half_life_seconds().unwrap(),
            5.0 * super::parse_duration("1 y").unwrap()
        );
    }
}

/// P140 H1 regression tests for the schedule-control findings (rebin, wall loading, partition,
/// decay-only runs). Each asserts the behaviour the finding calls for.
#[cfg(test)]
mod p140_schedule_tests {
    use super::input_parity_tests::error_of;
    use super::Spec;
    use serde_json::{json, Value};

    fn library_spec() -> Value {
        json!({
            "spec": "actinv-spec-1",
            "library": {"path": "activation.npz"},
            "decay": {"primary": "decay.endf"},
            "material": {"basis": "wt_percent", "composition": {"FE": 100.0}},
            "spectrum": {
                "structure": "custom",
                "boundaries_eV": [1.0, 2.0],
                "flux_per_group": [1.0],
                "total": 1.0e14,
            },
            "schedule": [{"dt": "1 s", "flux": 1.0}],
        })
    }

    fn rebin_spec() -> Value {
        let mut value = library_spec();
        value["spectrum"] = json!({
            "structure": "fispact-709",
            "boundaries_eV": [1.0, 10.0, 100.0, 1000.0],
            "flux_per_group": [1.0, 2.0, 3.0],
            "rebin": {},
        });
        value
    }

    fn decay_only_spec() -> Value {
        json!({
            "spec": "actinv-spec-1",
            "decay": {"primary": "decay.endf"},
            "material": {"basis": "wt_percent", "composition": {"FE": 100.0}},
            "schedule": [{"dt": "1 d", "flux": 0.0}],
        })
    }

    /// Validation followed by the physical-input resolution a run starts with.
    fn resolve(value: &Value) -> Result<(), String> {
        Spec::from_json(&value.to_string()).and_then(|spec| spec.physical_inputs().map(|_| ()))
    }

    /// A source spectrum wholly below the fispact-709 grid (1e-5 eV .. 1e9 eV).
    fn below_the_grid() -> Value {
        json!({
            "structure": "fispact-709",
            "boundaries_eV": [1.0e-12, 1.0e-11],
            "flux_per_group": [1.0e14],
            "rebin": {},
        })
    }

    #[test]
    fn p140_sc_f1_rebin_leaving_no_destination_flux_is_refused() {
        let mut value = library_spec();
        value["spectrum"] = below_the_grid();
        let error = resolve(&value)
            .expect_err("a rebin that lands none of the flux on the destination must be refused");
        assert!(
            error.contains("rebin") && error.contains("destination"),
            "{error}"
        );
        // a source that overlaps the destination grid is untouched by the refusal
        assert!(resolve(&rebin_spec()).is_ok());
    }

    #[test]
    fn p140_sc_f1_step_spectrum_rebin_leaving_no_destination_flux_is_refused() {
        let mut value = rebin_spec();
        value["schedule"] = json!([{"dt": "1 s", "flux": 1.0, "spectrum": below_the_grid()}]);
        let error = resolve(&value)
            .expect_err("a step spectrum that lands none of its flux must be refused");
        assert!(
            error.contains("rebin") && error.contains("destination"),
            "{error}"
        );
    }

    #[test]
    fn p140_sc_f2_zero_wall_loading_needs_no_spectrum_total() {
        // decay-only: the placeholder spectrum has no flux, and wall 0 is a cooling step
        let mut value = decay_only_spec();
        value["schedule"] = json!([{"dt": "1 d", "wall_loading_MW_m2": 0.0}]);
        let spec = Spec::from_json(&value.to_string()).unwrap();
        let physical = spec
            .physical_inputs()
            .expect("a zero wall loading is a zero multiplier whatever the spectrum");
        assert_eq!(physical.schedule[0].multiplier().get(), 0.0);

        // a library run on an all-zero spectrum
        let mut value = library_spec();
        value["spectrum"] = json!({
            "structure": "custom",
            "boundaries_eV": [1.0, 2.0],
            "flux_per_group": [0.0],
        });
        value["schedule"] = json!([{"dt": "1 d", "wall_loading_MW_m2": 0.0}]);
        let spec = Spec::from_json(&value.to_string()).unwrap();
        let physical = spec
            .physical_inputs()
            .expect("wall 0 on a zero spectrum runs");
        assert_eq!(physical.schedule[0].multiplier().get(), 0.0);

        // a positive wall loading still has nothing to scale
        value["schedule"] = json!([{"dt": "1 d", "wall_loading_MW_m2": 1.0}]);
        assert!(resolve(&value).unwrap_err().contains("positive total flux"));
    }

    #[test]
    fn p140_sc_f4_kerma_in_a_decay_only_spec_is_refused_at_validation() {
        let mut value = decay_only_spec();
        value["kerma"] = json!({
            "table": {"path": "kerma.json", "sha256": "0".repeat(64)},
        });
        let error = Spec::from_json(&value.to_string())
            .expect_err("a decay-only run has no activation library to take a kerma table from");
        assert!(
            error.contains("kerma") && error.contains("decay-only run"),
            "{error}"
        );
    }

    #[test]
    fn p140_sc_f6_empty_partition_is_no_partition() {
        let empty = json!([{"dt": "1 s", "flux": 1.0, "partition": {}}]);
        let mut failures = Vec::new();
        let mut check = |label: &str, value: &Value| {
            if let Err(error) = Spec::from_json(&value.to_string()) {
                failures.push(format!("{label}: {error}"));
            }
        };

        let mut value = library_spec();
        value["options"] = json!({"mode": "trace"});
        value["schedule"] = empty.clone();
        check("options.mode trace", &value);

        let mut value = library_spec();
        value["pathways"] = json!({"targets": ["Fe57"]});
        value["schedule"] = empty.clone();
        check("pathways", &value);

        let mut value = library_spec();
        value["schedule"] = json!([
            {"dt": "1 s", "flux": 1.0},
            {
                "dt": "2 s",
                "flux": 1.0,
                "library": {"path": "second.npz"},
                "spectrum": {
                    "structure": "custom",
                    "boundaries_eV": [1.0, 3.0, 5.0],
                    "flux_per_group": [1.0, 2.0],
                },
                "partition": {},
            },
        ]);
        check("step library override", &value);

        assert!(
            failures.is_empty(),
            "an empty partition is a no-op in the run and must not be refused: {failures:?}"
        );
    }

    #[test]
    fn p140_sc_f10_rebin_limits_outside_the_spectrum_are_refused_at_validation() {
        // the source spans 1 .. 1000 eV: emin at or above the top, emax at or below the bottom
        let mut failures = Vec::new();
        for (name, rebin) in [
            ("emin", json!({"emin_eV": 1.0e9})),
            ("emax", json!({"emax_eV": 0.5})),
        ] {
            let mut value = rebin_spec();
            value["spectrum"]["rebin"] = rebin.clone();
            match Spec::from_json(&value.to_string()) {
                Ok(_) => failures.push(format!("base {name}: validated")),
                Err(error) if !(error.contains(name) && error.contains("rebin")) => {
                    failures.push(format!("base {name}: unrelated message '{error}'"));
                }
                Err(_) => {}
            }
            let mut value = rebin_spec();
            value["schedule"] = json!([{
                "dt": "1 s",
                "flux": 1.0,
                "spectrum": {
                    "structure": "fispact-709",
                    "boundaries_eV": [1.0, 10.0, 100.0, 1000.0],
                    "flux_per_group": [1.0, 2.0, 3.0],
                    "rebin": rebin,
                },
            }]);
            match Spec::from_json(&value.to_string()) {
                Ok(_) => failures.push(format!("step {name}: validated")),
                Err(error) if !(error.contains(name) && error.contains("rebin")) => {
                    failures.push(format!("step {name}: unrelated message '{error}'"));
                }
                Err(_) => {}
            }
        }
        assert!(
            failures.is_empty(),
            "limits outside the spectrum are known at validation: {failures:?}"
        );
        // limits inside, or merely touching, the spectrum stay valid
        let mut value = rebin_spec();
        value["spectrum"]["rebin"] = json!({"emin_eV": 0.1, "emax_eV": 5000.0});
        assert!(Spec::from_json(&value.to_string()).is_ok());
    }

    #[test]
    fn p140_sc_f12_wall_loading_fallback_is_refused_for_non_neutron_projectiles() {
        let mut value = library_spec();
        value["projectile"] = json!("proton");
        value["options"] = json!({"temperature_K": 0.0});
        value["schedule"] = json!([{"dt": "1 s", "wall_loading_MW_m2": 1.0}]);
        let error = resolve(&value).expect_err(
            "4.44e13 n cm^-2 s^-1 per MW m^-2 is a 14 MeV neutron equivalence, not a proton one",
        );
        assert!(
            error.contains("wall_loading") && error.contains("neutron"),
            "{error}"
        );
        // a declared reference loading does not use the neutron current and stays valid
        value["spectrum"]["wall_loading_MW_m2"] = json!(4.15);
        assert!(resolve(&value).is_ok());
    }

    #[test]
    fn p140_sc_f16_omitted_base_spectrum_is_not_reported_as_the_placeholder() {
        // a step spectrum on a spec that declares no base spectrum
        let mut value = decay_only_spec();
        value["schedule"] = json!([{
            "dt": "1 d",
            "flux": 0.0,
            "spectrum": {"structure": "fispact-709", "flux_per_group": vec![0.0; 709]},
        }]);
        let error = resolve(&value).expect_err("a step spectrum needs a base spectrum to match");
        assert!(
            !error.contains("custom"),
            "the zero-flux placeholder must not leak into the message: {error}"
        );
        assert!(
            error.contains("spectrum"),
            "the message names the spectrum: {error}"
        );
    }

    // ---- P140: photon/radiological validation

    #[test]
    fn p140_ph_f4_point_source_needs_a_photon_producing_output() {
        // options.outputs without "photons" or "dose" builds no photon source, so a requested
        // point-source dose would be certified without having been computed.
        for outputs in [json!(["activity"]), json!(["inventory", "heat"]), json!([])] {
            let mut value = decay_only_spec();
            value["options"] = json!({"outputs": outputs});
            value["photon"] = json!({"point_source": {"distance_m": 1.0}});
            let error = error_of(&value);
            assert!(
                error.contains("photon.point_source") && error.contains("options.outputs"),
                "{outputs}: {error}"
            );
        }
    }

    #[test]
    fn p140_ph_f4_bremsstrahlung_needs_a_photon_producing_output() {
        for outputs in [json!(["activity"]), json!(["radiological", "ledger"])] {
            let mut value = decay_only_spec();
            value["options"] = json!({"outputs": outputs});
            value["photon"] = json!({"bremsstrahlung": {"nuclides": ["Cl36"]}});
            let error = error_of(&value);
            assert!(
                error.contains("photon.bremsstrahlung") && error.contains("options.outputs"),
                "{outputs}: {error}"
            );
        }
    }

    #[test]
    fn p140_ph_f4_guard_photon_extras_stay_valid_when_photons_are_produced() {
        // Controls that already hold: a photon-producing output list, or none at all, and an
        // output list that skips photons when no photon extra is requested.
        for outputs in [
            json!(["photons"]),
            json!(["dose"]),
            json!(["activity", "photons"]),
        ] {
            let mut value = decay_only_spec();
            value["options"] = json!({"outputs": outputs});
            value["photon"] = json!({
                "point_source": {"distance_m": 1.0},
                "bremsstrahlung": {"nuclides": ["Cl36"]},
            });
            assert!(Spec::from_json(&value.to_string()).is_ok(), "{outputs}");
        }
        let mut value = decay_only_spec();
        value["photon"] = json!({"point_source": {"distance_m": 1.0}});
        assert!(Spec::from_json(&value.to_string()).is_ok());
        let mut value = decay_only_spec();
        value["options"] = json!({"outputs": ["activity"]});
        assert!(Spec::from_json(&value.to_string()).is_ok());
    }

    #[test]
    fn p140_rad_f8_unknown_bundled_response_selector_fails_validation() {
        // The bundled table is in memory at validation time, so a typo must not wait for the run.
        for (table, bad, good) in [
            ("us-49cfr173-a2", "a2-u-medum", "a2-u-medium"),
            (
                "euratom-2013-59-annex-vii-a",
                "annex-vii-table-a",
                "annex-vii-table-a-part1",
            ),
        ] {
            let sha256 = crate::radiological::bundled_sha256(table).unwrap();
            let mut value = decay_only_spec();
            value["radiological"] = json!({
                "table": {"path": format!("bundled:{table}"), "sha256": sha256},
                "responses": [good],
            });
            assert!(
                Spec::from_json(&value.to_string()).is_ok(),
                "{table}: the real selector '{good}' must validate"
            );
            value["radiological"]["responses"] = json!([good, bad]);
            let error = error_of(&value);
            assert!(
                error.contains(bad) && error.contains("radiological response selector"),
                "{table}: {error}"
            );
        }
    }
}

/// P140 regressions (SG-02, SG-13): they assert the intended behaviour.
#[cfg(test)]
mod p140_sg_tests {
    use super::{Projectile, Spec, Spectrum};
    use actinv_data::groups::{GroupStructure, NAMED_STRUCTURES};
    use serde_json::{json, Value};

    fn flat_spectrum(structure: &str, groups: usize) -> Spectrum {
        serde_json::from_value(json!({
            "structure": structure,
            "flux_per_group": vec![1.0; groups],
        }))
        .unwrap()
    }

    #[test]
    fn p140_sg02_step_library_accepts_every_named_neutron_structure() {
        for (name, _) in NAMED_STRUCTURES {
            if name == "fispact-162" {
                continue;
            }
            let groups = GroupStructure::named(name).unwrap().unwrap().groups();
            flat_spectrum(name, groups)
                .check_structure_for(Projectile::Neutron, "schedule step spectrum")
                .unwrap_or_else(|error| panic!("{name} on a neutron step library: {error}"));
            // A refinement of the named grid, as for the base spectrum.
            flat_spectrum(&format!("{name}x2"), 2 * groups)
                .check_structure_for(Projectile::Neutron, "schedule step spectrum")
                .unwrap_or_else(|error| panic!("{name}x2 on a neutron step library: {error}"));
            // A neutron grid does not suit a charged-particle step library.
            assert!(
                flat_spectrum(name, groups)
                    .check_structure_for(Projectile::Proton, "schedule step spectrum")
                    .is_err(),
                "{name} must not suit a proton library"
            );
            // The flux count is still checked against the grid.
            assert!(flat_spectrum(name, groups + 1)
                .check_structure_for(Projectile::Neutron, "schedule step spectrum")
                .is_err());
        }
        // fispact-162 suits every non-neutron projectile and no neutron library.
        let spectrum = flat_spectrum("fispact-162", 162);
        assert!(spectrum
            .check_structure_for(Projectile::Alpha, "schedule step spectrum")
            .is_ok());
        assert!(spectrum
            .check_structure_for(Projectile::Neutron, "schedule step spectrum")
            .is_err());
        assert!(flat_spectrum("fispact-nonsense", 3)
            .check_structure_for(Projectile::Neutron, "schedule step spectrum")
            .is_err());
    }

    fn geometry_spec(material_density: Option<f64>, geometry_density: Option<f64>) -> Value {
        let mut material = json!({"basis": "wt_percent", "composition": {"FE": 100.0}});
        if let Some(density) = material_density {
            material["density_g_cm3"] = json!(density);
        }
        let mut geometry = json!({
            "shape": "foil",
            "length_cm": 0.01,
            "resonance_files": [{"path": "w184.endf", "sha256": "0".repeat(64)}],
        });
        if let Some(density) = geometry_density {
            geometry["density_g_cm3"] = json!(density);
        }
        json!({
            "spec": "actinv-spec-1",
            "library": {"path": "activation.npz"},
            "decay": {"primary": "decay.endf"},
            "material": material,
            "spectrum": {
                "structure": "custom",
                "boundaries_eV": [1.0, 2.0],
                "flux_per_group": [1.0],
            },
            "schedule": [{"dt": "1 s", "flux": 1.0}],
            "self_shielding": {"geometry": geometry},
        })
    }

    #[test]
    fn p140_sg13_geometry_density_defaults_to_the_material_density() {
        // Expected: accepted; the target density is the material's. Current code requires the key.
        let spec = Spec::from_json(&geometry_spec(Some(19.3), None).to_string())
            .expect("geometry.density_g_cm3 may be omitted when material.density_g_cm3 is given");
        assert!(spec.self_shielding.is_some());
        // With neither density the section is still refused, naming the density.
        let error = Spec::from_json(&geometry_spec(None, None).to_string()).unwrap_err();
        assert!(error.contains("density_g_cm3"), "{error}");
    }

    #[test]
    fn p140_sg13_geometry_density_disagreeing_with_the_material_density_is_an_error() {
        // Agreement is fine.
        assert!(Spec::from_json(&geometry_spec(Some(19.3), Some(19.3)).to_string()).is_ok());
        // Expected: iron at 7.874 g/cm^3 cannot be a 19.3 g/cm^3 foil. Current code accepts it.
        let error = Spec::from_json(&geometry_spec(Some(7.874), Some(19.3)).to_string())
            .expect_err("two different densities for one material must be refused");
        assert!(error.contains("density"), "{error}");
    }
}

#[cfg(test)]
mod photon_option_tests {
    use super::{MissingSpectra, PhotonOptions};

    #[test]
    fn default_options_serialize_without_the_p152_keys() {
        let value = serde_json::to_value(PhotonOptions::default()).unwrap();
        let object = value.as_object().unwrap();
        assert!(!object.contains_key("missing_spectra"));
        assert!(!object.contains_key("exclude_xrays"));
        // The round trip is byte-identical.
        let text = serde_json::to_string(&PhotonOptions::default()).unwrap();
        let again: PhotonOptions = serde_json::from_str(&text).unwrap();
        assert_eq!(serde_json::to_string(&again).unwrap(), text);
    }

    #[test]
    fn explicit_report_and_false_serialize_like_the_default() {
        let explicit: PhotonOptions =
            serde_json::from_str(r#"{"missing_spectra": "report", "exclude_xrays": false}"#)
                .unwrap();
        assert_eq!(
            serde_json::to_string(&explicit).unwrap(),
            serde_json::to_string(&PhotonOptions::default()).unwrap()
        );
    }

    #[test]
    fn requested_options_round_trip_and_unknown_values_are_refused() {
        let options: PhotonOptions =
            serde_json::from_str(r#"{"missing_spectra": "estimate", "exclude_xrays": true}"#)
                .unwrap();
        assert_eq!(options.missing_spectra, MissingSpectra::Estimate);
        assert!(options.exclude_xrays);
        let value = serde_json::to_value(&options).unwrap();
        assert_eq!(value["missing_spectra"], "estimate");
        assert_eq!(value["exclude_xrays"], true);
        assert!(serde_json::from_str::<PhotonOptions>(r#"{"missing_spectra": "guess"}"#).is_err());
    }
}
