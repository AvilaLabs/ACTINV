# ACTINV P3 — decay-data fallback, resolved-resonance reconstruction, rate-significance pruning, certificate, docs

**Opened:** 2026-08-25/26 on the principal's "continue". **Predecessor:** P2 (P2-CONDITIONAL). **Time box:** three calendar
days from hash. **External acts** (remote repository, publication, contact): the principal's. Data stay under
`~/nuclear-data/`; reads of public data only, each recorded with URL, size, SHA-256.

## Gates

### P3-G1 Decay-data fallback
The 20 EAF-2010 products without an ENDF/B-VIII.0 decay record (P2 ledger) are identified. A second decay sublibrary
from the IAEA mirror (JEFF-3.3 radioactive decay data, or ENDF/B-VIII.1 decay — whichever covers them; both recorded)
is parsed by the own parser; nuclides absent from the primary are taken from the fallback, with the source recorded per
nuclide in every ledger. Controls: (a) own parser vs `openmc.data.Decay` on the fallback file, 200 seeded nuclides
(seed 20260826) exact at 1e-12; (b) FNS rerun: `products_no_decay_record` count per experiment reported; residual
misses (if any) listed with reason. Report: half-life and mean-energy differences between sources for nuclides in both
(top 20 by relative difference) — information, not a gate.

### P3-G2 Resolved-resonance reconstruction and Doppler broadening (own code)
Formalisms: SLBW (LRF=1), MLBW (LRF=2), Reich–Moore (LRF=3); LRF=7 and LRU=2 with LSSF=0 are ledgered as unsupported
in P3 (no silent use of background-only MF=3). Doppler broadening to 293.6 K by the SIGMA1 kernel on the reconstructed
grid. Controls: (a) 0 K reconstruction of TENDL-2023 Fe-56, Ag-107 and W-186 MT 2 and MT 102 vs `openmc.data`'s
independent reconstruction on the same file, on 4,000 log-spaced points plus every resonance energy, ≤ 1e-6 relative
(points within 1e-9 relative of an exact pole excluded and counted); (b) Doppler invariants: a 1/v function and a
constant are unchanged by broadening ≤ 1e-6; (c) a single SLBW resonance broadened numerically vs the analytic ψ/χ
form ≤ 1e-4 at the peak and ≤ 1e-3 in the wings over ±20 Γ; (d) the 293.6 K TENDL-2023 one-group σ on the FNS Fe
spectrum for Fe-56 (n,γ), (n,p), (n,α), (n,2n) compared with EAF-2010 — reported.

### P3-G3 Rate-significance pruning
Forward bound: w(unit) = 1; w(i) = max_j w(j)·min(1, r_ij·T) over incoming edges, T = total schedule time; nodes with
w < 1e-20 are dropped and the sum of their bounds (atoms per gram ≤ w·N_bulk) is ledgered per experiment. Control: on
all 132 FNS experiments, heat with and without rate pruning ≤ 1e-10 relative at every matched point; timing before/after
reported.

### P3-G4 Certificate
The FNS harness emits `results/fns_certificate.json`: SHA-256 of every input (library npz, decay files, fns.zip,
abundance table, CRAM coefficients, protocol files), the solver binary hash, and per-experiment hashes of the stored
inventories and C/E. `controls/check_p3.py` re-derives every C/E from the inventories and re-hashes; MATCH required.

### P3-G5 Documentation (recorded, not gated)
README quick start (build, run one problem, run the harness), docs/METHOD.md, docs/DATA.md (sources, terms, hashes,
no-bundling rule), docs/HARNESS.md (adding another code's inventories), docs/LEDGER.md, CONTRIBUTING.md (DCO),
docs/VALIDATION.md generated from the FNS report.

## Verdict (`controls/check_p3.py`)
P3-PASS: G1–G4 controls pass. P3-CONDITIONAL: a gate passing after its one repair round. P3-FAIL: a gate failing after
repair. UNSCORED: time box. Honesty and boundaries as P2.
