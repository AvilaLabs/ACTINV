# ACTINV-P197 — `actinv maintenance`: planning maintenance jobs against shutdown dose rates

## Purpose

After a machine shuts down, crews do jobs inside it: cut a pipe, unbolt a bracket, change a module. Each job has a
place, a length and a crew. The dose rate at that place falls as the machine cools, so waiting lowers the crew's
dose and lengthens the outage. P197 adds `actinv maintenance`, which answers from dose rates tabulated over cooling
time:

- when each job may start (the dose rate at its place stays under an access limit for the whole job);
- the dose of each worker and of each crew, at each wait after shutdown;
- the earliest wait at which every job is allowed and every worker stays under an individual limit;
- with a cost per person-sievert and per outage day, the allowed wait of least cost.

The owner agreed on 2026-11-09 to build the planned features that need nothing outside the repository; this is the
first. The design is `docs/maintainers/MAINTENANCE_PLANNER.md`.

P197 tests the planner's arithmetic, not the dose rates it is given. Their accuracy is their source's.

## Disclosure of what has been seen

- Unit tests in `actinv_core::maintenance` and `actinv-cli`'s `maintenance` module: a one-nuclide curve on three
  times, a two-nuclide curve (half-lives 0.6 d and 70 d, amplitudes 3e-2 and 2e-4 Sv/h, the same as G2's `two`) on
  401 and 5 times, two locations with costs, an individual limit, zero dose rates and the refusals. Their values were
  read in full.
- The example `examples/maintenance/` (a 5 kg copper pipe and a 20 kg SS316 bracket after two years at 3e8 n/cm2/s
  of D-T neutrons, a point source at 1 m, cooled on 15 times to a year) was run and its plan result read in full,
  including its interpolation check (4.4% at the collective dose at 180 d). These are G3's two development
  components, on the 15-time grid. Their 73-time runs have not been made.
- Before it, the same two parts at first-wall levels (20 kg EUROFER97 at 3e14 and 5 kg copper at 1e14) were run and
  their point-source doses read (thousands and hundreds of Sv/h at 1 m); no plan was made with them.
- One bug was found and fixed on the example before this registration: the least-cost wait came out a sample point
  after the earliest wait when cost rises with waiting; the earliest wait is now a candidate.
- `controls/p197.py rehearse` (the one-nuclide curve on the 9-time grid, the two development components on both
  grids, and the refusal cases, into `target/p197-rehearsal`) was run once on the candidate's release build and read
  for exit statuses and whether every dose is present only: both plans exited 0, the ten refusal cases exited 2, and
  both components had every dose on both grids, with every run and plan exiting 0. No answer from it was read.
- None of G2's two- and three-nuclide curve answers on G2's grids, and none of the five held-out components, has been
  run before the scored run.

## The command

```
actinv maintenance PLAN.json [OUT.json]
```

`PLAN.json` (`actinv-maintenance-1`) holds `dose_rates`, `access_limit_Sv_h`, optional `individual_limit_Sv`, the
`jobs` in order (`name`, `location`, `duration_h`, `crew`), `waits_d`, an optional `cost` (`per_person_Sv`,
`per_outage_day`) and an optional `interpolation_tolerance` (default 0.05). `dose_rates` is one of:

- `{"runs": [{"result", "location"}]}`: `actinv run` results whose problems ask for `photon.point_source`; each
  step's point-source dose after the end of the last irradiated step;
- `{"twin", "shutdown_s", "sv_per_gy"}`: an `actinv twin` result's dose points, converted from Gy/h with the stated
  factor;
- `{"table"}`: an `actinv-dose-map-1` table in Sv/h, times from shutdown.

Between tabulated times the dose rate is an exponential (linear where an end is zero), which is exact for one
nuclide and, for a sum of decaying nuclides, never below the true curve. Nothing is extrapolated. Jobs run one after
another from the wait. Earliest starts and waits are found by sampling between the waits at which a job's start or
end meets a tabulated time and bisecting from the first allowed sample; the least-cost wait by golden-section search
around the best allowed sample, with the earliest wait a candidate. The interpolation check solves the plan again on
every other tabulated time (keeping the first and the last) and flags the result `coarse` when an answer (earliest
starts and wait, collective dose at each wait, least cost) moves by more than the tolerance. An invalid plan or dose
rate exits 2 with its cause on stderr and writes nothing.

The candidate is `45b6166`, the commit adding the command.

## Suite and gates

`controls/p197.py` is the harness and checker. **G2** compares with exact answers, **G3** with ACTINV's own dose on a
finer cooling grid. Relative differences are |a − b| / max(|a|, |b|); a missing answer against a present one is
infinite.

- **G0.** The SHA-256 of this protocol and of `controls/p197.py` are in `protocols/protocol_hash.txt`.
- **G1.** Provenance: the record is from a clean tracked tree (it names its commit and binary), on the registered
  cases, and every G2 case finished.
- **G2. Closed form.** Three curves, a sum of decaying exponentials in Sv/h: `one` (1e-2, 5 d), `two` (3e-2, 0.6 d;
  2e-4, 70 d), `three` (1e-2, 12.7 h; 1e-3, 312.2 d; 5e-3, 1925.28 d). Each is tabulated at shutdown and at 8, 40 and
  200 times log-spaced from 1 h to 365 d (9, 41 and 201 times) and planned twice: two jobs at the one place (8 h with
  2 people, then 16 h with 3), an access limit equal to the curve's dose rate at 20 d, waits of 0.5, 3, 20, 100 and
  300 d and costs of 1e7 per person-Sv and 1e3 per outage day; once with no individual limit and once with one equal
  to the second job's dose when the plan starts at 30 d. Exact answers: closed-form integrals, bisection on the exact
  curve, and the least cost by a 20,001-point scan and golden section on the exact cost. Compared: the earliest wait,
  each job's earliest start alone, each job's dose and the collective dose at every wait, and the least cost.
  - a. On every 201-time grid every compared answer is within 1e-3 of exact.
  - b. On every grid every job dose, collective dose and earliest start is at least exact × (1 − 1e-9).
  - c. On every grid of `one` every compared answer is within 1e-9 of exact.
  - d. On every grid with a compared answer more than 0.05 from exact the result's check is `coarse`, and no
    201-time grid is `coarse`.
- **G3. ACTINV-computed dose.** Seven components after two years at 3e8 n/cm2/s of D-T neutrons, as a point source at
  1 m with `photon.missing_spectra: "estimate"` and the NIST photon response
  (`~/nuclear-data/photon-response/nist-xcom-all.json`, its SHA-256 pinned by the examples): the two development
  components of `examples/maintenance/` and five held out, `actinv new` materials of 10 kg (EUROFER97, Inconel 718,
  tungsten, Al 6061) and 50 kg (ordinary concrete). Each is run on the example's 15 cooling times and on 73 (those
  and 60 log-spaced from 1 h to 365 d), and planned on each: one 6 h job with 2 people, an access limit of 0.9 times
  the 73-time run's dose rate at 30 d, waits of 1, 7, 30, 90 and 180 d, and costs of 1e6 per person-Sv and 2e3 per
  outage day. Compared as in G2, the 15-time plan against the 73-time plan.
  - a. Every point-source dose a plan reads is present.
  - b. For every component, either every compared answer of the 15-time plan is within 0.05 of the 73-time plan's,
    or the 15-time plan's check is `coarse`.
  - c. The 73-time run's dose rates written as an `actinv-dose-map-1` table give the same answers as the runs.
- **G4. Refusals.** Ten cases exit 2, write no result and name their cause on stderr: an unknown location, a wait past
  the tabulated times, a job longer than they cover, a table in mSv/h, times that do not increase, a crew of zero,
  a run whose problem has no `photon.point_source`, a twin source without `sv_per_gy`, a wrong schema, a negative
  dose rate.
- **G5.** CI green on the evidence commit.

Reported, never gated: the least-cost wait (near a flat minimum it is ill-conditioned while its cost is not), each
check's largest change, which G3 plans were flagged, and their earliest waits.

## Procedure

Inside the AGENTS.md scope, from a clean tracked tree at the candidate or later: a release build of `actinv`;
`python3 controls/p197.py rehearse --actinv-bin …` (read as disclosed above); then
`python3 controls/p197.py run --actinv-bin …` and `python3 controls/p197.py check`, writing
`results/p197_record.json` and `results/p197_verdict.json`. G5 is CI on the evidence commit.

**P197-PASS** if G0–G4 and G5 hold. One repair round is allowed, registered as an amendment before its run.

## Sentences

On P197-PASS, filled by `controls/p197.py check` from its record:

1. "`actinv maintenance` plans jobs after shutdown from dose rates tabulated over cooling time: when each job may
   start, the dose of each crew at each wait, the earliest allowed wait and the wait of least cost."
2. "On three dose curves of one, two and three decaying nuclides with exact answers, its doses, start times and
   least costs on a 201-time grid were within {dense} of exact, never below the exact dose or start on any
   grid, and its interpolation check flagged every grid where an answer was more than 5% off."
3. "On {n} activated components computed by ACTINV ({held} of them held out), plans from a 15-time cooling grid were
   within {unflagged} of plans from a 73-time grid wherever the check did not flag the coarser grid ({flagged}
   flagged)."

## Not claimed

That a plan is safe, or that any dose rate it reads is accurate: a point source at 1 m is a screen, and a twin's
dose points are point-kernel estimates with no scatter or buildup. That the check catches every interpolation error:
it is measured on these curves and components only. Dose rates that rise after shutdown (ingrowth) are not in the
suite; the earliest-start search can miss an allowed window narrower than one sample interval. The workbench page and
the Python helper for OpenMC dose maps are not part of P197.
