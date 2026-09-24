# ACTINV applicability map (P28)

Regime axes and element coverage qualified by P28 against the
`tendl-2025-patched` artifact (the shipped default at the time;
the default is now the full `tendl-2025` corpus — see
`DATA_LIMITATIONS.md` for the flip and the trade-off).
`qualified_with_ledger` means the element's source files carry
P25c residual defect classes (recorded, not cleared). `gap` names
missing coverage.

## projectile

```json
{
 "alpha": {
  "reason": "P25 coverage floor fail; no artifact",
  "status": "excluded"
 },
 "deuteron": {
  "reason": "P25 coverage floor fail; no artifact",
  "status": "excluded"
 },
 "neutron": {
  "status": "qualified"
 },
 "proton": {
  "reason": "P25 coverage floor fail; no artifact",
  "status": "excluded"
 }
}
```

## group_structure

```json
{
 "fispact-709": {
  "status": "qualified"
 },
 "other": {
  "reason": "no artifact; structure mismatch",
  "status": "excluded"
 }
}
```

## shielding

```json
{
 "finite_dilution": {
  "covered": [
   "Ag107",
   "Fe56",
   "Nb93",
   "Ta181",
   "U238",
   "W186"
  ],
  "status": "qualified_on_covered",
  "uncovered": "all other nuclides -> ledger-named passthrough or require_shielding_complete fail-closed"
 },
 "infinite_dilution": {
  "status": "qualified"
 }
}
```

## temperature_K

```json
{
 "measured_boundary": {
  "0.0": "contract_gap: requested temperature 0 K does not match library temperature 293.6 K",
  "1200.0": "contract_gap: requested temperature 1200 K does not match library temperature 293.6 K"
 },
 "note": "the patched artifact is single-temperature; any options.temperature_K != 293.6 fails closed",
 "qualified": [
  293.6
 ]
}
```

## responses

```json
{
 "qualified": [
  "total_activity_bq_per_g",
  "decay_heat_w_per_g",
  "photon_source_per_group",
  "inventory_per_nuclide",
  "total_atoms_per_g"
 ]
}
```

## elements

| element | targets | status | defect files | missing dosimetry |
|---|---|---|---|---|
| Ac | 17 | qualified | 0 | - |
| Ag | 18 | gap | 0 | [47109] |
| Al | 7 | qualified | 0 | - |
| Am | 20 | qualified | 0 | - |
| Ar | 12 | qualified | 0 | - |
| As | 18 | qualified | 0 | - |
| At | 11 | qualified | 0 | - |
| Au | 17 | gap | 0 | [79197] |
| B | 2 | qualified | 0 | - |
| Ba | 16 | qualified | 0 | - |
| Be | 4 | qualified | 0 | - |
| Bh | 15 | qualified | 0 | - |
| Bi | 20 | qualified | 0 | - |
| Bk | 19 | qualified | 0 | - |
| Br | 20 | qualified_with_ledger | 1 | - |
| C | 6 | qualified | 0 | - |
| Ca | 12 | qualified | 0 | - |
| Cd | 17 | qualified | 0 | - |
| Ce | 19 | qualified | 0 | - |
| Cf | 17 | qualified | 0 | - |
| Cl | 12 | qualified_with_ledger | 1 | - |
| Cm | 18 | qualified | 0 | - |
| Cn | 2 | qualified | 0 | - |
| Co | 12 | qualified | 0 | - |
| Cr | 12 | qualified | 0 | - |
| Cs | 12 | qualified | 0 | - |
| Cu | 18 | qualified | 0 | - |
| Db | 9 | qualified | 0 | - |
| Ds | 4 | qualified | 0 | - |
| Dy | 18 | qualified | 0 | - |
| Er | 15 | qualified | 0 | - |
| Es | 21 | qualified | 0 | - |
| Eu | 26 | qualified | 0 | - |
| F | 7 | qualified | 0 | - |
| Fe | 13 | qualified_with_ledger | 1 | - |
| Fl | 1 | qualified | 0 | - |
| Fm | 13 | qualified | 0 | - |
| Fr | 15 | qualified | 0 | - |
| Ga | 21 | qualified | 0 | - |
| Gd | 21 | qualified | 0 | - |
| Ge | 22 | qualified | 0 | - |
| Hf | 16 | qualified | 0 | - |
| Hg | 19 | qualified | 0 | - |
| Ho | 27 | qualified | 0 | - |
| Hs | 9 | qualified | 0 | - |
| I | 18 | qualified | 0 | - |
| In | 27 | gap | 0 | [49113] |
| Ir | 23 | qualified | 0 | - |
| K | 11 | qualified | 0 | - |
| Kr | 16 | qualified | 0 | - |
| La | 13 | qualified | 0 | - |
| Li | 2 | qualified | 0 | - |
| Lr | 9 | qualified | 0 | - |
| Lu | 17 | qualified | 0 | - |
| Mc | 1 | qualified | 0 | - |
| Md | 16 | qualified | 0 | - |
| Mg | 8 | qualified | 0 | - |
| Mn | 11 | qualified | 0 | - |
| Mo | 10 | qualified | 0 | - |
| Mt | 4 | qualified | 0 | - |
| N | 5 | qualified | 0 | - |
| Na | 6 | qualified | 0 | - |
| Nb | 15 | gap | 0 | [41093] |
| Nd | 23 | qualified | 0 | - |
| Ne | 6 | qualified | 0 | - |
| Nh | 6 | qualified | 0 | - |
| Ni | 17 | gap | 0 | [28058] |
| No | 6 | qualified | 0 | - |
| Np | 18 | qualified | 0 | - |
| O | 9 | qualified | 0 | - |
| Os | 33 | qualified | 0 | - |
| P | 8 | qualified | 0 | - |
| Pa | 17 | qualified | 0 | - |
| Pb | 22 | qualified | 0 | - |
| Pd | 16 | qualified | 0 | - |
| Pm | 22 | qualified | 0 | - |
| Po | 13 | qualified | 0 | - |
| Pr | 20 | qualified | 0 | - |
| Pt | 25 | qualified | 0 | - |
| Pu | 20 | qualified | 0 | - |
| Ra | 19 | qualified | 0 | - |
| Rb | 14 | qualified | 0 | - |
| Re | 26 | qualified | 0 | - |
| Rf | 9 | qualified | 0 | - |
| Rg | 4 | qualified | 0 | - |
| Rh | 19 | qualified | 0 | - |
| Rn | 13 | qualified | 0 | - |
| Ru | 12 | qualified | 0 | - |
| S | 13 | qualified | 0 | - |
| Sb | 19 | qualified | 0 | - |
| Sc | 12 | qualified_with_ledger | 2 | - |
| Se | 26 | qualified | 0 | - |
| Sg | 8 | qualified | 0 | - |
| Si | 8 | qualified | 0 | - |
| Sm | 24 | qualified | 0 | - |
| Sn | 27 | qualified | 0 | - |
| Sr | 17 | qualified | 0 | - |
| Ta | 12 | qualified | 0 | - |
| Tb | 19 | qualified | 0 | - |
| Tc | 8 | qualified | 0 | - |
| Te | 25 | qualified | 0 | - |
| Th | 16 | qualified | 0 | - |
| Ti | 9 | qualified | 0 | - |
| Tl | 20 | qualified | 0 | - |
| Tm | 12 | qualified | 0 | - |
| U | 17 | qualified | 0 | - |
| V | 7 | qualified | 0 | - |
| W | 23 | qualified | 0 | - |
| Xe | 24 | qualified | 0 | - |
| Y | 17 | qualified_with_ledger | 1 | - |
| Yb | 23 | qualified | 0 | - |
| Zn | 22 | qualified | 0 | - |
| Zr | 12 | qualified | 0 | - |
