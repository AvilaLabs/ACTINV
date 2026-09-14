# Draft — not sent

To: Dimitri Rochman <dimitri-alexandre.rochman@psi.ch>
Cc: Arjan Koning <a.koning@iaea.org>
Subject: TENDL-2025: four reproducible MF=3/MF=10 threshold inconsistencies

Dear Dr Rochman and Dr Koning,

While investigating activation-library construction in ACTINV, I found four
apparent inconsistencies in the TENDL-2025 neutron ENDF archive. I would appreciate
your assessment of whether these are known issues or require a different interpretation.

In each case, MF=3/MT=16 explicitly gives zero at the same energy where an
MF=10/MT=16 ground-state product cross section is positive:

| Evaluation | Energy (eV) | MF=3 (b) | MF=10 (b) |
|---|---:|---:|---:|
| Fe-53m | 7791974 | 0 | 107582.0 |
| Cl-35 | 13009500 | 0 | 24.59622 |
| Zr-88 | 12494780 | 0 | 70.11846 |
| Y-88 | 9459262 | 0 | 26.05538 |

These are matching explicit threshold ordinates, reproduced directly from the
source records without ACTINV's parser, interpolation, or group collapse.
The source versions are identified by SHA-256 in the attached note.

The note includes exact raw records, physical line numbers, archive provenance,
and a small standard-library Python reproducer. The review used locally archived
files matching our acquisition manifest; I have not established whether the live
distribution has since been corrected.

Are these known issues, and are corrected evaluations available? If confirmed,
could they be recorded on the TENDL known-deficiencies page, or could you direct
me to the preferred public tracking route? I would also welcome guidance on
whether related evaluations should be checked for the same threshold pattern.

A broader ACTINV audit identified additional interpolation and processing questions,
but this initial report is limited to these four explicit inconsistencies. I have
not quantified their effects on application results.

Thank you for your time and for making TENDL available.

Best regards,
Connor Avila
Avila Labs

---

## Local preparation notes (omit from the email)

Attach `TENDL2025_THRESHOLD_SUBMISSION.md` and
`reproduce_tendl2025_thresholds.py`. The broader P25 report is optional background;
it need not accompany the first message. No bulk nuclear data need be attached.

The official [Feedback link](https://tendl.imperial.ac.uk/tendl_2025/deficiencies.html)
opens a known-deficiencies page intended to list reported content/format problems.
As accessed 2026-09-14, it lists “To be anounced.” and exposes no submission form
or issue tracker. That does not prove no other tracker exists. Direct email to
the named developers is the recommended first route, with a request for a public
record or redirection. Email is the delivery mechanism; the technical note and
reproducer are the durable report. Publication of a versioned report can follow
adjudication, with its confirmation status stated explicitly.

Professional-address sources:

- Rochman: https://indico.psi.ch/event/16894/
- Koning: https://conferences.iaea.org/event/395/contributions/
- Developer roles: https://tendl.imperial.ac.uk/tendl_2025/reference.html

If there is no reply after about two weeks, a polite follow-up in the same thread
is reasonable. This is a suggested cadence, not a published TENDL policy.
