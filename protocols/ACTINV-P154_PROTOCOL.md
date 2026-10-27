# P154 — Native damage energy from evaluations, exact NRT and arc-dpa (FISPACT-II `ALLDISPEN`/`ATDISPEN` data gap)

Registered 2026-10-27, before any source change for the phase. Second parity wave; manual reading in
`docs/maintainers/FISPACT_MANUAL_KEYWORD_AUDIT.md` (P151).

## Change under test

`damage` folds a damage-energy table (MT=444) with the flux. TENDL and EAF as distributed carry no MT=444, so today a
user needs NJOY (HEATR) before any dpa. FISPACT-II reads MT=444–447 from NJOY-processed libraries. P154 computes the
damage energy directly from the evaluation.

1. **`actinv build-damage --method kinematic`** (the existing MT=444 path is unchanged and stays the default). From
   each neutron ENDF-6 evaluation, on the pointwise grid the activation-library builder already reconstructs at 0 K
   (resonances included), the damage-energy production cross section is computed channel by channel:
   - **elastic** (MT=2): two-body recoil E_R = 2A/(A+1)²·E·(1 − μ_cm), integrated over the MF=4 angular distribution
     (Legendre, tabulated or mixed; laboratory-frame data converted to the centre of mass);
   - **discrete two-body levels** (MT=51–90 and the charged-particle level ranges MT=600–648, 650–698, 700–748,
     750–798, 800–848) with their Q-values and MF=4 or MF=6 LAW=2 angular data;
   - **single-particle continuum** (MT=91, 649, 699, 749, 799, 849) given as MF=6 LAW=1 (Kalbach–Mann): the recoil
     follows from momentum conservation for each emitted energy and angle;
   - **capture** (MT=102): incident-momentum recoil plus the photon recoil from the evaluated photon emission where
     the evaluation gives it, otherwise from the excitation energy emitted as one photon (an upper bound, flagged).
   Recoil energy T is converted to damage energy T_d by the Lindhard partition in Robinson's fit (as NJOY HEATR's
   damage function), with the recoil and lattice Z and A of the target's element, and T_d = 0 below the displacement
   energy. Every other non-elastic channel with a cross section (multi-particle emission, MT=5, fission, channels
   lacking the distributions above) is **uncovered**: its cross section is reported per group, and its damage energy
   is bounded above by the maximum recoil energy its kinematics allow, so the table gives a computed value and an
   upper bound, never an uncovered channel counted as zero.
2. **Table format `actinv-damage-table-2`**: per target and group, the total damage energy (as table 1), the partials
   elastic / discrete inelastic (MT=51–91) / disappearance (MT=102–120 and 600–849) in NJOY's MT=445–447 grouping,
   the uncovered cross section and damage-energy upper bound, and two displacement cross sections integrated over the
   recoil spectrum at the element's displacement energy E_d: exact NRT (0 below E_d, 1 up to 2E_d/0.8, 0.8T_d/(2E_d)
   above) and arc-dpa (Nordlund et al., Nat. Commun. 9 (2018) 1084, with that paper's element parameters b and c,
   cited in the table). `damage` reads table 1 and table 2.
3. **Run outputs** (only with a table-2 file): per step `dpa_nrt` and `dpa_arc` (cumulative and rate) beside the
   existing linear `dpa`, their uncovered upper bounds, and, when `gas` is also requested, `he_appm_per_dpa`. A
   displacement energy in the spec that differs from the one a table-2 file was built with is refused for the
   recoil-integrated quantities.

Beyond FISPACT-II: no NJOY step; arc-dpa and exact NRT need the recoil spectrum, which a folded MT=444 cannot give;
the uncovered damage is bounded instead of absent.

## Gates

Checker: `controls/check_p154.py` (with `--self-check`). Reference: v1.6.1 release (`79e1e860…9c072`). Independent
reference: NJOY2016.79 (local build) RECONR + HEATR at 0 K, partials 444–447, displacement energy equal to ACTINV's,
on TENDL-2025 (patched) Fe-56, Cr-52, Ni-58 and W-184, collapsed by this script on the 709-group structure.

- **G0.** This protocol's SHA-256 is in `protocols/protocol_hash.txt`.
- **G1.** fmt, clippy `-D warnings`, workspace tests including kinematics, partition-function and MF=4/MF=6 parser
  tests.
- **G2, defaults unchanged.** `examples/damage_demo.json` and `examples/fns_fe_5min.json` byte-identical to the
  reference apart from `ms`; `build-damage` without `--method` byte-identical on the P23 corpus.
- **G3, elastic.** Group elastic damage energy agrees with NJOY MT=445 within 2 % in every group above 1 keV where it
  exceeds 1e-6 of its maximum; the distribution of differences below 1 keV is reported.
- **G4, inelastic and disappearance.** Computed discrete-plus-MT=91 damage agrees with NJOY MT=446, and computed
  capture-plus-charged-particle damage with MT=447, within 5 % in every group where the channel exceeds 1e-3 of the
  group total, for channels fully covered in that group; groups with uncovered contributions are reported.
- **G5, total and bound.** NJOY MT=444 lies within [computed, computed + uncovered upper bound] in every group (within
  the G3/G4 tolerances); the FNS-spectrum-collapsed totals and their difference from NJOY are reported.
- **G6, displacement quantities.** In an FNS Fe run with a table-2 file, `dpa`, `dpa_nrt` and `dpa_arc` are
  reproduced independently from the table within 1e-12 relative; the arc parameters equal the cited paper's
  values; `he_appm_per_dpa` equals the ratio of independently read helium production to `dpa`; the refusal of a
  mismatched displacement energy writes nothing.
- **G7.** CI green on the pushed commit; committed baselines unchanged.

## Verdict

P154-PASS if G1–G7 hold. A failing gate is preserved under `results/failures/p154/`; one repair round, registered as
an amendment first.

## Not claimed

Damage energy is computed only for the channels and data forms listed; uncovered channels carry an upper bound, not
a value. The partition function is the Lindhard–Robinson model; electronic-stopping refinements, cascade simulation
and temperature effects are not modelled. arc-dpa is available only for elements with published parameters.
