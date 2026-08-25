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
