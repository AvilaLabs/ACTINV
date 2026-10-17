# P146 inputs: impurity budgets under nuclear-data uncertainty

Committed inputs for `controls/check_p146.py` (protocol `protocols/ACTINV-P146_PROTOCOL.md`). Every file here is
read by the checker only; none is a published example. Keys starting with an underscore (`_note`) are comments: the
checker removes them from the staged copies, because the Rust documents refuse unknown fields.

## Files

| file | schema | role |
|---|---|---|
| `d1_steel.json` | `actinv-waste-budget-uncertainty-spec-1` | D1 spec: 200 samples, seed 146001, confidence 0.95, MF=33 + decay constants + flux 5 % |
| `d1_budget.json` | `actinv-waste-budget-1` | D1 budget: Fe balance, Cr 18 / Ni 8 / Mn 1.5 wt% matrix, Co 0.05 / Nb 0.01 / N 0.05 wt% impurities, class A, activated metal, targets 12 and 13 |
| `d1_problem.json` | `actinv-spec-1` | D1 base spec: the P145 C2 spectrum and 13-step schedule, irradiation flux multiplier 250 |
| `d2_v16.json`, `d2_v36.json`, `d2_v64.json` | spec | D2 specs: 1024 samples, MF=33 only, seeds 146011 / 146012 / 146013 |
| `d2_budget.json` | budget | D2 budget: Al balance, Co 0.05 wt% impurity, class A, target step 2 |
| `d2_problem.json` | spec | D2 base spec: thermal Maxwellian (P145 C3 spectrum), 30 d irradiation then 1 d cooling |
| `d2_cases.json` | `actinv-p146-d2-cases-1` | id, spec, sigma2, sidecar name and pinned SHA-256, seed, band, closed form |

## D1: stainless-steel-like activated metal

The material is the P145 C2 steel (Fe, Cr, Ni, Mn with Co, Nb and N impurities) with the P145 C2 spectrum, schedule
and TENDL covariance (same path and SHA-256). The irradiation step of `d1_problem.json` is scaled from the C2
multiplier of 10000 to 250. At the C2 multiplier the matrix alone is above class A at steps 10 to 13 (estimated
Table 2 Column 1 sums of 21139, 7461, 28 and 13), so no class A budget exists; at 250 the matrix is estimated to be
class A at steps 12 and 13 and Co, Nb and N to have finite budgets. Targets 12 and 13 are the 33 y and 101 y cooling
steps. The levels were chosen by hand estimate and have not been run: a development run
(`stage D1 DIR`, then `actinv waste budget budget.json nominal.json` in `DIR`) must show a feasible nominal class A
budget (`feasible` true, finite supremum) for Co, Nb, N and the joint scale factor before the candidate commit.

## D2: closed-form calibration

Aluminium is the balance: Al-28 (2.24 min) is gone after the one-day cooling (about 640 half-lives), so the balance
has no response in the governing Table 2 Column 1 sum, and the impurity interval is set by Co-60 alone with x0 about
0.27 wt% against a balance cap of 100 wt%. With the MF=33 rank-one sidecar on Co-59(n,gamma) (target index 528) the
Co-60 activity per atom of Co is multiplied by F, so the per-sample supremum is x* = x0 / F with
F = exp(sigma z - sigma2/2). The keep probability at level x is Phi((ln(x0/x) + sigma2/2)/sigma), and the k-th
smallest of n supremum samples has coverage distributed as Beta(n - k + 1, k). Gate B4 asks that this probability
at the reported budget lie in the central 99.9 % of that Beta distribution for sigma2 = 0.16, 0.36, 0.64.

## Generation rule for the sidecars

The three `d2_vNN.cov.npz` files are not tracked (the data-distribution control refuses `.npz`). They are written
at check time by `controls/p145_c3_make.py` (`write_covariance(path, sigma2)`, a deterministic stored zip) into the
staging folder, and the checker stops unless the SHA-256 of the written bytes equals the one pinned in `d2_vNN.json`
and `d2_cases.json`. The pins are the P145 C3 pins (Amendment C): 845f9dc1... (0.16), 26f0db8b... (0.36),
9eeb017c... (0.64). `.gitignore` lists `controls/p146_inputs/*.cov.npz`.

## Nuclide properties

A budget needs the half-life, Z and alpha flag of every nuclide an element solve can produce (reach pruning keeps
the whole reachable set, thousands of nuclides). The committed budgets therefore carry the token
`"nuclide_properties": "from_decay_data"`; staging replaces it with the map built from the primary then fallback
decay file of the base spec (first file wins; radioactive states only; alpha flag from a decay mode with positive
branching ratio and an RTYP containing the digit 4). The map is cached under `target/p146/properties/`, and its
entry count and SHA-256 are recorded in each gate record. The committed budgets are therefore not valid input for the
Rust command until staged.

## Evidence step order

All commands run from the repository root inside the memory-limited scope. Each step appends
`name<TAB>command<TAB>exit` to `results/p146/steps.tsv` and its output to `results/p146/logs/NAME.log`.

1. `build_cli`: `cargo build --release --locked -p actinv-cli`
2. `python3 controls/check_p146.py selftest` (no data, no binary)
3. D1: `b1 D1`, `b2 D1`, `b3 D1` (steps `b1_d1`, `b2_d1`, `b3_d1`); b2 and b3 reuse the b1 run when the binary and inputs are unchanged
4. D2: `b1`, `b2`, `b3` for `D2_V16`, `D2_V36`, `D2_V64` (steps `b1_d2_v16` ... `b3_d2_v64`)
5. `b4` (step `b4`)
6. `b5` (step `b5`)
7. `b6_study_regression`: `python3 controls/check_p136.py --reference target/p146/actinv-reference-0ea4a18 --candidate target/release/actinv --workdir target/p146/b6-p136-work --out results/p146/b6_study_regression.json` (G2 to G4)
8. P105 byte identity: `b6_p105_children` = `python controls/test_p105_children.py`, `b6_p105_check` = `python controls/check_p105.py --no-write`, `b6_p105_verdict` = `python controls/check_p105_verdict.py`
9. `b6_fmt`: `cargo fmt --all -- --check`
10. `b6_clippy_native`: `cargo clippy --workspace --all-targets --all-features -- -D warnings`; `b6_clippy_wasm`: `cargo clippy --locked -p actinv-gui --lib --no-default-features --target wasm32-unknown-unknown -- -D warnings`
11. `b6_workspace_tests`: `cargo test --locked --workspace`
12. binding checks: `b6_binding_fmt`, `b6_binding_check`, `b6_binding_clippy` (the `python/Cargo.toml` commands of P145 U5)
13. module: `b6_module_build`, `b6_module_install`, `b6_module_tests`
14. `b6_handbook`: `mdbook build` then `python3 scripts/check_docs.py`
15. `b6_p13_data_distribution`: `python3 controls/g1_p13_data_distribution.py && python3 controls/check_p13.py && git checkout -- results/`
16. `python3 controls/check_p146.py b6-collect` writes `results/p146/b6.json` (schema `actinv-p146-b6-1`) from `steps.tsv` and the logs; the gate steps listed in the `steps.tsv` must all exit 0
17. commit, push, record the CI runs of the pushed commit in `results/p146/ci_runs.json`, then `python3 controls/check_p146.py verdict results/p146/ci_runs.json`

The verdict is `P146-PASS` only if B1 to B5 pass for every case, the B6 record passes, the CI runs of the pushed
commit all succeeded with `controls` among them, and every gate record names the same commit, binary, protocol and
amendments.
