# ACTINV-P192 — ACTINV on FISPACT-II's integro-differential validation table

## Purpose

UKAEA validates FISPACT-II and its libraries against measured effective (spectrum-averaged) cross sections from
fusion neutron fields (UKAEA-R(18)004, Fleming, Sublet and Kopecky). The IAEA's CoNDERC distributes the table of
those measurements with FISPACT-II's calculated values (TENDL-2017) and the neutron spectra each measurement used.
UKAEA gave permission to use the extracts on 2026-11-06. The question:

> On the CoNDERC effective-cross-section table (RR_table_oct2020.txt, 365 measurements in 23 FNG, TU Dresden and
> JAEA-FNS spectra), how close are ACTINV's spectrum-averaged cross sections with its default TENDL-2025 library to
> the measurements, compared with FISPACT-II's published TENDL-2017 values on the same rows?

The answer is a measurement. FISPACT-II closer, or no difference, is still a PASS of this protocol: it certifies the
numbers, not their direction. A spectrum-averaged cross section is a convolution of the library with the spectrum,
so the comparison measures the nuclear data each code used more than the codes.

## Disclosure of what has been seen

- The table's header, its reaction labels and spectrum names, and its first four rows in full (four JAEA-FNS-pos3
  rows, FISPACT-II C/E 0.98, 1.06, 1 and 1.14). No other measured or FISPACT-II value was read.
- The first ten groups of the FNG-heat spectrum, to learn the file format.
- No ACTINV value for any row. Rehearsals of the harness were read for exit status, gate results, the count of
  rows per status and per selection rule, and the number of cross-checked rows within tolerance, and their output
  is deleted. The first found a bookkeeping error in the product check (332 rows `label_inconsistent`); after the
  fix all 365 rows were scored (240 by a total row, 125 by a state row) and all 24 cross-checks agreed.
- ACTINV's earlier FNS work (P172) scored decay heat and activities from JAEA-FNS irradiations, the same facility as
  184 of these rows. TENDL-2025 was not tuned on any of them, but the FNG and TU Dresden rows are reported separately.

## Inputs (pinned in `controls/check_p192.py`, never tracked)

- `RR_table_oct2020.txt` from `https://nds.iaea.org/data/effective/`, and the 23 spectra the table names from
  `https://nds.iaea.org/data/spectra/dat/` (175-group VITAMIN-J; 211 groups for FNG-Mo), under `~/nuclear-data`.
- ACTINV's default activation library: TENDL-2025, 709 groups (`tendl-2025-neutron-709g.npz` of data catalog 1.2.0,
  SHA-256 `ec4c72bf…`) and its index.
- The ACTINV binary built in the bounded scope from a clean checkout of the registration commit (SHA-256 pinned).

## Method (`controls/p192.py run`)

- **Rows.** Each table row is a measured value E with its uncertainty u, FISPACT-II's C/E, and FISPACT-II's
  calculated value C_F ("C - T17"). FISPACT-II's ratio is taken as C_F / E.
- **Reaction labels.** `T-A(n,x)P-A[g|m|n]` maps to the target's ground state and MT (2n 16, 3n 17, p 103, a 107,
  g 102, d 104, np 28, na 22, pa 112, n 4). The product named must follow from the target and channel, or the row
  is excluded as `label_inconsistent`. A suffix g, m or n selects the library row to that product's state 0, 1 or 2.
  A ground-state label for a reaction without state-resolved rows uses the reaction's total row
  (`ground_from_unsplit_total`). No suffix selects the reaction's total row, or the sum over the product's states
  when only state-resolved rows exist. A row with no matching library row is excluded with its reason.
- **Spectrum.** The DATA column (flux per group) is spread onto the library's 709 groups flat in lethargy within
  each source group (FISPACT-II's CNVTYPE 0 and ACTINV's `rebin` default). Flat in energy is computed as a
  sensitivity. ACTINV's value is Σ σ_g φ_g / Σ φ_g over the library groups.
- **Binary cross-check.** For 24 scored rows drawn with seed 192, `actinv inspect xs` with the spectrum as a
  `rebin` spec must give the harness's value within 1e-9 relative.

## Gates

- **G0:** this protocol's SHA-256, and those of `controls/p192.py` and `controls/check_p192.py`, are in
  `protocols/protocol_hash.txt`.
- **G1:** provenance. The table, the 23 spectra, the library, its index and the binary match their pins; the record
  comes from the registration commit with a clean tracked tree; 365 rows; not a rehearsal.
- **G2:** integrity. Every row is scored or excluded with a registered reason; at least 80% are scored; the 24
  cross-checked rows agree.
- **G3:** the measurement, always reported, never a gate. For ACTINV and FISPACT-II on the same scored rows: median
  |ln(C/E)|, geometric-mean C/E, share with |C − E| ≤ 2u, root-mean-square z = (C − E)/u. Paired d = |ln(C_A/E)| −
  |ln(C_F/E)|, two-sided Wilcoxon signed-rank test (zeros dropped, normal approximation with tie correction):
  ACTINV CLOSER if p < 0.05 and median d < 0, FISPACT-II CLOSER if p < 0.05 and median d > 0, else NO SIGNIFICANT
  DIFFERENCE. Reported on all scored rows, on the FNG and TU Dresden rows, per facility, and for the flat-in-energy
  sensitivity.
- **G4:** CI green on the evidence commit.

**P192-PASS** if G0, G1, G2 and G4 hold. One repair round (an amendment with a fresh run) is allowed.

## Allowed sentences

On P192-PASS, filled by `controls/check_p192.py` from the record, and quoted together:

1. "On {n} of the 365 measured effective cross sections in UKAEA's integro-differential validation table
   distributed by the IAEA's CoNDERC (FNG, TU Dresden and JAEA-FNS fusion spectra), ACTINV with its default
   TENDL-2025 library had a median |ln(C/E)| of {a} against {f} for FISPACT-II with TENDL-2017 as published in the
   same table; {label clause} (paired Wilcoxon signed-rank test, two-sided p = {p})."
2. "Within twice the experimental uncertainty were {x}% of ACTINV's values and {y}% of FISPACT-II's; on the {m}
   FNG and TU Dresden values alone, {label clause} (p = {p})."
3. "The calculated values are spectrum-averaged cross sections, so the comparison measures the nuclear data each
   code used (TENDL-2025 against TENDL-2017) more than the codes themselves."

They make no claim about FISPACT-II with TENDL-2025 or any other library.

## Commands (coordinator)

From a worktree at the registration commit, with the binary built there, inside the 6 GiB AGENTS.md scope:

- `python3 controls/p192.py run --actinv-bin BINARY`;
- `python3 controls/check_p192.py`.
