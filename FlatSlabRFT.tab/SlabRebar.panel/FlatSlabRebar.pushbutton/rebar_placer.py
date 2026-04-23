# -*- coding: utf-8 -*-
"""Place main slab rebar segments in Revit."""
from __future__ import print_function

import clr
clr.AddReference('System')
from System.Collections.Generic import List
from collections import defaultdict

from Autodesk.Revit.DB import Line, XYZ, Curve, Transaction, BuiltInParameter
from Autodesk.Revit.DB.Structure import Rebar, RebarStyle, RebarHookOrientation


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


def _set_rebar_mark(rebar, segment, params):
    """Set Mark as '<Bottom|Top> <X|Y>'."""
    if rebar is None:
        return

    layer_name = _get_layer_name(segment['z'], params)
    direction = segment.get('direction', 'X')
    mark_text = '{} {}'.format(layer_name, direction)

    try:
        mark_param = rebar.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
        if mark_param is None:
            mark_param = rebar.LookupParameter('Mark')
        if mark_param is not None and (not mark_param.IsReadOnly):
            mark_param.Set(mark_text)
    except Exception:
        pass


def place_segment(doc, floor, segment, bar_type, hook_type, layer_z, params):
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

        # Keep left/right (start/end) cover at hooked ends.
        p1_adj = p1
        p2_adj = p2
        if direction == 'X':
            start_x = p1.X + (side_cover if start_hook else 0.0)
            end_x = p2.X - (side_cover if end_hook else 0.0)
            if end_x - start_x <= 1e-4:
                # Too short after cover offsets: keep bar, drop hooks.
                start_hook = False
                end_hook = False
                start_x = p1.X
                end_x = p2.X
            p1_adj = XYZ(start_x, p1.Y, p1.Z)
            p2_adj = XYZ(end_x, p2.Y, p2.Z)
        else:
            start_y = p1.Y + (side_cover if start_hook else 0.0)
            end_y = p2.Y - (side_cover if end_hook else 0.0)
            if end_y - start_y <= 1e-4:
                # Too short after cover offsets: keep bar, drop hooks.
                start_hook = False
                end_hook = False
                start_y = p1.Y
                end_y = p2.Y
            p1_adj = XYZ(p1.X, start_y, p1.Z)
            p2_adj = XYZ(p2.X, end_y, p2.Z)

        dz = _get_vertical_leg_delta(layer_z, params)

        points = []
        if start_hook and abs(dz) > 0:
            points.append(XYZ(p1_adj.X, p1_adj.Y, p1_adj.Z + dz))
        points.append(p1_adj)
        points.append(p2_adj)
        if end_hook and abs(dz) > 0:
            points.append(XYZ(p2_adj.X, p2_adj.Y, p2_adj.Z + dz))

        curves = _build_curve_list(points)
        if curves.Count == 0:
            return None
    except Exception:
        return None

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
            True,   # useExistingShapeIfPossible
            True,   # createNewShape
        )
        _set_rebar_mark(rebar, segment, params)
        return rebar
    except Exception:
        # Retry without hooks (safety net for shape-matching failures)
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
            _set_rebar_mark(rebar, segment, params)
            return rebar
        except Exception:
            return None


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


def place_all_slab_bars(doc, floor, all_segments, bar_type, hook_type, params):
    """Place bars as slice-based rebar sets where possible.

    Returns (placed_count, failed_count, set_count).
    """
    placed = 0
    failed = 0
    set_count = 0
    t_place = Transaction(doc, 'Place Flat Slab Rebar')
    t_place.Start()
    try:
        grouped = defaultdict(list)
        cover = params.get('cover', 0.0)
        spacing_input = params.get('spacing', 0.0)
        stagger_splices = params.get('stagger_splices', False)
        geom_tol = max(1e-3, min(0.02, cover * 0.1))
        spacing_tol = max(1e-3, min(0.02, spacing_input * 0.1))
        for seg in all_segments:
            grouped[_slice_key(seg, geom_tol)].append(seg)

        for _, group in grouped.items():
            if not group:
                continue

            # With staggering enabled, rows split into two interleaved shape families.
            # Build sets per parity to keep same splice pattern per set.
            phase_groups = [group]
            phase_spacing = spacing_input if spacing_input > 0 else None
            if stagger_splices:
                even_rows = [s for s in group if (s.get('index', 0) % 2) == 0]
                odd_rows = [s for s in group if (s.get('index', 0) % 2) == 1]
                phase_groups = []
                if even_rows:
                    phase_groups.append(even_rows)
                if odd_rows:
                    phase_groups.append(odd_rows)
                if spacing_input > 0:
                    phase_spacing = spacing_input * 2.0

            blocks = []
            for phase_group in phase_groups:
                phase_group.sort(key=lambda s: s['fixed_val'])
                blocks.extend(
                    _split_contiguous_blocks(
                        phase_group,
                        phase_spacing,
                        spacing_tol
                    )
                )

            for block in blocks:
                base_seg = block[0]
                base_rebar = place_segment(
                    doc, floor, base_seg, bar_type, hook_type, base_seg['z'], params
                )
                if base_rebar is None:
                    failed += len(block)
                    continue

                placed += 1

                if len(block) == 1:
                    set_count += 1
                    continue

                fixed_vals = [s['fixed_val'] for s in block]
                uniform, spacing = _is_uniform_spacing(
                    fixed_vals,
                    expected_spacing=phase_spacing,
                    tol=spacing_tol
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
                        # Fallback API route for cases where NumberWithSpacing fails.
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
                    rb = place_segment(doc, floor, seg, bar_type, hook_type, seg['z'], params)
                    if rb is not None:
                        placed += 1
                    else:
                        failed += 1
                set_count += 1
        t_place.Commit()
    except Exception:
        t_place.RollBack()
        raise

    return placed, failed, set_count
