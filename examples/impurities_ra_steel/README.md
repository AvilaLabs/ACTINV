# Impurity screening of a reduced-activation steel

`actinv impurities impurities.json out.json` screens a Fe-9Cr-1.1W reduced-activation ferritic-martensitic steel
for eight impurities in the fusion first-wall spectrum of `../optimize_ra_steel`, after two full-power years at a
total neutron flux of 5e14 n/cm2/s, at 1 d, 1 y, 10 y and 100 y of cooling (result steps 2 to 5).

**All values are illustrative.** The matrix, the impurity nominal and range values, the irradiation history, the
flux level, the two limits (`heat.total` 1.4e-2 W/g, `activity:Nb94` 100 Bq/g), the component mass and density are
chosen to show the command; they are not a qualified material specification, a design limit or a regulatory
position. The Part 61 class is a nominal U.S. classification, not disposal acceptance.

`base_spec.json` carries the ACTINV data references of the other examples (library and decay files under
`actinv-data/v1.1.0/`, which this repository does not ship) and a `photon` section naming a photon response file,
`actinv-data/v1.1.0/photon/nist-xcom-all.json`, built with `scripts/build_photon_response.py --elements all`; the
contact-dose proxy is `null` without it. The handbook chapter `docs/guide/impurities.md` walks through the result.
