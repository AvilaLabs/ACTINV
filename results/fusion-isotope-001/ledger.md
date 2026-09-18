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
