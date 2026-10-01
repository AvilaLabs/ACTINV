# Known data limitations

Library coverage and evaluation defects can dominate an inventory calculation. A successful solve does not establish that every reaction or product is represented.

This page summarizes the shipped catalog **1.1.0**. The [detailed data disclosure](https://github.com/AvilaLabs/ACTINV/blob/master/docs/DATA_LIMITATIONS.md) preserves target lists, defect reports, and the underlying evidence.

## Full and patched neutron corpora

The default full TENDL-2025 neutron corpus covers 2,850 targets and carries known upstream emitted-state inconsistencies. The derived `tendl-2025-patched-neutron` corpus repairs only a confirmed contamination signature: 44 leading ordinates in 28 source files are zeroed, with other source values retained.

The patched build also rejects 1,172 source files with remaining unsupported or inconsistent data. Targets absent from the subset cannot contribute the corresponding evaluated reactions. That coverage loss was substantially worse on the recorded fast-spectrum FNS benchmark than retaining the full corpus's defects.

| Choice | Benefit | Limitation |
| --- | --- | --- |
| Full neutron corpus | Broader target coverage; default for the recorded FNS workflow | Retains known evaluation defects, including a thermal-energy contamination signature |
| Patched neutron subset | Removes that confirmed signature; recommended by the recorded data guidance for thermal/mixed-spectrum cases | Excludes many targets and retains other defect classes |

Check both material-target coverage and the affected reaction channels before deciding. A thermal-spectrum recommendation does not establish that the subset covers your material.

## Affected product-state data

The data disclosure identifies 17 affected IRDFF-II benchmark targets, including Ni-58, Nb-93, Ag-109, In-113, and Au-197. Their remaining blockers are not all covered by the bounded patch. Treat predictions involving those targets and affected isomeric production with the disclosed uncertainty and applicability limits.

ACTINV's current library builder validates product identities and emitted-state conservation. A new build can reject an evaluation that an older released artifact accepted. A parser fix or stricter builder does not retroactively repair an installed library.

## Other coverage boundaries

Products absent from the selected decay data remain leakage. Missing fission yields, photon spectra, covariance, response coefficients, and damage tables are reported separately. Complete-coverage switches apply to the feature that supplies them; they do not certify every input in the problem.

Keep the ledger with each result and read [Scope and qualification](qualification.md) when assessing the effect of missing data. The [data setup guide](data.md) lists correctly matched covariance bundles.
