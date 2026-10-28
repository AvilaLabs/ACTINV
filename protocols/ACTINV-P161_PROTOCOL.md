# ACTINV-P161 — pathway closure ignores noise-level inventories

## Finding

`pathway_closure` is the largest relative gap, over every step and every nuclide in the inventory, between the sum of a
nuclide's production chains and its population. It is computed in `crates/actinv-core/src/run.rs` on the complete
decomposition. A nuclide with chains counts at any population above zero. A nuclide with no chain counts only above
1e-12 of the step's largest population; the code calls anything below that noise-level.

On the quick-start example, `examples/fns_fe_5min.json`, master `2efc851` reports `pathway_closure = 4.117`. The app
shows this on the Pathways page as "the listed chains differ from the inventory by up to 411.7%". The gap is Mn-58 at
step 20: 2.70e-28 atoms/g, about 1e-38 of the step's largest population. Mn-58 has a 3-second half-life, and its
population 25 minutes into cooling is the residue of the rational decay approximation, not a physical amount. The
chain sum and the population are both residues, and they need not agree. `examples/pulsed_demo.json` reports 0.531
(Cr-57 at 9.0e-26 atoms/g).

## Exploration before registration (disclosed)

The reference binary was run on every `examples/*.json` that it accepts with the data installed on this machine.
Closure was then recomputed from each result's listed chains (outputs in `target/p161-explore/`, not tracked). Above
1e-12 of each step's largest population, the largest gap on every case is 1.7e-6 or less. That is the effect of the
1e-6 listing threshold in a recomputation from outside, so the complete decomposition is at least this close.

A variant of the quick start with `cram_order` 48 still reports 0.889 on Mn-58 at 1.1e-29 atoms/g. The step's numerical
floor (alpha0 times the largest population) is 2.7e-37 under CRAM48, so a cut at the numerical floor would not remove
the residue. The 1e-12 cut does, under both orders. A floor-based definition was considered and rejected on this
measurement.

## Change

The noise cut that already applies to nuclides without chains also applies to nuclides with chains. A nuclide counts
toward `pathway_closure` only if its population exceeds 1e-12 of the step's largest population, and the gap formula is
unchanged. No other field, solve or listed chain changes.

The app's Pathways page states the selected nuclide's own coverage at the selected step: its listed chains as a
fraction of its population. It describes `pathway_closure` as the largest gap over the whole result. The report
page's caveat uses the same wording.

## Reference, candidate and corpus

- **Reference:** `actinv` built from master `2efc851` (sha256 `672ba4a3a71d6780c58289bf5f12855467b4319fa081a4e37bfb850bb556acf5`).
- **Candidate:** the P161 branch.
- **Corpus:**
  - every `examples/*.json` that the reference runs with exit 0 in the checker's working directory;
  - the quick start with `options.cram_order` set to 48.

## Gates

- **G0.** This protocol's SHA-256 is in `protocols/protocol_hash.txt`.
- **G1.** `cargo fmt --check`, `cargo check --workspace --tests` and `cargo clippy --workspace --all-targets -D warnings` are
  clean, and the workspace tests pass. Two unit tests are included:
  - a nuclide below 1e-12 of the step's largest population, whose chains disagree with its population, does not
    raise `pathway_closure`;
  - the existing missing-chain test (Ni-60) still flags its gap.
- **G2.** For every corpus case, the reference and candidate results are identical apart from `pathway_closure` and the
  timing keys `ms`, `elapsed_ms`, `wall_time_s` and `cells_per_s` (at any depth).
- **G3.** For every corpus case:
  - the candidate's `pathway_closure` is at most the reference's;
  - it is within 1e-5 of the checker's own recomputation of the new definition from the candidate's listed chains.
    The recomputation covers nuclides above 1e-12 of each step's largest population, and the allowance covers the
    1e-6 listing threshold.
- **G4.** For every corpus case, the candidate's `pathway_closure` is at most 1e-5, the quick start included.
- **G5.** Regression tests cover the app and report wording:
  - the Pathways page shows the selected nuclide's coverage and labels `pathway_closure` as whole-result;
  - the report caveat appears above 0.01 and uses the same words.
- **G6.** CI green on the pushed evidence commit.

## Verdict

P161-PASS if G0–G6 hold. One repair round, registered as an amendment first.

## Not claimed

The 1e-12 cut is the code's existing noise threshold, not a derived error bound. A real attribution gap in a nuclide
below 1e-12 of the largest population is no longer reported by `pathway_closure`. The per-nuclide chains are still
listed, and the app's per-nuclide coverage still shows the gap.
