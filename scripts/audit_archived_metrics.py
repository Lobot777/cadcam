"""Audit archived metrics offline without changing or executing the inputs."""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[1]
SEED = 20261106
RESAMPLES = 10000
RUNS = ("easy/contract", "easy/script", "hard/contractr1",
        "hard/contractr2", "hard/scriptr1")
METRICS = ("iou", "removed_iou")
TAIL_MARKERS = ("Exception while processing file:", "Traceback (most recent call last)",
                "TIMEOUT", "空实体", "无效实体")
SOURCES = (
    "scripts/m5_nl_eval.py", "scripts/m5_model_compare.py",
    "scripts/m6_ocl_external.py", "scripts/m6_opencamlib.py",
    "reports/m5_nl_eval.json", "reports/m5_model_compare.json",
    "reports/m5_cloud_compare.json", "reports/m6_ocl_external.json",
    "reports/m6_ocl.json", "scripts/audit_archived_metrics.py",
    "tests/test_archived_metrics.py",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value, label):
    require(type(value) in (int, float) and math.isfinite(value),
            f"{label}: missing or nonfinite measurement")
    return value


def count(value, label):
    require(type(value) is int and value >= 0, f"{label}: invalid count")
    return value


def mean(values):
    return math.fsum(values) / len(values) if values else None


def index_rows(rows, id_key="id", expected=None):
    require(isinstance(rows, list) and bool(rows), "empty or invalid row collection")
    indexed = {}
    for row in rows:
        ident = row.get(id_key)
        require(type(ident) in (str, int) and ident != "", "missing task/part identity")
        require(ident not in indexed, f"duplicate identity: {ident}")
        indexed[ident] = row
    if expected is not None:
        require(set(indexed) == set(expected), "raw task identities differ from expected set")
    return indexed


def pair_rows(left, right):
    a, b = index_rows(left), index_rows(right)
    require(set(a) == set(b), "paired raw task sets differ (no intersection-only pairing)")
    for ident in a:
        for field in ("tag", "text", "prompt"):
            if field in a[ident] and field in b[ident]:
                require(a[ident][field] == b[ident][field],
                        f"paired task {ident}: conflicting {field}")
    return [(a[k], b[k]) for k in sorted(a)]


def validate_rows(rows):
    index_rows(rows)
    for row in rows:
        require(type(row.get("exec_ok")) is bool, f"{row['id']}: missing execution flag")
        for metric in METRICS:
            value = row.get(metric)
            if row["exec_ok"] or value is not None:
                finite(value, f"{row['id']}/{metric}")
                # Preserve tiny archived floating-point overshoots, not new clipping.
                require(-1e-9 <= value <= 1 + 1e-9, f"{row['id']}/{metric}: outside [0,1]")
        require(isinstance(row.get("errors", []), list), "errors must be an array when present")
        require(all(isinstance(e, dict) for e in row.get("errors", [])),
                "recorded errors must be objects")
        for field in ("error", "tail"):
            require(row.get(field) is None or isinstance(row[field], str),
                    f"{field} must be text or null")


def error_flags(row):
    return (bool(row.get("errors")), bool(row.get("error")),
            any(marker in (row.get("tail") or "") for marker in TAIL_MARKERS))


def summarize_rows(rows):
    validate_rows(rows)
    rows = sorted(rows, key=lambda r: r["id"])
    valid = [r for r in rows if r["exec_ok"]]
    flagged = [r for r in rows if any(error_flags(r))]
    semantic = [r for r in valid if r["removed_iou"] >= 0.9]
    out = {
        "n": len(rows), "execution": len(valid), "failures": len(rows) - len(valid),
        "execution_rate": len(valid) / len(rows), "valid_scored": len(valid),
        "semantic_removed_iou_ge_0_9": len(semantic),
        "semantic_rate_all_tasks": len(semantic) / len(rows),
        "semantic_rate_given_execution": len(semantic) / len(valid) if valid else None,
        "recorded_errors": {
            "tasks": len(flagged), "task_ids": [r["id"] for r in flagged],
            "executed_tasks": sum(r["exec_ok"] for r in flagged),
            "semantic_tasks": sum(r in semantic for r in flagged),
            "structured_entries": sum(len(r.get("errors", [])) for r in rows),
            "structured_array_present_tasks": sum("errors" in r for r in rows),
            "structured_arrays_at_archive_cap_3": sum(len(r.get("errors", [])) == 3 for r in rows),
            "structured_codes": dict(sorted(Counter(
                e.get("code", "UNSPECIFIED") for r in rows for e in r.get("errors", [])).items())),
            "request_error_tasks": sum(bool(r.get("error")) for r in rows),
            "explicit_error_tail_tasks": sum(error_flags(r)[2] for r in rows),
            "tail_present_tasks": sum("tail" in r for r in rows),
        },
    }
    for metric in METRICS:
        values = [r[metric] for r in valid]
        out[metric] = {
            "valid_scored": len(values), "conditional_mean_valid_outputs": mean(values),
            "all_task_mean_failure_zero": math.fsum(values) / len(rows),
            "failed_rows_with_score_ignored": sum(not r["exec_ok"] and r.get(metric) is not None
                                                  for r in rows),
        }
    if any("n_calls" in r or "legal" in r for r in rows):
        for r in rows:
            n = count(r.get("n_calls"), "n_calls")
            legal = count(r.get("legal"), "legal")
            require(legal <= n, "legal count exceeds calls")
            if "calls" in r:
                require(len(r["calls"]) == n, "raw calls disagree with n_calls")
        calls = sum(r["n_calls"] for r in rows)
        legal = sum(r["legal"] for r in rows)
        out["name_required_argument_legality_only"] = {
            "calls": calls, "legal": legal, "rate": legal / calls if calls else None,
            "successful_commits": None,
            "meaning": "Known tool name and presence of required arguments, NOT successful commits.",
        }
    return out


def quantile(sorted_values, probability):
    pos = (len(sorted_values) - 1) * probability
    lower = int(pos)
    upper = min(lower + 1, len(sorted_values) - 1)
    return sorted_values[lower] + (pos - lower) * (sorted_values[upper] - sorted_values[lower])


def bootstrap_difference(left, right, metric="removed_iou", conditional=False,
                         seed=SEED, resamples=RESAMPLES):
    require(metric in METRICS, "unsupported paired metric")
    require(type(resamples) is int and resamples > 0, "resamples must be positive")
    validate_rows(left)
    validate_rows(right)
    pairs = pair_rows(left, right)  # Assert full sets BEFORE conditional filtering.
    selected = [(a, b) for a, b in pairs if not conditional or (a["exec_ok"] and b["exec_ok"])]
    diffs = [(a[metric] if a["exec_ok"] else 0.0) -
             (b[metric] if b["exec_ok"] else 0.0) for a, b in selected]
    rng = random.Random(seed)
    samples = sorted(math.fsum(diffs[rng.randrange(len(diffs))] for _ in diffs) / len(diffs)
                     for _ in range(resamples)) if diffs else []
    return {
        "metric": metric, "direction": "left minus right", "raw_pairs": len(pairs),
        "n": len(diffs), "item_ids": [a["id"] for a, _ in selected],
        "estimand": "common valid outputs only" if conditional else "all tasks; execution failures zero",
        "mean_difference": mean(diffs),
        "percentile_95_interval": [quantile(samples, 0.025), quantile(samples, 0.975)] if samples else None,
        "seed": seed, "resamples": resamples,
        "inference": "EXPLORATORY item bootstrap conditional on archived answers and common oracle; "
                     "not model repeated-sampling inference or independent-truth validation.",
    }


def summarize_cutter(parts, route, require_deep_counts):
    records = [p[route]["ocl_oracle"] for p in parts]
    for rec in records:
        n = count(rec.get("sampled_tool_centers"), "sampled_tool_centers")
        g = count(rec.get("gouge_points"), "gouge_points")
        require(n > 0 and g <= n, "invalid sampled-point denominator/count")
        require(finite(rec.get("worst_gouge_depth_mm"), "cutter depth") >= 0, "negative depth")
        if require_deep_counts or "deep_gt_0_5mm" in rec:
            deep = count(rec.get("deep_gt_0_5mm"), "deep_gt_0_5mm")
            shallow = count(rec.get("shallow_le_0_5mm"), "shallow_le_0_5mm")
            require(deep <= g and deep + shallow == g, "inconsistent deep/shallow counts")
            require((deep > 0) == (rec["worst_gouge_depth_mm"] > 0.5),
                    "deep count conflicts with recorded maximum")
    npoints = sum(r["sampled_tool_centers"] for r in records)
    gouges = sum(r["gouge_points"] for r in records)
    has_deep = all("deep_gt_0_5mm" in r for r in records)
    depths = [r["worst_gouge_depth_mm"] for r in records]
    return {
        "parts": len(parts), "parts_any_finite_cutter_violation": sum(r["gouge_points"] > 0 for r in records),
        "parts_deep_gt_0_5mm": sum(d > 0.5 for d in depths),
        "part_deep_basis": "recorded deep counts, checked against maxima" if has_deep else
                           "recorded rounded cutter maxima > 0.5; deep point counts not archived",
        "max_finite_cutter_depth_mm": max(depths),
        "sampled_tool_centers": npoints, "gouge_points": gouges,
        "equal_part_mean_gouge_rate_pct": 100 * mean([r["gouge_points"] / r["sampled_tool_centers"]
                                                       for r in records]),
        "pooled_point_gouge_rate_pct": 100 * gouges / npoints,
        "deep_points": sum(r["deep_gt_0_5mm"] for r in records) if has_deep else None,
        "equal_part_mean_deep_rate_pct": 100 * mean([r["deep_gt_0_5mm"] / r["sampled_tool_centers"]
                                                      for r in records]) if has_deep else None,
        "pooled_point_deep_rate_pct": 100 * sum(r["deep_gt_0_5mm"] for r in records) / npoints if has_deep else None,
        "touch_eps_mm": sorted({finite(r.get("touch_eps_mm"), "touch_eps_mm") for r in records}),
        "sample_step_mm": sorted({finite(r.get("sample_step_mm"), "sample_step_mm") for r in records}),
    }


def summarize_ocl(report, external):
    parts = report["parts"]
    ids = index_rows(parts, "id" if external else "part")
    require(len(parts) == (130 if external else 10), "unexpected OCL part set")
    routes = ["B2_masked", "B2_half_cell_guard", "B3_no_inflate"]
    if not external:
        routes.append("OCL_toolpath")
    for p in parts:
        require("geometry" in p and all(route in p for route in routes),
                "missing geometry or route: cannot silently drop an unevaluated part")
    geom = [p["geometry"] for p in parts]
    geometry = {
        "meaning": "Point-probe heightmap comparison ONLY; not finite-cutter gouge depth.",
        "max_abs_diff_mm": max(finite(g.get("max_abs_diff_mm"), "geometry maximum") for g in geom),
        "columns_over_0_5mm": sum(count(g.get("columns_over_0_5mm"), "geometry columns") for g in geom),
        "compared_columns": sum(count(g.get("compared_columns"), "compared_columns") for g in geom),
    }
    cutters = {r: summarize_cutter(parts, r, external) for r in routes}
    # Surface mislabeled/stale archived summaries without using them to calculate metrics.
    comparisons = {}
    for summary_key, route in (("B2_worst_gouge_mm", "B2_masked"),
                               ("B3_worst_gouge_mm", "B3_no_inflate"),
                               ("guard_worst_gouge_mm", "B2_half_cell_guard")):
        if summary_key in report["summary"]:
            recorded = report["summary"][summary_key]
            raw = cutters[route]["max_finite_cutter_depth_mm"]
            comparisons[summary_key] = {"recorded_summary": recorded, "raw_nested_oracle": raw,
                                        "matches": recorded == raw}
    return {"parts": len(parts), "part_identity_sha256": hashlib.sha256(
                json.dumps(sorted(ids), separators=(",", ":")).encode()).hexdigest(),
            "all_routes_same_raw_parts": True, "geometry_point_probe": geometry,
            "finite_cutter_oracle": cutters, "archived_summary_checks": comparisons}


def audit(root=ROOT):
    root = Path(root)
    blobs = {name: (root / name).read_bytes() for name in SOURCES}
    def load(name):
        return json.loads(blobs["reports/" + name].decode("utf-8"))

    source = blobs["scripts/m5_nl_eval.py"].decode("utf-8")
    summary_node = next(n for n in ast.parse(source).body
                        if isinstance(n, ast.FunctionDef) and n.name == "summarize")
    runs = load("m5_nl_eval.json")["runs"]
    nl = {}
    for key in RUNS:
        rows = runs[key]["rows"]
        expected = [f"NL{i:02d}" for i in range(1, 21)] if key.startswith("easy/") else [f"H{i:02d}" for i in range(1, 31)]
        index_rows(rows, expected=expected)
        result = summarize_rows(rows)
        old = runs[key]["summary"]
        recomputed = {
            "n": result["n"], "exec_ok": result["execution"],
            "removed_iou_ge_0.9": result["semantic_removed_iou_ge_0_9"],
            "mean_iou": round(result["iou"]["conditional_mean_valid_outputs"], 3)
                        if result["valid_scored"] else None,
            "mean_removed_iou": round(result["removed_iou"]["conditional_mean_valid_outputs"], 3)
                                if result["valid_scored"] else None,
        }
        result["archived_summary_checks"] = {k: {"recorded": old.get(k), "recomputed": v,
                                                  "matches": old.get(k) == v} for k, v in recomputed.items()}
        result["raw_task_ids"] = sorted(expected)
        nl[key] = result
    paired = []
    for left, right in (("easy/contract", "easy/script"),
                        ("hard/contractr1", "hard/scriptr1"),
                        ("hard/contractr2", "hard/scriptr1"),
                        ("hard/contractr1", "hard/contractr2")):
        paired.append({"left": left, "right": right, "estimates": [
            bootstrap_difference(runs[left]["rows"], runs[right]["rows"], metric, conditional)
            for metric in METRICS for conditional in (False, True)]})
    cross = {"available_archived_runs": {}, "unavailable_archived_runs": {}}
    expected = [f"H{i:02d}" for i in range(1, 31)]
    for filename in ("m5_model_compare.json", "m5_cloud_compare.json"):
        report = load(filename)
        for model, entry in report["models"].items():
            rows = entry["orders"]["rows"]
            index_rows(rows, expected=expected)
            pair_rows(rows, runs["hard/contractr1"]["rows"])
            result = summarize_rows(rows)
            unavailable = all(not r["exec_ok"] and r.get("n_calls") == 0 and
                              (r.get("error") or "").startswith("HTTP 400:") for r in rows)
            result.update(source="reports/" + filename, raw_task_set_checked="H01..H30",
                          status="unavailable_http400_only" if unavailable else "available_archived_run")
            if unavailable:
                result["interpretation"] = "30 recorded request failures, not a model capability score; retained separately."
            dest = cross["unavailable_archived_runs" if unavailable else "available_archived_runs"]
            require(model not in cross["available_archived_runs"] and model not in cross["unavailable_archived_runs"],
                    "duplicate model across archives")
            dest[model] = result
    return {
        "metadata": {
            "schema_version": 1, "offline_archive_only": True,
            "limitations": [
                "WARNING: Gold references share the contract execution/failure path. Agreement can reward the same failed feature; archived scores are NOT independent geometric truth.",
                "Semantic >=0.9 means removed-IoU agreement with that archived oracle, NOT independently verified task correctness.",
                "Execution is the recorded output flag, NOT all features committed. Known names/required arguments do NOT imply successful commits.",
                "Recorded error arrays/messages/tails may be truncated; counts are recorded lower bounds, not complete error incidence. Cross-model rows omit commit-error arrays.",
                "EXPLORATORY bootstrap resamples paired items, conditional on fixed archived answers and common oracle; no model repeated-sampling inference, no multiplicity correction, no causal claim.",
                "Pairing asserts unique raw IDs, exact full task sets, and shared available tags/text. Archived rows do not establish identical prompt bytes or gold artifact hashes.",
                "Finite-cutter results are sampled archived oracle checks, not continuous-path safety certification; pooled points are not independent experimental units.",
                "10 G-code anchor parts and 20 NL tasks are distinct experiments and are NEVER paired. The obsolete 8-item hard study is not used.",
                "No models, services, CAD recomputation, network, or installations. No missing executed measurement is imputed. Failed executions alone receive zero in all-task scores.",
            ],
            "score_definitions": {"conditional": "mean over exec_ok rows with finite scores (missing score raises)",
                                  "all_task": "sum of executed scores / all tasks; execution failures contribute zero",
                                  "paired_conditional": "only items executed in BOTH routes; differs from separately conditional means"},
            "recorded_error_tail_markers": list(TAIL_MARKERS),
            "original_summarize": {"path": "scripts/m5_nl_eval.py", "start_line": summary_node.lineno,
                                   "end_line": summary_node.end_lineno,
                                   "sha256": hashlib.sha256(ast.get_source_segment(source, summary_node).encode()).hexdigest(),
                                   "finding": "Original means condition on execution and omit missing measurements; threshold counts are not execution counts."},
            "source_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(blobs.items())},
            "excluded": ["cross-model repair subexperiments", "obsolete 8-item study", "unpaired G-code-versus-NL comparisons"],
        },
        "nl_runs": nl, "paired_exploratory": paired, "cross_model": cross,
        "ocl_external_130": summarize_ocl(load("m6_ocl_external.json"), True),
        "ocl_anchor_10": summarize_ocl(load("m6_ocl.json"), False),
    }


def encode_report(report):
    return (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/revision_archive_audit.json")
    args = parser.parse_args()
    output = args.output.resolve()
    require(output.parent.is_dir(), "output parent directory must already exist")
    require(output == ROOT / "reports/revision_archive_audit.json",
            "this audit owns only reports/revision_archive_audit.json")
    data = encode_report(audit())
    output.write_bytes(data)
    print(f"Wrote {output} ({len(data)} bytes); SHA256 {hashlib.sha256(data).hexdigest()}")


if __name__ == "__main__":
    main()
