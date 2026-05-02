# -*- coding: utf-8 -*-
"""Void-RFT Revit placement.

Separate from rebar_placer.py in FlatSlabRebar — intentionally no shared
code. Trimmers and diagonals are placed as rebar sets where possible
(SetLayoutAsNumberWithSpacing), with automatic fallback to individual bars.

_SilentFailuresPreprocessor and _configure_fast_transaction copied verbatim
from FlatSlabRebar/rebar_placer.py lines 21-68.
"""
from __future__ import print_function

import time

import clr
clr.AddReference('System')
from System.Collections.Generic import List

from Autodesk.Revit.DB import (Line, XYZ, Curve, Transaction, BuiltInParameter,
                               TransactionStatus, IFailuresPreprocessor,
                               FailureSeverity, FailureProcessingResult,
                               FailureHandlingOptions)
from Autodesk.Revit.DB.Structure import Rebar, RebarStyle, RebarHookOrientation


# ---------------------------------------------------------------------------
# Failure handling (copied from rebar_placer.py)
# ---------------------------------------------------------------------------

_PreprocessorBase = IFailuresPreprocessor if IFailuresPreprocessor is not None else object


class _SilentFailuresPreprocessor(_PreprocessorBase):
    """Silently resolve all Revit failure messages without showing modal dialogs."""
    def PreprocessFailures(self, failuresAccessor):
        has_unresolvable = False
        for msg in list(failuresAccessor.GetFailureMessages()):
            if msg.GetSeverity() == FailureSeverity.Warning:
                failuresAccessor.DeleteWarning(msg)
            elif msg.HasResolutions():
                try:
                    failuresAccessor.ResolveFailure(msg)
                except Exception:
                    has_unresolvable = True
            else:
                has_unresolvable = True
        if has_unresolvable:
            return FailureProcessingResult.ProceedWithRollBack
        return FailureProcessingResult.Continue


_PREPROCESSOR = _SilentFailuresPreprocessor()


def _configure_fast_transaction(t):
    """Suppress all failure dialogs so Revit never shows a blocking popup."""
    try:
        opts = t.GetFailureHandlingOptions()
        opts.SetFailuresPreprocessor(_PREPROCESSOR)
        opts.SetClearAfterRollback(True)
        opts.SetDelayedMiniWarnings(True)
        opts.SetForcedModalHandling(False)
        t.SetFailureHandlingOptions(opts)
    except Exception:
        pass


def _compute_void_mark(meta, kind):
    """Use one unified mark for all void additional reinforcement."""
    return 'Void Add RFT'


def _rebar_element_get_parameters(element, name):
    result = element.GetParameters(name)
    if result is None:
        return []
    try:
        return list(result)
    except Exception:
        return []


def _find_mark_param(element):
    try:
        for p in _rebar_element_get_parameters(element, 'Mark'):
            if not p.IsReadOnly:
                return p
    except Exception:
        pass
    try:
        p = element.LookupParameter('Mark')
        if p is not None and not p.IsReadOnly:
            return p
    except Exception:
        pass
    try:
        p = element.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
        if p is not None and not p.IsReadOnly:
            return p
    except Exception:
        pass
    return None


def _find_comment_param(element):
    try:
        for p in _rebar_element_get_parameters(element, 'Comments'):
            if not p.IsReadOnly:
                return p
    except Exception:
        pass
    try:
        p = element.LookupParameter('Comments')
        if p is not None and not p.IsReadOnly:
            return p
    except Exception:
        pass
    try:
        p = element.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if p is not None and not p.IsReadOnly:
            return p
    except Exception:
        pass
    return None


def _apply_metadata_queue(doc, mark_queue, comment_queue):
    """Apply mark/comment in a dedicated transaction after creation commit."""
    if not mark_queue and not comment_queue:
        return
    t = Transaction(doc, 'Set Void Rebar Metadata')
    try:
        status = t.Start()
    except Exception as e:
        print('[void_placer] WARNING: metadata transaction start failed: {}'.format(e))
        return
    if TransactionStatus is not None and status != TransactionStatus.Started:
        print('[void_placer] WARNING: metadata transaction did not start')
        return

    _configure_fast_transaction(t)
    marks_ok = 0
    comments_ok = 0
    try:
        for eid, text in (mark_queue or []):
            try:
                elem = doc.GetElement(eid)
                if elem is None:
                    continue
                p = _find_mark_param(elem)
                if p is not None:
                    p.Set(text)
                    marks_ok += 1
            except Exception:
                pass
        for eid, text in (comment_queue or []):
            try:
                elem = doc.GetElement(eid)
                if elem is None:
                    continue
                p = _find_comment_param(elem)
                if p is not None:
                    p.Set(text)
                    comments_ok += 1
            except Exception:
                pass
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    print('[void_placer] metadata: marks={} comments={}'.format(marks_ok, comments_ok))


def _quantize(value, tol):
    if tol <= 0:
        return round(value, 6)
    return round(round(value / tol) * tol, 6)


def _is_uniform_spacing(values, expected_spacing, tol):
    if len(values) < 2:
        return False, 0.0
    diffs = []
    for i in range(1, len(values)):
        d = values[i] - values[i - 1]
        if d <= tol:
            return False, 0.0
        diffs.append(d)
    spacing = expected_spacing if expected_spacing and expected_spacing > tol else (sum(diffs) / float(len(diffs)))
    for d in diffs:
        if abs(d - spacing) > tol:
            return False, 0.0
    return True, spacing


def _try_set_layout(base_rebar, count, spacing):
    try:
        accessor = base_rebar.GetShapeDrivenAccessor()
        accessor.SetLayoutAsNumberWithSpacing(count, spacing, True, True, True)
        return True
    except Exception:
        try:
            accessor = base_rebar.GetShapeDrivenAccessor()
            accessor.SetLayoutAsMaximumSpacing(spacing, spacing * (count - 1), True, True, True)
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Trimmer placement
# ---------------------------------------------------------------------------

def place_trimmer_segments(doc, floor, segments, bar_type_by_dia, params):
    """Place a list of trimmer segment dicts (from void_geometry).

    segments: list of dicts with keys 'direction', 'start', 'end',
              'fixed_val', 'z', 'start_hook', 'end_hook', 'diam_mm', ...
    bar_type_by_dia: dict mapping int mm diameter -> Revit RebarBarType.
    params: dict with 'slab_thickness', 'cover', etc.

    Returns (placed_count, failed_count).
    """
    if not segments:
        return 0, 0

    placed = 0
    failed = 0
    mark_queue = []
    comment_queue = []
    t0 = time.time()

    t = Transaction(doc, 'Place Void Trimmer Rebar')
    try:
        status = t.Start()
    except Exception as e:
        print('[void_placer] WARNING: trimmer transaction could not start: {}'.format(e))
        return 0, len(segments)
    if TransactionStatus is not None and status != TransactionStatus.Started:
        print('[void_placer] WARNING: trimmer transaction did not start (status={})'.format(status))
        return 0, len(segments)

    _configure_fast_transaction(t)

    try:
        spacing = params.get('trimmer_spacing_ft', 0.0)
        tol = max(1e-3, min(0.02, spacing * 0.1)) if spacing > 0 else 1e-3

        grouped = {}
        for seg in segments:
            key = (
                seg.get('void_id', ''),
                seg.get('edge', ''),
                seg.get('direction', ''),
                int(seg.get('diam_mm', 0)),
                _quantize(seg.get('z', 0.0), 1e-4),
                _quantize(seg.get('start', 0.0), 1e-4),
                _quantize(seg.get('end', 0.0), 1e-4),
                bool(seg.get('start_hook', False)),
                bool(seg.get('end_hook', False)),
            )
            grouped.setdefault(key, []).append(seg)

        for _, group in grouped.items():
            group = sorted(group, key=lambda s: s.get('fixed_val', 0.0))
            fixed_vals = [s.get('fixed_val', 0.0) for s in group]
            uniform, set_spacing = _is_uniform_spacing(fixed_vals, spacing, tol)

            base = _place_single_straight_bar(doc, floor, group[0], bar_type_by_dia, params)
            if base is None:
                failed += 1
                rest = group[1:]
            else:
                mark_queue.append((base.Id, _compute_void_mark(group[0], 'trimmer')))
                if len(group) > 1 and uniform and _try_set_layout(base, len(group), set_spacing):
                    placed += len(group)
                    continue
                placed += 1
                rest = group[1:]

            for seg in rest:
                rb = _place_single_straight_bar(doc, floor, seg, bar_type_by_dia, params)
                if rb is None:
                    failed += 1
                else:
                    placed += 1
                    mark_queue.append((rb.Id, _compute_void_mark(seg, 'trimmer')))
        t.Commit()
    except Exception:
        t.RollBack()
        raise

    _apply_metadata_queue(doc, mark_queue, comment_queue)

    print('[void_placer] trimmers: placed={} failed={} time={:.2f}s'.format(
        placed, failed, time.time() - t0))
    return placed, failed


def _place_single_straight_bar(doc, floor, seg, bar_type_by_dia, params):
    """Place one straight void trimmer bar."""
    direction  = seg['direction']
    start      = seg['start']
    end        = seg['end']
    fixed_val  = seg['fixed_val']
    z          = seg['z']
    diam_mm    = seg['diam_mm']

    bar_type = bar_type_by_dia.get(int(diam_mm))
    if bar_type is None:
        print('[void_placer] WARNING: no bar type for Phi{}mm, skipping'.format(diam_mm))
        return None

    if direction == 'X':
        p1 = XYZ(start,     fixed_val, z)
        p2 = XYZ(end,       fixed_val, z)
        normal = XYZ(0, 1, 0)
    else:
        p1 = XYZ(fixed_val, start,     z)
        p2 = XYZ(fixed_val, end,       z)
        normal = XYZ(1, 0, 0)

    curves = List[Curve]()
    curves.Add(Line.CreateBound(p1, p2))

    if curves.Count == 0:
        return None

    try:
        return Rebar.CreateFromCurves(
            doc, RebarStyle.Standard, bar_type, None, None,
            floor, normal, curves,
            RebarHookOrientation.Left, RebarHookOrientation.Right,
            True, True,
        )
    except Exception as e:
        # Retry as straight bar if bent-bar placement fails.
        try:
            straight = List[Curve]()
            straight.Add(Line.CreateBound(p1, p2))
            return Rebar.CreateFromCurves(
                doc, RebarStyle.Standard, bar_type, None, None,
                floor, normal, straight,
                RebarHookOrientation.Left, RebarHookOrientation.Right,
                True, True,
            )
        except Exception:
            print('[void_placer] trimmer CreateFromCurves failed: {}'.format(e))
            return None


# ---------------------------------------------------------------------------
# Diagonal placement
# ---------------------------------------------------------------------------

def place_diagonal_segments(doc, floor, diagonals, bar_type_by_dia, params):
    """Place a list of diagonal dicts (from void_geometry).

    diagonals: list of dicts with 'p1', 'p2' (3-tuples in feet), 'diam_mm', ...
    bar_type_by_dia: dict mapping int mm diameter -> RebarBarType.

    Returns (placed_count, failed_count).
    """
    if not diagonals:
        return 0, 0

    placed = 0
    failed = 0
    mark_queue = []
    comment_queue = []
    t0 = time.time()

    t = Transaction(doc, 'Place Void Diagonal Rebar')
    try:
        status = t.Start()
    except Exception as e:
        print('[void_placer] WARNING: diagonal transaction could not start: {}'.format(e))
        return 0, len(diagonals)
    if TransactionStatus is not None and status != TransactionStatus.Started:
        print('[void_placer] WARNING: diagonal transaction did not start')
        return 0, len(diagonals)

    _configure_fast_transaction(t)

    try:
        spacing = params.get('trimmer_spacing_ft', 0.0)
        tol = max(1e-3, min(0.02, spacing * 0.1)) if spacing > 0 else 1e-3

        grouped = {}
        for diag in diagonals:
            x1, y1, _ = diag['p1']
            x2, y2, _ = diag['p2']
            dx = x2 - x1
            dy = y2 - y1
            length = (dx * dx + dy * dy) ** 0.5
            if length > 1e-9:
                ux = dx / length
                uy = dy / length
            else:
                ux = uy = 0.0
            key = (
                diag.get('void_id', ''),
                diag.get('corner', ''),
                diag.get('layer', ''),
                int(diag.get('diam_mm', 0)),
                _quantize(diag['p1'][2], 1e-4),
                _quantize(length, 1e-4),
                _quantize(ux, 1e-4),
                _quantize(uy, 1e-4),
            )
            grouped.setdefault(key, []).append(diag)

        try:
            _max_group = max([len(v) for v in grouped.values()]) if grouped else 0
        except Exception:
            _max_group = 0
        print('[void_placer] diagonals raw={} groups={} max_group={}'.format(
            len(diagonals), len(grouped), _max_group))

        for _, group in grouped.items():
            # Sort by midpoint projection on in-plane normal to bar axis.
            x1, y1, _ = group[0]['p1']
            x2, y2, _ = group[0]['p2']
            dx = x2 - x1
            dy = y2 - y1
            ln = (dx * dx + dy * dy) ** 0.5
            if ln > 1e-9:
                nx = -dy / ln
                ny = dx / ln
            else:
                nx = ny = 0.0

            def _proj(d):
                mx = (d['p1'][0] + d['p2'][0]) * 0.5
                my = (d['p1'][1] + d['p2'][1]) * 0.5
                return mx * nx + my * ny

            group = sorted(group, key=_proj)
            vals = [_proj(d) for d in group]
            uniform, set_spacing = _is_uniform_spacing(vals, spacing, tol)

            base = _place_single_diagonal(doc, floor, group[0], bar_type_by_dia, params)
            if base is None:
                failed += 1
                rest = group[1:]
            else:
                mark_queue.append((base.Id, _compute_void_mark(group[0], 'diagonal')))
                comment_queue.append((base.Id, 'Diagonal'))
                if len(group) > 1 and uniform and _try_set_layout(base, len(group), set_spacing):
                    placed += len(group)
                    continue
                placed += 1
                rest = group[1:]

            for diag in rest:
                rb = _place_single_diagonal(doc, floor, diag, bar_type_by_dia, params)
                if rb is None:
                    failed += 1
                else:
                    placed += 1
                    mark_queue.append((rb.Id, _compute_void_mark(diag, 'diagonal')))
                    comment_queue.append((rb.Id, 'Diagonal'))
        t.Commit()
    except Exception:
        t.RollBack()
        raise

    _apply_metadata_queue(doc, mark_queue, comment_queue)

    print('[void_placer] diagonals: placed={} failed={} time={:.2f}s'.format(
        placed, failed, time.time() - t0))
    return placed, failed


def _place_single_diagonal(doc, floor, diag, bar_type_by_dia, params):
    """Place one diagonal bar as a straight horizontal rebar (no hooks in v1)."""
    x1, y1, z1 = diag['p1']
    x2, y2, z2 = diag['p2']
    diam_mm = diag['diam_mm']

    bar_type = bar_type_by_dia.get(int(diam_mm))
    if bar_type is None:
        print('[void_placer] WARNING: no bar type for diagonal Phi{}mm'.format(diam_mm))
        return None

    p1 = XYZ(x1, y1, z1)
    p2 = XYZ(x2, y2, z2)
    if p1.DistanceTo(p2) < 1e-6:
        return None

    # For shape-driven sets, distribution follows the rebar plane normal.
    # Using Z here causes bars to array vertically through slab thickness.
    # Use an in-plane horizontal normal perpendicular to the diagonal bar
    # so spacing arrays horizontally in plan.
    dx = x2 - x1
    dy = y2 - y1
    norm_len = (dx * dx + dy * dy) ** 0.5
    if norm_len > 1e-9:
        normal = XYZ(-dy / norm_len, dx / norm_len, 0.0)
    else:
        normal = XYZ(0, 0, 1)

    curves = List[Curve]()
    curves.Add(Line.CreateBound(p1, p2))

    try:
        return Rebar.CreateFromCurves(
            doc, RebarStyle.Standard, bar_type, None, None,
            floor, normal, curves,
            RebarHookOrientation.Left, RebarHookOrientation.Right,
            True, True,
        )
    except Exception as e:
        print('[void_placer] diagonal CreateFromCurves failed: {}'.format(e))
        return None
