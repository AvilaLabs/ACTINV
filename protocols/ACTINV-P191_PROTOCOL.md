# ACTINV-P191 — ACTINV's R2S against OpenMC's own R2S with direct reaction-rate tallies, on the same data

## Purpose

P181 and its Amendment A compared ACTINV's R2S with OpenMC's own R2S in flux mode: OpenMC tallies a 709-group flux
and collapses its cross sections with it, as ACTINV does. Neither side then sees the flux shape inside a group.
OpenMC's default mode tallies every chain reaction directly in continuous energy, which does. It is OpenMC's most
faithful R2S and, at P163's 8 cells, its slowest. ACTINV's answer to the within-group shape is mixture
self-shielding, which P178 and P190 qualified at the level of reaction rates and which the OpenMC adapter now uses
by default (ledger 358). The question:

> On P181's tungsten, copper-alloy, water and steel model at 8 mesh elements, how close is ACTINV's shutdown dose,
> with and without mixture self-shielding, to OpenMC 0.15.3's own R2S with direct tallies on the same ENDF/B-VIII.1
> cross sections, and what does each take?

The answer is a measurement. A dose ratio outside P181's [0.90, 1.10] bound, or shielding that moves ACTINV away
from OpenMC, is still a PASS of this protocol: it certifies the numbers, not their direction.

## Disclosure of what has been seen

- P181's record and Amendment A's were read in full. At 8 elements, ACTINV on the ENDF/B-VIII.1 library gave 1.035
  times OpenMC flux mode's dose (P181A); the ledger 329 diagnostic showed the steel and tungsten photon sources above
  0.5 MeV differ between evaluations.
- P163 ran OpenMC's direct mode on the steel cube (8 cells, 468 s, 9.0 GiB at its 9 GiB cap; its dose agreed with
  flux mode to 0.1%). OpenMC's direct mode has never been run on P181's model, and neither has ACTINV with
  self-shielding on that model.
- No run of this protocol's arms was read before registration. Rehearsals (200 particles, all four arms) were read
  for exit statuses, gate results, whole-arm wall times and memory peaks only: `openmc_direct` peaked at 7.2 GB and
  every arm finished within 8 minutes. No dose, ratio or photon source of a rehearsal was read, and the outputs are
  deleted.

## Arms

All four arms are P181's pipeline (`controls/p181_harness.py`, imported unchanged): its model at mesh dimension 2,
14.1 MeV point source at 1e17 n/s for 365.25 d then 1e6 s of cooling, photon time index 2, 20,000 particles × 5
batches, seed 1000 (photon run 1500), two threads, P181's chain file and `cross_sections.xml` (ENDF/B-VIII.1 HDF5),
and its detector dose tally.

| Arm | Step 1 | Step 2 |
|---|---|---|
| `openmc_direct` | `R2SManager`, `get_microxs_and_flux` defaults: direct continuous-energy reaction-rate tallies | OpenMC's depletion on P181's chain |
| `actinv_mixture` | `ActinvR2SManager`: 709-group flux tally | `actinv mesh`, ENDF/B-VIII.1 709-group library (P181A's, `a964a44e…`), `self_shielding="mixture"` from the same HDF5 library |
| `actinv_plain` | as `actinv_mixture` | as `actinv_mixture` with `self_shielding=None` |
| `openmc_flux` | P181's flux mode on the 709 boundaries | OpenMC's depletion (context only) |

The two pipelines share the cross sections, not everything: OpenMC's chain takes its reaction list and branchings from
its chain file and its decay data from ENDF/B-VIII.0; ACTINV uses its 709-group library's reactions and ENDF/B-VIII.0
decay data with a JEFF-3.3 fallback, and builds the photon source from its own spectra. A dose ratio therefore
measures the whole pipeline, as in P181.

Each arm runs once, in its own systemd scope: 10 GiB memory (P181's), no swap, 128 tasks, two CPUs, a 4 h limit,
bounded waits and reaping by P181's helpers. Before each arm the harness waits at most 2 minutes (P181 waited up to
30) for free memory above the cap plus 1 GiB, then runs and records what was free; the workstation has 16 GiB of swap,
which the arms' scopes may not use. An arm stopped by its memory cap or time limit is recorded as censored.
The orchestrator itself runs inside the 6 GiB AGENTS.md scope. The ACTINV binary is built in the bounded scope from a
clean checkout of the registration commit; its SHA-256 is pinned in the checker.

## Gates

- **G0:** this protocol's SHA-256, and those of `controls/p191_harness.py` and `controls/check_p191.py`, are in
  `protocols/protocol_hash.txt`.
- **G1:** provenance. P181's pins (chain, cross sections, decay data), P181A's library pin and the binary pin; every
  arm from the registration commit with a clean tracked tree; all four arm files present; every scope bounded as
  above; not a rehearsal.
- **G2:** completeness. `openmc_direct`, `actinv_mixture` and `actinv_plain` exited 0, uncensored. (`openmc_flux`
  may be censored.)
- **G3:** the measurement, always reported, never a gate:
  - `R_m` and `R_p`, the mixture and plain ACTINV doses over the direct dose, with 1σ from the dose tallies' standard
    deviations; `R_f` for flux mode when it completed; photon-strength and flux-sum ratios; per-step wall times and
    peak memory.
  - Agreement: AGREES if |R_m − 1| ≤ 0.10, else DIFFERS.
  - Shielding: with d = |ln R_m| − |ln R_p| and its σ (arms taken as independent, which overstates σ since they share
    the transport seed), SHIELDING HELPS if d < −2σ, SHIELDING HURTS if d > 2σ, else SHIELDING NEUTRAL.
- **G4:** CI green on the evidence commit.

**P191-PASS** if G0, G1, G2 and G4 hold. One repair round (an amendment with a fresh run) is allowed.

## Allowed sentences

On P191-PASS, filled by `controls/check_p191.py` from the record (σ rounded up; times are steps 1 and 2, model to
photon source, since step 3 is the same photon transport in both pipelines):

1. "On P181's tungsten, copper-alloy, water and steel model with 8 mesh elements, ACTINV's R2S with mixture
   self-shielding gave a shutdown dose {R_m} ± {σ} times that of OpenMC 0.15.3's own R2S with direct reaction-rate
   tallies, on the same ENDF/B-VIII.1 cross sections; ACTINV's steps 1 and 2 (model to photon source) took {t_a} s
   against {t_o} s for OpenMC's."
2. "Without self-shielding ACTINV's dose was {R_p} ± {σ} times OpenMC's; {the shielding label's clause} ({label})."

They describe one model, one mesh size and one run per arm. They make no claim against measurement.

## Commands (coordinator)

From a worktree at the registration commit, with the binary built there, inside the 6 GiB AGENTS.md scope (each arm
starts its own 10 GiB scope), one arm at a time and with no other heavy job running:

- `python controls/p191_harness.py run --arm openmc_direct` (then `actinv_mixture`, `actinv_plain`, `openmc_flux`),
  with `--actinv-bin`, the input paths and `--xs-archive-sha256` as P181's commands give them;
- `python controls/p191_harness.py assemble`;
- `python3 controls/check_p191.py`.
