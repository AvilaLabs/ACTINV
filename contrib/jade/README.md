# ACTINV adapter and initial FNS iron benchmark for JADE

The [patch](jade-actinv-code-target.patch) is a locally tested draft contribution
for [JADE issue #548](https://github.com/JADE-V-V/JADE/issues/548). It targets
`developing` at `85fc7c7bd472d9fa298b57342a724981119b8aac`. The owner submitted
[draft PR #549](https://github.com/JADE-V-V/JADE/pull/549); maintainer review and
approval are pending.

The initial scope is scalar neutron activation with an explicit activation/decay
combination, local execution through `actinv run`, and the 1996 FNS iron
five-minute experiment. The patch includes code registration, configuration/GUI
compatibility, input translation, bounded execution, completion detection,
output parsing, comparison configuration, documentation and regression tests.
It does not add a Python dependency on ACTINV.

The real local check traversed JADE's application workflow from benchmark
execution through result discovery, raw CSV, C/E spreadsheet and Word atlas.
All 20 measurements remain in the comparison, with correct cooling times,
microW/g units and reported experimental errors. The benchmark template carried
unresolvable catalog references, so the run also shows that the selected
library replaces them. Calculated heat matched the existing ACTINV iron control
exactly. That establishes integration consistency, not new experimental
validation. See the [current verification](VERIFICATION-2026-09-23d.md) and the
[first verification](VERIFICATION-2026-09-23.md).

## Apply to a clean checkout

```bash
git clone https://github.com/JADE-V-V/JADE.git
cd JADE
git switch --detach 85fc7c7bd472d9fa298b57342a724981119b8aac
git switch -c Feature/actinv-fns-decay-heat
git apply --check /path/to/jade-actinv-code-target.patch
git apply /path/to/jade-actinv-code-target.patch
```

Install JADE using its documented environment instructions. The patch's
`docs/source/usage/actinv.rst` documents the configuration; the benchmark page
documents source files, units and expected local data layout. ACTINV, nuclear
libraries and benchmark data are installed separately. The default FNS entry
is disabled and its input download is not yet integrated.

## Verification and remaining decisions

- Focused Python tests: **168 passed, 14 skipped, 2 deselected**, including
  47 adapter cases, 29 duration controls and the application tests. OpenMC was
  unavailable; network installation and unrelated scheduler unit tests were excluded.
  Full JADE CI and an interactive GUI session have not been run.
- Real CLI integration on the preceding candidate: all 20 points, spreadsheet
  and plot verified. The duration/test cleanup reran the focused Python suite.
- Patch applies cleanly to the pinned upstream base, rechecked against the live
  `developing` head. Ruff reports nothing on added lines. The local Sphinx build
  adds no warning messages relative to that base, and the FNS link resolves.
- Initial limits: neutron, inline 709-group spectrum, one material, local
  execution, 180-second scalar timeout; no mesh, MPI, scheduler execution or
  optional uncertainty/response datasets. Session-wide MPI, prefix and
  run-mode settings are checked before any benchmark starts when ACTINV will
  execute. Input-only generation needs no ACTINV executable and permits these
  settings; continuation still checks them.
- Maintainers still need to review the interface and proposed convention:
  `nps` ignored, ACTINV `Error=0` means no Monte Carlo sampling error, while
  experimental errors remain in the C/E comparison.
- Benchmark input/measurement hosting remains open, and the applicable CoNDERC
  terms have not been confirmed. The patch contains no source measurements,
  spectra or nuclear libraries. ACTINV's own repository already contains the
  derived spectrum (`examples/fns_fe_5min.json`) and the 20 measured values
  (`results/fns-iron-001/comparison.csv`) with a source citation; the draft PR
  says so.

The [local verification driver](verify_fns_iron.py) uses the frozen
[`JADE-FNS-001` protocol](../../protocols/JADE-FNS-001.md) and ACTINV's existing
iron preparation/control. It requires a fresh output directory. On this
workstation it must run inside the enforced scope in the root `AGENTS.md`;
do not launch it as an unbounded standalone command.

## Contribution process

See the [walkthrough](CONTRIBUTING-WALKTHROUGH.md) and
[submitted draft PR text](DRAFT-PR.md). Draft PR #549 targets `developing`,
with focused questions for asynchronous discussion.

The reviewed changes are committed as `fc8f78a3f00a7a68c53f09520e2954f66626d41e`
and pushed to [AvilaLabs/JADE's feature branch](https://github.com/AvilaLabs/JADE/tree/Feature/actinv-fns-decay-heat).
The uploaded commit, PR description and changed-file list were verified against
the prepared contribution. The PR is open as a draft with no merge conflicts.
At the submission check, pytest reported `action_required` with no jobs started;
CodeRabbit skipped automatic review because the PR is a draft.

The [original review](REVIEW-2026-09-23.md) concerns the preserved
[historical prototype patch](history/jade-actinv-prototype-2026-09-23.patch).
Its intentionally failing [review checks](review_contracts.py) describe that
old interface. The repaired patch includes its own regression tests.

A second review of the repaired patch found fixes needed before a draft:
session settings were checked only after other work had run, the shutdown time
was unguarded, the hosting text was incomplete, and two documentation titles
produced warnings. They are addressed in the
[second verification](VERIFICATION-2026-09-23b.md); the reviewed version is
preserved as
[history/jade-actinv-code-target-882ae75e.patch](history/jade-actinv-code-target-882ae75e.patch).

The [follow-up review](REVIEW-2026-09-23c.md) identified an input-only regression
in that validation and an added documentation warning. Both are fixed in the
current patch; its predecessor is retained as
[history/jade-actinv-code-target-9954c150.patch](history/jade-actinv-code-target-9954c150.patch).

## Licensing boundary

The JADE-derived patch files, including the historical patches, are distributed
under JADE's GNU GPL version 3; see [LICENSE-JADE](LICENSE-JADE). Existing
upstream notices are retained. New adapter code is contributed under the same
terms. ACTINV's solver source and its existing MIT/Apache-2.0 licenses are
unchanged. JADE invokes the separately installed ACTINV command-line program
and exchanges JSON files with it.
