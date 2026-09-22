# Fusion Isotope Benchmark 001 — reduced-chain verification

Frozen 2026-09-18 before executing the reduced-chain calculation. Extends
G2 of the opening protocol; preserves the opening receipt and G3's unmatched
status. This is a deliberately reduced, prescribed-rate core-solver case,
not an OpenMC calculation, full ACTINV CLI/data-pipeline test, or experimental
validation. The original external-input G2/G3 scope remains pending.

## Inputs and model

Seal `examples/fusion_medical_isotopes/ac225/reduced_case.json` before execution.
Use Table 2's 275 kg Th-230, explicitly choosing it over interpreting the
rounded 27% label as exactly 270 kg. Use 230 g/mol as a declared mass-number
approximation and the exact SI Avogadro constant. Only this feed is tracked;
the remaining target mass is not an active species in this reduced model.

Initial product and daughter inventories are zero. Set R0 = 1.2 * 10000 *
3e14 * 0.065 atoms/s from the opening reference, without another factor 0.5.
Set k = R0/N230(0). This uses the published transport yield as an input; it
is neither an independent cross-section evaluation nor a recovered scalar
flux. Hold k fixed during irradiation while allowing feed depletion:

```
dN230/dt = -k N230
dN229/dt = k N230 - lambda229 N229
dNRa/dt  = lambda229 N229 - lambdaRa NRa
dNAc/dt  = lambdaRa NRa - lambdaAc NAc
dNsink/dt = lambdaAc NAc
```

All modeled transfers have unit branching. The sink counts heavy lineages
leaving Ac-225, not a resolved daughter inventory or total particle count.
Use Th-229's paper half-life (7916 Julian years); Ra-225 (14.9 days) and
Ac-225 (9.920 days) use the explicitly cited ENSDF evaluation. Ignore natural
Th-230 decay, other neutron reactions, neutron destruction of products,
spectral/geometry feedback, and harvesting. These are omissions, not claims
that the rates vanish in the physical target. No parameter fitting or sweep.

## Execution and independent control

Use the existing `cram_probe` executable, one repetition, with the repository's
CRAM-16 coefficients. Construct a five-state conservative matrix in Python;
ACTINV performs its sparse factorization and CRAM evolution. No new rate
adapter or core changes. Child processes have a 60-second timeout; Python's
`subprocess.run` kills and reaps this child on timeout. The probe spawns no
children. Use fresh temporary input/output files, never reuse old outputs.

Evaluate irradiation from the initial state at 1 day, 30 days, and 1, 3, 5,
10, 15 Julian years. For a separate one-year irradiation history, turn k to
exactly zero and evaluate cooling after 1, 7, 30, 100, 365 days, retaining
all products without chemical separation. Also compare one full irradiation
step with two half steps at each irradiation time.

The independent control is the closed-form Bateman sum, evaluated using
80-digit Decimal exponentials. It uses neither CRAM coefficients nor sparse
matrix assembly. For each initial species j and downstream species i,

```
N_i(t) += N_j(0) * product(b_j ... b_(i-1))
          * sum_m(exp(-a_m*t) / product_(q != m)(a_q-a_m))
```

Here a is the loss rate and b the next-species transfer rate. Skip paths
with zero transfer; the relevant nonzero paths have distinct loss rates.
Cooling references start from the analytic one-year state, not ACTINV's
state, so irradiation error cannot silently enter both answers.

Predeclare numerical acceptance: each state error <= 1e6 atoms + 1e-9 times
the absolute analytic state. Apply the same bound to full versus split steps.
Conservation error <= 1e-10 of initial feed; states must be finite and no
state may be below -1e6 atoms. Report maximum normalized errors. These are
numerical tolerances only, not physical uncertainties or paper-agreement gates.

## Publication comparison and evidence

Table 6 supplies Th-229 inventory checkpoints at 1, 3, 5, 10, 15 years:
532, 1530, 2460, 4460, 6060 Ci. They are inventories, not annual increments.
Report signed Ci and relative residuals without a pass/fail agreement
threshold. Never infer missing reaction rates from those five values. Daughter
predictions have no matched published comparison in this case.

Record input/protocol/source/probe/coefficient hashes, solver output, analytic
values, numerical verdict, cooling results, and publication residuals in a
new receipt. Do not replace earlier receipts or claim `reproduced`.
Generate a readable comparison table and standalone SVG plot. The plot is a
reduced-model comparison, not a validation plot; lines merely join samples.
CI must rerun the compiled probe, analytical control, and evidence checks.
Regression tests exercise analytic identities, incorrect transfer/normalization,
non-finite output, false claims, and timeout propagation.
