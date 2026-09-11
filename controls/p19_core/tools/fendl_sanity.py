#!/usr/bin/env python3
"""P19 G3 FENDL sanity: ACTINV group constants on the FENDL-3.2c 211-group
structure vs the FENDL GENDF itself.

Positional argv:
  shield-table-211   Artifact built with --groups fendl_211_groups.json for
                     {Fe56, Ag107, W186}.
  fendl-g-files      One or more FENDL-3.2c `group/*.g` GENDF files.
  output             Workspace path for `fendl_sanity.json`.

Both sides are lethargy-group Bondarenko cross sections at the same sigma0
grid. The residual differences are the weighting convention (FENDL processed
with iwt=11 VITAMIN-E weighting; ACTINV's collapse is 1/E lethargy, iwt=3),
the evaluation vintage (FENDL-3.2c vs TENDL-2025), and NJOY's resolved-region
suppression in partially-covered groups. This leg is a sanity bound on shape
and magnitude, not a qualification tolerance.

Pure arithmetic; no interpretation.
"""
import json
import re
import sys
from pathlib import Path

SIG0 = [1.0e10, 1.0e5, 1.0e4, 1.0e3, 1.0e2, 1.0e1, 3.0, 1.0, 0.3, 0.1]
CHANNEL_MT = {"total": 1, "elastic": 2, "fission": 18, "capture": 102}
FENDL_NAMES = {"26056": "Fe56", "47107": "Ag107", "74186": "W186"}

ENDF_FLOAT = re.compile(r"^([+-]?\d+\.?\d*)([+-]\d+)$")


def endf_field(line, a, b):
    text = line[a:b].strip()
    if not text:
        return 0.0
    if "e" not in text.lower():
        text = ENDF_FLOAT.sub(r"\1e\2", text)
    return float(text)


def parse_gendf(path):
    """GENDF MF=3: per (mt, T, group) LIST packs NG2 sub-blocks of NZ x NL
    values — for nsigz>1 NL=2 (ordinary and sigma0-weighted components).
    Returns xs as the sigma0-weighted (shielded) component."""
    lines = Path(path).read_text(errors="replace").splitlines()

    def fields(line):
        return [endf_field(line, k * 11, (k + 1) * 11) for k in range(6)]

    sections = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        try:
            mf, mt = int(line[70:72]), int(line[72:75])
        except ValueError:
            i += 1
            continue
        if mf != 3 or mt not in CHANNEL_MT.values():
            i += 1
            continue
        head = fields(line)
        nz = int(head[3])
        i += 1
        records = {}
        while i < len(lines):
            l2 = lines[i]
            try:
                mf2, mt2 = int(l2[70:72]), int(l2[72:75])
            except ValueError:
                i += 1
                continue
            if mf2 != 3 or mt2 != mt:
                break
            cont = fields(l2)
            temp, ng2, nw, group = cont[0], int(cont[2]), int(cont[4]), int(cont[5])
            i += 1
            if nw <= 0 or group <= 0:
                continue
            values = []
            while len(values) < nw and i < len(lines):
                l3 = lines[i]
                try:
                    if int(l3[70:72]) != 3 or int(l3[72:75]) != mt:
                        break
                except ValueError:
                    break
                values.extend(fields(l3))
                i += 1
            values = values[:nw]
            if nz <= 0 or ng2 < 1 or nw < nz * ng2:
                continue
            nl = nw // (nz * ng2)
            if nl < 1:
                continue
            # il=1 is the Bondarenko-weighted component (the shielded
            # constant); any il>1 is the transport-corrected component.
            xs = [values[nz * nl + iz * nl] for iz in range(nz)]
            records.setdefault(temp, {})[group] = {"xs_b": xs}
        # One MF=3 section per temperature; merge.
        if records:
            sections.setdefault(mt, {}).update(records)
    return sections


def main() -> int:
    shield = json.loads(Path(sys.argv[1]).read_text())
    gfiles = sys.argv[2:-1]
    output = Path(sys.argv[-1])

    result = {"schema": "avila.actinv/p19-fendl-sanity/v1", "materials": {}}
    for gpath in gfiles:
        name = Path(gpath).name
        # filenames like 26Fe056.g / 74W_186.g: the ZAID digits are ZA
        zaid_digits = "".join(ch for ch in name.split(".")[0] if ch.isdigit())
        key = FENDL_NAMES.get(zaid_digits)
        if key is None:
            continue
        block = shield["nuclides"].get(key)
        if block is None:
            result["materials"][key] = {"status": "not-in-table"}
            continue
        my_groups = {int(g["group"]): g for g in block["groups"]}
        sections = parse_gendf(gpath)
        cells = 0
        worst = 0.0
        sq = 0.0
        deep = 0.0
        inf = 0.0
        factor_cells = 0
        factor_worst = 0.0
        factor_sq = 0.0
        for channel, mt in CHANNEL_MT.items():
            section = sections.get(mt)
            if section is None:
                continue
            for t_key, by_group in section.items():
                for g_key, rec in by_group.items():
                    row = my_groups.get(int(g_key) - 1)
                    if row is None or "group_shielded_b" not in row:
                        continue
                    xs = [float(v) for v in rec["xs_b"]]
                    if len(xs) != len(SIG0) or xs[0] == 0.0:
                        continue
                    my_xs = row["group_shielded_b"][channel]
                    my_f = row["group_factors"][channel]
                    for si in range(len(SIG0)):
                        if xs[si] <= 0.0 or my_xs[si][0] <= 0.0:
                            continue
                        dev = abs(my_xs[si][0] - xs[si]) / xs[si]
                        cells += 1
                        sq += dev * dev
                        worst = max(worst, dev)
                        if si == 0:
                            inf = max(inf, dev)
                        if si == len(SIG0) - 1:
                            deep = max(deep, dev)
                        # Factor-vs-factor: normalizes away the unshielded
                        # convention difference (ladder-mean vs pointwise
                        # collapse) to isolate the shielding shape.
                        if si > 0:
                            f_oracle = xs[si] / xs[0]
                            if f_oracle > 0 and my_f[si][0] > 0:
                                fd = abs(my_f[si][0] - f_oracle) / f_oracle
                                factor_cells += 1
                                factor_sq += fd * fd
                                factor_worst = max(factor_worst, fd)
        result["materials"][key] = {
            "cells": cells,
            "max_relative_deviation": worst if cells else None,
            "rms_relative_deviation": (sq / cells) ** 0.5 if cells else None,
            "inf_sigma0_max": inf,
            "deep_sigma0_max": deep,
            "factor_cells": factor_cells,
            "factor_max_relative_deviation": (
                factor_worst if factor_cells else None
            ),
            "factor_rms_relative_deviation": (
                (factor_sq / factor_cells) ** 0.5 if factor_cells else None
            ),
        }
    output.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
