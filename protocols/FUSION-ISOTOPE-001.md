# Fusion Isotope Benchmark 001 — opening protocol

Frozen 2026-09-18 before executing the benchmark control. Base: ACTINV
`9b35cf0462d421e7d24685c742de7f87a67f647c`. Amend this protocol in a new,
separately named file; do not retroactively change acceptance criteria.

## Question and scope

Can ACTINV reproduce the Th-229 generator inventory underlying a published
Ac-225 production case? Reference: Parisi and Rutkowski, *Scalable Production
of Lead-212 and Actinium-225 Generators with Fusion Neutrons*,
[arXiv:2609.19166v1](https://arxiv.org/html/2609.19166v1), Section III,
Table 2, 10 MW wall / 7 cm slab / 27% row; Figure 4 is a later comparison.

The opening deliverable is a reproducibility audit and a data-independent
dimensional check. Neither is an executed ACTINV inventory reproduction.
No experimental validation, clinical output, economic feasibility, or full
contaminant-inventory claim follows from this work.

## Gates

- **G0 source audit:** retain a versioned citation, downloaded-source SHA-256,
  transcribed case metadata, and explicit missing inputs. The source bytes
  and nuclear-data libraries remain outside Git. A source hash identifies
  retrieved bytes, not scientific correctness or future HTML byte stability.
- **G1 arithmetic:** derive the source rate from source-plane area and emitted
  neutron rate per area. Multiply by the published atoms/source-neutron yield.
  Convert one year of gross produced Th-229 atoms to activity using its
  published half-life. Report the signed residual against the table, without
  fitting normalization or imposing a post-hoc agreement tolerance. Also report
  constant-production activity including parent decay. These use the paper's
  yield as an input and therefore do not independently reproduce neutronics.
- **G2 inventory verification (pending):** seal external rates, decay data,
  initial inventories, units, and independently calculated chain histories;
  freeze time points and numerical tolerances in an amendment before executing
  ACTINV. Check irradiation, zero-flux cooling, and the Th-229/Ra-225/Ac-225
  chain against a separately implemented analytic control.
- **G3 publication comparison (blocked on data):** reproduce target transport
  and inventory assumptions with pinned artifacts. Freeze observables,
  tolerances, tally statistics, spatial treatment, and digitization error
  before comparing. Separate matched-data results from alternate-library
  sensitivity. No fitted cross sections or source strengths.

G0/G1 checks may pass while G2/G3 remain unexecuted. The overall result must
remain `not_reproduced` until the later gates have executed successfully.
Missing spectra/rates must never be replaced silently with a 14.1 MeV delta
spectrum. Emitted-neutron areal rate, incident current, and scalar flux are
different quantities. The yield already uses all emitted source neutrons;
do not apply the one-sided factor of one half a second time.

## Provenance and controls

Seal the reference metadata and this protocol by SHA-256 in the opening
receipt. Record results in `results/fusion-isotope-001/`, append subsequent
events to its ledger, and preserve earlier receipts. CI checks hashes,
arithmetic regeneration, and rejection of altered normalization and false
reproduction claims. A local test runs under the root AGENTS.md cgroup limits.

This phase changes no Rust implementation or evaluated nuclear data. No
harvesting optimization, alternate isotope, transport solver integration,
or core API extension is included. Release-wide checks remain separate from
scientific benchmark acceptance.
