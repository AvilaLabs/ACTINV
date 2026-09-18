# ACTINV Fusion Isotope Benchmark 001

Thorium-229 generator production for actinium-225.

**Status: opening audit; the published result has not been reproduced with
ACTINV.** `reference.json` is literature metadata, not an `actinv-spec-1`
input. It deliberately cannot be mistaken for a runnable inventory problem.

The selected reference is the 27% thorium slab in Table 2 of
[Parisi and Rutkowski, arXiv:2609.19166v1](https://arxiv.org/html/2609.19166v1).
The direct production reaction is Th-230(n,2n)Th-229. Ac-225 is subsequently
supplied through the Th-229 decay chain. This case is distinct from the
[2025 fusion-isotope economic analysis](https://arxiv.org/abs/2512.09242).

## What can be checked now

The standard-library-only control recomputes the conversion from a published
per-neutron yield to gross annual parent activity. It needs no ACTINV binary,
OpenMC installation, network connection, or nuclear-data library:

```sh
systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 -p TasksMax=128 -p CPUQuota=200% -- env CARGO_BUILD_JOBS=1 RUST_TEST_THREADS=1 RAYON_NUM_THREADS=2 python3 controls/check_fusion_isotope_001.py
```

Run from the repository root. On CI, the control runs directly on the hosted
runner. `--write` creates the opening receipt and refuses to overwrite it.
The companion `controls/test_fusion_isotope_001.py` exercises independent
dimensional identities and rejection of corrupted evidence.

For source rate S, per-source-neutron production y, and decay constant lambda:

```
S = area_m2 * 10000 * emitted_neutrons_per_cm2_s
R = S * y                              [atoms/s]
gross_atoms = R * seconds_per_year
gross_activity = lambda * gross_atoms  [Bq]
activity_with_parent_decay = R * (1 - exp(-lambda * seconds_per_year))
```

The source yield already includes target interception. Multiplying by 0.5
again would undercount it. This calculation does not determine y, target
depletion, product neutron losses, daughter yields, or recoverable Ac-225.
The signed difference from the printed table is reported as an unresolved
arithmetic residual, not an ACTINV error or a fitted correction.

## Requirements for an inventory reproduction

The [frozen opening protocol](../../../protocols/FUSION-ISOTOPE-001.md)
defines the gates; `reference.json` names missing inputs. The reviewed paper
identifies ENDF/B-VIII.0 for the main reaction but does not provide a complete
hash-pinned calculation package. Its data statement promises a repository
upon publication; no calculation repository is linked in the reviewed HTML.
This is a limited source audit, not proof that no author data exist elsewhere.

Obtain and hash the original transport model, target spectra/rates, evaluated
files, decay data, and time histories before sealing G2/G3. Keep bulk inputs
outside Git and record download provenance, licenses, hashes, and conversion
commands. Resolve the composition and source normalization explicitly.

ACTINV's default TENDL bundle is not an identical-data substitute for this
reference. Its OpenMC importer accepts documented tally subsets, so the
actual transport handoff must be checked against `docs/SPEC.md`. A prescribed
rate chain needs an independent Bateman control before full comparison.
Any rate-input adapter must be reviewed separately; no adapter is assumed here.

After those gates, compare parent and daughter histories before adding
harvesting, impurities, competing products, or alternate libraries. Retain
separate statements for numerical verification, literature reproduction,
and experimental validation.
