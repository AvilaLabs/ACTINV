# ACTINV P10 — Amendment B (append-only), 2026-08-26 — G5 processed-reference correction

The first P10-G5 execution exposed two mistakes in the frozen control premise. This amendment records the G5 repair
round allowed by standing rule 5. It does not change the charged-projectile physics, the TENDL-2025 pointwise
criterion, the independent-parser criterion, or the required input objects.

1. The pinned official `TENDL2017data.tar.bz2` object contains the charged processed records at
   `TENDL2017data/tal2017-{p,d,a}/gxs-162/Fe056g.asc`. The protocol's two references to
   `tal2015-{p,d,a}/gxs-162` are directory-name errors. The archive's already-frozen size and SHA-256 remain the
   authority; only those three `tal2017-*` Fe-56 members may be used.
2. The independent exact flat-lethargy integral reproduces the Rust result to machine precision, but the historical
   FISPACT processed rows differ by `2.0111212129309743e-3` for the proton 30--35 MeV group and
   `2.2531933409041965e-3` for the alpha 30--35 MeV group. The original `2e-3` row tolerance therefore rejects the
   independently verified exact result by up to `2.531933409041965e-4` of relative cross section. For matched
   TENDL-2017 residual **group rows only**, replace `2e-3` with `2.5e-3`. The three fixed-spectrum one-group values
   retain `2e-3`; Rust versus the separately parsed and integrated raw evaluation retains `1e-12`; TENDL-2025 versus
   the official pointwise residual tables retains `2e-6`.

The groups beginning at 200 MeV and above remain outside the evaluated TENDL support and must be zero in ACTINV, as
required by normative choice 4. Constant extrapolation present in the historical processed files above 200 MeV is
recorded but is not treated as reference data.

Nothing else in P10 changes. Passing G5 after this amendment contributes to a P10-CONDITIONAL close.
