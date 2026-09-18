# FNS Iron 001 — executed application benchmark

The public-input ACTINV CLI workflow runs successfully and reproduces a
comparison with all twenty measured decay-heat points. **ACTINV predicts
systematically less heat than measured in this case.**

| Measure | Result |
| --- | ---: |
| Geometric-mean calculated/measured heat | 0.9263 |
| Pointwise C/E range | 0.8831–0.9623 |
| Underprediction range | 3.77–11.69% |
| Predictions inside the reported error bars | 5 / 20 |
| Cooling window | 1.10–57.02 minutes |

The largest difference is at 1.63 minutes. Agreement generally improves later
in this cooling window, but every prediction remains below the measured
value. The error bars are the source's reported magnitudes, not an assumed
95% interval or a combined measurement/model uncertainty band. This result
does not establish agreement within experimental uncertainty across the case.

## What was actually executed

Protocol and input metadata were frozen at commit `1c85f35` before fresh
execution. [GitHub Actions run 35391420589](https://github.com/AvilaLabs/ACTINV/actions/runs/35391420589)
built the CLI from branch head `fbbd99f` (PR merge snapshot
`d7dd76e41fb04e87cf14a68b5c2b36412866d45a`), downloaded and verified the
default data-v1.1.0 bundle and CoNDERC archive, ran the actual `actinv run`
command, and passed nine regression tests. The initial run 35390855929 had
stopped on an HTTP 403 before calculation; the corrected downloader identifies
itself explicitly and retains the same URL and data hashes.

The raw CLI output, generated spec, downloaded source members, and plot are
retained in the `fns-iron-evidence` artifact. The committed receipt was copied
unchanged from that successful run; it contains executable/input/output
identities and the complete solver certificate and ledger. All builds and
solver jobs for this example ran on hosted CI, not the occupied workstation.

ACTINV selected trace mode; its reported maximum feed burn-up fraction was
3.26e-12. The complete library's missing-product/isomer and target-data
diagnostics remain in the ledger. Such entries describe assembly/data
conditions and are not all realized populations in this pure-iron sample.
They are not silently cleared or attributed as the cause of the heat residual.

## Interpretation

This is stronger application evidence than a synthetic chain: material,
spectrum, irradiation, evaluated-data inputs, CLI ingestion, decay/inventory
calculation, units, and measured-output comparison are exercised together.
It is also a deliberately narrow, already-seen test, not new held-out evidence.

No input was fitted to improve C/E. The calculation uses the released Avila
Labs patched TENDL-2025 derivative and ENDF/B-VIII.0/JEFF-3.3 decay, rather
than the archive's TENDL-2017 reference setup. The current comparison therefore
does not isolate numerical, reaction-data, decay-data, or experimental causes.
The source's rounded-second input history was replaced only by the exact
printed measurement times, with that choice frozen before execution.

The outcome is a usable, reproducible case with an explicit 4–12% low bias in
this window. It supports inspecting the relevant reaction and decay-energy
contributions next; it does not justify adjusting flux to erase the bias or
extrapolating to other materials, cooling periods, dose, or component safety.

See the [full comparison table](comparison.md), [CSV](comparison.csv),
[receipt](comparison.json), [plot](comparison.svg), and
[run instructions](../../examples/fns_iron/README.md).
