# ACTINV P1 — feasibility gates carried from ACT-P0, missing-data ledger v0, repository skeleton

**Program:** ACTINV — open, standalone, activation-grade nuclide-inventory solver (Avila Labs).
**Principal's decisions (2026-08-25):** name ACTINV; build the solver first (A), the code-agnostic
validation harness second (B); overlap with existing codes is acceptable — ACTINV competes on being
installable, runnable from public data, fail-closed, and fast. Licence proposed MIT OR Apache-2.0
(pending the principal's confirmation; no LICENSE file is written before it).
**Opened:** 2026-08-25. **Owner of external acts:** principal. **Time box:** two calendar days from hash.
**Predecessor:** Avila-Labs/scouting/act-p0 (STOP-GAP-CLOSED; G-B2 and G-C unscored — they are P1-G1/G2).

## 0. Standing
House rules: protocol → hash → evidence; verdicts derived by `controls/check_p1.py`; append-only
`ledger.md`; `MANIFEST.sha256` once at close; no external contact, no publishing, **no remote repository**
(local `git init` only); reads of public data only, each recorded with URL, size, SHA-256; nuclear data
live under `~/nuclear-data/`, never inside the repository. Design rule from decision A-then-B: every
benchmark runner accepts any code's inventory output (ACTINV, ALARA, FISPACT-II, OpenMC) — the
harness is code-agnostic from its first line.

## 1. Gates

### P1-G1 Data route B′ (from ACT-P0 G-B2, Amendment B)
Own ENDF-6 parser for MF=3 (TAB1 with NBT/INT interpolation regions), MF=9 and MF=10; EAF-2010 files for
Fe-56, Ag-107, W-186 (IAEA mirror, already local with SHA-256). Spectrum: FNS Fe `1996exp_5min_fluxes`,
709 groups (descending), boundaries from `pypact.ALL_GROUPS[709]` (Apache-2.0), provenance written to
`results/spectrum.json` before any collapse. Intra-group flux shape: flat in lethargy (recorded).
One-group σ̄ for Fe-56 {102,103,107,16,105,104,28,22,32,111}, W-186 {102,16}, Ag-107 102 split into
Ag-108 ground / Ag-108m by MF=9.
Controls: (1) own σ̄ vs σ̄ from `openmc.data.IncidentNeutron.from_endf` on the same file, same grid, same
integrator ≤ 1e-6 relative for every reaction; (2) Ag-107 isomer split vs openmc product yields ≤ 1e-6;
(3) TENDL-2023 Fe-56: parser reports LRP=1 and the resolved-resonance upper energy — reconstruction
is recorded as required, not attempted in P1.
Verdict: G1-PASS / G1-FAIL (any control > 1e-6 after one append-only repair round).

### P1-G2 Solver core (from ACT-P0 G-C)
Bateman matrix over all 3,821 ENDF/B-VIII.0 decay materials (own parser, P0 G-B1) — daughters resolved
from RTYP/RFS to (Z,A,LISO); spontaneous fission and any unresolved daughter go to an explicit
"leakage" row, never dropped silently — plus Fe-56 reaction columns from G1 at 1e14 n cm⁻² s⁻¹.
Schedule: 1 y irradiation, cooling 1 d / 1 y / 100 y.
CRAM-16 (coefficients from `results/cram_coefficients.json`, Pusa 2016) with an OWN sparse complex LU
(CSC, partial pivoting permitted). Python reference `controls/cram_ref.py` first; then Rust crate
`crates/actinv-core` with no external linear-algebra crate (`num-complex` permitted).
Controls: (1) 3-nuclide analytic chain ≤ 1e-10; (2) dense `scipy.linalg.expm` on the full matrix ≤ 1e-6
relative for nuclides with N > 1e-15 ΣN; (3) Rust vs Python ≤ 1e-12; (4) atom conservation to 1e-12 with
leakage reported.
Timing: wall clock per CRAM-16 step (8 solves), Rust release, one thread: PASS ≤ 10 ms;
MARGINAL 10–100 ms; FAIL > 100 ms.

### P1-G3 Missing-data ledger v0 (the differentiator, first planted-failure control)
Every solve emits a ledger: nuclides present in the chain with no decay data; reactions with no cross
section for the spectrum; daughters not in the library; atoms sent to leakage — each with the fraction of
total atoms and of total activity affected at every output time.
Planted failure: delete one radioactive nuclide's decay record from a copy of the library (seeded choice,
seed 20260825, from the Fe-56 chain's actual products) and rerun; the ledger MUST name it and the
activity fraction attributed to it MUST be reported. If the ledger stays silent → G3-FAIL.

### P1-G4 Repository skeleton (setup, recorded)
`~/Documents/actinv`: Cargo workspace (`actinv-core`, `actinv-data`, `actinv-cli`), `python/` (PyO3 later),
`protocols/`, `controls/`, `results/`, `docs/`; `git init` local only; `.gitignore` excludes data and
results binaries; README states purpose, licence (pending), and the no-bundled-data rule.

## 2. Verdict (`controls/check_p1.py`)
P1-PASS: G1, G2 controls, G3 all pass, timing PASS or MARGINAL. P1-CONDITIONAL: G1 or G3 pass with G2
MARGINAL and one control repaired. P1-FAIL: any gate failing after its repair round. UNSCORED: time box.

## 3. Honesty clause and boundaries
Results as measured; failing controls recorded; estimates labelled; no claim about ACTINV's accuracy
follows from P1 — accuracy is P2's FNS gate. No external contact; no remote repository; no publication;
writes confined to `~/Documents/actinv`, `~/nuclear-data`, `~/.cargo`, `~/.rustup`, the scratchpad,
and `~/Documents/Avila-Labs/STATUS.md` at close.
