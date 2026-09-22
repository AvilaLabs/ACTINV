# Identity-resolution audit (2026-09-22)

Targeted sweep of every path that translates nuclide identity or parses
tabulated output — the bug class P42 exposed in the P26b stdout resolver
(silent misattribution producing wrong-but-plausible numbers).

## Surfaces swept

- `crates/actinv-data/src/builder.rs` — isomer resolution (`decay_resolve`
  tiers, `decay_match_elis`, catalog excitation match, LFS rank compression,
  `SYNTHETIC_LISO_BASE` aliasing guard)
- `crates/actinv-data/src/decay.rs` — `state_table` MF=1/MT=451 extraction
- `crates/actinv-data/src/composition.rs` — user-facing nuclide keys
  (`Nb93m1` etc.)
- `crates/actinv-core/src/chain.rs` — parent/daughter indexing, ledgered
  fallbacks (`isomer_fell_back_to_ground`, `products_unmapped`)
- `crates/actinv-core/src/run.rs`, `damage.rs`, `shielding.rs` — ZAID naming
- `controls/` — ALARA stdout parsers, ZAID/KZA scale math, dossier key
  encode/decode
- `python/src/objects.py` — spec key plumbing

## Findings

| Surface | Class | Status |
|---|---|---|
| `g2_p26b_leg.py::resolve_isotope_names` | demonstrated defect | fixed |
| `builder.rs::decay_resolve` LIS tier | latent defect | fixed |
| `builder.rs::decay_match_elis` exact-ELIS ties | latent defect | fixed |
| composition parser, state_table, chain fallbacks, ZAID scale math | — | clean |

### 1. `resolve_isotope_names` (evidence layer, live)

ALARA's stdout tables strip the element symbol (`-53` alone). The resolver
aligned anonymous rows to dump KZAs by mass + isomer, tie-broken by printed
t½ within 2%, then — failing that — picked the **first** candidate (lowest
Z) with an `ambiguous` flag that nothing downstream consumed. Isobars with
close half-lives could also silently match the first in-window candidate.

Reachability: `g2_p38_leg.py` imports this parser — the governed P40
comparator ran through it. P42 demonstrated concrete misassignments
(Ti-53/Ti-55 family read as zero/wrong element).

Fix: a multi-candidate row resolves only on a **unique** half-life
signature; anything else stays `unresolved` and is reported per case.

### 2. `decay_resolve` LIS tier (production, latent)

The evaluator's LIS label fallback used `.find` — first match wins. The
merged per-ZA decay tables carry duplicated LIS labels across different
isomers (each material numbers its own level scheme): **68 ZAs in
ENDF-B-VIII.0, 71 in JEFF-3.3** (e.g. Nb-90: LIS=1 labels both LISO=2 at
124.7 keV and LISO=7 at 382.0 keV).

Reachability: not live in the shipped 31-target artifact (its only Nb-90
row emits the ground state), but reachable in general builds. Fix: the LIS
match now requires uniqueness; a duplicated label falls through to the
caller's conservative path (synthesized ordinal / ground routing), which is
ledgered rather than silent.

### 3. `decay_match_elis` tight tier (production, latent)

Nearest-in-window wins unconditionally. ENDF-B-VIII.0 declares distinct
isomers at **identical ELIS** for four ZAs (Eu-136, Tb-154, Lu-162, Bi-194,
both states at ELIS=0); a product declaring a small excitation sits at
equal distance from both and energy cannot choose. Fix: an exact-distance
tie returns `None` — falls through to the LIS tier or the conservative
path.

## Regression coverage

- `builder::tests::duplicated_lis_label_cannot_resolve_isomer_identity`
- `builder::tests::identical_elis_isomers_cannot_resolve_by_energy`
- `controls/test_g2_p26b_resolver.py` — five resolver tests: unique-t½
  resolution, unmatched multi-candidate, tied half-lives, unambiguous
  rows, unique missing-half-life signature.

Executed: `cargo test -p actinv-data --lib builder::tests::` — 43/43 pass;
`python3 -m unittest test_g2_p26b_resolver` — 5/5 pass.

## Notes

- Pinned evidence is unaffected: P40's sealed per-nuclide record stands;
  the leg fix changes only future re-runs (ambiguous rows now surface as
  unresolved instead of silently resolving). P42's census used its own
  t½-matched parser and remains the authoritative arm measurement.
- The `ambiguous` flag was written but never read; removed in favour of
  honest `unresolved` reporting.
