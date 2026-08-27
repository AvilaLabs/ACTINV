# Method

**Problem.** Nuclide inventory under a particle flux spectrum and during cooling: dN/dt = A N, A = decay matrix + Σ_reactions
rate matrix, rates = σ̄ φ with σ̄ the spectrum-collapsed cross section per (target, reaction, product, isomer).

**Data.** The production Rust path in `actinv-data` strictly reads ENDF-6 and deterministically builds EAF-2010 and
TENDL activation libraries. Neutrons use CCFE-709 and a requested temperature; proton, deuteron and alpha use
CCFE-162 at 0 K. Supported neutron reconstruction includes SLBW, MLBW, Reich–Moore, R-matrix-limited, unresolved
infinite-dilution averages and SIGMA1 broadening. Independent Python/NJOY/OpenMC readers remain controls, not runtime
dependencies. Decay data are ENDF/B-VIII.0 primary with a JEFF-3.3 fallback, with source recorded per nuclide.

**Solver.** CRAM-16 (Pusa 2016 coefficients, incomplete-partial-fraction recurrence) with our own sparse complex LU
(Gilbert–Peierls, partial pivoting, Smith division). Two prunings: reachable-set (exact) and rate-significance (bounded:
a forward bound on atoms that could reach each nuclide; dropped nuclides and their bounds are written to the result).

**Trace activation.** When burn-up is negligible (recorded per run), the composition is a constant source through a unit
state and only products are solved — CRAM's absolute round-off is then relative to the product inventory, not the bulk.

## Activation-library construction (P10)

`actinv build-library` accepts one evaluation or a directory ordered by filename bytes. It validates fixed-width
records, numeric fields, section counts/tails, interpolation laws, projectile metadata and duplicate targets before
publishing. Each target checkpoint is addressed by the source hash, normalized options, group-boundary hash and Rust
builder fingerprint; final rows are sorted and the NPZ/index pair is canonical, so worker count and cache reuse do not
change bytes.

Neutron pointwise data start from the raw 0 K evaluation. Resolved SLBW/MLBW/Reich–Moore and limited R-matrix ranges
are reconstructed; `LSSF=0` unresolved ranges add infinite-dilution elastic/capture/fission/competitive averages to
MF=3, while `LSSF=1` retains the already averaged MF=3 values. Requested-temperature SIGMA1 broadening is exact at
0 K and windowed at positive temperature. Finite-dilution self-shielding, probability tables and Bondarenko factors
are not implemented or implied.

Positive isolated lines whose natural width is at most `1e-4` of their Doppler width use a separately area-preserving
analytic kernel. Other lines use adaptive linearization, with a ten-million-point cap and explicit convergence
certificate. Cross sections are collapsed as

`sigma_g = integral(sigma(E) dE/E) / ln(E_hi/E_lo)`.

For charged-particle s30 evaluations, ordinary MF=3/9/10 production is combined with aggregate MF=6/MT=5 residual
yields above 30 MeV. The incident projectile participates in residual arithmetic, multiple products/isomers are
retained, and free emitted neutrons are ledgered rather than inserted as inventory nuclides. Any unsupported law fails
that target with file/MF/MT context; production never substitutes a Python path or silent MF=3 fallback.

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
or photon transport. The OpenMC/MCNP point-source export remains a placeholder; P8 mesh output supplies cell-wise
activation and geometry metadata but does not infer a distributed photon-transport source.

Response coefficients use log-log interpolation while retaining duplicate absorption-edge energies. If the response
does not cover a contributing energy or material element, the dose is unavailable and the excluded power/elements are
ledgered.

## Transport-flux interchange and independent cells (P8)

Canonical neutron values are group integrals `Phi_g` in `n cm^-2 s^-1`. OpenMC tracklength flux tallies are integrated
over cell volume and normalized per source particle, so an imported group is

`Phi_cell,g = (sum_g / n_realizations) source_rate / V_cell`.

MCNP F4/FMesh values are already divided by volume; their import applies `source_rate` without another volume factor.
FISPACT standard `fluxes` values are physical inputs and receive no hidden scaling. Source-native total rows/bins are
checked against compensated group sums to `1e-12` relative and then retained only as provenance diagnostics.

When grids differ, ACTINV assumes the source group has constant flux per unit lethargy, FISPACT-II's default
`CNVTYPE=0` rule. For source interval `[a,b]`, destination overlap `[c,d]` receives

`Phi_overlap = Phi_source log(d/c) / log(b/a)`.

The overlap is clipped to the source and destination intervals. Contributions below and above the activation-library
grid are separately accumulated as underflow and overflow. Compensated sums require
`destination + underflow + overflow = source` to `1e-12` relative; exact boundary arrays bypass arithmetic and copy the
original values bit for bit.

Mesh mode is an embarrassingly parallel collection of ordinary activation problems, not coupled transport or material
inference. The activation library, index, decay chain and optional photon response are prepared once as immutable data.
Each canonical cell is rebinned, pruned and solved through the ordinary core path with one shared material and schedule.
Rayon parallelizes only a bounded chunk; indexed collection restores input order before streaming results. Therefore
thread count changes scheduling, not deterministic result records. Self-shielding, spatial interpolation, transport
feedback and heterogeneous material maps remain outside this method.

## Fission yields, coupled burn-up and pulses (P9)

Neutron-induced fission uses ENDF-6 MF=8/MT=454 independent yields. For parent `i`, the existing MT=18 loss removes
one parent atom at rate `r_f,i`; every product `j` receives

`A[j,i] += Y_j(E_eff) r_f,i`.

The raw yield sum must be two fission fragments within `1e-6` and is never normalized. Products outside the decay
chain receive the same yield-weighted feed in the leakage state, so `mapped yield + leakage yield = raw yield`.
An active fission parent without a supplied evaluation sends one fission event per parent to the established
no-yields leakage path. MT=459 cumulative yields are structurally parsed but cannot feed the matrix; spontaneous
fission yields remain out of scope.

At a fixed incident energy, yields are selected exactly, clamped outside the evaluation, or linearly interpolated over
the union of adjacent product tables, treating an absent endpoint product as zero. Spectrum-average mode first computes
each parent's fission-rate-weighted representative energy,

`E_eff = sum_g(Erep_g sigma_f,g Phi_g) / sum_g(sigma_f,g Phi_g)`,

where `Erep_g = (E_hi - E_lo) / ln(E_hi/E_lo)` is the logarithmic-mean energy consistent with constant flux per unit
lethargy. This is an energy-selection model, not direct energy integration of the yield surface; the selected bracket
and any clamping are certified.

The trace/coupled decision is based on physical exposure. For each initial nuclide `i`, ACTINV computes

`tau_i = L_i sum_k(dt_k m_k)`, `f_i = -expm1(-tau_i)`,

where `L_i` is its total base-spectrum neutron loss rate and `m_k` is the schedule multiplier. Automatic mode holds
the initial bulk constant only when `max(f_i) < 1e-6`; otherwise the full initial material and all products evolve in
one coupled matrix. Explicit modes bypass this choice. Positive multipliers scale all neutron reactions, while a zero
segment is decay-only. Decay remains active in every segment, so replacing separated pulses with a same-fluence
average is generally not equivalent. Elapsed time, multiplier-weighted time and physical fluence are accumulated after
every boundary.

## MF=33 covariance and local uncertainty (P11)

`actinv-data` strictly parses ENDF-6 MF=33 NI covariance forms LB=0--6, 8 and 9 into a separate
`actinv-covariance-1` sidecar. NC records, lumped reactions, foreign/cross-sublibrary references, malformed LIST
dimensions and unknown representations fail with MAT/MF/MT context. Component descriptors, deduplicated energy grids
and binary64 values are offset-indexed arrays; the adjacent index binds the activation library and index, group
boundaries, source manifest, per-target identity/inventory, builder fingerprint and sidecar hash. The ordinary path
does not open or allocate this data when `uncertainty` is omitted.

For covariance collapse, each activation-group integral `Phi_g` is represented as constant flux per unit energy
inside its group while that group's activation cross section remains constant. Integration uses the union of
activation and covariance boundaries. Relative components multiply the applicable activation-row cross sections;
absolute LB=0 is used directly, with product-row/base-row ratios where a File-3 covariance feeds a derived product
row. LB=8 and LB=9 retain ENDF-102's short-range subgroup-width laws. The resulting one-group matrix is the double
flux-weighted integral divided by total flux squared. Zero total flux produces zero nominal cross sections and zero
covariance.

Each activation row is a distinct parameter `(target, MT, ZAP, LFS, LMF)`. Rows with a valid target/MT
self-covariance are covered; MF=10 rows require MF=40 and are therefore uncovered in P11. A missing cross component
between covered rows contributes zero as absent evaluated covariance and is counted. No covariance value is projected
to positive semidefinite form, clipped, decorrelated or otherwise repaired. Both orientations are assembled and
symmetry is diagnosed before propagation.

CRAM order is selectable as 16 or 48. For a parameter direction `dA`, every incomplete-partial-fraction pole reuses
the primal factorization and differentiates the selected recurrence exactly:

`M z = y`, `M = dt A - theta I`, `M dz = dy - dt dA z`.

Tangents cross irradiation, pulse and cooling boundaries with the nominal state; a zero-flux segment has exact
`dA = 0`. Trace sources and coupled depletion use the same row-to-matrix mapping as the nominal solve. Reported
sensitivities are local derivatives at the fixed mode, prune set, schedule and nominal nuclear data, in response units
per barn.

For response sensitivity vector `S` and the covered collapsed matrix `C`, the MF=33 variance is `S C S^T`. A
materially negative result fails. Only a negative residue no larger than
`128 epsilon sum(abs(S_i C_ij S_j))` is reported and set to zero. The two-sided normal interval uses the requested
confidence; the selected-versus-alternate CRAM response difference remains a separate numerical-method bound and
expands a separately named conservative interval. Neither interval is a tolerance limit or safety margin. Decay and
MF=32, production/MF=40 and yield, incident-flux, material-composition, response-coefficient and model uncertainties
are excluded and listed in every uncertainty ledger/certificate.

## Radiological response evaluation (P12)

ACTINV evaluates only responses selected from an explicit, hash-pinned `actinv-radiological-table-1` file. For step
activity `a_i` in Bq/g and a clearance or waste limit `L_i` in Bq/kg, the reported dimensionless sum of fractions is

`I = sum_i(1000 a_i / L_i)`.

For an ingestion or inhalation coefficient `h_i` in Sv/Bq, the reported value is

`D = sum_i(a_i h_i)` in `Sv/g_material_intake`.

ACTINV does not turn that value into a scenario dose: it applies no intake amount, occupancy, frequency, chemical or
aerosol selection, retention model, or safety factor. Those choices belong to the table and the user's analysis.
Positive activity without a coefficient is excluded from the numerical sum but is never silent: every result reports
covered and missing activity, coverage fraction, and the exact missing-nuclide list. Complete-coverage mode converts
any such omission into an error. Zero total activity has coverage one.

The table is verified and parsed before solving. Prepared and mesh paths share the same immutable parsed table, so
workers do not reread or duplicate it. The certificate binds the table's declared and computed hashes, title, source,
edition, jurisdiction, response kind, basis, and coefficient count; the ledger mirrors per-step coverage.

**Certificate and ledger.** The core computes SHA-256 for the activation library, its index, both decay files and the
optional photon response, every fission-yield evaluation, an optional covariance sidecar/index, and an optional
radiological table before solving.
Declared hashes and the library/index link
fail closed. Every run reports composition gaps, explicit-isotope masses, products without evaluated decay data,
fission selection/balance/leakage, burn-up selection, numerical-floor/negative round-off, photon normalization and
missing-spectrum bounds, and response coverage.

Known failure modes in activation data, and the controls that guard against each, are collected in
[DATA_TRAPS.md](DATA_TRAPS.md).
