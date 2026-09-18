# ACTINV Fusion Isotope Benchmark 001

Thorium-229 generator production for actinium-225.

**Status: reduced-chain core-solver verification passed. The
published transport/inventory calculation has not been reproduced.**
`reference.json` is frozen literature metadata; `reduced_case.json` specifies
the separately frozen reduced model. Neither is an `actinv-spec-1` input.

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

## Reduced-chain verification

The [first amendment](../../../protocols/FUSION-ISOTOPE-001-AMENDMENT-1.md)
defines a bounded verification case using ACTINV's existing `cram_probe`.
It tests the real sparse/CRAM core against an independent, 80-digit
closed-form Bateman control. It does not exercise the CLI, evaluated-data
import, transport handoff, or automatic reaction/decay-chain assembly.

The five states are Th-230, Th-229, Ra-225, Ac-225, and an unresolved
downstream sink. The initial feed is Table 2's 275 kg of Th-230, using a
declared 230 g/mol mass approximation. Other target constituents are omitted.
The paper's source normalization and 0.065 atoms per emitted neutron yield
give an initial production rate R0. Set a fixed effective reaction rate
`k = R0 / N230(0)` and allow feed depletion. This is a prescribed rate
derived from the published yield, not an independently calculated cross
section or a substitute scalar flux.

The model includes Th-229/Ra-225/Ac-225 decay and stops at Ac-225 loss.
Natural Th-230 decay, competing reactions, product neutron destruction,
transport feedback, contaminants, and harvesting are explicitly omitted.
The 7916-year parent half-life comes from the paper; the 14.9-day and
9.920-day daughter half-lives come from the cited ENSDF evaluation. The
metadata includes source URLs and downloaded-byte hashes. No bulk data are
vendored and no input is fitted to the comparison checkpoints.

The control evaluates seven irradiation times, five cooling times after a
separate one-year irradiation, atom-lineage conservation, and full versus
two-half-step consistency. Zero-flux cooling retains all products; it is not
a chemical separation or harvesting model. Numerical tolerances were frozen
before execution and do not represent nuclear-data uncertainties.

To reproduce on the Linux workstation, run each command separately under
the root `AGENTS.md` limits, with no other build/solver job from this task:

```sh
mkdir -p target/preflight-tmp
systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 -p TasksMax=128 -p CPUQuota=200% -- env CARGO_BUILD_JOBS=1 RUST_TEST_THREADS=1 RAYON_NUM_THREADS=2 TMPDIR="$PWD/target/preflight-tmp" cargo build --release -p actinv-core --bin cram_probe
systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 -p TasksMax=128 -p CPUQuota=200% -- env CARGO_BUILD_JOBS=1 RUST_TEST_THREADS=1 RAYON_NUM_THREADS=2 TMPDIR="$PWD/target/preflight-tmp" python3 controls/fusion_isotope_chain.py
systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 -p TasksMax=128 -p CPUQuota=200% -- env CARGO_BUILD_JOBS=1 RUST_TEST_THREADS=1 RAYON_NUM_THREADS=2 TMPDIR="$PWD/target/preflight-tmp" python3 controls/test_fusion_isotope_chain.py
```

Verify the scope's cgroup limits read-only before starting jobs. A fresh
checkout needs Rust dependencies; the numerical control itself uses only
Python's standard library. Creating the plot with `--write` additionally
requires Matplotlib. `--write` refuses to replace an existing receipt.
GitHub's `fusion-isotope` workflow builds the probe and reruns verification.

## Published comparison and limitations

The [executed technical note](../../../results/fusion-isotope-001/reduced-chain-note.md),
[numerical receipt](../../../results/fusion-isotope-001/reduced-chain.json),
[comparison table](../../../results/fusion-isotope-001/comparison.md), and
[standalone plot](../../../results/fusion-isotope-001/comparison.svg) record
the result. The reduced model is 3.56% above the published one-year inventory
and 26.96% above the fifteen-year inventory. Numerical verification passes;
the literature reproduction remains incomplete.

Table 6 provides five usable Th-229 inventory checkpoints: 532, 1530, 2460,
4460, and 6060 Ci at 1, 3, 5, 10, and 15 years. They are inventory present,
not annual production. In particular, the 532 Ci one-year inventory and
Table 2's 542 Ci gross annual production are different observables.

The comparison reports signed residuals without an agreement threshold.
Increasing divergence could reflect omitted neutron destruction or other
assumptions, but the missing rates prevent assigning a cause. A reduced-model
match would also not establish that the original transport model was reproduced.
Ac-225 inventories here are model predictions, not recoverable yields or
published matched benchmarks.

## Requirements for a matched inventory reproduction

The [frozen opening protocol](../../../protocols/FUSION-ISOTOPE-001.md)
defines the gates; `reference.json` names missing inputs. The reviewed paper
identifies ENDF/B-VIII.0 for the main reaction but does not provide a complete
hash-pinned calculation package. Its data statement promises a repository
upon publication; no calculation repository is linked in the reviewed HTML.
The subsequent public search covered the arXiv source archive, GitHub, and
Zenodo. The source archive contains manuscript/figure files, not the numerical
model package. Related [gold-production scripts](https://github.com/jckica/prl-paper)
and [general neutronics tooling](https://github.com/jckica/neutronics_workflow)
are available, but no matching case package was located. This is not proof
that no author data exist elsewhere.

Obtain and hash the original transport model, target spectra/rates, evaluated
files, decay data, and full time histories before sealing the original
matched-data G2/G3. Table 6 corrects the earlier impression that no numerical
history points were available; it does not supply the missing model inputs.
Keep bulk inputs
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
