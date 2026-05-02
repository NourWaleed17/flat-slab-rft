# -*- coding: utf-8 -*-
"""Slab/shaft helpers for VoidRFT.

Copied from FlatSlabRebar/geometry.py on 2026-04-24. VoidRFT and
FlatSlabRebar intentionally keep separate copies to decouple. If you
improve detection here, consider backporting to FlatSlabRebar too.

Functions copied from geometry.py:
  polygon_area              lines  26-34
  _sort_curves_into_loop    lines  37-78
  _extract_polygon_loops    lines  81-128
  _curve_array_to_polygon   lines 131-155
  _segments_cross           lines 158-167
  _polygon_is_self_intersecting  lines 170-185
  get_slab_data             lines 234-291
  get_shaft_opening_polygons lines 299-397

New (not in FlatSlabRebar geometry.py):
  get_floor_cover
  bbox_from_polygon
"""
from __future__ import print_function

from Autodesk.Revit.DB import FilteredElementCollector, Floor, Opening

TOLERANCE = 0.001  # feet


# ---------------------------------------------------------------------------
# Polygon helpers (copied verbatim)
# ---------------------------------------------------------------------------

def polygon_area(polygon):
    """Compute unsigned area using the shoelace formula."""
    n = len(polygon)
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += polygon[i][0] * polygon[j][1]
        area -= polygon[j][0] * polygon[i][1]
    return abs(area) / 2.0


def _sort_curves_into_loop(curves):
    """Reorder curves so each curve's end connects to the next curve's start."""
    if len(curves) <= 1:
        return list(curves), True

    SORT_TOL = 0.01

    sorted_c = [curves[0]]
    remaining = list(curves[1:])

    for _ in range(len(remaining)):
        last_end = sorted_c[-1].GetEndPoint(1)
        found = False
        for i, c in enumerate(remaining):
            if last_end.DistanceTo(c.GetEndPoint(0)) < SORT_TOL:
                sorted_c.append(c)
                remaining.pop(i)
                found = True
                break
            if last_end.DistanceTo(c.GetEndPoint(1)) < SORT_TOL:
                try:
                    sorted_c.append(c.CreateReversed())
                except Exception:
                    sorted_c.append(c)
                remaining.pop(i)
                found = True
                break
        if not found:
            return list(curves), False

    return sorted_c, True


def _extract_polygon_loops(all_curves):
    """Split a flat curve collection into separate closed-loop polygons."""
    SORT_TOL = 0.01
    remaining = list(all_curves)
    loops = []

    while remaining:
        loop = [remaining.pop(0)]
        for _ in range(len(remaining)):
            try:
                last_end = loop[-1].GetEndPoint(1)
            except Exception:
                break
            found = False
            for i, c in enumerate(remaining):
                try:
                    if last_end.DistanceTo(c.GetEndPoint(0)) < SORT_TOL:
                        loop.append(remaining.pop(i))
                        found = True
                        break
                    if last_end.DistanceTo(c.GetEndPoint(1)) < SORT_TOL:
                        try:
                            loop.append(c.CreateReversed())
                        except Exception:
                            loop.append(c)
                        remaining.pop(i)
                        found = True
                        break
                except Exception:
                    continue
            if not found:
                break

        polygon = _curve_array_to_polygon(loop)
        if len(polygon) >= 3:
            loops.append(polygon)

    return loops


def _curve_array_to_polygon(curve_array):
    """Convert a CurveArray or CurveLoop to a 2D polygon [(x, y), ...]."""
    curves = list(curve_array)
    if not curves:
        return []
    sorted_curves, _ = _sort_curves_into_loop(curves)
    points = []
    for curve in sorted_curves:
        try:
            tessellated = list(curve.Tessellate())
            for pt in tessellated[:-1]:
                points.append((pt.X, pt.Y))
        except Exception:
            p = curve.GetEndPoint(0)
            points.append((p.X, p.Y))
    return points


def _segments_cross(x1, y1, x2, y2, x3, y3, x4, y4):
    """Return True if segment (p1-p2) properly crosses segment (p3-p4)."""
    def _cross(ox, oy, ax, ay, bx, by):
        return (ax - ox) * (by - oy) - (ay - oy) * (bx - ox)
    d1 = _cross(x3, y3, x4, y4, x1, y1)
    d2 = _cross(x3, y3, x4, y4, x2, y2)
    d3 = _cross(x1, y1, x2, y2, x3, y3)
    d4 = _cross(x1, y1, x2, y2, x4, y4)
    return (((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and
            ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)))


def _polygon_is_self_intersecting(polygon):
    """Return True if any two non-adjacent polygon edges cross each other."""
    n = len(polygon)
    if n < 4:
        return False
    for i in range(n):
        x1, y1 = polygon[i]
        x2, y2 = polygon[(i + 1) % n]
        for j in range(i + 2, n):
            if j == n - 1 and i == 0:
                continue
            x3, y3 = polygon[j]
            x4, y4 = polygon[(j + 1) % n]
            if _segments_cross(x1, y1, x2, y2, x3, y3, x4, y4):
                return True
    return False


# ---------------------------------------------------------------------------
# Main extraction (copied verbatim)
# ---------------------------------------------------------------------------

def get_slab_data(floor):
    """Extract polygon, Z levels, thickness and bounding box from a floor."""
    sketch_id = getattr(floor, 'SketchId', None)
    if sketch_id is None or sketch_id.IntegerValue < 0:
        raise Exception('Selected floor has no editable sketch.')

    sketch = floor.Document.GetElement(sketch_id)
    if sketch is None:
        raise Exception('Cannot get sketch from selected floor element.')

    profile = sketch.Profile

    loops = []
    for curve_array in profile:
        polygon = _curve_array_to_polygon(curve_array)
        if len(polygon) >= 3:
            area = polygon_area(polygon)
            loops.append((area, polygon))

    if not loops:
        raise Exception('No profile loops found in slab sketch.')

    loops.sort(key=lambda t: t[0], reverse=True)
    outer_polygon = loops[0][1]
    sketch_void_polygons = [t[1] for t in loops[1:]]

    bb = floor.get_BoundingBox(None)
    if bb is None:
        raise Exception('Cannot get bounding box of selected floor.')

    top_z = bb.Max.Z
    min_x = bb.Min.X
    min_y = bb.Min.Y
    max_x = bb.Max.X
    max_y = bb.Max.Y

    thickness = None
    floor_type = floor.FloorType
    for param_name in ('Default Thickness', 'Thickness', 'Structural Depth'):
        param = floor_type.LookupParameter(param_name)
        if param is not None and param.AsDouble() > 0:
            thickness = param.AsDouble()
            break
    if thickness is None:
        thickness = bb.Max.Z - bb.Min.Z

    bottom_z = top_z - thickness

    return {
        'outer_polygon':        outer_polygon,
        'sketch_void_polygons': sketch_void_polygons,
        'top_z':                top_z,
        'bottom_z':             bottom_z,
        'thickness':            thickness,
        'bbox':                 (min_x, min_y, max_x, max_y),
    }


def get_shaft_opening_polygons(doc, slab_bbox, top_z,
                               main_floor_id=None, slab_bottom_z=None):
    """Return 2-D polygons for all shaft openings overlapping the slab bbox."""
    shaft_polygons = []
    min_x, min_y, max_x, max_y = slab_bbox

    try:
        openings = list(FilteredElementCollector(doc).OfClass(Opening).ToElements())
    except Exception:
        return []

    for opening in openings:
        try:
            host = None
            try:
                host = opening.Host
            except Exception:
                host = None

            if host is not None:
                if not isinstance(host, Floor):
                    continue
                if (main_floor_id is not None
                        and host.Id.IntegerValue != main_floor_id.IntegerValue):
                    continue
            else:
                bb = opening.get_BoundingBox(None)
                if bb is None:
                    continue
                z_min = slab_bottom_z if slab_bottom_z is not None else (top_z - TOLERANCE)
                z_max = top_z + TOLERANCE
                if bb.Max.Z < z_min or bb.Min.Z > z_max:
                    continue

            curves = None
            try:
                curves = opening.BoundaryCurves
            except Exception:
                curves = None

            if curves is not None:
                loop_polygons = _extract_polygon_loops(list(curves))
            else:
                bb = opening.get_BoundingBox(None)
                if bb is None:
                    continue
                loop_polygons = [[
                    (bb.Min.X, bb.Min.Y),
                    (bb.Max.X, bb.Min.Y),
                    (bb.Max.X, bb.Max.Y),
                    (bb.Min.X, bb.Max.Y),
                ]]

            slab_bbox_area = (max_x - min_x) * (max_y - min_y)

            for polygon in loop_polygons:
                if len(polygon) < 3:
                    continue

                if _polygon_is_self_intersecting(polygon):
                    _xs = [p[0] for p in polygon]
                    _ys = [p[1] for p in polygon]
                    polygon = [
                        (min(_xs), min(_ys)),
                        (max(_xs), min(_ys)),
                        (max(_xs), max(_ys)),
                        (min(_xs), max(_ys)),
                    ]

                shaft_xs = [p[0] for p in polygon]
                shaft_ys = [p[1] for p in polygon]
                s_min_x, s_max_x = min(shaft_xs), max(shaft_xs)
                s_min_y, s_max_y = min(shaft_ys), max(shaft_ys)

                shaft_bbox_area = (s_max_x - s_min_x) * (s_max_y - s_min_y)
                if slab_bbox_area > 0 and shaft_bbox_area > slab_bbox_area * 0.20:
                    continue

                if (s_max_x < min_x - TOLERANCE or s_min_x > max_x + TOLERANCE
                        or s_max_y < min_y - TOLERANCE or s_min_y > max_y + TOLERANCE):
                    continue

                shaft_polygons.append(polygon)
        except Exception:
            continue

    return shaft_polygons


# ---------------------------------------------------------------------------
# New helpers (not in FlatSlabRebar geometry.py)
# ---------------------------------------------------------------------------

def get_floor_cover(floor):
    """Read top-face rebar cover from a Floor element. Returns feet.

    Tries the CONCRETE_COVER_TOP element-id parameter, follows it to the
    RebarCoverType, and reads COVER_TYPE_LENGTH. Falls back to 40mm (0.131ft)
    if the parameter is unavailable — the dialog lets the user override.
    """
    try:
        from Autodesk.Revit.DB import BuiltInParameter
        p = floor.get_Parameter(BuiltInParameter.CONCRETE_COVER_TOP)
        if p is not None:
            eid = p.AsElementId()
            if eid is not None and eid.IntegerValue > 0:
                ctype = floor.Document.GetElement(eid)
                if ctype is not None:
                    cp = ctype.get_Parameter(BuiltInParameter.COVER_TYPE_LENGTH)
                    if cp is not None and cp.AsDouble() > 0.0:
                        return cp.AsDouble()
    except Exception:
        pass
    return 40.0 / 304.8


def bbox_from_polygon(polygon):
    """Return (x_min, y_min, x_max, y_max) bounding box of a 2-D polygon."""
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return (min(xs), min(ys), max(xs), max(ys))
