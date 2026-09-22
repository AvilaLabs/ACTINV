# FNS Iron 001 evidence ledger

Append-only. Earlier FNS and CB1/CB2 results are unchanged.

- 2026-09-18 — Selected the already-seen Fe 1996 five-minute CoNDERC case
  for end-to-end application packaging. Reviewed the source deck, measured
  values, spectrum and accompanying plot. The plot explicitly labels minutes
  after irradiation and microW/g. Froze protocol and metadata at 1c85f35
  before fresh calculation. Use exact measurement times, all twenty points,
  and the released patched TENDL-2025 bundle; no fitted parameters or assumed
  uncertainty confidence level. Numerical execution is assigned to hosted CI
  to avoid competing with existing workstation simulations.

- 2026-09-18 — Initial hosted run 35390855929 built the CLI and downloaded
  the released nuclear data, but the CoNDERC host rejected Python's default
  HTTP client with 403 before any solver execution. Added an explicit ACTINV
  User-Agent and a download-integrity regression; the source URL, hash,
  physical model, and acceptance criteria are unchanged.

- 2026-09-18 — Hosted run 35391420589 executed the CLI at branch head
  fbbd99f (merge snapshot d7dd76e41fb04e87cf14a68b5c2b36412866d45a).
  All twenty points, source/data hashes, time/unit checks, and heat closure
  passed, together with nine regression tests. Recorded comparison.json
  SHA-256 `357b0349228a4e765286497f9a625f3f1203ddff912910a3776a509fa3abeb1f`.
  Geometric-mean C/E 0.9263; range 0.8831–0.9623; maximum absolute relative
  residual 11.69%; 5/20 inside reported errors. All predictions are low.
  Physical agreement remains descriptive, not passed. Preserved raw output
  in the CI artifact and committed the receipt, CSV/table, plot and note.
