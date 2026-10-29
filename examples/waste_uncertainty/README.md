# waste uncertainty: how likely is each Part 61 class under nuclear-data uncertainty?

`actinv waste uncertainty` solves `problem.json` (1 g of iron, FNS 14 MeV, 5 minutes of irradiation, then 1 d, 30 d
and 1 y of cooling) once nominally and 72 times with correlated draws of the MF=33 cross sections and the decay
constants, classifies every draw as a 1 kg activated-metal component, and reports class probabilities at steps 2 to 4.
72 is the smallest sample count that can establish a class at confidence 0.95; the command accepts 20 to 4096, and
with fewer than 72 it can only report `not_established`.

```sh
actinv data fetch
actinv data fetch tendl-2025-neutron-covariance
actinv waste uncertainty examples/waste_uncertainty/uncertainty.json uncertainty-result.json
```

Expected runtime: 73 solves of a one-element problem, seconds to a minute after the data are installed (the first
run also builds the prepared-data cache). Not timed.

Look at `components[0].targets[]`: `established_class`, `classes` (count, probability and Clopper-Pearson interval per
class), `failed_samples` (should be empty) and `needed_samples` when nothing is established. Pure iron carries no
Part 61 nuclide of concern at FNS energies, so expect Class A with all samples agreeing; the example shows the
command and its output shape, not a classification-sensitive case. The probabilities cover only the sampled
channels.
