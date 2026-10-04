"""Independent ordinary twin expectations for P113's unassayed fixtures.

These fixtures have passing certified bands and no detector or assay declarations.
They establish the retained ordinary result contract without executing production.
Assay cases are checked by same-request parity and specific assimilation evidence.
"""
from __future__ import annotations

import copy
import math
from pathlib import Path


NOTE = ("clearance is on the certified band edge, not the nominal — a cell clears "
        "only when its conservative interval clears the limit. dose_points flux "
        "is a point-kernel screening estimate (no scatter/buildup transport), "
        "not a certified band")


def _finite(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("legacy fixture requires finite numbers")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("legacy fixture requires finite numbers")
    return number


def expected_legacy(case: dict) -> dict | None:
    spec = case["spec"]
    if any(spec.get(name) for name in ("assays", "dose_assays", "dose_points")):
        return None
    records = [json_record(line) for line in case["mesh_ndjson"].splitlines()
               if line.strip()]
    cells = [record for record in records if record["record"] == "cell"]
    if (records[0]["record"] != "header" or records[-1]["record"] != "footer"
            or records[0]["cell_count"] != len(cells)
            or records[-1]["cell_count"] != len(cells)):
        raise ValueError("legacy fixture mesh counts/order differ")
    rows = []
    binding = {}
    for cell in cells:
        entries = []
        for step in cell["result"]["steps"]:
            time = _finite(step["t_s"])
            selected = spec.get("times_s")
            if selected is not None and not any(
                    abs(time - _finite(wanted)) <= max(abs(time), abs(_finite(wanted)), 1e-300) * 1e-9
                    for wanted in selected):
                continue
            for limit in spec["limits"]:
                boundary = _finite(limit["limit"])
                if boundary <= 0.0:
                    raise ValueError("P113 legacy fixture limits must be positive")
                band = step["uncertainty"]["responses"][limit["response"]]["conservative_interval"]
                low, high = map(_finite, band)
                sense = limit.get("sense", "le")
                if sense not in {"le", "ge"}:
                    raise ValueError("invalid legacy fixture limit sense")
                margin = ((boundary - high) if sense == "le" else (low - boundary)) / boundary
                if margin < 0.0:
                    raise ValueError("P113 independent legacy fixtures require passing bands")
                entries.append({"t_s": time, "limit": limit["name"],
                                "band": [low, high], "margin": margin, "clears": True})
                key = f"{limit['name']}@{time:g}"
                if key not in binding or margin < binding[key]["margin"]:
                    binding[key] = {"cell": cell["id"], "margin": margin,
                                    "band": [low, high], "limit": boundary}
        if not entries:
            raise ValueError("legacy fixture must evaluate at least one band per cell")
        rows.append({"cell": cell["id"], "ordinal": cell["ordinal"],
                     "verdict": "cleared", "worst_margin": min(item["margin"] for item in entries),
                     "entries": entries})
    available = {cell["id"] for cell in cells}
    components = {}
    for name, members in sorted(spec.get("components", {}).items()):
        if not members or any(member not in available for member in members):
            raise ValueError("legacy fixture component membership is incomplete")
        components[name] = {"cells": list(members), "verdict": "cleared", "missing_cells": []}
    return {"schema": "actinv-twin-1", "mesh_output": spec["mesh_output"], "cells": len(cells),
            "limits": [{"name": item["name"], "response": item["response"],
                        "limit": item["limit"], "sense": item.get("sense", "le")}
                       for item in spec["limits"]],
            "per_cell": rows, "components": components, "dose_points": [],
            "facility": {"cleared": len(cells), "restricted": 0, "binding_cells": binding,
                         "assimilated_cells": [], "assay_recommendations": [], "dose_assimilations": []},
            "note": NOTE}


def json_record(line: str) -> dict:
    import json
    value = json.loads(line)
    if not isinstance(value, dict):
        raise ValueError("legacy mesh record must be an object")
    return value


def validate_legacy(output: dict, case: dict, case_dir: Path, compare) -> list[str]:
    expected = case["expected"].get("legacy_twin_result")
    if expected is None:
        return []
    ordinary = {key: copy.deepcopy(value) for key, value in output.items()
                if key not in {"waste_classification", "waste_facility_coverage"}}
    if ordinary.get("mesh_output") != str(case_dir / case["spec"]["mesh_output"]):
        return ["legacy mesh_output does not match the supplied input path"]
    # Only this environment-dependent path is replaced by its declared reference.
    ordinary["mesh_output"] = case["spec"]["mesh_output"]
    return compare(ordinary, expected, "independent-ordinary-twin-result")
