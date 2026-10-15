# P139 — Geometry self-shielding from R-matrix-limited (LRF=7) evaluations

Registered 2026-10-15, before implementation and before any P139 evidence, under the owner-approved FISPACT-II parity
plan (2026-10-15: "ask yourself if there's a way to surpass rather than just match"). Base commit: `0c3a573`.
Tracker row: geometry self-shielding (row 23). Origin: the 2026-10-15 PARKING entry found by P129 G3.

## Change under test

P129 geometry self-shielding refuses resolved ranges given as R-matrix-limited (LRF=7) parameters, so on ENDF/B-VIII.0,
ENDF/B-VIII.1 and FENDL-3.2c (W-186 and many others) it works only from TENDL. The universal sigmoid curve needs, per
resonance, the energy, the statistical factor g, Γn, Γγ and the total Γ. P139 derives them from LRF=7 spin groups:

- Channels are classified by their particle pair: the incident-neutron pair (MT=2) gives neutron channels, the
  capture pair (MT=102, including the eliminated capture channel) gives capture channels; every other channel
  (fission, charged particles, inelastic) counts only toward Γ.
- IFG=0 (widths): each channel width is |Γ_c| as listed (ENDF-6: the width at the resonance energy).
- IFG=1 (reduced-width amplitudes): Γ_c = 2·P_c(|E_r|)·γ_c², with the channel's penetrability at |E_r| by the same
  convention and radii the existing LRF=7 reconstruction uses; eliminated photon channels have P = 1.
- Γn = Σ over neutron channels, Γγ = Σ over capture channels, Γ = Σ over all channels.
- g = (2J+1)/(2(2I+1)) with J the spin group's |J| and I the target spin of the neutron pair.
- Resonances at E ≤ 0, or with Γn = 0 or Γγ = 0, are skipped as for LRF 1–3.

Everything downstream (z, G, group means, ledger, certificate) is unchanged. Specs that never named an LRF=7 file
produce byte-identical results.

## Gates

Checker: `controls/check_p139.py`, run by the coordinator under the 6 GiB cgroup. Reference: release build of
`0c3a573`. Candidate: release build of the implementation commit.

- **G0.** This protocol's SHA-256 is in `protocols/protocol_hash.txt` in a commit before any P139 evidence.
- **G1.** fmt, clippy with `-D warnings` and the workspace tests, including new unit tests for channel classification,
  both IFG branches, the statistical factor and the skip rules.
- **G2, unchanged elsewhere.** `examples/fns_fe_5min.json`, `examples/shielding_demo.json` and the P129 geometry case
  on the TENDL-2025 W-186 multi-level Breit–Wigner (LRF=2) file (foil 0.01 cm and 0.1 cm) give byte-identical results apart from `ms`
  under reference and candidate.
- **G3, independent recomputation.** On the installed ENDF/B-VIII.1 W-186 evaluation (LRF=7), foil 0.01 cm and
  0.1 cm, the checker parses MF=2 itself, derives Γn, Γγ, Γ and g per resonance, computes z, G and the weighted group
  factors on the run's group structure, and compares them with the ledger's applied factors within 1e-9 relative.
  Every factor lies in [A2, 1] and factors fall monotonically with thickness.
- **G4, representation equivalence.** The checker transcribes the TENDL-2025 W-186 resolved range (LRF=2) into two
  synthetic LRF=7 (KRM=3) files of the same resonances: a neutron channel per resonance with Γn, an eliminated
  capture channel with Γγ, a fission channel with Γf when non-zero, and a competitive channel carrying any listed
  total in excess of the partial widths. One file uses IFG=0 with the widths, the other IFG=1 with reduced amplitudes
  γ = √(Γ/(2P)). Both are hash-pinned in the run spec. Group factors from each equal those from the original LRF=2
  file within 1e-12 (IFG=0) and 1e-9 (IFG=1) relative, in every group.
- **G5, report-only.** ENDF/B-VIII.1 (LRF=7) against TENDL-2025 (LRF=2) W-186 group factors for both foils,
  tabulated. Evaluations differ; there is no threshold.
- **G6.** CI green on the pushed commit, with the committed run-output baselines unchanged.

A gate that fails is recorded as P139-FAIL with its evidence; repairs need an amendment registered first.

## Not claimed

The universal sigmoid curve remains the published single-resonance approximation: interference between channels and
between resonances, which the LRF=7 formalism carries, is not used by it. No measurement validation.
