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
