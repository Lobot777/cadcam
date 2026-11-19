"""FreeCAD-only Boolean worker. Never loads or executes archived Python scripts."""
import json
import os
from pathlib import Path
import sys
import traceback

sys.dont_write_bytecode = True


def normalized(value):
    if isinstance(value, float):
        return round(value, 8)
    if isinstance(value, dict):
        return {k: normalized(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalized(v) for v in value]
    return value


def run(payload):
    import FreeCAD as App
    import Part

    abs_tol = payload['volume_abs_tolerance_mm3']
    rel_tol = payload['volume_relative_tolerance']
    metric_tol = payload['metric_tolerance']
    errors = []

    def tolerance(volume):
        return max(abs_tol, rel_tol * volume)

    def check(shape, label, single=False):
        if shape.isNull() or not shape.isValid() or shape.Volume <= abs_tol or not shape.Solids:
            raise ValueError(label + ': invalid or empty solid')
        if single and len(shape.Solids) != 1:
            raise ValueError(label + ': expected one solid')
        return {'valid': True, 'solid_count': len(shape.Solids), 'volume_mm3': shape.Volume}

    def pocket(p, height):
        return Part.makeBox(p['w'], p['h'], p['d'],
                            App.Vector(p['cx'] - p['w']/2, p['cy'] - p['h']/2, height-p['d']))

    def union(cutters):
        if not cutters:
            raise ValueError('No requested cutters')
        result = cutters[0]
        for cutter in cutters[1:]:
            result = result.fuse(cutter)
        return result

    def target(spec):
        L, W, H = spec['size']
        stock = Part.makeBox(L, W, H)
        pockets = [pocket(p, H) for p in spec['pockets']]
        cutters = list(pockets)
        for h in spec['holes']:
            depth = H if h['through'] else h['depth']
            cutters.append(Part.makeCylinder(h['dia']/2, depth,
                                            App.Vector(h['x'], h['y'], H-depth)))
        merged = union(cutters)
        removed = stock.common(merged)
        solid = stock.cut(merged)
        validation = check(solid, 'reference', single=True)
        check(removed, 'removed material')
        check(merged, 'union of cutters')
        removed_again = stock.cut(solid)
        residual = abs(stock.Volume - solid.Volume - removed.Volume)
        if residual > tolerance(stock.Volume):
            raise ValueError('Reference volume conservation failure')
        if abs(removed_again.Volume - removed.Volume) > tolerance(stock.Volume):
            raise ValueError('Reference removed-volume crosscheck failure')
        validation.update({'stock_volume_mm3': stock.Volume, 'cut_volume_mm3': removed.Volume,
                           'pocket_union_cut_volume_mm3': stock.common(union(pockets)).Volume,
                           'volume_conservation_residual_mm3': residual,
                           'within_stock': solid.cut(stock).Volume <= tolerance(stock.Volume)})
        if not validation['within_stock'] or not 0 < removed.Volume < stock.Volume:
            raise ValueError('Invalid reference cut volume or stock containment')
        return solid, stock, validation

    def measure(a, b, stock, fuzzy, refine):
        # Coplanar-face unification is a retry, not a geometry-changing repair.
        if refine:
            a, b = a.removeSplitter(), b.removeSplitter()
        ra, rb = stock.cut(a, fuzzy), stock.cut(b, fuzzy)
        if refine:
            ra, rb = ra.removeSplitter(), rb.removeSplitter()
        # Diagnose OCC's silent coincident-shell failure instead of reporting a
        # bogus volume: subtracting a shape that shares the stock's outer shell
        # can return the stock unchanged while shape.cut(stock) says it is inside.
        for label, shape, removed in (('reference', a, ra), ('output', b, rb)):
            if removed.Volume >= stock.Volume - abs_tol:
                try:
                    inside = shape.cut(stock, fuzzy).Volume
                except Exception:
                    inside = None
                if inside is not None and inside <= abs_tol:
                    raise ValueError(
                        'OCC coincident-shell Boolean failure on %s: stock.cut(shape) returned the '
                        'stock unchanged although shape lies inside the stock (shape volume %.9g mm3)'
                        % (label, shape.Volume))
        whole_common = a.common(b, fuzzy)
        if not whole_common.isNull() and not whole_common.isValid():
            raise ValueError('Invalid whole-material Boolean intersection')
        whole_common_refined = whole_common.removeSplitter()
        if whole_common_refined.isValid() and whole_common_refined.Volume > 0:
            whole_common = whole_common_refined
        removed_common = ra.common(rb, fuzzy)
        if not removed_common.isNull() and not removed_common.isValid():
            raise ValueError('Invalid removed-material Boolean intersection')
        # Direct common() can be off by thousands of mm3 on coplanar shells;
        # removeSplitter() restores the exact shared-volume union identity.
        removed_common_refined = removed_common.removeSplitter()
        if removed_common_refined.isValid() and removed_common_refined.Volume > 0:
            removed_common = removed_common_refined
        removed_inter = removed_common.Volume
        removed_union = ra.Volume + rb.Volume - removed_inter
        outside_a, outside_b = a.cut(stock, fuzzy).Volume, b.cut(stock, fuzzy).Volume
        tol = tolerance(stock.Volume)
        removed_fused = ra.fuse(rb, fuzzy)
        if not removed_fused.isNull() and not removed_fused.isValid():
            raise ValueError('Invalid removed-material Boolean union')
        # removeSplitter() removes redundant splitter edges/faces introduced by
        # fuse; it preserves the point set and is a refinement, not a tolerance
        # change. NL07's raw fuse over-counts by 943 mm3 until refined.
        fused_raw = removed_fused.Volume
        fused_refined = removed_fused.removeSplitter()
        if fused_refined.isNull() or not fused_refined.isValid() or fused_refined.Volume <= 0:
            raise ValueError('Invalid refined removed-material union')
        fused_volume = fused_refined.Volume
        union_residual = abs(removed_union-fused_volume)
        if union_residual > tol:
            raise ValueError('Removed union/intersection volume mismatch: %.9g mm3 '
                             '(raw fuse %.9g, refined %.9g)'
                             % (union_residual, fused_raw, fused_volume))
        if not -tol <= removed_inter <= min(ra.Volume, rb.Volume) + tol:
            raise ValueError('Removed intersection volume outside bounds')
        complement_residual = None
        direct_whole_residual = None
        # For stock-contained outputs use two actual complement Booleans:
        # A intersect B = S minus (Ra union Rb),
        # A union B = S minus (Ra intersect Rb).
        # This avoids OCC's silently empty direct whole intersections while
        # crosschecking subtraction, union, intersection and volume identities.
        if outside_a <= tol and outside_b <= tol:
            if max(abs(a.Volume + ra.Volume-stock.Volume),
                   abs(b.Volume + rb.Volume-stock.Volume)) > tol:
                raise ValueError('Output/removed stock partition volume mismatch')
            whole_common_complement = stock.cut(fused_refined, fuzzy)
            whole_fused_complement = stock.cut(removed_common, fuzzy)
            for shape in (whole_common_complement, whole_fused_complement):
                if not shape.isNull() and not shape.isValid():
                    raise ValueError('Invalid whole-volume complement Boolean')
            whole_inter = whole_common_complement.Volume
            whole_union = whole_fused_complement.Volume
            complement_residual = max(abs(whole_inter-(stock.Volume-removed_union)),
                                      abs(whole_union-(stock.Volume-removed_inter)),
                                      abs(whole_union-(a.Volume+b.Volume-whole_inter)))
            if complement_residual > tol:
                raise ValueError('Whole/removed complement mismatch: %.9g mm3' % complement_residual)
            direct_whole_residual = abs(whole_common.Volume-whole_inter)
        else:
            whole_inter = whole_common.Volume
            whole_union = a.fuse(b, fuzzy).Volume
            if not whole_common.isNull() and not whole_common.isValid():
                raise ValueError('Invalid direct whole intersection')
            if abs(whole_union-(a.Volume+b.Volume-whole_inter)) > tol:
                raise ValueError('Direct whole union/intersection mismatch: ' + json.dumps({
                    'a': a.Volume, 'b': b.Volume, 'outside_a': outside_a, 'outside_b': outside_b,
                    'common': whole_inter, 'union': whole_union,
                    'removed_common': removed_inter, 'removed_union': removed_union}))
        if not -tol <= whole_inter <= min(a.Volume, b.Volume) + tol:
            raise ValueError('Whole intersection volume outside bounds')
        ratio = lambda i, u: min(1., max(0., i/u)) if u > abs_tol else 1.
        whole_diff = max(0., whole_union-whole_inter)
        removed_diff = max(0., removed_union-removed_inter)
        return {'whole_iou': ratio(whole_inter, whole_union),
                'removed_iou': ratio(removed_inter, removed_union),
                'a_solid_volume_mm3': a.Volume, 'b_solid_volume_mm3': b.Volume,
                'a_cut_volume_mm3': ra.Volume, 'b_cut_volume_mm3': rb.Volume,
                'whole_symmetric_difference_mm3': whole_diff,
                'removed_symmetric_difference_mm3': removed_diff,
                'a_only_removed_mm3': max(0., ra.Volume-removed_inter),
                'b_only_removed_mm3': max(0., rb.Volume-removed_inter),
                'a_outside_stock_mm3': outside_a,
                'b_outside_stock_mm3': outside_b,
                'boolean_complement_residual_mm3': complement_residual,
                'direct_whole_intersection_residual_mm3': direct_whole_residual,
                'direct_whole_intersection_rejected': direct_whole_residual is not None and direct_whole_residual > tol,
                'whole_iou_method': 'stock_complement_booleans' if complement_residual is not None else 'direct_booleans',
                'boolean_union_residual_mm3': union_residual,
                'boolean_union_raw_volume_mm3': fused_raw,
                'boolean_union_refined_volume_mm3': fused_volume,
                'boolean_fuzzy_tolerance_mm': fuzzy,
                'boolean_refined': refine,
                'disagrees': whole_diff > tolerance(stock.Volume)}

    def partition_measure(a, b, stock, fuzzy):
        # General Fuse returns a disjoint Boolean cell partition plus provenance
        # maps. It avoids faulty pairwise classification at tangent boundaries.
        result, mapping = a.generalFuse([b, stock], fuzzy)
        raw_pieces = result.Solids
        if not raw_pieces or not result.isValid():
            raise ValueError('Invalid General Fuse partition')
        # OCC can return individual cells with reversed orientation, so a raw
        # Volume sum is not a point-set measure. Normalise each cell first, then
        # verify that the partition repartitions the stock exactly.
        pieces = []
        for piece in raw_pieces:
            if piece.Volume < 0:
                flipped = piece.copy()
                flipped.reverse()
                pieces.append(flipped)
            else:
                pieces.append(piece)
        if any(piece.Volume <= 0 or not piece.isValid() for piece in pieces):
            raise ValueError('Invalid General Fuse partition cell')
        groups = []
        for mapped in mapping:
            mapped_solids = [solid for shape in mapped for solid in shape.Solids]
            groups.append({i for i, piece in enumerate(pieces)
                           if any(piece.isSame(solid) for solid in mapped_solids)})
        if len(groups) != 3:
            raise ValueError('General Fuse provenance mapping missing')
        volumes = [p.Volume for p in pieces]
        volume = lambda indices: sum(volumes[i] for i in sorted(indices))
        tol = tolerance(stock.Volume)
        if abs(sum(volumes) - stock.Volume) > tol:
            raise ValueError('General Fuse partition total volume mismatch: %.9g mm3'
                             % abs(sum(volumes) - stock.Volume))
        residual = max(abs(volume(group)-shape.Volume) for group, shape in zip(groups, (a, b, stock)))
        if residual > tol or set.union(*groups) != set(range(len(pieces))):
            raise ValueError('General Fuse source-volume/map mismatch: %.9g mm3' % residual)
        ga, gb, gs = groups
        bounds = stock.BoundBox
        contained = all(shape.BoundBox.XMin >= bounds.XMin-1e-7 and shape.BoundBox.YMin >= bounds.YMin-1e-7
                        and shape.BoundBox.ZMin >= bounds.ZMin-1e-7 and shape.BoundBox.XMax <= bounds.XMax+1e-7
                        and shape.BoundBox.YMax <= bounds.YMax+1e-7 and shape.BoundBox.ZMax <= bounds.ZMax+1e-7
                        for shape in (a, b))
        if contained and abs(bounds.XLength*bounds.YLength*bounds.ZLength-stock.Volume) <= tol:
            if gs != set(range(len(pieces))) or abs(sum(volumes)-stock.Volume) > tol:
                raise ValueError('General Fuse partition contradicts bounding-box stock containment')
        ra, rb = gs-ga, gs-gb
        whole_inter, whole_union = volume(ga & gb), volume(ga | gb)
        removed_inter, removed_union = volume(ra & rb), volume(ra | rb)
        outside_a, outside_b = volume(ga-gs), volume(gb-gs)
        ratio = lambda i, u: min(1., max(0., i/u)) if u > abs_tol else 1.
        whole_diff = volume(ga ^ gb)
        removed_diff = volume(ra ^ rb)
        return {'whole_iou': ratio(whole_inter, whole_union),
                'removed_iou': ratio(removed_inter, removed_union),
                'a_solid_volume_mm3': a.Volume, 'b_solid_volume_mm3': b.Volume,
                'a_cut_volume_mm3': volume(ra), 'b_cut_volume_mm3': volume(rb),
                'whole_symmetric_difference_mm3': whole_diff,
                'removed_symmetric_difference_mm3': removed_diff,
                'a_only_removed_mm3': volume(ra-rb), 'b_only_removed_mm3': volume(rb-ra),
                'a_outside_stock_mm3': outside_a, 'b_outside_stock_mm3': outside_b,
                'boolean_complement_residual_mm3': abs(whole_diff-removed_diff) if outside_a <= tol and outside_b <= tol else None,
                'boolean_union_residual_mm3': residual,
                'boolean_partition_source_volume_residual_mm3': residual,
                'boolean_partition_cell_count': len(pieces),
                'direct_whole_intersection_residual_mm3': None,
                'direct_whole_intersection_rejected': True,
                'whole_iou_method': 'general_fuse_cell_partition',
                'boolean_fuzzy_tolerance_mm': fuzzy, 'boolean_refined': False,
                'disagrees': whole_diff > tol}

    def compare(a, b, stock):
        rejected = []
        # Primary: a General Fuse cell partition that is validated against the
        # source volumes and the stock volume before any metric is accepted.
        # Pairwise Booleans break down on coplanar shells; the partition either
        # validates exactly or is rejected.
        for fuzzy in (0., 1e-7):
            try:
                result = partition_measure(a, b, stock, fuzzy)
                result['rejected_boolean_attempts'] = rejected
                return result
            except Exception as exc:
                rejected.append({'method': 'general_fuse_cell_partition',
                                 'fuzzy_tolerance_mm': fuzzy, 'error': str(exc)})
        for fuzzy, refine in ((0., False), (0., True), (1e-7, False), (1e-7, True)):
            try:
                result = measure(a, b, stock, fuzzy, refine)
                result['rejected_boolean_attempts'] = rejected
                return result
            except Exception as exc:
                rejected.append({'fuzzy_tolerance_mm': fuzzy, 'refined': refine, 'error': str(exc)})
        # A shared rigid rotation preserves the requested geometry and all
        # volume metrics; it can avoid axis-aligned OCC classifier degeneracy.
        for angle in (17., 43.):
            try:
                rotated = [shape.copy() for shape in (a, b, stock)]
                for shape in rotated:
                    shape.rotate(App.Vector(0, 0, 0), App.Vector(1, 2, 3), angle)
                result = measure(*rotated, 0., False)
                result['boolean_rigid_rotation_degrees'] = angle
                result['rejected_boolean_attempts'] = rejected
                return result
            except Exception as exc:
                rejected.append({'method': 'shared_rigid_rotation', 'degrees': angle, 'error': str(exc)})
        raise ValueError('All Boolean comparison attempts failed: ' + json.dumps(rejected))

    rows = []
    for task in payload['tasks']:
        tid = task['id']
        out = {'id': tid, 'reference_validation': {}, 'gold_comparison': {},
               'pairing_checks': {}, 'measurements': {d: {} for d in ('latent', 'visible', 'archived_gold')},
               'runtime_errors': []}
        def error(stage, exc):
            item = {'id': tid, 'stage': stage, 'error': type(exc).__name__ + ': ' + str(exc)}
            errors.append(item)
            out['runtime_errors'].append(item)
        references = {}
        L, W, H = task['latent']['size']
        stock = Part.makeBox(L, W, H)
        for definition in ('latent', 'visible'):
            try:
                solid, _, validation = target(task[definition])
                references[definition] = solid
                out['reference_validation'][definition] = validation
                # The analytic oracle uses no CAD operations, and independently
                # checks the union of all rectangular pockets for every task.
                if definition == 'latent':
                    delta = abs(validation['pocket_union_cut_volume_mm3'] - task['rectangular_oracle_mm3'])
                    out['rectangular_oracle_error_mm3'] = delta
                    if delta > tolerance(stock.Volume):
                        raise ValueError('Analytic rectangular union oracle mismatch')
            except Exception as exc:
                error('reference/' + definition, exc)
        if tid == 'NL02':
            p, q = task['latent']['pockets']
            a = pocket(p, H)
            b = pocket(q, H)
            out['NL02_overlap_oracle'] = {
                'first_dimensions': [p['w'], p['h'], p['d']],
                'second_dimensions': [q['w'], q['h'], q['d']],
                'first_volume_mm3': a.Volume, 'second_volume_mm3': b.Volume,
                'overlap_volume_mm3': a.common(b).Volume,
                'union_volume_mm3': a.fuse(b).Volume,
                'expected_union_mm3': 6400 + 8000 - 480 * 5,
                'passed': abs(a.fuse(b).Volume - 12000) < abs_tol
            }
            if not out['NL02_overlap_oracle']['passed']:
                error('NL02_oracle', ValueError('Expected 12000 mm3'))
        try:
            gold = Part.read(task['paths']['gold'])
            check(gold, 'archived gold')
            references['archived_gold'] = gold
            for definition in ('latent', 'visible'):
                if definition in references:
                    out['gold_comparison'][definition] = compare(references[definition], gold, stock)
            expected_first = stock.cut(pocket(task['latent']['pockets'][0], H))
            for key, expected in [('gold_stock', stock), ('gold_first_pocket', expected_first),
                                  ('contract_stock', stock)]:
                archived = Part.read(task['paths'][key])
                check(archived, key)
                result = compare(expected, archived, stock)
                out['pairing_checks'][key] = result
                if result['disagrees']:
                    raise ValueError('Archive pairing geometry mismatch: ' + key)
        except Exception as exc:
            error('archive/pairing', exc)
        if 'latent' in references and 'visible' in references:
            try:
                out['hidden_coordinate_drift'] = compare(references['latent'], references['visible'], stock)
            except Exception as exc:
                error('hidden_coordinate_drift', exc)
        for route in ('contract', 'script'):
            archived = task['archive'][route]
            if not archived['exec_ok']:
                for definition in out['measurements']:
                    out['measurements'][definition][route] = {'status': 'archived_exec_failed',
                                                           'whole_iou': 0., 'removed_iou': 0.}
                continue
            candidate = None
            try:
                candidate = Part.read(task['paths'][route])
                check(candidate, 'archived ' + route + ' output')
            except Exception as exc:
                error('output/' + route, exc)
            # Archive pairing must run even when a validated comparison fails.
            if candidate is not None and 'archived_gold' in references:
                try:
                    old_a, old_b = references['archived_gold'], candidate
                    old_ra, old_rb = stock.cut(old_a), stock.cut(old_b)
                    legacy = {}
                    for metric, left, right in [('whole_iou', old_a, old_b),
                                                ('removed_iou', old_ra, old_rb)]:
                        u = left.fuse(right).Volume
                        legacy[metric] = left.common(right).Volume/u if u > 1e-9 else 0.
                    drifts = {metric: abs(legacy[metric] - archived[old])
                              for metric, old in [('whole_iou', 'iou'), ('removed_iou', 'removed_iou')]
                              if archived[old] is not None}
                    out['pairing_checks'][route + '_legacy_reproduction'] = legacy
                    out['pairing_checks'][route + '_reported_metric_deltas'] = drifts
                    if len(drifts) != 2 or any(v > metric_tol for v in drifts.values()):
                        raise ValueError('Archived STEP/report raw metric mismatch')
                except Exception as exc:
                    error('archive/metric_pairing/' + route, exc)
            for definition in out['measurements']:
                try:
                    if candidate is None or definition not in references:
                        raise ValueError('Missing validated output or reference')
                    measurement = compare(references[definition], candidate, stock)
                    measurement['status'] = 'ok'
                    out['measurements'][definition][route] = measurement
                    if definition == 'archived_gold':
                        legacy = out['pairing_checks'].get(route + '_legacy_reproduction', {})
                        out['pairing_checks'][route + '_validated_minus_legacy'] = {
                            metric: measurement[metric]-legacy[metric] for metric in legacy}
                except Exception as exc:
                    out['measurements'][definition][route] = {'status': 'audit_error',
                                                           'whole_iou': None, 'removed_iou': None}
                    error('compare/' + definition + '/' + route, exc)
        rows.append(out)
    return {'complete': not errors, 'tasks': rows, 'runtime_errors': errors,
            'FreeCAD_version': list(App.Version()), 'OCC_version': Part.OCC_VERSION}


def main():
    path = Path(os.environ['WORKORDER_AUDIT_OUTPUT'])
    try:
        payload = json.loads(Path(os.environ['WORKORDER_AUDIT_INPUT']).read_text(encoding='utf-8'))
        result = run(payload)
    except Exception:
        result = {'complete': False, 'fatal_error': traceback.format_exc()}
    path.write_text(json.dumps(normalized(result), ensure_ascii=False, sort_keys=True,
                               indent=2, allow_nan=False) + '\n', encoding='utf-8')


# FreeCAD executes this file with the filename stem as __name__.
if __name__ in ('__main__', '_workorder_reference_worker'):
    main()
