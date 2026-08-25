# Ledger semantics

Every ACTINV run carries a ledger; nothing is dropped silently. Categories:

- `composition_isotopes_absent` — isotopes in the material with no cross sections (with their abundance).
- `products_no_evaluated_decay_data_ENDFB80_JEFF33` — reaction products with no decay record in either library; their
  production is booked to the leakage state (atoms reported per step).
- `fission_no_yields_to_leakage` — fission on actinide targets; yields are not yet implemented.
- `bulk_production_dropped` — production of a composition isotope under the constant-bulk approximation (rates listed).
- `negative_atoms_zeroed_per_step` — CRAM round-off below zero, set to zero, amount recorded.
- `rate_pruning` — nuclides dropped by rate-significance pruning with the bound on atoms each could have received.
- `measured_rows_excluded` — measurement rows that could not be aligned or are non-positive, with the reason.
- `decay_data_sources_used`, `burnup_flag`, `nuclides_without_decay_energy_data`.

A run whose ledger is empty is not "better"; it is a run with nothing to report. A run with entries is honest.
