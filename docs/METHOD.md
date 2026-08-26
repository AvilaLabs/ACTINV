# Method

**Problem.** Nuclide inventory under a neutron flux spectrum and during cooling: dN/dt = A N, A = decay matrix + Σ_reactions
rate matrix, rates = σ̄ φ with σ̄ the spectrum-collapsed cross section per (target, reaction, product, isomer).

**Data.** Activation cross sections: EAF-2010 (816 targets, every channel separate, MF=9/10 isomer branching), parsed by
our own ENDF-6 reader and pre-collapsed to the FISPACT 709-group structure with flat-lethargy weighting. TENDL-2023
support (resolved-resonance reconstruction — SLBW, MLBW, Reich-Moore — and SIGMA1 Doppler broadening) is implemented
in `controls/resonance.py` and `controls/doppler.py`; the R-matrix-limited formalism (LRF=7) and unresolved ranges
with LSSF=0 are reported as unsupported, never silently approximated. Decay data: ENDF/B-VIII.0 primary, JEFF-3.3
fallback, source recorded per nuclide.

**Solver.** CRAM-16 (Pusa 2016 coefficients, incomplete-partial-fraction recurrence) with our own sparse complex LU
(Gilbert–Peierls, partial pivoting, Smith division). Two prunings: reachable-set (exact) and rate-significance (bounded:
a forward bound on atoms that could reach each nuclide; dropped nuclides and their bounds are written to the result).

**Trace activation.** When burn-up is negligible (recorded per run), the composition is a constant source through a unit
state and only products are solved — CRAM's absolute round-off is then relative to the product inventory, not the bulk.

## Decay photons (P7)

The photon reader consumes ENDF-6 MF=8/MT=457 radiation spectra. The source is the sum of `STYP=0` gamma radiation and
`STYP=9` X rays plus annihilation radiation. For a discrete emission, photons/decay are `FD RI`. For a continuous
distribution they are `FC RP(E) dE`; ENDF interpolation laws 1–5 are integrated analytically for both photon count and
the first energy moment. Electrons (`STYP=8`) and matter-dependent bremsstrahlung are not photon emissions here.

Evaluations do not always close their listed emissions to the record's mean electromagnetic energy. ACTINV therefore
retains the evaluated yields and creates a distinct transport yield:

`source yield = evaluated yield × E_EM / integral(E × evaluated spectrum dE)`.

The raw moment, relative discrepancy and scale are written to the ledger. If `E_EM` is positive but there is no
evaluated photon spectrum, ACTINV invents no spectrum: it emits zero photons and reports the exact omitted-power bound
`activity × E_EM`. Group underflow and overflow are likewise separate rather than folded into edge groups.

The default decay-photon structure is the FISPACT 24-group structure from 0 to 20 MeV. Each group carries an exact
count integral and energy integral; its transport energy is their ratio. Discrete lines remain available alongside the
grouped representation.

## Gamma response quantities

For photon group `g`, with source energy rate `S_g` and centroid `E_g`, the unshielded point-source air-kerma-rate
constant is

`Gamma = 1/(4 pi) sum_g [(mu_en/rho)_air(E_g) S_g]`,

with the requested low-energy cutoff and explicit unit conversions. The contact result implements the FISPACT-II
semi-infinite-slab screening expression

`D_contact = B/2 sum_g [(mu_en/rho)_air(E_g) / (mu/rho)_material(E_g) × S_g]`.

Elemental mass attenuation coefficients are mixed by initial material mass fraction. The reported
`contact_gamma_air_dose_proxy_Gy_h` is an air-dose screening proxy: it is not effective dose, a finite-object solution,
or photon transport. Spatial source distributions and mesh coupling are P8; the OpenMC/MCNP point source is explicitly
a placeholder for them.

Response coefficients use log-log interpolation while retaining duplicate absorption-edge energies. If the response
does not cover a contributing energy or material element, the dose is unavailable and the excluded power/elements are
ledgered.

**Certificate and ledger.** The core computes SHA-256 for the activation library, its index, both decay files and the
optional photon response before solving. Declared hashes and the library/index link fail closed. Every run reports
composition gaps, products without evaluated decay data, fission booked to leakage, numerical-floor/negative
round-off, photon normalization and missing-spectrum bounds, and response coverage.

Known failure modes in activation data, and the controls that guard against each, are collected in
[DATA_TRAPS.md](DATA_TRAPS.md).
