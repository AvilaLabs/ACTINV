# maintenance: when can the crew go in, and what dose does waiting save?

`actinv maintenance` plans two jobs in a port cell after a fusion machine shuts down: cutting out a section of copper
pipe (8 h, two people) and then unbolting a stainless-steel bracket (6 h, three people). It reads the dose rate 1 m
from each part over the year after shutdown, from an ordinary `actinv run` of each, and reports when each job may
start, the dose of every job at each wait, the earliest wait at which the plan is allowed and the wait that costs least.
**All values are illustrative:** the parts, flux, limits and costs are invented, and a point source at 1 m is a screen,
not the field in a real port cell.

The two problems are 5 kg of copper and 20 kg of SS316 after two years at 3e8 n/cm2/s of D-T neutrons, a port-cell
level, then cooled to a year. Each asks for `photon.point_source` at 1 m, which needs a photon response
built from the NIST tables (the build downloads them):

```sh
actinv data fetch
python3 scripts/build_photon_response.py examples/maintenance/photon-response.json --elements all
actinv run examples/maintenance/pipe.json examples/maintenance/pipe-result.json
actinv run examples/maintenance/bracket.json examples/maintenance/bracket-result.json
actinv maintenance examples/maintenance/plan.json examples/maintenance/plan-result.json
```

The problems pin the response's SHA-256. If NIST has changed its pages since, the run refuses the response: put your
file's SHA-256 in both problems. Expected runtime: about 5 s and 20 s for the two runs, under a second for the plan.

In `plan-result.json` (`actinv-maintenance-result-1`) look at:

- `alone`: the pipe job could start at shutdown; the bracket job only after about 21.5 days, when the dose rate 1 m
  from the bracket falls under the 3 mSv/h access limit.
- `waits`: at 1, 7 and 14 days the plan is not allowed (the bracket is above the access limit, and at 1 day a bracket
  worker would also pass the 20 mSv individual limit). From 30 days on it is, and the collective dose falls slowly,
  from 52 to 33 person-mSv by 180 days, because the bracket's dose comes mostly from Co-60 and Mn-54.
- `earliest_wait_d` and `best`: with 1e6 per person-Sv and 2,000 per outage day, every day of waiting costs more than
  the dose it saves, so the best wait is the earliest one.
- `interpolation_check`: dropping every other cooling time moves the answers by up to 4.4%, under the 5% tolerance. A
  coarser cooling grid would be flagged.
