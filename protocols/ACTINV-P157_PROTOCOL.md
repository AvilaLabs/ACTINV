# P157 — Impurity screening for fusion materials: rankings, limits and worst cases over composition ranges

Registered 2026-10-27, before any source change for the phase. Second 1.7 release phase, its headline. The
uncertainty layer (sampled bands, confidence limits, class across uncertainty and ranges) is a separate phase, P158,
so that this phase qualifies the deterministic part on its own.

## Change under test

A materials engineer asks: *which impurities in this alloy matter, at which cooling times, for which concern, and how
much of each can I tolerate?* Today ACTINV answers parts of this in four commands with four input formats: `budget`
(clearance index only), `waste budget` (Part 61 class only), `waste composition solve` (class over ranges, no
per-impurity view) and `optimize` (a search, not an attribution). P157 adds one command.

1. **`actinv impurities SPEC.json [OUT.json] [--no-verify]`**, input schema `actinv-impurities-1`:
   `base_spec` (an `actinv-spec-1` run whose flux, schedule and data are used and whose `material` is replaced),
   `balance` (the element taking the remainder to 100 wt%), `matrix` (element → wt%), `impurities` (element →
   `{"nominal": wt%, "min": wt%, "max": wt%}`, `min` ≤ `nominal` ≤ `max`, or a number meaning all three equal),
   `targets` (one-based result step numbers) and `endpoints`, each one of:
   - `activity.total` (Bq/g), `heat.total` (W/g), `activity:<nuclide>` (Bq/g);
   - `dose.contact_proxy` (Gy/h, the semi-infinite-slab gamma proxy; needs the base spec's `photon` section);
   - `clearance_index` (IAEA clearance, limit 1, with the bundled or a supplied limits table, as `budget`);
   - `waste_class` with `"class": "A" | "B" | "C"` (U.S. 10 CFR 61.55, as `waste`).
   A scalar endpoint may carry a user `limit`; without one it is ranked and bounded but has no limits.
2. **Method.** At fixed flux, coupled activation is linear in the initial composition (P75b). The command solves each
   element of `matrix`, `impurities` and `balance` once as a pure material. Every activity, heat, clearance-index and
   Part 61 table sum of any composition is then the weighted sum of those runs, with the balance element absorbing the
   change. `dose.contact_proxy` is not linear, because the material's own attenuation enters it: the command forms the
   photon source of a composition as the weighted sum of the pure runs' sources and evaluates the proxy at that
   composition's attenuation, exactly as a run would. It forces and records `mode: coupled` and `prune: reach`, as
   `budget` does.
3. **Output** `actinv-impurities-result-1`, per target and endpoint:
   - the value with the matrix only, at nominal, and at the **worst case over the declared ranges**, with that corner
     named: for a linear endpoint each impurity at `min` or `max` according to the sign of its gradient, which is
     exact; for `dose.contact_proxy` the largest value over all corners of the range box, stated as a corner maximum
     (an interior maximum is not excluded);
   - each impurity's gradient (per wt%, at nominal), its **contribution** (the endpoint at nominal minus the endpoint
     with that impurity removed and the balance element absorbing it) at nominal and at its range maximum, and a
     **ranking** by contribution, with the top three product nuclides behind each impurity's contribution;
   - with a `limit`: each impurity's limit alone, with the others at nominal, and with the others at their worst
     case, each with a status (`limit`, `no response`, `infeasible`), and the joint factor `k` that scales every
     impurity's nominal to the limit. For a linear endpoint these are closed-form; for `dose.contact_proxy` they are
     found by bisection to 1e-9 relative in the impurity's weight fraction;
   - for `waste_class`: the class at nominal and the **worst class over the ranges**, from the Part 61 table sums each
     maximised over the ranges (a conservative bound, since the class is non-decreasing in every sum), with the
     binding table, column and nuclides. Per-impurity class limits are reported as `waste budget` gives them;
   - a `summary` per endpoint: the tightest limit per impurity across targets and its binding step, the impurities
     ranked by their largest contribution across targets, and the worst value or class across targets.
4. **Verification** (default on): the command re-solves the full material at nominal, at every reported worst-case
   corner, and at every reported limit, and writes predicted and solved values side by side (tolerance 1e-6
   relative). Any miss exits 3 after writing the output, as `budget` does. Refused, because the response would not be
   linear in composition: `self_shielding`, `options.screen`, schedule `feed`, and an `uncertainty` section (P158).
5. **Worked example and handbook.** `examples/impurities_ra_steel/`: a reduced-activation ferritic-martensitic steel
   matrix with declared impurity ranges for Co, Ni, Nb, Mo, Cu, Al, N and Ag in the fusion spectrum of
   `examples/optimize_ra_steel`, a declared multi-year irradiation, and cooling times of 1 d, 1 y, 10 y and 100 y.
   Its values are illustrative, not a qualified material specification. A handbook chapter, "Screen a material's
   impurities", walks through it and is linked from `SUMMARY.md`.

Beyond FISPACT-II: an impurity sensitivity or limit there needs a run per variant; here one solve per element gives
every ranking, limit and worst case at every target, each checked by a full solve.

## Gates

Checker: `controls/check_p157.py` (with `--self-check`), written from this protocol without reading the
implementation. Reference: master after P156's merge (or `cf5d3f0` if P157 merges first; the checker records which).
All runs inside the required cgroup.

- **G0.** This protocol's SHA-256 is in `protocols/protocol_hash.txt`.
- **G1.** fmt, clippy `-D warnings`, workspace tests (`cargo check --tests` included).
- **G2, existing commands unchanged.** The `budget`, `waste budget` and `waste composition solve` examples and the
  P79/P108/P110 control inputs give output byte-identical to the reference apart from `ms`.
- **G3, linear attribution.** On three cases (the worked example; a tungsten armour case W + Re, Os, Ta, Mo, Co, Ni;
  a vanadium alloy case V-4Cr-4Ti + Nb, Mo, Al, Si, Cu, Co), the checker runs every pure element itself with `actinv
  run` and recomputes each gradient, contribution, nominal and worst-case value of the linear endpoints (activity,
  heat, nuclide activity, clearance index). The command agrees within 1e-9 relative, and its rankings and top-three
  product nuclides equal the checker's. `dose.contact_proxy` is checked by full solves in G4.
- **G4, limits and worst cases by full solves.** On the same cases the checker solves the full material, with its own
  `actinv run`, at every reported limit (the endpoint equals its limit within 1e-6 relative), at every reported
  worst-case corner (equal within 1e-6), and at all 2^n corners of the impurity box for n ≤ 6 impurities (no corner
  exceeds the reported worst case by more than 1e-6 relative; the worst is attained at the named corner). The
  command's own `verification` block is consistent with these solves. For `dose.contact_proxy` the checker also
  solves the material with each ranked impurity removed, and the reported contributions agree within 1e-6 relative
  and rank in the same order.
- **G5, waste class over ranges.** For each case and target: the reported nominal class equals `actinv waste` on the
  checker's full nominal solve; the reported worst class is at least the class of every corner solve; it equals the
  class of the separately maximised table sums, which the checker recomputes; and it agrees with `waste composition
  solve` on the same ranges where that command accepts the case.
- **G6, refusals.** Each item-4 refusal, a `min` > `nominal`, an unknown endpoint, `dose.contact_proxy` without a
  `photon` section and a `limit` on `waste_class` are refused with a nonzero exit and no output.
- **G7, example and handbook.** The worked example runs, its verification passes, and the handbook chapter's commands
  and output excerpts match a fresh run (the `check_p140_docs.py` walk-through, extended to the chapter).
- **G8.** CI green on the pushed commit; committed baselines unchanged.

## Verdict

P157-PASS if G1–G8 hold. Development runs are disclosed in `results/p157/`. A failing gate is preserved under
`results/failures/p157/`; one repair round, registered as an amendment first.

## Not claimed

Linearity holds for fixed flux without self-shielding or feed (P75b); verification checks each reported number, not
the method in general. Contact dose is a slab screening proxy, not a dose for a geometry; its worst case over a
range is a corner maximum. The Part 61 class is the
nominal U.S. classification, not disposal acceptance, and the worst class over ranges is a conservative bound. The
worked example's composition and history are illustrative. Nuclear-data uncertainty is P158.
