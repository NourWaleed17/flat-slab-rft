# -*- coding: utf-8 -*-
"""Place main slab rebar segments in Revit."""
from __future__ import print_function

import clr
clr.AddReference('System')
from System.Collections.Generic import List
from collections import defaultdict

from Autodesk.Revit.DB import (Line, XYZ, Curve, Transaction, BuiltInParameter,
                               FailureHandlingOptions, FailureSeverity,
                               TransactionStatus, IFailuresPreprocessor,
                               FailureProcessingResult)
from Autodesk.Revit.DB.Structure import Rebar, RebarStyle, RebarHookOrientation


# IFailuresPreprocessor is a Revit API interface; fall back to object in tests.
_PreprocessorBase = IFailuresPreprocessor if IFailuresPreprocessor is not None else object


class _SilentFailuresPreprocessor(_PreprocessorBase):
    """Silently resolve all Revit failure messages without showing modal dialogs.

    Warnings are deleted immediately.  Errors (e.g. 'rebar out of host') are
    resolved using their default resolution so the transaction can continue.
    Bars that genuinely cannot be resolved are skipped — they will appear in
    the 'failed' counter rather than as a blocking popup.
    """
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
                # No resolution available (e.g. "Can't solve Rebar Shape" in some
                # contexts). Roll back silently rather than letting Revit show a
                # blocking modal dialog that would lock the document for subsequent
                # transactions.
                has_unresolvable = True
        if has_unresolvable:
            return FailureProcessingResult.ProceedWithRollBack
        return FailureProcessingResult.Continue


_PREPROCESSOR = _SilentFailuresPreprocessor()


def _configure_fast_transaction(t):
    """Suppress all failure dialogs so Revit never shows a blocking popup.

    SetForcedModalHandling(False) prevents the modal error dialog entirely.
    The preprocessor then dismisses warnings and resolves errors so the
    transaction can commit without user interaction.
    """
    try:
        opts = t.GetFailureHandlingOptions()
        opts.SetFailuresPreprocessor(_PREPROCESSOR)
        opts.SetClearAfterRollback(True)
        opts.SetDelayedMiniWarnings(True)
        opts.SetForcedModalHandling(False)
        t.SetFailureHandlingOptions(opts)
    except Exception:
        pass   # older Revit API — proceed without tuning


def _build_curve_list(points):
    """Return a .NET List[Curve] from a polyline point sequence."""
    curves = List[Curve]()
    for i in range(len(points) - 1):
        p_start = points[i]
        p_end = points[i + 1]
        if p_start.DistanceTo(p_end) <= 1e-6:
            continue
        curves.Add(Line.CreateBound(p_start, p_end))
    return curves


def _get_vertical_leg_delta(layer_z, params):
    """Return signed Z delta for vertical end legs."""
    slab_top_z = params.get('slab_top_z')
    slab_bottom_z = params.get('slab_bottom_z')
    slab_thickness = params.get('slab_thickness')
    cover = params.get('cover', 0.0)
    if slab_thickness is None or slab_thickness <= 0:
        return 0.0

    leg_len = slab_thickness - 2.0 * cover
    if leg_len <= 0:
        return 0.0

    if slab_top_z is None or slab_bottom_z is None:
        # Fallback direction if slab levels are unavailable.
        return -leg_len

    to_top = abs(slab_top_z - layer_z)
    to_bottom = abs(layer_z - slab_bottom_z)
    # Bottom layer hooks up, top layer hooks down.
    return leg_len if to_bottom <= to_top else -leg_len


def _get_layer_name(layer_z, params):
    """Return Bottom/Top based on Z proximity to slab faces."""
    slab_top_z = params.get('slab_top_z')
    slab_bottom_z = params.get('slab_bottom_z')
    if slab_top_z is None or slab_bottom_z is None:
        return 'Bottom'
    to_top = abs(slab_top_z - layer_z)
    to_bottom = abs(layer_z - slab_bottom_z)
    return 'Bottom' if to_bottom <= to_top else 'Top'


def _find_mark_param(element):
    """Return the first writable Mark parameter on element, or None.

    Tries three approaches in order of reliability in IronPython/pyRevit:
      1. GetParameters('Mark') — avoids overload-resolution issues
      2. LookupParameter('Mark') — fast path for most elements
      3. get_Parameter(BuiltInParameter.ALL_MODEL_MARK) — built-in fallback
    """
    # Approach 1: GetParameters — most robust in IronPython
    try:
        for p in rebar_element_get_parameters(element, 'Mark'):
            if not p.IsReadOnly:
                return p
    except Exception:
        pass
    # Approach 2: LookupParameter
    try:
        p = element.LookupParameter('Mark')
        if p is not None and not p.IsReadOnly:
            return p
    except Exception:
        pass
    # Approach 3: BuiltInParameter
    try:
        p = element.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
        if p is not None and not p.IsReadOnly:
            return p
    except Exception:
        pass
    return None


def rebar_element_get_parameters(element, name):
    """Wrapper around element.GetParameters(name) — handles IronPython list iteration."""
    result = element.GetParameters(name)
    if result is None:
        return []
    try:
        return list(result)
    except Exception:
        return []


def _compute_mark_text(segment, params):
    """Return the mark string for a segment without touching any Revit element."""
    direction  = segment.get('direction', 'X')
    mesh_layer = segment.get('mesh_layer', '')
    if mesh_layer == 'top':
        layer_name = 'Top'
    elif mesh_layer == 'bottom':
        layer_name = 'Bottom'
    else:
        layer_name = _get_layer_name(segment['z'], params)

    if segment.get('is_add_rft'):
        return 'Add {} {}'.format(layer_name, direction)
    return '{} {}'.format(layer_name, direction)


def apply_mark_queue(doc, mark_queue, comment_queue=None):
    """Apply marks and optional comments in a single dedicated transaction.

    Called AFTER all placement transactions have committed so that Revit's
    post-commit shape-registration regeneration cannot reset the marks.
    Elements that no longer exist (deleted by failure preprocessor) are skipped.

    comment_queue : optional list of (ElementId, comment_text) for bars that
                    should carry a Comments annotation (e.g. 'Staggered').
    """
    if not mark_queue and not comment_queue or doc is None:
        return
    t = Transaction(doc, 'Set Rebar Marks')
    try:
        status = t.Start()
    except Exception as e:
        print('[rebar_placer] WARNING: mark transaction could not start: {}. Marks skipped.'.format(e))
        return
    if TransactionStatus is not None and status != TransactionStatus.Started:
        print('[rebar_placer] WARNING: mark transaction did not start (status={}). Marks skipped.'.format(status))
        return
    _configure_fast_transaction(t)
    ok = failed = 0
    comments_ok = 0
    try:
        for eid, mark_text in (mark_queue or []):
            try:
                elem = doc.GetElement(eid)
                if elem is None:
                    continue
                param = _find_mark_param(elem)
                if param is not None:
                    param.Set(mark_text)
                    ok += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
        for eid, comment_text in (comment_queue or []):
            try:
                elem = doc.GetElement(eid)
                if elem is None:
                    continue
                param = _find_comment_param(elem)
                if param is not None:
                    param.Set(comment_text)
                    comments_ok += 1
            except Exception:
                pass
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    print('[rebar_placer] marks set: ok={} failed={} comments={}'.format(ok, failed, comments_ok))


def _find_comment_param(element):
    """Return the first writable Comments parameter on element, or None."""
    try:
        for p in rebar_element_get_parameters(element, 'Comments'):
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


def _set_rebar_mark(rebar, segment, params):
    """Kept for backwards-compat; no longer used in the main placement path.

    Direct in-transaction mark-setting is unreliable when createNewShape=True
    because Revit's shape-registration regeneration at commit time resets the
    Mark parameter.  Use _compute_mark_text + apply_mark_queue instead.
    """
    pass


def place_segment(doc, floor, segment, bar_type, layer_z, params):
    """Place a single straight rebar segment.

    Returns the created Rebar element, or None on failure.
    """
    start     = segment['start']
    end       = segment['end']
    fixed_val = segment['fixed_val']
    direction = segment['direction']

    if direction == 'X':
        p1 = XYZ(start,     fixed_val, layer_z)
        p2 = XYZ(end,       fixed_val, layer_z)
        normal = XYZ(0, 1, 0)  # bar and legs lie in XZ plane
    else:
        p1 = XYZ(fixed_val, start,     layer_z)
        p2 = XYZ(fixed_val, end,       layer_z)
        normal = XYZ(1, 0, 0)  # bar and legs lie in YZ plane

    try:
        start_hook = segment.get('start_hook', False)
        end_hook = segment.get('end_hook', False)
        side_cover = params.get('cover', 0.0)

        # Bar endpoints are already inset from the slab face by cover (done in
        # bar_generator).  No additional cover offset needed here — the vertical
        # hook leg sits exactly at the bar endpoint, which is already inside.
        p1_adj = p1
        p2_adj = p2

        dz = _get_vertical_leg_delta(layer_z, params)
        leg_ft = segment.get('leg_ft', 0.0)

        # For add RFT J-bars, verify the vertical hook leg meets the minimum
        # hook-extension requirement for the chosen bar type (12× bar diameter
        # per ACI 318, minimum 150mm).  If it doesn't, fall back to a straight
        # bar to avoid Revit's "Can't solve Rebar Shape" post-commit error which
        # locks the document and blocks all subsequent transactions.
        if segment.get('is_add_rft') and segment.get('has_hook', False) and leg_ft > 0 and abs(dz) > 0:
            try:
                _dp = (bar_type.LookupParameter('Bar Diameter')
                       or bar_type.LookupParameter('Nominal Diameter'))
                if _dp is not None:
                    _diam_ft = _dp.AsDouble()
                    _min_hook_ft = max(12.0 * _diam_ft, 150.0 / 304.8)
                    if abs(dz) < _min_hook_ft:
                        print('[rebar_placer] add_rft J-bar: dz={:.4f}ft < min_hook={:.4f}ft'
                              ' for bar diam={:.1f}mm — using straight bar'.format(
                                  abs(dz), _min_hook_ft, _diam_ft * 304.8))
                        dz = 0.0   # forces fall-through to straight bar below
            except Exception:
                pass

        if segment.get('is_add_rft'):
            _has_hook = segment.get('has_hook', False)
            print('[rebar_placer] add_rft seg: dir={} has_hook={} leg_ft={:.4f}ft '
                  'dz={:.4f}ft hook_at_max={} -> shape={}'.format(
                      direction,
                      _has_hook,
                      leg_ft,
                      dz,
                      segment.get('hook_at_max', True),
                      'J-bar' if (_has_hook and leg_ft > 0 and abs(dz) > 0) else 'straight',
                  ))

        points = []
        if leg_ft > 0 and abs(dz) > 0 and segment.get('is_add_rft') and segment.get('has_hook', False):
            # J-bar: straight end at origin side, vertical leg + horizontal return at far end.
            # hook_at_max=True  → hook is at p2 (vary_max side); return goes back toward p1.
            # hook_at_max=False → hook is at p1 (vary_min side); return goes back toward p2.
            hook_at_max = segment.get('hook_at_max', True)

            if direction == 'X':
                span = p2_adj.X - p1_adj.X   # always positive
                if span > leg_ft:
                    if hook_at_max:
                        pt_straight = p1_adj
                        pt_hook_top = p2_adj
                        ret_sign    = -1      # return goes in -X (back toward p1)
                    else:
                        pt_straight = p2_adj
                        pt_hook_top = p1_adj
                        ret_sign    = +1      # return goes in +X (back toward p2)
                    pt_hook_bot = XYZ(pt_hook_top.X, pt_hook_top.Y, pt_hook_top.Z + dz)
                    pt_return   = XYZ(pt_hook_top.X + ret_sign * leg_ft,
                                      pt_hook_top.Y,
                                      pt_hook_top.Z + dz)
                    points = [pt_straight, pt_hook_top, pt_hook_bot, pt_return]
            else:  # direction == 'Y'
                span = p2_adj.Y - p1_adj.Y
                if span > leg_ft:
                    if hook_at_max:
                        pt_straight = p1_adj
                        pt_hook_top = p2_adj
                        ret_sign    = -1
                    else:
                        pt_straight = p2_adj
                        pt_hook_top = p1_adj
                        ret_sign    = +1
                    pt_hook_bot = XYZ(pt_hook_top.X, pt_hook_top.Y, pt_hook_top.Z + dz)
                    pt_return   = XYZ(pt_hook_top.X,
                                      pt_hook_top.Y + ret_sign * leg_ft,
                                      pt_hook_top.Z + dz)
                    points = [pt_straight, pt_hook_top, pt_hook_bot, pt_return]
            # if span <= leg_ft: fall through to straight bar below

        if not points:
            if start_hook and abs(dz) > 0:
                points.append(XYZ(p1_adj.X, p1_adj.Y, p1_adj.Z + dz))
            points.append(p1_adj)
            points.append(p2_adj)
            if end_hook and abs(dz) > 0:
                points.append(XYZ(p2_adj.X, p2_adj.Y, p2_adj.Z + dz))

        curves = _build_curve_list(points)
        if curves.Count == 0:
            return None
        # Straight bar: 2 points, no hooks.
        is_straight = (len(points) == 2 and not start_hook and not end_hook
                       and not segment.get('is_add_rft'))
    except Exception:
        return None

    # Straight bars: True/True (match existing straight shape).
    # Bent bars (hooks, J-bars): False/True (always create new shape).
    # False/False is rejected by the Revit API.
    # Any Dmin/shape warnings from bent-bar registration are suppressed by
    # _SilentFailuresPreprocessor so they never block placement.
    use_existing = is_straight
    create_new   = True

    rebar = None
    try:
        rebar = Rebar.CreateFromCurves(
            doc,
            RebarStyle.Standard,
            bar_type,
            None,
            None,
            floor,
            normal,
            curves,
            RebarHookOrientation.Left,
            RebarHookOrientation.Right,
            use_existing,
            create_new,
        )
    except Exception:
        # Retry with a straight bar as safety net.
        try:
            straight_curves = _build_curve_list([p1, p2])
            rebar = Rebar.CreateFromCurves(
                doc,
                RebarStyle.Standard,
                bar_type,
                None,
                None,
                floor,
                normal,
                straight_curves,
                RebarHookOrientation.Left,
                RebarHookOrientation.Right,
                True,
                True,
            )
        except Exception:
            return None

    return rebar


def _quantize(value, tol):
    """Snap value to tolerance grid to reduce floating-point fragmentation."""
    if tol <= 0:
        return round(value, 6)
    return round(round(value / tol) * tol, 6)


def _slice_key(seg, geom_tol):
    """Grouping key so one rebar set represents one geometric slice."""
    return (
        seg['direction'],
        _quantize(seg['z'], max(geom_tol * 0.2, 1e-4)),
        _quantize(seg['start'], geom_tol),
        _quantize(seg['end'], geom_tol),
        bool(seg.get('start_hook', False)),
        bool(seg.get('end_hook', False)),
        bool(seg.get('hook_at_max', True)),   # J-bars with different hook sides differ in shape
    )


def _is_uniform_spacing(values, expected_spacing=None, tol=1e-3):
    """Return (is_uniform, spacing) for sorted coordinates with tolerance."""
    if len(values) < 2:
        return False, 0.0

    diffs = []
    for i in range(1, len(values)):
        d = values[i] - values[i - 1]
        if d <= tol:
            return False, 0.0
        diffs.append(d)

    if expected_spacing is not None and expected_spacing > tol:
        spacing = expected_spacing
    else:
        spacing = sum(diffs) / float(len(diffs))

    if spacing <= tol:
        return False, 0.0

    for d in diffs:
        if abs(d - spacing) > tol:
            return False, 0.0
    return True, spacing


def _split_contiguous_blocks(group, expected_spacing, tol):
    """Split a slice group into contiguous row blocks by spacing."""
    if not group:
        return []
    if len(group) == 1:
        return [group]

    blocks = []
    current = [group[0]]
    for i in range(1, len(group)):
        prev = group[i - 1]['fixed_val']
        curr = group[i]['fixed_val']
        d = curr - prev
        if d <= tol:
            # Degenerate overlap: keep together, uniform check will handle validity.
            current.append(group[i])
            continue

        if expected_spacing is not None and expected_spacing > tol:
            is_contiguous = abs(d - expected_spacing) <= tol
        else:
            is_contiguous = True

        if is_contiguous:
            current.append(group[i])
        else:
            blocks.append(current)
            current = [group[i]]

    blocks.append(current)
    return blocks


_BATCH_SIZE = 200   # groups per transaction commit — keeps each commit fast


def _place_blocks(doc, floor, blocks, bar_type, params,
                  phase_spacing, group_spacing_tol, spacing_input, mark_queue,
                  comment_queue=None):
    """Place a list of blocks (each block = list of segments).

    Appends (ElementId, mark_text) to mark_queue for every bar placed.
    When stagger_splices is True and a bar has splice_end set, also appends
    (ElementId, 'Staggered') to comment_queue so the rebar element carries a
    Comments annotation visible in schedules and properties.

    Marks and comments are NOT set here — they are applied in a separate
    transaction after all placement batches commit so that Revit's
    shape-registration regeneration cannot reset them.

    Returns (placed, failed, set_count).
    """
    placed = failed = set_count = 0
    stagger_splices = params.get('stagger_splices', False)

    def _maybe_queue_comment(eid, seg):
        if comment_queue is not None and stagger_splices and seg.get('splice_end'):
            comment_queue.append((eid, 'Staggered'))

    for block in blocks:
        base_seg = block[0]
        base_rebar = place_segment(
            doc, floor, base_seg, bar_type, base_seg['z'], params
        )
        if base_rebar is None:
            failed += 1
            for seg in block[1:]:
                rb = place_segment(doc, floor, seg, bar_type, seg['z'], params)
                if rb is not None:
                    placed += 1
                    mark_queue.append((rb.Id, _compute_mark_text(seg, params)))
                    _maybe_queue_comment(rb.Id, seg)
                else:
                    failed += 1
            continue

        placed += 1
        mark_queue.append((base_rebar.Id, _compute_mark_text(base_seg, params)))
        _maybe_queue_comment(base_rebar.Id, base_seg)

        if len(block) == 1:
            set_count += 1
            continue

        fixed_vals = [s['fixed_val'] for s in block]
        uniform, spacing = _is_uniform_spacing(
            fixed_vals, expected_spacing=phase_spacing, tol=group_spacing_tol
        )
        if uniform:
            try:
                accessor = base_rebar.GetShapeDrivenAccessor()
                accessor.SetLayoutAsNumberWithSpacing(
                    len(block), spacing, True, True, True
                )
                placed += (len(block) - 1)
                set_count += 1
                continue
            except Exception:
                try:
                    array_length = spacing * (len(block) - 1)
                    accessor = base_rebar.GetShapeDrivenAccessor()
                    accessor.SetLayoutAsMaximumSpacing(
                        spacing, array_length, True, True, True
                    )
                    placed += (len(block) - 1)
                    set_count += 1
                    continue
                except Exception:
                    pass

        for seg in block[1:]:
            rb = place_segment(doc, floor, seg, bar_type, seg['z'], params)
            if rb is not None:
                placed += 1
                mark_queue.append((rb.Id, _compute_mark_text(seg, params)))
                _maybe_queue_comment(rb.Id, seg)
            else:
                failed += 1
        set_count += 1

    return placed, failed, set_count


def place_all_slab_bars(doc, floor, all_segments, bar_type, params):
    """Place bars as slice-based rebar sets, committed in batches.

    Each batch of _BATCH_SIZE groups is placed in its own transaction so that
    Revit's model regeneration is spread across many small commits rather than
    one enormous commit at the end.  Returns (placed_count, failed_count, set_count).
    """
    placed = 0
    failed = 0
    set_count = 0

    cover = params.get('cover', 0.0)
    spacing_input = params.get('spacing', 0.0)
    stagger_splices = params.get('stagger_splices', False)
    geom_tol = max(1e-3, min(0.02, cover * 0.1))
    spacing_tol = max(1e-3, min(0.02, spacing_input * 0.1))

    # --- Group segments into slice keys (pure Python, no Revit API) ---
    grouped = defaultdict(list)
    for seg in all_segments:
        grouped[_slice_key(seg, geom_tol)].append(seg)

    # --- Build all blocks (pure Python) ---
    all_blocks = []          # list of (blocks, phase_spacing, group_spacing_tol)
    for _, group in grouped.items():
        if not group:
            continue

        seg_spacing = group[0].get('spacing_ft')
        if seg_spacing and seg_spacing > 0:
            phase_spacing = seg_spacing
            group_spacing_tol = max(1e-3, min(0.02, seg_spacing * 0.1))
        else:
            phase_spacing = spacing_input if spacing_input > 0 else None
            group_spacing_tol = spacing_tol

        phase_groups = [group]
        if stagger_splices and spacing_input > 0:
            has_even = any((s.get('index', 0) % 2) == 0 for s in group)
            has_odd  = any((s.get('index', 0) % 2) == 1 for s in group)
            if not (has_even and has_odd):
                phase_spacing = (seg_spacing or spacing_input) * 2.0
                group_spacing_tol = max(1e-3, min(0.02, phase_spacing * 0.1))

        for phase_group in phase_groups:
            phase_group.sort(key=lambda s: s['fixed_val'])
            blocks = _split_contiguous_blocks(phase_group, phase_spacing, group_spacing_tol)
            for block in blocks:
                all_blocks.append((block, phase_spacing, group_spacing_tol))

    total_blocks = len(all_blocks)
    print('[rebar_placer] {} groups → {} blocks, placing in batches of {}...'.format(
        len(grouped), total_blocks, _BATCH_SIZE))

    # mark_queue accumulates (ElementId, mark_text) across ALL batches.
    # comment_queue accumulates (ElementId, comment_text) for staggered bars.
    # Both are applied in one final transaction after every shape is registered.
    mark_queue    = []
    comment_queue = []

    # --- Place in batches ---
    for batch_start in range(0, total_blocks, _BATCH_SIZE):
        batch = all_blocks[batch_start: batch_start + _BATCH_SIZE]
        print('[rebar_placer] batch {}-{} of {}...'.format(
            batch_start + 1, batch_start + len(batch), total_blocks))

        t = Transaction(doc, 'Place Flat Slab Rebar')
        try:
            status = t.Start()
        except Exception as e:
            print('[rebar_placer] WARNING: batch could not start ({}). Remaining batches skipped.'.format(e))
            break
        if TransactionStatus is not None and status != TransactionStatus.Started:
            print('[rebar_placer] WARNING: batch did not start (status={}). Remaining batches skipped.'.format(status))
            break
        _configure_fast_transaction(t)
        try:
            for block, phase_spacing, group_spacing_tol in batch:
                p, f, s = _place_blocks(
                    doc, floor, [block], bar_type, params,
                    phase_spacing, group_spacing_tol, spacing_input, mark_queue,
                    comment_queue=comment_queue,
                )
                placed    += p
                failed    += f
                set_count += s
            t.Commit()
        except Exception:
            t.RollBack()
            raise

    # --- Apply marks and comments in a single post-placement transaction ---
    # This runs after ALL shapes are registered so Revit's post-commit
    # regeneration cannot reset the marks.
    print('[rebar_placer] applying {} marks, {} comments...'.format(
        len(mark_queue), len(comment_queue)))
    apply_mark_queue(doc, mark_queue, comment_queue=comment_queue)

    return placed, failed, set_count
