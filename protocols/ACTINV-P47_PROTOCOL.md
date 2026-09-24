# ACTINV-P47 protocol — dose-qualified spatial handoff

**Status:** DRAFT — unopened, unhashed | **Drafted:** 2026-09-23 |
**Parent:** roadmap draft innovation extension P47 (`docs/ROADMAP.md`,
commit `6db406d`) | **Depends on:** P32-CONDITIONAL
(`results/verdict_p32.json`); implemented dose leg
(`controls/p32_dose.py`); bounded job slots per local safety rules

Frozen comparison bands, job envelope and dose metrics are placeholders
to be fixed at the freeze; this document authorizes no execution.

## Intent

Discharge P32's recorded conditions so the R2S chain's claim can
upgrade from "flux proxy" to "computed dose on the executed geometry".
Three locally-dischargeable items and one external one:

1. Execute the implemented `EnergyFunctionFilter` dose leg — the frozen
   exported sources SHA-re-verified, then replayed through OpenMC
   photon transport scoring E·μ_en,air directly, producing a dose rate
   with its Monte Carlo standard deviation.
2. Carry the neutron-tally statistical error into the activation
   comparison band — the per-cell tally relative standard deviation
   enters the stated flux-normalization uncertainty used in the
   `openmc.deplete` comparison, so the comparison's agreement is
   reported inside a propagated band rather than as bare deviation.
3. Report the contact-proxy/transported-dose relationship for what it
   is: different models (uncollided semi-infinite slab vs transported
   geometry). Their ratio is *reported* with its drivers named; it is
   not forced into agreement and no dose claim is made from the proxy.
4. External geometry: consume a benchmark geometry (SINBAD is the
   natural source) **only if** a lawful copy arrives through the
   principal's channels before the freeze. Otherwise the self-produced
   condition stays named verbatim — it is not discharged by silence.

MCNP distributed-source emission remains a documented placeholder;
it cannot be verified without a licensed MCNP install and opens only
when that route exists.

## Scope

- Frozen artifacts: the P32 exported sources, manifests and comparison
  records, re-hashed at G0 and asserted byte-identical to the sealed
  record before any replay.
- Dose execution under the enforced local cgroup, one job at a time,
  bounded waits and resumable output per the local safety rules.
- Tally-error propagation: per-cell tally relative std-dev enters the
  comparison band by the declared linear mechanism (flux normalization
  is linear in the qualified trace regime); the propagation mechanism
  and its applicability limit are named in the record.
- Deliverable record: executed dose table per cell per cooling step
  with MC std-dev, the propagated-band comparison, and the named
  geometry provenance (self-produced, or the licensed external
  geometry's identity).

## Out of scope

- A regulatory dose prediction, room/shutdown dose-rate claims, or
  scattered-photon completeness beyond what the transported tally
  measures — the executed geometry's dose is reported, not a bounding
  safety case.
- New physics, geometry generation, or any transport inside ACTINV.

## Gates (draft)

- **G0** — seal: protocol hash, opening commit, frozen P32 artifact
  hashes re-verified, OpenMC version pinned, job envelope declared.
- **G1** — dose execution: the dose leg runs to completion inside the
  envelope; per-cell per-step dose + MC std-dev records complete.
- **G2** — controls: a synthetic point/box source in void geometry
  reproduces the analytic uncollided dose within the frozen MC
  tolerance; tally-error propagation linearity checked on a
  two-cell fixture (scaled sigma scales the band, not the central
  value); a mutated frozen source fails the G0 re-hash.
- **G3** — report: the comparison re-issued with the propagated band;
  contact-proxy relationship reported with named drivers; geometry
  provenance stated verbatim.
- **G4** — independent closure: checker re-derives the dose table from
  tally records, re-verifies artifact identities and gate ordering,
  rejects planted mutations, emits the verdict.

## Closure rule (draft)

PASS only if the dose leg executed under seal, the propagated-band
comparison is issued, and controls hold — with the geometry provenance
stated as whatever it actually was. CONDITIONAL if an amendment was
used or the external geometry remained unavailable (condition named,
not discharged). FAIL otherwise.
