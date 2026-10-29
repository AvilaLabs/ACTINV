# ACTINV-P162 — starting points for a new problem: a cited material library and analytic source spectra

## Purpose

A new problem needs a composition and a neutron spectrum. Today the app offers neither: users load the iron example
and overwrite it, or type every weight fraction and paste 709 group values. P162 ships two kinds of starting data
with the program, used by the app's New problem flow and by `actinv new --material ID --source SHAPE`:

1. **A material library.** Each entry is a composition in weight percent with its citation.
2. **Four analytic source spectra.** Each is a standard closed-form shape, integrated over each group of the 709-group
   structure and scaled to a total flux the user gives.

These are physics inputs: a wrong trace element or a mis-integrated shape skews every result built on it. This phase
fixes what is shipped and how each item is checked. The screens that use them are not part of it.

## The library

The library is `crates/actinv-core/data/materials-v1.json` (schema `actinv-materials-1`), embedded in the binary.
Each entry carries:

- `id` and `name`;
- `category`;
- `composition_wt_percent`, with element symbols only, every value positive, summing to 100;
- `basis`, either `nominal` or `specification_maxima` (trace elements at their specified maximum, the major element
  as the balance), or `measured_heat` (a published analysis of one heat);
- `density_g_cm3`, or null where no primary source gives one;
- `source` (citation, table, URL);
- `note`, which states what the composition leaves out when it leaves out trace elements that drive activation
  (Co, Nb, Ag, Eu, Ta, Mo, Ni, Cu).

The entries for this version, with the source each is taken from (research record: `results/p162/sources.json`):

| id | basis | source |
|---|---|---|
| `eurofer97` | specification maxima for O, B, Al, Si, P, Ti, Co, Ni, Cu, Nb, Mo; majors at range midpoints; Fe balance | EUROFER97 specification as tabulated in Chakin et al., Nucl. Mater. Energy 46 (2026) 102079, Table 3 |
| `eurofer97-3-measured` | measured heat (KIT analysis of EUROFER97-3); "<x" values taken at x | same table |
| `ss304`, `ss304l`, `ss316` | nominal | PNNL-15870 Rev. 1 (2011), Steel, Stainless 304, 304L and 316 |
| `inconel718` | nominal | PNNL-15870 Rev. 1, Inconel 718 |
| `zircaloy4` | nominal | PNNL-15870 Rev. 1, Zircaloy-4 |
| `al6061` | nominal | PNNL-15870 Rev. 1, Aluminum 6061 |
| `concrete-ordinary` | nominal | PNNL-15870 Rev. 1, Concrete, Ordinary (NBS 03) |
| `water` | nominal | PNNL-15870 Rev. 1, Water, Liquid |
| `graphite-reactor` | nominal | PNNL-15870 Rev. 1, Graphite (reactor grade) |
| `tungsten` | specification maxima for C, N, Fe, Ni, Si; W balance | ASTM B760-07 Table 1; density PNNL-15870 Rev. 1 |
| `tantalum`, `niobium`, `titanium-grade2` | specification maxima for the listed elements; balance | ASTM B708-05, B393-05, B265-08b; densities PNNL-15870 Rev. 1 |
| `copper` | nominal | PNNL-15870 Rev. 1, Copper |
| `beryllium-s65` | specification maxima, with O from BeO at its 0.90 wt% maximum; Be balance | Materion S-65 data sheet |
| `v-4cr-4ti` | measured heat (NIFS-HEAT-2) | Miyazawa, Muroga, Hishinuma, J. Plasma Fusion Res. Series 11 (2015), Table 1 |
| `pb-15.7li` | nominal; 15.7 atom % Li converted to weight percent with natural Li | Favuzza, Results in Materials 23 (2024) 100622 |

Excluded for want of a primary source that could be read: F82H, SS316L(N)-IG, CuCrZr-IG. They are recorded in
`results/p162/sources.json` with the reason, and can be added by an amendment once their specifications are read.

## The spectra

All four are defined on the `fispact-709` structure and scaled so the group values sum to the total flux given.
Each group value is the integral of the shape's flux per unit energy, φ(E), over the group's energy range:

- **`fusion-14mev`**: a D–T line. All flux goes in the group containing 14.1 MeV.
- **`fission`**: a Watt spectrum, φ(E) ∝ exp(−E/a) sinh(√(bE)), with a = 0.988 MeV and b = 2.249 MeV⁻¹ (U-235
  thermal fission). Group integrals are taken from its closed-form integral (error functions).
- **`thermal`**: a Maxwellian, φ(E) ∝ E exp(−E/kT), with kT = 0.0253 eV. Group integrals are taken from its
  closed-form integral.
- **`one-over-e`**: slowing down, φ(E) ∝ 1/E between 0.5 eV and 1 MeV and zero outside. Group integrals are
  logarithms.

Each is labelled in the app and in `actinv help new` as a generic shape, not a facility's spectrum.

## Gates

- **G0.** This protocol's SHA-256 is in `protocols/protocol_hash.txt`.
- **G1.** `cargo fmt --check`, `cargo check --workspace --tests` and `cargo clippy --workspace --all-targets -D
  warnings` are clean, and the workspace tests pass. Unit tests cover:
  - every library entry parsing, with compositions summing to 100;
  - each spectrum summing to the requested total;
  - the 14.1 MeV group being found;
  - `actinv new --material ID --source SHAPE --flux F` writing a problem that `Spec::from_json` accepts.
- **G2.** The checker `controls/check_p162.py` reads the library and confirms, for every entry:
  - the schema;
  - symbols in the periodic table;
  - positive values summing to 100 within 1e-9;
  - a non-empty citation;
  - its basis and composition matching `results/p162/sources.json` to the stated rounding: each element within 1e-9
    of the recorded value, and the balance element equal to 100 minus the others.
- **G3.** The checker recomputes every spectrum independently in Python (closed forms, the 709 boundaries read from
  `crates/actinv-data/data/fispact_709_groups.json`) and compares each group with `actinv new` output to 1e-10
  relative, for groups above 1e-30 of the largest. It also checks the integrals against adaptive numerical
  quadrature to 1e-8 relative on the groups holding 99.9% of the flux, and the total to 1e-12 relative.
- **G4.** For the Pb–Li entry, the weight fractions equal the atom fractions 15.7/84.3 converted with the standard
  atomic weights of natural Li and Pb, to 1e-6.
- **G5.** `actinv new --material eurofer97 --source fusion-14mev --flux 1e12` and `--material ss316 --source fission
  --flux 1e13` each run with exit 0 on the standard data. Both results' material blocks equal the library
  composition.
- **G6.** CI green on the pushed evidence commit.

## Verdict

P162-PASS if G0–G6 hold. One repair round, registered as an amendment first.

## Not claimed

- A library composition is a starting point, not a substitute for the user's own material certificate. Nominal
  and specification compositions are not real heats. Specification maxima overstate trace elements by design.
  PNNL generic compositions leave out trace elements that can dominate activation, and each entry's note says which.
- The analytic spectra are textbook shapes. They are not any facility's spectrum, and they are not a replacement for
  a transport calculation.
