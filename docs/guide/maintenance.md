# Plan maintenance after shutdown

After a machine shuts down, crews go in to cut pipes, unbolt brackets and change parts. The dose rate at each job's
place falls as the machine cools, so waiting longer lowers the crew's dose and lengthens the outage.
`actinv maintenance` answers, from dose rates you already have:

- when each job may start, with the dose rate at its place under your access limit for the whole job;
- the dose each worker and each crew receives, at each wait after shutdown you ask about;
- the earliest wait at which every job is allowed and every worker stays under your individual limit;
- with a cost per person-sievert and per outage day, the allowed wait that costs least.

```sh
actinv maintenance plan.json plan-result.json
```

The planner does arithmetic on the dose rates it is given. Their accuracy is their source's, and it never says a plan
is safe.

The **Maintenance** page of the desktop app and the browser does the same from the open result and the files you
add; see [Plan maintenance after shutdown](desktop.md#plan-maintenance-after-shutdown).

## Write a plan

A plan (`actinv-maintenance-1`) names where its dose rates come from, the limits, the jobs in the order they are done,
and the waits to tabulate. This is `examples/maintenance/plan.json`:

```json
{
 "schema": "actinv-maintenance-1",
 "dose_rates": {"runs": [
  {"result": "pipe-result.json", "location": "pipe"},
  {"result": "bracket-result.json", "location": "bracket"}
 ]},
 "access_limit_Sv_h": 3.0e-3,
 "individual_limit_Sv": 2.0e-2,
 "jobs": [
  {"name": "cut out pipe section", "location": "pipe", "duration_h": 8, "crew": 2},
  {"name": "unbolt bracket", "location": "bracket", "duration_h": 6, "crew": 3}
 ],
 "waits_d": [1, 7, 14, 30, 60, 90, 180],
 "cost": {"per_person_Sv": 1.0e6, "per_outage_day": 2.0e3}
}
```

- `access_limit_Sv_h`: a job may run only while the dose rate at its place stays at or under this.
- `individual_limit_Sv` (optional): every worker's dose must stay at or under this.
- `jobs`: each with a `location` (one of the dose-rate locations), `duration_h` and `crew`. Jobs run one after
  another from the wait, and every crew member is present for the whole job.
- `waits_d`: waits after shutdown, in days, to report in full.
- `cost` (optional): without it there is no least-cost wait.
- `interpolation_tolerance` (optional, default 0.05): see [the check](#the-interpolation-check).

Paths are relative to the plan.

## Where the dose rates come from

`dose_rates` takes one of three forms.

**ACTINV runs** (`runs`): one `actinv run` result per location, whose problem asks for a point-source dose
(`photon.point_source`, see the [specification](specification.md#point-source-dose-photonpoint_source)). The planner
reads each step's dose rate at that distance after the end of the last irradiated step. This treats a whole component
as a point at a stated distance: a screen for handling a part, not the field inside a machine.

**A facility twin** (`twin`, `shutdown_s`, `sv_per_gy`): an `actinv twin` result's dose points, whose twin spec gives
them `dose_coeff_gy_cm2_per_photon_h`. Their times run from the start of the problem, so `shutdown_s` says when the
machine shut down. They are in Gy/h of air, and `sv_per_gy` converts them; nothing is converted unless you say how.
Twin dose points are point-kernel estimates with no scatter or buildup.

**A table** (`table`): any other dose rates, such as photon transport results, in an `actinv-dose-map-1` file:

```json
{
 "schema": "actinv-dose-map-1",
 "unit": "Sv/h",
 "source": "OpenMC shutdown dose, decay steps 1-12",
 "locations": [
  {"name": "port-3", "times_s": [0, 3600, 86400, 604800], "dose_rate": [2.1e-2, 1.4e-2, 6.0e-3, 2.2e-3]}
 ]
}
```

Times are seconds after shutdown and must increase; dose rates are in Sv/h and must not be negative.

## What it reports

`plan-result.json` (`actinv-maintenance-result-1`) holds:

- `alone`: each job's earliest start if it were done on its own.
- `waits`: for each wait, each job's start and end, its peak dose rate, the dose of one worker and of its crew,
  whether it is allowed, the plan's collective dose, its largest individual dose, whether the whole plan is allowed,
  and its cost.
- `earliest_wait_d`: the earliest allowed wait.
- `best`: the allowed wait of least cost, laid out like a row of `waits`.
- `interpolation_check`, the inputs with their SHA-256, and notes on the dose-rate source.

Between two tabulated times the dose rate is taken as an exponential, or as a straight line where one end is zero.
That is exact for one decaying nuclide. For several, the interpolated rate is never below the true one, so doses are
not underestimated. Nothing is extrapolated: a plan that needs a dose rate before the first or after the last
tabulated time is refused. Near a flat minimum the least-cost wait can move a long way for a small change in cost; read
the cost of nearby waits from `waits` before relying on it.

## The interpolation check

Every result also solves the plan again with every other tabulated time dropped, keeping the first and the last, and
reports the largest relative change in its answers (`largest_relative_change`, and `at` for which answer). If that is
above `interpolation_tolerance`, `coarse` is `true`: add cooling times around the waits and job lengths that matter
and run the dose rates again. The check errs towards flagging, since dropping half the times moves the answers by more
than the full grid's own error.

## The worked example

`examples/maintenance/` plans the two jobs above: a copper pipe and a stainless-steel bracket in a port cell after two
years at 3e8 n/cm2/s of D-T neutrons, each as a point source at 1 m. Its README gives the commands, including building
the photon response. The pipe job could start at shutdown; the bracket job only after about 21.5 days, so the plan is
allowed from 21.1 days, and waiting longer saves little dose because the bracket's dose comes mostly from Co-60 and
Mn-54. **Its values are illustrative.**

## How it was tested

From the registered test P197:

> `actinv maintenance` plans jobs after shutdown from dose rates tabulated over cooling time: when each job may start,
> the dose of each crew at each wait, the earliest allowed wait and the wait of least cost.
>
> On three dose curves of one, two and three decaying nuclides with exact answers, its doses, start times and least
> costs on a 201-time grid were within 0.0006 of exact, never below the exact dose or start on any grid, and its
> interpolation check flagged every grid where an answer was more than 5% off.
>
> On 7 activated components computed by ACTINV (5 of them held out), plans from a 15-time cooling grid were within
> 0.02 of plans from a 73-time grid wherever the check did not flag the coarser grid (5 flagged).

The five flagged components were also within 2.4%; the check flagged them anyway. P197 tested the planner's
arithmetic, not any dose rate it reads. Dose rates that rise after shutdown, from a daughter growing in, were not in
the test; the search for the earliest start can miss an allowed window narrower than its sampling interval.
