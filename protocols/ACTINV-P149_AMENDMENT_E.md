# P149 Amendment E — the S6 fission case on a library that contains uranium

Registered after evidence attempt 4 and before S6 is rerun. No study is weakened. The fission case becomes the case
the protocol describes. S0–S5 are unaffected.

## E1. What happened

Attempt 4 ran S0 and S6 under Amendment D, then the checker, which reported P149-COMPLETE. S0 regenerated every
Table 3 control on the release binary. S6 recorded the pathway defect for W as Amendment D provides.

The natural-uranium row did not look like a fission case. Rate pruning kept 12 states and 27 non-zeros, and the
unpruned run with the default outputs took 1.4 s. A 14 MeV irradiation of uranium for 5 min produces fission
products far above the 10^-8 atoms/g threshold.

The attempt-4 S6 record, its log and the checker report are preserved under `results/failures/p149/attempt4/`.

## E2. Cause

S6 builds every case from the FNS specifications (`controls/cb3_fns.py`), so every case used the FNS activation
library, the TENDL-2017 709-group build. That build has 1,072 targets and none with Z = 90–94, so it holds no
uranium cross sections.

The natural-uranium case therefore had no neutron reactions:
- the fission yields were never used;
- the network held the uranium isotopes and their decay daughters only.

Neither the protocol nor Amendment A named a library for this case. The checker had no test that fission took place.

## E3. Changes

`controls/p149_s6_scaling.py`:
- The natural-uranium case uses the bundled TENDL-2025 neutron 709-group library
  (`actinv-data/v1.1.0/activation/tendl-2025-neutron-709g.npz`), the S5 library. It holds U-234, U-235, U-238 and
  Pu-239.
- Its SHA-256 is recorded in the spec and in the case record. Every case now records its library.
- Each natural-uranium row records the number of inventory nuclides with Z ≤ 72 and a positive population at the
  end of irradiation. The decay chains of uranium stay at Z ≥ 81, so only fission produces such nuclides.
- Fe, SS316 and W are unchanged.

`controls/check_p149.py`:
- S6 requires each case's library identity.
- S6 requires a positive fission-product count in every natural-uranium row.
- A selftest plant with no fission products must be caught. The selftest passes.
- The amended checker fails the attempt-4 S6 record.

## E4. Rerun and repeat

On the same binaries (`8845e93e…`), from fresh disk-backed work directories:
1. **Attempt 5:** S6 (all four cases, so that every case carries its library identity), then the checker.
2. **Repeat:** S6 again into the repeat output directory, compared as in Amendment C, C4.

The S0 record of attempt 4 and its repeat stand.

## E5. Development run

Natural uranium on the TENDL-2025 library, release binary, inside the required cgroup. This run is not evidence.
- With rate pruning: 1,172 of 3,873 states kept, 1,112 inventory nuclides with Z ≤ 72 at the end of irradiation,
  and a 0.87 s solve.
- Unpruned with the default outputs, pathways included: 4.2 s wall time and 1.0 GB peak resident set.
