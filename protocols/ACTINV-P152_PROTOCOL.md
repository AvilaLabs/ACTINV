# P152 — Estimated γ spectra with certified dose bounds (FISPACT-II `SPEK`, tracker row 8) and X-ray exclusion (`NOXRAY`)

Registered 2026-10-27, before any source change for the phase. Second parity wave (owner direction 2026-10-27:
close the remaining FISPACT-II gaps and be better where we can). Manual reading: `docs/maintainers/FISPACT_MANUAL_KEYWORD_AUDIT.md` (P151).

## Change under test

Today a nuclide whose MT=457 record gives a mean electromagnetic energy per decay (`E_em`) but no STYP 0 or 9 spectral
content contributes its power to `unrepresented_gamma_power_W_g`, and dose values that need a spectrum are withheld.
Two opt-in photon options are added; without them every output stays byte-identical.

1. **`photon.missing_spectra: "estimate"`** (default `"report"`, today's behaviour). For each such nuclide:
   - **Point estimate.** A photon-number spectrum falling exponentially with energy, n(E) ∝ exp(−aE/E_max) on
     (0, E_max], a = 14, normalised so that its energy equals the nuclide's E_em power. E_max follows the decay mode
     that carries the largest branching ratio: β− twice the mean light-particle energy per decay (MT=457 index 0);
     β+ or electron capture 5 MeV; isomeric transition E_em; any other mode 5 MeV. Beyond the manual's rule, E_max is
     capped by the largest decay Q-value among the nuclide's modes with non-zero branching (when one is given), so no
     estimated photon carries more energy than the decay releases. The spectrum is integrated exactly into the
     run's photon groups and added to the step source; the nuclide moves from the unrepresented to the estimated
     account.
   - **Certified bounds.** Every dose quantity ACTINV computes from the grouped source (contact γ dose proxy,
     `point_source` dose at each point) is linear in group power. For a nuclide with power P and cap E_cap (the
     largest Q-value above, or the top of the group structure when no Q-value is given), its contribution lies in
     [P·min ρ_g, P·max ρ_g] over the groups whose lower edge is below E_cap, where ρ_g is the quantity per unit power
     placed in group g, as ACTINV's own dose model computes it. This holds for any spectrum that conserves the
     nuclide's E_em power and respects energy conservation. Each step reports the represented-only value, the
     estimate and the lower and upper bounds, so the estimate is never presented without its range.
   - **Reporting.** Per step: `estimated_gamma_power_W_g`, the estimated nuclides with their power and their share of
     each dose estimate, and the estimated share of each dose. The point-source dose, withheld today when power is
     unrepresented, is given with its bounds. Ledger and certificate record the request and the model constants.
   - **Inspection.** `actinv inspect decay NUCLIDE --estimated-spectrum` prints the grouped estimate for any nuclide
     that has a mean EM energy, including one that has evaluated spectra, beside its evaluated grouped spectrum.
2. **`photon.exclude_xrays: true`** (default false). STYP 9 content is removed after the nuclide's spectrum is scaled
   to its E_em; the removed power is reported per step as `excluded_xray_power_W_g` and is not counted as
   unrepresented. Heat, activity and inventory are unaffected.

Beyond FISPACT-II: the estimate respects each decay's Q-value; each dose quantity carries a range valid for any
energy-conserving spectrum; the estimator's accuracy is measured over the whole decay library (G5) instead of being
assumed.

## Gates

Checker: `controls/check_p152.py` (with `--self-check`). Reference: the v1.6.1 release binary
(`79e1e860…9c072`). Decay data: the hash-pinned decay file of each case.

- **G0.** This protocol's SHA-256 is in `protocols/protocol_hash.txt`.
- **G1.** fmt, clippy `-D warnings`, workspace tests including new photon estimate and X-ray tests.
- **G2, defaults unchanged.** `examples/fns_fe_5min.json`, `examples/shielding_demo.json` and a photon/dose case with
  unrepresented power byte-identical to the reference apart from `ms`; with `missing_spectra: "report"` and
  `exclude_xrays: false` given explicitly, identical apart from the request echo.
- **G3, estimate exactness.** For every estimated nuclide in at least two cases (an activated-steel cooling case and a
  case dominated by short-lived products), an independent Python reimplementation of item 1 from this script's own
  MT=457 read reproduces E_max and the grouped photon numbers within 1e-12 relative, and the estimated energy equals
  E_em × activity within 1e-12 relative.
- **G4, bounds.** In the same cases, ρ_g recomputed independently from the response data reproduces each nuclide's
  bounds within 1e-9 relative; every reported estimate lies within its bounds; the represented-only values equal the
  `"report"` run's values within 1e-12 relative.
- **G5, whole-library validation.** For every nuclide in the decay file with STYP 0 content, E_em > 0 and a branching
  mode: the estimate is built as if its spectrum were missing (`inspect --estimated-spectrum`), and the contact proxy
  (iron) and point-source dose (air, 1 m) per unit power from its evaluated grouped spectrum are compared with the
  bounds and the estimate. Containment: every evaluated value lies within its bounds, except for nuclides whose
  evaluated lines lie above their own largest Q-value, which are listed as data inconsistencies and excluded.
  Accuracy (information): median and 90th percentile of |ln(estimate/evaluated)| for this estimator and for the
  same estimator without the Q-value cap.
- **G6, X-ray exclusion.** In a case with strong X-ray emitters, `excluded_xray_power_W_g` equals an independent
  STYP 9 power sum within 1e-12 relative; the remaining grouped source equals the full source minus the scaled STYP
  9 lines; heat, activity and inventory are byte-identical to the run without exclusion.
- **G7.** CI green on the pushed commit; committed baselines unchanged.

## Verdict

P152-PASS if G1–G7 hold. A failing gate is preserved under `results/failures/p152/`; one repair round, registered as
an amendment first.

## Not claimed

The point estimate is a model: its accuracy is what G5 measures over nuclides that have spectra, which may differ
from nuclides that lack them. The bounds are certified only within ACTINV's own grouped dose model and the decay
data as evaluated. No build-up or geometry beyond the existing contact proxy and point source is added.
