//! Named data overrides (P124, FISPACT-II `OVER`): per-nuclide replacements of a reaction's
//! one-group collapsed cross section, of a half-life, and of their declared uncertainties.
//!
//! The library is never edited. A cross-section override is a view over the activation library
//! that substitutes the collapsed value of the named rows, so every consumer of the collapse
//! (reaction rates, burn-up optical depth, mode selection) sees one consistent value; a half-life
//! override is applied by the run as a decay-constant factor on the prepared chain.
use crate::spec::NamedOverride;
use actinv_data::library::{ReactionLibrary, Row};
use std::collections::HashMap;

/// How one library row's collapsed value is replaced.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) enum RowRule {
    /// `cross_section` (`ACROSS`): the reaction's collapsed value becomes `value_b` barns. The
    /// loss row (`zap == -1`) takes it outright; each product row keeps its share of the loss
    /// row's original collapsed value, so isomer branching ratios are preserved.
    Absolute { value_b: f64, loss_row: usize },
    /// `cross_section_scale`: every row of the reaction is multiplied by the factor.
    Scale(f64),
}

/// One resolved cross-section override, for the ledger.
#[derive(Clone, Debug)]
pub(crate) struct CrossSectionOverride {
    pub(crate) nuclide: String,
    pub(crate) kind: String,
    pub(crate) mt: i32,
    pub(crate) value: f64,
    /// Every library row of the reaction.
    pub(crate) rows: Vec<usize>,
    /// The loss row, which carries the reaction's total collapsed value (the first row when the
    /// library has no loss row for a `cross_section_scale`).
    pub(crate) representative_row: usize,
}

/// The cross-section overrides of a spec, resolved to library rows.
#[derive(Debug, Default)]
pub(crate) struct CrossSectionOverrides {
    rules: HashMap<usize, RowRule>,
    pub(crate) entries: Vec<CrossSectionOverride>,
}

impl CrossSectionOverrides {
    pub(crate) fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// Whether any override sets an absolute value (which self-shielding cannot be folded into).
    pub(crate) fn has_absolute(&self) -> bool {
        self.entries
            .iter()
            .any(|entry| entry.kind == "cross_section")
    }
}

/// Parses an override's nuclide as an explicit `(ZA, LISO)` identity.
pub(crate) fn nuclide_key(raw: &str) -> Result<(i32, i32), String> {
    match actinv_data::composition::material_key(raw) {
        Ok(actinv_data::composition::MaterialKey::Nuclide { za, liso, .. }) => Ok((za, liso)),
        Ok(_) => Err(format!(
            "override nuclide '{raw}' must be an explicit nuclide such as 'Pu239'"
        )),
        Err(error) => Err(format!("override nuclide '{raw}': {error}")),
    }
}

/// Library rows (and their loss row) of `nuclide`'s reaction `mt`.
fn reaction_rows(
    item: &NamedOverride,
    rows: &[Row],
    targets: &[(i32, i32)],
) -> Result<(i32, Vec<usize>, Option<usize>), String> {
    let key = nuclide_key(&item.nuclide)?;
    let mt = item
        .mt
        .ok_or_else(|| format!("override {} '{}' has no mt", item.kind, item.nuclide))?;
    let target = targets
        .iter()
        .position(|candidate| *candidate == key)
        .ok_or_else(|| {
            format!(
                "override {} '{}' names a nuclide that is not a target of the activation library",
                item.kind, item.nuclide
            )
        })?;
    let matched: Vec<usize> = rows
        .iter()
        .enumerate()
        .filter(|(_, row)| row.target == target && row.mt == mt)
        .map(|(index, _)| index)
        .collect();
    if matched.is_empty() {
        return Err(format!(
            "override {} '{}' MT {mt} matches no activation-library row",
            item.kind, item.nuclide
        ));
    }
    let loss_row = matched.iter().copied().find(|&index| rows[index].zap == -1);
    Ok((mt, matched, loss_row))
}

/// Resolves the `cross_section` and `cross_section_scale` overrides against the library.
pub(crate) fn resolve_cross_sections(
    overrides: &[NamedOverride],
    rows: &[Row],
    targets: &[(i32, i32)],
) -> Result<CrossSectionOverrides, String> {
    let mut resolved = CrossSectionOverrides::default();
    for item in overrides
        .iter()
        .filter(|item| item.is_cross_section_value())
    {
        let (mt, matched, loss_row) = reaction_rows(item, rows, targets)?;
        let rule = if item.kind == "cross_section" {
            RowRule::Absolute {
                value_b: item.value,
                loss_row: loss_row.ok_or_else(|| {
                    format!(
                        "override cross_section '{}' MT {mt} has no loss row to anchor the \
                         reaction's total; use cross_section_scale",
                        item.nuclide
                    )
                })?,
            }
        } else {
            RowRule::Scale(item.value)
        };
        for &index in &matched {
            resolved.rules.insert(index, rule);
        }
        resolved.entries.push(CrossSectionOverride {
            nuclide: item.nuclide.clone(),
            kind: item.kind.clone(),
            mt,
            value: item.value,
            representative_row: loss_row.unwrap_or(matched[0]),
            rows: matched,
        });
    }
    Ok(resolved)
}

/// The activation library with the named rows' collapsed cross sections replaced.
pub(crate) struct OverriddenLibrary<'a> {
    base: &'a dyn ReactionLibrary,
    overrides: &'a CrossSectionOverrides,
}

impl<'a> OverriddenLibrary<'a> {
    pub(crate) fn new(base: &'a dyn ReactionLibrary, overrides: &'a CrossSectionOverrides) -> Self {
        Self { base, overrides }
    }

    /// A positive absolute cross section cannot be set on a reaction whose original collapsed
    /// value is zero under `phi`: its split among product rows is undefined.
    pub(crate) fn check_spectrum(&self, phi: &[f64]) -> Result<(), String> {
        for entry in &self.overrides.entries {
            if entry.kind == "cross_section"
                && entry.value > 0.0
                && self.base.one_group(entry.representative_row, phi) <= 0.0
            {
                return Err(format!(
                    "override cross_section '{}' MT {} cannot set a nonzero value: the reaction \
                     collapses to zero under a spectrum of the schedule",
                    entry.nuclide, entry.mt
                ));
            }
        }
        Ok(())
    }

    /// The reaction's collapsed cross section before and after the override, under `phi`.
    pub(crate) fn before_after(&self, entry: &CrossSectionOverride, phi: &[f64]) -> (f64, f64) {
        (
            self.base.one_group(entry.representative_row, phi),
            self.one_group(entry.representative_row, phi),
        )
    }
}

impl ReactionLibrary for OverriddenLibrary<'_> {
    fn rows(&self) -> &[Row] {
        self.base.rows()
    }

    fn group_count(&self) -> usize {
        self.base.group_count()
    }

    fn boundaries_ev(&self) -> &[f64] {
        self.base.boundaries_ev()
    }

    fn collapse_row(
        &self,
        row: usize,
        phi: &[f64],
        flux_denominator: f64,
        first_flux_group: usize,
        last_flux_group: usize,
    ) -> f64 {
        let collapsed = self.base.collapse_row(
            row,
            phi,
            flux_denominator,
            first_flux_group,
            last_flux_group,
        );
        match self.overrides.rules.get(&row) {
            None => collapsed,
            Some(RowRule::Scale(factor)) => collapsed * factor,
            Some(RowRule::Absolute { value_b, loss_row }) => {
                if *loss_row == row {
                    return *value_b;
                }
                let loss = self.base.collapse_row(
                    *loss_row,
                    phi,
                    flux_denominator,
                    first_flux_group,
                    last_flux_group,
                );
                if loss > 0.0 {
                    collapsed * (value_b / loss)
                } else {
                    0.0
                }
            }
        }
    }

    fn collapse_row_scaled(
        &self,
        row: usize,
        phi: &[f64],
        flux_denominator: f64,
        first_flux_group: usize,
        last_flux_group: usize,
        scale: &dyn Fn(usize) -> f64,
    ) -> f64 {
        let collapsed = self.base.collapse_row_scaled(
            row,
            phi,
            flux_denominator,
            first_flux_group,
            last_flux_group,
            scale,
        );
        // An absolute value cannot be folded into the shielded collapse; the run refuses that
        // combination before getting here.
        match self.overrides.rules.get(&row) {
            Some(RowRule::Scale(factor)) => collapsed * factor,
            _ => collapsed,
        }
    }

    fn row_cross_section(&self, row: usize, group: usize) -> f64 {
        self.base.row_cross_section(row, group)
    }

    fn fission_average_energy_ev(&self, row: usize, phi: &[f64]) -> Result<Option<f64>, String> {
        self.base.fission_average_energy_ev(row, phi)
    }
}

/// Per-parameter scale factors realising the `cross_section_uncertainty` overrides (`ADCROSS`).
///
/// `row_indices[i]`/`one_group_barns[i]` are the library row and nominal collapsed value of
/// covariance parameter `i`, and `covariance` is the dense row-major matrix over those
/// parameters. A covered parameter of the named reaction gets the factor that makes its relative
/// standard deviation `value`; with the matrix rescaled by [`rescale_covariance`] the
/// correlations are unchanged. Returns the factors and one ledger record per override.
pub(crate) fn uncertainty_scales(
    overrides: &[NamedOverride],
    rows: &[Row],
    targets: &[(i32, i32)],
    row_indices: &[usize],
    one_group_barns: &[f64],
    covariance: &[f64],
) -> Result<(Vec<f64>, Vec<serde_json::Value>), String> {
    let n = row_indices.len();
    if one_group_barns.len() != n || covariance.len() != n * n {
        return Err("covariance parameter bookkeeping is inconsistent".into());
    }
    let mut scale = vec![1.0; n];
    let mut records = Vec::new();
    for item in overrides
        .iter()
        .filter(|item| item.kind == "cross_section_uncertainty")
    {
        let (mt, matched, _) = reaction_rows(item, rows, targets)?;
        let (mut rescaled, mut without_variance) = (0usize, 0usize);
        for (parameter, row) in row_indices.iter().enumerate() {
            if !matched.contains(row) {
                continue;
            }
            let variance = covariance[parameter * n + parameter];
            if variance > 0.0 {
                scale[parameter] = item.value * one_group_barns[parameter] / variance.sqrt();
                rescaled += 1;
            } else {
                without_variance += 1;
            }
        }
        if rescaled == 0 {
            return Err(format!(
                "override cross_section_uncertainty '{}' MT {mt}: no covariance-covered parameter \
                 with nonzero variance to rescale",
                item.nuclide
            ));
        }
        records.push(serde_json::json!({
            "nuclide": item.nuclide,
            "kind": item.kind,
            "mt": mt,
            "relative_standard_uncertainty": item.value,
            "parameters_rescaled": rescaled,
            "parameters_without_variance": without_variance,
        }));
    }
    Ok((scale, records))
}

/// Multiplies a dense row-major `scale.len()`-square covariance by `scale[i] * scale[j]`.
pub(crate) fn rescale_covariance(covariance: &mut [f64], scale: &[f64]) {
    let n = scale.len();
    for (i, scale_i) in scale.iter().enumerate() {
        for (j, scale_j) in scale.iter().enumerate() {
            covariance[i * n + j] *= scale_i * scale_j;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::chain::{self, Chain, ChainLedger, RateLedger};
    use actinv_data::fission::EffectiveYields;
    use actinv_data::library::Library;

    fn item(kind: &str, mt: Option<i32>, value: f64) -> NamedOverride {
        NamedOverride {
            nuclide: "Fe56".into(),
            kind: kind.into(),
            mt,
            value,
            units: None,
        }
    }

    /// Fe56 (n,gamma): a loss row of 10 b and product rows of 6 b (ground) and 4 b (isomer),
    /// in one group.
    fn library() -> Library {
        let row = |zap: i32, lfs: i32| Row {
            target: 0,
            mt: 102,
            zap,
            lfs,
            lmf: 3,
        };
        Library {
            rows: vec![row(-1, 0), row(26_057, 0), row(26_057, 1)],
            sig: vec![10.0, 6.0, 4.0],
            ngroups: 1,
            bounds: vec![1.0, 2.0],
        }
    }

    fn chain() -> Chain {
        Chain {
            index: HashMap::from([((26_056, 0), 0), ((26_057, 0), 1)]),
            keys: vec![(26_056, 0), (26_057, 0)],
            lambda: vec![0.0, 0.0],
            decay: Vec::new(),
            leak: 2,
            unit: 3,
            n: 4,
            ledger: ChainLedger::default(),
        }
    }

    const TARGETS: [(i32, i32); 1] = [(26_056, 0)];
    const PHI: [f64; 1] = [1.0e14];
    fn per_barn() -> f64 {
        crate::power::square_cm_per_barn() * 1.0e14
    }

    fn rates(library: &dyn ReactionLibrary) -> Vec<(usize, usize, f64)> {
        chain::reaction_rates(
            library,
            &TARGETS,
            &PHI,
            &chain(),
            &HashMap::<(i32, i32), EffectiveYields>::new(),
            &mut RateLedger::default(),
            None,
            None,
            false,
        )
    }

    #[test]
    fn absolute_override_sets_the_collapsed_value_and_keeps_branching() {
        let library = library();
        let resolved = resolve_cross_sections(
            &[item("cross_section", Some(102), 5.0)],
            library.rows(),
            &TARGETS,
        )
        .unwrap();
        assert_eq!(resolved.entries[0].rows, vec![0, 1, 2]);
        assert_eq!(resolved.entries[0].representative_row, 0);
        let view = OverriddenLibrary::new(&library, &resolved);
        view.check_spectrum(&PHI).unwrap();
        // The loss row is the new total; the 6:4 product split is halved with it.
        let sigma: Vec<f64> = (0..3).map(|row| view.one_group(row, &PHI)).collect();
        assert!((sigma[0] - 5.0).abs() < 1e-15, "{sigma:?}");
        assert!((sigma[1] - 3.0).abs() < 1e-15, "{sigma:?}");
        assert!((sigma[2] - 2.0).abs() < 1e-15, "{sigma:?}");
        assert_eq!(view.before_after(&resolved.entries[0], &PHI), (10.0, 5.0));
        // The effective rate follows: the unmodified library burns at 10 b, the view at 5 b.
        let plain = rates(&library);
        let changed = rates(&view);
        assert!((plain[0].2 + 10.0 * per_barn()).abs() < 1e-22, "{plain:?}");
        assert!(
            (changed[0].2 + 5.0 * per_barn()).abs() < 1e-22,
            "{changed:?}"
        );
        assert!(
            (changed[1].2 - 3.0 * per_barn()).abs() < 1e-22,
            "{changed:?}"
        );
    }

    #[test]
    fn scale_override_multiplies_every_row_and_zero_turns_the_reaction_off() {
        let library = library();
        let resolved = resolve_cross_sections(
            &[item("cross_section_scale", Some(102), 0.25)],
            library.rows(),
            &TARGETS,
        )
        .unwrap();
        let view = OverriddenLibrary::new(&library, &resolved);
        let sigma: Vec<f64> = (0..3).map(|row| view.one_group(row, &PHI)).collect();
        assert_eq!(sigma, vec![2.5, 1.5, 1.0]);

        // ACROSS ... 0.0 (the manual's "remove capture" use) leaves no triplet at all.
        let resolved = resolve_cross_sections(
            &[item("cross_section", Some(102), 0.0)],
            library.rows(),
            &TARGETS,
        )
        .unwrap();
        assert!(rates(&OverriddenLibrary::new(&library, &resolved)).is_empty());
    }

    #[test]
    fn unresolvable_overrides_are_named_errors() {
        let library = library();
        let rows = library.rows();
        let mut absent_nuclide = item("cross_section", Some(102), 1.0);
        absent_nuclide.nuclide = "Co59".into();
        assert!(resolve_cross_sections(&[absent_nuclide], rows, &TARGETS)
            .unwrap_err()
            .contains("not a target"));
        assert!(
            resolve_cross_sections(&[item("cross_section", Some(16), 1.0)], rows, &TARGETS)
                .unwrap_err()
                .contains("matches no activation-library row")
        );
        let mut element = item("cross_section", Some(102), 1.0);
        element.nuclide = "Fe".into();
        assert!(resolve_cross_sections(&[element], rows, &TARGETS)
            .unwrap_err()
            .contains("explicit nuclide"));
        // A reaction that collapses to zero under the spectrum cannot be given a nonzero value.
        let resolved =
            resolve_cross_sections(&[item("cross_section", Some(102), 1.0)], rows, &TARGETS)
                .unwrap();
        let view = OverriddenLibrary::new(&library, &resolved);
        assert!(view
            .check_spectrum(&[0.0])
            .unwrap_err()
            .contains("collapses to zero"));
    }

    #[test]
    fn uncertainty_override_rescales_the_named_parameter_and_keeps_correlation() {
        // Parameters: the Fe56 loss row (10 b) and an unrelated row (3 b), correlation 0.5.
        let rows = vec![
            Row {
                target: 0,
                mt: 102,
                zap: -1,
                lfs: 0,
                lmf: 3,
            },
            Row {
                target: 0,
                mt: 16,
                zap: -1,
                lfs: 0,
                lmf: 3,
            },
        ];
        let mut covariance = vec![1.0, 0.15, 0.15, 0.09];
        let (scale, records) = uncertainty_scales(
            &[item("cross_section_uncertainty", Some(102), 0.05)],
            &rows,
            &TARGETS,
            &[0, 1],
            &[10.0, 3.0],
            &covariance,
        )
        .unwrap();
        // sigma_nom * 5 % = 0.5 b, against the declared 1 b: factor 0.5; the other row untouched.
        assert_eq!(scale, vec![0.5, 1.0]);
        assert_eq!(records[0]["parameters_rescaled"], 1);
        rescale_covariance(&mut covariance, &scale);
        assert!((covariance[0] - 0.25).abs() < 1e-15);
        assert!((covariance[3] - 0.09).abs() < 1e-15);
        let correlation = covariance[1] / (covariance[0].sqrt() * covariance[3].sqrt());
        assert!((correlation - 0.5).abs() < 1e-15, "{correlation}");
        // No covered parameter of the reaction: named error.
        assert!(uncertainty_scales(
            &[item("cross_section_uncertainty", Some(107), 0.05)],
            &rows,
            &TARGETS,
            &[0, 1],
            &[10.0, 3.0],
            &[1.0, 0.0, 0.0, 0.09],
        )
        .is_err());
    }
}
