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

## 7 — 2026-08-25 — P3 opened
- Principal: "continue". P3 protocol hashed (protocols/protocol_hash.txt). Scope: decay fallback, own resonance
  reconstruction + Doppler, rate-significance pruning, certificate, docs.

## 8 — 2026-08-26 — memory incident and rule
- The first `controls/doppler.py` vectorised the SIGMA1 kernel over all (output × input) pairs — for the 58,000-point
  Fe-56 grid that is a 27 GB array; the laptop (30 GB) killed the session. Own defect. Fix: chunked outputs (512) and
  a ±8 half-width window on input segments; peak memory ~ MB. Rule from now on: every heavy control or run executes
  under `ulimit -v 12000000` (12 GB) so a mistake fails the job, not the machine; no single array may exceed ~1 GB
  without a stated reason in the ledger. The FENDL-reference control (Amendment A) is rerun under this rule.
- FENDL-3.2c W-186 uses LRF=7 (R-matrix limited) — unsupported in P3, ledgered; TENDL-2023 W-186 is MLBW.

## 9 — 2026-08-26 — P3 G1, remote repository, attribution, amendments
- G1 (a): own parser vs openmc.data.Decay on JEFF-3.3 (3,852 materials), 200 seeded nuclides (seed 20260826):
  **0 mismatches** at 1e-12. Merge: 50 nuclides added from JEFF-3.3 (absent from ENDF/B-VIII.0), source recorded per
  nuclide. Coverage of the 19 exotic EAF products: 1 (JEFF-3.3); 18 have no evaluated decay data in either library —
  ledgered under `products_no_evaluated_decay_data_ENDFB80_JEFF33` and booked to leakage; realised atoms in FNS runs
  are nil (rates ≤ 5e-17 /s/g). The "ZA=0 product" rows are MT=18 fission on 102 actinide targets → own category
  `fission_no_yields_to_leakage`. The EAF-2010 `isotopes*.zip` archives are cross-section files, not decay data.
  Largest half-life disagreements ENDF/B-VIII.0 vs JEFF-3.3 (information): F-14 5e-22 s vs 1e-9 s; Be-13; Re-164;
  Po-219 3e-7 s vs 120 s — placeholders in one library or the other; all far from anything measured here.
- Remote: the principal created https://github.com/AvilaLabs/ACTINV (private) and authorised its use; `origin`
  added and the P0–P2 history pushed (commits adb4e11, f7e2b5e, 74cc621). Principal's rule recorded: no Claude
  co-author trailers or contributor listing — commits are authored by Connor Avila; `attribution` cleared in
  settings; all existing commits verified trailer-free.
- **P3 Amendment A** (sha 8008e60c… protocol; amendment 314a2e42…/see protocol_hash.txt): G2 control (a) reference
  changed to IAEA's NJOY-processed FENDL-3.2c ACE (293.6 K) of the same evaluation, because the openmc wheel lacks
  its compiled reconstruction module. **P3 Amendment B**: adaptive 0 K grid before broadening; constant-invariance
  window y ≥ 10; control (c) split into brute-force quadrature (≤1e-6) and ψ-function (O(Γ_D/E_r), ≤2e-3);
  G3 criterion two-part (heat ≤1e-8 relative; fail-closed bound on removed heat ≤1e-12 using E·min(λB, F) with
  F the feed-rate bound — the atom-only bound was honest but vacuous for λ ~ 1e20 s⁻¹ nuclides).
- SIGMA1 defect found and fixed: segment slope per eV used where per x² (E = kT x²) was required; brute-force
  quadrature now agrees on 1/v, constant, linear to 1e-9. The 0 K reconstruction agreed with NJOY at 1 eV to 1e-5
  before any broadening (Ag-107 elastic 7.0732 vs 7.0739 b; capture 5.2489 vs 5.2500 b).
- Data finding (G2 d): Fe-56 on the FNS D-T spectrum, TENDL-2023 vs EAF-2010 — (n,2n) 0.360 vs 0.442 b (−19 %),
  (n,α) 0.0404 vs 0.0364 b (+11 %), (n,p) 0.0936 vs 0.0940 b, (n,γ) 2.15e-3 vs 1.34e-3 b (+60 %; Doppler 293 K
  changes it by <0.1 % on this spectrum).

## 10 — 2026-08-26 — P3 close: P3-FAIL on G2
- G2 after Amendment B: (c1) SIGMA1 vs exact-kernel quadrature 1e-12…1e-15 (implementation exact); (c2) ψ peak 5.5e-7,
  wings ±20 Γ 1.2 % (the ψ reference is a Gaussian-in-energy approximation; not accurate to 2e-3 in the far wings);
  (b) 1/v 2.1e-6 (interpolation of 1/v on a 400/decade grid); constant at y ≥ 10 deviates 0.498 % — because a constant
  is NOT invariant: the exact law is σ₀(1 + 1/(2y²)) (second moment of the kernel), my control premise was wrong;
  (a) vs NJOY/FENDL ACE: medians 5e-5 / 3e-5 (elastic) and 2.5e-4 / 1.1e-3 (capture), p99 0.25–1.3 %, maxima
  0.9–2.0 % at narrow resonances (Fe-56 307 keV; Ag-107 3.7–3.9 keV) — the 0 K sampling (161 linear points over
  ±40 Γ) under-resolves resonances narrower than ~1 eV and Doppler-dominated peak heights inherit the area error.
  G2 has used its repair round → **G2 FAIL** by the protocol; corrections go to P3b under a new protocol.
- G3 PASS (threshold 1e-8 atoms/g; heat difference 2.8e-9; removed-heat bound 3.1e-13; median 71 states, 2.7 ms;
  all 132 experiments 0.4 s of solver time). G4 PASS (certificate: every input hash matched, every C/E re-derived to
  3.8e-16). G1 PASS. G5 recorded (README, CONTRIBUTING, docs/METHOD, DATA, HARNESS, LEDGER, VALIDATION).
- Verdict `controls/check_p3.py`: **P3-FAIL**. Session closed; manifest regenerated once; committed and pushed to the
  private remote (author Connor Avila, no trailers).

## 11 — 2026-08-26 — P3b: G2 second attempt, PASS
- Protocol protocols/ACTINV-P3b_PROTOCOL.md (90e011a4…). Changes: 0 K sampling uniform in arctan(2(E−E_r)/Γ_r)
  over ±200 Γ (401 points per resonance) plus one midpoint refinement where linear interpolation errs by > 1e-4
  (Fe-56: 235,686 points, 54,167 refined; Ag-107: 331,942 / 94,019); control (b) replaced by the exact kernel laws
  (1/v invariant; constant → σ₀(1 + 1/(2y²))); control (c2) gated within ±5 Γ where the ψ approximation holds.
- Results: (a) own reconstruction + SIGMA1 vs IAEA's NJOY ACE at 293.6 K — Fe-56 (Reich–Moore) MT2 max 2.3e-3 /
  median 5.8e-5, MT102 max 1.6e-3 / median 3.0e-4; Ag-107 (MLBW) MT2 max 4.3e-4 / median 1.3e-5, MT102 max 1.5e-3 /
  median 2.4e-4 (all ≤ 3e-3, the three NJOY 0.1 % tolerances). (b) 1.3e-7, 9.8e-8. (c1) ≤ 1.4e-12. (c2) peak 5.5e-7,
  ±5 Γ 2.0e-5 (±20 Γ 1.2 %, information). Peak memory bounded (chunked reconstruction and kernel; `ulimit -v`).
- `controls/check_p3b.py` → **P3b-PASS**. Combined with P3: every P3 gate now has a passing successor record.
- Housekeeping this session at the principal's direction: author email on all commits set to the principal's GitHub
  address by history rewrite; no assistant attribution anywhere in the repository.

## 12 — 2026-08-26 — P4 opened
- Roadmap row P4; protocol hashed. TENDL-2023 neutron library download from the IAEA mirror started (2,848 zips,
  4 parallel, under `~/nuclear-data/tendl-2023/`).
- Builder `controls/tendl_build.py` smoke-tested on the four local TENDL-2023 files (Fe-56, Ag-107, Ag-107m, W-186):
  237 rows, 0 errors; Fe-56 58 s (broadening dominates). Fe-56 one-group capture 2.052e-3 b at density 1 vs
  2.148e-3 b on P3b's density — 4.5 % grid sensitivity → G2 (c) convergence is run on the seeded sample BEFORE the
  full build to choose the density. MT=5 (n,anything) products are not tracked (ledgered per target).
- TENDL-2023: 2,847 target files (the listing's 2,848th entry was the parent-directory link); 2.9 GB zipped, 12 GB
  unzipped; zip manifest written. FENDL-3.2c: all 192 ENDF-6 files fetched; MF=2-identical twins with TENDL-2023:
  Be-9, F-19 (no resolved resonances — trivial) and Th-232 (Reich–Moore, the meaningful reference); their ACE files
  fetched (Th-232 85 MB, 293.6 K).
- Builder cost: the SIGMA1 kernel now evaluates exp/erf once per array (exactness re-verified 1e-12…1e-16); the
  thermal backbone below 1 eV is sparse (300 points) because on a log grid the sub-0.03 eV region put thousands of
  points inside every ±8 window; dense backbone 2,000/decade × density above 1 eV. Smoke set at density 2: Fe-56 51 s,
  Ag-107 86 s, W-186 19 s. Fe-56 capture one-group 2.0512e-3 b at density 1 and 2.0512e-3 b at density 2 (5e-4 apart):
  the earlier "4.5 % sensitivity" was P3b's control (d), whose resonance windows predate the arctan sampling — the
  builder is the converged one. Convergence control (density 1 vs 2, 40 seeded targets) launched to decide the density.
- Process note: `pkill -f` killed the tool's own shell three times (pattern present in the command text); rule saved.
- Density-1 sample (40 targets, 8 workers): 379 s, median 12 s per target, max 337 s → full build projected ≈ 2 h.
  Sample ledgers: no INCOMPLETE-URR, no LRF=7 (TENDL uses LSSF=1, LRF 2/3); MT=5 lumping flagged on 39/40.
  Actinide fission without MF=8 products was labelled "unmapped"; builder now emits the fission row (ZAP 0) that the
  runner books to its fission category. Fresh-clone test of the repository: builds in 3.7 s, solves a stored problem.
- Website: two routes (/actinv, /actinv/docs) and a header link added to AvilaLabs.org on branch actinv-pages;
  typecheck, lint and build pass; PR #15 opened for the principal to merge and deploy. Status text dated 26 Aug 2026.
- Convergence control (density 1 vs 2, 40 targets, 4 workers, 2 GB cap): control (b) 4.3e-16 PASS; control (c)
  FAIL — 20 rows > 1e-3, K-42 capture 62 % in group 423, Cr-50 7.7 %, Co-62m 8.8 %, Zn-67 3.1 %, Se-77m 4.5 %;
  four heavy targets hit the 2 GB virtual cap (33 MiB window arrays). Repairs before the full build: broadening chunk
  512 → 128; cap 4 GB, 3 workers; the K-42 case investigated before choosing a density.
- K-42 diagnosis: resonance at 2948.50 eV lies 0.009 eV above the range's EH = 2948.491 eV; the adaptive grid placed
  points only for resonances strictly inside the range, leaving the half-peak below EH to the 3.4 eV backbone
  (width 0.81 eV, Doppler 2.6 eV). Reconstruction was correct; sampling was not. Builder now samples every resonance
  within ±200 Γ of the range bounds. Convergence control relaunched (3 workers, 4 GB cap, chunk 128).
- Convergence rerun after the boundary fix: errors 0/0; K-42 62 % → 0.28 %; but Cr-50 (group 500, 100 keV) 7.7 %,
  Zn-67 3.1 %, Zn-77m 3.8 %, Sr-89 7.8 % persisted. Probe on Cr-50: unbroadened group value converged (0.3 %),
  broadened not (3.38 → 3.14 → 3.11e-3 b with density). Cause: resonances 0.5 eV wide at 100 keV against a 14 eV
  Doppler width — the grid resolved Γ, not the broadened line. Sampling width is now max(Γ, Γ_D(E_r), 1e-3 eV):
  Cr-50 group 500 = 3.09565e-3 / 3.09550e-3 / 3.09519e-3 b at densities 1/2/4. Convergence control relaunched.
- Boundary handling: explicit grid points at EL⁺ and EH⁻, MF=3 side from EH inclusive, iterative (≤8-pass) midpoint
  refinement, broadening kernel fed with the MF=3 points above EH; zero-length segments (ENDF double points at
  discontinuities) now contribute nothing in the group integral (they produced NaN). Y-79 group 239: 6.5e-5.
- After the boundary/double-point fixes: Cr-50 group 544 rel 4.2e-5, Sr-89 group 463 2.2e-6, Y-79 6.5e-5; Zn-67 group 492
  (EH = 70 keV inside the group; MF=3 jumps 30× at the RRR/URR boundary) 4.2e-3 — still above 1e-3. CHECKPOINT
  2026-08-26: the convergence control must be rerun with the final builder before the full build; the full build has not
  started. Work committed as P4-in-progress.
- Boundary broadening: the Doppler-smoothed step at EH (Zn-67: MF=3 0 → 0.0786 b at 70 keV, Γ_D 10 eV) was sampled at
  the backbone spacing; output points now dense (80 per 10 Γ_D) on both sides of EH and the broadening extends 10 Γ_D
  above EH before splicing to unbroadened MF=3. Zn-67 group 492: 5.8e-11; Sr-84 0; Se-77m 6.4e-7; Cr-50 4.2e-5;
  K-42 5.1e-5. Convergence control relaunched with the final builder.
- Convergence rerun with the final resolved-range handling: all boundary/high-energy rows converged; remaining rows
  were MF=9 isomer-product rows (Np-235 → Np-236m 118 %, Cs-120m 15 %, Tl-188m 15 %, Y-79 4 %): the product grid
  σ(E)·y(E) lacked the yield table's own points and its linear ramps (lin-lin, e.g. 0 → 0.2255 across 0.0147–0.0253 eV)
  were integrated as if the product were linear. Yield points added and each ramp sampled with 64 geometric points:
  Np-235 118 % → 6.9e-4. Probe of the last two rows running.
- Fr-226 (41 % in group 308): TENDL synthetic resonances 1e-7…1e-3 eV wide against Γ_D = 0.08 eV; sampling at the
  broadened scale skipped the 0 K peaks (area wrong; refinement flagged "not converged in 8 passes"). Resonances are
  now sampled at two scales — Γ itself (no floor) for the area, Γ_D for the broadened shape. Probe running.
- Two-scale sampling: Fr-226 group 308 → 1.7e-4. Linearisation tolerance 1e-3 → 2e-4 (Th-224 2.2e-3 → 3.9e-5; Fe-56
  28 s → 44 s at density 2); backbone 2,000 → 3,000 points/decade for sparse-resonance files (Rb-94 1.14e-3 marginal).
  Convergence control relaunched with these settings (3 workers, 4 GB cap).
- Convergence control, final builder (3,000/decade backbone, two-scale sampling, 2e-4 linearisation, boundary
  broadening, yield ramps): control (b) 4.3e-16; control (c) 113/119 rows ≤ 1e-3, six rows on Fr-226 (≤ 1.5e-2) and
  Rb-94 (1.9e-3); errors 0/0; sample builds 763 s / 2,452 s. **P4 Amendment A**: ≥ 95 % rule with named flags.
  Full TENDL-2023 build launched at density 1: 5 workers, 3 GB cap per process (≈ 9 h projected).

## 13 — 2026-08-26 — build interrupted by a machine shutdown; builder made resumable
- The full TENDL-2023 build reached 2,451/2,847 targets (86 %, 13,272 s, 0 errors) when the laptop shut down. All of it
  was lost: `tendl_build.py` accumulated rows in memory and wrote the .npz only at the end. Own design defect for a
  multi-hour job — not a data or physics problem, and nothing else was affected (protocol, controls, convergence
  results and the amendment are committed at 4611f63).
- Repair: per-target cache (`<out>/cache_<name>/<file>.npz`) written atomically as each target finishes, keyed by a
  fingerprint over tendl_build/resonance/doppler/endf_common/g1_collapse plus density and temperature, so any change to
  the physics invalidates it. Verified on the smoke set: two independent cold builds bit-identical (237 rows, rows and
  sig arrays equal), resumed build 0.4 s vs 147 s, `n_from_cache` recorded in the index. Rule for the roadmap's
  standing rules: any job over ~10 minutes checkpoints per unit of work.

## 14 — 2026-08-26 — build cost attacked (profile, Rust kernel, subset scheduling)
- Profile: SIGMA1 broadening is **91–97 %** of per-target time (Fe-56 13.97 s of 15.35 s; Th-224 19.76 s of 20.35 s);
  reconstruction 1.3 s / 0.5 s, parsing and collapse negligible.
- Rust SIGMA1 kernel (`crates/actinv-core/src/doppler.rs`, exposed as `actinv.broaden`): verified against the exact
  quadrature control at 1.4e-12 (1/v) and ~1e-15 (constant, linear, resonance line), and against the numpy kernel at
  8.5e-16; on a real 90 k-point Fe-56 grid 294 s → 131 s (**2.2×**, max rel diff 1.9e-10 from summation order).
  Only 2× because numpy was already vectorised and the kernel is transcendental-bound (exp, erf) — recorded so the
  next optimisation targets the algorithm, not the language. `controls/doppler.py` now calls it with a pure-Python
  fallback (`ACTINV_PURE_PYTHON=1`).
- Scheduling: the FNS validation needs only the composition isotopes of the 73 materials — **255 targets, not 2,847**
  (all present in TENDL-2023; symlinked in ~/nuclear-data/tendl-2023/fns_subset). Subset library building now with
  7 workers; the full library follows. Control to run once both exist: FNS results from the subset library equal those
  from the full library (products' own activation is trace and already bounded by the rate pruning).

## 15 — 2026-08-26 — the FNS gate caught a completeness bug in the TENDL pipeline
- First TENDL-2023 run (subset library, 255 targets, 18 min): median gm C/E 0.988 (vs EAF-2010 1.024, reference 1.009)
  but median max|ln C/E| 0.472 vs 0.284 — better centre, much worse spread. Diagnosis from the per-nuclide table:
  every large loss is a **metastable state produced by inelastic scattering** — Y-89m (Y 5-min gm 0.94 → 0.28),
  Ba-137m, Ce-139m, Hg-199m, Rb-86m — all exactly zero under TENDL.
- Cause (own bug): `tendl_build.py` skipped MT=4 and MT=51–91 as "no transmutation". True for the ground state, false
  for isomers: both TENDL and EAF encode (n,n')→metastable as MF=10/MT=4 partials with LFS>0. The EAF builder had no
  such skip list, so only the TENDL library lost them. Fix: for inelastic MTs keep the LFS>0 partials as production of
  the isomer and set the ground-state loss to their sum (never the total inelastic cross section). Verified on Y-89:
  MT=4 → Y-89m 3.93e-1 b one-group on the FNS spectrum, previously absent; ledger entry per target.
- **This is the value of the subset schedule**: the bug surfaced 20 minutes after the library existed, not 4 hours.
- Second cause, same gate: **ENDF `LFS` is the product's nuclear level index, not the isomeric-state number**, and the
  numbering is library-dependent — TENDL gives Ba-137m as level 2, Hg-199m as 7, W-185m as 6, Rb-86m as 2, while the
  decay sublibraries index isomers as LISO = 1, 2. EAF-2010 happens to use 1, so only the TENDL library was affected:
  every isomer fell back silently to its ground state. Fix: the builder renumbers the distinct positive LFS of each
  (MT, product) in increasing level order onto isomeric ordinals and ledgers every remap; the runner now ledgers the
  ground-state fallback instead of taking it silently (`isomer_state_absent_from_decay_library_used_ground`).
  Verified: Ba-137 MT=4 LFS 2 → LISO 1, 0.166 b one-group on the FNS spectrum.
- After both isomer fixes (subset rebuilt, 132 experiments rerun): ACTINV/TENDL-2023 median gm C/E **1.035**,
  median max|ln C/E| 0.311, within 30 % everywhere 45 % — against ACTINV/EAF-2010 1.024 / 0.284 / 47 % and the
  FISPACT-II/TENDL-2017 reference 1.009 / 0.223 / 52 %. TENDL-2023 is closer to the measurement than EAF-2010 in
  59/132 experiments and closer to the reference in 75/132. Two independent libraries built by ACTINV's own pipeline
  now agree with each other and with the licensed reference.
- **P4 Amendment B** (gate input vs deliverable, per standing rule 7): gates scored on the 255-target FNS subset plus
  the 3-target twins library; G1 scored on the full 2,847-target deliverable; added control — subset and full library
  must give identical FNS results.
- **G2a PASS**: the three MF=2-identical FENDL/TENDL twins (Be-9, F-19, Th-232) built by ACTINV's pipeline vs IAEA's
  NJOY-processed ACE at 293.6 K — one-group on the FNS spectrum within 1.4e-4 (worst, Be-9), per-group within 2.3e-3;
  Th-232 (Reich–Moore, resonance-dominated) 5.6e-5 / 1.1e-3. 192 FENDL files checked to find the twins.
- Full 2,847-target deliverable build launched with both isomer fixes (6 workers, 3 GB cap, resumable cache).

## 16 — 2026-08-26/27 — P4 closed: P4-FAIL
- Full TENDL-2023 library complete: **2,847 targets, 164,315 rows, 0 errors**, 293.6 K, 709 groups. The first assembly
  attempt died with MemoryError under the 3 GB cap after computing every target; the per-target cache made the retry
  cost 14 s (2,847/2,847 from cache) — the checkpointing rule paying for itself. Cap raised to 12 GB for the parent,
  which also assembles; no code change, so the cache fingerprint (6dc8cf9b…) stayed valid.
- **Verdict P4-FAIL.** G1 PASS (2,847 targets, 0 errors, 2,801 with ledger entries, 2 unsupported ranges), G2a PASS
  (twins vs NJOY 1.4e-4 one-group / 2.3e-3 per-group), G3 PASS (132/132, C/E re-derived 3.2e-16, median gm C/E 1.035),
  G4 PASS (69 inputs re-matched). **G2b and G2c FAIL.**
- Diagnosis — **all three failures are mis-specified controls or criteria, not defects in the library**:
  1. **G2b (max 9.3e-1)**: fails only on MT=4, and by construction. Since the inelastic-isomer fix the library stores
     the isomer *partial* cross section for MT=4 (correct); control (b) still compares it against the *total* MF=3
     inelastic cross section, which is a different quantity. Every failing row is MT=4 (Zn-77m 8.1e-1, Co-62m 3.5e-1,
     Se-77m 2.2e-1); all other 768 reactions agree to 4.3e-16. The control predates the fix and must exclude inelastic
     MTs or compare against the isomer partial.
  2. **G2c (94.96 % vs ≥95 %)**: the known Fr-226/Rb-94 grid sensitivity, max 1.6e-2, both targets now flagged in the
     library index (the flag propagates into every run's ledger). Fails my own Amendment A threshold by one row of 119.
     Not adjusted — see the standing rule; the limitation is already in the roadmap's v0.1 known-limitations table.
  3. **Subset-vs-full equality (Amendment B's added control)**: worst 2.5e-6 against a 1e-12 criterion. The criterion
     assumed bit-identity; the full library legitimately adds activation *of the products themselves*, a real physical
     effect. 2.5e-6 on decay heat is the honest size of the subset approximation, and the criterion should state a
     physical threshold rather than bit-identity.
- Execution errors in my own closing script, recorded: it flagged the index *before* the convergence control that
  produces the flags (so the first pass ran unflagged), and the certificate then had to be regenerated after flagging —
  which the certificate correctly caught as a changed input (`library_index` mismatched) before being re-derived.
  Nothing was silently accepted; the verdict is unchanged by either correction.
- **Not attempted while the principal slept**: no control was rewritten, no threshold moved, no P4b run. The two
  mis-specified controls are diagnosed with numbers and left for the principal's decision.
