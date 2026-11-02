# ACTINV-P184 — IRDFF-II covariances for TENDL-2025's dosimetry reactions in the covariance sidecar

## Purpose

ACTINV's dose and activity uncertainty bands take the cross-section part from the TENDL-2025 MF=33 sidecar
(`actinv-data/v1.2.0/uncertainty/tendl-2025-neutron-709g.cov.npz`, SHA-256 `c19dec86…`). For the dosimetry reactions,
TENDL-2025 takes its cross sections from IRDFF-II but its covariances from TALYS parameter variation. The two
disagree widely. At 14.1 MeV, TENDL-2025's relative standard deviation for Fe-56(n,p) is 50% and IRDFF-II's is 1.1%,
for a cross section the two files give within 0.2%. Over the 59 IRDFF-II reactions compared, TENDL-2025's value is
3 or more times IRDFF-II's in 36 and less than IRDFF-II's in 13 (actinide fission among them). ACTINV's iron R2S
example therefore shows a 51% dose band, almost all of it from Fe-56(n,p).

P184 builds a sidecar in which each reaction that IRDFF-II evaluates takes IRDFF-II's covariance. The covariance is
expressed about TENDL-2025's cross section and includes the mean difference between the two evaluations. P184 then
checks that the sidecar holds exactly that and nothing else changed. It does not show that the band is right. The
band's agreement with brute-force sampling is a later protocol, and shipping the sidecar is a data release that
needs the owner's approval.

## Disclosure of what has been seen

- The 51% iron band and its breakdown (`cross_section_mf33` 50.9% of the dose; Mn-56 activity 50.9%, Cr-51 69%,
  Mn-54 41%, Fe-55 18%), and the TENDL-2025/IRDFF-II standard-deviation table above.
- Development runs of the harness:
  - on Fe-56, Ni-58, V-51, Fe-54 and Am-241 (selection, written matrices, per-bin ratios and differences,
    eigenvalues);
  - a full 2,850-input build with five, then seven, patched files, checked with this protocol's G3 and G4 code;
  - a stage of all 62 selected reactions: its selection and each reaction's largest |b|, the bins where it occurs,
    and its smallest eigenvalue. No sidecar was built from it.
- The gates below were written after those runs. Four defects they found were fixed before registration:
  - a step in TENDL-2025's tables at 30 MeV, where MT=103 and MT=107 drop to zero and the production moves to MT=5;
  - Am-241(n,f), which has two LB=8 blocks;
  - TENDL-2025's MF=3 inside the resolved-resonance range is only a background, which made the bias term
    meaningless there (|b| up to 1e8 for Pu-239 fission). TENDL's side is therefore the activation library;
  - the library group holding 30 MeV straddles TENDL's MT=5 changeover, so the range also ends where TENDL-2025's
    MF=3 ends.
- The FNS iron observation problem was run once with the v1.2.0 sidecar to learn its output format.
- No band or activity uncertainty has been computed with a changed covariance.

## Data

- **IRDFF-II:** `IRDFF-II_ENDF.zip`, SHA-256 `225b2578…` (P24's pin), member `IRDFF-II.endf`, SHA-256 `162b1de7…`.
- **TENDL-2025 sources:** for each of the 2,850 targets in the v1.2.0 sidecar index (`tendl-2025-neutron-709g.cov_index.json`), the file whose SHA-256 is
  the index's `source_sha256`. 2,821 are in the local corpus (`~/nuclear-data/tendl-2025-patched/files/n`). The
  other 29 are extracted from TENDL-2025's `TENDL-n.tgz`, and Pb-208's two literal `NaN` fields are repaired as P10
  Amendment D records. A source with any other hash stops the run.
- **Activation library:** v1.2.0's `tendl-2025-neutron-709g.npz` (SHA-256 `ec4c72bf…`), the library the shipped
  sidecar was built for.
- **Binary:** `actinv` built from the registration commit, which adds `build-covariance --mf33-from`.

## Method

`controls/p184_covariance.py stage` applies this selection rule to every MF=33 section of IRDFF-II. A reaction
(target ZA, MT) is replaced when:

- ZA is a single nuclide (not a natural element);
- TENDL-2025's file for ZA has an MF=33 section for MT;
- IRDFF-II's section is one self-covariance: an LB=5 LS=1 matrix, optionally with LB=8 blocks;
- both files give MF=3 for MT;
- the activation library has a reaction-total row (ZAP = −1) for (ZA, MT), the row ACTINV applies MT's covariance
  through.

Every other IRDFF-II section is recorded as left out, with the reason.

For a replaced reaction, on IRDFF-II's MF=33 energy grid, the replacement is:

    C[k,l] = r_k r_l C_I[k,l] + b_k b_l,    r_k = s_I,k / s_T,k,    b_k = (s_T,k − s_I,k) / s_T,k

- `C_I` is IRDFF-II's relative matrix.
- `s_I,k` is IRDFF-II's cross section averaged with flat weighting over interval k, integrating the lin-lin table
  exactly.
- `s_T,k` is the same average of the activation library's reaction-total row, each group's cross section flat in
  energy, as ACTINV applies covariances. It is the cross section ACTINV computes with. Inside the resolved-resonance
  range, TENDL-2025's MF=3 holds only a background and cannot serve.
- The averages cover only energies where both give the cross section:
  - from the later of IRDFF-II's first point and the library row's first nonzero group;
  - to the earliest of IRDFF-II's last nonzero point, the end of the library row's last nonzero group and
    TENDL-2025's last nonzero MF=3 point.

  Above 30 MeV TENDL-2025 moves most productions to MT=5, and the library group holding 30 MeV straddles that.
- r and b are 0 where `s_T,k` is 0.
- Where IRDFF-II's intervals are narrower than the library's groups, b varies within a group. Because ACTINV takes
  each group as flat, a rate's bias term sums to the group-level difference between the two evaluations.
- IRDFF-II's LB=8 blocks are kept, each F scaled by r² of the interval holding its own interval's midpoint.
- TENDL-2025's subsections that correlate another reaction with a replaced one are removed.

The patched evaluation goes to `mf33/`. The 2,850 sources are hard-linked into `inputs/`. Then:

    actinv build-covariance inputs/ ACTIVATION.npz p184.cov.npz --workers 4 --mf33-from mf33/

## Gates

- **G0:** this protocol's SHA-256 is in `protocols/protocol_hash.txt`.
- **G1 (inputs):** the record shows:
  - the IRDFF-II archive and member hashes above;
  - every one of the 2,850 inputs with its v1.2.0 index hash;
  - the activation library's hash, the same file in the stage and the build;
  - a binary built from a clean checkout of the registration commit;
  - the build and observation runs inside a bounded scope (at most 6 GiB, no swap, 128 tasks and 4 CPUs, the
    owner's 2026-10-31 allowance), the build with exit status 0.
- **G2 (selection):** the checker applies the selection rule itself, to IRDFF-II, the sources and the library.
  Its replaced and left-out sets equal the record's.
- **G3 (replaced reactions):** for each replaced reaction, the new sidecar (its NPY members, read with NumPy) holds:
  - one LB=5 component (MT1 = MT) on IRDFF-II's grid, whose values equal the checker's own computation of C to
    within `5e-7 × max|C| + 1e-12`;
  - one LB=8 component per IRDFF-II LB=8 block, equal to the checker's to the same tolerance;
  - no other component of that target with MT or MT1 equal to the replaced MT.

  Each LB=5 matrix's smallest eigenvalue is at least `−1e-6` times its largest.
- **G4 (nothing else changed):** every component of the v1.2.0 sidecar that does not involve a replaced reaction
  (MT or MT1 replaced, same target) is in the new sidecar, with the same LB, kind, and bit-identical grids and
  values. The new sidecar has no component that is neither such a copy nor a G3 component. The new index's targets
  equal v1.2.0's, apart from `mf33_source_sha256` and the section, component and LB counts of patched targets.
- **G5 (observation):** `examples/fns_fe_5min.json` is run once with each sidecar. Each run adds an `uncertainty`
  section:
  - channel `cross_section_mf33` only;
  - responses: the activities of Mn-56, Mn-54, Fe-53, Cr-51 and Mn-57 at every reported step.

  Both runs exit 0 and report every response. The relative standard deviations are recorded; no threshold applies
  to them.
- **G6:** CI green on the evidence commit.

P184-PASS if G0–G6 hold. One repair round (an amendment with a fresh run) is allowed.

## Allowed sentences

On P184-PASS, filled by the checker from the record:

1. "In ACTINV's P184 sidecar, {N} TENDL-2025 reactions on {M} target nuclides take IRDFF-II's covariance, expressed
   about TENDL-2025's cross section and including the difference between the two evaluations; every other
   covariance is TENDL-2025's, unchanged."
2. "For FNS iron after 5 minutes' irradiation, the cross-section part of the relative standard deviation of the
   {nuclide} activity is {a}% with TENDL-2025's covariances and {b}% with P184's, at {time} after shutdown." This is
   one sentence per G5 response, at the first reported time after shutdown (66 s).

The sidecar is not shipped by P184. A sentence about ACTINV's default band applies only after a data release.

## Commands (coordinator)

- `controls/p184_harness.py stage`, `build` and `observe`, then `controls/check_p184.py`.
- Run from a worktree at the registration commit, inside that bounded scope.
- Outputs go under `target/p184-work`. The sidecar is kept, outside Git, for the release decision; the other
  outputs are deleted after the verdict.
