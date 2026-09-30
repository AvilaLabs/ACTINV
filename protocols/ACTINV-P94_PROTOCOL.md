# ACTINV-P94 — Photonuclear (incident-gamma) activation

Date: 2026-09-30. Status: **frozen before the changed code is written.**

## Background

ACTINV activates materials under neutron, proton, deuteron and alpha fluxes. FISPACT-II also
handles incident gammas, from the TENDL `g` sublibrary. That matters for electron-accelerator
isotope production (for example Mo-100(γ,n)Mo-99), bremsstrahlung targets, and the high-energy
photon field in some fusion and accelerator components.

The public TENDL-2025 gamma archive is in hand:
- `TENDL-g.tgz`: 1,562,708,119 bytes, SHA-256 `01ae9f05…c378`, 2,850 files;
- manifest `~/nuclear-data/tendl-2025/staging/TENDL-g.manifest.json`.

A read-only survey of Fe-56, Cu-63, Ta-181 and W-186 found:
- the headers carry NSUB = 0 and AWI = 0;
- the data run to 200 MeV;
- the residual production uses the same MF=3/6/8/10 families that the proton, deuteron and alpha
  builder already reads;
- photofission appears as the same MF=10 IZAP=-1 sentinel;
- no MF=2 is present.

FISPACT-II's shared `ebins_162` (SHA-256 `4b1ba7ec…0b0e`) is byte-identical to the CCFE-162 source
that ACTINV already vendors.

The FISPACT-II processed TENDL-2017 gamma group library is inside the `TENDL2017data.tar.bz2` object
that P10 already pinned (2,595,437,294 bytes, LFS object `7f305df2…ec8`). That object is public but
rate-limited, and it is being downloaded again now. The raw TENDL-2017 gamma evaluations for 8
nuclides come from the public TENDL-2017 `TENDL-g.tgz` (SHA-256 `dfa0df7f…d23a`). No licensed or
registered data are used.

## Change under test

Branch `p94-gamma`, created from the master commit that registers this protocol.

1. **Projectile.** `Projectile` gains `Gamma`: name `gamma`, NSUB 0, projectile ZA (0, 0).
   - AWI is checked as exactly zero; the relative mass check does not apply.
   - The expected group structure is CCFE-162 at 0 K.
   - Residuals are target + photon − emitted particles, so the neutron MT deltas shift by one mass
     unit.
   - Everything else follows the existing charged-projectile path: MF=6/MT=5 aggregate production,
     MF=8/MF=10 isomer resolution, `fluence_particles_cm2`, and refusal of fission yields and of a
     nonzero temperature.
2. **Missing MF=8.** Some gamma MTs (for example 50/51/91) carry MF=3/6 without an MF=8 product
   declaration. The builder resolves their residual from the ENDF MT reaction definition
   (ground state), or it fails closed. It never drops them silently. Every such row is counted in
   the build ledger.
3. **Build and runtime.**
   - `build-library --projectile gamma` works.
   - Specs accept `"projectile": "gamma"`, and the spec/index projectile check covers it.
   - The CLI, Python and mesh paths carry the projectile through.
4. **Flux particle labels.**
   - `import-flux openmc` records the tally's particle. A tally with a photon `ParticleFilter`
     writes photon units, and neutron output bytes are unchanged.
   - A mesh run fails before solving when its projectile disagrees with the flux file's particle
     label. An unlabelled legacy file is accepted only for neutrons.
5. **Documentation.** `docs/SPEC.md`, `docs/QUANTITIES.md` and the data documentation describe the
   gamma projectile, its data source and its limits: no photofission yields in v1, and no
   Doppler/temperature treatment.
6. **Out of scope.** Publishing a gamma data bundle through `actinv data fetch` is a separate
   release decision and is not part of this protocol.

## Gates

Reference: master release `actinv` (the checker records its SHA-256; archived as
`target/p94/ref_actinv`). Checker `controls/check_p94.py`, run under the 6 GB cgroup cap, with logs
in `target/p94/`.

- **G0:** the protocol hash is registered before the change is written.
- **G1 static and runtime contract:** fmt, clippy `-D warnings`, and
  `cargo test --release -p actinv-core -p actinv-data`. The tests include:
  - `Projectile::Gamma` parse/name/NSUB/ZA, with every mismatch plant failing closed: wrong NSUB,
    nonzero AWI, gamma index with a neutron spec and the reverse, nonzero temperature, fission
    yields;
  - a synthetic one-step gamma activation case giving the analytic parent loss and product feed
    through the CLI, Python, prepared and mesh paths, with no fields differing between paths;
  - the missing-MF=8 rule on a synthetic evaluation;
  - photon-labelled flux import, and a projectile/particle mismatch that fails in mesh.
- **G2 unchanged behaviour:**
  - (a) The 783 P75b single specs (full result JSON without timing keys) and the three mesh
    profiles at 1 thread (bytes, footer without timing) are identical to the reference.
  - (b) The TENDL-2025 proton library, rebuilt with the candidate and with the reference from the
    same inputs and options, is byte-identical in the `.npz`. Its index is identical apart from
    the builder-identity fields, which the checker lists by name.
  - (c) `import-flux openmc` on the P32 neutron statepoint gives identical bytes from both binaries.
- **G3 complete build:**
  - The candidate builds all 2,850 TENDL-2025 gamma evaluations on CCFE-162 at 0 K.
  - It must finish with zero target errors, zero silent unsupported fallbacks and zero convergence
    flags, and the library must load in a gamma spec.
  - Reported: target, row and MT counts; photofission targets; rows resolved by the missing-MF=8
    rule; wall time and peak RSS.
- **G4 independent collapse (TENDL-2025):**
  - For Fe-56, Cu-63, Ta-181 and W-186, a separate Python ENDF-6 parser and exact flat-lethargy
    integrator (not the Rust code) computes every residual group row on CCFE-162.
  - It must agree with the built library to a relative 1e-9 for every group value above 1e-12 of
    that row's maximum.
  - Every group at or above 200 MeV must be exactly zero.
- **G5 same-data cross-code (FISPACT-II / TENDL-2017):**
  - Inputs: the 8 raw TENDL-2017 gamma evaluations (Fe-56, Cu-63, Ni-58, Nb-93, W-186, Ta-181,
    Al-27, Pb-208) built with the candidate, and the FISPACT-II `tal2017-g/gxs-162` records
    extracted from the pinned object after its SHA-256 is verified.
  - Three fixed spectra on CCFE-162 groups below 200 MeV:
    - `gdr_flat_8_30_MeV`: flat in lethargy on 8–30 MeV;
    - `brems_20_MeV`: lethargy width × (1 − E/20 MeV) on 8–20 MeV, evaluated at the group
      geometric centre;
    - `hard_60_MeV`: log-normal in energy, centred at 60 MeV with σ(ln E) 0.30, on 30–200 MeV.
  - Compare one-group values of every matched residual that carries at least 1e-3 of that
    nuclide's summed residual production under the spectrum.
  - Pass:
    - at least **95 %** of (nuclide, residual, spectrum) values agree within **2e-3** relative;
    - all of them agree within **2e-2**.
  - Matched group rows are reported against P10's 2.5e-3 row tolerance, along with unmatched
    residuals on either side.
  - G5 runs when the object's download has completed and been verified. If the object cannot be
    obtained, G5 is recorded as NOT RUN, and P94 cannot merge.
- **G6 CI replay:** every step of the local CI replay exits 0.

Merge only if G0–G6 all pass. A FAIL stands; thresholds are not lowered afterwards.
