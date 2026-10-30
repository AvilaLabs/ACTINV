# ACTINV-P167 — activation reaction rates in the NPI Řež quasi-monoenergetic neutron fields

## Purpose

All of ACTINV's measured integral activation evidence comes from one facility, JAEA FNS, plus the IRDFF-II
spectrum-averaged cross sections in fission spectra (P17). This protocol adds a second, independent facility with an
open publication: the 7Li(p,n) quasi-monoenergetic neutron source on the U-120M cyclotron of the Nuclear Physics
Institute of the Czech Academy of Sciences (NPI CAS, Řež). Foils were irradiated in two fields, from 12.4 MeV and
14.4 MeV protons. The authors measured reaction rates from the foil activities and the neutron spectrum at the foils
by stilbene spectrometry, and published both. The question:

> How close are the reaction rates calculated with ACTINV's shipped TENDL-2025 library (data catalog 1.2.0) to the
> rates measured in the two Řež fields?

Accuracy is reported, not gated. The numbers are published in the handbook and the competitive benchmark report
whatever they are.

The fields peak at about 10–12.5 MeV with a broad tail down to 1 MeV, unlike both the fission spectra of P17 and the
14 MeV FNS source. The observable is the reaction rate, the activation cross section folded with the spectrum, so
this tests ACTINV's activation data. It does not test decay data, inventories or decay heat.

## Source (frozen before any scored ACTINV calculation)

- **Paper.** Z. Matěj, M. Košťál, M. Majerle et al., *The methodology for validation of cross sections in quasi
  monoenergetic neutron field*, arXiv:2306.09353v1 (5 June 2023), `~/nuclear-data/npi-rez-pli/2306.09353v1.pdf`,
  SHA-256 `7ef44f1d94aa629639dec2e08555717d8582c85a3b8cbcc382834c847b6064c5`. It is distributed under arXiv's
  non-exclusive licence. The PDF is not redistributed. The repository holds only the numbers extracted from its tables,
  with the citation.
- **Extraction.** `controls/p167_rez.py --extract` reads, through `pdftotext -layout` (poppler 26.01.0):
  - Tables 2 and 3: measured reaction rates per target atom, with E50 and uncertainty.
  - Tables 4 and 5: the evaluated spectra at the foils, as flux per 0.1 MeV bin (Eup − 0.1, Eup], with 113 bins from
    0.9 to 12.2 MeV and 126 bins from 1.0 to 13.6 MeV.
  - Tables 6 and 7: the authors' C/E − 1 for IRDFF-II, JEFF-3.3, JENDL-4 and ENDF/B-VIII.0, with the comparison
    uncertainty.

  It writes `results/p167/rez_tables.json`, SHA-256
  `7a2281ba2a2a5b6be0aeb9ab813ee09da1817f0be1daee16cd751b216574921a`. The harness refuses to run unless a fresh
  extraction equals this file.
- **Reading check (development gate D, done).** Folding the IRDFF-II 725-group evaluation with the extracted spectra
  reproduces every one of the authors' 27 published IRDFF-II C/E values within 0.30 percentage points. The largest
  differences are ⁵¹V(n,α) at 12.4 MeV (−1.97% against −1.7%) and ¹⁹⁷Au(n,2n) at 14.4 MeV (3.20% against 2.9%). This
  fixes the reading of the spectrum's units and bins.

## What is scored

- **Scored (29 points).** The reactions in the authors' validation Tables 6 and 7, with their comparison uncertainty:
  - 12.4 MeV field (13 reactions): ⁵⁴Fe(n,p), ⁴⁷Ti(n,p), ⁴⁶Ti(n,p), ⁵⁹Co(n,p), ⁶⁰Ni(n,p), ⁵⁶Fe(n,p), ²⁴Mg(n,p),
    ⁵⁹Co(n,α), ⁴⁸Ti(n,p), ⁵¹V(n,α), ¹⁹⁷Au(n,2n), ⁵⁸Ni(n,x)⁵⁷Co and ⁵⁹Co(n,2n).
  - 14.4 MeV field (16 reactions): the same 13 plus ⁵⁴Fe(n,α), ¹⁹F(n,2n) and ⁵⁵Mn(n,2n), as in Table 7.
- **Normalization, not scored.** ⁵⁸Ni(n,p) and ²⁷Al(n,α) in each field. The authors normalized the spectra to these
  two rates (section 4.3), so they are not independent checks. Their ACTINV C/E is reported as a diagnostic of the
  normalization.
- **Secondary, not pooled.** ⁵⁸Ni(n,2n) in the 14.4 MeV field. Its threshold is 12.4 MeV, so the rate depends on the
  spectrum's tail, where the bin uncertainties are 7–61%. The authors did not validate it. It is reported alone, with
  the measurement uncertainty only.

## ACTINV calculation

- **Library.** The shipped TENDL-2025 709-group neutron library of data catalog 1.2.0,
  `actinv-data/v1.2.0/activation/tendl-2025-neutron-709g.npz`, SHA-256
  `ec4c72bf598dc8ad3d533d9cfafdcf493e2d1f949a3e4db6251495659b68cc44`, and its index,
  `8bd19b4001c246758e739cd0067a0087e1ce5c2157438dae97bd52e1d3beb3fb`. This is the library P17 used.
- **ACTINV's own path.** For each field and target, `actinv inspect xs LIBRARY --target X --spectrum S --out F`. The
  spectrum `S` is the extracted 0.1 MeV histogram with `rebin: {convention: equal_energy}` onto `fispact-709`. Flat in
  energy inside each bin is the reading the IRDFF-II check confirmed.
- **Measured product.** The calculated rate is ACTINV's production of the measured product nuclide from that target:
  the sum of `reaction_rate_per_target_atom_per_s` over every library row with that product, over all reactions and
  all states. This matches activities counted after isomers have decayed; the paper reports the ⁵⁸ᵐCo shares
  explicitly. ⁵⁸Ni(n,x)⁵⁷Co is direct ⁵⁷Co production; ⁵⁷Ni from ⁵⁸Ni(n,2n), measured at 0.3% of it, is not added.
- **Independent fold.** The harness also folds the same library rows itself, with an exact overlap of each 0.1 MeV
  bin with the 709 groups, and records both values. The `equal_lethargy` rebin is recorded as a sensitivity.
- **Binary.** The P163 release-profile build, `186cc2587bb61766b3b3c426c61cd44f00ed472401026dea2ca3dd595c8d1194`,
  from d6f516b. The evidence commit's `crates/` tree must equal d6f516b's. This protocol changes no ACTINV source.
- Runs inside the bounded scope from AGENTS.md.

## Development runs and data exposure

Disclosed so the reader can judge independence:

- The IRDFF-II reproduction above (no ACTINV data involved).
- One trial of `actinv inspect xs` for Co-59 in the 14.4 MeV field, to learn the output format. I saw one ACTINV
  value: ⁵⁹Co(n,2n) at 14.4 MeV, rate 2.206 × 10⁻¹⁸ s⁻¹ against the measured 2.38 × 10⁻¹⁸.
- One full harness run, with output limited to mechanical agreement; no C/E was printed and the record was deleted.
  ACTINV's `inspect xs` and the independent fold agreed to 6 × 10⁻¹⁶ relative on all 34 rows. `equal_lethargy`
  differed from `equal_energy` by at most 7 × 10⁻⁵.

No library, rebin convention, product rule or reaction set was chosen or changed after an ACTINV result. All of them
are fixed above. There is nothing to tune: the library is the shipped one.

## Registered summary

`controls/check_p167.py` writes `results/p167_verdict.json`. For the 29 scored points, pooled and per field, it
gives:

- point count;
- geometric-mean C/E;
- median, 90th-percentile and maximum |ln C/E|;
- the fraction within 1σ and within 2σ of the authors' comparison uncertainty.

On the same points, as context, it gives the same statistics for the four libraries in the authors' Tables 6 and 7
(C/E = 1 + published value / 100). IRDFF-II covers 27 points, because it has no ⁵⁸Ni(n,x)⁵⁷Co. It also lists the
normalization and secondary diagnostics.

Allowed sentences, filled from the computed numbers only:

1. "Against {n} measured reaction rates in two quasi-monoenergetic 7Li(p,n) neutron fields at NPI Řež (12.4 and
   14.4 MeV protons; Matěj et al., 2023), ACTINV with its TENDL-2025 library has a median |ln C/E| of {m} and a
   geometric-mean C/E of {g}."
2. "On the same points the authors' calculations give a median |ln C/E| of {m_endf} with ENDF/B-VIII.0, {m_jeff}
   with JEFF-3.3 and {m_jendl} with JENDL-4, and {m_irdff} with IRDFF-II on the {n_irdff} points it covers."
   IRDFF-II is a dosimetry library fitted partly to integral data such as these. It is context, not a peer; this
   sentence may only appear with sentence 1.
3. "{k} of the {n} ACTINV values are within 2σ of the measurement."

Rounding: three decimals. No sentence may call ACTINV's data better or worse than another library's. The
comparison is of evaluations, and every column uses the same measured spectrum.

## Gates

- **G0.** This protocol's SHA-256 (and any amendment's) is in `protocols/protocol_hash.txt`.
- **G1.** Inputs and environment:
  - the PDF, library, index and IRDFF-II archive hashes equal the pins;
  - the tables file equals its pin and, when the PDF is present, a fresh extraction;
  - the binary is the pinned one, and the crates tree is d6f516b's;
  - the tracked tree was clean;
  - the bounded cgroup.
- **G2.** Fidelity:
  - each field has the registered spectrum bins and range, and the registered reactions with their statuses;
  - every IRDFF-II fold reproduces the published IRDFF-II value within 0.35 percentage points;
  - every ACTINV rate is finite and positive and equals the independent fold within 10⁻⁹ relative;
  - the `equal_lethargy` sensitivity is within 10⁻³.
- **G3.** The summary and sentences are recomputed from the record and written to (or, with `--no-write`, equal) the
  verdict file.
- **G4.** CI green on the pushed evidence commit.

## Verdict

P167-PASS if G0–G4 hold, whatever the agreement. One repair round, registered as an amendment first.

## Not claimed

- Anything about decay heat, inventories, isomer branching or the FNS-type 14 MeV field. The scored observable is a
  production rate, summed over the product's states.
- That this qualifies the measured spectra or the authors' normalization. ACTINV uses them as published.
- A ranking of evaluated libraries.
- Anything about the spectrum below 0.9 MeV, which the paper does not give. Reactions with thresholds above about
  1 MeV are insensitive to it; all scored reactions are threshold reactions.
