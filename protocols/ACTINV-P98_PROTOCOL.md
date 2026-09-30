# ACTINV-P98 — Photonuclear activation with the TENDL-2017 photofission encoding (successor to P94)

Date: 2026-09-30. Status: **frozen before the changed code is written and before any FISPACT-II
value is read.**

## Why this protocol exists

P94 (`b27a9754…`, candidate `debe514a…`) passed G1–G4 and fails G5 as registered. G5 needs all 8
raw TENDL-2017 gamma evaluations built with the candidate, and the candidate cannot build them:

- **Default profile (`none`).** 5 of 8 fail closed on emitted-state sums above the MF=3 total:
  - Cu-63 MT106 ZAP 27060 group 121, an excess of 5.5e-15 b;
  - Nb-93 MT16, Ta-181 MT4, W-186 MT4 and Pb-208 MT4, where MF=3 is zero in the group.
  - For Ta-181 MT4 the raw data show the cause. The MF=3 table starts at 8 MeV with 1.76e-2 b and
    has no threshold point, while MF=10 carries the physical ramp from 0 at 7.616 MeV. These are
    TENDL-2017 source defects, documented and held.
- **`--profile tendl`.** This is the shipped TENDL ingestion mode. It reconciles state sums at
  collapse time and records each such group as `state_sum_normalized`. Under it, 7 of 8 build.
- **Pb-208 still fails** with "declared photofission product yield present". TENDL-2017 writes the
  MT18 total photofission cross section as MF=10 IZAP=0 LFS=0, with an MF=8 ZAP=0 LMF=10
  descriptor and no MF=3 MT18. Ta-181 and W-186 use the same encoding. TENDL-2025 uses the
  IZAP=-1 sentinel that P94 handles. The P94 survey covered TENDL-2025 only.

No FISPACT-II value has been read: the pinned archive is still downloading. P94 is recorded as FAIL
on G5, and its G5 comparison is never run.

P94's G4 checker was run twice:
- **Run 1** failed on MF=10 rows at up to 4.7e-7. The builder's documented state-sum
  reconciliation (common factor T/S where the emitted sum exceeds the MF=3 total, unchanged at or
  below the 1e-15 b floor) was not replicated in the independent code.
- **Run 2** passed at 1.7e-13. The checker now re-derives that rule from its specification. Its
  scaled-group counts match the builder ledger's counts.

That checker change came after a result, and it is disclosed here. P98 reruns G4 from scratch.

## Change under test

Branch `p94-gamma`: P94's change plus one builder rule.

- **Photofission encoding.** When the build opts into a normalization profile (`--profile` not
  `none`), a gamma evaluation's MT18 is read as the total-photofission sentinel, exactly like
  IZAP=-1: validated, used as the MT18 runtime comparator, and omitted as an inventory product.
  - It applies only when all of the following hold:
    - there is no MF=3 MT18;
    - MF=10 MT18 has exactly one section, with IZAP=0 and LFS=0;
    - no IZAP=-1 section is present;
    - MF=8 MT18 declares nothing, or only ZAP=0 LMF=10 LFS=0.
  - Each application writes a ledger line naming the encoding.
  - Under `--profile none`, or when any condition fails, the existing fail-closed error stands.
  - Declared photofission product yields remain out of scope.

The candidate is the release `actinv` built from that branch.

## Gates

Reference: master release `actinv`, whose SHA-256 the checker records; archived as
`target/p94/ref_actinv`. Checker: `controls/check_p94.py` with `ACTINV_GAMMA_PROTOCOL=P98`
selecting `target/p98/` and `results/p98_verdict.json`. It runs under the 6 GB cgroup cap.
Nothing carries over from P94.

- **G0:** the protocol hash is registered before the change is written.
- **G1:** P94's G1 in full. It adds unit tests for:
  - the IZAP=0 encoding, read as the sentinel under a profile;
  - the same encoding failing closed under `none`;
  - a planted second MF=10 MT18 section failing closed;
  - a planted MF=3 MT18 failing closed;
  - an IZAP=0 section with LFS≠0 failing closed.
- **G2:** P94's G2(a)–(c) in full.
- **G3:** P94's G3 in full. In addition, the TENDL-2025 gamma `.npz` must be byte-identical to
  P94's candidate build (`d4590b8e…`), since the default profile is unchanged.
- **G4:** P94's G4 with the run-2 checker: independent parser and integrator, and the independent
  re-derivation of the documented state-sum rule. The 4 TENDL-2025 nuclides must agree to 1e-9
  relative above 1e-12 of each row's maximum, with exact zeros at or above 200 MeV. In addition,
  there must be zero envelope violations, and every independent row must exist in the library and
  the reverse.
- **G5 (FISPACT-II / TENDL-2017), same inputs, spectra and pass bands as P94:**
  - **Build options, fixed now:** `--format tendl --projectile gamma --groups fispact-162
    --temperature-K 0 --decay endf-b-viii-0_decay.dat --decay-fallback jeff-3-3_decay.dat
    --profile tendl`, without continue-on-error.
  - All 8 evaluations must build. The raw files' SHA-256 values are recorded; Cu-63 is
    `fad99af7…`, Pb-208 `222d9875…`.
  - Reported, not gated: every `state_sum_normalized` group and every IZAP=0 read.
  - **FISPACT-II side:**
    - verify the archive SHA-256 `7f305df2…ec8`, then stream-extract `tal2017-g/gxs-162`;
    - reconstruct group values with the P10 G5 reader (`controls/g5_p10_charged.py`
      `parse_independent`/`production_terms`/`processed_row`).
  - **Residual key:** (ZA, isomer label).
    - The ACTINV side uses its canonical label.
    - The FISPACT side maps each raw LFS through the same raw-to-canonical mapping that the
      candidate's G5 index records for that nuclide, MT and ZAP.
    - Raw states that the mapping merges are summed on both sides.
    - A FISPACT raw state with no mapping entry is an unmatched residual.
  - **Pass bands:**
    - one-group values for residuals carrying at least 1e-3 of the nuclide's summed residual
      production under the spectrum;
    - at least 95 % within 2e-3 relative, and all within 2e-2.
  - **Reported:** group rows against P10's 2.5e-3 row tolerance, and unmatched residuals on both
    sides.
  - If the archive cannot be obtained or verified, G5 is NOT RUN and P98 cannot merge.
- **G6:** CI replay, every step exits 0.

Merge only if G0–G6 all pass. A FAIL stands; thresholds are not lowered afterwards.
