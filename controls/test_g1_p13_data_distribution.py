#!/usr/bin/env python3
"""Keep the synthetic fuzz archive exception exact and independently reviewed."""

import ast
import io
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import g1_p13_data_distribution as control


FIXTURE = "verification/fuzz/seeds/npz/small-library.npz"


class ReviewedArchiveTests(unittest.TestCase):
    def test_reviewed_fixture_contains_only_the_documented_synthetic_arrays(self):
        data = (control.ROOT / FIXTURE).read_bytes()
        self.assertEqual(len(data), 764)
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            expected = {
                "rows.npy": ("<i8", (1, 5), "<5q", (260560, 102, 0, 0, 3)),
                "sig.npy": ("<f8", (1, 2), "<2d", (1.0, 2.0)),
                "bounds.npy": ("<f8", (3,), "<3d", (1.0, 2.0, 3.0)),
            }
            self.assertEqual(sorted(archive.namelist()), sorted(expected))
            for name, (dtype, shape, layout, values) in expected.items():
                with self.subTest(member=name):
                    member = archive.read(name)
                    self.assertEqual(member[:8], b"\x93NUMPY\x01\x00")
                    header_length, = struct.unpack("<H", member[8:10])
                    header = ast.literal_eval(member[10:10 + header_length].decode("ascii"))
                    self.assertEqual(header, {
                        "descr": dtype, "fortran_order": False, "shape": shape,
                    })
                    self.assertEqual(struct.unpack(layout, member[10 + header_length:]), values)
        self.assertTrue(control.reviewed_archive(FIXTURE))

    def test_modified_fixture_and_unlisted_identical_copy_are_rejected(self):
        data = (control.ROOT / FIXTURE).read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = root / FIXTURE
            fixture.parent.mkdir(parents=True)
            fixture.write_bytes(data)
            copy = fixture.with_name("unreviewed.npz")
            copy.write_bytes(data)
            with patch.object(control, "ROOT", root):
                self.assertTrue(control.reviewed_archive(FIXTURE))
                self.assertFalse(control.reviewed_archive(copy.relative_to(root).as_posix()))
                fixture.write_bytes(bytes([data[0] ^ 1]) + data[1:])
                self.assertFalse(control.reviewed_archive(FIXTURE))


if __name__ == "__main__":
    unittest.main()
