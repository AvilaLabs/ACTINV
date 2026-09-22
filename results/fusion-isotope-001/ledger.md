# Fusion Isotope Benchmark 001 evidence ledger

Append-only events; earlier findings and receipts remain part of the record.

- 2026-09-18 — Opened on ACTINV 9b35cf0462d421e7d24685c742de7f87a67f647c.
  Selected the Table 2 mixed-thorium slab and sealed the opening protocol
  before numerical execution. Retrieved versioned reference HTML outside Git;
  its SHA-256 is recorded in the literature metadata. Full transport and
  inventory reproduction remain blocked on the explicitly listed external
  inputs. No ACTINV/OpenMC job has executed for this case at opening.

- 2026-09-18 — Executed five Python control tests and generated/rechecked
  `opening.json` under an inspected systemd scope: memory.max=6442450944,
  memory.swap.max=0, pids.max=128, cpu.max="200000 100000". All controls
  passed. The nominal printed inputs give 553.7755904942 Ci gross Th-229
  activity per full-power year versus the table's 542 Ci (+2.1726%). This
  unresolved residual may reflect rounded inputs or other assumptions;
  no normalization was adjusted. This is arithmetic using the published
  per-neutron yield, not an independent transport or ACTINV result.

- 2026-09-18 — Public follow-up search inspected the arXiv source archive,
  GitHub, and Zenodo. No matching numerical model package was located. Table 6
  does supply five numerical Th-229 inventory checkpoints; the earlier missing
  full Figure 4 histories do not mean there are no comparison values. Preserved
  the original source-audit receipt and recorded the added metadata separately.

- 2026-09-18 — Froze Amendment 1 and `reduced_case.json` in commit 4968374
  before numerical execution. It declares a prescribed-rate five-state chain,
  explicit omissions, fixed times, independent Bateman control, and numerical
  tolerances. Original matched-data G2/G3 remain pending.

- 2026-09-18 — GitHub Actions run 35368517712 built the existing ACTINV core
  probe and executed the reduced chain at implementation commit 710969c.
  All nine regression tests passed, as did irradiation/cooling, split-step,
  and conservation controls. Largest numerical error/allowance was 0.008969;
  largest conservation residual was 7.64e-16. Receipt SHA-256:
  `0070e68a7ac5be44e0bf5890d95f484f3af5f4082cb8f8926de9a51cc6884063`.
  Reduced-model Th-229 exceeds Table 6 by 3.56%, 6.92%, 9.69%, 17.96%,
  and 26.96% at 1, 3, 5, 10, 15 years. No inputs were fitted. Recorded the
  receipt, comparison table/plot, and technical note; overall remains
  `not_reproduced`. Workstation computation was deferred because other
  simulations were active; all benchmark execution occurred on hosted CI.
