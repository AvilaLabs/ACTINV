# ACTINV P37 Protocol — identical-data rejection classification

Frozen 2026-09-18 before any classification work. This protocol covers only
file-level classification of the twelve FENDL-3.2c source evaluations ACTINV's
validated artifact rejected at P26b. It does not change production validation
behavior, does not re-run the comparator leg, and does not decide whether any
strictness class should be relaxed — a disposition change would be a separate
phase with its own protocol.

## Scope

Twelve source files under `nuclear-data/p26b-work/failed-nuclides/`, rejected
by `actinv build-library` during the P26b G1 FENDL-3.2c artifact build:

| file | recorded rejection |
|---|---|
| Cr50.tendl | MF=10 partials, no MF=3 (MT17) |
| Cr52.tendl | MF=10 partials, no MF=3 (MT17) |
| Cr53.tendl | MF=10 partials, no MF=3 (MT17) |
| Cr54.tendl | MF=10 partials, no MF=3 (MT17) |
| Fe57.tendl | invalid RML particle pair for MT=2 |
| Mn55.tendl | MF=10 partials, no MF=3 (MT30) |
| Ni62.tendl | Breit-Wigner total width below component sum, LRX=0 |
| W180.tendl | MF=10 partials, no MF=3 (MT44) |
| W182.tendl | MF=2 ZA/AWR disagree with MF=1 |
| W183.tendl | invalid RML particle pair for MT=2 |
| W184.tendl | MF=2 ZA/AWR disagree with MF=1 |
| W186.tendl | MF=10 state sum exceeds runtime total (rel excess 4.165e-3) |

## Classification labels

Per file, exactly one label is assigned from evidence at the cited file
location, cross-referenced against the ENDF-6 format manual conventions and
ACTINV's check implementation:

- `true_defect` — the file contradicts itself or violates ENDF-6 semantics
  at the cited location; ACTINV's rejection is correct.
- `actinv_strictness` — the file is legal ENDF-6; ACTINV's check demands
  something the format does not require (e.g. an MF=3 total for every MF=10
  product section). Rejection is a deliberate contract choice, not a file
  defect.
- `ambiguous` — the file is unusual and neither label is defensible without
  evaluation-author intent; the evidence is recorded with the ambiguity
  named.

## Acceptance gates

- G1: every one of the twelve files classified with quoted file evidence
  (record offsets/lines) and a named check site in `crates/actinv-data`.
- G2: an independent checker re-derives every classification from the sealed
  files alone and reproduces the label set; mutation self-test rejects
  planted mislabels.
- G3: negative controls — a file whose cited region is absent must be
  marked `evidence_missing`, never guessed; a label outside the enum fails.
- G4: verdict records the label histogram, the consequence for the
  identical-data arm (open/closed and why), and names explicitly that no
  validation behavior changed.

## Non-goals

No relaxation of ACTINV validation is implemented in this phase. No claim
that the identical-data arm is executable follows unless a classification
demonstrates a correctable cause — and even then, correction is a separate
phase decision.
