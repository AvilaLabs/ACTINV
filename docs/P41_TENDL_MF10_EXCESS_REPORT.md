# TENDL-2025 MF=10/MF=9 emitted-state conservation failures — corpus enumeration

Prepared 2026-09-21 by Connor Avila / Avila Labs, from ACTINV P41 corpus audit.
Status: **held for release verification — not submitted.** The maintainer has
already identified the root cause (an unflushed array in TALYS
`channelsout.f90` leaking previous-channel values into emitted-state output)
and stated it will be fixed in the next TENDL version; every class below is a
manifestation of that mechanism. This document is retained as the census and
re-verification checklist for the next release: any class that survives the
fix becomes a reportable residual. Companion to the 2026-09-14 four-file
threshold submission (`TENDL2025_THRESHOLD_SUBMISSION.md`).

## Observation

During construction of ACTINV's TENDL-2025 neutron activation artifact, the
builder's fail-closed conservation check rejected 1,172 of 2,850 published
neutron evaluations. Re-auditing each flagged section at **explicit tabulated
ordinates** (no interpolation) classifies the failures as:

| Class | Files | Signature |
|---|---:|---|
| Emitted > 0 where MF=3 total = 0 | 502 | MF=10/MF=9 declares production at an ordinate where the reaction total is exactly zero — the same signature as the four files already reported |
| Emitted sum > positive MF=3 total | 51 | Σ over emitted states exceeds the tabulated MF=3 total at a shared ordinate — three files (n-Ta180m, n-No254, n-Mt277, all MT=103) sit at *exactly* 2.000×, a signature consistent with a state row carrying the full reaction total in addition to partial rows |
| MF=9 multiplicities sum > 1 | 24 | State multiplicities for a single-residual reaction sum above unity at an ordinate — top values ~1.94–1.99, the same near-double signature |
| Literal `NaN` in MF=3 | 1 | n-Pb208.tendl lines 781 and 1613 contain `NaN` in a numeric field |
| Interpolation-only excess | 594 | Consistent at every declared ordinate; lin-lin interpolation of a sparse emitted-state grid overshoots the denser MF=3 curve between ordinates |

The first four classes (578 files) are tabulated contradictions reproducible
by reading the file text — no group collapse, interpolation, or solver is
involved. The 594 interpolation-only files are a weaker class included for
completeness; they may sit inside the evaluator's tolerance conventions.

## Relationship to the 2026-09-14 submission

The four previously reported files (n-Fe053m, n-Cl035, n-Zr088, n-Y088) showed
a positive MF=10 product cross section at a tabulated energy where MF=3 is
zero. This audit finds **502 files with that same signature** — the reported
cases were the visible tip of a systematic class, concentrated in the
state-resolved production channels (top offenders: MT=16 ×237 files, MT=32
×125, MT=107 ×35, MT=105 ×32 in the ordinate-violating classes).

## Exhibits

All values below are explicit file ordinates; columns 67–80 carry MAT/MF/MT/NS.

### 1. n-Ag096.tendl — same ordinate, total zero, product 6 megabarns

Line 1410 (MF=3, MT=16 — reaction total):

```text
 1.281025+7 0.000000+0 1.300000+7 8.257721-7 1.350000+7 1.816770-54692 3 16    4
```

Line 62473 (MF=10, MT=16, ZAP=47095, LFS=0 — ground-state product):

```text
 1.281025+7 5.997737+6 1.300000+7 8.257721-7 1.350000+7 1.808412-5469210 16    4
```

At the identical energy 12.81025 MeV, the file declares an (n,2n) total of
exactly 0 barns and a ground-state production cross section of 5,997,737
barns. The two tables agree again from the second ordinate onward (8.2577e-7
at 13 MeV), so the defect is a spike at the threshold ordinate — but it is a
tabulated value, not an interpolation artifact.

### 2. n-Ag096m.tendl — same pattern, 20.6 megabarns

Line 1317 (MF=3/MT=16): `1.258289+7 0.000000+0` at 12.58289 MeV.
Line 64637 (MF=10/MT=16, ZAP=47095, LFS=0): `1.258289+7 2.059453+7` —
20,594,530 barns at the identical ordinate.

### 3. n-Pb208.tendl — literal NaN

```text
781:  6.000000+5 4.262064-7 8.000000+5 4.925328-7 1.000000+6        NaN8237 3  1   36
1613: 6.000000+5 4.262064-7 8.000000+5 4.925328-7 1.000000+6        NaN8237 3  3   36
```

MF=3/MT=1 (total) and MF=3/MT=3 (nonelastic) both carry a `NaN` at the
1 MeV ordinate. `grep -n NaN` reproduces it; no parser required.

### 4. Largest zero-total ordinate violations (sample)

| File | MT | Emitted (b) | MF=3 total (b) | Energy (eV) |
|---|---|---:|---:|---:|
| n-Ag096m.tendl | 16 | 2.059e+07 | 0 | 1.2583e+07 |
| n-Ag096.tendl | 16 | 5.998e+06 | 0 | 1.2810e+07 |
| n-Tc090m.tendl | 16 | 1.743e+06 | 0 | 1.1383e+07 |
| n-Ag097.tendl | 16 | 1.617e+06 | 0 | 1.4614e+07 |
| n-Cs118m.tendl | 16 | 1.557e+06 | 0 | 9.8713e+06 |
| n-Rh092.tendl | 16 | 1.352e+06 | 0 | 1.2638e+07 |

247 of the 502 zero-total files emit at least 1 barn; the rest emit
sub-barn values at zero-total ordinates.

## Version continuity

The defect class is not new to TENDL-2025. The identical check fails on
TENDL-2023 files — n_047-Ag-107_4725.dat (MT103/MF=10, 5.1% collapsed excess),
n_047-Ag-107M_4726.dat (3.4%), n_089-Ac-227_8931.dat (3.7%) — while
TENDL-2017's n_4725_47-Ag-107.dat passes (it carries none of these MF=10
sections). The class appears to have entered with the state-resolved
production sections added between the 2017 and 2023 processing campaigns and
is still shipping in TENDL-2025.

## Method

ACTINV's builder requires, per (MT, MF=10 or 9, ZAP): the emitted-state sum
must not exceed the MF=3 runtime total beyond a 0.001 envelope (0.03 for
mechanism-gated interpolation cases), checked on the FISPACT-709 group grid.
1,172 files failed; each was then re-parsed with fixed-width ENDF fields and
re-checked at explicit ordinates only — a violation counts only if it exists
at a tabulated energy, not between ordinates. Full per-file classification:
`results/p41_tendl_mf10_ordinate_audit.json`; full failure messages and
source shas: `~/nuclear-data/tendl-2025-patched/build/BUILD_RECORD.json`.

## Source identification

Library: TENDL-2025 neutron s30 ENDF archive (same archive as the previous
submission; sha256 `e547527688506cbe09813364dcefa2aed11f474139bfa129d7cd4ca24fae21fa`).
The archive was not redownloaded; checks establish the discrepancies in these
archived copies. Four previously reported files are locally patched in our
working copy; all exhibit files below are byte-identical to the archive.

```text
33c39f6ceefdd8a09a4d0c5fe85327f5b35038172e6e775f99518f8626b7c6c0  n-Ag096.tendl
c5d3521fb5d29d2da42953bcbf09b3ea72b4551d2d723187404e80c8c503a02b  n-Ag096m.tendl
96b74fa09b6b76d87c16e6edbef7fb63570dc7645386e6328636731739ce65c0  n-Tc090m.tendl
5b702882c3ac126cb4a78e3406b3cb5212b22f4721621c900584a7edc2a5765c  n-Ac227.tendl
32249bf71ee52a159ef8f94a4cb85d5c456aba13e1a4c4d9129c2304b6dc4137  n-Pb208.tendl
63772d0fbfa9619e8ce6626eeda8bf7b27233c26cfca268f4299b7b799aae647  n-Ag107.tendl
ff5a70af17ee7297bf05ef2d31ceea150695c3a2151a665aa9e4f7dd7c186c3a  n-Mt277.tendl
```

TENDL-2023 corroboration files (IAEA mirror naming):

```text
33fddd5fa9bccf78fd8ae497459314011775407b5cb76bc41d358c6a8d57f5fc  n_047-Ag-107_4725.dat
636d35346d435180980b206cb9dbead87a7c679c70daf3baad511b09c00f17ae  n_089-Ac-227_8931.dat
```

## Independent corroboration

OpenMC's ENDF reader (openmc.data, a fully separate codebase) parses
n-Ag107.tendl identically: MT=103 products include Pd-107 ground and isomer
states whose implied multiplicities reach ~19.5 nuclei per reaction near
9 meV — physically impossible for a single-residual channel. The raw values
are confirmed; only the classification of *which* table is wrong is open.

## Caveats

- The audit identifies inconsistency between a file's own sections; it does
  not determine whether MF=3 or MF=10/MF=9 carries the error.
- The 594 interpolation-only files may be within the evaluator's intended
  tolerances; we flag them but do not assert defect.
- Many zero-total violations sit at sub-threshold ordinates where both
  quantities are physically ~0; the megabarn-scale exhibits (Ag-096/96m)
  are not that.
- ACTINV evicts these files rather than repairing them — the artifact
  therefore carries a documented coverage boundary (1,678 of 2,850 targets).
