# Threshold correction sensitivity study — TCS-1

Frozen before calculation, 2026-09-14. Authorized by Connor Avila in this session.
This is a new, retrospective sensitivity study; it does not amend ACTINV P25/P25b
or qualify a production library. No experimental measurements are selected or scored.

## Inputs and intervention

Use the four SHA-256-pinned original neutron evaluations enumerated in ACTINV's
`controls/reproduce_tendl2025_thresholds.py` and primary threshold submission.
Reproduce the four explicit MF=3/MT=16 zero versus MF=10/MT=16 nonzero
ground-state contradictions before calculation. Verify negative Q, matching first
energies, and linear-linear interpolation. Abort on an unsupported interpolation law.
Create an in-memory variant replacing only the first ground-state MF=10 ordinate
with zero. Preserve every other source byte, energy, state, and cross section.
Record the original and variant full-file SHA-256 and the exact changed record.
Do not distribute the input files or change any installed data.

## Fixed calculations

For all four cases, integrate ground-state production and the unmodified MF=3
total over 0–20 MeV with (1) a uniform energy density, and (2) a Gaussian centered
at 14 MeV with standard deviation 0.5 MeV, normalized on 0–20 MeV. These are
illustrative spectra, not measured facility spectra. Set cross sections to zero
below the negative-Q threshold; require source coverage through 20 MeV.
Also report the first-interval area and the point value at 14.1 MeV.

Use analytic integration of linear segments as the primary method, and an
independent fixed-field parser plus segmentwise numerical quadrature as a check.
Integrate the removed triangular contribution directly to avoid cancellation in
tiny differences. Cross-check the flat-spectrum difference using exact decimal
triangle area. Require agreement within 1e-9 relative or 1e-13 b absolute for
folded original/corrected values; use 1e-9 relative for positive flat deltas.
For Gaussian deltas smaller than 1e-13 b, report absolute validation limits
explicitly rather than claiming resolved relative accuracy from subtraction.
Include analytic constant/linear controls and reject deliberately modified hashes.

Report spectrum-averaged cross sections, absolute and relative differences, and
production rates per target nucleus at total flux 1e14 cm^-2 s^-1. This is a
reaction-rate calculation; it makes no decay, depletion, target-composition,
activity, dose, or inventory prediction. A single-ordinate intervention is not
proof that the rest of a file is valid. The fixed 14.1 MeV comparison is a control,
not an independent experimental validation.

## Execution and record

Run one scientific job at a time under the ACTINV-required systemd scope:
MemoryMax=6G, MemorySwapMax=0, TasksMax=128, CPUQuota=200%. Verify actual cgroup
limits inside each job before work. Keep temporary output on disk. Record code,
protocol, source-evidence and result hashes. A failed check stays visible and
requires a dated amendment for any methodological change.

## Publication scope

The manuscript reports the four source contradictions and this bounded rate
sensitivity. Root cause and upstream fix status are attributed to the recorded
14 September 2026 private correspondence, not independently verified TALYS
source inspection. Broader historical census counts are not confirmed-defect
counts. The existing ACTINV v1.0.0 software manuscript is related work with a
different contribution; its benchmark results are not republished here.
