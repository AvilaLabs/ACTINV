# ACTINV-P45 protocol — executed complete-campaign benchmark

**Status:** DRAFT — unopened, unhashed | **Drafted:** 2026-09-23 |
**Parent:** roadmap draft innovation extension P45 (`docs/ROADMAP.md`,
commit `6db406d`) | **Depends on:** P43 (robustness-inclusive workload);
P31 amortization machinery; P26b/P38/P40 comparator lineage

Frozen workloads, comparator versions, resource limits and the timing
host are placeholders to be fixed at the freeze; this document
authorizes no execution.

## Intent

Replace the kernel-ratio headline with an executed whole-workload
claim. P45 runs frozen complete campaigns end-to-end on the release
candidate and on every lawfully executable equivalent-output
comparator, with the comparator's documented amortization enabled, and
reports preparation, solve, sampling, output and evidence time
separately. The P26 lesson binds this phase: an undetermined target is
a failure mode — every figure here is an executed measurement, a
measured loss is published as one, and an inaccessible comparator is
`unmeasured`, never a silent win.

## Scope

- Two frozen workloads:
  - **Variant campaign**: a materials × schedules grid on a declared
    spectrum with scalar responses at declared cooling times, including
    the declared robustness block where the comparator admits an
    equivalent (otherwise reported as a capability asymmetry, not
    omitted silently).
  - **Distinct-spectrum campaign**: a mesh workload with per-cell
    spectra at a frozen cell count, within the P21 measured memory
    envelope.
- Comparator arms, each pinned at freeze: ALARA (locally built, with
  its converted-library amortized path — the P26b/P38/P40 lineage) and
  OpenMC (`deplete`/`IndependentOperator`, pinned release, documented
  chain reuse enabled). FISPACT-II and SCALE/ORIGEN cells stay
  `unmeasured` absent lawful executables; a collaborator leg may be
  appended by amendment without reopening the phase.
- Output parity contract per comparator, frozen at G1 *before timing*:
  the exact response set compared (the intersection of qualified
  outputs — e.g. per-nuclide atoms and total activity per unit mass at
  declared cooling times), the tolerance classifying outputs as
  matched, and the explicit list of ACTINV responses the comparator
  cannot emit (capability mismatch, reported — never converted into a
  speed ratio).
- Time accounting: cold and warm states; preparation, per-case solve,
  robustness sampling, output/evidence and checker overhead reported
  separately so any ratio is attributable; resource limits
  (threads/memory) matched and recorded; host pinned.
- Failure accounting: failed cases counted on both sides; a case a
  comparator cannot express is `contract_gap`, not a timing exclusion.

## Out of scope

- Extrapolated figures (no projected million-cell or unseen-hardware
  numbers), kernel-only claims, transport-coupled workloads, and any
  metric computed on mismatched outputs.
- Optimizing ACTINV for the benchmark: candidate changes inside the
  phase require a separately-frozen amendment and re-verified parity.

## Gates (draft)

- **G0** — seal: protocol hash, opening commit, candidate artifact
  identity, comparator identities and their enabled amortization paths,
  host record, prior verdict re-verification.
- **G1** — feasibility and parity: one profiled representative unit per
  arm (standing rule 7), the output-parity contract frozen from
  measured capability, the full population sized and frozen; an arm
  that cannot execute the parity contract is `unmeasured` here, not
  carried forward.
- **G2** — controls: parity holds per case on a frozen pre-timed
  subset; warm-state digests verify; a planted output mismatch is
  detected before timing; timing methodology (warm-ups, batch size,
  spread reporting) frozen.
- **G3** — execution: both workloads run to completion on every
  measured arm within the frozen resource envelope; per-case and
  per-stage records complete; failures ledgered.
- **G4** — independent closure: checker re-derives every ratio and
  total from raw records, verifies parity preconditions held during
  timed runs, rejects planted mutations, emits the verdict.

## Closure rule (draft)

PASS only if both workloads executed on at least one comparator arm
with the parity contract held and complete accounting. CONDITIONAL if
amendments were used or a headline arm is `unmeasured`. FAIL otherwise.
The published claim names the exact executed workloads, comparator
versions, resource limits and host — nothing else is claimed.
