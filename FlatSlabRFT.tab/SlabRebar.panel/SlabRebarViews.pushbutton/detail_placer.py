# -*- coding: utf-8 -*-
"""Place bending details, distribution dimensions, and rebar tags."""
from __future__ import print_function

import time

from Autodesk.Revit.DB import (
    FilteredElementCollector, BuiltInParameter,
    Line, XYZ, IndependentTag,
    TagOrientation, Reference, ReferenceArray,
    ElementTransformUtils
)
from Autodesk.Revit.DB.Structure import Rebar, MultiplanarOption

# X bars run along X → distributed along Y axis; Y bars → distributed along X axis
X_MARKS = {'Bottom X', 'Top X', 'Add Bottom X', 'Add Top X', 'Drop Panel X'}
VOID_ADD_MARK_KEY   = '__VOID_ADD__'
VOID_ADD_MARK_VALUE = 'Void Add RFT'


def _get_mark(rebar_elem):
    param = rebar_elem.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
    if param is None:
        return ''
    return param.AsString() or ''


def _get_comments(rebar_elem):
    """Return instance Comments text, or empty string."""
    try:
        p = rebar_elem.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if p is not None:
            return p.AsString() or ''
    except Exception:
        pass
    return ''


def get_representative_bar(doc, mark_value):
    """Return the first Rebar element with the given mark, or None."""
    collector = FilteredElementCollector(doc).OfClass(Rebar)
    for rb in collector:
        if _get_mark(rb) == mark_value:
            return rb
    return None


def _get_all_bars(doc, mark_value, view=None):
    """Return all Rebar elements with the given mark.

    If view is provided, only elements visible in that view are returned —
    this matches what Revit's filter shows and avoids placing details for
    bars on other levels or hidden by view settings.
    """
    if view is not None:
        collector = FilteredElementCollector(doc, view.Id).OfClass(Rebar)
    else:
        collector = FilteredElementCollector(doc).OfClass(Rebar)
    return [rb for rb in collector if _get_mark(rb) == mark_value]


def _bar_centerline_curves(rebar_elem):
    """Return the centerline curves of a rebar element."""
    try:
        curves = rebar_elem.GetCenterlineCurves(
            False, False, False,
            MultiplanarOption.IncludeAllMultiplanarCurves, 0
        )
        return list(curves) if curves else []
    except Exception:
        return []


def _bar_midpoint(rebar_elem):
    """Return midpoint XYZ of the first centerline curve, or bbox centre."""
    curves = _bar_centerline_curves(rebar_elem)
    if curves:
        try:
            c = curves[0]
            return c.Evaluate(0.5, True)
        except Exception:
            pass
    try:
        bb = rebar_elem.get_BoundingBox(None)
        if bb is not None:
            return XYZ(
                (bb.Min.X + bb.Max.X) / 2.0,
                (bb.Min.Y + bb.Max.Y) / 2.0,
                (bb.Min.Z + bb.Max.Z) / 2.0
            )
    except Exception:
        pass
    return None


def _all_bars_bbox(bars):
    """Return a combined bounding box-like object for all bars, or None."""
    min_x = min_y = min_z = None
    max_x = max_y = max_z = None

    for bar in bars or []:
        try:
            bb = bar.get_BoundingBox(None)
        except Exception:
            bb = None
        if bb is None:
            continue

        if min_x is None:
            min_x, min_y, min_z = bb.Min.X, bb.Min.Y, bb.Min.Z
            max_x, max_y, max_z = bb.Max.X, bb.Max.Y, bb.Max.Z
        else:
            min_x = min(min_x, bb.Min.X)
            min_y = min(min_y, bb.Min.Y)
            min_z = min(min_z, bb.Min.Z)
            max_x = max(max_x, bb.Max.X)
            max_y = max(max_y, bb.Max.Y)
            max_z = max(max_z, bb.Max.Z)

    if min_x is None:
        return None

    class _CombinedBBox(object):
        pass

    combined = _CombinedBBox()
    combined.Min = XYZ(min_x, min_y, min_z)
    combined.Max = XYZ(max_x, max_y, max_z)
    return combined


def _bar_direction(rebar_elem):
    """Return normalised direction XYZ of the first centerline curve."""
    curves = _bar_centerline_curves(rebar_elem)
    if curves:
        try:
            c = curves[0]
            d = c.GetEndPoint(1) - c.GetEndPoint(0)
            if d.GetLength() > 1e-6:
                return d.Normalize()
        except Exception:
            pass
    return XYZ(1, 0, 0)


def _curve_signature_xy(rebar_elem):
    """Return XY-only curve signature used to collapse top/bottom twins."""
    curves = _bar_centerline_curves(rebar_elem)
    if not curves:
        return None
    try:
        c = max(curves, key=lambda x: x.Length)
        p0 = c.GetEndPoint(0)
        p1 = c.GetEndPoint(1)
        a = (round(p0.X, 4), round(p0.Y, 4))
        b = (round(p1.X, 4), round(p1.Y, 4))
        if b < a:
            a, b = b, a
        return (a, b, round(c.Length, 4))
    except Exception:
        return None


def _bar_mid_z(rebar_elem):
    """Return representative bar Z for layer selection (higher = top)."""
    p = _bar_midpoint(rebar_elem)
    if p is not None:
        return p.Z
    try:
        bb = rebar_elem.get_BoundingBox(None)
        if bb is not None:
            return (bb.Min.Z + bb.Max.Z) / 2.0
    except Exception:
        pass
    return 0.0


def _dedupe_void_keep_top(bars):
    """Collapse identical top/bottom void bars and keep only top-layer bars."""
    chosen = {}
    fallback = []
    for bar in bars:
        sig = _curve_signature_xy(bar)
        if sig is None:
            fallback.append(bar)
            continue
        z = _bar_mid_z(bar)
        prev = chosen.get(sig)
        if prev is None or z > prev[0]:
            chosen[sig] = (z, bar)
    return [v[1] for v in chosen.values()] + fallback


def _get_rebar_bending_detail_type_by_name(doc, type_name):
    """Return RebarBendingDetailType by robust name matching, or None."""
    if not type_name:
        return None
    wanted = ''.join(str(type_name).strip().lower().split())
    try:
        from Autodesk.Revit.DB.Structure import RebarBendingDetailType
        for t in FilteredElementCollector(doc).OfClass(RebarBendingDetailType):
            try:
                nm = (getattr(t, 'Name', '') or '').strip()
                if ''.join(nm.lower().split()) == wanted:
                    return t
                p = t.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
                pnm = (p.AsString() if p is not None else '') or ''
                if ''.join(pnm.strip().lower().split()) == wanted:
                    return t
            except Exception:
                continue
    except Exception:
        pass
    return None


def _list_rebar_bending_detail_type_names(doc):
    """Return all available bending detail type names for diagnostics."""
    names = []
    try:
        from Autodesk.Revit.DB.Structure import RebarBendingDetailType
        for t in FilteredElementCollector(doc).OfClass(RebarBendingDetailType):
            try:
                nm = (getattr(t, 'Name', '') or '').strip()
                if nm:
                    names.append(nm)
                    continue
                p = t.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
                pnm = (p.AsString() if p is not None else '') or ''
                if pnm:
                    names.append(pnm.strip())
            except Exception:
                continue
    except Exception:
        pass
    return names


def _void_group_key(bar):
    """Group key for void bars to select one tagged representative per set."""
    curves = _bar_centerline_curves(bar)
    if not curves:
        return ('fallback', bar.Id.IntegerValue)
    try:
        c = max(curves, key=lambda x: x.Length)
        p0 = c.GetEndPoint(0)
        p1 = c.GetEndPoint(1)
        dx = p1.X - p0.X
        dy = p1.Y - p0.Y
        is_diag = (abs(dx) > 1e-4 and abs(dy) > 1e-4)
        orient = 'diag' if is_diag else 'orth'
        length = round(c.Length, 4)
    except Exception:
        orient = 'unk'
        length = 0.0
    try:
        type_id = bar.GetTypeId().IntegerValue
    except Exception:
        type_id = -1
    comments = (_get_comments(bar) or '').strip().lower()
    return (type_id, comments, orient, length)


def _void_rep_key(bar):
    """Representative grouping for void detail view (target: up to 4 details).

    Groups by plan category only:
      Trimmer X, Trimmer Y, Diagonal slope +, Diagonal slope -
    """
    curves = _bar_centerline_curves(bar)
    if not curves:
        return ('fallback', bar.Id.IntegerValue)
    try:
        c = max(curves, key=lambda x: x.Length)
        p0 = c.GetEndPoint(0)
        p1 = c.GetEndPoint(1)
        dx = p1.X - p0.X
        dy = p1.Y - p0.Y
    except Exception:
        return ('fallback', bar.Id.IntegerValue)

    cm = (_get_comments(bar) or '').strip().lower()
    is_diag = ('diagonal' in cm) or (abs(dx) > 1e-4 and abs(dy) > 1e-4)
    if is_diag:
        slope = 'pos' if (dx * dy) >= 0 else 'neg'
        return ('diag', slope)

    axis = 'X' if abs(dx) >= abs(dy) else 'Y'
    return ('trim', axis)


def _element_id_sort_value(element):
    """Return a stable sortable id value for Revit elements and test stubs."""
    try:
        return (0, int(element.Id.IntegerValue))
    except Exception:
        pass
    try:
        return (0, int(element.Id))
    except Exception:
        pass
    return (1, str(getattr(element, 'Id', '')))


def _void_detail_sort_key(bar):
    """Sort void bars by model location, then by id for repeatable output."""
    try:
        bb = bar.get_BoundingBox(None)
    except Exception:
        bb = None

    if bb is not None:
        return (
            round(bb.Min.X, 4), round(bb.Min.Y, 4), round(bb.Min.Z, 4),
            round(bb.Max.X, 4), round(bb.Max.Y, 4), round(bb.Max.Z, 4),
            _element_id_sort_value(bar),
        )

    p = _bar_midpoint(bar)
    if p is not None:
        return (
            round(p.X, 4), round(p.Y, 4), round(p.Z, 4),
            round(p.X, 4), round(p.Y, 4), round(p.Z, 4),
            _element_id_sort_value(bar),
        )

    return (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, _element_id_sort_value(bar))


# ---------------------------------------------------------------------------
# Bending detail
# ---------------------------------------------------------------------------

def _get_rebar_bending_detail_type(doc):
    """Return the first RebarBendingDetailType in the document, or None."""
    try:
        from Autodesk.Revit.DB.Structure import RebarBendingDetailType
        types = FilteredElementCollector(doc).OfClass(RebarBendingDetailType).ToElements()
        if types and len(types) > 0:
            return types[0]
    except Exception:
        pass
    return None


def _direction_from_mark(mark_value):
    """Return bar run direction XYZ from mark string ending in X or Y."""
    if mark_value and mark_value.strip().endswith('Y'):
        return XYZ(0, 1, 0)
    return XYZ(1, 0, 0)




def _detail_origin_from_curves(curves, rebar_elem):
    """Return the midpoint of the longest centerline curve as the detail origin.

    Accepts pre-computed curves so callers can reuse them without extra API calls.
    Falls back to bar midpoint if curves are unavailable.
    """
    if curves:
        longest = max(curves, key=lambda c: c.Length)
        p0 = longest.GetEndPoint(0)
        p1 = longest.GetEndPoint(1)
        return XYZ(
            (p0.X + p1.X) / 2.0,
            (p0.Y + p1.Y) / 2.0,
            (p0.Z + p1.Z) / 2.0,
        )
    return _bar_midpoint(rebar_elem)


def _void_detail_center_move_vector(rebar_elem):
    """Return a move vector that centers a void detail over its rebar set."""
    if _rebar_qty(rebar_elem) <= 1:
        return None

    curves = _bar_centerline_curves(rebar_elem)
    if not curves:
        return None

    try:
        bb = rebar_elem.get_BoundingBox(None)
    except Exception:
        bb = None
    if bb is None:
        return None

    try:
        c = max(curves, key=lambda x: x.Length)
        p0 = c.GetEndPoint(0)
        p1 = c.GetEndPoint(1)
        origin = XYZ(
            (p0.X + p1.X) / 2.0,
            (p0.Y + p1.Y) / 2.0,
            (p0.Z + p1.Z) / 2.0,
        )
        center = XYZ(
            (bb.Min.X + bb.Max.X) / 2.0,
            (bb.Min.Y + bb.Max.Y) / 2.0,
            (bb.Min.Z + bb.Max.Z) / 2.0,
        )
        dx = p1.X - p0.X
        dy = p1.Y - p0.Y

        # Orthogonal trimmers move only across the set rows; diagonals use the
        # full XY delta because their distribution axis is oblique in plan.
        if abs(dx) > 1e-4 and abs(dy) > 1e-4:
            vec = XYZ(center.X - origin.X, center.Y - origin.Y, 0.0)
        elif abs(dx) >= abs(dy):
            vec = XYZ(0.0, center.Y - origin.Y, 0.0)
        else:
            vec = XYZ(center.X - origin.X, 0.0, 0.0)

        if vec.GetLength() < 1e-6:
            return None
        return vec
    except Exception:
        return None


def place_bending_detail(doc, view, rebar_element, mark_value, detail_type, bar_index=0, move_vector=None):
    """Place a RebarBendingDetail at the bar's location.

    detail_type must be pre-fetched once outside the loop to avoid a
    FilteredElementCollector query per bar.

    Returns the created detail element, or None on failure.
    """
    try:
        from Autodesk.Revit.DB.Structure import RebarBendingDetail

        if detail_type is None:
            return None

        # Compute curves once; reuse for origin calculation.
        curves = _bar_centerline_curves(rebar_element)
        origin = _detail_origin_from_curves(curves, rebar_element)
        if origin is None:
            return None

        scale = 1.0

        _first_err = None
        try:
            detail = RebarBendingDetail.Create(
                doc, view.Id, rebar_element.Id,
                bar_index, detail_type, origin, scale
            )
        except Exception as _e1:
            _first_err = _e1
            try:
                detail = RebarBendingDetail.Create(
                    doc, view.Id, rebar_element.Id,
                    bar_index, detail_type.Id, origin, scale
                )
                _first_err = None
            except Exception as _e2:
                raise Exception('attempt1={} | attempt2={}'.format(_e1, _e2))

        if detail is None:
            return None

        # Enable Align to Bar — Revit positions the symbol on the actual bar geometry.
        try:
            p = detail.LookupParameter('Align to Bar')
            if p is not None and not p.IsReadOnly:
                p.Set(1)
        except Exception:
            pass

        # Force angle to 0° — Align to Bar sets ~57°, this corrects it.
        try:
            p_angle = detail.LookupParameter('Angle')
            if p_angle is not None and not p_angle.IsReadOnly:
                p_angle.Set(0.0)
        except Exception:
            pass

        # Tag position = Top (0); tag alignment = Rebar Shape Family (0), so
        # labels follow the detail instead of floating independently in view.
        try:
            p_tag_pos = detail.LookupParameter('Tag Position')
            if p_tag_pos is not None and not p_tag_pos.IsReadOnly:
                p_tag_pos.Set(0)   # 0 = Top
        except Exception:
            pass
        try:
            p_tag_align = detail.LookupParameter('Tag Alignment')
            if p_tag_align is not None and not p_tag_align.IsReadOnly:
                p_tag_align.Set(0)   # 0 = Rebar Shape Family
        except Exception:
            pass

        # Reduce tag offset so the tag sits close to the bar line.
        try:
            p_tag_offset = detail.LookupParameter('Tag Offset')
            if p_tag_offset is not None and not p_tag_offset.IsReadOnly:
                p_tag_offset.Set(2.0 / 304.8)   # 2 mm in feet
        except Exception:
            pass

        # Shift the detail along the distribution axis to the 1/4 position.
        if move_vector is not None:
            try:
                ElementTransformUtils.MoveElement(doc, detail.Id, move_vector)
            except Exception as e:
                print('Warning: could not move bending detail: {}'.format(e))

        return detail
    except Exception as e:
        print('Warning: bending detail placement failed: {}'.format(e))
        return None


# ---------------------------------------------------------------------------
# Distribution dimension
# ---------------------------------------------------------------------------

def _get_rebar_zone_extent(rebar_elem, dist_axis):
    """Return (zone_min, zone_max, perp_coord, z, axis, count) spanning the rebar set.

    zone_min/max are the bounding box extents in the distribution direction.
    perp_coord is 1/4 of the bar length from the bar start (bar direction).
    Returns None if bbox unavailable or span is zero (single bar).
    """
    bb = rebar_elem.get_BoundingBox(None)
    if bb is None:
        return None
    count_param = rebar_elem.get_Parameter(BuiltInParameter.REBAR_ELEM_QUANTITY_OF_BARS)
    count = int(count_param.AsInteger()) if count_param is not None else 1
    z = (bb.Min.Z + bb.Max.Z) / 2.0
    if dist_axis == 'Y':
        zone_min = bb.Min.Y
        zone_max = bb.Max.Y
        if zone_max - zone_min < 1e-6:
            return None
        # bar runs along X — position dimension at 1/4 of bar length from bar start
        perp = bb.Min.X + (bb.Max.X - bb.Min.X) / 4.0
        return zone_min, zone_max, perp, z, 'Y', count
    else:
        zone_min = bb.Min.X
        zone_max = bb.Max.X
        if zone_max - zone_min < 1e-6:
            return None
        # bar runs along Y — position dimension at 1/4 of bar length from bar start
        perp = bb.Min.Y + (bb.Max.Y - bb.Min.Y) / 4.0
        return zone_min, zone_max, perp, z, 'X', count


def place_distribution_dimension(doc, view, rebar_elem, zone_extent):
    """Create a Revit Dimension spanning the rebar set's full slice zone.

    Creates two tiny DetailLine elements at the zone boundaries as reference
    anchors (rebar set geometry refs are not accessible via the Revit API).
    """
    try:
        zone_min, zone_max, perp, z, axis = zone_extent[:5]
        span_ft = zone_max - zone_min
        if span_ft < 1e-6:
            return None

        tiny = 5.0 / 304.8  # 5 mm in feet — anchor line half-width

        def make_anchor(coord):
            """Create a tiny detail line perpendicular to the dim direction at coord."""
            if axis == 'Y':
                p0 = XYZ(perp - tiny, coord, z)
                p1 = XYZ(perp + tiny, coord, z)
            else:
                p0 = XYZ(coord, perp - tiny, z)
                p1 = XYZ(coord, perp + tiny, z)
            dl = doc.Create.NewDetailCurve(view, Line.CreateBound(p0, p1))
            return dl.GeometryCurve.Reference

        ref1 = make_anchor(zone_min)
        ref2 = make_anchor(zone_max)

        refs = ReferenceArray()
        refs.Append(ref1)
        refs.Append(ref2)

        if axis == 'Y':
            dim_line = Line.CreateBound(XYZ(perp, zone_min, z), XYZ(perp, zone_max, z))
        else:
            dim_line = Line.CreateBound(XYZ(zone_min, perp, z), XYZ(zone_max, perp, z))

        return doc.Create.NewDimension(view, dim_line, refs)

    except Exception as e:
        print('Warning: dimension placement failed: {}'.format(e))
        return None


# ---------------------------------------------------------------------------
# Donut marker
# ---------------------------------------------------------------------------

def _make_circle_loop(center, radius):
    """Return a CurveLoop that is a full circle (two 180-degree arcs)."""
    import math
    from Autodesk.Revit.DB import Arc, CurveLoop
    xAxis = XYZ(1, 0, 0)
    yAxis = XYZ(0, 1, 0)
    arc1 = Arc.Create(center, radius, 0.0,     math.pi,           xAxis, yAxis)
    arc2 = Arc.Create(center, radius, math.pi, 2.0 * math.pi,     xAxis, yAxis)
    loop = CurveLoop()
    loop.Append(arc1)
    loop.Append(arc2)
    return loop


def place_donut(doc, view, center, outer_r, filled_region_type=None):
    """Place a solid filled circle at center.

    filled_region_type: pre-fetched FilledRegionType to avoid a collector
                        scan on every call; falls back to a fresh scan if None.
    """
    from Autodesk.Revit.DB import FilledRegion, FilledRegionType
    try:
        frt = filled_region_type or FilteredElementCollector(doc).OfClass(FilledRegionType).FirstElement()
        if frt is None:
            return None
        outer_loop = _make_circle_loop(center, outer_r)
        return FilledRegion.Create(doc, frt.Id, view.Id, [outer_loop])
    except Exception as e:
        print('Warning: donut placement failed: {}'.format(e))
        return None


# ---------------------------------------------------------------------------
# Rebar tag
# ---------------------------------------------------------------------------

def place_rebar_tag(doc, view, rebar_element, tag_family_symbol):
    """Place a rebar tag on rebar_element in view.

    Caller must pre-activate tag_family_symbol before entering the loop.
    """
    if tag_family_symbol is None:
        return None
    try:
        loc_pt = _bar_midpoint(rebar_element)
        if loc_pt is None:
            return None

        tag = IndependentTag.Create(
            doc,
            tag_family_symbol.Id,
            view.Id,
            Reference(rebar_element),
            False,
            TagOrientation.Horizontal,
            loc_pt
        )
        return tag
    except Exception as e:
        print('Warning: tag placement failed: {}'.format(e))
        return None


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


def _rebar_qty(bar):
    """Return the number of bars in a rebar element (1 for individual bars)."""
    try:
        p = bar.get_Parameter(BuiltInParameter.REBAR_ELEM_QUANTITY_OF_BARS)
        if p is not None:
            return int(p.AsInteger())
    except Exception:
        pass
    return 1


def _rebar_qty(bar):
    """Return the number of bars in a rebar element (1 for individual bars)."""
    try:
        p = bar.get_Parameter(BuiltInParameter.REBAR_ELEM_QUANTITY_OF_BARS)
        if p is not None:
            return int(p.AsInteger())
    except Exception:
        pass
    return 1


def _annotate_one_set(doc, view, bar, mark_value, detail_type, dist_axis, outer_r,
                      filled_region_type=None):
    """Place bending detail + extension line + circle for one rebar set element.

    bar       — representative Rebar element for this set
    Returns (detail_placed, dim_placed, donut_placed).
    """
    # Compute zone first so the deterministic move_vector is known before
    # the bending detail is placed.  AlignToBar always anchors to bar 0 at
    # zone_min; sliding by span/3 moves the detail to the donut position.
    zone = _get_rebar_zone_extent(bar, dist_axis)
    donut_center = None
    move_vec     = None
    if zone is not None:
        zone_min, zone_max, perp, z_dim, axis, count = zone
        span  = zone_max - zone_min
        third = zone_min + span / 3.0
        donut_center = XYZ(perp, third, z_dim) if axis == 'Y' else XYZ(third, perp, z_dim)
        if span > 1e-6:
            move_vec = XYZ(0.0, span / 3.0, 0.0) if axis == 'Y' else XYZ(span / 3.0, 0.0, 0.0)

    bd = place_bending_detail(doc, view, bar, mark_value, detail_type,
                              bar_index=0, move_vector=move_vec)

    dim = dn = None
    if zone is not None:
        dim = place_distribution_dimension(doc, view, bar, zone)
        dn  = place_donut(doc, view, donut_center, outer_r,
                          filled_region_type=filled_region_type)

    return bd is not None, dim is not None, dn is not None


def place_all_details(doc, views_dict, tag_family_symbol):
    """Place bending detail + extension line + circle per rebar set in each view.

    Strategy:
    - Rebar SET elements (REBAR_ELEM_QUANTITY_OF_BARS > 1): one annotation each.
      These are the uniform-distribution groups created by SetLayoutAsNumberWithSpacing.
    - Individual bar elements (qty = 1): all individual bars sharing a mark are
      treated as one logical group; they receive a single combined annotation
      using the bounding box of the whole group.
    - ONE rebar tag placed on the first/representative bar per mark.
    """
    from Autodesk.Revit.DB import FilledRegionType

    detail_type = _get_rebar_bending_detail_type(doc)
    if detail_type is None:
        print('[detail_placer] Warning: no RebarBendingDetailType — bending details skipped.')

    # Cache FilledRegionType once — avoids a collector scan per donut call.
    frt_cache = FilteredElementCollector(doc).OfClass(FilledRegionType).FirstElement()

    t_regen = time.time()
    if tag_family_symbol is not None and not tag_family_symbol.IsActive:
        tag_family_symbol.Activate()
        doc.Regenerate()
        print('[detail_placer] tag symbol activate+regen: {:.2f}s'.format(time.time() - t_regen))

    # ONE doc-scoped collector for all rebar, grouped by mark.
    t_collect = time.time()
    wanted_marks = set()
    for mark_key in views_dict.keys():
        if mark_key == VOID_ADD_MARK_KEY:
            wanted_marks.add(VOID_ADD_MARK_VALUE)
        else:
            wanted_marks.add(mark_key)
    bars_by_mark = {}
    total_rebar_scanned = 0
    for rb in FilteredElementCollector(doc).OfClass(Rebar):
        total_rebar_scanned += 1
        mark = _get_mark(rb)
        if mark in wanted_marks:
            bars_by_mark.setdefault(mark, []).append(rb)
    print('[detail_placer] rebar collector: scanned={} matched={} marks  {:.2f}s'.format(
        total_rebar_scanned,
        sum(len(v) for v in bars_by_mark.values()),
        time.time() - t_collect,
    ))

    skipped = []
    for mark_value, view in views_dict.items():
        t_mark = time.time()
        print('[detail_placer] --- mark: {!r} ---'.format(mark_value))

        lookup_mark = VOID_ADD_MARK_VALUE if mark_value == VOID_ADD_MARK_KEY else mark_value
        all_bars = bars_by_mark.get(lookup_mark, [])
        if not all_bars:
            print('[detail_placer]   No rebar — skipping.')
            skipped.append(mark_value)
            continue

        if mark_value == VOID_ADD_MARK_KEY:
            bars_for_detail = sorted(
                _dedupe_void_keep_top(all_bars),
                key=_void_detail_sort_key
            )
            print('[detail_placer]   void bars={} top_detail_sets={}'.format(
                len(all_bars), len(bars_for_detail)))

            tagged_type = _get_rebar_bending_detail_type_by_name(
                doc, 'Bending Detail for void'
            ) or detail_type
            if tagged_type is detail_type:
                _names = _list_rebar_bending_detail_type_names(doc)
                if _names:
                    print('[detail_placer] available bending detail types: {}'.format(', '.join(_names)))

            total_attempted = total_details = total_failed = 0
            for bar in bars_for_detail:
                total_attempted += 1
                move_vec = _void_detail_center_move_vector(bar)
                bd = place_bending_detail(
                    doc, view, bar, lookup_mark, tagged_type,
                    bar_index=0, move_vector=move_vec
                )
                if bd is not None:
                    total_details += 1
                else:
                    total_failed += 1

            print('[detail_placer]   independent tag: skipped for void view')
            print('[detail_placer]   mark total {:.2f}s  attempted={} details={} dims=0 donuts=0 failed={}'.format(
                time.time() - t_mark, total_attempted, total_details, total_failed))
            continue

        dist_axis  = 'Y' if mark_value in X_MARKS else 'X'
        view_scale = getattr(view, 'Scale', 50)
        outer_r    = 1.0 / 304.8 * view_scale

        rebar_sets      = [b for b in all_bars if _rebar_qty(b) > 1]
        individual_bars = [b for b in all_bars if _rebar_qty(b) == 1]
        print('[detail_placer]   bars={} sets={} individual={}'.format(
            len(all_bars), len(rebar_sets), len(individual_bars)))

        total_details = total_dims = total_donuts = total_failed = 0

        # ── One annotation per rebar element (SET or individual) ─────────────
        for bar in rebar_sets + individual_bars:
            t_bar = time.time()
            bd_ok, dim_ok, dn_ok = _annotate_one_set(
                doc, view, bar, mark_value, detail_type, dist_axis, outer_r,
                filled_region_type=frt_cache,
            )
            print('[detail_placer]   bar(qty={}) annotation: {:.0f}ms  bd={} dim={} dn={}'.format(
                _rebar_qty(bar), (time.time() - t_bar) * 1000, bd_ok, dim_ok, dn_ok))
            if bd_ok:  total_details += 1
            else:      total_failed  += 1
            if dim_ok: total_dims    += 1
            if dn_ok:  total_donuts  += 1

        # ── ONE tag on the first bar ───────────────────────────────────────
        if tag_family_symbol is None:
            print('[detail_placer]   Tag: skipped (no family selected)')
        else:
            t_tag = time.time()
            tag = place_rebar_tag(doc, view, all_bars[0], tag_family_symbol)
            print('[detail_placer]   place_rebar_tag: {:.0f}ms  ok={}'.format(
                (time.time() - t_tag) * 1000, tag is not None))

        print('[detail_placer]   mark total {:.2f}s  details={} dims={} donuts={} failed={}'.format(
            time.time() - t_mark, total_details, total_dims, total_donuts, total_failed))

    return skipped
