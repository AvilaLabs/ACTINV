# TENDL-2025 defect and convention-hazard report

Findings from ACTINV phase P25 — the construction-coverage census of the held-out isomeric-scoring
population. Every quarantined evaluation was re-derived independently by an exact-decimal oracle that
compares MF=10 emitted state partials against the file's own declared MF=3 totals at declared and
intermediate energies. The classification, per-file evidence, repair record and acceptance verdict are
hash-pinned in the repository:

- `results/g1_p25_census.json` — per-file failure re-derivation and row-level outcome census
- `results/g2_p25_traces.json` — per-(MT, ZAP) decimal classification, mechanism kinds and detail strings
- `results/g4_p25_repairs.json` — bounded repair record (which mechanisms were reconciled, which files still fail closed)
- `results/g5_p25_acceptance.json` — acceptance scoring under the frozen gates
- `results/verdict_p25.json` — `P25-FAIL`; evidence sha256 digests included

## What is a defect here

A *genuine source inconsistency* is a contradiction among the file's own declared values: emitted state
partials exceeding the declared channel total at a declared gridpoint, or group-level partial cross
sections under a declared zero total. No interpolation rule, unit convention or downstream processing
legitimately resolves these; they fail closed in ACTINV's builder.

*Convention hazards* are constructs that look like violations to a naive audit but are consistent within
TENDL's own semantics: the 1e-20 barn printing floor, charged-particle MT=4 carrying a different-residual
product, and fission partials whose comparator is the `IZAP=-1` sentinel rather than MF=3/MT=18. These are
reported for downstream consumers, not as defects the evaluator must fix.

Classes A and B below are hard defects — declared-value contradictions. Class C files carry excesses
beyond any mechanism-consistent envelope (0.03 relative); they are probably defects but are flagged for
adjudication rather than asserted. Classes D and E are incompleteness/identity findings. The convention
hazards follow.

## Scale and disposition

| class | evaluations | ACTINV disposition |
|---|---:|---|
| A. gridpoint contradictions | 50 | fail closed; residual defects audit-ledgered |
| B. zero-total with partials | 45 | fail closed; audit-ledgered where sub-group |
| C. interp-looking, beyond envelope | 25 | fail closed; adjudication requested |
| D. missing MF=3 comparator (MT=18 fission) | 50 | sentinel/partial-sum comparator, ledgered |
| E. ELFS vs QM−QI conflict | 14 bounded, 2 beyond bound | ELFS precedence within 1 keV; else fail closed |
| floor convention (1e-20 b) | 395 files carry it; 227 failed on it alone | floor-aware reconciliation, ledgered |
| charged-particle MT=4 different-residual | 2,156 | projectile-aware dispatch; artifact-declared `emission_model` |

## Convention hazard: charged-particle MT=4

Every charged-particle evaluation with an MF=8/MT=4 section declares a *different-residual* product ZAP
(712 alpha + 757 deuteron + 687 proton files); all 1,081 neutron files declare same-nuclide. MT=4 in a
proton/deuteron/alpha file is a neutron-emission transmutation channel. Any consumer that applies the
neutron "(x,x′) same residual" convention to charged-particle files silently credits the wrong nuclide —
ACTINV did so before P25 and produced plausible totals with zero-valued isomer predictions.

## Convention hazard: the 1e-20 barn floor

TENDL prints `1e-20` barn as an effectively-zero floor on both MF=3 totals and MF=10 partials. Co-equal
floor states under a floor total produce up to 100% relative "excess" with no physical weight. A purely
relative conservation audit reads these as violations; 227 quarantined evaluations failed on nothing else.

## Per-file defect tables

### A. Emitted partials exceed the declared total at declared gridpoints (50 evaluations)

| projectile | evaluation | worst rel. excess | decimal-oracle detail |
|---|---|---|---|
| deuteron | `d-Y089.tendl` | 1.235e+05 | MT104/ZAP39089 @ 9.2956e+05 eV: total 0, states 1.791e-15 b; MT104/ZAP39089 @ declared 9.2956e+05 eV: rel 1.235e+05 |
| neutron | `n-Ag106m.tendl` | 1.252e+02 | MT4/ZAP47106 @ 1.4080e+00 eV: total 0, states 7.193e-14 b; MT4/ZAP47106 @ 9.3624e+00 eV: total 0, states 4.477e-07 b; MT4/ZAP47106 @ 1.1740e+05 eV (between gridpoints): rel 1.082e+ |
| neutron | `n-Au197.tendl` | 1.855e+01 | MT4/ZAP79197 @ 7.7747e+04 eV: total 0, states 6.984e-02 b; MT4/ZAP79197 @ 2.7016e+05 eV (between gridpoints): rel 7.860e-01; MT4/ZAP79197 @ 2.8043e+05 eV (between gridpoints): rel  |
| neutron | `n-Au198m.tendl` | 1.856e+01 | MT4/ZAP79198 @ 1.3339e+01 eV: total 0, states 1.449e-02 b; MT4/ZAP79198 @ 1.0000e+02 eV: total 0, states 4.670e-03 b; MT4/ZAP79198 @ 1.3906e+02 eV: total 0, states 4.067e-03 b; MT4 |
| neutron | `n-Br079.tendl` | 6.560e+06 | MT4/ZAP35079 @ 2.1026e+05 eV: total 0, states 2.456e-01 b; MT4/ZAP35079 @ declared 2.1026e+05 eV: rel 6.560e+06; MT4/ZAP35079 @ 2.1987e+05 eV (between gridpoints): rel 1.427e+02; M |
| neutron | `n-Cd106.tendl` | 4.273e+10 | MT32/ZAP47104 @ 1.5296e+07 eV: total 0, states 2.562e-05 b; MT32/ZAP47104 @ declared 1.5303e+07 eV: rel 4.273e+10 |
| neutron | `n-Cl035.tendl` | 7.918e+04 | MT16/ZAP17034 @ 1.3010e+07 eV: total 0, states 2.460e+01 b; MT16/ZAP17034 @ declared 1.3160e+07 eV: rel 7.918e+04 |
| neutron | `n-Er168.tendl` | 3.193e+03 | MT107/ZAP66165 @ declared 1.0000e-05 eV: rel 6.743e-01; MT107/ZAP66165 @ 1.5347e-05 eV (between gridpoints): rel 1.074e+00; MT107/ZAP66165 @ 1.9012e-05 eV (between gridpoints): rel |
| neutron | `n-Eu151.tendl` | 2.221e+01 | MT107/ZAP61148 @ declared 1.0000e-05 eV: rel 1.869e-01; MT107/ZAP61148 @ 1.3997e-05 eV (between gridpoints): rel 4.039e-01; MT107/ZAP61148 @ 1.6559e-05 eV (between gridpoints): rel |
| neutron | `n-Ge075m.tendl` | 6.981e+08 | MT4/ZAP32075 @ 5.3207e+04 eV (between gridpoints): rel 1.776e-01; MT4/ZAP32075 @ 6.1010e+04 eV (between gridpoints): rel 1.134e-02; MT4/ZAP32075 @ 1.1499e+05 eV (between gridpoints |
| neutron | `n-Hg195m.tendl` | 1.623e+15 | MT4/ZAP80195 @ 1.1649e+00 eV: total 0, states 2.255e-02 b; MT4/ZAP80195 @ 1.0000e+01 eV: total 0, states 7.585e-03 b; MT4/ZAP80195 @ 3.7352e+01 eV: total 0, states 5.984e-03 b; MT4 |
| neutron | `n-Hg197m.tendl` | 1.333e+12 | MT4/ZAP80197 @ 5.1421e+00 eV: total 0, states 3.904e-02 b; MT4/ZAP80197 @ 1.0000e+01 eV: total 0, states 2.762e-02 b; MT4/ZAP80197 @ 4.9405e+01 eV: total 0, states 1.906e-02 b; MT4 |
| neutron | `n-Ir193.tendl` | 1.855e+01 | MT4/ZAP77193 @ 7.3423e+04 eV: total 0, states 9.601e-02 b; MT4/ZAP77193 @ declared 8.0657e+04 eV: rel 1.963e+00; MT4/ZAP77193 @ 8.0658e+04 eV (between gridpoints): rel 1.963e+00; M |
| neutron | `n-Ir194m.tendl` | 1.301e+09 | MT4/ZAP77194 @ 3.2117e+00 eV: total 0, states 5.201e-04 b; MT4/ZAP77194 @ 1.0000e+01 eV: total 0, states 2.871e-04 b; MT4/ZAP77194 @ 1.0000e+02 eV: total 0, states 8.479e-05 b; MT4 |
| neutron | `n-K039.tendl` | 3.299e+16 | MT111/ZAP17038 @ 1.0788e+07 eV: total 0, states 2.163e-01 b; MT111/ZAP17038 @ declared 1.1477e+07 eV: rel 3.299e+16 |
| neutron | `n-Kr079m.tendl` | 2.722e+19 | MT4/ZAP36079 @ 8.4671e+00 eV: total 0, states 4.869e-02 b; MT4/ZAP36079 @ 1.0000e+01 eV: total 0, states 4.460e-02 b; MT4/ZAP36079 @ 3.6293e+01 eV: total 0, states 3.534e-02 b; MT4 |
| neutron | `n-Lu178m.tendl` | 6.109e+11 | MT4/ZAP71178 @ 1.7088e+00 eV: total 0, states 4.455e-09 b; MT4/ZAP71178 @ 1.0000e+01 eV: total 0, states 1.632e-09 b; MT4/ZAP71178 @ 4.6488e+01 eV: total 0, states 1.136e-09 b; MT4 |
| neutron | `n-Mo095.tendl` | 2.743e+13 | MT32/ZAP41093 @ 1.3780e+07 eV: total 0, states 1.506e-03 b; MT32/ZAP41093 @ declared 1.3811e+07 eV: rel 2.743e+13 |
| neutron | `n-Mo096.tendl` | 1.365e+13 | MT105/ZAP41094 @ 9.4022e+06 eV: total 0, states 1.069e-08 b; MT105/ZAP41094 @ declared 9.4435e+06 eV: rel 1.365e+13 |
| neutron | `n-Mo097.tendl` | 1.933e+13 | MT32/ZAP41095 @ 1.4039e+07 eV: total 0, states 2.010e-05 b; MT32/ZAP41095 @ declared 1.4277e+07 eV: rel 1.933e+13 |
| neutron | `n-Nb091m.tendl` | 2.335e+09 | MT4/ZAP41091 @ 9.4578e+05 eV (between gridpoints): rel 3.427e-01; MT4/ZAP41091 @ 1.0943e+06 eV (between gridpoints): rel 2.391e-01; MT4/ZAP41091 @ 1.2215e+06 eV (between gridpoints |
| neutron | `n-Nb093.tendl` | 1.855e+01 | MT4/ZAP41093 @ 6.9425e+05 eV (between gridpoints): rel 6.590e-02; MT4/ZAP41093 @ 7.5203e+05 eV (between gridpoints): rel 1.621e+00; MT4/ZAP41093 @ 8.1760e+05 eV (between gridpoints |
| neutron | `n-Os184.tendl` | 5.302e+19 | MT18: MF=10 states with no MF=3 total; MT111/ZAP74183 @ 4.4176e+06 eV: total 0, states 4.993e-01 b; MT111/ZAP74183 @ declared 4.7804e+06 eV: rel 5.302e+19 |
| neutron | `n-Os190.tendl` | 1.157e-01 | MT4/ZAP76190 @ 1.8771e+05 eV: total 0, states 2.912e-01 b; MT4/ZAP76190 @ 5.5076e+05 eV (between gridpoints): rel 9.859e-02; MT4/ZAP76190 @ 5.6094e+05 eV (between gridpoints): rel  |
| neutron | `n-Pd102.tendl` | 2.266e+11 | MT32/ZAP45100 @ 1.5600e+07 eV: total 0, states 1.731e-05 b; MT32/ZAP45100 @ declared 1.5709e+07 eV: rel 2.266e+11 |
| neutron | `n-Pd104.tendl` | 2.444e+11 | MT105/ZAP45102 @ 9.5824e+06 eV: total 0, states 1.966e-09 b; MT105/ZAP45102 @ declared 9.7245e+06 eV: rel 2.444e+11 |
| neutron | `n-Pd105.tendl` | 1.184e+12 | MT32/ZAP45103 @ 1.3652e+07 eV: total 0, states 2.511e-05 b; MT32/ZAP45103 @ declared 1.3692e+07 eV: rel 1.184e+12 |
| neutron | `n-Pr141.tendl` | 8.734e+10 | MT32/ZAP58139 @ 1.2280e+07 eV: total 0, states 1.523e-06 b; MT32/ZAP58139 @ declared 1.3039e+07 eV: rel 8.734e+10 |
| neutron | `n-Pt194.tendl` | 4.307e+03 | MT18: MF=10 states with no MF=3 total; MT107/ZAP76191 @ declared 1.0000e-05 eV: rel 9.064e-01; MT107/ZAP76191 @ 1.4656e-05 eV (between gridpoints): rel 1.307e+00; MT107/ZAP76191 @  |
| neutron | `n-Re187.tendl` | 1.065e+10 | MT18: MF=10 states with no MF=3 total; MT32/ZAP74185 @ 1.1024e+07 eV: total 0, states 1.652e-08 b; MT32/ZAP74185 @ declared 1.1222e+07 eV: rel 1.065e+10 |
| neutron | `n-Rh101m.tendl` | 2.754e+14 | MT4/ZAP45101 @ 5.4789e+00 eV: total 0, states 1.170e-01 b; MT4/ZAP45101 @ 1.0000e+01 eV: total 0, states 8.580e-02 b; MT4/ZAP45101 @ 1.0000e+02 eV: total 0, states 2.556e-02 b; MT4 |
| neutron | `n-Sc045.tendl` | 5.592e+00 | MT4/ZAP21045 @ 1.2678e+04 eV: total 0, states 3.635e-07 b; MT4/ZAP21045 @ 2.0000e+04 eV: total 0, states 1.774e-02 b; MT4/ZAP21045 @ 3.0000e+04 eV: total 0, states 2.695e-02 b; MT4 |
| neutron | `n-Sc046m.tendl` | 8.716e+05 | MT4/ZAP21046 @ 6.7902e+02 eV: total 0, states 1.318e-02 b; MT4/ZAP21046 @ 1.0000e+03 eV: total 0, states 1.224e-02 b; MT4/ZAP21046 @ 2.0000e+03 eV: total 0, states 1.260e-02 b; MT4 |
| neutron | `n-Sn112.tendl` | 1.102e+10 | MT32/ZAP49110 @ 1.5459e+07 eV: total 0, states 9.521e-07 b; MT32/ZAP49110 @ declared 1.5522e+07 eV: rel 1.102e+10 |
| neutron | `n-Sn114.tendl` | 1.851e+01 | MT107/ZAP48111 @ 1.4982e-05 eV (between gridpoints): rel 2.238e-01; MT107/ZAP48111 @ 1.8338e-05 eV (between gridpoints): rel 3.537e-01; MT107/ZAP48111 @ 2.2445e-05 eV (between grid |
| neutron | `n-Sn117.tendl` | 7.101e+05 | MT4/ZAP50117 @ 1.5993e+05 eV: total 0, states 2.611e-01 b; MT4/ZAP50117 @ 6.8435e+05 eV (between gridpoints): rel 5.085e-02; MT4/ZAP50117 @ 7.1768e+05 eV (between gridpoints): rel  |
| neutron | `n-Ta181.tendl` | 2.066e+01 | MT18: MF=10 states with no MF=3 total; MT107/ZAP71178 @ declared 1.0000e-05 eV: rel 1.104e-01; MT107/ZAP71178 @ 1.4982e-05 eV (between gridpoints): rel 3.589e-01; MT107/ZAP71178 @  |
| neutron | `n-Tc095m.tendl` | 2.241e+02 | MT4/ZAP43095 @ 3.0066e+05 eV (between gridpoints): rel 7.140e-03; MT4/ZAP43095 @ 5.9420e+05 eV (between gridpoints): rel 1.474e-01; MT4/ZAP43095 @ 6.1410e+05 eV (between gridpoints |
| neutron | `n-Tc096m.tendl` | 8.296e+02 | MT4/ZAP43096 @ 9.9242e+00 eV: total 0, states 1.993e-02 b; MT4/ZAP43096 @ 1.0000e+01 eV: total 0, states 1.989e-02 b; MT4/ZAP43096 @ 1.0000e+02 eV: total 0, states 1.993e-02 b; MT4 |
| neutron | `n-Te119m.tendl` | 6.951e+19 | MT4/ZAP52119 @ 1.3988e+00 eV: total 0, states 4.705e-09 b; MT4/ZAP52119 @ 1.0000e+01 eV: total 0, states 3.002e-09 b; MT4/ZAP52119 @ 1.6501e+01 eV: total 0, states 3.205e-09 b; MT4 |
| neutron | `n-Ti047.tendl` | 2.738e+13 | MT105/ZAP21045 @ 1.0975e+07 eV: total 0, states 1.992e-04 b; MT105/ZAP21045 @ declared 1.0987e+07 eV: rel 2.738e+13 |
| neutron | `n-Xe134.tendl` | 5.640e-02 | MT4/ZAP54134 @ 8.5342e+05 eV: total 0, states 2.427e-01 b; MT4/ZAP54134 @ 1.6259e+06 eV (between gridpoints): rel 4.387e-02; MT4/ZAP54134 @ 1.6485e+06 eV (between gridpoints): rel  |
| neutron | `n-Zr089m.tendl` | 3.696e+15 | MT4/ZAP40089 @ 5.1284e+05 eV (between gridpoints): rel 2.379e-01; MT4/ZAP40089 @ 8.7321e+05 eV (between gridpoints): rel 2.683e-02; MT4/ZAP40089 @ 9.3445e+05 eV (between gridpoints |
| neutron | `n-Zr090.tendl` | 8.750e-02 | MT4/ZAP40090 @ 1.7805e+06 eV: total 0, states 2.253e-02 b; MT4/ZAP40090 @ 2.2108e+06 eV (between gridpoints): rel 8.750e-02; MT4/ZAP40090 @ declared 2.3450e+06 eV: rel 1.220e-02; M |
| neutron | `n-Zr091.tendl` | 9.162e+13 | MT32/ZAP39089 @ 1.3468e+07 eV: total 0, states 7.002e-03 b; MT32/ZAP39089 @ declared 1.4387e+07 eV: rel 9.162e+13 |
| neutron | `n-Zr092.tendl` | 3.088e+08 | MT105/ZAP39090 @ 8.9376e+06 eV: total 0, states 2.395e-10 b; MT105/ZAP39090 @ declared 9.6271e+06 eV: rel 3.088e+08 |
| proton | `p-Cd111.tendl` | 6.856e-01 | MT103/ZAP48111 @ 2.4762e+05 eV: total 0, states 1.548e-13 b; MT103/ZAP48111 @ declared 3.9981e+05 eV: rel 6.856e-01 |
| proton | `p-Pb207.tendl` | 2.548e-01 | MT18: MF=10 states with no MF=3 total; MT103/ZAP82207 @ 5.7247e+05 eV: total 0, states 5.650e-13 b; MT103/ZAP82207 @ declared 1.6413e+06 eV: rel 2.548e-01 |
| proton | `p-Tc099.tendl` | 7.498e+00 | MT103/ZAP43099 @ 1.4194e+05 eV: total 0, states 6.849e-15 b; MT103/ZAP43099 @ declared 1.4414e+05 eV: rel 7.498e+00 |
| proton | `p-Zr090.tendl` | 6.681e-01 | MT103/ZAP40090 @ 1.7805e+06 eV: total 0, states 3.168e-09 b; MT103/ZAP40090 @ declared 2.3450e+06 eV: rel 6.681e-01 |

### B. Zero or near-zero declared total with group-level partials (45 evaluations)

| projectile | evaluation | worst rel. excess | decimal-oracle detail |
|---|---|---|---|
| alpha | `a-Lu174m.tendl` | 0.000e+00 | MT103/ZAP72177 @ 1.2000e+07 eV: total 0, states 4.442e-15 b |
| alpha | `a-Sr087.tendl` | 0.000e+00 | MT107/ZAP38087 @ 4.0642e+05 eV: total 0, states 1.334e-14 b |
| deuteron | `d-In108m.tendl` | 0.000e+00 | MT105/ZAP49107 @ 6.0000e+06 eV: total 0, states 2.594e-10 b |
| deuteron | `d-Y091m.tendl` | 0.000e+00 | MT105/ZAP39090 @ 4.0000e+06 eV: total 0, states 1.818e-10 b |
| neutron | `n-Ag104m.tendl` | 1.856e+01 | MT4/ZAP47104 @ 5.8153e+00 eV: total 0, states 4.752e-04 b; MT4/ZAP47104 @ 1.0000e+01 eV: total 0, states 4.947e-04 b; MT4/ZAP47104 @ 1.0000e+02 eV: total 0, states 1.076e-03 b; MT4 |
| neutron | `n-Ag107m.tendl` | 1.855e+01 | MT4/ZAP47107 @ 2.8000e+01 eV: total 0, states 5.246e-02 b; MT4/ZAP47107 @ 1.0000e+02 eV: total 0, states 2.543e-02 b; MT4/ZAP47107 @ 2.0000e+02 eV: total 0, states 1.706e-02 b; MT4 |
| neutron | `n-Ag108m.tendl` | 2.613e+02 | MT4/ZAP47108 @ 1.3274e+00 eV: total 0, states 2.232e-04 b; MT4/ZAP47108 @ 1.0000e+01 eV: total 0, states 8.207e-05 b; MT4/ZAP47108 @ 7.2202e+01 eV: total 0, states 4.579e-05 b; MT4 |
| neutron | `n-Ag109.tendl` | 1.851e+01 | MT4/ZAP47109 @ 8.8850e+04 eV: total 0, states 3.375e-08 b; MT4/ZAP47109 @ 1.3399e+05 eV (between gridpoints): rel 1.179e-01; MT4/ZAP47109 @ 3.1426e+05 eV (between gridpoints): rel  |
| neutron | `n-Ag109m.tendl` | 1.856e+01 | MT4/ZAP47109 @ 1.8000e+01 eV: total 0, states 5.680e-02 b; MT4/ZAP47109 @ 1.0000e+02 eV: total 0, states 2.156e-02 b; MT4/ZAP47109 @ 1.6526e+02 eV: total 0, states 1.695e-02 b; MT4 |
| neutron | `n-Ag110m.tendl` | 1.073e+00 | MT4/ZAP47110 @ 1.1949e+00 eV: total 0, states 1.954e-02 b; MT4/ZAP47110 @ 1.0000e+01 eV: total 0, states 6.715e-03 b; MT4/ZAP47110 @ 1.0000e+02 eV: total 0, states 2.118e-03 b; MT4 |
| neutron | `n-Am244m.tendl` | 1.323e+04 | MT4/ZAP95244 @ 3.6822e+00 eV: total 0, states 1.290e-12 b; MT4/ZAP95244 @ 1.0000e+01 eV: total 0, states 5.778e-12 b; MT4/ZAP95244 @ 3.4040e+01 eV: total 0, states 5.323e-11 b; MT4 |
| neutron | `n-Au197m.tendl` | 1.855e+01 | MT4/ZAP79197 @ 1.5500e+01 eV: total 0, states 1.905e+00 b; MT4/ZAP79197 @ 7.2554e+01 eV: total 0, states 1.026e+00 b; MT4/ZAP79197 @ 1.3906e+05 eV (between gridpoints): rel 6.680e- |
| neutron | `n-Ba137m.tendl` | 1.854e+01 | MT4/ZAP56137 @ 3.5000e+02 eV: total 0, states 1.230e+00 b; MT4/ZAP56137 @ 6.0000e+02 eV: total 0, states 7.997e-01 b; MT4/ZAP56137 @ 9.7695e+02 eV: total 0, states 5.471e-01 b; MT4 |
| neutron | `n-Br080m.tendl` | 1.740e+00 | MT4/ZAP35080 @ 8.8443e+00 eV: total 0, states 5.901e-02 b; MT4/ZAP35080 @ 1.0000e+01 eV: total 0, states 5.535e-02 b; MT4/ZAP35080 @ 1.0000e+02 eV: total 0, states 1.658e-02 b; MT4 |
| neutron | `n-Ce139m.tendl` | 1.855e+01 | MT4/ZAP58139 @ 1.7823e+01 eV: total 0, states 7.385e+00 b; MT4/ZAP58139 @ 1.0000e+02 eV: total 0, states 2.476e+00 b; MT4/ZAP58139 @ 2.0000e+02 eV: total 0, states 1.548e+00 b; MT4 |
| neutron | `n-Cs134m.tendl` | 1.856e+01 | MT4/ZAP55134 @ 1.6000e+01 eV: total 0, states 1.381e-01 b; MT4/ZAP55134 @ 4.7425e+01 eV: total 0, states 1.031e-01 b; MT4/ZAP55134 @ 1.1926e+05 eV (between gridpoints): rel 2.156e- |
| neutron | `n-Eu150m.tendl` | 9.689e+04 | MT4/ZAP63150 @ 2.3507e+00 eV: total 0, states 1.282e-11 b; MT4/ZAP63150 @ 1.0000e+01 eV: total 0, states 2.462e-09 b; MT4/ZAP63150 @ 1.0000e+02 eV: total 0, states 1.032e-05 b; MT4 |
| neutron | `n-Fe053m.tendl` | 7.904e-01 | MT4/ZAP26053 @ 1.3808e+05 eV (between gridpoints): rel 6.996e-02; MT4/ZAP26053 @ 2.7667e+05 eV (between gridpoints): rel 1.982e-02; MT4/ZAP26053 @ 2.9817e+05 eV (between gridpoints |
| neutron | `n-In110m.tendl` | 2.784e+02 | MT4/ZAP49110 @ 2.7321e+00 eV: total 0, states 4.096e-13 b; MT4/ZAP49110 @ 1.0000e+01 eV: total 0, states 1.654e-07 b; MT4/ZAP49110 @ 1.9010e+01 eV: total 0, states 2.015e-07 b; MT4 |
| neutron | `n-In112m.tendl` | 1.856e+01 | MT4/ZAP49112 @ 8.5253e-01 eV: total 0, states 5.682e-01 b; MT4/ZAP49112 @ 1.0000e+00 eV: total 0, states 5.246e-01 b; MT4/ZAP49112 @ 1.0000e+01 eV: total 0, states 1.656e-01 b; MT4 |
| neutron | `n-In114m.tendl` | 1.856e+01 | MT4/ZAP49114 @ 8.6288e-01 eV: total 0, states 1.077e-12 b; MT4/ZAP49114 @ 1.0000e+00 eV: total 0, states 1.344e-12 b; MT4/ZAP49114 @ 1.0000e+01 eV: total 0, states 1.191e-05 b; MT4 |
| neutron | `n-In119m.tendl` | 6.198e-01 | MT4/ZAP49119 @ 6.2537e+01 eV: total 0, states 1.107e+00 b; MT4/ZAP49119 @ 1.0000e+02 eV: total 0, states 8.032e-01 b; MT4/ZAP49119 @ 1.0818e+02 eV: total 0, states 7.781e-01 b; MT4 |
| neutron | `n-Ir193m.tendl` | 1.856e+01 | MT4/ZAP77193 @ 5.8000e+00 eV: total 0, states 7.710e-03 b; MT4/ZAP77193 @ 1.0000e+01 eV: total 0, states 5.750e-03 b; MT4/ZAP77193 @ 1.0000e+02 eV: total 0, states 1.589e-03 b; MT4 |
| neutron | `n-Lu174m.tendl` | 1.330e+00 | MT4/ZAP71174 @ 4.1708e-01 eV: total 0, states 9.486e+00 b; MT4/ZAP71174 @ 1.0000e+00 eV: total 0, states 6.096e+00 b; MT4/ZAP71174 @ 1.0000e+01 eV: total 0, states 1.871e+00 b; MT4 |
| neutron | `n-Lu176m.tendl` | 3.107e+03 | MT4/ZAP71176 @ 1.0000e+02 eV: total 0, states 1.657e-10 b; MT4/ZAP71176 @ 1.9719e+02 eV: total 0, states 4.619e-10 b; MT4/ZAP71176 @ 6.1636e+04 eV (between gridpoints): rel 6.431e+ |
| neutron | `n-Mo091m.tendl` | 1.855e+01 | MT4/ZAP42091 @ 3.7281e+01 eV: total 0, states 1.660e-01 b; MT4/ZAP42091 @ 5.0867e+05 eV (between gridpoints): rel 2.413e-01; MT4/ZAP42091 @ 7.1687e+05 eV (between gridpoints): rel  |
| neutron | `n-Os183m.tendl` | 1.101e+01 | MT4/ZAP76183 @ 1.2363e+00 eV: total 0, states 2.353e-05 b; MT4/ZAP76183 @ 1.0000e+01 eV: total 0, states 4.358e-02 b; MT4/ZAP76183 @ 5.7875e+01 eV: total 0, states 2.728e-02 b; MT4 |
| neutron | `n-Pm148m.tendl` | 1.137e+00 | MT4/ZAP61148 @ 1.0979e+00 eV: total 0, states 4.966e+00 b; MT4/ZAP61148 @ 1.0000e+01 eV: total 0, states 1.507e+00 b; MT4/ZAP61148 @ 4.2326e+01 eV: total 0, states 6.544e-01 b; MT4 |
| neutron | `n-Pr142m.tendl` | 9.310e-01 | MT4/ZAP59142 @ 9.0960e+00 eV: total 0, states 1.476e-02 b; MT4/ZAP59142 @ 1.0000e+01 eV: total 0, states 1.357e-02 b; MT4/ZAP59142 @ 5.6232e+01 eV: total 0, states 7.874e-03 b; MT4 |
| neutron | `n-Pr144m.tendl` | 5.535e+00 | MT4/ZAP59144 @ 7.8956e+00 eV: total 0, states 4.099e+00 b; MT4/ZAP59144 @ 1.0000e+01 eV: total 0, states 3.507e+00 b; MT4/ZAP59144 @ 2.8473e+01 eV: total 0, states 2.928e+00 b; MT4 |
| neutron | `n-Re184m.tendl` | 1.054e+00 | MT4/ZAP75184 @ 3.7723e-01 eV: total 0, states 6.555e-03 b; MT4/ZAP75184 @ 1.0000e+00 eV: total 0, states 4.015e-03 b; MT4/ZAP75184 @ 1.0000e+01 eV: total 0, states 1.253e-03 b; MT4 |
| neutron | `n-Rh102m.tendl` | 1.856e+01 | MT4/ZAP45102 @ 4.0433e+00 eV: total 0, states 8.413e-02 b; MT4/ZAP45102 @ 1.0000e+01 eV: total 0, states 5.313e-02 b; MT4/ZAP45102 @ 1.0000e+02 eV: total 0, states 1.642e-02 b; MT4 |
| neutron | `n-Rh104m.tendl` | 1.146e+00 | MT4/ZAP45104 @ 1.7406e+00 eV: total 0, states 2.848e-02 b; MT4/ZAP45104 @ 1.0000e+01 eV: total 0, states 1.274e-02 b; MT4/ZAP45104 @ 9.2789e+01 eV: total 0, states 7.635e-03 b; MT4 |
| neutron | `n-Rh105m.tendl` | 1.796e+00 | MT4/ZAP45105 @ 1.4748e+01 eV: total 0, states 6.828e-01 b; MT4/ZAP45105 @ 1.9568e+04 eV (between gridpoints): rel 6.929e-03; MT4/ZAP45105 @ 2.6531e+05 eV (between gridpoints): rel  |
| neutron | `n-Sb120m.tendl` | 3.201e+02 | MT4/ZAP51120 @ 1.7682e+00 eV: total 0, states 1.478e-07 b; MT4/ZAP51120 @ 1.0000e+01 eV: total 0, states 6.233e-08 b; MT4/ZAP51120 @ 6.1478e+01 eV: total 0, states 3.951e-08 b; MT4 |
| neutron | `n-Sb122m.tendl` | 1.438e+02 | MT4/ZAP51122 @ 3.5113e+00 eV: total 0, states 8.652e-04 b; MT4/ZAP51122 @ 1.0000e+01 eV: total 0, states 5.048e-04 b; MT4/ZAP51122 @ 1.0000e+02 eV: total 0, states 1.568e-04 b; MT4 |
| neutron | `n-Sb128m.tendl` | 7.291e-01 | MT4/ZAP51128 @ 8.4045e+01 eV: total 0, states 1.276e-01 b; MT4/ZAP51128 @ 1.0000e+02 eV: total 0, states 1.127e-01 b; MT4/ZAP51128 @ 2.0000e+02 eV: total 0, states 6.985e-02 b; MT4 |
| neutron | `n-Sm143m.tendl` | 4.322e-01 | MT4/ZAP62143 @ 1.4321e+00 eV: total 0, states 8.811e-03 b; MT4/ZAP62143 @ 1.0000e+01 eV: total 0, states 1.951e+00 b; MT4/ZAP62143 @ 3.3948e+01 eV: total 0, states 1.584e+00 b; MT4 |
| neutron | `n-Te131m.tendl` | 1.851e+01 | MT4/ZAP52131 @ 1.9671e+03 eV: total 0, states 1.896e-02 b; MT4/ZAP52131 @ 2.0000e+03 eV: total 0, states 1.891e-02 b; MT4/ZAP52131 @ 3.0000e+03 eV: total 0, states 1.804e-02 b; MT4 |
| neutron | `n-W185m.tendl` | 1.856e+01 | MT4/ZAP74185 @ 3.0103e+00 eV: total 0, states 8.106e+00 b; MT4/ZAP74185 @ 1.0000e+01 eV: total 0, states 4.314e+00 b; MT4/ZAP74185 @ 5.7940e+01 eV: total 0, states 2.586e+00 b; MT4 |
| neutron | `n-Y087m.tendl` | 1.856e+01 | MT4/ZAP39087 @ 2.1282e+01 eV: total 0, states 6.731e-01 b; MT4/ZAP39087 @ 4.4244e+01 eV: total 0, states 5.612e-01 b; MT4/ZAP39087 @ 4.1770e+05 eV (between gridpoints): rel 3.233e- |
| neutron | `n-Y088.tendl` | 0.000e+00 | MT16/ZAP39087 @ 9.4593e+06 eV: total 0, states 2.606e+01 b |
| neutron | `n-Y090m.tendl` | 5.399e-01 | MT4/ZAP39090 @ 2.1338e+02 eV: total 0, states 1.586e+00 b; MT4/ZAP39090 @ 3.0000e+02 eV: total 0, states 1.248e+00 b; MT4/ZAP39090 @ 4.6987e+02 eV: total 0, states 9.729e-01 b; MT4 |
| neutron | `n-Zr088.tendl` | 1.851e+01 | MT16/ZAP40087 @ 1.2495e+07 eV: total 0, states 7.012e+01 b; MT107/ZAP38085 @ 1.4982e-05 eV (between gridpoints): rel 2.238e-01; MT107/ZAP38085 @ 1.8338e-05 eV (between gridpoints): |
| proton | `p-Sr087.tendl` | 0.000e+00 | MT103/ZAP38087 @ 3.9303e+05 eV: total 0, states 1.751e-15 b |

### C. Apparent interpolation artifacts exceeding the mechanism envelope (needs adjudication) (25 evaluations)

| projectile | evaluation | worst rel. excess | decimal-oracle detail |
|---|---|---|---|
| neutron | `n-Ag107.tendl` | 1.851e+01 | MT4/ZAP47107 @ 1.2677e+05 eV (between gridpoints): rel 1.436e-01; MT4/ZAP47107 @ 3.2787e+05 eV (between gridpoints): rel 1.388e+00; MT4/ZAP47107 @ 4.2714e+05 eV (between gridpoints |
| neutron | `n-Am243.tendl` | 1.854e+01 | MT107/ZAP93240 @ 1.4454e-05 eV (between gridpoints): rel 2.021e-01; MT107/ZAP93240 @ 1.7378e-05 eV (between gridpoints): rel 3.179e-01; MT107/ZAP93240 @ 2.0893e-05 eV (between grid |
| neutron | `n-Ba132.tendl` | 1.853e+01 | MT107/ZAP54129 @ 1.4656e-05 eV (between gridpoints): rel 2.104e-01; MT107/ZAP54129 @ 1.7743e-05 eV (between gridpoints): rel 3.316e-01; MT107/ZAP54129 @ 2.1480e-05 eV (between grid |
| neutron | `n-Ba134.tendl` | 1.849e+01 | MT107/ZAP54131 @ 1.5347e-05 eV (between gridpoints): rel 2.386e-01; MT107/ZAP54131 @ 1.9012e-05 eV (between gridpoints): rel 3.784e-01; MT107/ZAP54131 @ 2.3553e-05 eV (between grid |
| neutron | `n-Ce138.tendl` | 1.853e+01 | MT107/ZAP56135 @ 1.4656e-05 eV (between gridpoints): rel 2.104e-01; MT107/ZAP56135 @ 1.7743e-05 eV (between gridpoints): rel 3.316e-01; MT107/ZAP56135 @ 2.1480e-05 eV (between grid |
| neutron | `n-Ce140.tendl` | 1.856e+01 | MT107/ZAP56137 @ 1.6201e-05 eV (between gridpoints): rel 2.725e-01; MT107/ZAP56137 @ 2.0621e-05 eV (between gridpoints): rel 4.354e-01; MT107/ZAP56137 @ 2.6247e-05 eV (between grid |
| neutron | `n-Cs133.tendl` | 1.851e+01 | MT107/ZAP53130 @ 1.4982e-05 eV (between gridpoints): rel 2.238e-01; MT107/ZAP53130 @ 1.8338e-05 eV (between gridpoints): rel 3.537e-01; MT107/ZAP53130 @ 2.2445e-05 eV (between grid |
| neutron | `n-Cu063.tendl` | 1.847e+01 | MT107/ZAP27060 @ 1.6070e-05 eV (between gridpoints): rel 2.674e-01; MT107/ZAP27060 @ 2.0372e-05 eV (between gridpoints): rel 4.267e-01; MT107/ZAP27060 @ 2.5825e-05 eV (between grid |
| neutron | `n-I127.tendl` | 1.851e+01 | MT107/ZAP51124 @ 1.4982e-05 eV (between gridpoints): rel 2.238e-01; MT107/ZAP51124 @ 1.8338e-05 eV (between gridpoints): rel 3.537e-01; MT107/ZAP51124 @ 2.2445e-05 eV (between grid |
| neutron | `n-In113.tendl` | 1.856e+01 | MT4/ZAP49113 @ 6.5261e+05 eV (between gridpoints): rel 4.640e-02; MT4/ZAP49113 @ 1.0334e+06 eV (between gridpoints): rel 2.960e-01; MT4/ZAP49113 @ 1.0388e+06 eV (between gridpoints |
| neutron | `n-In115.tendl` | 1.124e-01 | MT4/ZAP49115 @ 6.0239e+05 eV (between gridpoints): rel 3.591e-02; MT4/ZAP49115 @ 8.3586e+05 eV (between gridpoints): rel 5.519e-02; MT4/ZAP49115 @ 8.7172e+05 eV (between gridpoints |
| neutron | `n-Kr080.tendl` | 1.855e+01 | MT107/ZAP34077 @ 1.4307e-05 eV (between gridpoints): rel 1.959e-01; MT107/ZAP34077 @ 1.7114e-05 eV (between gridpoints): rel 3.078e-01; MT107/ZAP34077 @ 2.0470e-05 eV (between grid |
| neutron | `n-Mo092.tendl` | 1.856e+01 | MT107/ZAP40089 @ 1.5688e-05 eV (between gridpoints): rel 2.522e-01; MT107/ZAP40089 @ 1.9649e-05 eV (between gridpoints): rel 4.012e-01; MT107/ZAP40089 @ 2.4611e-05 eV (between grid |
| neutron | `n-Nd142.tendl` | 1.856e+01 | MT107/ZAP58139 @ 1.5472e-05 eV (between gridpoints): rel 2.436e-01; MT107/ZAP58139 @ 1.9245e-05 eV (between gridpoints): rel 3.868e-01; MT107/ZAP58139 @ 2.3938e-05 eV (between grid |
| neutron | `n-Ni058.tendl` | 1.854e+01 | MT103/ZAP27058 @ 1.6561e-05 eV (between gridpoints): rel 2.866e-01; MT103/ZAP27058 @ 2.1312e-05 eV (between gridpoints): rel 4.592e-01; MT103/ZAP27058 @ 2.7426e-05 eV (between grid |
| neutron | `n-Np237.tendl` | 1.854e+01 | MT107/ZAP91234 @ 1.4312e-05 eV (between gridpoints): rel 1.961e-01; MT107/ZAP91234 @ 1.7122e-05 eV (between gridpoints): rel 3.082e-01; MT107/ZAP91234 @ 2.0484e-05 eV (between grid |
| neutron | `n-Ru096.tendl` | 1.856e+01 | MT107/ZAP42093 @ 1.5472e-05 eV (between gridpoints): rel 2.436e-01; MT107/ZAP42093 @ 1.9245e-05 eV (between gridpoints): rel 3.868e-01; MT107/ZAP42093 @ 2.3938e-05 eV (between grid |
| neutron | `n-Sm144.tendl` | 1.849e+01 | MT107/ZAP60141 @ 1.5347e-05 eV (between gridpoints): rel 2.386e-01; MT107/ZAP60141 @ 1.9012e-05 eV (between gridpoints): rel 3.784e-01; MT107/ZAP60141 @ 2.3553e-05 eV (between grid |
| neutron | `n-Sr084.tendl` | 1.851e+01 | MT107/ZAP36081 @ 1.4982e-05 eV (between gridpoints): rel 2.238e-01; MT107/ZAP36081 @ 1.8338e-05 eV (between gridpoints): rel 3.537e-01; MT107/ZAP36081 @ 2.2445e-05 eV (between grid |
| neutron | `n-Te120.tendl` | 1.854e+01 | MT107/ZAP50117 @ 1.4454e-05 eV (between gridpoints): rel 2.021e-01; MT107/ZAP50117 @ 1.7378e-05 eV (between gridpoints): rel 3.179e-01; MT107/ZAP50117 @ 2.0893e-05 eV (between grid |
| neutron | `n-Te122.tendl` | 1.856e+01 | MT107/ZAP50119 @ 1.5472e-05 eV (between gridpoints): rel 2.436e-01; MT107/ZAP50119 @ 1.9245e-05 eV (between gridpoints): rel 3.868e-01; MT107/ZAP50119 @ 2.3938e-05 eV (between grid |
| neutron | `n-Xe126.tendl` | 1.851e+01 | MT107/ZAP52123 @ 1.4982e-05 eV (between gridpoints): rel 2.238e-01; MT107/ZAP52123 @ 1.8338e-05 eV (between gridpoints): rel 3.537e-01; MT107/ZAP52123 @ 2.2445e-05 eV (between grid |
| neutron | `n-Xe128.tendl` | 1.851e+01 | MT107/ZAP52125 @ 1.5136e-05 eV (between gridpoints): rel 2.300e-01; MT107/ZAP52125 @ 1.8621e-05 eV (between gridpoints): rel 3.641e-01; MT107/ZAP52125 @ 2.2909e-05 eV (between grid |
| neutron | `n-Xe133m.tendl` | 2.192e-01 | MT4/ZAP54133 @ 2.9890e+05 eV (between gridpoints): rel 4.886e-03; MT4/ZAP54133 @ 3.7749e+05 eV (between gridpoints): rel 1.578e-01; MT4/ZAP54133 @ 4.5043e+05 eV (between gridpoints |
| neutron | `n-Y089.tendl` | 6.552e-01 | MT4/ZAP39089 @ 1.5245e+06 eV (between gridpoints): rel 6.552e-01; MT4/ZAP39089 @ 1.7645e+06 eV (between gridpoints): rel 7.797e-02; MT4/ZAP39089 @ 2.2476e+06 eV (between gridpoints |

### D. MF=10 partials without an MF=3 comparator section (50 evaluations)

| projectile | evaluation | worst rel. excess | decimal-oracle detail |
|---|---|---|---|
| alpha | `a-Os183m.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| alpha | `a-Os192.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| alpha | `a-Po199m.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| alpha | `a-Po201m.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| alpha | `a-Po211m.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| alpha | `a-Po212m.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| alpha | `a-Pt194.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| deuteron | `d-Au197.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| deuteron | `d-Au198m.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| deuteron | `d-Bi203.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| deuteron | `d-Bi209.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| deuteron | `d-Hg193m.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| deuteron | `d-Hg195m.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| deuteron | `d-Hg197m.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| deuteron | `d-Np237.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| deuteron | `d-Pb207.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| deuteron | `d-Pb208.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| deuteron | `d-Po211m.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| deuteron | `d-Pt198.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| deuteron | `d-Re184m.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| deuteron | `d-Th232.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| deuteron | `d-Tl196m.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| deuteron | `d-Tl198m.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| deuteron | `d-U238.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| neutron | `n-Au200m.tendl` | 1.856e+01 | MT4/ZAP79200 @ 1.6844e+03 eV (between gridpoints): rel 5.876e-02; MT4/ZAP79200 @ 4.6664e+03 eV (between gridpoints): rel 3.773e-02; MT4/ZAP79200 @ 6.6433e+03 eV (between gridpoints |
| neutron | `n-Bi209.tendl` | 1.852e+01 | MT18: MF=10 states with no MF=3 total; MT107/ZAP81206 @ 1.5849e-05 eV (between gridpoints): rel 2.586e-01; MT107/ZAP81206 @ 1.9953e-05 eV (between gridpoints): rel 4.120e-01; MT107 |
| neutron | `n-Bi210m.tendl` | 1.212e+04 | MT4/ZAP83210 @ 4.8653e+04 eV (between gridpoints): rel 2.220e-01; MT4/ZAP83210 @ 7.7008e+04 eV (between gridpoints): rel 6.984e-01; MT4/ZAP83210 @ 1.6295e+05 eV (between gridpoints |
| neutron | `n-Hg196.tendl` | 1.854e+01 | MT18: MF=10 states with no MF=3 total; MT107/ZAP78193 @ 1.4454e-05 eV (between gridpoints): rel 2.021e-01; MT107/ZAP78193 @ 1.7378e-05 eV (between gridpoints): rel 3.179e-01; MT107 |
| neutron | `n-Hg198.tendl` | 1.855e+01 | MT18: MF=10 states with no MF=3 total; MT107/ZAP78195 @ 1.4307e-05 eV (between gridpoints): rel 1.959e-01; MT107/ZAP78195 @ 1.7114e-05 eV (between gridpoints): rel 3.078e-01; MT107 |
| neutron | `n-Hg200.tendl` | 1.849e+01 | MT18: MF=10 states with no MF=3 total; MT107/ZAP78197 @ 1.5347e-05 eV (between gridpoints): rel 2.386e-01; MT107/ZAP78197 @ 1.9012e-05 eV (between gridpoints): rel 3.784e-01; MT107 |
| neutron | `n-Pt198.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| neutron | `n-Re185.tendl` | 1.853e+01 | MT18: MF=10 states with no MF=3 total; MT107/ZAP73182 @ 1.4656e-05 eV (between gridpoints): rel 2.104e-01; MT107/ZAP73182 @ 1.7743e-05 eV (between gridpoints): rel 3.316e-01; MT107 |
| neutron | `n-W180.tendl` | 1.855e+01 | MT18: MF=10 states with no MF=3 total; MT107/ZAP72177 @ 1.4110e-05 eV (between gridpoints): rel 1.877e-01; MT107/ZAP72177 @ 1.6762e-05 eV (between gridpoints): rel 2.943e-01; MT107 |
| proton | `p-Au197.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| proton | `p-Au198m.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| proton | `p-Hg193m.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| proton | `p-Hg195m.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| proton | `p-Hg197m.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| proton | `p-Np236m.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| proton | `p-Pb204m.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| proton | `p-Pt198.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| proton | `p-Pu239.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| proton | `p-Re182m.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| proton | `p-Re184m.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| proton | `p-Ta181.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| proton | `p-Tl198m.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| proton | `p-U235.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| proton | `p-U238.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| proton | `p-W184.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |
| proton | `p-W186.tendl` | 0.000e+00 | MT18: MF=10 states with no MF=3 total |

### E. MF=8 ELFS vs QM−QI state-identity conflicts resolved within the 1 keV bound (14 evaluations)

| projectile | evaluation |
|---|---|
| alpha | `a-In115.tendl` |
| alpha | `a-Pt194.tendl` |
| alpha | `a-Sb128m.tendl` |
| alpha | `a-Tc094m.tendl` |
| deuteron | `d-Mo092.tendl` |
| neutron | `n-In115.tendl` |
| neutron | `n-Pr142m.tendl` |
| neutron | `n-Sn118.tendl` |
| proton | `p-In115.tendl` |
| proton | `p-Kr079m.tendl` |
| proton | `p-Sn118.tendl` |
| proton | `p-Sn130m.tendl` |
| proton | `p-Te129m.tendl` |
| proton | `p-Te131m.tendl` |
