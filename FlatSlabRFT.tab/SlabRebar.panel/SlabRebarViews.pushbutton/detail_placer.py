# -*- coding: utf-8 -*-
"""Place bending details, distribution dimensions, and rebar tags."""
from __future__ import print_function

from Autodesk.Revit.DB import (
    FilteredElementCollector, BuiltInParameter,
    Line, XYZ, IndependentTag,
    TagOrientation, Reference, ReferenceArray,
    ElementTransformUtils
)
from Autodesk.Revit.DB.Structure import Rebar, MultiplanarOption

# X bars run along X → distributed along Y axis; Y bars → distributed along X axis
X_MARKS = {'Bottom X', 'Top X', 'Add Bottom X', 'Add Top X', 'Drop Panel X'}


def _get_mark(rebar_elem):
    param = rebar_elem.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
    if param is None:
        return ''
    return param.AsString() or ''


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


def _all_bars_bbox(bars):
    """Return a combined bounding box (SimpleNamespace with Min/Max XYZ) for a list of bars.

    Used to place the single bending detail outside the ENTIRE bar zone, not
    just one element's bbox.
    """
    min_x = min_y = float('inf')
    max_x = max_y = float('-inf')
    z = 0.0
    found = False
    for rb in bars:
        bb = rb.get_BoundingBox(None)
        if bb is None:
            continue
        if bb.Min.X < min_x:
            min_x = bb.Min.X
        if bb.Min.Y < min_y:
            min_y = bb.Min.Y
        if bb.Max.X > max_x:
            max_x = bb.Max.X
        if bb.Max.Y > max_y:
            max_y = bb.Max.Y
        z = (bb.Min.Z + bb.Max.Z) / 2.0
        found = True
    if not found:
        return None
    import types as _t
    return _t.SimpleNamespace(
        Min=XYZ(min_x, min_y, z),
        Max=XYZ(max_x, max_y, z),
    )


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

        # Tag position = Top (0); tag alignment = View (0).
        try:
            p_tag_pos = detail.LookupParameter('Tag Position')
            if p_tag_pos is not None and not p_tag_pos.IsReadOnly:
                p_tag_pos.Set(0)   # 0 = Top
        except Exception:
            pass
        try:
            p_tag_align = detail.LookupParameter('Tag Alignment')
            if p_tag_align is not None and not p_tag_align.IsReadOnly:
                p_tag_align.Set(1)   # 1 = View (0 = Rebar Shape Family)
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


def place_donut(doc, view, center, outer_r):
    """Place a solid filled circle at center."""
    from Autodesk.Revit.DB import FilledRegion, FilledRegionType
    try:
        frt = FilteredElementCollector(doc).OfClass(FilledRegionType).FirstElement()
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

def place_all_details(doc, views_dict, tag_family_symbol):
    """Place bending details (one per rebar set element), span annotation, and tag per view."""
    # Fetch once — reused for every bar across all marks.
    detail_type = _get_rebar_bending_detail_type(doc)
    if detail_type is None:
        print('[detail_placer] Warning: no RebarBendingDetailType found — bending details will be skipped.')

    # Pre-activate tag symbol once to avoid doc.Regenerate() per bar.
    if tag_family_symbol is not None and not tag_family_symbol.IsActive:
        tag_family_symbol.Activate()
        doc.Regenerate()

    skipped = []
    for mark_value, view in views_dict.items():
        print('[detail_placer] Processing mark: {!r}'.format(mark_value))

        # Collect only bars visible in this specific view so the count matches
        # what Revit's filter shows (bars on other levels are excluded).
        all_bars = _get_all_bars(doc, mark_value, view=view)
        if not all_bars:
            print('[detail_placer]   No rebar visible in view for mark {!r} — skipping.'.format(mark_value))
            skipped.append(mark_value)
            continue

        print('[detail_placer]   Rebar visible in view: {}'.format(len(all_bars)))

        dist_axis = 'Y' if mark_value in X_MARKS else 'X'
        # Donut radii in model space: size on paper × view scale
        view_scale = getattr(view, 'Scale', 50)
        outer_r = 1.0 / 304.8 * view_scale   # 1 mm on paper (solid circle)
        placed_details = placed_dims = placed_donuts = failed_details = 0

        for i, bar in enumerate(all_bars):
            zone_extent = _get_rebar_zone_extent(bar, dist_axis)

            if zone_extent is not None:
                zone_min, zone_max, perp, z_dim, axis, count = zone_extent
                span = zone_max - zone_min
                bar_index = count // 4
                if i == 0:
                    print('[detail_placer]   [diag] count={} bar_index={} span={:.0f}mm'.format(
                        count, bar_index, span * 304.8))
                # Build move vector for rebar sets (span > 0.5 ft ≈ 150 mm).
                # Align to Bar always snaps to bar 0; MoveElement shifts to 1/4.
                if span > 0.5:
                    if axis == 'Y':
                        move_vec = XYZ(0.0, span / 4.0, 0.0)
                    else:
                        move_vec = XYZ(span / 4.0, 0.0, 0.0)
                else:
                    move_vec = None  # individual bar — stay at original location
            else:
                bar_index = 0
                move_vec = None

            bd = place_bending_detail(doc, view, bar, mark_value, detail_type, bar_index,
                                      move_vector=move_vec)
            if bd is not None:
                placed_details += 1
            else:
                failed_details += 1

            if zone_extent is not None:
                dim = place_distribution_dimension(doc, view, bar, zone_extent)
                if dim is not None:
                    placed_dims += 1

                quarter_coord = zone_min + span / 4.0
                if axis == 'Y':
                    donut_center = XYZ(perp, quarter_coord, z_dim)
                else:
                    donut_center = XYZ(quarter_coord, perp, z_dim)
                dn = place_donut(doc, view, donut_center, outer_r)
                if dn is not None:
                    placed_donuts += 1

        print('[detail_placer]   Details: {}  failed: {}  dims: {}  donuts: {}'.format(
            placed_details, failed_details, placed_dims, placed_donuts))

        # Tag on first bar
        if tag_family_symbol is None:
            print('[detail_placer]   Tag: skipped (no tag family selected)')
        else:
            tag = place_rebar_tag(doc, view, all_bars[0], tag_family_symbol)
            print('[detail_placer]   Tag: {}'.format(
                'placed' if tag is not None else 'FAILED (see warning above)'))

    return skipped
