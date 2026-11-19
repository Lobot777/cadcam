"""Unit and archive checks for the part-level external audit statistics."""
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "m6_external_stats", ROOT / "scripts" / "m6_external_stats.py")
stats = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stats)


class McNemarTests(unittest.TestCase):
    def test_no_discordant_pairs(self):
        self.assertEqual(stats._mcnemar_exact(0, 0), 1.0)

    def test_symmetric_ten_zero(self):
        # Two-sided exact binomial for 10 vs 0 is 2 * 2^-10.
        self.assertAlmostEqual(stats._mcnemar_exact(10, 0), 2.0 / 1024.0, places=12)
        self.assertAlmostEqual(stats._mcnemar_exact(0, 10), 2.0 / 1024.0, places=12)

    def test_balanced_is_one(self):
        self.assertEqual(stats._mcnemar_exact(5, 5), 1.0)


@unittest.skipUnless((ROOT / "reports" / "m6_external_stats.json").exists(),
                     "Run scripts/m6_external_stats.py first")
class ArchivedStatisticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rep = json.loads((ROOT / "reports" / "m6_external_stats.json").read_text(encoding="utf-8"))

    def test_headline_counts_and_intervals(self):
        self.assertEqual(self.rep["n_parts"], 130)
        self.assertEqual(self.rep["depth_summary"]["plain_over_0_5"], 94)
        self.assertEqual(self.rep["depth_summary"]["guard_over_0_5"], 8)
        row = next(r for r in self.rep["threshold_sensitivity"] if r["threshold_mm"] == 0.5)
        self.assertEqual(row["plain_parts"], 94)
        self.assertEqual(row["guard_parts"], 8)
        lo, hi = row["plain_wilson95"]
        self.assertLess(lo, 94 / 130)
        self.assertGreater(hi, 94 / 130)

    def test_paired_effect_never_worsens(self):
        paired = self.rep["paired_guard_effect"]
        self.assertEqual(paired["guard_only_bad"], 0)
        self.assertEqual(paired["plain_only_bad"], 86)
        self.assertEqual(paired["plain_only_bad"] + paired["guard_only_bad"]
                         + paired["both_bad"] + paired["neither_bad"], 130)
        self.assertLess(paired["exact_two_sided_p"], 1e-25)

    def test_threshold_plateau(self):
        rows = {r["threshold_mm"]: r for r in self.rep["threshold_sensitivity"]}
        for th in (0.5, 0.75, 1.0, 2.0, 5.0):
            self.assertEqual(rows[th]["plain_parts"], 94)
        self.assertEqual(rows[0.25]["plain_parts"], 102)
        self.assertEqual(rows[20.0]["plain_parts"], 49)

    def test_coverage_association_is_negative(self):
        self.assertLess(self.rep["coverage_analysis"]["spearman_coverage_vs_violation"], -0.3)
        bands = {b["band"]: b for b in self.rep["coverage_analysis"]["bands"]}
        self.assertGreater(bands["<96%"]["plain_rate"], bands[">=99%"]["plain_rate"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
