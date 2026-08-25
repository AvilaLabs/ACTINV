# ACTINV ledger (append-only)

## 0 — 2026-08-25 — program opened
- Name ACTINV chosen by the principal (crates.io free, PyPI free, 60 trivial GitHub name hits — ACT-P0
  results/names.json). Sequencing A (solver) then B (code-agnostic harness) — principal's decision.
  Licence proposed MIT OR Apache-2.0, pending. Predecessor record: Avila-Labs/scouting/act-p0.
- P1 protocol written and hashed (protocols/protocol_hash.txt). Local git only; no remote.

## 1 — 2026-08-25 — G4 skeleton, G2 part 1, G1
- G4: Cargo workspace (actinv-core / actinv-data / actinv-cli), `num-complex` resolved from crates.io,
  release build 1.7 s. Local git commit adb4e11. No remote.
- G2 part 1 (`controls/chain.py`): decay network from ENDF/B-VIII.0 → 3,821 nuclides (3,562 with λ>0),
  8,751 nonzeros, explicit leakage row: 128 spontaneous-fission branches (no yields in P1) and 8 absent
  daughters (Ni-48→Co-48; Cf-239/256, Es-240/243/258, Rf-253, Ds-279 products) booked to leakage —
  the first missing-data-ledger entries, produced by construction rather than by inspection.
- G1 (`controls/g1_collapse.py`, results/g1_collapse.json; spectrum written first to results/spectrum.json:
  FNS Fe 1996exp_5min, 709 groups, boundaries from pypact — stored descending, reversed; flat-lethargy
  intra-group shape): own MF=3/MF=9/MF=10 parser on EAF-2010 vs openmc.data on the same file, same grid,
  same integrator → **relative difference 0.0 on all 16 comparisons** (Fe-56 ×10 reactions; Ag-107 (n,γ)
  total + LFS 0/1 via MF=9; W-186 (n,γ) + (n,2n) LFS 0/1 via MF=10). One-group values on the FNS D-T
  spectrum: Fe-56 (n,2n) 0.4425 b, (n,p) 0.0940 b, (n,np) 0.0830 b, (n,α) 0.0364 b, (n,d) 7.47e-3 b,
  (n,γ) 1.34e-3 b, (n,nα) 2.59e-3 b, (n,t) 8.48e-5 b, (n,2p) 1.84e-9 b, (n,nd) 0; Ag-107(n,γ)→Ag-108
  2.951e-2 b / →Ag-108m 7.805e-4 b; W-186(n,γ) 6.92e-2 b; W-186(n,2n)→W-185 0.896 b / →W-185m 0.621 b.
  Fixes before scoring (not repair rounds — no control had run): pypact boundary order; EAF header
  "temperature" key is not a temperature (openmc labels it '3407087K'); W-186 (n,2n) exists only as
  MF=10 sections in EAF (no MF=3 total) → MF=9/10 controlled with openmc's low-level TAB1 reader.
  TENDL-2023 Fe-56: LRP=1, resolved resonances 1e-5 eV–850 keV (LRF=3 Reich-Moore) → reconstruction
  required (recorded, not attempted).

## 2 — 2026-08-25 — G2, G3, repairs, verdict
- G2 Python reference (`controls/cram_ref.py`, own Gilbert–Peierls sparse complex LU, CRAM-16 in OpenMC's IPF
  recurrence with the P0-recorded Pusa coefficients): control (1) analytic 3-chain 2.2e-15; control (4)
  conservation incl. leakage 6.7e-16; one irradiation step 0.1 s in pure Python (fill is small).
- Control (2) first form — dense expm on the full matrix — overflowed: max λ = 3.01e22 s⁻¹ (T½ 2.3e-23 s),
  3,050 nuclides with λ·1 y > 700. **P1 Amendment A** (sha 910ea789…): control on the closed reachable
  sub-network (10 states from Fe-56). Result: 1.28e-11 (irradiation 1 y), 1.33e-11 (cooling 1 d); CRAM
  mass outside the sub-network 6.2e-20 (below the 1e-15 rule; the script's exact-zero test was corrected
  to the protocol's rule — Amendment B §2).
- G2 Rust crate (`crates/actinv-core`: sparse.rs Gilbert–Peierls LU with partial pivoting, cram.rs; bin
  cram_probe): n=3,822, nnz 8,760, max LU nnz 14,998; **8.44 ms per CRAM-16 step** (50 reps, one thread,
  release) → timing PASS. Control (3) first run 8.55e-12 relative (2.2e-16 absolute) — cause: naive vs
  Smith complex division. **P1 Amendment B** (sha d69c2d19…): own Smith division in the crate → **0.0**.
- G3 (`controls/g3_ledger.py`): seeded planted deletion (ZA 24053, Cr-53, stable): named in the ledger,
  atom fraction 1.148e-4 booked to leakage → PASS. Supplementary radioactive deletion (Mn-56): named;
  its activity share in the unmodified run 48.7 % at end of irradiation, 0.15 % after 1 d, 0 after 1 y
  (CRAM round-off −1.5e-11 on a fully decayed component, reported as 0 with this note).
- Inventory sanity (Fe-56, 1e14 n cm⁻² s⁻¹, 1 y, FNS D-T spectrum): Fe-55 1.23e-3, Mn-55 4.48e-4,
  Cr-53 1.15e-4, Cr-52 8.2e-6, Fe-57 4.2e-6 atoms per initial atom — consistent with (n,2n) 0.44 b × fluence.
- `controls/check_p1.py` → **P1-PASS** (G1 PASS worst 0.0/16; G2 controls PASS, timing PASS 8.44 ms;
  G3 PASS; G4 recorded). Session closed; MANIFEST.sha256 regenerated once; local git commit.
- For P2: prune to the reachable network before factorising (10 states here vs 3,822); resonance
  reconstruction for TENDL; the FNS accuracy gate (measured decay heat vs ACTINV, code-agnostic runner).

## 3 — 2026-08-25 — P2 opened
- Principal: licence dual MIT OR Apache-2.0 confirmed; "P2 can proceed". LICENSE-MIT written; LICENSE-APACHE
  fetched verbatim (sha256 cfc7749b…); Cargo `license = "MIT OR Apache-2.0"`; README updated.
- P2 protocol hashed (protocols/protocol_hash.txt). Python.h present in the venv → PyO3 feasible (G5).
- FNS conventions (survey, not scored): 132 experiments / 73 materials; MASS card = element wt-%; fluxes file in
  absolute units (Fe: Σ = 1.1166e10 vs FLUX 1.116e10); schedule units SECS (default) / MINS / DAYS; 5-min
  experiments report times in minutes, 7-hour ones in days; `.nuclides` gives per-nuclide kW/kg vs time (years).
- EAF-2010 full library download started (background, 817 zips, 4 parallel).

## 4 — 2026-08-25 — P2 G1/G2/G3 results and repairs
- G1 library: 816 EAF-2010 targets (70 metastable), 115,831 (target, MT, product, LFS) rows, 0 parse failures,
  0 MF=8 header mismatches, 55 s build. Control (a) first run 1.1e-9 — cumulative-sum differencing lost precision
  on small groups; repair: direct per-group summation (`np.add.reduceat`); rebuilt; **4.2e-16** over 655 reactions.
  Control (b) 4.1e-14 over 1,661 reactions (openmc TAB1 reader). Control (c) 0/0. One repair round used for G1.
- G2 CLI (`actinv-solve`, reachable-set pruning): P1 Fe-56 schedule — pruned 10 states **0.105 ms** total vs
  unpruned 3,822 states 31.6 ms. First-run criteria unattainable (Mn-56 equilibrium component differs by 2.5e-18
  = 2.2e-16 of ΣN; per-unit-flux scaling differs from P1 by 4e-25 on a 5.6e-17 component); **P2 Amendment A**
  (sha 3e91d74d…): |Δ|/ΣN ≤ 1e-12 and ≤ 1e-12 relative on components > 1e-3 ΣN → pruned vs unpruned 2.2e-16 /
  3.5e-16; vs P1 Rust 4.1e-25; vs Python 0.0. PASS.
- G3 harness: (a) Mn-56 evaluator vs hand 0.0; (b) `.out` TOTAL HEAT → kW/kg vs `.nuclides` Total 2.2e-16 over
  2,379 points (readers and units verified on every experiment); (c) 132 compositions: abundance sums exact, mass
  balance 2.2e-16 g. Reader fixes before scoring: `.nuclides` header tokens are space-padded symbols ("H   3") —
  regex parse; natural Ta-180 is the isomer (openmc `Ta180_m1`) — composition keeps LISO.
- G4 first run used the pre-repair library; discarded; rerun launched with the rebuilt library.
- G5: maturin needs VIRTUAL_ENV; build relaunched.

## 5 — 2026-08-25 — G4 method repair (Amendment B) and harness alignment (Amendment C)
- G4 run 1 (post-library-repair): 132/132 ran, 0 errors, but 55 experiments returned non-finite ACTINV C/E and the
  C/E-reproduction control failed ×180. Diagnosis: reachable networks of 1,444 states; CRAM absolute error
  ~1e-16 × ‖n‖ with ‖n‖ = the stable bulk (~1e22 atoms/g) → ~1e6 atoms/g of signed round-off on unpopulated
  products with λ up to 1e22 s⁻¹ → spurious/negative heat of the signal's order. The stored inventories excluded
  negatives while the heat included them — the reproduction control caught the inconsistency.
- **P2 Amendment B** (sha 3d850970…): trace-activation formulation — bulk composition as a constant source through
  a unit state; products solved by CRAM; negative components zeroed and ledgered; bulk natural radioactivity as a
  source plus its constant heat reported separately; validity recorded as max burn-up fraction per experiment.
  Run 2: burn-up 1e-12…1e-10 on every experiment; zeroed round-off ≤ 8e-5 atoms/g (products ~1e10); Al 5-min
  ACTINV C/E 0.972 vs FISPACT-II 0.963 with Mg-27 1.072 vs 1.064 μW/g; Co 5-min 1.032 vs 1.035 (Mn-56 0.02826 vs
  0.02820 μW/g); Br 1.028 vs 1.014.
- Findings in run 2 that are the data, not the code: Al 1996exp_7hour at 13–50 d — measured ~5e-5 μW/g floor vs
  both codes 1e-6…1e-9 (ACTINV and FISPACT within 10 % of each other); Bi 1996exp_5min has measured rows ≤ 0.
- **P2 Amendment C** (sha 2eb19cf6…): alignment by time with unit inference, exclusion of
  non-positive measurements (ledgered), `.nuclides` name regex tolerant of unspaced names ("Ir194", "Au196n").
  Run 3 launched; its records are the scored ones. G4 has used its repair round (B); C is a reader rule.

## 6 — 2026-08-25 — G4 final run, verdict, close
- Final G4 run (Amendments B + C, unit inference excluding padded zero rows): **132/132 experiments, 0 errors,
  0 unmatched experiments**; every C/E reproduced by `check_p2.py` from the stored inventories to 3.8e-16; wall 50 s
  for all 132 (solver ≤ 92 ms per experiment, ~1,440-state pruned networks); max burn-up fraction 6.6e-10
  (trace formulation valid everywhere). 47 measured rows excluded and ledgered (padded zeros, heat ≤ 0, no step).
- **Accuracy (reported, not gated):** median geometric-mean C/E ACTINV **1.024** vs FISPACT-II/TENDL-2017 **1.009**;
  median max|ln C/E| 0.284 vs 0.223; experiments with max|ln C/E| ≤ ln 1.3: 47 % vs 52 %. ACTINV tracks FISPACT
  within 20 % at every measured point in 103/132 experiments; geometric-mean C/E within 10 % of FISPACT's in 88/132;
  corr(ln C/E) 0.79; median |ln(gm_A/gm_F)| 0.06. Dispositions: AGREE-MEAS 41, AGREE-REF 32, DISAGREE 59 — of the
  59, both codes are > 30 % off the measurement somewhere (measurement-driven: late-time calorimeter floor in the
  7-hour series (Al, V), Tb/Dy/Ba patterns identical in both codes). ACTINV > 30 % off where FISPACT is within 30 %:
  11 mild cases (Ag, Hg, Inc600, K, Mo, Na 7h, Nb, Pt, Re, Se, Sr; 0.27–0.37 vs 0.03–0.25); the reverse: 4 (Re 5-min,
  S, Sb, Ta). Diagnostic trigger not fired (0.284 − 0.223 < ln 2).
- Library-difference findings (EAF-2010 + ENDF/B-VIII.0 vs TENDL-2017 + UKDD): Bi — Tl-206m from Bi-209(n,α)
  6.7e-5 vs 2.0e-3 μW/g and Bi-210 6.4e-5 vs 1.9e-5 μW/g (ACTINV closer to measurement at early times in
  2000exp_5min: C/E 1.1–1.6 vs 3.0–4.8); Tb-158m 3.0e-2 vs 1.5e-2; Sc-47 in V 1.5e-4 vs 3.7e-5. Oxide samples show
  N-16 from O-16(n,p) as the top early contributor in both codes (Tb, Dy, Ca).
- Ledger totals over 132 experiments: 2,640 product-without-decay-record events (the same 20 EAF products lack an
  ENDF/B-VIII.0 decay record — listed per experiment; to be resolved in P3 with a second decay source), 11,243
  bulk-production terms dropped (constant-bulk approximation, rates recorded), 0 composition isotopes absent,
  0 nuclides without mean-energy data.
- `.problem` files (regenerable by `controls/run_fns.py`, 238 MB) deleted before the manifest; `.result` and `.json`
  records kept. Figures: results/fns_figures/summary.png, ce_all.png. Report: results/FNS_REPORT.md.
- `controls/check_p2.py` → **P2-CONDITIONAL** (G1–G5 PASS; G4 after its repair round). Session closed; manifest
  regenerated once; local git commit. No external contact; no remote repository.
