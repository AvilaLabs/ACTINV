# ACTINV-P176 — ACTINV's optional self-shielding against P173's continuous-energy tallies

This follows P173 (item B1 of `docs/maintainers/WORKFLOW_ACCURACY_PLAN.md`) and decides the form of item 2.

## Purpose

P173 compared ACTINV's default 709-group rates (flat-lethargy groups, infinite dilution, self-shielding off) with OpenMC
0.15.3 continuous-energy tallies on the same ENDF/B-VIII.1 data in four 14.1 MeV shell models (S steel, W tungsten,
C CuCrZr, K concrete). The collapse-and-self-shielding difference `r_coll = ln(R_omg / R_openmc)` was material (above 0.05
and above 3 standard errors) in 11 rows, all capture: Mn-55, Co-59 and Mo-98 (n,gamma) in all three steel regions
(+0.064 to +0.111) and Cu-63 (n,gamma) in both CuCrZr regions (+0.133, +0.162). That is resonance self-shielding, which
infinite-dilution group constants miss. P173 stated that the benefit of ACTINV's own optional self-shielding "is not
measured here". This protocol measures it. The question:

> With ACTINV's existing self-shielding switched on the way a user would, from the documented problem options and each
> model's composition and geometry, do the 11 material P173 capture rows come within tolerance of the continuous-energy
> tally, and does it leave the other rows (threshold reactions, tungsten, concrete) unharmed?

The answer decides item 2. If an existing option removes the capture differences and harms nothing, the correction is "use
that option by default for R2S and mesh runs" (a later protocol), not a new tally. Otherwise item 2 proceeds as P173
warranted. This protocol certifies the measurement; it makes no accuracy claim against experiment.

## Disclosure of what has been seen

- No OpenMC calculation is repeated. The OpenMC rates and the tallied 709-group fluxes are P173's, pinned by hash below.
- No scored quantity of this protocol (a self-shielded loss rate, its ratio to the unshielded one, `r_ss`) has been
  computed or read before registration. The harness and checker were written and self-tested without running ACTINV; the rehearsal (below) then ran it with a
  flat flux, which yields no scored quantity.
- Seen before registration: P173's verdict and ledger entries 297, 299 and 300 (the 11 material-collapse rows and the
  W-182(n,p) processing finding); P173's record fields that say which reactions have a total (loss) row in the library
  (`total_row_R_actinv` null for Fe-56(n,p), W-183(n,p) and W-186(n,p), nine rows); the MF=2 headers of the ENDF/B-VIII.1
  evaluations of the nuclides involved (energy ranges only, no cross section); and the source and documentation of the
  self-shielding options (next section). Earlier validation records of the shielding code (`results/g1_d6e_resolved_shielding.json`,
  P19, P129, P139) check its mechanics, not its agreement with a continuous-energy tally on these models; none was
  consulted for a value.
- Every parameter below (arms, thicknesses, the rule for the resonance files, thresholds, decision rules) was fixed from
  the source and the guide and the draft of this protocol, not from any run.

## ACTINV's self-shielding options (read from the source, not assumed)

ACTINV has two exclusive options, both under `self_shielding` in the problem (`crates/actinv-core/src/spec.rs:267`
`SelfShieldingOptions`, `:352` `SelfShieldingGeometry`; guide `docs/guide/specification.md:870-1060`):

| Option | Source | What it does |
|---|---|---|
| **Bondarenko table** (`table`, `dilution`) | `crates/actinv-core/src/shielding.rs:346-440` (`plan_with_overrides`); builder `crates/actinv-data/src/builder.rs:3925-4078` (`shielding_nuclide_entry`), `:4083` (`build_shielding`); CLI `crates/actinv-cli/src/command.rs:777` (`actinv build-shielding EVAL_DIR OUT.json`) | A hash-pinned `actinv-shield-table-1` holds, per nuclide and group, factors on a sigma0 grid (1e10 … 0.1 b) and at 293.6, 600, 900, 1200 K (`crates/actinv-data/src/shielding.rs:1620-1623`). With `dilution: "composition"` each covered nuclide's sigma0 is `sum_j(n_j sigma_p,j)/n_i` over the other nuclides of the declared material (`shielding.rs:405-420`). The factor scales the group cross section of that nuclide in the groups the table covers, per channel (mt 2 elastic, 18/19 fission, 102 capture, every other mt the total factor, `shielding.rs:145-152`). |
| **Geometry** (`geometry`) | `crates/actinv-core/src/shielding.rs:519` (sigmoid), `:829-1000` (`PreparedGeometry`); guide `specification.md:968-1060` | FISPACT-II `SSFGEOMETRY`: a universal sigmoid curve per resolved resonance for a foil, wire, sphere or cylinder of one nuclide, from hash-pinned ENDF resonance files. `reactions` is "all" (default) or "capture". |

Both are applied inside the solver's rate assembly: `ShieldPlan` is built once per run from the declared composition
(`crates/actinv-core/src/run.rs:4129-4155`) and every library row's group cross section is multiplied by its group scale
(`crates/actinv-core/src/chain.rs:561-570`, the rate assembly the solver's matrix uses; `run.rs:2383` and `:5383` apply the same scale in the derivative and covariance paths).

**What the table covers.** The builder (`builder.rs:3925-3997`; `crates/actinv-data/src/shielding.rs:2290-2338`,
`collapse_to_groups`) builds probability-table factors for unresolved ranges and, since D6e, a Doppler-broadened pointwise
quadrature for resolved Breit-Wigner, Reich-Moore and R-matrix-limited ranges. The run certificate says so
(`run.rs:8529`). The guide said "resolved-region pointwise shielding is not applied" until master `a38d1fa` corrected
it to the code. The scored result is the check: the table arm either moves the resolved-range capture rows or it does
not.

**What the geometry model does not do.** Per resonance it counts only the shielded nuclide's own peak (the other
nuclides of the mixture are neglected), it is a foil/wire/sphere/cylinder of one body in an external flux, not a region
inside bulk material whose flux was already tallied with the surrounding material present, and it never shields the
unresolved range (`run.rs:8515-8521`, guide `specification.md:1049-1056`). Its use on these shells is a documented-option
test, not a recommended configuration (see Disclosures).

## Same data and the pinned binary

- Library, group structure, OpenMC data, models, materials, reactions and normalisation: as P173 (`protocols/ACTINV-P173_PROTOCOL.md`,
  SHA-256 `14b21e9d…`). The ACTINV library is `~/nuclear-data/endfb-viii.1-p172/endfb81_all_709g.npz` (SHA-256 `ecaca7c0…`).
- Evaluations for the table and the resonance files: the 67 ENDF/B-VIII.1 neutron files of the nuclides in the four
  models (the union of P173's `atom_densities_atom_b_cm` keys), from the NNDC archive `neutrons-version.VIII.1.tar.gz`
  (SHA-256 `decff900…64e4`, the archive P172 built the library from). The 67 file names and SHA-256 are constants of the
  harness (`ENDF_FILES`) and are hashed by G0.
- Decay data (required by every problem, not used by any rate): ENDF/B-VIII.0 decay with JEFF-3.3 fallback, as P172.
  Every target of the registered reactions has a decay record in the primary file, so the depletion table lists it.
- **Binary: the master release binary**, `36af79228fc56c1116bdfca375b6050e906bd6901475bcbfd2c1fa60b712619c`, built from
  crates tree `f7b5e60e…` (master `a38d1fa`), passed as `ACTINV_BIN` and pinned in the harness. Two earlier binaries could
  not build the table, and the rehearsals that showed it are why this one is pinned:
  - The P173 binary (`ebeeb25c…`, crates `fd259e18…`) refuses Na-23 (MF=2/MT=151, a Breit-Wigner total width 376.34
    below the component sum 376.353 under LRX=0). The audit's `normalize.rs` change normalises that width, but only
    `build-library` applied the normalisation.
  - The audit binary (`c6673d0a…`, crates `9b8dce96…`) therefore refused Na-23 in `build-shielding` too. Master
    `a38d1fa` adds `build-shielding --profile` (the same normalisation, `endfb8` here). It also raises the resolved-range
    quadrature's depth cap from 8 to 16, after the build stopped on a sub-eV level of Si-28's R-matrix-limited range.

  Since the P173 tree, `run.rs`, `library.rs`, `resonance.rs`, `endf.rs`, `builder.rs` and `normalize.rs` have changed
  in output-file handling, allocation bounds, index-key strictness and NaN repair. The Breit-Wigner normalisation also
  changed, and it can move other LRF=2 evaluations (Mo-98) slightly. The shielding builder and CLI changed as described
  above, and `r2s.rs` gained an exporter that no arm calls. The spec, chain, depletion and runtime shielding code is
  unchanged. The control arm A0 therefore tests this binary against P173's rates (G1, 1e-9 on every row) rather than
  reproducing them by construction.

## Models, regions and rows

The four P173 models, with P173's regions and reactions: S (3 regions), W (3), C (2), K (2). A row is a (model, region,
reaction) of P173, 78 in all, 76 of them used by P173 (scored, OpenMC standard error at or below 0.5%). A row is scored in an
arm when P173 used it and the arm's ACTINV run has a loss rate for it (see "How the self-shielded rate is obtained").
No row is held out: the protocol measures existing code, it fits nothing.

## Arms (fixed now)

Every arm solves the same problem, per region, except for the `self_shielding` section:

```
{"spec": "actinv-spec-1", "projectile": "neutron",
 "library": {"path": <P173 library>, "sha256": <pin>},
 "decay": {"primary": <ENDF/B-VIII.0 decay>, "fallback": <JEFF-3.3 decay>},
 "material": {"mass_g": 1.0, "basis": "atoms_per_g", "density_g_cm3": <model density>,
              "composition": {<nuclide>: n * 1e24 / density, ...}},      # n: OpenMC's atom density, atom/b/cm
 "spectrum": {"structure": "custom", "boundaries_eV": <fispact-709, ascending>,
              "flux_per_group": <P173's tallied group flux of the region, cm per source>, "descending": false},
 "schedule": [{"dt": "1 s", "flux": 1.0}],
 "summary": {"depletion": true, "nuclides": false, "elements": false}}
```

The material is OpenMC's own natural-isotope expansion, so the number densities are exactly P173's (this matters for the
dilution and the geometry).

| Arm | `self_shielding` |
|---|---|
| **A0** | absent. The control: it must reproduce P173's rates. |
| **A1** | `{"table": {"path": <table>, "sha256": <pin>}, "dilution": "composition"}`, with `sigma0_b` absent and `require_shielding_complete` false. The table is `actinv build-shielding <67 evaluations> table.json --projectile neutron --groups fispact-709 --profile endfb8 --cache target/p176-shield-cache` (the ENDF/B-VIII.1 tape normalization; without it the Na-23 Breit-Wigner widths are refused. The cache is keyed by each evaluation's bytes, the options and the shielding code, so the scored run reuses the rehearsal's checkpoints and writes the same table). |
| **A2** | `{"geometry": {"shape": "foil", "length_cm": <thickness of the tallied region>, "resonance_files": [...]}}`; `reactions`, `choose` and `density_g_cm3` omitted (all reactions; every listed nuclide; density from `material`). Thicknesses: S 2, 5, 10 cm; W 0.5, 1.5, 5 cm; C 2, 5 cm; K 10, 20 cm. |
| **A3** | as A2 with the thickness of the whole solid body of the model: S 40, W 10, C 15, K 70 cm. |

The resonance files of A2 and A3 are those of the model's nuclides whose evaluation has a resolved range as its first
energy range (ACTINV refuses an evaluation without one); nuclides without one stay unshielded and are named by ACTINV in
`shielding_uncovered`. The foil is the geometry the guide offers for a layer of material; A2 and A3 bracket the two
readings of "the target's thickness" for a shell inside a solid body. No other geometry option is tried. The Bondarenko
arm needs no geometry; a user would set `dilution: "composition"` and give the composition, which the problem does.

There are 4 arms x 10 regions = 40 ACTINV runs and one table build. The temperature is the default 293.6 K, the
library's and the table grid's first column.

## How the self-shielded rate is obtained

`actinv inspect xs --spectrum` (P173's G1 collapse) does not apply self-shielding: it collapses the library row with the
spectrum and nothing else (`crates/actinv-cli/src/inspect.rs:989-1060`). The shielded rate exists only inside a run.
The run's **depletion table** (`summary.depletion`, FISPACT-II `DEPLETION`) reports, for each initial-material nuclide, a
`reaction` channel `MT<n>` whose `rate_per_s` is "the sum of the nuclide's loss rows with that MT, collapsed under the
step's spectrum with self-shielding, `options.rate_scale` and named overrides applied, times the flux multiplier"
(guide `docs/guide/results.md:153-155`). Those are the rates the solver's matrix diagonal carries
(`crates/actinv-core/src/chain.rs:596-612`, `run.rs:4186-4196`). With the spectrum equal to the tallied group flux and
one 1 s step at multiplier 1, `rate_per_s` is `sum_g sigma_g s_g L_g * 1e-24` per target atom, in 1/s per source particle,
with `s_g` the arm's group scale.

Only total (ZAP -1) loss rows appear there. A reaction whose library has no total row has no channel (Fe-56(n,p),
W-183(n,p), W-186(n,p), nine rows). Those rows are reported `no_loss_row`, not scored, not dropped. All three reactions have thresholds in the MeV range,
above the resolved range of their nuclide (Fe-56 to 850 keV, the tungsten isotopes to 10 keV), so the effect there is
expected to be small, but this protocol does not measure it.

**Scored quantity.** For each scored arm and P173 row, with `L_a(arm)` the depletion `rate_per_s` of the row's target and MT:

- `q = L_a(arm) / L_a(A0)` (the same run problem without `self_shielding`, same region);
- `R_ss = R_actinv(P173) * q`, with P173's `R_actinv` (the sum over product states);
- `r_ss = ln(R_ss / R_openmc) = r(P173) + ln q`, with `se(r_ss) = se(r)` of P173 (the shielding scale is exact and
  deterministic; the flux error enters through the same tallied flux, so P173's conservative first-order error is
  carried over).

Using the ratio `q` rather than `L_a(arm)` alone keeps P173's definition of `R_actinv` (product-state sum) and removes any
difference between the total-row and product-row sums. G1 checks that the control's `L_a(A0)` equals P173's total-row rate
to 1e-9 relative, row by row.

## Frozen inputs

All pinned in `PINNED` of `controls/p176_selfshield.py` and checked by G1:

| input | SHA-256 |
|---|---|
| ACTINV library, its index, the fispact-709 group file | as P173 (`ecaca7c0…`, `ff59cccf…`, `ce4bf523…`) |
| NNDC archive `neutrons-version.VIII.1.tar.gz` | `decff90016bfb5c25c8d4c7fb8d81f94ff2f104853165f0fe6a21bf61c4164e4` |
| `endf-b-viii-0_decay.dat` | `6f04cf009086c179021f243a58dadc2d5bb078de5ba39c4fe46ccad77d228ddb` |
| `jeff-3-3_decay.dat` | `850b8b7f85f8d88b6ad826c4cd341aaaffabd525c8ecf3c588a0ad437bf5d123` |
| `results/p173_ce_rates.json` | `f4b6aea267f492ad4242b5551f6709aba10d7f831007892575a54cfe11095ad1` |
| `results/p173_verdict.json` | `36aeb818a9cc483f9c076a3199d6e8bd0ddab99a47ef0adb65432577f9a59f44` |
| `results/p173/openmc_S.json` | `1ff792834fc444c1d586df3a6bf6590b0587c4e6e40bd478fb04a10c00459cb5` |
| `results/p173/openmc_W.json` | `7f43329dd6e616ecd881eb7330bf37949351aeda21f50b4eac298b1e0b64eebb` |
| `results/p173/openmc_C.json` | `e2242072775f5158da32ceaf72435dfeb222f32204565bb0c5919038ba97b4d6` |
| `results/p173/openmc_K.json` | `d655d544041a0bbc3255279e067340a072e19e4baa996ea72812066e4d625681` |
| `protocols/ACTINV-P173_PROTOCOL.md`, `controls/p173_ce_rates.py`, `controls/check_p173.py` | `14b21e9d…`, `42df1b54…`, `a04f201f…` (P176 imports the last two) |
| `actinv` binary (`ACTINV_BIN`) | `36af79228fc56c1116bdfca375b6050e906bd6901475bcbfd2c1fa60b712619c` |
| Bondarenko table built by that binary from the 67 pinned evaluations | `SHIELD_TABLE_SHA256` in the harness, filled by the coordinator from the rehearsal's table before registration (the build has no random draws; G1 requires the scored table to equal it) |

Recorded by hash in the record: `controls/p176_selfshield.py` (which holds the arms, the rules and the evaluation pins as
constants; the record also carries the SHA-256 of those constants) and `crates/actinv-data/data/fispact_709_groups.json`.
The tracked tree must be clean.

## Gates

- **G0:** this protocol's SHA-256 is in `protocols/protocol_hash.txt`; the record's harness, group-structure and constants
  hashes equal the files' and the harness' own.
- **G1:** every pin above; the bounded cgroup of the table build, each of the 16 arm modes and the assembly; the table
  build succeeded and is the pinned table; shielding is active in every A1, A2 and A3 run that succeeded (ledger
  `shielding` block present, A1's `table_sha256` equal to the pinned table) and absent from every A0 run; the control A0
  reproduces P173's total-row rate on all 78 rows to 1e-9 relative, and has a loss channel exactly where P173 had a
  total row.
- **G2:** one run per (arm, region); each run's problem recomputes from the harness and the pinned P173 files to its
  recorded digest; every P173 row is scored in each of A1, A2, A3 or not scored with a registered reason (`p173_not_scored`,
  `arm_failed`, `no_loss_row`, `rate_zero`); each scored row's `q`, `R_ss` and `r_ss` recompute from the recorded loss
  rates and equal P173's fields.
- **G3:** the decision rules below are computed by the checker from the record and written to
  `results/p176_verdict.json` (with `--no-write`, compared with it).
- **G4:** CI green on the evidence commit.

## Decision rules (what the result permits)

Material means P173's rule: `|x| > 0.05` and `|x| > 3 se`. Per arm A1, A2, A3, separately:

- **Removes the capture differences.** Each of P173's 11 material-collapse rows is scored in the arm and not material in
  `r_ss`, that is `|r_ss| <= 0.05` or `|r_ss| <= 3 se(r)`. A row not scored in the arm means the arm does not remove.
- **Safe.** Every P173 used row that was not material in `r` stays not material in `r_ss`. Such rows with no loss
  channel (`no_loss_row`) are listed as unassessed and do not block; an `arm_failed` or `rate_zero` row among them does.
- **Candidate.** The arm removes and is safe.

**Outcome.**

- If at least one arm is a candidate, item 2 of the workflow plan becomes "make that option the default for R2S and mesh
  runs" (a later protocol, with its own qualification), not a new tally. The record lists the candidate arms.
- If no arm is a candidate, item 2 proceeds as P173 warranted.

The checker also reports, per arm, the 11 rows' `r` and `r_ss`, the largest `|r_ss|` over used rows by model, the rows
made material, the unassessed rows and the largest `|r_ss|` over the used capture rows of S and C.

**Allowed public sentence, if earned.** For a candidate arm only, with X the largest `exp|r_ss| - 1` over the used
capture (n,gamma) rows of the steel and CuCrZr models, in percent, rounded up to 0.1:

- A1: "With its optional Bondarenko self-shielding, ACTINV's 709-group capture rates in the tested steel and copper-alloy
  shells agree with OpenMC continuous-energy tallies on the same data within X%."
- A2 or A3: the same sentence with "optional geometry self-shielding (a foil of the tallied region's thickness)" or "(a foil of the
  whole model body's thickness)" for "optional Bondarenko self-shielding".
- If any listed capture row of those shells is not scored or not used, the checker adds that count in a second sentence
  and the first does not cover those rows.

Otherwise no sentence. No sentence may call a code more or less accurate than the other, and none may extend beyond the
four models.

## Verdict

P176-PASS if G0-G3 hold (G4 external), whatever the outcome. A run that cannot be made (an ACTINV error on an arm) is
recorded with its error, scored `arm_failed`, and the arm cannot be a candidate. One repair round, registered as an
amendment first.

## Environment

- ACTINV: the pinned binary. OpenMC is not run. Python 3 with the standard library only (no numpy: nothing here reads the
  library arrays).
- Each mode runs inside the AGENTS.md systemd scope (`MemoryMax=6G`, no swap, 128 tasks, 200% CPU) and refuses to start
  without it. The harness runs the binary as a child of that scope with bounded waits (`subprocess.run` timeouts: 1 h per
  run, 6 h for the table; a timed-out child is killed and reaped) and `PYTHONHASHSEED=0`, `RAYON_NUM_THREADS=2`.
  ACTINV's data cache goes to `target/p176-work/cache`.
- One mode at a time. Two bounded scopes may run side by side only if free memory allows (AGENTS.md), never two modes that write the same file.

## Disclosures

- **Calculation against calculation, same data.** OpenMC's tally is the reference only for the two approximations of
  P173; it shares the evaluation's own errors. Nothing here is compared with experiment.
- **Four models only.** A result applies to these four shells and 14.1 MeV source, not to other spectra or geometries.
- **Not all rows are assessable.** Fe-56(n,p), W-183(n,p) and W-186(n,p) have no loss channel (nine rows); the effect
  on them is not measured. W-182(n,p) (the processing difference of ledger 300) is unaffected by shielding and stays out
  of scope; its three rows enter only as P173 rows material in `r`.
- **Where harm can come from, stated before any run.** (1) Bondarenko: every reaction other than elastic, fission and
  capture takes the *total* factor (`shielding.rs:145-152`) in the groups the table covers, so (n,p), (n,2n) and (n,a)
  rows are scaled wherever a covered group (resolved or unresolved range up to about 0.8 MeV for Fe, Cr and Ni, 1 MeV for
  Mn) has a nonzero cross section for them. (2) Geometry: `reactions: "all"` does the same with the geometry factor,
  and the model neglects the other nuclides of the mixture and, as a foil model, the flux depression the transport
  already contains. Over-shielding of tungsten and steel is possible and is what A2 and A3 test; the result may be that
  the geometry arms fail. The safe test measures this.
- **The sigma0 of the Bondarenko arm** comes from the declared composition and is the same in every group of a nuclide;
  it is not tuned. No dilution override is used.
- **Temperature**: the table is at the library's 293.6 K (first grid column); P173's OpenMC data are at 294 K.
- **`r_ss` carries P173's processing term.** `r_ss = r_proc + r_coll_after`; the 11 rows have |r_proc| <= 0.01 in P173, so
  `r_ss` is a fair measure of what remains of the collapse difference there. The checker states it per row.
- **The guide once said resolved-region shielding "is not applied".** That sentence conflicted with the code and was
  corrected with the `--profile` fix (`a38d1fa`). The guide now describes the resolved-range Bondarenko fold, and the
  protocol does not edit it.

## Not claimed

- Anything about reactions, materials or spectra not in P173's four models.
- That OpenMC's rate is the true rate.
- Anything about speed, decay heat or accuracy against measurement.
- That the self-shielded rate is right for a component: only whether it agrees with the continuous-energy tally here.
- That a default-on option is safe in general: a candidate arm only earns the later protocol that would qualify it.

## Rehearsal

Before registration the coordinator may run the whole pipeline once with `P176_REHEARSAL=1`: outputs under
`target/p176-rehearsal*`, never `results/`, and the tallied flux replaced by a flat 1.0 per group, so no OpenMC result
and no scored quantity enters. It exists to find harness defects (an ACTINV error on an arm, a resonance file ACTINV
refuses, the shape of the depletion table, the table build time) and to produce the table whose SHA-256 is pinned. Only
exit codes, the table record and the harness' own messages are read.

The rehearsal ran on 2026-10-31 from `a38d1fa`, with the pinned binary, inside the bounded scope (2 CPUs):
- **Table:** exit 0 in 12,268 s. It covers 54 shielding targets from the 67 evaluations. 13 evaluations have no
  resonance ranges (C-12, C-13, Ca-46, H-1, H-2, K-40, N-14, N-15, O-16, O-17, O-18, P-31, S-36). SHA-256 `b8ccd5a4…`,
  pinned above. The build wrote per-evaluation checkpoints to `target/p176-shield-cache`.
- **Arms A0–A3 for models C and S:** all exit 0. A1's ledger names the pinned table, and A0 reports no shielding.
- **Things read beyond exit codes:**
  - the per-nuclide summary of the applied factors. It showed that a fission factor of 0 on non-fissile nuclides set
    every `min_factor` to 0, so the summary now leaves those zeros out;
  - which rates were absent. Only Fe-56(n,p) in model S was absent, the registered no-loss-row case.
- **Assembly:** refused, as it should be. It needs all four models, and the rehearsal ran two.

No rate or ratio was compared with OpenMC.

## Commands (coordinator)

From the worktree, with `PYTHONDONTWRITEBYTECODE=1` and `ACTINV_BIN=<main checkout>/target/p176-bin/actinv`, each inside
`systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0 -p TasksMax=128 -p CPUQuota=200% -- env RAYON_NUM_THREADS=2`:

0. Before registration: `python3 -I controls/check_p176.py --self-test`; the rehearsal (`P176_REHEARSAL=1`): `table`,
   then `arm --arm A0|A1|A2|A3 --model C` (and `--model S`, whose evaluations are the most varied); copy the table's SHA-256 from
   `target/p176-rehearsal/table.json` into `SHIELD_TABLE_SHA256`; register the protocol, harness and checker hashes in
   `protocols/protocol_hash.txt`.
1. `python3 controls/p176_selfshield.py table`.
2. `python3 controls/p176_selfshield.py arm --arm A0 --model S`, then every other (arm, model): A0-A3 for S, W, C, K (16 modes).
3. `python3 controls/p176_selfshield.py assemble`, then `python3 controls/check_p176.py`.
