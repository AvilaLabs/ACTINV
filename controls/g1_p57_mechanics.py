#!/usr/bin/env python3
"""P57 G1 — mechanics: the fixture r2s-source emits all three foreign
formats with reconcilable structure, and every documented refusal fires.
"""
from __future__ import annotations

import json
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controls"))
import p57_case as p57  # noqa: E402

RESULT = ROOT / "results/g1_p57_mechanics.json"


def check_openmc_structure(text: str, n_cells: int) -> dict:
    root = ET.fromstring(text)
    assert root.tag == "sources"
    srcs = [e for e in root if e.tag == "source"]
    out = {"n_sources": len(srcs), "with_energy": 0, "with_box": 0,
           "isotropic": 0}
    for s in srcs:
        assert s.get("type") == "independent"
        assert s.get("particle") == "photon"
        space = s.find("space")
        if space is not None and space.get("type") == "cartesian":
            if all(space.find(a).get("type") == "uniform"
                   for a in ("x", "y", "z")):
                out["with_box"] += 1
        en = s.find("energy")
        if en is not None and en.get("type") == "discrete":
            out["with_energy"] += 1
            vals = en.find("parameters").text.split()
            assert len(vals) % 2 == 0
        if s.find("angle") is not None and \
                s.find("angle").get("type") == "isotropic":
            out["isotropic"] += 1
    return out


def check_mcnp_structure(text: str, n_cells: int) -> dict:
    toks = text.split()
    out = {"sdef": toks.count("SDEF"), "si": sum(1 for t in toks
                                                 if t.startswith("SI")),
           "sp": sum(1 for t in toks if t.startswith("SP"))}
    assert out["sdef"] == n_cells
    # per cell: 3 spatial SI + 1 energy SI = 4
    assert out["si"] == 4 * n_cells
    assert out["sp"] == 4 * n_cells
    return out


def check_serpent_structure(text: str, n_cells: int) -> dict:
    lines = [l for l in text.splitlines() if l.startswith("src ")]
    assert len(lines) == n_cells
    for l in lines:
        t = l.split()
        assert t[0] == "src" and t[2] == "p"
        for ax in ("sx", "sy", "sz"):
            i = t.index(ax)
            assert float(t[i + 1]) < float(t[i + 2])
    return {"src_lines": len(lines)}


def main() -> int:
    checks = {}
    fixture = p57.fixture_r2s()
    cells = [json.loads(l) for l in fixture.splitlines()
             if json.loads(l).get("record") == "cell"]

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)

        for fmt in ("openmc", "mcnp", "serpent"):
            out = td / f"out.{fmt}"
            r = p57.run_export(fmt, fixture, out)
            checks[f"{fmt}_emits"] = r.returncode == 0 and out.exists()
            if not checks[f"{fmt}_emits"]:
                print(r.stderr)

        om = ET.fromstring((td / "out.openmc").read_text())
        srcs = [e for e in om if e.tag == "source"]
        checks["openmc_three_sources"] = len(srcs) == 3
        checks["openmc_strengths_match"] = all(
            abs(float(s.get("strength")) - c["photons_s"]) < 1e-12
            for s, c in zip(srcs, cells))
        checks["openmc_isotropic_all"] = all(
            s.find("angle").get("type") == "isotropic" for s in srcs)
        checks["openmc_provenance"] = "actinv-source-adapter-1" in \
            (td / "out.openmc").read_text() and \
            "input_sha256=" in (td / "out.openmc").read_text()
        st = check_openmc_structure((td / "out.openmc").read_text(), 3)
        checks["openmc_struct"] = (st["with_box"] == 3 and
                                   st["with_energy"] == 2 and
                                   st["isotropic"] == 3)

        ms = check_mcnp_structure((td / "out.mcnp").read_text(), 3)
        checks["mcnp_struct"] = (ms["sdef"] == 3 and ms["si"] == 12 and
                                 ms["sp"] == 12)
        checks["mcnp_provenance"] = (td / "out.mcnp").read_text() \
            .startswith("c actinv-source-adapter-1")
        checks["mcnp_mev"] = "SI4 L 0.1 0.5" in \
            (td / "out.mcnp").read_text()  # 100keV & 500keV -> 0.1, 0.5 MeV

        ss = check_serpent_structure((td / "out.serpent").read_text(), 3)
        checks["serpent_struct"] = ss["src_lines"] == 3
        checks["serpent_line_spectrum"] = "sb 2 0" in \
            (td / "out.serpent").read_text()
        checks["serpent_id_sanitized"] = "src s_cell_beta_2_1" in \
            (td / "out.serpent").read_text()  # 'cell-beta/2' -> '_'
        checks["serpent_provenance"] = (td / "out.serpent").read_text() \
            .startswith("% actinv-source-adapter-1")

        # zero-group cell emits zero-weight entries, not silence
        checks["zero_cell_mcnp_wgt0"] = "WGT=0" in \
            (td / "out.mcnp").read_text()
        checks["zero_cell_serpent"] = "se 1.0" in \
            (td / "out.serpent").read_text()
        checks["zero_cell_openmc"] = 'strength="0"' in \
            (td / "out.openmc").read_text()

        # ---- rejections ----
        def expect_fail(name, fmt, text):
            out = td / f"{name}.out"
            r = p57.run_export(fmt, text, out)
            return r.returncode != 0

        checks["reject_bad_bounds"] = expect_fail(
            "bad_bounds", "mcnp", p57.fixture_bad_bounds())
        checks["reject_wrong_schema"] = expect_fail(
            "wrong_schema", "openmc",
            fixture.replace("actinv-r2s-source-1", "actinv-other-9"))
        checks["reject_no_footer"] = expect_fail(
            "no_footer", "serpent",
            "\n".join(fixture.splitlines()[:-1]) + "\n")
        no_step = fixture.replace('"step": 2,', "")
        checks["reject_no_step"] = expect_fail("no_step", "mcnp", no_step)
        checks["reject_no_bounds"] = expect_fail(
            "no_bounds", "openmc",
            fixture.replace('"bounds_cm": [[0.0, 1.0], [0.0, 2.0], [0.0, 3.0]]',
                            '"bounds_cm": null'))
        checks["reject_bad_format"] = expect_fail(
            "bad_fmt", "fispact", fixture)
        checks["reject_garbage"] = expect_fail(
            "garbage", "mcnp", "not json\n")

    evidence = {"schema": "actinv-p57-g1-mechanics-1",
                "pass": all(checks.values()), "checks": checks}
    RESULT.write_text(json.dumps(evidence, indent=1, sort_keys=True) + "\n")
    print(json.dumps(checks, indent=1, sort_keys=True))
    return 0 if evidence["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
