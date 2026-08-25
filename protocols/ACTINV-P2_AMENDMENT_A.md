# ACTINV P2 — Amendment A (append-only), 2026-08-25 — G2 control criteria

**Diagnosis (results/g2_cli.json, first run):** pruned vs unpruned differ by 2.5e-18 absolute on Mn-56 (value 1.26e-7,
a secular-equilibrium component formed as a difference of O(ΣN) terms in the CRAM recurrence) = 2.2e-16 of the total
— one machine epsilon. The "bitwise vs P1" control differs by 4.1e-25 on a component of 5.6e-17 because the CLI scales a
per-unit-flux reaction matrix in Rust while P1 pre-scaled it in Python (different rounding of the diagonal sum);
on every populated component the CLI equals the Python reference at 0.0.
**Repair (criteria, not code):** control (a) pruned vs unpruned: |Δ_i| ≤ 1e-12·ΣN for every component, and ≤ 1e-12
relative for components > 1e-3 of ΣN. Control (b): CLI unpruned vs Python reference = 0.0 on components > 1e-15 ΣN
(as P1), and vs P1 Rust |Δ_i| ≤ 1e-12·ΣN. The bit-for-bit wording is withdrawn as unattainable by design. One repair
round used for G2 criteria. Nothing else changes.
