# Author review and submission notes

## Scientific status

The requested new calculation and formatted manuscript are complete. This is an
author-review draft, not a journal submission or an approved corrected library.
The independent checker passed for all four cases. PDF pagination and the main
results table/figure were visually inspected. No email or submission was sent.

## Claim-to-evidence map

| Claim | Evidence | Limit |
|---|---|---|
| Four explicit contradictions | `supplement/comparison.json` records, original SHA-256, standalone reproducer | Exact archived bytes only |
| One ordinate changed | original and variant full-file hashes and changed-field record, independent checker | Entire file not qualified |
| Spectrum-dependent production change | `comparison.json` folds, `check.json` independent quadrature | Prescribed synthetic spectra |
| Unchanged 14.1 MeV values | all four point controls in both scripts | No distributed-spectrum guarantee |
| TALYS work-array cause and intended future correction | `evidence/defect_report_snapshot.md`, upstream-response section | Repository summary of private correspondence, not an independently inspected patch |
| Distinct from existing paper | ACTINV `paper/manuscript.html` and `paper/README.md`, reviewed 2026-09-14 | Compare against the actual submitted version before sending |

Historical P25 group-collapse results were found and informed the investigation.
The new manuscript tables instead use fresh direct folds, avoiding a mixture of
the historical emitted-state-sum diagnostics and ground-state-only sensitivities.
The broader historical report contains qualifications and superseded diagnostic
language. It is internal review evidence, excluded from the supplementary zip.

## Decisions still belonging to the author before journal submission

- Select a journal and apply its article-type and declaration requirements.
- Verify the private correspondence and the intended attribution. If the journal
  requires permission for a personal communication, obtain it before submission;
  no correspondence has been quoted verbatim or forwarded here.
- Confirm funding, competing interests, author details and the AI-assistance
  declaration. No funding or conflict statement was invented.
- Supply the related submitted ACTINV manuscript to the editor and explain the
  distinct contribution. Consider a brief source-data limitation update to that
  manuscript through its editorial process.
- Deposit the final supplement with a persistent identifier if desired, then
  replace the present availability wording. No DOI or public release is claimed.

Potential next scientific work is validation against a corrected official
TENDL release or an application-specific measured spectrum. Neither is required
to reproduce the narrower findings made in this draft.
