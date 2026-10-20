"""P120 Amendment D regressions (D2). These never launch ACTINV."""
import hashlib
import unittest

import check_p110


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


INDENT = b"                  "
NEW_BLOCK = (b'"rate_pruning": {\n'
             + INDENT + b'"activity_Bq_per_g_bound": 0.0,\n'
             + INDENT + b'"dropped": [],\n'
             + INDENT + b'"dropped_activity_Bq_per_g_bound": 0.0,\n'
             + INDENT + b'"dropped_heat_W_per_g_bound": 0.0,\n'
             + INDENT + b'"heat_W_per_g_bound": 0.0,\n'
             + INDENT + b'"kept_activity_Bq_per_g_bound": 0.0,\n'
             + INDENT + b'"kept_heat_W_per_g_bound": 0.0,\n'
             + INDENT + b'"threshold_atoms_per_g": 0.0,\n'
             + INDENT + b'"unbounded_components": []\n')
OLD_BLOCK = (b'"rate_pruning": {\n'
             + INDENT + b'"dropped": [],\n'
             + INDENT + b'"removed_heat_W_per_g_bound": -0.0,\n'
             + INDENT + b'"threshold_atoms_per_g": 0.0\n')
TAIL = b'                },\n                "schedule": {\n'
OUTPUT = b'{\n  "ledger": {\n                ' + NEW_BLOCK + TAIL + b'                ' + NEW_BLOCK + TAIL + b'}\n'


class P148LedgerMapTests(unittest.TestCase):
    def test_the_unpruned_block_maps_to_the_pre_p148_block(self):
        mapped = check_p110.map_p148_ledger(OUTPUT)
        self.assertEqual(mapped, OUTPUT.replace(NEW_BLOCK, OLD_BLOCK))
        self.assertEqual(mapped.count(OLD_BLOCK), 2)

    def test_output_without_the_block_is_unchanged(self):
        self.assertEqual(check_p110.map_p148_ledger(OLD_BLOCK + TAIL), OLD_BLOCK + TAIL)

    def test_a_pruned_or_altered_block_is_not_mapped(self):
        planted = {
            "dropped nuclide": NEW_BLOCK.replace(b'"dropped": [],', b'"dropped": ["Mn59"],'),
            "nonzero heat bound": NEW_BLOCK.replace(b'"heat_W_per_g_bound": 0.0', b'"heat_W_per_g_bound": 1e-23'),
            "nonzero threshold": NEW_BLOCK.replace(b'"threshold_atoms_per_g": 0.0', b'"threshold_atoms_per_g": 1.0'),
            "unbounded component": NEW_BLOCK.replace(b'"unbounded_components": []', b'"unbounded_components": [3]'),
            "missing field": NEW_BLOCK.replace(INDENT + b'"kept_heat_W_per_g_bound": 0.0,\n', b""),
            "extra field": NEW_BLOCK.replace(b'"dropped": [],\n', b'"dropped": [],\n' + INDENT + b'"x": 0.0,\n'),
            "mixed indentation": NEW_BLOCK.replace(INDENT + b'"heat_W', INDENT + b' "heat_W'),
        }
        for name, block in planted.items():
            with self.subTest(name):
                self.assertNotEqual(block, NEW_BLOCK)
                self.assertEqual(check_p110.map_p148_ledger(block + TAIL), block + TAIL)

    def test_any_other_byte_difference_survives_the_map(self):
        changed = OUTPUT.replace(b'"schedule"', b'"schedulf"', 1)
        self.assertNotEqual(digest(check_p110.map_p148_ledger(OUTPUT)), digest(check_p110.map_p148_ledger(changed)))

    def test_the_digest_applies_both_registered_maps(self):
        source = check_p110.Path(check_p110.__file__).read_text(encoding="utf-8")
        self.assertEqual(source.count("map_version_token(map_p148_ledger(output_bytes),"), 1)


if __name__ == "__main__":
    unittest.main()
