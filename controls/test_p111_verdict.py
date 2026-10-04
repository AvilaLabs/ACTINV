"""Source-only rejection checks for P111 verdict evidence parsing."""
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_p111_verdict as verdict


class VerdictEvidenceTests(unittest.TestCase):
    def test_bound_files_reject_wrong_hash_and_unsafe_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); artifact=root/"controls.json"; artifact.write_text("frozen")
            digest=hashlib.sha256(b"frozen").hexdigest()
            with patch.object(verdict,"ROOT",root):
                self.assertTrue(verdict._bound_files({"controls.json":digest},{"controls.json"}))
                self.assertFalse(verdict._bound_files({"controls.json":"0"*64},{"controls.json"}))
                self.assertFalse(verdict._bound_files({"../controls.json":digest},{"../controls.json"}))

    def test_boolean_counts_are_not_integer_evidence(self):
        self.assertFalse(verdict.count(True,1))
        self.assertFalse(verdict.count(False,0))
        self.assertTrue(verdict.count(1,1))


if __name__=="__main__":
    unittest.main()
