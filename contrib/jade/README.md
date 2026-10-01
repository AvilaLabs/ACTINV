# ACTINV adapter and initial FNS iron benchmark for JADE

The ACTINV adapter and initial FNS iron decay-heat configuration were merged into
[JADE's `developing` branch](https://github.com/JADE-V-V/JADE/tree/developing) on October 1, 2026:
[JADE PR #549](https://github.com/JADE-V-V/JADE/pull/549).
The accompanying input and measurement package was merged into
[IAEA Open Benchmarks](https://github.com/IAEA-NDS/open-benchmarks) the same day:
[data PR #26](https://github.com/IAEA-NDS/open-benchmarks/pull/26).

Use the merged upstream adapter. The local [draft patch](jade-actinv-code-target.patch) is retained
as contribution history and is not needed for this setup. The merge is into the development branch;
check whether a packaged JADE release contains the adapter before using that release instead.
See [external evidence](../../EXTERNAL_EVIDENCE.md) for the dated review and acceptance record.

## Supported workflow

The initial adapter supports deterministic scalar neutron activation of one material, an inline
709-group spectrum, explicit activation/decay files, and local execution through `actinv run`.
JADE generates the input, runs the separately installed CLI, validates the output, and produces
raw results, C/E spreadsheets, and a plotted atlas.

The initial case is the 1996 FNS elemental-iron experiment with a 300-second irradiation and
20 measured cooling times from IAEA CoNDERC. Comparisons retain the reported experimental errors.
Mesh inputs, non-neutron projectiles, MPI/scheduler execution, uncertainty sampling, and other
optional response calculations are outside this initial adapter. No ACTINV Python dependency
is added to JADE.

## Install the merged upstream version

The following source installation pins the merge commit containing the adapter. Follow JADE's
[installation guidance at that commit](https://github.com/JADE-V-V/JADE/blob/bf13b637bf746eb898ed592976a0c1d6b0f1cac2/docs/source/usage/installation.rst)
for platform and Python requirements.

```bash
git clone --branch developing --single-branch https://github.com/JADE-V-V/JADE.git
cd JADE
git switch --detach bf13b637bf746eb898ed592976a0c1d6b0f1cac2
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Install ACTINV separately, fetch its nuclear data, and retain the file paths printed by the data command:

```bash
python -m pip install actinv
actinv data fetch
```

Initialize a JADE working directory outside the source checkout:

```bash
mkdir ../jade-actinv-run
cd ../jade-actinv-run
jade
```

JADE's initialization fetches benchmark inputs from IAEA Open Benchmarks, including the merged
FNS case. For an existing working directory, JADE's documented fetch utility is
`python -m jade.utilities --fetch`.

## Configure ACTINV and its data

In the JADE working directory, configure `cfg/env_vars_cfg.yml` for local ACTINV execution:

```yaml
mpi_tasks: 0
openmp_threads: 1
executables:
  actinv: /absolute/path/to/actinv
run_mode: local
code_job_template: {}
exe_prefix: null
```

Replace the executable placeholder with your installed CLI's absolute path. These settings apply
to the whole JADE session. Clear JADE's default `exe_prefix: srun`; the initial ACTINV adapter
rejects executable prefixes, multiple MPI tasks, and scheduler modes. JADE's `openmp_threads`
setting does not configure ACTINV.

Add the selected data combination to `cfg/libs_cfg.yml`, replacing the paths with your installed
activation and decay files:

```yaml
TENDL2025-patched-ENDF8-JEFF33:
  actinv:
    path: /absolute/path/to/tendl-2025-patched-neutron-709g.npz
    decay_primary: /absolute/path/to/endf-b-viii-0_decay.dat
    decay_fallback: /absolute/path/to/jeff-3-3_decay.dat
```

The activation NPZ needs its companion `<stem>_index.json` with schema
`actinv-library-index-2`, neutron projectile, and `fispact-709` groups. `decay_fallback` is optional.
Use absolute paths and a library name that describes the actual data: this example names ACTINV's
patched TENDL-2025 derivative, not an official TENDL release. JADE replaces the template's library
references with these explicitly selected files.

Select that library in the `FNS-DecayHeat` entry of `cfg/run_cfg.yml`:

```yaml
FNS-DecayHeat:
  codes:
    actinv: [TENDL2025-patched-ENDF8-JEFF33]
  description: FNS iron five-minute irradiation
  nps: 1
  only_input: false
  custom_input: null
```

The shipped benchmark entry has an empty ACTINV library list; select a library to enable it.
`nps` is an unused compatibility field for this deterministic solver. Input-only generation
(`only_input: true`) needs no ACTINV executable; execution and continuation check the local settings.

The authoritative details are in JADE's
[ACTINV configuration](https://github.com/JADE-V-V/JADE/blob/bf13b637bf746eb898ed592976a0c1d6b0f1cac2/docs/source/usage/user_configuration.rst)
and [run documentation](https://github.com/JADE-V-V/JADE/blob/bf13b637bf746eb898ed592976a0c1d6b0f1cac2/docs/source/usage/run.rst).

## Run and compare with measurements

Select the experiment and the same ACTINV library in `cfg/pp_cfg.yml`:

```yaml
benchmarks: [FNS-DecayHeat]
code_libs: [_exp_-_exp_, _actinv_-_TENDL2025-patched-ENDF8-JEFF33_]
```

Then execute and post-process from the JADE working directory:

```bash
jade --run
jade --raw
jade --pp
```

Each ACTINV process has a 180-second timeout. JADE validates finite output at every requested
schedule endpoint before writing `actinv.complete`. `jade --cnt` restarts an incomplete scalar
case from its persisted input; it does not resume an internal solver checkpoint. Regenerate the
input when changing the selected data.

The FNS configuration compares calculated/measured heat at all 20 cooling times, converts W/g
to microW/g, and retains experimental errors. ACTINV's `Error=0` field denotes absent Monte Carlo
sampling error, not zero model or nuclear-data uncertainty. These comparisons do not represent
total predictive uncertainty. See JADE's
[post-processing guidance](https://github.com/JADE-V-V/JADE/blob/bf13b637bf746eb898ed592976a0c1d6b0f1cac2/docs/source/usage/postprocessing.rst)
and [FNS benchmark description](https://github.com/JADE-V-V/JADE/blob/bf13b637bf746eb898ed592976a0c1d6b0f1cac2/docs/source/benchmarks/benchdesc/fns-decay-heat.rst).

## Historical contribution evidence

The pre-merge local check traversed JADE's application workflow from execution through raw CSV,
C/E spreadsheet, and Word atlas. Calculated heat matched the existing ACTINV iron control at
all 20 measured cooling times. That established integration consistency for the recorded setup.
The original contribution notes are preserved in the
[pre-merge README snapshot](https://github.com/AvilaLabs/ACTINV/blob/4c86c1da6c15dd1919f66cb33749ac547974cc26/contrib/jade/README.md)
and the retained [draft patch](jade-actinv-code-target.patch).
The merged PR records subsequent maintainer review and revisions.

## Licensing boundary

The JADE-derived draft patch is distributed under JADE's GNU GPL version 3. The upstream adapter
is contributed under JADE's terms. ACTINV's solver remains separately licensed under MIT or
Apache-2.0; JADE invokes the separately installed command-line program and exchanges JSON files with it.
