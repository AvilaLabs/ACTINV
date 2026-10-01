#!/usr/bin/env python3
"""P100 G4 self-test: synthetic checks for the MF=3 threshold extension, the profile's per-group
normalization, and the IZAP=0 photofission sentinel -- all re-derived in controls/p94_g4_endf.py
independently from protocols/ACTINV-P100_PROTOCOL.md (no crates/actinv-data/src/builder.rs read).

Does not touch any raw ENDF-6 file or .npz library; every table here is built by hand. Safe to run
directly (no cgroup wrapper needed -- purely in-process, no large data loaded):

    python3 controls/p100_g4_selftest.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import p94_g4_endf as g4  # noqa: E402

MEV = 1.0e6
FAILURES = []


def check(name, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def tab1(x, y, interp=None):
    interp = interp or [(len(x), 2)]
    return {"c1": 0.0, "c2": 0.0, "l1": 0, "l2": 0, "interp": interp, "x": list(x), "y": list(y)}


def mf10_product(zap, table, lfs=0):
    return {"zap": zap, "qm_eV": 0.0, "qi_eV": 0.0, "lfs": lfs, "table": table}


# --------------------------------------------------------------------- case 1: applies
def case_1_applies():
    mf3 = tab1([14 * MEV, 20 * MEV], [1.0e-3, 1.0e-3])
    a = tab1([13.2 * MEV, 14 * MEV, 20 * MEV], [0.0, 0.4e-3, 0.4e-3])
    b = tab1([13.2 * MEV, 14 * MEV, 20 * MEV], [0.0, 0.5e-3, 0.5e-3])
    sections = [a, b]
    new_table, applied, line = g4.apply_mf3_threshold_extension(4, mf3, sections)

    check("case1: rule applies", applied is True)
    check("case1: ledger line produced", line is not None)
    if line is not None:
        check("case1: ledger line matches the extension prefix",
              bool(g4.EXTENSION_LEDGER_PREFIX.match(line)), line)

    expected_x = [13.2 * MEV, 14 * MEV, 14 * MEV, 20 * MEV]
    expected_y = [0.0, 0.9e-3, 1.0e-3, 1.0e-3]
    check("case1: added point (13.2 MeV, 0)", new_table["x"][0] == 13.2 * MEV and new_table["y"][0] == 0.0,
          f"got ({new_table['x'][0]!r}, {new_table['y'][0]!r})")
    check("case1: joining point (14 MeV, 0.9e-3)",
          new_table["x"][1] == 14 * MEV and abs(new_table["y"][1] - 0.9e-3) < 1e-15,
          f"got ({new_table['x'][1]!r}, {new_table['y'][1]!r})")
    check("case1: doubled with original (14 MeV, 1.0e-3)",
          new_table["x"][2] == 14 * MEV and new_table["y"][2] == 1.0e-3,
          f"got ({new_table['x'][2]!r}, {new_table['y'][2]!r})")
    check("case1: full point sequence matches", new_table["x"] == expected_x and new_table["y"] == expected_y,
          f"x={new_table['x']} y={new_table['y']}")
    check("case1: rest of original table kept", new_table["x"][3:] == [20 * MEV] and new_table["y"][3:] == [1.0e-3])
    check("case1: added region is lin-lin (INT=2)", new_table["interp"][0][1] == 2, str(new_table["interp"]))
    check("case1: added-region NBT covers the added points + joining + doubled original point",
          new_table["interp"][0][0] == len(expected_x) - 1,  # 2 added region points(13.2,14) + joining + double = 3
          str(new_table["interp"]))


# ----------------------------------------------------- case 2: non-lin-lin section -> unchanged
def case_2_non_linlin():
    mf3 = tab1([14 * MEV, 20 * MEV], [1.0e-3, 1.0e-3])
    # INT=1 (histogram) on the only segment below E3 -- triggers (positive value below E3), but
    # must not apply because the linearity condition fails.
    a = tab1([13.2 * MEV, 14 * MEV, 20 * MEV], [0.5e-3, 0.5e-3, 0.5e-3], interp=[(3, 1)])
    new_table, applied, line = g4.apply_mf3_threshold_extension(16, mf3, [a])

    check("case2: rule does not apply", applied is False)
    check("case2: table unchanged", new_table is mf3)
    check("case2: a ledger line names the reason", line is not None and "not applied" in line)
    check("case2: reason does not match the extension-applied prefix (no false positive count)",
          line is not None and not g4.EXTENSION_LEDGER_PREFIX.match(line), line)


# ------------------------------------------------ case 3: zero below E3 -> unchanged, no ledger
def case_3_zero_states():
    mf3 = tab1([14 * MEV, 20 * MEV], [1.0e-3, 1.0e-3])
    a = tab1([13.2 * MEV, 14 * MEV, 20 * MEV], [0.0, 0.0, 0.0])
    new_table, applied, line = g4.apply_mf3_threshold_extension(16, mf3, [a])

    check("case3: rule does not apply", applied is False)
    check("case3: table unchanged", new_table is mf3)
    check("case3: no ledger line", line is None)


# ------------------------------------------------------------- case 4: no MF=10 ZAP>=0 sections
def case_4_no_sections():
    mf3 = tab1([14 * MEV, 20 * MEV], [1.0e-3, 1.0e-3])
    new_table, applied, line = g4.apply_mf3_threshold_extension(16, mf3, [])
    check("case4: rule does not apply (no sections)", applied is False)
    check("case4: table unchanged (no sections)", new_table is mf3)
    check("case4: no ledger line (no sections)", line is None)


# --------------------------------------------------------- case 5: left_value_at doubled point
def case_5_left_value_doubled():
    # A discontinuity at 14 MeV: first occurrence (0.2e-3) is the left/lower limit, second
    # (0.3e-3) is the right/upper limit.
    t = tab1([13.0 * MEV, 14 * MEV, 14 * MEV, 15 * MEV], [0.1e-3, 0.2e-3, 0.3e-3, 0.3e-3])
    check("case5: left_value_at a doubled point returns the first (lower-side) value",
          g4.left_value_at(t, 14 * MEV) == 0.2e-3, str(g4.left_value_at(t, 14 * MEV)))
    check("case5: left_value_at off-grid interpolates",
          abs(g4.left_value_at(t, 13.5 * MEV) - 0.15e-3) < 1e-18)
    check("case5: left_value_at outside range is 0", g4.left_value_at(t, 10 * MEV) == 0.0)


# ------------------------------------------------- case 6: reconcile_states profile_normalize
def case_6_profile_normalize():
    boundaries = [10 * MEV, 15 * MEV, 20 * MEV]
    mf3 = tab1([10 * MEV, 20 * MEV], [1.0, 1.0])  # total = 1.0 b in every group
    # Two states summing to 3.0 b in the first group: far outside the 1e-3 standard envelope.
    s1 = tab1([10 * MEV, 20 * MEV], [2.0, 2.0])
    s2 = tab1([10 * MEV, 20 * MEV], [1.0, 1.0])
    products = [mf10_product(1001, s1, lfs=0), mf10_product(1001, s2, lfs=1)]
    rows = {(5, 1001, 0): [0.0, 0.0], (5, 1001, 1): [0.0, 0.0]}

    ledger_off = []
    g4.reconcile_states(5, mf3, products, dict(rows), ledger_off, boundaries, profile_normalize=False)
    check("case6: default (no profile) records an envelope violation",
          any("builder would fail closed" in line for line in ledger_off))
    check("case6: default (no profile) never writes state_sum_normalized",
          not any("state_sum_normalized" in line for line in ledger_off))

    rows_on = {(5, 1001, 0): [0.0, 0.0], (5, 1001, 1): [0.0, 0.0]}
    ledger_on = []
    g4.reconcile_states(5, mf3, products, rows_on, ledger_on, boundaries, profile_normalize=True)
    check("case6: --profile-normalize records state_sum_normalized instead",
          any("state_sum_normalized" in line for line in ledger_on))
    check("case6: --profile-normalize writes no envelope violation for the same group",
          not any("builder would fail closed" in line for line in ledger_on))
    total_scaled = rows_on[(5, 1001, 0)][0] + rows_on[(5, 1001, 1)][0]
    check("case6: scaled states now sum to the MF=3 total (T/S applied)",
          abs(total_scaled - 1.0) < 1e-12, str(total_scaled))


# ------------------------------------------------------------- case 7: classify_mt18 sentinel
def case_7_classify_mt18():
    sentinel_table = tab1([10 * MEV, 20 * MEV], [0.5, 0.5])
    ev = {
        "mf3": {}, "mf6": {},
        "mf8": {18: [{"zap": 0, "elfs_eV": 0.0, "lfs": 0, "lmf": 10}]},
        "mf9": {}, "mf10": {18: [mf10_product(0, sentinel_table, lfs=0)]},
    }
    info = g4.classify_mt18(ev)
    check("case7: recognizes the IZAP=0 total-photofission sentinel",
          info["shape"] == "izap0_total_photofission_sentinel", str(info))

    ev_absent = {"mf3": {}, "mf6": {}, "mf8": {}, "mf9": {}, "mf10": {}}
    check("case7: absent MT18 reports 'absent'", g4.classify_mt18(ev_absent)["shape"] == "absent")

    ev_bad = {
        "mf3": {}, "mf6": {},
        "mf8": {18: [{"zap": 92238, "elfs_eV": 0.0, "lfs": 0, "lmf": 10}]},
        "mf9": {}, "mf10": {18: [mf10_product(92238, sentinel_table, lfs=0)]},
    }
    raised = False
    try:
        g4.classify_mt18(ev_bad)
    except ValueError:
        raised = True
    check("case7: a declared photofission product yield fails loud", raised)


def main():
    case_1_applies()
    case_2_non_linlin()
    case_3_zero_states()
    case_4_no_sections()
    case_5_left_value_doubled()
    case_6_profile_normalize()
    case_7_classify_mt18()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        return 1
    print("all self-test checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
