"""P120 Amendment C regressions (C2). These read Git objects and never launch ACTINV."""
import hashlib
import json
import unittest

import check_p110


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class VersionTokenTests(unittest.TestCase):
    OUTPUT = b'{"solver":"actinv-core 1.5.0","note":"1.5.0","other":"actinv-core 1.5.01"}'

    def test_only_the_exact_token_is_mapped(self):
        mapped = check_p110.map_version_token(self.OUTPUT, "1.5.0", "1.4.0")
        self.assertIn(b'"solver":"actinv-core 1.4.0"', mapped)
        self.assertIn(b'"note":"1.5.0"', mapped)
        self.assertEqual(mapped.replace(b"actinv-core 1.4.0", b"actinv-core 1.5.0"), self.OUTPUT)

    def test_any_other_byte_difference_survives_the_map(self):
        changed = self.OUTPUT.replace(b'"note"', b'"nite"')
        self.assertNotEqual(digest(check_p110.map_version_token(self.OUTPUT, "1.5.0", "1.4.0")),
                            digest(check_p110.map_version_token(changed, "1.5.0", "1.4.0")))

    def test_equal_versions_leave_the_bytes_alone(self):
        self.assertIs(check_p110.map_version_token(self.OUTPUT, "1.5.0", "1.5.0"), self.OUTPUT)

    def test_the_sealed_version_is_read_from_the_seal_commit(self):
        current, sealed = check_p110._versions()
        self.assertEqual(sealed, "1.4.0")
        self.assertRegex(current, r"^\d+\.\d+\.\d+")


class SealedControlHashTests(unittest.TestCase):
    def setUp(self):
        self.sealed = json.loads(check_p110.G0.read_text(encoding="utf-8"))["control_hashes"]

    def test_seal_commit_objects_equal_the_sealed_control_hashes(self):
        self.assertEqual(check_p110._control_hashes(self.sealed), self.sealed)

    def test_a_commit_where_the_files_differ_does_not_match(self):
        before = check_p110.P110_SEAL_COMMIT + "~1"
        self.assertNotEqual(check_p110._control_hashes(self.sealed, before), self.sealed)


if __name__ == "__main__":
    unittest.main()
