# ACTINV-P195 Amendment A — the repair round: In and Nb foils restored, nuclide names resolved, barns typed

P195 failed G7 (ledger 374): CI's P16 typed-boundary control refused the evidence commit, because `unfold.rs`
multiplied by its own barn factor (1e-24) instead of going through `quantity.rs`, the one place that conversion may
live. Its scored run (ledger 373) had passed G0–G3, G5 and G6, but three of G3's declared measurements never entered
it. The protocol names them In115m, In116m and Nb92m. ACTINV's results name a first isomer by its level
(`In115m1`), and `actinv unfold` looked the given name up verbatim, so the harness's pre-check read them as not
produced and dropped them, as P195 provides for. G3 ran on 8 foils and 12 measurements, without the In and Nb
foils. A user writing `In115m` would get the same false refusal. P195's foil list declares fifteen measurements,
not the sixteen its text and ledger 373 state; twelve were kept.

## Disclosure

- Written after P195's run, all of whose results have been read: the G3 statistics overall and by band, the G4
  arms, and the drops above.
- Also read: the failed CI job's log, and the P16 control's output on the change below.
- Nothing else has been run since, other than unit tests and builds. A first start of this amendment's run, from an
  unregistered draft without change 2, was stopped during its G1 step; its G1 record was deleted unread.

## Change

1. **Names.** `actinv unfold` resolves each measured nuclide to the canonical name results use before looking it
   up (`In115m` and `In115m1` are the same state; `Hf178m2` stays itself; an element is refused), and reports the
   canonical name. A unit test covers the resolution.
2. **Barns.** A reaction rate's group terms and its cross-section gradient go through `quantity.rs`'s
   `CrossSectionBarns`, `ParticleFlux` and `RatePerBarnSecond`, so the barn factor appears only there. The product is
   σ (10⁻²⁴ φ) instead of (σ 10⁻²⁴) φ, which differs by rounding only. Nothing else in the command changes.
3. **G3 foils.** The three measurements are named `In115m1`, `In116m1` and `Nb92m1`.
4. **G3 gate.** In addition to P195's conditions, all fifteen declared measurements must be kept: a drop fails G3.

Everything else is P195's: problems, priors, seed 195, truths, noise, G4 arms, refusals, gates, tolerances and
allowed sentences. Because the noise draws follow the number of measurements, every truth after the first differs
from P195's.

## Procedure

`controls/p195a.py` runs P195's sealed harness and checker (`controls/p195.py`, `controls/check_p195.py`) with these
changes only:

- the three names above;
- the work directory `target/p195a-work`, and the records and verdict `results/p195a_g1.json`,
  `results/p195a_record.json` and `results/p195a_verdict.json`;
- G0 requiring also the SHA-256 of this amendment and of `controls/p195a.py` in `protocols/protocol_hash.txt`;
- the added G3 condition.

The candidate is the commit that adds changes 1 and 2, built clean; before its run, the P16 control
(`controls/g1_p16_quantities.py`, `controls/check_p16.py`) passes on it locally. A fresh run of everything follows,
inside the 6 GiB scope: `controls/p195a.py g1`, `controls/p195a.py run`, then `controls/p195a.py check`. G7 is CI on
the evidence commit.

**P195A-PASS** if G0, G1, G2, G3 (with the added condition), G5, G6 and G7 hold. This is P195's one repair round. On
P195A-PASS its sentences, filled from its record, replace P195's. No further amendment is allowed. If P195A fails,
no P195 or P195A sentence may be quoted.
