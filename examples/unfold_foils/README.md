# unfold_foils: what spectrum explains these foil activities?

`actinv unfold` fits the 709-group spectrum of `problem.json` (the JAEA-FNS iron spectrum of
`examples/fns_fe_5min.json`, used as the prior) to the activities of a nickel and an aluminium foil, irradiated for
one hour and counted after one hour of cooling, and to one reaction rate. **The measured values in
`measurements.json` are illustrative**, chosen to be consistent with a spectrum somewhat harder than the prior.

```sh
actinv data fetch
actinv unfold examples/unfold_foils/problem.json examples/unfold_foils/measurements.json unfold-result.json
```

Expected runtime: about 20 seconds once the data are installed (four foil solves). It prints
`chi-square 4.074 on 4`. In `unfold-result.json`, look at `bands`: the flux above 10 MeV is determined to about 2%,
the 1–10 MeV band moves up and narrows a little, and below 1 MeV the result is the prior, because no measurement
responds there. Each entry of `measurements` gives its pulls and response window.

See [Unfold a neutron spectrum from foils](../../docs/guide/unfold.md).
