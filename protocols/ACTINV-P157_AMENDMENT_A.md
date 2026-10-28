# P157 Amendment A — the worked example's photon response is a user-built file, not a catalog path

Registered after CI failed G8 on the evidence commit `78ebd9d`; the failure is preserved under
`results/failures/p157/g8_ci_attempt1/`. This is the phase's one repair round. Every other gate and criterion is
unchanged.

## Finding that requires it

The controls workflow's "Public examples are portable" step (`controls/check_public_examples.py`, which runs
`controls/check_example_hashes.py`) failed:

    declared hash mismatch: examples/impurities_ra_steel/base_spec.json /photon/response:
    actinv-data/v1.1.0/photon/nist-xcom-all.json is not in the v1.1.0 data catalog

`base_spec.json` named its photon response under `actinv-data/v1.1.0/`, the directory that `actinv data fetch`
installs from the catalog. No ACTINV data catalog contains a photon response. The user builds it with
`scripts/build_photon_response.py`. The local runs passed because a copy of the built file had been placed in that
directory on the development machine, so a user following the example would not have found the file at the
named path. The check found a real defect in the example, not in the check.

## A1. Repair

`examples/impurities_ra_steel/base_spec.json` names its photon response as `photon-response/nist-xcom-all.json`,
which is relative to the working directory like the example's other data paths and outside the catalog's
`actinv-data/vX.Y.Z/` namespace. The declared SHA-256 is unchanged. The example's README says where the file goes and
how to build it. No Rust source, checker, tolerance, protocol gate, other example or handbook command changes.

## A2. Evidence after the repair

The evidence run (G0 and G2–G7) is repeated in full with the repaired example, using the same checker and the
same reference binary. A candidate built from the repair commit must be byte-identical in source to `8702e67`
outside the two example files. The photon response is placed at `<data home>/photon-response/nist-xcom-all.json`,
and its SHA-256 must match the declared one. Before the rerun, `actinv-data/v1.1.0/photon/` is removed from the
data home, so the rerun cannot pass by using the old path. G8 is then judged on the pushed evidence commit.
