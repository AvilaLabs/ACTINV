# FNS Iron 001 — application benchmark

Frozen 2026-09-18 before fresh execution. This packages the already-seen
CoNDERC/JAEA FNS Fe 1996 five-minute case; it is not a blind or newly held-out
validation experiment. It changes no solver, nuclear data, or earlier results.

## Question and inputs

Can a user obtain public inputs, run the released-data ACTINV CLI, and
reproduce a transparent comparison with all twenty measured decay-heat points?
The application is decay heat after fusion-neutron exposure, not isotope
production, dose, component qualification, or whole-reactor thermal analysis.

Read only named, SHA-256-pinned members of the CoNDERC `fns.zip` archive.
Record its source URL and hash, the input deck, spectrum, measurements, and
plot that identifies units. Keep downloaded data outside Git. Case metadata
is in `examples/fns_iron/case.json`. Use the repository's pinned v1.1.0
catalog and default `tendl-2025-patched-neutron` bundle, ENDF/B-VIII.0 decay
with JEFF-3.3 fallback. This is Avila Labs' documented remediation derivative
of TENDL-2025, not an official TENDL release. It is not the older FISPACT
reference's TENDL-2017 data. No code-ranking claim is made.

Use the existing iron spec's one-gram, natural-iron composition, 300-second
irradiation and total flux 1.116e10 n/cm2/s. Independently check its 709-group
spectrum against the original flux member and its normalization/material/
irradiation against the original deck. Evaluate cooling at exactly the
measurement times (first column in minutes), not the input deck's rounded
whole-second endpoints. Construct positive incremental durations with Decimal
arithmetic; never treat cumulative times as increments or add irradiation time
to the experimental cooling axis. No interpolation or point selection.

## Controls and measures

- Require all 20 source measurements and 21 CLI output states, including EOI.
- Require pinned archive/member/catalog/data hashes, finite positive heat,
  monotonically increasing measurement times, positive reported errors, zero
  cooling flux, and output time agreement within 1e-6 seconds.
- Check heat component sum against total within 1e-12 relative + 1e-20 W/g.
- Convert W/g to microW/g with exactly 1e6. Report each calculated/measured
  ratio, signed relative residual, and residual divided by the reported error.
- Report geometric mean C/E, min/max C/E, maximum absolute relative residual,
  and the number inside the reported error bars. The source's third column
  is treated as a reported uncertainty only: no assumed confidence level,
  independent-error model, chi-square significance, or model uncertainty band.
- Physical agreement is reported, never used as a post-hoc pass gate. A
  successful execution/integrity check must not be labeled experimental
  validation passed. No fitting of source strength or material composition.
- Future CI repeats the CLI calculation and compares every recorded heat
  with relative tolerance 1e-8 plus 1e-9 microW/g absolute. This is a numerical
  regression allowance, not an experimental agreement tolerance.

## Execution and evidence

Fresh subprocess outputs go into a temporary directory; the CLI command has
a 180-second timeout using subprocess.run (kill/reap on timeout). This single
material, non-mesh run does not request workers. No old result may be reused
after a child failure. Test timeout/error propagation and reject malformed
data, missing points, unit/time shifts, hash changes, and altered results.

Run on hosted CI to avoid adding work to the occupied workstation. If local
execution is later needed, obey the root AGENTS.md cgroup and workload rules.
Store the compact comparison, executable/source/input hashes, and complete
solver certificate/ledger in a new receipt; retain raw CLI JSON, generated
spec, and extracted source members in the CI artifact. Generate CSV, Markdown,
and a standalone plot. Preserve the first receipt and append ledger entries.
The benchmark remains specific to this material, irradiation, cooling window,
data release and measured observable, regardless of how well it agrees.
