#!/usr/bin/env python3
"""Independent energy-accounting and stale/malformed-output controls."""
import copy
import math
import unittest

import fns_iron_diagnose as diag


class HeatControl(unittest.TestCase):
    def setUp(self):
        self.records = {(25056, 0): {'nst': 0, 'half_life': math.log(2),
                                    'energies': [1, 0, 2, 0, 3, 0, 1000, 0]}}
        self.result = {'steps': [{}]}
        for time, atoms in [(301, 10), (302, 10 / math.e)]:
            self.result['steps'].append({
                't_s': time, 'flux': 0,
                'inventory': [{'Z': 25, 'A': 56, 'LISO': 0, 'nuclide': 'Mn56', 'atoms_per_g': atoms}],
                'activity_Bq_per_g': {'Mn56': atoms},
                'heat_W_per_g': {'total': atoms * 6 * 1.602176634e-19}})

    def test_total_energies_exclude_detailed_components_and_uncertainties(self):
        rows = diag.reconstruct(self.result, self.records)
        self.assertAlmostEqual(rows[0]['total_microW_per_g'], 60 * 1.602176634e-13, places=20)
        self.assertAlmostEqual(rows[1]['Mn56_cooling_relative_difference'], 0, places=14)

    def test_heat_mismatch_rejected(self):
        self.result['steps'][1]['heat_W_per_g']['total'] *= 100
        with self.assertRaisesRegex(ValueError, 'reconstruction'):
            diag.reconstruct(self.result, self.records)

    def test_missing_active_record_rejected(self):
        with self.assertRaisesRegex(ValueError, 'missing active'):
            diag.reconstruct(self.result, {})

    def test_wrong_time_and_nonfinite_heat_rejected(self):
        rows = [{'cooling_seconds': 1, 'measured_microW_per_g': 1, 'reported_error_microW_per_g': .1},
                {'cooling_seconds': 2, 'measured_microW_per_g': 1, 'reported_error_microW_per_g': .1}]
        for field, value in [('t_s', 5), ('heat_W_per_g', {'total': float('nan')})]:
            result = copy.deepcopy(self.result)
            result['steps'][1][field] = value
            with self.assertRaises(ValueError):
                diag.score_any(result, rows)


if __name__ == '__main__':
    unittest.main()
