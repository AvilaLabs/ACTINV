# ACTINV P109 amendment A — Rust edition compatibility repair

Frozen 2026-10-03 before repair. Parent protocol SHA-256:
`03aa601e2b1c85bbe4058763f11031e90f732b8adeac89af4b1bd40ee358d485`.

The coordinator's first bounded formatting attempt exited 1 because the new
optional photon-settings check used a let chain in the Rust 2021 CLI crate.
The coordinator suggested that syntax during static review. Preserve the log
as `results/p109_format_failure.log`, SHA-256
`0a9ee2fb99edbbcd617c3db2763bf981948c4fc48d20f76f4c02052f12b00c10`.
The attempted wrapper source SHA-256 was
`7c7033c39aa07c9cc1ad2e34fcb6310659b773cd2bedef951fcd13d1758bb9d9`.

This consumes the one repair round. Replace that check with an edition-compatible
predicate and format the implementation. No physics, input domain, population,
threshold, projection, coverage or class rule changes are authorized by this
amendment. The independent Python controls are still being implemented; neither
G0 nor native CLI evidence has executed. Complete and seal those controls under
the original protocol before any native science run. Retain the failed quality
attempt alongside all later quality results. Another failed gate closes P109
FAIL and requires a separately frozen successor.
