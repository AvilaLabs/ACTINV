# ACTINV P7 — decay-photon source and gamma-dose proxy

**Roadmap row:** P7 (first phase of the v0.2 milestone). **Opened:** after the P6/v0.1 close. **Time box:** two
calendar days. **External acts:** the principal's. This protocol covers decay photons and their source-term exports;
spatial/mesh coupling remains P8 and photon transport remains out of scope.

**Minimum gate input** (standing rule 7): four evaluations extracted from the already-pinned ENDF/B-VIII.0 decay
sublibrary — Co-60 (discrete gamma and X-ray spectra), Cs-137 plus Ba-137m (the equilibrium-family dose-constant
case), and Mn-68 (continuous gamma spectrum) — the NIST dry-air response and elemental Fe attenuation tables, and
the existing 10-target CI activation library. No activation-library build and no full FNS rerun.

## Normative physics and wire-format choices

1. “Decay photon” means the sum of ENDF-6 MF=8/MT=457 `STYP=0` gamma radiation and `STYP=9` X rays plus
   annihilation radiation. Discrete intensity is `FD*RI`; continuous intensity is `FC*RP(E)`, integrated using the
   ENDF interpolation law. Internal-conversion electrons (`STYP=8`) and beta bremsstrahlung generated in matter are
   not photons in this source and are not estimated in P7.
2. Both the evaluated raw intensity and the transport-source intensity are retained. For a nuclide with a nonzero
   evaluated photon spectrum and positive mean electromagnetic energy, one explicit scale factor makes the transport
   spectrum's energy moment equal `E_EM`. This is necessary because some evaluations have incomplete emission lists.
   The factor, raw closure error and affected nuclide are ledgered; it is never silent. A nuclide with `E_EM > 0` but
   no evaluated photon spectrum contributes no invented photons and is ledgered with its unrepresented gamma-power
   bound.
3. The default multigroup structure is the FISPACT 24-group decay-photon structure from 0 to 20 MeV. Custom strictly
   increasing boundaries are allowed. Every group reports photon rate and emitted power, with an energy centroid
   derived from the same integral; underflow/overflow is reported and never folded into an edge group.
4. The specific gamma constant is the unshielded point-source air-kerma-rate constant above a 20 keV cutoff, computed
   from the photon spectrum and NIST dry-air mass energy-absorption coefficients. Units are both
   `Gy m^2/(Bq s)` and `mGy m^2/(GBq h)`. The Cs-137 reference means Cs-137 with Ba-137m in secular equilibrium.
5. Contact gamma dose is the FISPACT-II semi-infinite-slab approximation
   `B/2 * sum[(mu_en/rho)_air / (mu/rho)_material * S_gamma]`, with default build-up `B=2`. NIST elemental mass
   attenuation coefficients are mixed by initial material mass fraction. The result is labelled
   `contact_gamma_air_dose_proxy_Gy_h`; it is a screening proxy, not a geometry-resolved or effective dose.
6. Photon interaction-response data are a separate `actinv-photon-response-1` JSON input with a declared SHA-256.
   The core verifies that hash, records the computed hash in the certificate, and fails closed on mismatch. P7 also
   repairs the existing certificate path so the activation library, its index, and both decay files carry computed
   hashes rather than unverified paths or a merely repeated declaration.
7. `actinv-spec-1` gains an optional `photon` object: group structure/custom boundaries, response file and hash,
   build-up factor, and gamma-constant cutoff. Existing specifications remain valid. Photon results are per gram and
   also carry total strength using `material.mass_g`.
8. OpenMC export is a valid Python fragment containing `openmc.stats.Discrete` and an
   `openmc.IndependentSource(particle="photon", strength=...)`. MCNP export is a valid `MODE P`/`SDEF PAR=P ERG=D1`
   plus discrete `SI1 L`/`SP1` fragment. Both use multigroup energy centroids, preserve normalized probabilities, and
   use total photons/s as source strength/weight. Their default point at the origin is an explicit placeholder for the
   user or P8 to replace with spatial data.

## Deliverables

1. Rust MF=8/MT=457 spectrum reader for discrete and continuous records, including ENDF interpolation metadata,
   exposed by `actinv-data` and independently inspectable by the dump utility.
2. Per-step line and multigroup photon sources, source power, energy closure, specific gamma constants, and contact
   dose proxy through the one Rust spec-to-result path used by CLI, Python and harness.
3. `actinv export-openmc RESULT.json STEP OUT.py` and `actinv export-mcnp RESULT.json STEP OUT.sdef`.
4. A reproducible response-data builder for NIST X-ray mass attenuation/energy-absorption tables; response files stay
   outside the repository and are pinned like the nuclear inputs.
5. Updated specification, method, data, ledger and user documentation.

## Gates

**G1 Spectrum reader.** On the minimum four evaluations, Rust and an independent Python ENDF reader agree at 0.0 on
record counts and at 1e-12 relative on every spectrum header, discrete field, interpolation breakpoint and continuum
point. The delivery audit parses all 3,821 primary decay evaluations without a structural error and reports counts by
`STYP`/`LCON`; that audit is not a prerequisite for starting the other gates.

**G2 Source conservation.** For each minimum-input nuclide with evaluated photons, line-plus-continuum integration and
24-group collapse preserve photon count to 1e-12 and the energy-normalized source integral equals `E_EM` to 1e-6
relative (the roadmap criterion). Every normalization and every energy outside the group structure appears in the
ledger. A planted missing spectrum produces zero invented photons and the exact omitted-power bound.

**G3 Inventory integration and identity.** On the existing Fe CI spec, CLI = Python = harness at 0.0 for every photon
line, group, dose scalar, ledger entry and certificate field. At every step, the total source is the sum of its nuclide
contributions to 1e-12; source power agrees with the result's gamma heat wherever the represented-spectrum fraction is
complete, otherwise the difference equals the ledgered bound.

**G4 Dose references.** From ENDF/B-VIII.0 spectra and the pinned NIST response, the calculated 20-keV-cutoff air-kerma
constants are within 2% of the tabulated values Co-60 = 0.309 and equilibrium Cs-137/Ba-137m = 0.078
`mGy m^2/(GBq h)`. The contact-dose result for a planted Fe slab agrees with an independent implementation of the
FISPACT equation to 1e-12 relative, and per-nuclide contributions sum to the total to 1e-12.

**G5 Transport exports.** Independent readers recover the same nonzero energy centroids and normalized probabilities
from the OpenMC and MCNP fragments at 0.0; both strengths equal the selected step's total photons/s at 1e-12. The
OpenMC fragment parses as Python and the MCNP cards obey line-length/continuation rules.

**G6 Provenance and regression.** A declared response or library hash mismatch is a hard error. Computed SHA-256 values
for library, library index, primary/fallback decay and response inputs appear through all entry points. Existing P5/P6
controls remain green; photon work does not change any pre-P7 inventory, activity or heat scalar.

## Verdict (`controls/check_p7.py`)

P7-PASS: G1-G6. P7-CONDITIONAL after one repair round on a gate. P7-FAIL otherwise. Standing rules 1-7 apply.
