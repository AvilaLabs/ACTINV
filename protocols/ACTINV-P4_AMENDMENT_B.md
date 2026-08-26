# ACTINV P4 — Amendment B (append-only), 2026-08-26 — gate input separated from phase deliverable

**Trigger:** the principal observed that the phase was being executed in written order (full library first), not in
dependency order. P4's accuracy and reproduction gates (G2b, G2c, G3, G4) need only the composition isotopes of the 73
FNS materials — **255 of 2,847 targets** — and G2a needs only the three FENDL/TENDL MF=2-identical twins (Be-9, F-19,
Th-232). Building the full library first cost a four-hour build ahead of a twenty-minute validation, and (this is the
substantive part) delayed the discovery of two completeness bugs that the gate found within an hour of the subset
existing: inelastic isomer channels dropped, and ENDF level indices mistaken for isomeric-state numbers.

**Amendment (now standing rule 7 of the roadmap):**
1. P4's **gate input** is the FNS subset library (255 targets) plus the twins library (3 targets). G2a, G2b, G2c, G3 and
   G4 are scored on those.
2. P4's **deliverable** is the full 2,847-target library, built afterwards with the same code and fingerprint. G1 is
   scored on the deliverable: all targets parsed, zero errors, every unsupported feature ledgered.
3. A control is added: FNS results from the subset library equal those from the full library at 1e-12 on every matched
   point (products' own activation is trace and bounded by the rate pruning) — scored when the deliverable exists.
Nothing else changes. One repair round for the schedule; the gates themselves are unchanged.
