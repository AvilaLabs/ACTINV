"""Source-only regressions for the NJOY kerma-table converter (synthetic tapes, no NJOY run)."""
from __future__ import annotations

import io
import json
import math
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import tempfile
import unittest

import build_kerma_table as builder


def real(value: float) -> str:
    return f"{value:11.4E}"


def integer(value: int) -> str:
    return f"{value:11d}"


class Tape:
    """Minimal ENDF-6 tape writer: 66 data columns plus MAT/MF/MT/sequence."""

    def __init__(self, mat: int) -> None:
        self.mat = mat
        self.rows: list[str] = []
        self.sequence = 0

    def line(self, fields: list[str], mf: int, mt: int) -> None:
        self.sequence += 1
        data = "".join(fields).ljust(66)
        self.rows.append(f"{data}{self.mat:4d}{mf:2d}{mt:3d}{self.sequence:5d}")

    def end(self, mf: int) -> None:
        self.rows.append(f"{' ' * 66}{self.mat:4d}{mf:2d}{0:3d}{99999:5d}")

    def text(self) -> str:
        return "\n".join(["TPID".ljust(66) + "   1 0  0    0", *self.rows, " " * 66 + "  -1 0  0    0"])


def pendf_tape(
    za: float, liso: int, temperature: float, points: list[tuple[float, float]], mt: int = 301
) -> str:
    tape = Tape(2631)
    tape.line([real(za), real(55.9), integer(0), integer(0), integer(0), integer(0)], 1, 451)
    tape.line([real(0.0), real(0.0), integer(0), integer(liso), integer(0), integer(6)], 1, 451)
    tape.line([real(1.0), real(2.0e7), integer(0), integer(0), integer(0), integer(7)], 1, 451)
    tape.line([real(temperature), real(0.0), integer(0), integer(0), integer(0), integer(0)], 1, 451)
    tape.end(1)
    tape.line([real(za), real(55.9), integer(0), integer(0), integer(0), integer(0)], 3, mt)
    tape.line([real(0.0), real(0.0), integer(0), integer(0), integer(1), integer(len(points))], 3, mt)
    tape.line([integer(len(points)), integer(2)], 3, mt)
    flat = [value for point in points for value in point]
    for start in range(0, len(flat), 6):
        tape.line([real(v) for v in flat[start : start + 6]], 3, mt)
    tape.end(3)
    return tape.text()


def gendf_tape(
    za: float, temperature: float, boundaries: list[float], groups: dict[int, float]
) -> str:
    tape = Tape(2631)
    ngn = len(boundaries) - 1
    tape.line([real(za), real(55.9), integer(0), integer(1), integer(-1), integer(1)], 1, 451)
    words = ["test".ljust(11), real(1.0e10), *[real(b) for b in boundaries], real(0.0)]
    tape.line(
        [real(temperature), real(0.0), integer(ngn), integer(0), integer(len(words)), integer(0)],
        1,
        451,
    )
    for start in range(0, len(words), 6):
        tape.line(words[start : start + 6], 1, 451)
    tape.end(1)
    tape.line([real(za), real(za), integer(1), integer(1), integer(0), integer(ngn)], 3, 301)
    for group, sigma in sorted(groups.items()):
        tape.line(
            [real(temperature), real(0.0), integer(2), integer(1), integer(2), integer(group)],
            3,
            301,
        )
        tape.line([real(1.0e8), real(sigma)], 3, 301)
    tape.end(3)
    return tape.text()


class KermaConverterTests(unittest.TestCase):
    def test_endf_number_forms(self) -> None:
        self.assertEqual(builder.endf_float(" 1.500000+3"), 1500.0)
        self.assertEqual(builder.endf_float("-2.5-2   "), -0.025)
        self.assertEqual(builder.endf_float("1.0000E+00"), 1.0)
        self.assertEqual(builder.endf_float("           "), 0.0)
        with self.assertRaises(builder.TapeError):
            builder.endf_float("not-a-number")

    def test_nuclide_naming_matches_the_runtime(self) -> None:
        self.assertEqual(builder.za_name(26056.0, 0), "Fe56")
        self.assertEqual(builder.za_name(95242.0, 1), "Am242m1")
        self.assertEqual(builder.za_name(26000.0, 0), "Fe")

    def test_pendf_constant_and_linear_sections_collapse_exactly(self) -> None:
        boundaries = [1.0, 10.0, 100.0]
        constant = builder.read_tape(pendf_tape(26056.0, 0, 293.6, [(1.0e-5, 10.0), (2.0e7, 10.0)]))
        materials, uncovered = builder.pendf_materials(constant, boundaries, (301,), None)
        self.assertEqual(uncovered, [])
        self.assertEqual(materials[0].name, "Fe56")
        self.assertEqual(materials[0].temperature, 293.6)
        for value in materials[0].mt[301]:
            self.assertAlmostEqual(value, 10.0, places=9)
        # sigma(E) = E collapsed with a 1/E weight is (hi - lo) / ln(hi / lo)
        linear = builder.read_tape(pendf_tape(26056.0, 0, 293.6, [(1.0e-5, 1.0e-5), (2.0e7, 2.0e7)]))
        materials, _ = builder.pendf_materials(linear, boundaries, (301,), None)
        for (low, high), value in zip(zip(boundaries, boundaries[1:]), materials[0].mt[301]):
            self.assertAlmostEqual(value, (high - low) / math.log(high / low), places=6)

    def test_pendf_range_edges_are_zero_and_other_temperatures_are_skipped(self) -> None:
        sections = builder.read_tape(pendf_tape(26056.0, 0, 293.6, [(2.0, 5.0), (4.0, 5.0)]))
        materials, _ = builder.pendf_materials(sections, [1.0, 2.0, 4.0, 8.0], (301,), None)
        values = materials[0].mt[301]
        self.assertEqual(values[0], 0.0)
        self.assertAlmostEqual(values[1], 5.0, places=9)
        self.assertEqual(values[2], 0.0)
        skipped, _ = builder.pendf_materials(sections, [1.0, 2.0], (301,), 600.0)
        self.assertEqual(skipped, [])

    def test_pendf_without_the_requested_section_is_uncovered_not_zero_filled(self) -> None:
        sections = builder.read_tape(
            pendf_tape(26056.0, 0, 293.6, [(1.0e-5, 1.0), (2.0e7, 1.0)], mt=444)
        )
        materials, uncovered = builder.pendf_materials(sections, [1.0, 2.0], (301,), None)
        self.assertEqual(materials, [])
        self.assertEqual(uncovered, ["Fe56"])

    def test_non_linear_interpolation_is_refused(self) -> None:
        with self.assertRaises(builder.TapeError):
            builder.collapse_linlin([1.0, 2.0], [(2, 5)], [1.0, 2.0], [1.0, 2.0])

    def test_gendf_groups_and_boundaries_are_read_and_missing_groups_are_zero(self) -> None:
        sections = builder.read_tape(
            gendf_tape(26056.0, 293.6, [1.0, 10.0, 100.0, 1000.0], {1: 2.5e3, 3: 7.5e3})
        )
        boundaries, materials, uncovered = builder.gendf_materials(sections, (301,), None)
        self.assertEqual(boundaries, [1.0, 10.0, 100.0, 1000.0])
        self.assertEqual(uncovered, [])
        self.assertEqual(materials[0].name, "Fe56")
        self.assertEqual(materials[0].mt[301], [2.5e3, 0.0, 7.5e3])

    def test_command_line_writes_a_table_the_runtime_format_describes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kerma-table-") as directory:
            root = Path(directory)
            tape = root / "fe56.gendf"
            tape.write_text(gendf_tape(26056.0, 293.6, [1.0, 10.0, 100.0], {1: 1.0, 2: 2.0}))
            output = root / "kerma.json"
            with redirect_stdout(io.StringIO()):
                status = builder.main(
                    [str(output), "--gendf", str(tape), "--group-structure", "test-2g"]
                )
            self.assertEqual(status, 0)
            table = json.loads(output.read_text())
            self.assertEqual(table["format"], "actinv-kerma-table-1")
            self.assertEqual(table["units"], "kerma_barn_eV_per_group")
            self.assertEqual(table["projectile"], "neutron")
            self.assertEqual(table["boundaries_eV"], [1.0, 10.0, 100.0])
            self.assertEqual(table["targets"], {"Fe56": [1.0, 2.0]})
            self.assertEqual(table["temperature_K"], 293.6)
            self.assertEqual(table["files"][0]["path"], "fe56.gendf")
            self.assertEqual(len(table["files"][0]["sha256"]), 64)
            self.assertEqual(table["uncovered"], [])
            self.assertNotIn("damage_energy_targets", table)
            for field in ("citation", "edition", "url"):
                self.assertTrue(table["source"][field].strip())

    def test_pendf_requires_boundaries_and_an_empty_result_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kerma-table-") as directory:
            root = Path(directory)
            tape = root / "fe56.pendf"
            tape.write_text(pendf_tape(26056.0, 0, 293.6, [(1.0e-5, 1.0), (2.0e7, 1.0)], mt=444))
            output = root / "kerma.json"
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                builder.main([str(output), "--pendf", str(tape), "--group-structure", "x"])
            boundaries = root / "boundaries.json"
            boundaries.write_text(json.dumps({"boundaries_eV": [1.0, 2.0]}))
            error = io.StringIO()
            with redirect_stderr(error):
                status = builder.main(
                    [
                        str(output),
                        "--pendf",
                        str(tape),
                        "--boundaries-from",
                        str(boundaries),
                        "--group-structure",
                        "x",
                    ]
                )
            self.assertEqual(status, 1)
            self.assertIn("MF=3/MT=301", error.getvalue())
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
