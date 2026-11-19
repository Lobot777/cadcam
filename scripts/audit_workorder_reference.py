"""Offline reference-construction audit; never imports the evaluation gateway."""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import itertools
import json
import os
from pathlib import Path
import random
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SEED = 20261106
FREECAD = Path('E:/FreeCAD_1.1.4-Windows-x86_64-py311/bin/freecadcmd.exe')
SOURCE = ROOT / 'scripts/m5_nl_eval.py'
ARCHIVE = ROOT / 'reports/m5_nl_eval.json'
WORK = ROOT / 'build/revision_reference_audit'
REPORT = ROOT / 'reports/revision_reference_audit.json'


# Tolerance configuration must survive JSON serialization exactly. Rounding it to
# 8 decimals previously turned volume_relative_tolerance=1e-9 into 0.0, which
# silently tightened every Boolean volume check.
EXACT_FLOAT_KEYS = {'volume_abs_tolerance_mm3', 'volume_relative_tolerance',
                    'metric_tolerance', 'volume_abs_mm3', 'volume_relative',
                    'archive_metric'}


def normalized(value, key=None):
    if isinstance(value, float):
        if key in EXACT_FLOAT_KEYS:
            return value
        return round(value, 8)
    if isinstance(value, dict):
        return {k: normalized(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [normalized(v) for v in value]
    return value


def dump(path, data):
    path.write_text(json.dumps(normalized(data), ensure_ascii=False, sort_keys=True,
                               indent=2, allow_nan=False) + '\n', encoding='utf-8')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def load_tasks():
    # Compile only these two local pure functions. No evaluation imports, main,
    # gateway, model client, or generated script is imported/executed.
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef)
             and n.name in ('make_tasks', '_describe')]
    if len(nodes) != 2:
        raise ValueError('Current generator functions not found')
    namespace = {'random': random, 'SEED': SEED}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), namespace)
    return namespace['make_tasks'](20, SEED), namespace['_describe']


def visible_spec(task):
    """Mirror %.0f on every printed numeric field, not Python int truncation."""
    result = {k: copy.deepcopy(task[k]) for k in ('size', 'pockets', 'holes')}
    fmt = lambda v: float('%.0f' % v)
    result['size'] = [fmt(v) for v in result['size']]
    for p in result['pockets']:
        for key in ('cx', 'cy', 'w', 'h', 'd'):
            p[key] = fmt(p[key])
    for h in result['holes']:
        for key in ('x', 'y', 'dia'):
            h[key] = fmt(h[key])
        if not h['through']:
            h['depth'] = fmt(h['depth'])
        # Through-hole depth is unprinted and geometrically ignored.
    return result


def pocket_bounds(p, size):
    return [(max(0., p['cx'] - p['w']/2), min(size[0], p['cx'] + p['w']/2)),
            (max(0., p['cy'] - p['h']/2), min(size[1], p['cy'] + p['h']/2)),
            (max(0., size[2] - p['d']), size[2])]


def rectangular_union_volume(pockets, size):
    """Kernel-free inclusion/exclusion oracle for stock-clipped rectangular cuts."""
    boxes = [pocket_bounds(p, size) for p in pockets]
    total = 0.
    for n in range(1, len(boxes) + 1):
        for group in itertools.combinations(boxes, n):
            volume = 1.
            for axis in range(3):
                volume *= max(0., min(b[axis][1] for b in group)
                              - max(b[axis][0] for b in group))
            total += (-1)**(n+1) * volume
    return total


def prepare():
    tasks, _ = load_tasks()
    archive = json.loads(ARCHIVE.read_text(encoding='utf-8'))
    if archive['seed'] != SEED:
        raise ValueError('Archive seed does not match reconstruction')
    ids = [t['id'] for t in tasks]
    rows = {}
    for route in ('contract', 'script'):
        run = archive['runs']['easy/' + route]
        if sorted(r['id'] for r in run['rows']) != ids:
            raise ValueError('Archive must contain exactly the 20 unique generated IDs')
        rows[route] = {r['id']: r for r in run['rows']}
    files = {str(p): sha(p) for p in (SOURCE, ARCHIVE, Path(__file__),
             ROOT / 'scripts/_workorder_reference_worker.py',
             ROOT / 'tests/test_workorder_reference.py',
             ROOT / 'build/nl_eval/script/NL03/part.py',
             ROOT / 'build/nl_eval/script/NL07/part.py')}
    records = []
    for t in tasks:
        tid = t['id']
        base = ROOT / 'build/nl_eval/contract' / tid
        paths = {'gold': base / 'gold.step',
                 'gold_stock': base / 'gold/box_01.step',
                 'gold_first_pocket': base / 'gold/pocket_02.step',
                 'contract_stock': base / 'run/box_01.step',
                 'contract': base / 'run/export_out.step',
                 'script': ROOT / 'build/nl_eval/script' / tid / 'out.step'}
        for path in paths.values():
            files[str(path)] = sha(path) if path.exists() else None
        latent = {k: t[k] for k in ('size', 'pockets', 'holes')}
        visible = visible_spec(t)
        selected = {route: {k: rows[route][tid].get(k) for k in
                    ('exec_ok', 'iou', 'removed_iou', 'calls', 'errors', 'error', 'tail')}
                    for route in rows}
        for route in rows:
            if not isinstance(selected[route]['exec_ok'], bool):
                raise ValueError('Missing Boolean exec_ok: ' + tid)
        stock_call = selected['contract']['calls'][0]
        if stock_call['tool'] != 'cad.create_box' or [stock_call['args'][k] for k in
                ('length', 'width', 'height')] != t['size']:
            raise ValueError('Archived contract stock call mismatch: ' + tid)
        records.append({'id': tid, 'text': t['text'],
                        'text_sha256': hashlib.sha256(t['text'].encode('utf-8')).hexdigest(),
                        'latent': latent, 'visible': visible,
                        'latent_sha256': fingerprint(latent),
                        'visible_sha256': fingerprint(visible),
                        'generator_calls_sha256': fingerprint(t['calls']),
                        'rectangular_oracle_mm3': rectangular_union_volume(t['pockets'], t['size']),
                        'paths': {k: str(v) for k, v in paths.items()}, 'archive': selected})
    return records, files, {route: archive['runs']['easy/' + route]['summary']
                            for route in rows}


def summarize(records, definition, route):
    scored = [r['measurements'][definition][route] for r in records]
    errors = [r['id'] for r, m in zip(records, scored) if m['status'] == 'audit_error']
    out = {'n': len(scored), 'archived_exec_ok': sum(r['archive'][route]['exec_ok'] for r in records),
           'failed_export_zero_count': sum(m['status'] == 'archived_exec_failed' for m in scored),
           'runtime_error_ids': errors, 'complete': not errors}
    for metric in ('removed_iou', 'whole_iou'):
        vals = [m[metric] for m in scored if m['status'] == 'ok']
        known_sum = sum(m[metric] for m in scored if m['status'] != 'audit_error')
        successes = sum(v >= .9 for v in vals)
        out[metric] = {'all_task_mean': None if errors else known_sum/len(scored),
                       'all_task_mean_bounds': [known_sum/len(scored), (known_sum+len(errors))/len(scored)],
                       'conditional_mean': sum(vals)/len(vals) if vals else None,
                       'conditional_n': len(vals),
                       'conditional_definition': 'Validated measurements among archived successful exports; excludes audit errors.',
                       'ge_0.9_count': None if errors else successes,
                       'ge_0.9_count_bounds': [successes, successes+len(errors)]}
    return out


def bootstrap(records):
    import numpy as np
    rng = np.random.default_rng(SEED)
    indices = rng.integers(0, len(records), size=(10000, len(records)))
    result = {'seed': SEED, 'replicates': 10000, 'n_common_tasks': len(records),
              'interpretation': 'Exploratory paired percentile 95% intervals on these 20 tasks; '
                                'failed exports score zero. No causal superiority claim.', 'deltas': {}}
    def series(definition, route, metric):
        ms = [r['measurements'][definition][route] for r in records]
        lower = np.array([0. if m['status'] == 'audit_error' else m[metric] for m in ms])
        upper = np.array([1. if m['status'] == 'audit_error' else m[metric] for m in ms])
        return lower, upper
    def add(name, a, b):
        low, high = a[0]-b[1], a[1]-b[0]
        if not np.array_equal(low, high):
            result['deltas'][name] = {
                'mean': None, 'mean_bounds': [float(low.mean()), float(high.mean())],
                'bootstrap_95_sensitivity_envelope': [float(np.quantile(low[indices].mean(axis=1), .025)),
                                                     float(np.quantile(high[indices].mean(axis=1), .975))],
                'interpretation': 'Not a point-estimate CI: conservative sensitivity envelope with each unresolved audit score bounded in [0,1]; no imputation asserted.'}
            return
        result['deltas'][name] = {'mean': float(low.mean()),
                                 'percentile_95_ci': np.quantile(low[indices].mean(axis=1), [.025, .975]).tolist()}
    for metric in ('removed_iou', 'whole_iou'):
        for definition in ('latent', 'visible', 'archived_gold'):
            add(definition + '/' + metric + '/contract_minus_script',
                series(definition, 'contract', metric), series(definition, 'script', metric))
        for route in ('contract', 'script'):
            for definition in ('latent', 'visible'):
                add(route + '/' + metric + '/' + definition + '_minus_archived_gold',
                    series(definition, route, metric), series('archived_gold', route, metric))
            add(route + '/' + metric + '/visible_minus_latent',
                series('visible', route, metric), series('latent', route, metric))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freecad', type=Path, default=FREECAD)
    args = parser.parse_args()
    # Only the authorized output directory is created. Its parent already exists.
    if not WORK.parent.is_dir() or not REPORT.parent.is_dir():
        raise ValueError('Output parent missing')
    WORK.mkdir(exist_ok=True)
    records, evidence, archived_summaries = prepare()
    payload = WORK / 'input.json'
    result_path = WORK / 'worker_result.json'
    dump(payload, {'tasks': records, 'volume_abs_tolerance_mm3': 1e-5,
                   'volume_relative_tolerance': 1e-9, 'metric_tolerance': 1e-6})
    result_path.unlink(missing_ok=True)  # Never accept a stale success payload.
    env = dict(os.environ, WORKORDER_AUDIT_INPUT=str(payload),
               WORKORDER_AUDIT_OUTPUT=str(result_path), PYTHONDONTWRITEBYTECODE='1')
    process_error = None
    try:
        process = subprocess.run([str(args.freecad.resolve()), str(ROOT / 'scripts/_workorder_reference_worker.py')],
                                 env=env, cwd=str(WORK), capture_output=True,
                                 timeout=900, encoding='utf-8', errors='replace')
        (WORK / 'freecad.stdout.log').write_text(process.stdout, encoding='utf-8')
        (WORK / 'freecad.stderr.log').write_text(process.stderr, encoding='utf-8')
        if process.returncode != 0:
            process_error = 'FreeCAD exit code %s' % process.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        process_error = str(exc)
    if result_path.exists():
        worker = json.loads(result_path.read_text(encoding='utf-8'))
    else:
        worker = {'complete': False, 'fatal_error': 'FreeCAD did not produce required JSON payload'}
    changed = [p for p, digest in evidence.items()
               if (sha(Path(p)) if Path(p).exists() else None) != digest]
    report = {'schema_version': 1, 'seed': SEED,
              'method': {'reference': 'Direct Part boxes/cylinders, union all cutters before stock subtraction; no nominal full-volume rejection.',
                         'independence': 'Independent reference CONSTRUCTION; same FreeCAD/OCC geometry kernel, not a second kernel.',
                         'boolean_validation': 'Removed union/intersection and stock partitions are checked. Whole intersection/union use De Morgan complement Booleans when stock-contained. Failed attempts retry coplanar-face unification, fuzzy tolerance 1e-7 mm, shared rigid rotations, and General Fuse Boolean cell partitions with source-volume and bounding-stock checks. All rejected attempts are retained. Unresolved comparisons are null, not fabricated scores; initial unchecked results are not used.',
                         'prompt_provenance': 'Raw archived prompt unavailable. Text reconstructed from CURRENT generator with seed 20261106; not claimed to be the preserved original prompt.',
                         'visible_definition': "float('%.0f' % value) for every printed dimension and coordinate; unprinted through-hole depth ignored.",
                         'latent_definition': 'Full-precision size+pockets+holes directly from current generator.',
                         'hidden_coordinate_drift': 'Visible-versus-latent difference is specification information loss, not attributed to model error.',
                         'pairing': 'Require seed/unique IDs/contract stock calls, geometric gold stock and first-pocket checks, run stock checks, and reproduction of the original raw whole/removed-IoU operations on archived STEP pairs. Raw reproduction is only a pairing guard, never a validated score; corrected-minus-legacy differences are retained. These are consistency checks, not proof of original prompt identity.',
                         'failures': 'Archived exec_ok=false always scores zero; no generated scripts executed. Audit/runtime errors are null and separate, never silently zero.',
                         'tolerances': {'volume_abs_mm3': 1e-5, 'volume_relative': 1e-9, 'archive_metric': 1e-6},
                         'determinism': 'Fixed seeds; numeric JSON rounded to 8 decimals; no timestamps or timings in report. Runtime logs are not part of deterministic evidence.'},
              'archived_summaries': archived_summaries,
              'tasks': records, 'worker': worker, 'process_error': process_error,
              'source_or_archive_changed_during_audit': changed}
    if worker.get('tasks'):
        by_id = {r['id']: r for r in worker['tasks']}
        if set(by_id) != {r['id'] for r in records}:
            raise ValueError('Worker task IDs do not match input')
        for record in records:
            record.update(by_id[record['id']])
        report['worker'] = {k: v for k, v in worker.items() if k != 'tasks'}
        report['summaries'] = {d: {route: summarize(records, d, route) for route in ('contract', 'script')}
                               for d in ('latent', 'visible', 'archived_gold')}
        report['paired_bootstrap'] = bootstrap(records)
        report['gold_disagreement_ids'] = {d: [r['id'] for r in records
                    if r.get('gold_comparison', {}).get(d, {}).get('disagrees', False)]
                    for d in ('latent', 'visible')}
        report['hidden_coordinate_drift_ids'] = [r['id'] for r in records
                    if r.get('hidden_coordinate_drift', {}).get('disagrees', False)]
        drifts = [r['hidden_coordinate_drift'] for r in records if 'hidden_coordinate_drift' in r]
        report['hidden_coordinate_drift_summary'] = {
            'n': len(drifts), 'changed_task_count': len(report['hidden_coordinate_drift_ids']),
            'mean_removed_iou': sum(d['removed_iou'] for d in drifts)/len(drifts) if drifts else None,
            'mean_whole_iou': sum(d['whole_iou'] for d in drifts)/len(drifts) if drifts else None,
            'attribution': 'Text-formatting information loss, not model error.'}
    report['audit_completed'] = bool(len(worker.get('tasks', [])) == 20 and not process_error and not changed)
    report['reference_complete'] = bool(report['audit_completed'] and all(
        len(r.get('reference_validation', {})) == 2 and r.get('rectangular_oracle_error_mm3', 1) < 1e-5
        for r in records))
    report['archive_pairing_complete'] = bool(report['audit_completed'] and all(
        all(key in r.get('pairing_checks', {}) and not r['pairing_checks'][key]['disagrees']
            for key in ('gold_stock', 'gold_first_pocket', 'contract_stock')) and
        all(not r['archive'][route]['exec_ok'] or
            (len(r['pairing_checks'].get(route + '_reported_metric_deltas', {})) == 2 and
             max(r['pairing_checks'][route + '_reported_metric_deltas'].values()) <= 1e-6)
            for route in ('contract', 'script')) for r in records))
    report['complete'] = bool(worker.get('complete') and not process_error and not changed)
    evidence[str(payload)] = sha(payload)
    if result_path.exists():
        evidence[str(result_path)] = sha(result_path)
    report['sha256_evidence'] = evidence
    dump(REPORT, report)
    print(json.dumps({'report': str(REPORT), 'complete': report['complete'],
                      'summaries': report.get('summaries'),
                      'gold_disagreement_ids': report.get('gold_disagreement_ids')}, indent=2))
    return 0 if report['complete'] else 1


if __name__ == '__main__':
    sys.exit(main())
