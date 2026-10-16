# P145 evidence inputs

Inputs for `controls/check_p145.py` (protocol `protocols/ACTINV-P145_PROTOCOL.md`). Paths in the specs are relative
to this folder. The checker stages every case under `target/p145/<label>` (a symlinked `actinv-data`, a copy of the
spec and problem, a `keep` folder), strips keys that start with an underscore, and runs the binary there.

## C1: `c1_fns_fe.json`

The FNS iron benchmark (`examples/fns_fe_5min.json`) as one 1000 g general-waste component with 150 cm3 displaced
volume, and a declared external tritium inventory of 1000 Bq at every target step. Targets are steps 1, 6, 11 and 21.
Channels: MF=33 cross sections (TENDL-2025 709-group sidecar, pinned by sha256), flux at 5 % relative standard
deviation, decay constants. 100 samples, seed 145001. Used by U1 (byte comparison with a `study.robustness` run)
and U2 (independent recount of the result).

## C2: `c2_steel.json` and `c2_steel_problem.json`

A stainless-steel-like activated metal (Fe, Cr 18, Ni 8, Mn 1.5, Co 0.05, Nb 0.005 wt percent) with the spectrum,
library, decay files and cooling steps of `examples/optimize_ra_steel/base_spec.json`, irradiated for one year at
10^4 times that spectrum (about 1.1e14 n/cm2/s; the example's 300 s pulse is too short to activate it). Targets are
cooling steps 10 to 13, about 1, 4, 32 and 100 years after shutdown. 100 samples, seed 145002. Used by U1 and U2.

The levels were set before the candidate commit with 20-sample development runs: at 32 years the Table 2 column 2
(B/C) sum of Ni-63 is near 1 (5th to 95th percentile 0.98 to 1.12), and at 100 years the Table 1 sum, dominated by
Ni-59 with a wide MF=33 spread, straddles its Class A limit. Nothing is tuned after the commit. Regenerate with
`python3 controls/p145_c2_make.py` after editing its parameter block.

## C3: calibration, `c3_p90`, `c3_p50`, `c3_p10`

Pure cobalt, 30 d at unit flux then 1 d of cooling, thermal Maxwellian neutrons. Co-59(n,gamma) is the only
reaction with a nonzero rate, so the Table 2 Column 1 sum is set by Co-60 alone. The covariance sidecar is a
synthetic rank-one relative covariance in which the three Co-59 MT102 rows each carry one constant block of value
sigma2, so the sampled multiplier is lognormal, exp(sigma z - sigma2/2). Closed form for the nominal normalized sum
s0 and relative variance sigma2:

    P(s > 1) = Phi((ln s0 - sigma2 / 2) / sigma)

| case | sigma2 | target P(s > 1) | component mass (g) | target s0 |
|------|--------|-----------------|--------------------|-----------|
| p90  | 0.16   | 0.9             | 12.95              | 1.8087    |
| p50  | 0.36   | 0.5             | 8.573              | 1.1972    |
| p10  | 0.64   | 0.1             | 3.537              | 0.4940    |

4096 samples per case, 99.9 % Clopper-Pearson interval, step 2 is the target. The masses are hand estimates
(parameters at the top of `controls/p145_c3_make.py`). U3 requires the closed form evaluated at the s0 the run
actually computes to lie inside the interval, and the placement of that closed form within 0.15 of the target. If
a placement check fails, rescale the mass by (observed s0 / target s0), regenerate with
`python3 controls/p145_c3_make.py`, and commit the result before the evidence run.

`python3 controls/p145_c3_make.py --check-library PATH` verifies that Co-59 is the expected target of a library.

## Files

- `c3_cases.json` lists the three cases, their sidecar hashes and the closed-form text.
- `c3_*.cov.npz` are written deterministically (stored zip members, fixed timestamps).
