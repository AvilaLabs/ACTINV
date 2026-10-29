# reverse-qualified: what flux explains the measured activities, and can it be trusted?

`actinv reverse-qualified` fits the irradiation multiplier of the one-segment foil problem to the three measured
activities of `examples/reverse_demo_measurements.json` (Mn-56, Fe-55, Mn-54; produced by a forward run with
multiplier 2.0), adding the nuclear-data covariance of the model to the measurement errors, and records the checks
that qualify the estimate. `problem.json` is `examples/reverse_demo_problem.json` with a library `catalog:`
reference and an `uncertainty` block that bands the three measured activities, which the qualified mode requires.

```sh
actinv data fetch
actinv data fetch tendl-2025-neutron-covariance
actinv reverse-qualified examples/reverse_qualified/problem.json examples/reverse_demo_measurements.json qualified.ndjson
```

Expected runtime: one forward run per irradiation segment (here one), seconds once the data are installed. Not timed.

`qualified.ndjson` is `actinv-reverse-qualified-1`, one JSON record per line. Look at the `estimate` record's `multiplier`
(expect about 2.0) and `posterior_sigma`, the `identifiability` record's `resolvable` flag, and the `consistency`
record. For the plain least-squares fit of the same data, use
`actinv reverse examples/reverse_demo_problem.json examples/reverse_demo_measurements.json`.
