# P146 Amendment A — definitions fixed before the evidence

Registered 2026-10-16, after implementation and before the candidate commit and any evidence run. Development runs
made before this amendment, on uncommitted code: D1 at 20 samples and confidence 0.8 (to time the case and align the
checker's field adapters), and the committed D2 σ² = 0.36 case through the checker's B1, B2, B3 and B5 logic. No
evidence input, seed, sample count or threshold was changed after those runs. They are disclosed here because the
committed D2 σ² = 0.36 case is deterministic, so its evidence run will repeat the development result. No gate is
relaxed.

## 1. Capped suprema in verification (item 5, B3)

A per-sample supremum can be set by the balance limit (the impurity reaches 100 wt% less the matrix and the other
impurities) rather than by a class constraint. Such a supremum is reported with status `capped` and enters the order
statistic at the cap (protocol item 4). At 1.01 times a capped supremum the balance is negative, so that point is
not a composition and has no class. For a capped sample the command classifies only the 0.99 point and lists the 1.01
point in `verification_skipped` with its reason. B3 requires the 0.99 points for every required rank, the 1.01 points
for every required rank that is not capped, and, for each skipped 1.01 point, a kept interval whose status is capped.

## 2. Zero variance in B5

A sampling block with every channel at zero variance is refused (protocol item 7, "no channel"). B5's invariance
check therefore uses the flux channel alone at relative standard deviation 1e-14. The budget must equal the nominal
supremum within 1e-12 relative.

## 3. Nuclide properties of the committed budgets

`actinv waste budget` requires explicit properties (half-life, Z, alpha emission) for every nuclide an element solve
can produce. The committed D1 and D2 budget documents carry the token `from_decay_data`. `controls/check_p146.py`
replaces it when it stages a case by the properties of every radionuclide in the primary and fallback decay files the
base spec names, using the P145 rule for alpha emission, and records the SHA-256 of the expanded map. The command
under test receives only the expanded document.

## 4. D1 irradiation amplitude and targets

D1 keeps the P145 C2 spectrum shape and step durations, but sets the irradiation flux multiplier to 250 instead of
1e4. At 1e4 the matrix alone is far above Class A, so no budget exists. The targets are the C2 steps 12 and 13
(about 33 and 101 years of cooling), because at steps 10 and 11 the matrix alone is not Class A at this amplitude.
At these targets the nominal Co interval is capped (Co-60 has decayed), while N and Nb bind on the Table 1 sum.

## 5. What "reported unchanged" means for the nominal (item 1, B2)

The `nominal` member must equal the result of a separate `actinv waste budget` run of the same staged document as
JSON values (canonical serialization with sorted keys). The standalone command writes the same value
pretty-printed. The comparison is exact, not within a tolerance.
