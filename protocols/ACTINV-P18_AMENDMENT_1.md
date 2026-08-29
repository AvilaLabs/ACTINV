# ACTINV P18 Amendment 1 — early-row disclosure and forced-diagnostic quarantine

Written 2026-08-29 before G0, before any production edit, before the family partition was generated and before any
diagnostic or held-out score. The frozen P18 protocol remains
`002afb038bbbf1ad0bdb34149971f8d3f33a3e2590c6d04ced87bb5ada046e09`. This is P18's sole permitted repair round;
an otherwise successful closure is therefore `P18-CONDITIONAL`. A second repair need closes P18-FAIL under the
unchanged protocol.

## What happened

A discovery command intended to redact supplemental records tested whether column 1 was `D`. The Rodrigo supplement
is fixed width and places its record marker in column 20, so the test did not match. The displayed slice comprised
physical lines 1--140 and exposed dependent values belonging to five reaction families before G0's metadata-only
seal. The command did not write a file, run ACTINV, select a partition, calculate a metric or alter production source.

The exposed family identities are:

- unsupported gamma projectile: `86Kr(g,n)85Kr`;
- unsupported gamma projectile: `181Ta(g,n)180Ta`;
- supported neutron projectile: `35Cl(n,2n)34Cl`;
- supported neutron projectile: `39K(n,2n)38K`; and
- supported neutron projectile: `45Sc(n,2n)44Sc`.

No dependent value outside those five families was displayed. The two gamma families were already ineligible under
P18's frozen projectile predicate. The three neutron families might otherwise have entered the deterministic split,
so their blind status is irrecoverable.

## Binding repair

1. All five exposed families are permanently forced diagnostic before deterministic ranking. They are removed from
   the eligible partition pool exactly like the two paper case studies and P17-exposed families, and their row IDs
   remain visible in the seal and row ledger.
2. No exposed row may contribute to a held-out count, metric, bootstrap sample or release decision. Diagnostic
   reporting may retain every row after G0 is green.
3. G0 records the literal disclosed physical-line interval, family list and this amendment hash. The independent
   checker rejects a missing family, an additional quarantined family, a held-out exposed row or a changed interval.
4. All future redaction and metadata parsing identify record type with exact fixed-width column 20 after verifying a
   minimum line length and the six integer identity fields. A regression fixture proves that a `D` record with leading
   spaces is suppressed and that prose containing `R` in column 20 is not parsed as a reaction record before the first
   `999999999999999999` delimiter.
5. The held-out fraction and hash ordering are unchanged. Forced-diagnostic families are removed before ranking as
   already specified; they are not replaced or hand-selected. Thus every still-eligible family retains exactly the
   partition it would have received from the frozen algorithm after the now-explicit quarantine set is applied.

This amendment changes no source hash, eligibility predicate, score, threshold, mapper rule, conservation tolerance,
performance ceiling or release gate. It narrows only which already-disclosed families may claim held-out status.
