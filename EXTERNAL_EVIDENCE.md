# External review, integrations, and validation

This record tracks publicly verifiable external review, accepted integrations, benchmark hosting,
and independent scientific validation of ACTINV. Each entry identifies the contribution, its scope,
and the evidence supporting it. Dates below are merge dates in UTC.

## Accepted contributions

| Date | External project | Evidence type | Accepted contribution | Source |
| --- | --- | --- | --- | --- |
| 2026-10-01 | JADE | Upstream integration and maintainer review | ACTINV scalar execution target and the initial FNS iron five-minute decay-heat benchmark, with comparisons at 20 measured cooling times | [Merged PR #549](https://github.com/JADE-V-V/JADE/pull/549) |
| 2026-10-01 | IAEA Nuclear Data Section, Open Benchmarks | Benchmark package review and hosting | ACTINV input and experimental measurements for the same FNS iron case | [Merged PR #26](https://github.com/IAEA-NDS/open-benchmarks/pull/26) |

These are two accepted contributions supporting one benchmark workflow. The experimental measurements
come from the existing IAEA CoNDERC FNS archive.

### JADE: ACTINV adapter and FNS iron benchmark

**Status:** merged into JADE's `developing` branch on October 1, 2026.

- **Accepted functionality:** code and library selection, scalar input generation, local ACTINV CLI
  execution, output validation, raw processing, and calculated/measured decay-heat spreadsheets and plots.
- **Initial scope:** neutron activation of one material with an inline 709-group spectrum; the
  1996 FNS elemental-iron experiment with a 300-second irradiation and 20 measured cooling times.
  Reported experimental errors are retained.
- **Review evidence:** the PR records human maintainer feedback, revisions, approval, and merge.
  This establishes upstream acceptance of the adapter and its benchmark configuration. It does not
  establish an independent scientific evaluation of ACTINV's full validation corpus or other capabilities.
- **Availability:** the merge establishes availability in the development branch. Check that a
  packaged JADE release includes this commit before using its release number as an integration reference.

Sources: [PR #549 and review discussion](https://github.com/JADE-V-V/JADE/pull/549),
[merged commit `bf13b637`](https://github.com/JADE-V-V/JADE/commit/bf13b637bf746eb898ed592976a0c1d6b0f1cac2),
and the [benchmark description at that commit](https://github.com/JADE-V-V/JADE/blob/bf13b637bf746eb898ed592976a0c1d6b0f1cac2/docs/source/benchmarks/benchdesc/fns-decay-heat.rst).
See the [ACTINV JADE setup guide](contrib/jade/README.md).

### IAEA Open Benchmarks: FNS iron data package

**Status:** merged into `IAEA-NDS/open-benchmarks`'s `main` branch on October 1, 2026.

- **Accepted package:** one ACTINV input, benchmark metadata, and all 20 measured heat values in
  JADE's input and experimental-CSV layout. The package retains the 709-group spectrum, 300-second
  irradiation, published cooling endpoints, and reported relative experimental errors.
- **Purpose:** supplies the case through JADE's standard IAEA benchmark-input download. ACTINV
  and activation/decay libraries are installed separately.
- **Scope of acceptance:** review and hosting of the contributed benchmark package. Acceptance
  does not constitute IAEA certification or endorsement of ACTINV's solver accuracy.

Sources: [PR #26 and review discussion](https://github.com/IAEA-NDS/open-benchmarks/pull/26),
[merged commit `1d9855b0`](https://github.com/IAEA-NDS/open-benchmarks/commit/1d9855b00e05c5d5e25a11026ac56940dc75b516),
and the [original CoNDERC FNS archive](https://www-nds.iaea.org/conderc/fusion/files/fns.zip).

## Adding evidence

Add completed, publicly verifiable records as they become available. For independent reproductions,
published code comparisons, or scientific peer reviews, record:

- the date, researcher or group, and public report, publication, or review;
- the ACTINV version or commit and the nuclear-data identities used;
- the cases, observables, method, and acceptance criteria evaluated;
- the reported results, limitations, and whether ACTINV was run independently.

Keep integration review, benchmark hosting, scientific peer review, and independent numerical
validation identified by their evidence type. Outreach, planned tests, and offers to review are not
completed evidence entries.

ACTINV's own measured-data comparisons and executable controls are documented in the
[validation guide](docs/guide/validation.md), [validation records](docs/VALIDATION.md), and
[competitive benchmark](COMPETITIVE_BENCHMARK.md). Their stated scope and limitations remain applicable.
