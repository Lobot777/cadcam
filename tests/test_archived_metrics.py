"""Standard-library tests for the standalone archive audit; no model/CAD imports."""
import copy
import hashlib
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("audit_archived_metrics", ROOT / "scripts/audit_archived_metrics.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def row(ident, score=1.0, executed=True, **extra):
    return dict(id=ident, exec_ok=executed, iou=score, removed_iou=score, **extra)


def part(n, g, deep, maximum, geometry_max=999.0):
    return {"geometry": {"max_abs_diff_mm": geometry_max}, "B2_masked": {"ocl_oracle": {
        "sampled_tool_centers": n, "gouge_points": g,
        "deep_gt_0_5mm": deep, "shallow_le_0_5mm": g - deep,
        "worst_gouge_depth_mm": maximum, "touch_eps_mm": 0.02, "sample_step_mm": 0.5,
    }}}


class RowTests(unittest.TestCase):
    def test_conditional_failure_zero_semantics_and_recorded_errors_distinct(self):
        rows = [row("A", 0.95, errors=[{"code": "ToolError"}], n_calls=2, legal=2),
                row("B", 0.25, n_calls=1, legal=1), row("C", None, False, n_calls=0, legal=0)]
        before = copy.deepcopy(rows)
        result = audit.summarize_rows(rows)
        self.assertEqual(rows, before)
        self.assertEqual((result["execution"], result["failures"], result["valid_scored"]), (2, 1, 2))
        self.assertEqual(result["semantic_removed_iou_ge_0_9"], 1)
        self.assertAlmostEqual(result["removed_iou"]["conditional_mean_valid_outputs"], 0.6)
        self.assertAlmostEqual(result["removed_iou"]["all_task_mean_failure_zero"], 0.4)
        self.assertEqual(result["recorded_errors"]["semantic_tasks"], 1)
        self.assertEqual(result["name_required_argument_legality_only"]["rate"], 1)
        self.assertIsNone(result["name_required_argument_legality_only"]["successful_commits"])

    def test_executed_missing_and_nonfinite_scores_raise(self):
        for metric in audit.METRICS:
            for bad in (None, float("nan"), float("inf"), -0.1, 1.1, True, "0.9"):
                with self.subTest(metric=metric, bad=bad):
                    r = row("A")
                    r[metric] = bad
                    with self.assertRaises(ValueError):
                        audit.summarize_rows([r])
            r = row("A")
            del r[metric]
            with self.assertRaises(ValueError):
                audit.bootstrap_difference([r], [row("A")])

    def test_failed_scores_not_resurrected_and_zero_not_missing(self):
        result = audit.summarize_rows([row("A", 0), row("B", 1, False)])
        self.assertEqual(result["removed_iou"]["conditional_mean_valid_outputs"], 0)
        self.assertEqual(result["removed_iou"]["all_task_mean_failure_zero"], 0)
        self.assertEqual(result["removed_iou"]["failed_rows_with_score_ignored"], 1)
        result = audit.summarize_rows([row("A", None, False)])
        self.assertIsNone(result["removed_iou"]["conditional_mean_valid_outputs"])
        self.assertEqual(result["removed_iou"]["all_task_mean_failure_zero"], 0)

    def test_invalid_counts_flags_ids_and_empty_rows(self):
        bad_rows = [[], [row("A"), row("A")], [row("A", n_calls=1, legal=2)],
                    [row("A", n_calls=2, legal=2, calls=[])], [dict(id="A", iou=1, removed_iou=1)],
                    [row("A", errors="not an array")]]
        for rows in bad_rows:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                audit.summarize_rows(rows)

    def test_explicit_tail_errors_not_any_console_output(self):
        result = audit.summarize_rows([
            row("A", tail="Write Done"), row("B", tail="FreeCAD banner"),
            row("C", tail="Exception while processing file: failed"),
            row("D", None, False, error="HTTP 400: invalid"),
        ])
        self.assertEqual(result["recorded_errors"]["tasks"], 2)
        self.assertEqual(result["recorded_errors"]["explicit_error_tail_tasks"], 1)
        self.assertEqual(result["recorded_errors"]["structured_array_present_tasks"], 0)


class PairTests(unittest.TestCase):
    def test_single_item_known_interval(self):
        result = audit.bootstrap_difference([row("A", 0.75)], [row("A", 0.25)], resamples=50)
        self.assertEqual(result["mean_difference"], 0.5)
        self.assertEqual(result["percentile_95_interval"], [0.5, 0.5])

    def test_small_pairing_is_deterministic_and_order_independent(self):
        a, b = [row("B", 0), row("A", 1)], [row("A", 0), row("B", 1)]
        result = audit.bootstrap_difference(a, b, seed=7, resamples=100)
        self.assertEqual(result, audit.bootstrap_difference(a[::-1], b[::-1], seed=7, resamples=100))
        self.assertEqual(result["item_ids"], ["A", "B"])
        self.assertEqual(result["mean_difference"], 0)
        self.assertEqual(result["percentile_95_interval"], [-1, 1])
        self.assertIn("EXPLORATORY", result["inference"])

    def test_raw_pairing_rejects_mismatch_before_filtering(self):
        for left, right in (([row("A")], [row("B")]),
                            ([row("A"), row("A")], [row("A")]),
                            ([row("A", tag="x")], [row("A", tag="y")]),
                            ([row("A"), row("B", None, False)], [row("A")])):
            with self.assertRaises(ValueError):
                audit.bootstrap_difference(left, right, conditional=True, resamples=10)
        with self.assertRaises(ValueError):
            audit.index_rows([row("NL01")], expected=[f"NL{i:02d}" for i in range(1, 21)])
        with self.assertRaises(ValueError):
            audit.bootstrap_difference([row("A")], [row("A")], resamples=0)

    def test_common_valid_is_not_separately_conditional(self):
        a = [row("A", 0.8), row("B", 1)]
        b = [row("A", 0.2), row("B", None, False)]
        common = audit.bootstrap_difference(a, b, conditional=True, resamples=20)
        all_tasks = audit.bootstrap_difference(a, b, resamples=20)
        self.assertEqual((common["raw_pairs"], common["n"]), (2, 1))
        self.assertAlmostEqual(common["mean_difference"], 0.6)
        self.assertAlmostEqual(all_tasks["mean_difference"], 0.8)
        empty = audit.bootstrap_difference([row("A", None, False)], [row("A")],
                                           conditional=True, resamples=20)
        self.assertEqual(empty["n"], 0)
        self.assertIsNone(empty["mean_difference"])
        self.assertIsNone(empty["percentile_95_interval"])


class CutterTests(unittest.TestCase):
    def test_equal_weight_vs_pooled_and_separate_geometry(self):
        parts = [part(10, 5, 1, 2), part(100, 1, 0, 0.2)]
        result = audit.summarize_cutter(parts, "B2_masked", True)
        self.assertEqual(result["parts_any_finite_cutter_violation"], 2)
        self.assertEqual(result["parts_deep_gt_0_5mm"], 1)
        self.assertEqual(result["max_finite_cutter_depth_mm"], 2)  # NOT geometry's 999.
        self.assertAlmostEqual(result["equal_part_mean_gouge_rate_pct"], 25.5)
        self.assertAlmostEqual(result["pooled_point_gouge_rate_pct"], 600 / 110)
        self.assertEqual(result["deep_points"], 1)

    def test_anchor_deep_point_counts_are_unknown_not_zero(self):
        p = part(10, 5, 1, 2)
        del p["B2_masked"]["ocl_oracle"]["deep_gt_0_5mm"]
        del p["B2_masked"]["ocl_oracle"]["shallow_le_0_5mm"]
        result = audit.summarize_cutter([p], "B2_masked", False)
        self.assertEqual(result["parts_deep_gt_0_5mm"], 1)
        self.assertIsNone(result["deep_points"])
        self.assertIsNone(result["pooled_point_deep_rate_pct"])
        with self.assertRaises(ValueError):
            audit.summarize_cutter([p], "B2_masked", True)

    def test_invalid_or_missing_cutter_measurements_raise(self):
        for key, value in (("sampled_tool_centers", 0), ("gouge_points", 11),
                           ("deep_gt_0_5mm", 8), ("worst_gouge_depth_mm", None),
                           ("worst_gouge_depth_mm", 0.5)):
            p = part(10, 5, 1, 2)
            p["B2_masked"]["ocl_oracle"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                audit.summarize_cutter([p], "B2_masked", True)


class ArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = audit.audit(ROOT)

    def test_provenance_and_scope(self):
        meta = self.report["metadata"]
        for name, digest in meta["source_sha256"].items():
            self.assertEqual(digest, hashlib.sha256((ROOT / name).read_bytes()).hexdigest())
        self.assertEqual((meta["original_summarize"]["start_line"], meta["original_summarize"]["end_line"]), (422, 439))
        self.assertEqual(set(self.report["nl_runs"]), set(audit.RUNS))
        self.assertIn("NOT independent geometric truth", meta["limitations"][0])
        self.assertNotIn("generated_at", audit.encode_report(self.report).decode())

    def test_easy_archive_execution_does_not_mean_no_error(self):
        contract = self.report["nl_runs"]["easy/contract"]
        script = self.report["nl_runs"]["easy/script"]
        self.assertEqual((contract["execution"], contract["semantic_removed_iou_ge_0_9"]), (20, 20))
        self.assertEqual(contract["recorded_errors"]["tasks"], 13)
        self.assertEqual(contract["recorded_errors"]["structured_entries"], 19)
        self.assertEqual((script["execution"], script["semantic_removed_iou_ge_0_9"]), (19, 2))
        for run in self.report["nl_runs"].values():
            self.assertTrue(all(c["matches"] for c in run["archived_summary_checks"].values()))

    def test_cross_model_failure_and_unavailability_retained(self):
        cross = self.report["cross_model"]
        self.assertEqual(len(cross["available_archived_runs"]), 15)
        llama = cross["available_archived_runs"]["llama3.1:8b"]
        self.assertEqual((llama["execution"], llama["valid_scored"], llama["failures"]), (29, 29, 1))
        hy = cross["unavailable_archived_runs"]["hunyuan-2.0-thinking"]
        self.assertEqual((hy["execution"], hy["failures"]), (0, 30))
        self.assertEqual(hy["recorded_errors"]["request_error_tasks"], 30)
        self.assertIsNone(hy["name_required_argument_legality_only"]["rate"])

    def test_ocl_scopes_and_no_cross_experiment_pairs(self):
        external = self.report["ocl_external_130"]
        self.assertEqual(external["parts"], 130)
        for route, expected in (("B2_masked", (128, 94, 71.104)),
                                ("B2_half_cell_guard", (19, 8, 42.288))):
            cutter = external["finite_cutter_oracle"][route]
            self.assertEqual((cutter["parts_any_finite_cutter_violation"],
                              cutter["parts_deep_gt_0_5mm"], cutter["max_finite_cutter_depth_mm"]), expected)
        self.assertEqual(external["geometry_point_probe"]["max_abs_diff_mm"], 71.104)
        anchor = self.report["ocl_anchor_10"]
        self.assertEqual(anchor["parts"], 10)
        self.assertIsNone(anchor["finite_cutter_oracle"]["B2_masked"]["deep_points"])
        for comparison in self.report["paired_exploratory"]:
            self.assertIn(comparison["left"], audit.RUNS)
            self.assertIn(comparison["right"], audit.RUNS)
            self.assertEqual(comparison["estimates"][0]["raw_pairs"],
                             20 if comparison["left"].startswith("easy/") else 30)


if __name__ == "__main__":
    unittest.main()
