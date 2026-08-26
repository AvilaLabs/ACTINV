# DRAFT — ACTINV P4b — ultra-narrow resonance sampling   (not hashed; opens only if P4 closes FAIL on G2c)

**Scope:** the single control P4 could not clear — group values for targets whose evaluations contain synthetic
resonances far narrower than the Doppler width (TENDL Fr-226: Γ ~ 1e-7 eV against Γ_D = 0.08 eV; Rb-94). Nothing else.

**Diagnosis to confirm first (cheap, no builder change):** whether the residual is controlled by the number of points
across the 0 K peak or by the interpolation of its wings — measured on Fr-226 group 220 by varying peak points
(201 / 801 / 3201) at fixed backbone density, and backbone density at fixed peak points. Recorded either way.

**Method under test:** for a resonance with Γ < Γ_D / 100, the 0 K line is a delta function on the broadening scale.
Replace its sampled peak by its analytic area A = ∫σ dE over the line (closed form from the resonance parameters) and
broaden that area as a Gaussian of width Γ_D centred on E_r, superposed on the sampled remainder. This removes the
sampling problem instead of refining it.

**Gates:**
(a) On Fr-226 and Rb-94: capture and fission group values converge between grid densities 1, 2 and 4 to ≤ 1e-3 on every
    group with σ ≥ 1e-4 b.
(b) Areas preserved: for each treated resonance the analytic area equals the numerically integrated 0 K area to 1e-6.
(c) No regression: on the seeded 40-target sample, every previously converged row is unchanged to 1e-9, and the
    ≥ 95 % / ≤ 2e-2 criterion of P4 Amendment A is met with zero flagged targets.
(d) FNS unchanged: the 132-experiment results move by ≤ 1e-6 (neither Fr-226 nor Rb-94 is an FNS composition isotope,
    so any change would indicate an unintended side effect).

**Verdict:** P4b-PASS if (a)–(d) hold; CONDITIONAL after one repair round; else FAIL. Rebuilds invalidate the library
cache by fingerprint, so the full library is rebuilt once at the end — budget it in the plan, per standing rule 7.
