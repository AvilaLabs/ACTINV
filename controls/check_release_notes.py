#!/usr/bin/env python3
"""P6-G5: the release notes must carry every entry of the roadmap's known-limitations table, so a limitation cannot be
dropped between the plan and what ships. Compares the two tables row for row."""
import os, sys, json
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
def table(text, header):
    seg = text.split(header)[1].split("\n## ")[0]
    lines = seg.splitlines(); start = next(i for i, l in enumerate(lines) if l.startswith("|---"))
    out = []
    for l in lines[start + 1:]:
        if not l.startswith("| "): break
        out.append(tuple(c.strip() for c in l.strip("|").split("|")))
    return out
road = table(open(os.path.join(ROOT, "docs", "ROADMAP.md")).read(), "### Known limitations carried into v0.1")
notes = table(open(os.path.join(ROOT, "docs", "RELEASE_NOTES_v0.1.md")).read(), "## Known limitations")
missing = [r for r in road if r not in notes]; extra = [r for r in notes if r not in road]
res = {"roadmap_rows": len(road), "release_note_rows": len(notes),
       "missing_from_release_notes": [r[0][:60] for r in missing], "not_in_roadmap": [r[0][:60] for r in extra],
       "pass": bool(road and not missing and not extra)}
json.dump(res, open(os.path.join(ROOT, "results", "check_release_notes.json"), "w"), indent=1); print(json.dumps(res, indent=1))
