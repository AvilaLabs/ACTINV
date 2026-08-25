# ACTINV P2 — FNS decay-heat accuracy instrument (code-agnostic), solver CLI with pruning, PyO3 API

**Opened:** 2026-08-25 on the principal's "P2 can proceed". **Licence:** dual MIT OR Apache-2.0 confirmed by the
principal; LICENSE-MIT written, LICENSE-APACHE fetched verbatim from apache.org (sha256 cfc7749b…).
**Time box:** three calendar days from hash. **External acts:** principal's. **No remote repository.**
**Predecessor:** P1 (P1-PASS). Nuclear data stay under `~/nuclear-data/`, never in the repository.

## Gates

### P2-G1 Library
EAF-2010 full neutron library (817 targets) from the IAEA mirror (`~/nuclear-data/eaf-2010`, SHA-256 manifest).
Own parser reads MF=3, MF=8 (product ZAP/LFS/LMF), MF=9, MF=10 for every file; a file or section that fails to
parse is ledgered — zero silent skips. 709-group pre-collapse per (target, MT, product, LFS): σ_g = ∫_g σ(E) dE/E
÷ ln(E_{g+1}/E_g) (flat-lethargy weight, lin-lin between union points; boundaries from `pypact.ALL_GROUPS[709]`).
Controls: (a) seeded sample of 40 files (seed 20260825), every reaction: one-group σ on the FNS Fe spectrum via the
709-group library equals the pointwise collapse of P1 to 1e-12 relative; (b) same sample: pointwise σ vs openmc's
low-level TAB1 reader ≤ 1e-6; (c) for every MF=9/MF=10 section, (ZAP, LFS) from MF=8 equals the section header.

### P2-G2 Solver CLI
`actinv-solve` (crate `actinv-cli`): reads a problem file (decay matrix; reaction matrix per unit flux; n0; CRAM
coefficients; schedule of (dt, φ)); prunes to the set reachable from support(n0) over the union pattern; CRAM-16
per step; writes sparse inventories per step, pruned size, timing.
Controls: (a) pruned vs unpruned on the P1 Fe-56 problem ≤ 1e-12 relative on components > 1e-15 of total;
(b) unpruned = P1 Rust result bit-for-bit and = Python reference at 0.0; (c) per-step time, pruned — reported.

### P2-G3 Harness — the code-agnostic instrument
(i) inventory interchange: JSON records {Z, A, LISO, atoms_per_g, t_s, source}; (ii) decay-heat evaluator for ANY
inventory: P = Σ λ_i N_i (E_light + E_EM + E_heavy)_i from ENDF/B-VIII.0 MT=457 (own parser), W/g; (iii) readers for
FISPACT-II `.i` (MASS composition, FLUX, irradiation and cumulative cooling schedule with SECS/MINS/HOURS/DAYS/YEARS),
`.exp` (measured heat, σ), `.out` (TOTAL HEAT per interval), `.nuclides` (per-nuclide kW/kg vs time); (iv) composition:
element wt-% → isotopic atoms per gram via natural-abundance and atomic-mass tables copied from `openmc.data` with its
citation (independent re-verification deferred to P3, recorded); (v) C/E for every code, and a checker that recomputes
every C/E from stored inventories.
Controls: (a) single-nuclide hand calculation (Mn-56) vs evaluator ≤ 1e-10; (b) `.out` TOTAL HEAT (kW, MASS 1e-3 kg)
converted to kW/kg equals the `.nuclides` Total column at matching steps ≤ 1e-6 (two outputs of one FISPACT run — tests
readers and units); (c) composition closure: Σ atom fractions = 1 and element mass balance to 1e-12.

### P2-G4 FNS run — accuracy REPORTED, instrument GATED
All 132 experiments. Per experiment: φ_g = file groups scaled to the FLUX total; irradiation TIME; cooling schedule from
the `.i`; ACTINV heat evaluated at the cumulative cooling times (instantaneous; convention recorded); measured values
aligned to the schedule (row count must match, else ledgered); C/E_ACTINV and C/E_FISPACT-II(TENDL-2017 reference).
Per-experiment ledger: composition isotopes absent from EAF-2010 (with abundance), reaction products without a decay
record (booked to leakage), nuclides lacking mean-energy data. Report: geometric-mean C/E and max|ln C/E| per
experiment for both codes; top-3 heat contributors (ACTINV vs `.nuclides`) at first and last time.
Dispositions (reported): AGREE-MEAS (every point within max(2σ_exp, 10 %)), AGREE-REF (|ln C/E_ACTINV − ln C/E_FISPACT|
≤ 0.1 at every point), DISAGREE (else, with nuclide diagnosis).
Diagnostic trigger: if the median over experiments of max|ln C/E_ACTINV| exceeds FISPACT's by more than ln 2, the run is
INSTRUMENT-SUSPECT: the three worst experiments are diagnosed nuclide by nuclide before the verdict; the diagnosis is
recorded whether or not a defect is found.
Instrument gate: PASS iff every experiment ran, every data gap is ledgered, and `controls/check_p2.py` reproduces every
C/E from the stored inventories to 1e-12.

### P2-G5 PyO3 API (conditional)
`python/` maturin build of module `actinv` exposing the solver; control: Python-called step = CLI result at 0.0.
Tooling failure → DEFERRED, recorded, not a failure.

## Verdict (`controls/check_p2.py`)
P2-PASS: G1–G4 controls and instrument gate pass, G5 pass or deferred. P2-CONDITIONAL: a gate passing after its one
repair round. P2-FAIL: a gate failing after repair. UNSCORED: time box.

## Honesty and boundaries
As P1. Accuracy numbers are reported as measured against a 2010-vintage library (EAF-2010) with ENDF/B-VIII.0 decay
data; no accuracy claim beyond the tables. Reads of public data only; writes confined to `~/Documents/actinv`,
`~/nuclear-data`, `~/.cargo`, `~/.rustup`, the venv (`pip install maturin`), the scratchpad, and Avila-Labs STATUS at close.
