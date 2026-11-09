# Maintenance dose planner — design note

Status: CLI and core built (`actinv maintenance`, `actinv_core::maintenance`); not yet registered or qualified. Owner agreed on 2026-11-09 to build the planned features that need nothing outside
the repository; this is the first.

## The question it answers

After shutdown, a crew has jobs to do inside the machine: replace a module, inspect a weld, swap a target. Each job
has a place, a length and a crew size, and the dose rate at that place falls as the machine cools. Waiting longer
lowers the crew's dose and lengthens the outage. The planner answers:

- When is each job allowed to start (the dose rate at its place is under the access limit)?
- What dose does each worker and the whole crew receive, for a given start?
- How do collective dose and outage length trade off as the wait grows, and which wait minimizes a declared cost
  of dose against a declared cost of outage time?

ACTINV already has everything except the plan itself. Nothing in the repository schedules jobs, sums worker dose or
finds when a dose rate crosses a limit (checked 2026-11-09).

## Inputs

`actinv maintenance PLAN.json OUT.json`, with `PLAN.json` of schema `actinv-maintenance-1`:

```json
{
  "schema": "actinv-maintenance-1",
  "dose_rates": {"twin": "twin-result.json", "shutdown_s": 6.31e7, "sv_per_gy": 1.0},
  "access_limit_Sv_h": 1.0e-4,
  "individual_limit_Sv": 2.0e-3,
  "jobs": [
    {"name": "replace module", "location": "port-3", "duration_h": 40, "crew": 4},
    {"name": "inspect weld",   "location": "port-1", "duration_h": 6,  "crew": 2}
  ],
  "waits_d": [1, 3, 7, 14, 30, 60, 90],
  "cost": {"per_person_Sv": 5.0e5, "per_outage_day": 2.0e5}
}
```

Dose rates come from one of:

- **`runs`**: `[{"result": "module-result.json", "location": "module"}, …]`, one `actinv run` result per location,
  using each step's `photon_source.point_source_dose.gamma_air_dose_Sv_h` (the problem needs `photon.point_source`).
  Shutdown is the end of the result's last irradiated step. This is the dose rate at a stated distance from one
  component, treated as a point: a screen for handling a removed part, not a field inside the machine.
- **`twin`**: an `actinv-twin-1` result, using `dose_points[].steps[].dose_gy_h`. Its times are from the start of the
  problem, so `shutdown_s` is required. Its dose is in Gy/h of air, so `sv_per_gy` is required, and no conversion is
  silent. The twin's dose points are point-kernel estimates with no scatter or buildup, and the result says so.
- **`table`**: an `actinv-dose-map-1` file, the format for dose from any other source (OpenMC photon transport above
  all): `{schema, unit: "Sv/h", locations: [{name, times_s, dose_rate}]}`, times from shutdown. A Python helper on the
  OpenMC adapter will write it from the photon tallies, which today stay in memory.

`location` names a dose point or table location. Jobs run in the listed order, one after the other, starting at
shutdown plus the wait. `cost` is optional; without it there is no optimum, only the curve.

## Method

- **Dose rate between times.** Interpolated as an exponential between two tabulated cooling times (linearly where an
  end is zero). That is exact for one decaying nuclide, and for a sum of decaying nuclides, whose logarithm is convex
  in time, the interpolated rate is never below the true one, so the dose is not underestimated. Ingrowth from a
  parent can break the convexity, which the interpolation check below catches. The planner refuses a time before the
  first or after the last tabulated time; it never extrapolates.
- **Interpolation check.** Every result reports how much the answer moves when every other tabulated time is dropped.
  A large move means the cooling grid is too coarse for the plan, and the result says so instead of passing quietly.
- **Dose of a job.** The integral of the dose rate over the job's interval, in closed form on each piece,
  times the crew for collective dose. Individual dose assumes every crew member is present throughout.
- **Earliest start.** For each job alone, the first start at which the dose rate at its place stays under the access
  limit for the whole job; for the plan, the first wait at which every job does and every worker stays under the
  individual limit. Both are found by sampling between the waits at which a job's start or end meets a tabulated
  time (32 samples per interval) and bisecting from the first feasible sample. A feasible window narrower than one
  sample interval can be missed; with dose rates that fall steadily there is none.
- **Trade-off.** For each wait in `waits_d`: outage length, collective dose and, with `cost`, the total cost; and the
  feasible wait of least total cost, found by golden-section search between the feasible samples around the best one.

## Outputs

An `actinv-maintenance-result-1` result: per job, the earliest start and the dose rate there; per wait, each job's start
and end, its individual and collective dose, the plan's totals and cost; the best wait; the interpolation check; the
source of the dose rates and its stated limits; and the input hashes.

## Not in the first version

- Parallel crews, or jobs that can be reordered. The listed order is the plan.
- Dose that changes during a job because the crew moves or shielding is removed. One location per job.
- Per-nuclide dose rates, which would allow exact decay between tabulated times instead of interpolation.
- Any claim that a plan is safe. The planner does arithmetic on the dose rates it is given. Their accuracy is the
  accuracy of their source.

## Testing (to be registered as P197 before the scored run)

1. **Closed form.** A location whose dose rate is a sum of two decaying exponentials, tabulated on grids from sparse
   to dense. Earliest starts and job doses match the closed form within stated tolerances on the dense grids, and
   the interpolation check flags the sparse ones.
2. **Against ACTINV's own dose.** A twin result from `examples/export_r2s`, tabulated densely and sparsely. Planner
   doses from the sparse grid against an integral of the dense grid.
3. **Refusals.** An unknown location, times out of order, a wait beyond the tabulated times, a Gy input without
   `sv_per_gy`, a negative or zero duration or crew.
4. **The workbench page and the CLI give the same result** on the example.

The tolerances and the problem set get fixed in the registration, before the scored run.

## Surfaces

The CLI command, a **Maintenance** page in the workbench (jobs as an editable table, the trade-off curve as a chart,
and the best wait highlighted), a handbook chapter with a worked example under `examples/maintenance/`, and the Python
helper for OpenMC dose maps.
