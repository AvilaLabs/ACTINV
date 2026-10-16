#![allow(non_snake_case)] // JSON wire names carry their physical units (boundaries_eV, sigma_barns)
//! Opt-in high-energy splice (FISPACT-II `USESPALLATION` parity).
//!
//! A user-supplied table of residual-product cross sections per energy group, typically from an intra-nuclear
//! cascade code, replaces or extends the activation library's rows in every group at and above a splice energy.
//! Groups below the splice are never touched. Nothing is fetched or invented here: the table is the only source
//! of the spliced values, and its identity, the effective splice energy and the rows it changed are recorded in
//! the library index. Library rows the table does not replace keep their values even when they are nonzero in a
//! spliced group (a residual-arithmetic production row of the same channel is not the table's `lmf=10` row); the
//! splice reports them so a double count is visible.
//!
//! Table format `actinv-spallation-residuals-1` (JSON, unknown fields rejected):
//!
//! ```json
//! {
//!   "schema": "actinv-spallation-residuals-1",
//!   "description": "origin of the data (code, version, run) - required, free text",
//!   "projectile": "proton",
//!   "boundaries_eV": [2.0e8, 2.4e8, ..., 1.0e9],
//!   "targets": [
//!     {"za": 82208, "liso": 0, "channels": [
//!       {"mt": 5,
//!        "loss_barns": [ ... ],
//!        "products": [{"zap": 80200, "lfs": 0, "sigma_barns": [ ... ]}]}
//!     ]}
//!   ]
//! }
//! ```
//!
//! `boundaries_eV` are ascending and must be exactly the library's group boundaries from the effective splice
//! energy up to the top of the library, so every table group is one library group. Each `sigma_barns` /
//! `loss_barns` array has one value per table group. A product becomes a library row `(mt, zap, lfs, lmf=10)`,
//! the MF=10 residual-production convention FISPACT merges; `loss_barns`, when given, becomes the matching
//! target-depletion row `(mt, -1, -1, 0)`. With no `loss_barns` the channel produces nuclides without depleting
//! the target.

use crate::activation::Projectile;
use crate::library::{Library, Row};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::collections::{BTreeSet, HashMap, HashSet};
use std::path::Path;

pub const SPALLATION_SCHEMA: &str = "actinv-spallation-residuals-1";

/// FISPACT-II's example splice energy (200 MeV) for the default user setting.
pub const DEFAULT_SPLICE_EV: f64 = 2.0e8;

const BOUNDARY_RELATIVE_TOLERANCE: f64 = 1e-12;

#[derive(Clone, Debug, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct SpallationTable {
    pub schema: String,
    pub description: String,
    pub projectile: String,
    #[serde(rename = "boundaries_eV")]
    pub boundaries_eV: Vec<f64>,
    pub targets: Vec<SpallationTarget>,
}

#[derive(Clone, Debug, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct SpallationTarget {
    pub za: i32,
    pub liso: i32,
    pub channels: Vec<SpallationChannel>,
}

#[derive(Clone, Debug, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct SpallationChannel {
    pub mt: i32,
    #[serde(default)]
    pub loss_barns: Option<Vec<f64>>,
    pub products: Vec<SpallationProduct>,
}

#[derive(Clone, Debug, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct SpallationProduct {
    pub zap: i32,
    #[serde(default)]
    pub lfs: i32,
    pub sigma_barns: Vec<f64>,
}

fn check_values(values: &[f64], groups: usize, label: &str) -> Result<(), String> {
    if values.len() != groups {
        return Err(format!(
            "{label} has {} values; the table has {groups} groups",
            values.len()
        ));
    }
    if values
        .iter()
        .any(|value| !value.is_finite() || *value < 0.0)
    {
        return Err(format!("{label} must be finite and nonnegative"));
    }
    Ok(())
}

impl SpallationTable {
    pub fn from_json(text: &str) -> Result<Self, String> {
        let table: Self = serde_json::from_str(text)
            .map_err(|error| format!("invalid spallation table: {error}"))?;
        table.validate()?;
        Ok(table)
    }

    pub fn groups(&self) -> usize {
        self.boundaries_eV.len().saturating_sub(1)
    }

    /// Structural validation independent of any library: schema, boundaries, array lengths, value ranges and
    /// duplicate keys. Library compatibility (grid, targets, projectile) is checked by [`SpallationTable::apply`].
    pub fn validate(&self) -> Result<(), String> {
        if self.schema != SPALLATION_SCHEMA {
            return Err(format!(
                "unsupported spallation table schema '{}'; expected {SPALLATION_SCHEMA}",
                self.schema
            ));
        }
        if self.description.trim().is_empty() {
            return Err(
                "spallation table needs a nonempty description naming its data source".into(),
            );
        }
        Projectile::parse(&self.projectile)?;
        if self.boundaries_eV.len() < 2 {
            return Err("spallation table boundaries_eV needs at least two values".into());
        }
        if self
            .boundaries_eV
            .iter()
            .any(|value| !value.is_finite() || *value <= 0.0)
            || self.boundaries_eV.windows(2).any(|pair| pair[1] <= pair[0])
        {
            return Err(
                "spallation table boundaries_eV must be finite, positive and strictly ascending"
                    .into(),
            );
        }
        if self.targets.is_empty() {
            return Err("spallation table has no targets".into());
        }
        let groups = self.groups();
        let mut seen_targets = BTreeSet::new();
        for target in &self.targets {
            let name = format!("target ZA={}/LISO={}", target.za, target.liso);
            if target.za < 1001 || target.liso < 0 {
                return Err(format!(
                    "{name}: ZA must be at least 1001 and LISO nonnegative"
                ));
            }
            if !seen_targets.insert((target.za, target.liso)) {
                return Err(format!("{name} appears twice"));
            }
            if target.channels.is_empty() {
                return Err(format!("{name} has no channels"));
            }
            let mut seen_mts = BTreeSet::new();
            for channel in &target.channels {
                let label = format!("{name} MT{}", channel.mt);
                if !(1..=999).contains(&channel.mt) {
                    return Err(format!("{label}: MT must be in 1..=999"));
                }
                if !seen_mts.insert(channel.mt) {
                    return Err(format!("{label} appears twice"));
                }
                if channel.products.is_empty() {
                    return Err(format!("{label} has no products"));
                }
                if let Some(loss) = &channel.loss_barns {
                    check_values(loss, groups, &format!("{label} loss_barns"))?;
                }
                let mut seen_products = BTreeSet::new();
                for product in &channel.products {
                    let label = format!("{label} product ZAP={}/LFS={}", product.zap, product.lfs);
                    if product.zap < 1 || product.lfs < 0 {
                        return Err(format!("{label}: ZAP must be positive and LFS nonnegative"));
                    }
                    if !seen_products.insert((product.zap, product.lfs)) {
                        return Err(format!("{label} appears twice"));
                    }
                    check_values(
                        &product.sigma_barns,
                        groups,
                        &format!("{label} sigma_barns"),
                    )?;
                }
            }
        }
        Ok(())
    }

    /// Index of the first library boundary at or above `splice_ev`, the lower edge of the first spliced group.
    /// The splice energy snaps up onto the library grid so no group is ever blended from two sources.
    pub fn effective_boundary_index(bounds: &[f64], splice_ev: f64) -> Result<usize, String> {
        if !splice_ev.is_finite() || splice_ev <= 0.0 {
            return Err("spallation splice energy must be finite and positive".into());
        }
        let index = bounds
            .partition_point(|bound| *bound < splice_ev * (1.0 - BOUNDARY_RELATIVE_TOLERANCE));
        if index == 0 {
            return Err(format!(
                "spallation splice energy {splice_ev} eV is at or below the library's lowest boundary {} eV; every group would be replaced",
                bounds[0]
            ));
        }
        if index + 1 >= bounds.len() {
            return Err(format!(
                "spallation splice energy {splice_ev} eV leaves no group above it; the library tops out at {} eV",
                bounds[bounds.len() - 1]
            ));
        }
        Ok(index)
    }

    /// Merge this table into `library` at and above `splice_ev` (snapped up to a group boundary).
    ///
    /// `target_keys[i]` is the `(ZA, LISO)` of library target `i`. Rows already present with the same
    /// `(target, mt, zap, lfs, lmf)` key have their spliced groups replaced; other table rows are appended after
    /// the target's existing rows with zero cross section below the splice. Table targets absent from the
    /// library are skipped and reported, as `USESPALLATION 0` skips isotopes without both files. Rows the table
    /// does not name keep every group value.
    pub fn apply(
        &self,
        library: &mut Library,
        target_keys: &[(i32, i32)],
        projectile: Projectile,
        splice_ev: f64,
    ) -> Result<SpliceReport, String> {
        self.validate()?;
        if Projectile::parse(&self.projectile)? != projectile {
            return Err(format!(
                "spallation table is for projectile '{}', the library is '{}'",
                self.projectile,
                projectile.name()
            ));
        }
        let ngroups = library.ngroups;
        if library.bounds.len() != ngroups + 1 || library.sig.len() != library.rows.len() * ngroups
        {
            return Err("spallation splice needs a structurally valid library".into());
        }
        let first_boundary = Self::effective_boundary_index(&library.bounds, splice_ev)?;
        let spliced_bounds = &library.bounds[first_boundary..];
        let aligned = spliced_bounds.len() == self.boundaries_eV.len()
            && spliced_bounds
                .iter()
                .zip(&self.boundaries_eV)
                .all(|(have, want)| {
                    (have - want).abs() <= BOUNDARY_RELATIVE_TOLERANCE * have.abs()
                });
        if !aligned {
            return Err(format!(
                "spallation table boundaries ({} values from {} to {} eV) are not the library's {} boundaries from the effective splice {} eV to {} eV",
                self.boundaries_eV.len(),
                self.boundaries_eV[0],
                self.boundaries_eV[self.boundaries_eV.len() - 1],
                spliced_bounds.len(),
                spliced_bounds[0],
                spliced_bounds[spliced_bounds.len() - 1]
            ));
        }
        let effective_splice_eV = library.bounds[first_boundary];

        let target_index: HashMap<(i32, i32), usize> = target_keys
            .iter()
            .enumerate()
            .map(|(index, key)| (*key, index))
            .collect();
        let mut rows_by_target: Vec<Vec<usize>> = vec![Vec::new(); target_keys.len()];
        for (row_index, row) in library.rows.iter().enumerate() {
            rows_by_target
                .get_mut(row.target)
                .ok_or_else(|| {
                    format!(
                        "library row {row_index} names unknown target {}",
                        row.target
                    )
                })?
                .push(row_index);
        }

        let mut report = SpliceReport {
            requested_splice_eV: splice_ev,
            effective_splice_eV,
            first_group: first_boundary,
            groups_spliced: ngroups - first_boundary,
            targets: Vec::new(),
            targets_not_in_library: Vec::new(),
            untouched_nonzero_rows: Vec::new(),
        };
        let mut replaced_rows: HashSet<usize> = HashSet::new();
        // Rows to append per library target, in table order.
        let mut additions: Vec<Vec<(Row, &[f64])>> = vec![Vec::new(); target_keys.len()];
        for table_target in &self.targets {
            let Some(&target) = target_index.get(&(table_target.za, table_target.liso)) else {
                report
                    .targets_not_in_library
                    .push((table_target.za, table_target.liso));
                continue;
            };
            let mut spliced = SplicedTarget {
                target,
                za: table_target.za,
                liso: table_target.liso,
                rows_replaced: 0,
                rows_added: 0,
            };
            for channel in &table_target.channels {
                let mut wanted: Vec<(Row, &[f64])> = Vec::new();
                if let Some(loss) = &channel.loss_barns {
                    let row = Row {
                        target,
                        mt: channel.mt,
                        zap: -1,
                        lfs: -1,
                        lmf: 0,
                    };
                    wanted.push((row, loss.as_slice()));
                }
                for product in &channel.products {
                    let row = Row {
                        target,
                        mt: channel.mt,
                        zap: product.zap,
                        lfs: product.lfs,
                        lmf: 10,
                    };
                    wanted.push((row, product.sigma_barns.as_slice()));
                }
                for (row, sigma) in wanted {
                    let existing: Vec<usize> = rows_by_target[target]
                        .iter()
                        .copied()
                        .filter(|&index| library.rows[index] == row)
                        .collect();
                    if existing.is_empty() {
                        additions[target].push((row, sigma));
                        spliced.rows_added += 1;
                    } else {
                        for index in existing {
                            replaced_rows.insert(index);
                            library.sig[index * ngroups + first_boundary..(index + 1) * ngroups]
                                .copy_from_slice(sigma);
                            spliced.rows_replaced += 1;
                        }
                    }
                }
            }
            for &index in &rows_by_target[target] {
                let row = library.rows[index];
                let spliced_values =
                    &library.sig[index * ngroups + first_boundary..(index + 1) * ngroups];
                if !replaced_rows.contains(&index)
                    && spliced_values.iter().any(|value| *value != 0.0)
                {
                    report.untouched_nonzero_rows.push(UntouchedRow {
                        za: table_target.za,
                        liso: table_target.liso,
                        mt: row.mt,
                        zap: row.zap,
                        lfs: row.lfs,
                        lmf: row.lmf,
                    });
                }
            }
            report.targets.push(spliced);
        }

        if additions.iter().any(|rows| !rows.is_empty()) {
            let mut last_row_of_target: HashMap<usize, usize> = HashMap::new();
            for (target, rows) in rows_by_target.iter().enumerate() {
                if let Some(&last) = rows.last() {
                    last_row_of_target.insert(last, target);
                }
            }
            let added: usize = additions.iter().map(Vec::len).sum();
            let mut rows = Vec::with_capacity(library.rows.len() + added);
            let mut sig = Vec::with_capacity(library.sig.len() + added * ngroups);
            let emit = |rows: &mut Vec<Row>, sig: &mut Vec<f64>, pending: &[(Row, &[f64])]| {
                for (row, sigma) in pending {
                    rows.push(*row);
                    sig.resize(sig.len() + first_boundary, 0.0);
                    sig.extend_from_slice(sigma);
                }
            };
            for (index, row) in library.rows.iter().enumerate() {
                rows.push(*row);
                sig.extend_from_slice(&library.sig[index * ngroups..(index + 1) * ngroups]);
                if let Some(&target) = last_row_of_target.get(&index) {
                    emit(&mut rows, &mut sig, additions[target].as_slice());
                }
            }
            // Targets with no existing rows take their additions at the end, in target order.
            for (target, pending) in additions.iter().enumerate() {
                if rows_by_target[target].is_empty() {
                    emit(&mut rows, &mut sig, pending.as_slice());
                }
            }
            library.rows = rows;
            library.sig = sig;
        }
        Ok(report)
    }
}

/// What a splice changed in one library target.
#[derive(Clone, Debug, PartialEq)]
pub struct SplicedTarget {
    pub target: usize,
    pub za: i32,
    pub liso: i32,
    pub rows_replaced: usize,
    pub rows_added: usize,
}

#[derive(Clone, Debug, PartialEq)]
pub struct SpliceReport {
    pub requested_splice_eV: f64,
    pub effective_splice_eV: f64,
    /// Index of the first spliced group; groups below it are untouched.
    pub first_group: usize,
    pub groups_spliced: usize,
    pub targets: Vec<SplicedTarget>,
    pub targets_not_in_library: Vec<(i32, i32)>,
    /// Rows of spliced targets that the splice left alone although they are nonzero in a spliced group.
    pub untouched_nonzero_rows: Vec<UntouchedRow>,
}

/// A library row the splice did not replace that still carries cross section above the splice.
#[derive(Clone, Debug, PartialEq)]
pub struct UntouchedRow {
    pub za: i32,
    pub liso: i32,
    pub mt: i32,
    pub zap: i32,
    pub lfs: i32,
    pub lmf: i32,
}

/// Rows listed by name in the index `spallation` block; the count covers all of them.
const UNTOUCHED_ROWS_LISTED: usize = 50;

impl SpliceReport {
    /// The `spallation` block of the library index.
    pub fn provenance(&self, splice: &SpallationSplice) -> serde_json::Value {
        let rows_replaced: usize = self.targets.iter().map(|t| t.rows_replaced).sum();
        let rows_added: usize = self.targets.iter().map(|t| t.rows_added).sum();
        let targets: Vec<serde_json::Value> = self
            .targets
            .iter()
            .map(|t| {
                serde_json::json!({
                    "za": t.za,
                    "liso": t.liso,
                    "rows_replaced": t.rows_replaced,
                    "rows_added": t.rows_added,
                })
            })
            .collect();
        let absent: Vec<serde_json::Value> = self
            .targets_not_in_library
            .iter()
            .map(|(za, liso)| serde_json::json!({"za": za, "liso": liso}))
            .collect();
        let untouched: Vec<serde_json::Value> = self
            .untouched_nonzero_rows
            .iter()
            .take(UNTOUCHED_ROWS_LISTED)
            .map(|row| {
                serde_json::json!({
                    "za": row.za,
                    "liso": row.liso,
                    "mt": row.mt,
                    "zap": row.zap,
                    "lfs": row.lfs,
                    "lmf": row.lmf,
                })
            })
            .collect();
        serde_json::json!({
            "schema": SPALLATION_SCHEMA,
            "table_path": splice.source_path,
            "table_sha256": splice.source_sha256,
            "table_description": splice.table.description,
            "table_projectile": splice.table.projectile,
            "requested_splice_eV": self.requested_splice_eV,
            "effective_splice_eV": self.effective_splice_eV,
            "first_spliced_group": self.first_group,
            "groups_spliced": self.groups_spliced,
            "rows_replaced": rows_replaced,
            "rows_added": rows_added,
            "targets": targets,
            "targets_not_in_library": absent,
            "untouched_rows_nonzero_in_spliced_groups": {
                "count": self.untouched_nonzero_rows.len(),
                "rows": untouched,
                "note": "library rows of spliced targets the table did not replace but that are nonzero at or above the splice; a production row of a channel the table also supplies is then counted twice",
            },
        })
    }
}

/// A table loaded for a build, with the splice energy the user chose and the identity of the table file.
#[derive(Clone, Debug)]
pub struct SpallationSplice {
    pub table: SpallationTable,
    pub splice_ev: f64,
    pub source_path: String,
    pub source_sha256: String,
}

impl SpallationSplice {
    pub fn from_file(path: impl AsRef<Path>, splice_ev: f64) -> Result<Self, String> {
        let path = path.as_ref();
        if !splice_ev.is_finite() || splice_ev <= 0.0 {
            return Err("spallation splice energy must be finite and positive".into());
        }
        let bytes = std::fs::read(path)
            .map_err(|error| format!("cannot read spallation table {}: {error}", path.display()))?;
        let text = std::str::from_utf8(&bytes).map_err(|error| {
            format!("spallation table {} is not UTF-8: {error}", path.display())
        })?;
        let table = SpallationTable::from_json(text)
            .map_err(|error| format!("{}: {error}", path.display()))?;
        Ok(Self {
            table,
            splice_ev,
            source_path: path.display().to_string(),
            source_sha256: format!("{:x}", Sha256::digest(&bytes)),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Six groups: boundaries 1, 10, 100, 1e3, 1e4, 1e5, 1e6 eV. Spliced groups are 3..6.
    fn library() -> (Library, Vec<(i32, i32)>) {
        let rows = vec![
            Row {
                target: 0,
                mt: 5,
                zap: -1,
                lfs: -1,
                lmf: 0,
            },
            Row {
                target: 0,
                mt: 5,
                zap: 80200,
                lfs: 0,
                lmf: 10,
            },
            Row {
                target: 0,
                mt: 102,
                zap: 82209,
                lfs: 0,
                lmf: -1,
            },
            Row {
                target: 1,
                mt: 102,
                zap: 82207,
                lfs: 0,
                lmf: -1,
            },
        ];
        let mut sig = Vec::new();
        for row in 0..rows.len() {
            for group in 0..6 {
                sig.push(1.0 + row as f64 + 0.1 * group as f64);
            }
        }
        let library = Library {
            rows,
            sig,
            ngroups: 6,
            bounds: vec![1.0, 10.0, 1e2, 1e3, 1e4, 1e5, 1e6],
        };
        (library, vec![(82208, 0), (82206, 0)])
    }

    fn table_json() -> serde_json::Value {
        serde_json::json!({
            "schema": SPALLATION_SCHEMA,
            "description": "synthetic unit-test data",
            "projectile": "neutron",
            "boundaries_eV": [1e3, 1e4, 1e5, 1e6],
            "targets": [
                {"za": 82208, "liso": 0, "channels": [
                    {"mt": 5,
                     "loss_barns": [10.0, 20.0, 30.0],
                     "products": [
                        {"zap": 80200, "sigma_barns": [1.0, 2.0, 3.0]},
                        {"zap": 79197, "lfs": 0, "sigma_barns": [4.0, 5.0, 6.0]}
                     ]}
                ]},
                {"za": 26056, "liso": 0, "channels": [
                    {"mt": 5, "products": [{"zap": 25054, "sigma_barns": [1.0, 1.0, 1.0]}]}
                ]}
            ]
        })
    }

    fn parse(value: &serde_json::Value) -> Result<SpallationTable, String> {
        SpallationTable::from_json(&value.to_string())
    }

    #[test]
    fn splice_replaces_above_and_leaves_below_untouched() {
        let (mut library, keys) = library();
        let before = library.sig.clone();
        let table = parse(&table_json()).unwrap();
        let report = table
            .apply(&mut library, &keys, Projectile::Neutron, 1e3)
            .unwrap();
        assert_eq!(report.first_group, 3);
        assert_eq!(report.groups_spliced, 3);
        assert_eq!(report.effective_splice_eV, 1e3);
        assert_eq!(report.targets_not_in_library, vec![(26056, 0)]);
        assert_eq!(report.targets.len(), 1);
        assert_eq!(
            (
                report.targets[0].rows_replaced,
                report.targets[0].rows_added
            ),
            (2, 1)
        );

        // Row 0 is the MT5 loss row, row 1 the existing 80200 product: groups 0..3 identical, 3..6 replaced.
        assert_eq!(&library.sigma(0)[..3], &before[..3]);
        assert_eq!(&library.sigma(0)[3..], &[10.0, 20.0, 30.0]);
        assert_eq!(&library.sigma(1)[..3], &before[6..9]);
        assert_eq!(&library.sigma(1)[3..], &[1.0, 2.0, 3.0]);
        // The unrelated MT102 row of the same target is unchanged.
        assert_eq!(library.sigma(2), &before[12..18]);
        // The new 79197 product sits right after the target's last existing row, zero below the splice.
        assert_eq!(library.rows.len(), 5);
        assert_eq!(
            library.rows[3],
            Row {
                target: 0,
                mt: 5,
                zap: 79197,
                lfs: 0,
                lmf: 10
            }
        );
        assert_eq!(library.sigma(3), &[0.0, 0.0, 0.0, 4.0, 5.0, 6.0]);
        assert_eq!(library.rows[4].target, 1);
        assert_eq!(library.sigma(4), &before[18..24]);
        library.validate().unwrap();
    }

    #[test]
    fn splice_energy_snaps_up_to_a_group_boundary() {
        let (mut library, keys) = library();
        let table = parse(&table_json()).unwrap();
        // 500 eV lies inside group 2 (100..1000), so the splice snaps up to 1000 eV.
        let report = table
            .apply(&mut library, &keys, Projectile::Neutron, 500.0)
            .unwrap();
        assert_eq!(report.requested_splice_eV, 500.0);
        assert_eq!(report.effective_splice_eV, 1e3);
        assert_eq!(report.first_group, 3);
    }

    #[test]
    fn splice_rejects_a_mismatched_grid_or_projectile() {
        let (library, keys) = library();
        // The table must be on the library grid from the effective splice up.
        let mut off_grid = table_json();
        off_grid["boundaries_eV"] = serde_json::json!([1e3, 5e3, 1e5, 1e6]);
        let error = parse(&off_grid)
            .unwrap()
            .apply(&mut library.clone(), &keys, Projectile::Neutron, 1e3)
            .unwrap_err();
        assert!(error.contains("not the library's"), "{error}");
        // Splicing at 1e4 needs a two-group table, not the three-group one.
        let error = parse(&table_json())
            .unwrap()
            .apply(&mut library.clone(), &keys, Projectile::Neutron, 1e4)
            .unwrap_err();
        assert!(error.contains("not the library's"), "{error}");
        // A proton table does not splice into a neutron library.
        let mut proton = table_json();
        proton["projectile"] = serde_json::json!("proton");
        let error = parse(&proton)
            .unwrap()
            .apply(&mut library.clone(), &keys, Projectile::Neutron, 1e3)
            .unwrap_err();
        assert!(error.contains("projectile"), "{error}");
    }

    #[test]
    fn splice_energy_must_leave_groups_on_both_sides() {
        let bounds = [1.0, 10.0, 100.0];
        assert_eq!(
            SpallationTable::effective_boundary_index(&bounds, 10.0),
            Ok(1)
        );
        assert!(SpallationTable::effective_boundary_index(&bounds, 100.0).is_err());
        assert!(SpallationTable::effective_boundary_index(&bounds, 1e9).is_err());
        assert!(SpallationTable::effective_boundary_index(&bounds, 0.5).is_err());
        assert!(SpallationTable::effective_boundary_index(&bounds, f64::NAN).is_err());
        assert!(SpallationTable::effective_boundary_index(&bounds, -1.0).is_err());
    }

    #[test]
    fn targets_with_no_rows_take_their_additions_at_the_end() {
        let (mut library, _) = library();
        let keys = vec![(82208, 0), (82206, 0), (82204, 0)];
        let mut value = table_json();
        value["targets"] = serde_json::json!([
            {"za": 82204, "liso": 0, "channels": [
                {"mt": 5, "products": [{"zap": 80200, "sigma_barns": [7.0, 8.0, 9.0]}]}
            ]}
        ]);
        let report = parse(&value)
            .unwrap()
            .apply(&mut library, &keys, Projectile::Neutron, 1e3)
            .unwrap();
        assert_eq!(report.targets.len(), 1);
        assert_eq!(library.rows.len(), 5);
        assert_eq!(library.rows[4].target, 2);
        assert_eq!(library.sigma(4), &[0.0, 0.0, 0.0, 7.0, 8.0, 9.0]);
        library.validate().unwrap();
    }

    #[test]
    fn splice_without_overlap_leaves_the_library_byte_identical() {
        let (mut library, keys) = library();
        let before = (library.rows.clone(), library.sig.clone());
        let mut value = table_json();
        value["targets"] = serde_json::json!([
            {"za": 26056, "liso": 0, "channels": [
                {"mt": 5, "products": [{"zap": 25054, "sigma_barns": [1.0, 1.0, 1.0]}]}
            ]}
        ]);
        let report = parse(&value)
            .unwrap()
            .apply(&mut library, &keys, Projectile::Neutron, 1e3)
            .unwrap();
        assert!(report.targets.is_empty());
        assert_eq!((library.rows, library.sig), before);
    }

    #[test]
    fn provenance_records_table_identity_and_changes() {
        let (mut library, keys) = library();
        let table = parse(&table_json()).unwrap();
        let report = table
            .apply(&mut library, &keys, Projectile::Neutron, 1e3)
            .unwrap();
        let splice = SpallationSplice {
            table,
            splice_ev: 1e3,
            source_path: "table.json".into(),
            source_sha256: "ab".repeat(32),
        };
        let provenance = report.provenance(&splice);
        assert_eq!(provenance["schema"], SPALLATION_SCHEMA);
        assert_eq!(provenance["table_sha256"], "ab".repeat(32));
        assert_eq!(provenance["table_description"], "synthetic unit-test data");
        assert_eq!(provenance["effective_splice_eV"], 1e3);
        assert_eq!(provenance["groups_spliced"], 3);
        assert_eq!(provenance["rows_replaced"], 2);
        assert_eq!(provenance["rows_added"], 1);
        assert_eq!(provenance["targets"][0]["za"], 82208);
        assert_eq!(provenance["targets_not_in_library"][0]["za"], 26056);
    }

    #[test]
    fn table_validation_rejects_malformed_tables() {
        let reject = |edit: &dyn Fn(&mut serde_json::Value), needle: &str| {
            let mut value = table_json();
            edit(&mut value);
            let error = parse(&value).unwrap_err();
            assert!(error.contains(needle), "{needle}: {error}");
        };
        reject(&|v| v["schema"] = serde_json::json!("other"), "schema");
        reject(
            &|v| v["description"] = serde_json::json!("  "),
            "description",
        );
        reject(&|v| v["projectile"] = serde_json::json!("muon"), "muon");
        reject(
            &|v| v["boundaries_eV"] = serde_json::json!([1e3]),
            "at least two",
        );
        reject(
            &|v| v["boundaries_eV"] = serde_json::json!([1e3, 1e3, 1e5, 1e6]),
            "strictly ascending",
        );
        reject(
            &|v| v["boundaries_eV"] = serde_json::json!([1e6, 1e5, 1e4, 1e3]),
            "strictly ascending",
        );
        reject(&|v| v["targets"] = serde_json::json!([]), "no targets");
        reject(
            &|v| {
                v["targets"][0]["channels"][0]["products"][0]["sigma_barns"] =
                    serde_json::json!([1.0, 2.0])
            },
            "has 2 values",
        );
        reject(
            &|v| {
                v["targets"][0]["channels"][0]["products"][0]["sigma_barns"] =
                    serde_json::json!([1.0, -2.0, 3.0])
            },
            "nonnegative",
        );
        reject(
            &|v| v["targets"][0]["channels"][0]["loss_barns"] = serde_json::json!([1.0, 2.0]),
            "loss_barns",
        );
        reject(
            &|v| v["targets"][0]["channels"][0]["mt"] = serde_json::json!(0),
            "MT must",
        );
        reject(
            &|v| v["targets"][0]["channels"][0]["products"][0]["zap"] = serde_json::json!(0),
            "ZAP must be positive",
        );
        reject(
            &|v| v["targets"][0]["channels"][0]["products"][1]["zap"] = serde_json::json!(80200),
            "appears twice",
        );
        reject(
            &|v| v["targets"][1]["za"] = serde_json::json!(82208),
            "appears twice",
        );
        reject(
            &|v| v["targets"][0]["channels"] = serde_json::json!([]),
            "no channels",
        );
        // Misspelt fields are an error, never silently ignored.
        reject(
            &|v| v["splice_eV"] = serde_json::json!(2e8),
            "unknown field",
        );
        // Values must be finite: serde_json rejects NaN, so check a non-finite spelling at the type level.
        assert!(check_values(&[f64::INFINITY], 1, "x").is_err());
        assert!(check_values(&[f64::NAN], 1, "x").is_err());
    }

    #[test]
    fn from_file_hashes_the_table_bytes_and_validates_the_splice_energy() {
        let directory = std::env::temp_dir().join(format!(
            "actinv-spallation-{}-{}",
            std::process::id(),
            "from_file"
        ));
        std::fs::create_dir_all(&directory).unwrap();
        let path = directory.join("table.json");
        let text = table_json().to_string();
        std::fs::write(&path, &text).unwrap();
        let splice = SpallationSplice::from_file(&path, DEFAULT_SPLICE_EV).unwrap();
        assert_eq!(
            splice.source_sha256,
            format!("{:x}", Sha256::digest(text.as_bytes()))
        );
        assert_eq!(splice.splice_ev, 2.0e8);
        assert_eq!(splice.table.groups(), 3);
        assert!(SpallationSplice::from_file(&path, 0.0).is_err());
        assert!(SpallationSplice::from_file(directory.join("missing.json"), 1e3).is_err());
        std::fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn p140_sg06_splice_reports_library_rows_left_untouched_but_nonzero_above_the_splice() {
        // A residual-arithmetic production row (lmf -1) shares (mt, zap, lfs) with the table's lmf=10
        // row, so the splice replaces the loss row, leaves this row alone and appends the table's:
        // the production is counted twice. The report has to name such rows.
        let rows = vec![
            Row {
                target: 0,
                mt: 5,
                zap: -1,
                lfs: -1,
                lmf: 0,
            },
            Row {
                target: 0,
                mt: 5,
                zap: 80200,
                lfs: 0,
                lmf: -1,
            },
            // Nonzero below the splice only: not a double count.
            Row {
                target: 0,
                mt: 102,
                zap: 82209,
                lfs: 0,
                lmf: -1,
            },
        ];
        let mut sig = vec![1.0; 12];
        sig.extend([1.0, 1.0, 1.0, 0.0, 0.0, 0.0]);
        let mut library = Library {
            rows,
            sig,
            ngroups: 6,
            bounds: vec![1.0, 10.0, 1e2, 1e3, 1e4, 1e5, 1e6],
        };
        let table = parse(&serde_json::json!({
            "schema": SPALLATION_SCHEMA,
            "description": "synthetic unit-test data",
            "projectile": "neutron",
            "boundaries_eV": [1e3, 1e4, 1e5, 1e6],
            "targets": [{"za": 82208, "liso": 0, "channels": [
                {"mt": 5,
                 "loss_barns": [10.0, 20.0, 30.0],
                 "products": [{"zap": 80200, "sigma_barns": [1.0, 2.0, 3.0]}]}
            ]}]
        }))
        .unwrap();
        let report = table
            .apply(&mut library, &[(82208, 0)], Projectile::Neutron, 1e3)
            .unwrap();
        // One loss row replaced, one product row appended; the lmf=-1 product row is untouched.
        assert_eq!(library.rows.len(), 4);
        let splice = SpallationSplice {
            table,
            splice_ev: 1e3,
            source_path: "table.json".into(),
            source_sha256: "0".repeat(64),
        };
        let block = report.provenance(&splice);
        let untouched = &block["untouched_rows_nonzero_in_spliced_groups"];
        assert_eq!(
            untouched["count"], 1,
            "provenance lacks untouched_rows_nonzero_in_spliced_groups: {block}"
        );
        assert_eq!(untouched["rows"][0]["mt"], 5);
        assert_eq!(untouched["rows"][0]["zap"], 80200);
    }
}
