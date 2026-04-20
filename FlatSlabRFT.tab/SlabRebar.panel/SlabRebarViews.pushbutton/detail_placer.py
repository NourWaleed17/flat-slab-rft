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


class _BBox(object):
    """Minimal bounding box container (IronPython 2.7 has no types.SimpleNamespace)."""
    __slots__ = ('Min', 'Max')

    def __init__(self, min_xyz, max_xyz):
        self.Min = min_xyz
        self.Max = max_xyz


def _all_bars_bbox(bars):
    """Return a combined _BBox with Min/Max XYZ for a list of bars."""
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
    return _BBox(XYZ(min_x, min_y, z), XYZ(max_x, max_y, z))


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

def _zone_from_combined_bbox(combined_bb, dist_axis):
    """Compute zone extent tuple from a pre-built combined bounding box.

    Returns (zone_min, zone_max, perp, z, axis, 1) or None if span is zero.
    'perp' is placed at the 1/4 point of the bar-length axis so annotations
    sit away from the slab edge rather than dead-centre.
    """
    if combined_bb is None:
        return None
    z = (combined_bb.Min.Z + combined_bb.Max.Z) / 2.0
    if dist_axis == 'Y':
        zone_min = combined_bb.Min.Y
        zone_max = combined_bb.Max.Y
        if zone_max - zone_min < 1e-6:
            return None
        perp = combined_bb.Min.X + (combined_bb.Max.X - combined_bb.Min.X) / 4.0
        return zone_min, zone_max, perp, z, 'Y', 1
    else:
        zone_min = combined_bb.Min.X
        zone_max = combined_bb.Max.X
        if zone_max - zone_min < 1e-6:
            return None
        perp = combined_bb.Min.Y + (combined_bb.Max.Y - combined_bb.Min.Y) / 4.0
        return zone_min, zone_max, perp, z, 'X', 1


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
    bd = place_bending_detail(doc, view, bar, mark_value, detail_type,
                              bar_index=0, move_vector=None)

    zone = _get_rebar_zone_extent(bar, dist_axis)
    dim = dn = None
    if zone is not None:
        zone_min, zone_max, perp, z_dim, axis, _ = zone
        span = zone_max - zone_min
        dim = place_distribution_dimension(doc, view, bar, zone)
        third  = zone_min + span / 3.0
        center = XYZ(perp, third, z_dim) if axis == 'Y' else XYZ(third, perp, z_dim)
        dn = place_donut(doc, view, center, outer_r, filled_region_type=filled_region_type)

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
    wanted_marks = set(views_dict.keys())
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

        all_bars = bars_by_mark.get(mark_value, [])
        if not all_bars:
            print('[detail_placer]   No rebar — skipping.')
            skipped.append(mark_value)
            continue

        dist_axis  = 'Y' if mark_value in X_MARKS else 'X'
        view_scale = getattr(view, 'Scale', 50)
        outer_r    = 1.0 / 304.8 * view_scale

        rebar_sets      = [b for b in all_bars if _rebar_qty(b) > 1]
        individual_bars = [b for b in all_bars if _rebar_qty(b) == 1]
        print('[detail_placer]   bars={} sets={} individual={}'.format(
            len(all_bars), len(rebar_sets), len(individual_bars)))

        total_details = total_dims = total_donuts = total_failed = 0

        # ── One annotation per rebar SET element ──────────────────────────
        for bar in rebar_sets:
            t_set = time.time()
            bd_ok, dim_ok, dn_ok = _annotate_one_set(
                doc, view, bar, mark_value, detail_type, dist_axis, outer_r,
                filled_region_type=frt_cache,
            )
            print('[detail_placer]   set annotation: {:.0f}ms  bd={} dim={} dn={}'.format(
                (time.time() - t_set) * 1000, bd_ok, dim_ok, dn_ok))
            if bd_ok:  total_details += 1
            else:      total_failed  += 1
            if dim_ok: total_dims    += 1
            if dn_ok:  total_donuts  += 1

        # ── Individual bars → one combined annotation ──────────────────────
        if individual_bars:
            rep_bar = individual_bars[0]

            t_bbox = time.time()
            combined_bb = _all_bars_bbox(individual_bars)
            print('[detail_placer]   _all_bars_bbox ({} bars): {:.0f}ms'.format(
                len(individual_bars), (time.time() - t_bbox) * 1000))

            zone_extent = _zone_from_combined_bbox(combined_bb, dist_axis)

            t_bd = time.time()
            bd = place_bending_detail(doc, view, rep_bar, mark_value, detail_type,
                                      bar_index=0, move_vector=None)
            print('[detail_placer]   place_bending_detail: {:.0f}ms  ok={}'.format(
                (time.time() - t_bd) * 1000, bd is not None))
            if bd is not None: total_details += 1
            else:              total_failed  += 1

            if zone_extent is not None:
                zone_min, zone_max, perp, z_dim, axis, _ = zone_extent
                span = zone_max - zone_min

                t_dim = time.time()
                dim = place_distribution_dimension(doc, view, rep_bar, zone_extent)
                print('[detail_placer]   place_distribution_dimension: {:.0f}ms  ok={}'.format(
                    (time.time() - t_dim) * 1000, dim is not None))
                if dim is not None: total_dims += 1

                third  = zone_min + span / 3.0
                center = XYZ(perp, third, z_dim) if axis == 'Y' else XYZ(third, perp, z_dim)

                t_dn = time.time()
                dn = place_donut(doc, view, center, outer_r, filled_region_type=frt_cache)
                print('[detail_placer]   place_donut: {:.0f}ms  ok={}'.format(
                    (time.time() - t_dn) * 1000, dn is not None))
                if dn is not None: total_donuts += 1

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
