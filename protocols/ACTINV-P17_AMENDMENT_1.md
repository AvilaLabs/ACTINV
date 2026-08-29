# ACTINV P17 Amendment 1 — held-out notation and source anomaly

Recorded 2026-08-29 after the first authorized held-out read. The unseal source was commit
`b7039e2bff4346d9c39c283fcd2aabd768a8e628`; first access was recorded at
`2026-08-29T02:53:58Z`. This is the single append-only repair round permitted by the P17 protocol. If all remaining
gates pass, the closure verdict is therefore `P17-CONDITIONAL`, not `P17-PASS`.

The pre-unseal diagnostic grammar covered all labels in Tables 18–20, but the first read of held-out Tables 21–25 and
36 exposed publication-specific notation that did not occur in the diagnostic partition. This amendment freezes the
minimum additional mapping and records one internally inconsistent source row. It changes no source measurement,
metric, acceptance threshold, production code, production library or prior result.

## Reaction and material notation

- Add lanthanum (`La`, Z=57) to the element-symbol table.
- Preserve `g` as the total `(n,gamma)` response from MF=3/MT=102. Interpret `gm` as the MF=10/MT=102 branch to
  product isomer LFS=1, and interpret the newly observed `gg` as the MF=10/MT=102 branch to product ground state
  LFS=0. No total-to-branch inference is allowed when the requested MF=10 branch is absent.
- Interpret `rmleu`, `rmlpu` and `rmldu` as the publication's response-matrix labels for, respectively, the enriched
  uranium, plutonium and depleted uranium fission-foil mixtures in Table 22. The Table 23 labels `U235f`, `Pu239f`
  and `U238f` use the same respective foil compositions. A composite response is the atom-fraction-weighted sum of
  isotope MT=18 fission responses; contaminants printed in Table 22 remain in the sum. `Np237f` remains a
  single-target MT=18 response because no alternative Np-foil composition is supplied.

## Cover handling and H1/H2 observables

The exact cover tokens revealed by Tables 23 and 25 are `bare`, `Cd`, `Cdtk`, `Cdtk/B4C` and `Cdna`.

- `bare` uses the hash-pinned unshielded SPR-III (MAT 9014) or ACRR free-field (MAT 9010) spectrum.
- `Cd`, `Cdtk` and `Cdtk/B4C` require reaction-dependent cover transport or self-shielding. Geometry alone is not a
  supplied correction, and ACTINV's released activation kernel is not a cover-transport solver. These rows are
  preserved and ledgered `unsupported_self_shielding`; they are never approximated as dilute or bare.
- The paper does not define the distinct `Cdna` token in the supplied source pages. It remains an opaque source label
  and is also ledgered `unsupported_self_shielding`; no geometry is inferred from its spelling.
- A spectral index depends on both numerator and monitor responses. Table 23 normalizes to covered `Ni58p-Cd`, so
  every H1 production spectral index depends on an unsupported covered response. Table 25 normalizes to
  `Ni58p-bare`, so only H2 entries whose own token is `bare` are eligible for a production score. The monitor identity
  row is preserved but is not counted as predictive accuracy.

The paper states that both tables' EOI values were normalized to a 16-minute baseline exposure. Independent
measurement arithmetic therefore reconstructs the experimental spectral index from the printed EOI values using
`t = 960 s` and half-lives from the hash-pinned IRDFF-II decay archive. For an activation product with decay constant
`lambda`, define `s(lambda,t) = 1 - exp(-lambda*t)`. Relative to the Ni-58(n,p)Co-58 monitor,

```text
SI_exp(activity) = (A_i / A_monitor) * s(lambda_monitor,t) / s(lambda_i,t)
SI_exp(fission)  = (F_i / A_monitor) * s(lambda_monitor,t) / t
```

The reconstructed value must agree with `published SI / published SI C/E` within the rounding implied by the printed
table. This arithmetic check is required even for a row whose production calculation is unscored because of a cover.
It verifies the measurement definition; it does not supply the missing cover calculation.

## Table 36 final U-238 row

The literal final row of Table 36 prints measured SACS `1.080E+02 mb`, calculated SACS `3.899E+02 mb`, and C/E
`1.011`. The printed calculated value divided by the measurement is about 3.61, not 1.011; the PDF image confirms
that this is not a text-extraction error. All three literals are retained. The production comparison uses the printed
measurement, while the independent pointwise fold is reported separately. The published IRDFF calculation is marked
`source_internal_inconsistency` for this row and is not silently replaced by a value inferred from the printed C/E.

## Frozen effect

This amendment exhausts P17's one repair round. It adds no accuracy gate and authorizes no tuning. Every held-out row
must still appear exactly once, the existing per-family metrics remain unchanged, and any further post-unseal mapping
or metric correction forces `P17-FAIL` under the original closure rule.
