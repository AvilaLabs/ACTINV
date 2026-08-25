# ACTINV P1 — session close, 2026-08-25

**Protocol:** protocols/ACTINV-P1_PROTOCOL.md (c2234d07…), Amendment A (910ea789…), Amendment B (d69c2d19…).
**Verdict (controls/check_p1.py): P1-PASS.**

| gate | result |
|---|---|
| G1 data route B′ | PASS — own MF=3/9/10 parser on EAF-2010, FNS 709-group collapse; 16/16 comparisons vs openmc.data at relative 0.0 |
| G2 solver core | controls PASS (analytic 2.2e-15; dense expm on reachable sub-network 1.3e-11; Rust = Python 0.0; conservation 6.7e-16); **8.44 ms/step** Rust, one thread, full 3,822-nuclide matrix |
| G3 missing-data ledger v0 | PASS — planted deletion named and quantified; supplementary: Mn-56 carried 48.7 % of shutdown activity |
| G4 skeleton | Cargo workspace builds; local git only |

Repairs (append-only): control (2) domain (Amendment A); Smith complex division + scoring rule (Amendment B).
No external contact; no remote repository; no data in the repository. Licence file pending the principal.
