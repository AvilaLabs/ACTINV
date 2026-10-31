# ACTINV-P177 — ACTINV's geometry self-shielding against P173's continuous-energy tallies

This completes P176. P176 could not score ACTINV's geometry self-shielding option: every geometry run failed on the
ENDF/B-VIII.1 Cu-63 file. P177 scores that option now that ACTINV can read the file.

## Purpose

P173 compared ACTINV's default 709-group rates with OpenMC 0.15.3 continuous-energy tallies on the same ENDF/B-VIII.1
data, in four 14.1 MeV shell models (S steel, W tungsten, C CuCrZr, K concrete). It found 11 material collapse
differences, all capture:
- Mn-55, Co-59 and Mo-98 (n,γ), in all three steel regions;
- Cu-63 (n,γ), in both CuCrZr regions.

P176 then scored ACTINV's optional self-shielding (P176-PASS, ledger 312):
- **A1, the Bondarenko table with composition dilution:** safe, and it removes the steel rows. It leaves Cu-63 at
  `r_ss` 0.055 and 0.069, so it is not a candidate.
- **A2 and A3, the geometry option (a foil of the region's or the body's thickness):** failed on every run. ACTINV's
  strict reader refused Cu-63's R-Matrix Limited photon pair (PNT/SHF = −1).

ACTINV `4af8e68` added `self_shielding.geometry.profile`. With `"endfb8"`, it applies the same ENDF/B-VIII.1 tape
normalization that `build-library --profile endfb8` uses, before the resonance files are read. The question:

> With the geometry option and `"profile": "endfb8"`, do P173's 11 material capture rows come within tolerance of the
> continuous-energy tally, without harming the other rows?

## Disclosure of what has been seen

- **Everything P176 disclosed.** In addition: P176's results, which are A1's rows and the A2/A3 failure (ledgers
  311–312).
- **One run of a P177 problem, made while testing the profile fix on 2026-10-31.**
  - **The problem:** P176's A2 problem for CuCrZr 10–12 cm, on P173's tallied flux, with `"profile": "endfb8"` added.
    That is this protocol's A2 C:10-12 run.
  - **The binary:** a development build of the fix, `66c754b`, whose code equals `4af8e68` in `actinv-core`.
  - **What was read:** its exit status (0), its step summary (`1 steps, 48 of 3873 states`), the key names of the
    ledger's `shielding` block, and the list of shielded nuclides (Cr-50/52/53/54, Cu-63/65, Zr-90/91/92/94/96).
    No rate was read.
  - **What happened to its output:** deleted unread before this draft. The same run without the profile failed with
    the refusal message, which was read.
- **No other P177 scored quantity was computed.** The harness and checker were written and self-tested without running
  ACTINV.

## Same as P176

The following are P176's, unchanged and imported from `controls/p176_selfshield.py` and `controls/check_p176.py`:
- the models, regions and rows;
- the problem each arm solves;
- how the self-shielded rate is obtained (the depletion table's loss rates);
- the resonance-file rule and the 67 pinned evaluations;
- P173's pinned inputs;
- the not-scored reasons;
- the materiality rule;
- the decision rules (removes, safe, candidate);
- the sentence template.

Where this protocol is silent, P176's text applies with A2 and A3 for its arms.

## Arms (fixed now)

| Arm | `self_shielding` |
|---|---|
| **A0** | Absent. The control, P176's A0. It must reproduce P173's rates. |
| **A2** | P176's A2, a foil of the tallied region's thickness (S 2, 5, 10 cm; W 0.5, 1.5, 5 cm; C 2, 5 cm; K 10, 20 cm), with `"profile": "endfb8"` added to `geometry`. |
| **A3** | P176's A3, a foil of the whole model body's thickness (S 40, W 10, C 15, K 70 cm), with `"profile": "endfb8"`. |

There is no Bondarenko arm: P176 scored it. That gives 3 arms × 10 regions = 30 ACTINV runs and no table build.

## Pinned binary

`actinv`, the release build of `4af8e68`, SHA-256 `372dd99f271cdd2b2b6881d46499c5ec7cc11f74033dba5da42d3a69ce2f8fcd` (`ACTINV_BIN_SHA256` in `controls/p177_geometry.py`).
Its crates tree is `587be363437ce13c6ddd99d998b6be76ad19e413`.

ACTINV does not echo `profile` in the result ledger. The profile is bound instead in three ways:
- through each run's problem, which G2 recomputes from the harness to its recorded digest;
- through the harness constants hash;
- through each run entry's `geometry_profile`, which G1 checks.

## Gates

- **G0:** this protocol's SHA-256 is in `protocols/protocol_hash.txt`. The record's harness, group-structure and
  constants hashes equal the files' and the harness' own. The constants hash also covers `p176_selfshield.py`'s own
  SHA-256.
- **G1:**
  - every pin;
  - the bounded cgroup (P173's rule: 6 GiB, no swap, 128 tasks, at most 2 CPUs) for each of the 12 arm modes and the
    assembly;
  - `geometry_profile` is `endfb8` in the record and in every A2 and A3 run entry;
  - geometry shielding is present in every A2 and A3 run that succeeded, and absent from every A0 run;
  - A0 reproduces P173's total-row rate on all 78 rows to 1e-9 relative.
- **G2:** as P176's, for arms A2 and A3.
- **G3:** the decision rules are computed by `controls/check_p177.py` and written to `results/p177_verdict.json`.
- **G4:** CI green on the evidence commit.

## Decision rules and outcome

The rules are P176's, applied to A2 and A3 separately. Material means `|x| > 0.05` and `|x| > 3 se`. An arm is a
**candidate** if it meets both conditions:
- **It removes the differences:** each of the 11 material-collapse rows is scored and is not material in `r_ss`.
- **It is safe:** every used P173 row that was not material stays not material.

- **If A2 or A3 is a candidate,** item 2 of `docs/maintainers/WORKFLOW_ACCURACY_PLAN.md` becomes "make that geometry
  option the default for R2S and mesh runs". That is a later protocol with its own qualification.
- **If neither is a candidate,** item 2 proceeds. The record then shows how close each arm came on Cu-63, beside A1's
  0.055 and 0.069. That information informs a combined form (the Bondarenko table plus a Cu-63 treatment). It decides
  nothing here.

**Allowed public sentence:** P176's template, for a candidate arm only. Otherwise there is no sentence. No sentence may
call either code more or less accurate, and none may extend beyond the four models.

## Verdict

P177-PASS if G0–G3 hold (G4 external), whatever the outcome. A run that cannot be made is recorded with its error and
scored `arm_failed`, and that arm cannot be a candidate. This protocol has one repair round, registered as an amendment
before any rerun.

## Rehearsal

Before registration, the coordinator runs every arm mode once with `P177_REHEARSAL=1`. The outputs go under
`target/p177-rehearsal*`, and the flux is flat (1.0 per group). P176's lesson applies: every arm record's per-run
`ok` and `stderr_tail` are read, not only the mode's exit code.

The rehearsal ran on 2026-10-31 from `4af8e68` (plus these untracked drafts), with the pinned binary, inside the
2-CPU scope:
- **Arm modes:** all 12 exited 0, and so did the assembly.
- **Runs:** all 30 per-run records have `ok` true, and none has a recorded error.
- **Geometry runs:** every A2 and A3 run has `geometry_profile` `endfb8` and a ledger `geometry` block. The shielded
  nuclides number S 30, W 5, C 11 and K 22. The unshielded ones are those with no resolved range:
  - S: C, N, P-31, S-36;
  - K: H, C, O, K-40, Ca-46.
- **What was read:** only those fields, the exit codes, and the record's `geometry_profile` and `rehearsal` flags.

No rate was compared with OpenMC.

## Commands (coordinator)

From the worktree. Set `PYTHONDONTWRITEBYTECODE=1` and `ACTINV_BIN=<main checkout>/target/p177-bin/actinv`. Run each
command inside the AGENTS.md scope at 200 % CPU:

1. `python3 -I controls/check_p177.py --self-test`.
2. `python3 controls/p177_geometry.py arm --arm A0|A2|A3 --model S|W|C|K` (12 modes).
3. `python3 controls/p177_geometry.py assemble`.
4. `python3 controls/check_p177.py`.

## Not claimed

- Anything beyond P173's four models, or about other geometry shapes.
- That the foil is the right geometry for a shell inside a body. A2 and A3 bracket the two readings, as in P176.
- That OpenMC's rate is the true rate, or anything about measurement, speed or decay heat.
