# P18 G0 evidence seal

P18's acceptance evidence was partitioned before any still-eligible dependent value was read. The seal is
`results/p18_family_seal.json`; its canonical SHA-256 is
`3c4de15c94fbc39de279fda6a33e68e27dad89626f57172055df90113e81e94b`. It contains reaction identities, source
line IDs, incident energies, measurement forms, EXFOR entry IDs and source flags. It contains no measured cross
section, isomeric ratio or dependent uncertainty.

## What the structural pass found

The Rodrigo et al. paper states 962 reaction families and 12,313 data points. A naive scan found 963 `R` characters
in fixed column 20 because the preamble word `Reference:` happens to place its `R` there. The bounded parser does not
recognize records before the first `999999999999999999` delimiter. After that delimiter there are exactly 962 `R`
records, 962 `H` records, 12,313 `D` records and 963 delimiters, reconciling the publication exactly.

The family-level split is deterministic from the frozen string `ACTINV-P18-HOLDOUT-v1`, projectile and canonical
family ID:

| projectile | ranked eligible families | diagnostic | held out |
|---|---:|---:|---:|
| neutron | 170 | 128 | 42 |
| proton | 356 | 267 | 89 |
| deuteron | 83 | 63 | 20 |
| alpha | 118 | 89 | 29 |

Across the entire source, 561 families and 6,600 rows are diagnostic, 180 families and 1,945 rows are held out, and
221 families and 3,768 rows are structurally ineligible because the projectile is unsupported or the target is a
natural-element mixture. Later mapping, domain and value predicates may mark a sealed row unscored, but no family can
move between diagnostic and held-out partitions.

Amendment 1 permanently quarantines the five families displayed in physical lines 1--140. Two are unsupported gamma
families and three are supported neutron families. Nine target/product pairs already exposed by P17 and both paper
case studies are also diagnostic before ranking. The independent checker requires those exact assignments and rejects
an added, removed or relabeled quarantine.

## Provenance and controls

The external G0 pass freshly streamed and matched SHA-256 for 10.5 GB of four TENDL-2025 archives, their four staging
manifests, all four released activation libraries, both released decay payloads, the pinned ENDF-6 manual, the paper,
the supplement, protocol and amendment. It also live-verified successful P17 closure workflow `33232228355` and proved
that no production path changed from the P18 opening source.

The metadata parser decodes only fixed identity columns, incident energy, measurement type, EXFOR ID and source flags.
A planted secret in the dependent column span never appears in output. Additional plants cover a leading-space `D`,
the preamble `R`, duplicate/malformed families and input reordering. `controls/check_g0_p18.py` independently rederives
every row ID, family ID, forced-diagnostic assignment, stratum rank and count without importing the sealing control;
it rejects partition, dependent-field, quarantine and row-identity mutations.

Reproduction with the frozen external paths is:

```text
ulimit -v 12000000
python controls/g0_p18_seal.py --verify-github
python controls/check_g0_p18.py --no-write
```

Diagnostic dependent values remain unavailable to P18 work until this checkpoint's GitHub workflow passes. Held-out
dependent values remain unavailable until the separately required G4 unseal checkpoint is green.
