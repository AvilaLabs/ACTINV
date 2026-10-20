#!/usr/bin/env python3
"""P149 S3 dense reference in multi-precision arithmetic (mpmath), for `p149_s3_histories.py`.

A step's matrix exponential over a year spans decay constants from 1e-13 to 1e2 per second, so `scipy.linalg.expm`
in double precision loses about 1e-8 relative accuracy to scaling and squaring. This helper evaluates the same
products of exponentials at 40 significant digits and again at 60, and reports the largest relative difference
between the two as the reference's own precision.

Input JSON: {"durations": [t_k], "blocks": [{"target": i, "local": j, "initial": [...], "matrices": [[[...]]...]}]},
one block per nuclide with that nuclide's causal ancestor subspace (matrix k for step k). Output JSON:
{"dps": 40, "check_dps": 60, "max_relative_check": r, "values": {"i": [x_i after every step, as 40-digit strings]}}.

Usage: p149_mp_dense.py IN.json OUT.json   (run with an interpreter that has mpmath)
"""
from __future__ import annotations

import json
import sys

import mpmath

DPS = 40
CHECK_DPS = 60


def evaluate(data: dict, dps: int) -> dict[str, list]:
    mpmath.mp.dps = dps
    cache = {}
    values = {}
    for block in data["blocks"]:
        state = mpmath.matrix([mpmath.mpf(v) for v in block["initial"]])
        series = []
        for matrix, duration in zip(block["matrices"], data["durations"]):
            key = (json.dumps(matrix), repr(duration))
            if key not in cache:
                cache[key] = mpmath.expm(mpmath.matrix(matrix) * mpmath.mpf(duration))
            state = cache[key] * state
            series.append(state[block["local"]])
        values[str(block["target"])] = series
    return values


def main() -> int:
    data = json.loads(open(sys.argv[1]).read())
    values = evaluate(data, DPS)
    check = evaluate(data, CHECK_DPS)
    worst = mpmath.mpf(0)
    for target, series in values.items():
        for value, reference in zip(series, check[target]):
            if reference != 0:
                worst = max(worst, abs(value - reference) / abs(reference))
            elif value != 0:
                worst = mpmath.inf
    mpmath.mp.dps = DPS
    out = {"mpmath": mpmath.__version__, "dps": DPS, "check_dps": CHECK_DPS, "max_relative_check": float(worst),
           "values": {t: [mpmath.nstr(v, DPS) for v in series] for t, series in values.items()}}
    open(sys.argv[2], "w").write(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
