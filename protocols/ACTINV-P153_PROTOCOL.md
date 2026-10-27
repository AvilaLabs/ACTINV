# P153 — Fission-yield selection: rate-weighted energy mixing, actinide selection, cumulative yields (FISPACT-II `FISYIELD`, `CUMFYLD`, `FISCHOOSE`, tracker row 17)

Registered 2026-10-27, before any source change for the phase. Second parity wave; manual reading in
`docs/maintainers/FISPACT_MANUAL_KEYWORD_AUDIT.md` (P151).

## Change under test

Today `fission_yields.energy` interpolates each parent's independent yields once, at a fixed energy or at the
spectrum's fission-weighted mean energy. In a mixed spectrum (thermal plus 14 MeV) a single mean energy can sit
where little fission happens. FISPACT-II mixes the tabulated yield sets, each weighted by the fission rate in its
energy range. Three opt-in additions; without them every output stays byte-identical.

1. **`fission_yields.energy: "rate_weighted"`.** For each parent and each distinct step spectrum, the yield vector is
   Y = Σ_k w_k Y_k over the tabulated incident energies E_k, with w_k = Σ_g r_g h_k(E_g) / Σ_g r_g, r_g = φ_g σ_f,g the
   parent's fission rate in group g, E_g = √(E_lo E_hi) the group's lethargy midpoint and h_k the piecewise-linear
   hat functions on the E_k (h_1 = 1 below E_1, h_K = 1 above E_K). This is the expectation over the fission-rate
   distribution of the same linear interpolation `effective()` applies at one energy, so a spectrum confined to one
   group reproduces `"fixed"` at that group's midpoint. Yield standard uncertainties combine as √(Σ w_k² σ_k²). The
   certificate records w_k per parent and spectrum.
2. **`fission_yields.parents`**: `{"include": [...]}` or `{"exclude": [...]}`. A parent outside the selection keeps
   its fission reaction, and its whole fission rate goes to leakage under the new ledger key
   `fission_unselected_to_leakage` (per parent), never silently dropped. Naming a parent with no yield file, or a
   nuclide that is not fissionable in the library, is refused.
3. **`fission_yields.yields: "cumulative"`** (default `"independent"`). MT=459 tables replace MT=454 in the rate
   matrix. Because a product then also receives atoms through its in-chain precursors, the ledger key
   `fission_cumulative_overlap` reports per parent the summed cumulative yield and the yield share of products that
   have an in-chain decay precursor also receiving a yield (the overlap that can be counted twice).
4. **`pathways.fission_parents: [...]`** limits which fission parents route attribution may pass through; the
   inventory is unchanged, and attribution through other parents is reported as unexplained.

Beyond FISPACT-II: the mixing is continuous (piecewise-linear, as the evaluation interpolates) instead of binned,
done per step spectrum and reported; unselected fission is booked to leakage with its rate; cumulative mode reports
the overlap it introduces.

## Gates

Checker: `controls/check_p153.py` (with `--self-check`). Reference: v1.6.1 release (`79e1e860…9c072`). Data: an
activation library containing uranium (as P149 Amendment E), ENDF/B-VIII.0 neutron fission yields (hash-pinned).

- **G0.** This protocol's SHA-256 is in `protocols/protocol_hash.txt`.
- **G1.** fmt, clippy `-D warnings`, workspace tests including new yield-mixing, selection and cumulative tests.
- **G2, defaults unchanged.** `examples/fns_fe_5min.json`, `examples/shielding_demo.json` and a U-235 fission case
  with the existing `"spectrum_average"` setting byte-identical to the reference apart from `ms`.
- **G3, rate-weighted mixing.** U-235 and U-238 in three spectra (thermal Maxwellian, FNS 14 MeV, and their mix with
  equal fission rates): w_k recomputed independently from the library fission rows and the spectrum, and the mixed
  yields used by the run (`inspect` or certificate) reproduced from this script's own MT=454 read, within 1e-12
  relative. A one-group spectrum reproduces `"fixed"` at the group midpoint exactly. For the mixed spectrum the
  difference from `"spectrum_average"` in the five largest fission-product activities is reported (information).
- **G4, selection.** With U-238 excluded, its fission rate equals `fission_unselected_to_leakage` within 1e-12
  relative, and the U-235 products equal those of a run where U-235 is the only yield file; include and exclude forms
  of the same selection give byte-identical results; each refusal names its cause and writes nothing.
- **G5, cumulative.** The yields in the matrix equal this script's MT=459 read; `fission_cumulative_overlap` is
  recomputed independently from the chain and yields.
- **G6, pathway parent filter.** No route passes through an excluded parent; the inventory is byte-identical to the
  unfiltered run; route closure accounts for the excluded attribution as unexplained.
- **G7.** CI green on the pushed commit; committed baselines unchanged.

## Verdict

P153-PASS if G1–G7 hold. A failing gate is preserved under `results/failures/p153/`; one repair round, registered as
an amendment first.

## Not claimed

The mixed yields inherit the evaluation's incident-energy grid (typically three energies) and its linear
interpolation; no yield model beyond the evaluation is added. Cumulative mode remains an approximation whose overlap is
reported, not corrected. Multi-chance fission yields not in the evaluation are not constructed.
