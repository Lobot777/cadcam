"""Unit oracles plus integration checks of the independently run FreeCAD audit."""
import importlib.util
import json
from pathlib import Path
import random
import sys
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('audit_workorder_reference', ROOT / 'scripts/audit_workorder_reference.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class ReferenceUnitTests(unittest.TestCase):
    def test_visible_rounding_matches_printed_text(self):
        tasks, describe = audit.load_tasks()
        for task in tasks:
            visible = audit.visible_spec(task)
            for seed in range(10):
                self.assertEqual(describe(*task['size'], task['pockets'], task['holes'], random.Random(seed)),
                                 describe(*visible['size'], visible['pockets'], visible['holes'], random.Random(seed)))
            for old, new in zip(task['holes'], visible['holes']):
                self.assertEqual(new['x'], float('%.0f' % old['x']))
                self.assertEqual(new['y'], float('%.0f' % old['y']))
        self.assertTrue(any(audit.visible_spec(t)['holes'] != t['holes'] for t in tasks))

    def test_rounding_all_dimensions_and_ties(self):
        task = {'size': [60.5, 61.5, 40.4],
                'pockets': [{'cx': 20.5, 'cy': 21.5, 'w': 10.8, 'h': 11.1, 'd': 3.5}],
                'holes': [{'x': 9.5, 'y': 10.5, 'dia': 6.5, 'depth': 4.5, 'through': False},
                          {'x': 12.1, 'y': 14.9, 'dia': 8.2, 'depth': 7.7, 'through': True}]}
        visible = audit.visible_spec(task)
        self.assertEqual(visible['size'], [60., 62., 40.])
        self.assertEqual(visible['pockets'][0], {'cx': 20., 'cy': 22., 'w': 11., 'h': 11., 'd': 4.})
        self.assertEqual(visible['holes'][0], {'x': 10., 'y': 10., 'dia': 6., 'depth': 4., 'through': False})
        self.assertEqual(visible['holes'][1]['depth'], 7.7)
        self.assertEqual(task['holes'][0]['x'], 9.5)  # no mutation

    def test_nl02_analytic_overlap(self):
        tasks, _ = audit.load_tasks()
        task = tasks[1]
        self.assertEqual(task['id'], 'NL02')
        p, q = task['pockets']
        self.assertEqual([p['w'], p['h'], p['d']], [40, 20, 8])
        self.assertEqual([q['w'], q['h'], q['d']], [40, 40, 5])
        b1, b2 = [audit.pocket_bounds(pocket, task['size']) for pocket in (p, q)]
        overlap_area = 1
        for axis in (0, 1):
            overlap_area *= min(b1[axis][1], b2[axis][1]) - max(b1[axis][0], b2[axis][0])
        self.assertEqual(overlap_area, 480)
        self.assertEqual(audit.rectangular_union_volume(task['pockets'], task['size']), 6400 + 8000 - 2400)

    def test_rectangular_union_triple_and_clipping(self):
        p = {'cx': 3, 'cy': 3, 'w': 4, 'h': 4, 'd': 4}
        q = dict(p, cx=5, d=2)
        self.assertEqual(audit.rectangular_union_volume([p, q, p], [10, 10, 10]), 80)
        self.assertEqual(audit.rectangular_union_volume([dict(p, cx=0, cy=0)], [10, 10, 10]), 16)
        self.assertEqual(audit.rectangular_union_volume([], [10, 10, 10]), 0)

    def test_tolerance_configuration_survives_serialization(self):
        payload = {'volume_abs_tolerance_mm3': 1e-5,
                   'volume_relative_tolerance': 1e-9,
                   'metric_tolerance': 1e-6,
                   'method': {'tolerances': {'volume_relative': 1e-9}},
                   'measurement': 1.234567891234}
        out = audit.normalized(payload)
        self.assertEqual(out['volume_relative_tolerance'], 1e-9)
        self.assertEqual(out['volume_abs_tolerance_mm3'], 1e-5)
        self.assertEqual(out['metric_tolerance'], 1e-6)
        self.assertEqual(out['method']['tolerances']['volume_relative'], 1e-9)
        self.assertEqual(out['measurement'], 1.23456789)
        self.assertEqual(json.loads(json.dumps(out))['volume_relative_tolerance'], 1e-9)

    def test_failed_export_zero_and_runtime_error_not_zero(self):
        def row(status, value, ok):
            return {'id': 'NL00', 'archive': {'script': {'exec_ok': ok}},
                    'measurements': {'visible': {'script': {'status': status,
                                     'removed_iou': value, 'whole_iou': value}}}}
        records = [row('ok', .8, True), row('archived_exec_failed', 0, False)]
        result = audit.summarize(records, 'visible', 'script')
        self.assertEqual(result['removed_iou']['all_task_mean'], .4)
        self.assertEqual(result['removed_iou']['conditional_mean'], .8)
        records.append(row('audit_error', None, True))
        result = audit.summarize(records, 'visible', 'script')
        self.assertIsNone(result['removed_iou']['all_task_mean'])
        self.assertFalse(result['complete'])


@unittest.skipUnless(audit.REPORT.exists(), 'Run the offline FreeCAD audit first')
class FreeCADReferenceIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads(audit.REPORT.read_text(encoding='utf-8'))

    def test_references_complete_and_archive_paired(self):
        self.assertTrue(self.report['audit_completed'], self.report['worker'])
        self.assertTrue(self.report['reference_complete'])
        self.assertTrue(self.report['archive_pairing_complete'])
        self.assertEqual(len(self.report['tasks']), 20)
        self.assertEqual(self.report['complete'], not bool(self.report['worker']['runtime_errors']))
        self.assertFalse(self.report['source_or_archive_changed_during_audit'])
        for task in self.report['tasks']:
            for key in ('gold_stock', 'gold_first_pocket', 'contract_stock'):
                self.assertFalse(task['pairing_checks'][key]['disagrees'])

    def test_freecad_matches_analytic_oracle(self):
        for task in self.report['tasks']:
            for definition in ('latent', 'visible'):
                validation = task['reference_validation'][definition]
                self.assertTrue(validation['valid'])
                self.assertTrue(validation['within_stock'])
                self.assertEqual(validation['solid_count'], 1)
                self.assertGreater(validation['cut_volume_mm3'], 0)
                self.assertAlmostEqual(validation['stock_volume_mm3'],
                                       validation['cut_volume_mm3'] + validation['volume_mm3'], places=4)
            self.assertLess(task['rectangular_oracle_error_mm3'], 1e-5)
        nl02 = next(t for t in self.report['tasks'] if t['id'] == 'NL02')
        oracle = nl02['NL02_overlap_oracle']
        self.assertTrue(oracle['passed'])
        self.assertAlmostEqual(oracle['union_volume_mm3'], 12000, places=5)
        self.assertAlmostEqual(oracle['overlap_volume_mm3'], 2400, places=5)
        self.assertTrue(nl02['gold_comparison']['latent']['disagrees'])

    def test_boolean_complement_identity(self):
        for task in self.report['tasks']:
            for measurements in task['measurements'].values():
                for measurement in measurements.values():
                    if measurement['status'] != 'ok':
                        continue
                    residual = measurement['boolean_complement_residual_mm3']
                    if residual is not None:
                        self.assertLess(residual, max(1e-5, task['reference_validation']['latent']['stock_volume_mm3'] * 1e-9))
                        self.assertAlmostEqual(measurement['whole_symmetric_difference_mm3'],
                                               measurement['removed_symmetric_difference_mm3'], places=3)

    def test_runtime_errors_are_unscored_not_export_failures(self):
        for definition, routes in self.report['summaries'].items():
            for route, summary in routes.items():
                errors = [t['id'] for t in self.report['tasks']
                          if t['measurements'][definition][route]['status'] == 'audit_error']
                self.assertEqual(summary['runtime_error_ids'], errors)
                for metric in ('removed_iou', 'whole_iou'):
                    lo, hi = summary[metric]['all_task_mean_bounds']
                    self.assertAlmostEqual(hi-lo, len(errors)/20, places=7)
                    if errors:
                        self.assertIsNone(summary[metric]['all_task_mean'])
                        self.assertIsNone(summary[metric]['ge_0.9_count'])
                    else:
                        self.assertAlmostEqual(summary[metric]['all_task_mean'], lo)

    def test_failures_and_evidence(self):
        for task in self.report['tasks']:
            for route in ('contract', 'script'):
                if not task['archive'][route]['exec_ok']:
                    for definition in ('latent', 'visible', 'archived_gold'):
                        m = task['measurements'][definition][route]
                        self.assertEqual(m['status'], 'archived_exec_failed')
                        self.assertEqual(m['removed_iou'], 0)
                        self.assertEqual(m['whole_iou'], 0)
        for filename, digest in self.report['sha256_evidence'].items():
            self.assertEqual(audit.sha(Path(filename)) if Path(filename).exists() else None, digest, filename)
        self.assertEqual(self.report['paired_bootstrap']['n_common_tasks'], 20)


if __name__ == '__main__':
    unittest.main(verbosity=2)
