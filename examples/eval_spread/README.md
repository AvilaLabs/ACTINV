# eval-spread: how much does the choice of decay evaluation matter?

`actinv eval-spread` solves `problem.json` (1 g of iron, FNS 14 MeV, 5 minutes of irradiation, then 1 d and 30 d of
cooling) twice, once with ENDF/B-VIII.0 decay data as primary and once with JEFF-3.3 as primary, and reports the
spread of every quantity in log space. It needs `decay.fallback`, which the problem declares.

```sh
actinv data fetch
actinv eval-spread examples/eval_spread/problem.json spread.json
```

Expected runtime: two solves of a one-element problem, seconds once the data are installed. Not timed.

In `spread.json` look at `per_nuclide_activity`, `per_heat_component`, `per_response_nominal` and
`suggested_unmodeled_relative` (equal to `max_abs_ln_ratio`, the largest observed spread). It is a candidate value for `uncertainty.unmodeled_relative`, not an uncertainty by itself.
