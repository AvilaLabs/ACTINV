# P147 inputs: mixed packages of discrete items and encapsulation

Committed inputs for `controls/check_p147.py` (protocol `protocols/ACTINV-P147_PROTOCOL.md`). Every file here is
read by the checker only; none is a published example. Keys starting with an underscore (`_note`) are comments: the
checker removes them from the staged copies, because the Rust documents refuse unknown fields.

## Files

| file | schema | role |
|---|---|---|
| `a_lo_problem.json` | `actinv-spec-1` | activation run LO: cold steel-like activated metal, steps 11 to 13 are the 4 y, 33 y and 101 y cooling steps |
| `a_hi_problem.json` | spec | activation run HI: the same material at a higher irradiation level (hot pieces) |
| `a_nbco_problem.json` | spec | activation run NBCO: Nb- and Co-bearing steel |
| `a1_steel_pieces.json` | `actinv-waste-package-spec-1` | A1: pieces of several sizes (three under 280 cm3 from LO, one hot piece under 280 cm3 from HI, two larger LO pieces) and two pieces sectioned from one component |
| `a2_nbco_mix.json` | package spec | A2: Nb- and Co-bearing pieces (one of 316 cm3, one under 280 cm3) mixed with LO and HI pieces and a contaminated-material item (two waste-type groups) |
| `a3_encapsulated_low.json` | package spec | A3: two LO pieces and a declared Cs-137 sealed source in a 208000 cm3 drum, loading under 14 percent (credit capped at 0.2 m3 and 500 kg) |
| `a4_encapsulated_credited.json` | package spec | A4: three LO pieces and a hot declared Cs-137 sealed source in a 20000 cm3 container, loading about 20 percent (whole container credited), the source removed from the mixture |
| `a5_screen_37mbq.json` | package spec | A5: four A1 items with the 37 MBq total-activity screen |
| `a6_screen_highest.json` | package spec | A6: the A2 items with the highest-item screen |

The three activation problems are run by the checker (`actinv run`) into `target/p147/act`; the A1 to A6 specs name
the three result files `act_lo_result.json`, `act_hi_result.json` and `act_nbco_result.json`. Targets of all six cases
are steps 11, 12 and 13. The piece volumes are mass / 7.9 g/cm3. The levels and the step numbers were chosen by
hand estimate and have not been run: a development run (`stage A1 DIR`, then the printed commands, and the same for
A2 to A6) must show that the three runs finish, that the A1 and A2 pieces fall on both sides of 280 cm3, that A4 removes
the source at Class A and B, and that the reported package classes are not all the same.

## The synthetic battery

E1 also checks a deterministic battery of at least 60 packages, built in the checker and not stored. It is made in
three layers, in this order:

1. about 35 directed packages (`d01_` to `d37_`), each designed by hand to reach named branches: the restrictive-table
   tie, the controlling share, Factors of 2 and 10, BTP Table 2 and 3 limits, pooled items under 280 cm3, sectioning
   (merged, single piece, split by Table 3, split by Table 2), the two screens, a package above Class C, a removed
   hot source, an unknown nuclide, and the encapsulation statuses (credited, low loading, alternative approach
   required, Class C cap exceeded, the 14 percent and 331 ft3 edges);
2. 30 random packages from `random.Random(147001)`: one to six items of five waste types, volumes from 8 to 4e6 cm3,
   one to four nuclides per item at 10^-4.5 to 10^-0.2 of the largest class limit of the nuclide's row, optional
   sectioning, a screen, and an encapsulation block in a quarter of the packages;
3. a coverage-guided extension: further random packages from the same generator are kept only when they reach a
   branch not yet reached, until every named branch of the independent algorithm has been hit (at most 3000
   candidates).

The nuclide set is fixed (H-3, C-14, Fe-55, Mn-54, Co-58, Co-60, Ni-59, Ni-63, Sr-90, Nb-94, Tc-99, I-129, Cs-137, Pu-241,
Pu-239, Am-241) with embedded half-lives, Z and alpha flags. The gate record lists the hit count of every branch, the
battery SHA-256, and the package names. `check_p147.py selftest` fails if any branch is unreached, if the battery is
not byte-identical when generated twice, or if it has fewer than 60 packages. `stage NAME DIR` writes any package
by name for a development run.

## Nuclide properties

The committed A1 to A6 specs carry the token `"nuclide_properties": "from_decay_data"`; staging replaces it with the
half-life, Z and alpha flag of every radionuclide of the primary then fallback decay file of `a_lo_problem.json` (first
file wins; alpha emission is a decay mode with positive branching ratio and an RTYP containing the digit 4), through the
P146 helper `property_map` (cached under `target/p146/properties/`). The committed specs are therefore not valid input
for the Rust command until staged. The synthetic packages carry the embedded map.

## Evidence step order

All commands run from the repository root inside the memory-limited scope, with the activation data tree named by
`P147_DATA` (then `P146_DATA`, `P145_DATA`, `./actinv-data`). Each step appends `name<TAB>command<TAB>exit` to
`results/p147/steps.tsv` and its output to `results/p147/logs/NAME.log`.

1. `build_cli`: `cargo build --release --locked -p actinv-cli`
2. `e6_selftest`: `python3 controls/check_p147.py selftest` (no data, no binary)
3. the gates, one step each, named `e1` to `e5`: `python3 controls/check_p147.py e1` ... `e5`
4. P103 and P105 waste controls: `e6_p103_check` (`python controls/check_p103.py --no-write`), `e6_p105_children` (`python controls/test_p105_children.py`),
   `e6_p105_check` (`python controls/check_p105.py --no-write`), `e6_p105_verdict` (`python controls/check_p105_verdict.py`)
5. `e6_p145_selftest`: `python3 controls/check_p145.py selftest`
6. `e6_fmt`: `cargo fmt --all -- --check`
7. `e6_clippy_native`: `cargo clippy --workspace --all-targets --all-features -- -D warnings`; `e6_clippy_wasm`: `cargo clippy --locked -p actinv-gui --lib --no-default-features --target wasm32-unknown-unknown -- -D warnings`
8. `e6_workspace_tests`: `cargo test --locked --workspace` (the passed and failed counts are read from this log)
9. binding checks: `e6_binding_fmt`, `e6_binding_check`, `e6_binding_clippy` (the `python/Cargo.toml` commands of P145 U5)
10. module: `e6_module_build`, `e6_module_install`, `e6_module_tests`
11. `e6_handbook`: `mdbook build` then `python3 scripts/check_docs.py`
12. `e6_p13_data_distribution`: `python3 controls/g1_p13_data_distribution.py && python3 controls/check_p13.py && git checkout -- results/`
13. `python3 controls/check_p147.py e6-collect` writes `results/p147/e6.json` (schema `actinv-p147-e6-1`) from `steps.tsv` and the logs; every step in `steps.tsv`, the listed ones and the gate steps, must exit 0, and the committed waste controls and the P145 U2 records must be unchanged since the commit that registered the protocol
14. commit, push, record the CI runs of the pushed commit in `results/p147/ci_runs.json`, then `python3 controls/check_p147.py verdict results/p147/ci_runs.json`

The verdict is `P147-PASS` only if E1 to E5 pass, the E6 record passes, the CI runs of the pushed commit all succeeded with
`controls` among them, and every gate record names the same commit, binary, protocol and amendments.
