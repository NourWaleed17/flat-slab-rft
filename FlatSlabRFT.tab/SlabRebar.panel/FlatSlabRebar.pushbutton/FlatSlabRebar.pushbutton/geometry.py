# -*- coding: utf-8 -*-
"""Geometry extraction and 2D math for Flat Slab Rebar."""
from __future__ import print_function

from Autodesk.Revit.DB import (FilteredElementCollector, Floor, Opening,
                                JoinGeometryUtils, Transaction, Wall,
                                BuiltInCategory, FamilyInstance)

TOLERANCE = 0.001  # feet
MM_TO_FEET = 0.00328084

# Safer detection defaults for drop panel floors.
DP_TOP_Z_TOLERANCE = 20.0 * MM_TO_FEET         # 20 mm
DP_THICKNESS_TOLERANCE = 3.0 * MM_TO_FEET      # 3 mm
DP_RELAXED_TOP_Z_TOLERANCE = 120.0 * MM_TO_FEET       # 120 mm
DP_RELAXED_THICKNESS_TOLERANCE = 60.0 * MM_TO_FEET    # 60 mm
DP_MAX_TOP_BELOW_MULTIPLIER = 6.0

_LAST_DP_DEBUG = {}


# ---------------------------------------------------------------------------
# Polygon helpers
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
    """Reorder curves so that each curve's end connects to the next curve's start.

    Revit's Opening.BoundaryCurves (and occasionally Sketch.Profile) may return
    curves in arbitrary order.  This function chains them into a single closed
    loop.

    Returns (sorted_curves, sort_succeeded).
    sort_succeeded is False when the chain is broken (curves could not all be
    connected), meaning the resulting polygon may be self-intersecting.
    """
    if len(curves) <= 1:
        return list(curves), True

    SORT_TOL = 0.01  # feet — generous enough for typical model precision

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
                # Curve stored in reverse — flip it.
                try:
                    sorted_c.append(c.CreateReversed())
                except Exception:
                    sorted_c.append(c)
                remaining.pop(i)
                found = True
                break
        if not found:
            # Chain broken — fall back to original order; signal failure.
            return list(curves), False

    return sorted_c, True


def _extract_polygon_loops(all_curves):
    """Split a flat curve collection into separate closed-loop polygons.

    Revit's Opening.BoundaryCurves for a shaft opening created with multiple
    disconnected holes in one Edit Boundary session returns ALL the boundary
    curves as a single flat list.  Each disconnected sub-group of curves forms
    one closed loop (one shaft hole).  This function chains each sub-group into
    a loop and returns a list of 2-D polygons — one per hole.
    """
    SORT_TOL = 0.01  # feet
    remaining = list(all_curves)
    loops = []

    while remaining:
        # Seed the current loop with the first available curve.
        loop = [remaining.pop(0)]

        # Extend the loop by chaining matching endpoints.
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
                break  # Current loop is closed or can't be extended further.

        polygon = _curve_array_to_polygon(loop)
        if len(polygon) >= 3:
            loops.append(polygon)

    return loops


def _curve_array_to_polygon(curve_array):
    """Convert a CurveArray (or CurveLoop) to a 2D polygon [(x, y), ...].

    Curves are first sorted into a connected loop (fixing arbitrary ordering
    from Opening.BoundaryCurves), then each curve is tessellated so that arcs
    and splines produce accurate polygon approximations instead of coarse
    chord-only representations.
    """
    curves = list(curve_array)
    if not curves:
        return []
    sorted_curves, _ = _sort_curves_into_loop(curves)
    points = []
    for curve in sorted_curves:
        try:
            tessellated = list(curve.Tessellate())
            # Skip the last tessellated point: it equals the start of the
            # next curve (duplicate vertex).
            for pt in tessellated[:-1]:
                points.append((pt.X, pt.Y))
        except Exception:
            # Fallback for any curve type that doesn't support Tessellate.
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
                continue  # skip the wrap-around adjacent pair
            x3, y3 = polygon[j]
            x4, y4 = polygon[(j + 1) % n]
            if _segments_cross(x1, y1, x2, y2, x3, y3, x4, y4):
                return True
    return False


def point_in_polygon(x, y, polygon):
    """Ray-casting algorithm: True if (x, y) is inside polygon."""
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / float(yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _point_on_segment(px, py, x1, y1, x2, y2, tol):
    """Return True if point lies on segment within tolerance."""
    dx = x2 - x1
    dy = y2 - y1
    seg_len2 = dx * dx + dy * dy
    if seg_len2 <= tol * tol:
        return ((px - x1) * (px - x1) + (py - y1) * (py - y1)) <= tol * tol

    t = ((px - x1) * dx + (py - y1) * dy) / seg_len2
    if t < -tol or t > 1.0 + tol:
        return False

    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    dist2 = (px - proj_x) * (px - proj_x) + (py - proj_y) * (py - proj_y)
    return dist2 <= tol * tol


def point_in_polygon_or_edge(x, y, polygon, tol=TOLERANCE):
    """Inclusive point-in-polygon test (inside or on boundary)."""
    n = len(polygon)
    for i in range(n):
        j = (i + 1) % n
        if _point_on_segment(x, y, polygon[i][0], polygon[i][1], polygon[j][0], polygon[j][1], tol):
            return True
    return point_in_polygon(x, y, polygon)


# ---------------------------------------------------------------------------
# Main geometry extraction
# ---------------------------------------------------------------------------

def get_slab_data(floor):
    """Extract polygon, Z levels, thickness and bounding box from a floor."""
    sketch_id = getattr(floor, 'SketchId', None)
    if sketch_id is None or sketch_id.IntegerValue < 0:
        raise Exception('Selected floor has no editable sketch.')

    sketch = floor.Document.GetElement(sketch_id)
    if sketch is None:
        raise Exception('Cannot get sketch from selected floor element.')

    profile = sketch.Profile  # CurveArrArray

    loops = []
    for curve_array in profile:
        polygon = _curve_array_to_polygon(curve_array)
        if len(polygon) >= 3:
            area = polygon_area(polygon)
            loops.append((area, polygon))

    if not loops:
        raise Exception('No profile loops found in slab sketch.')

    # Largest area = outer boundary; smaller loops = sketch voids (openings)
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

    # Thickness from FloorType parameter, fall back to bbox height
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


def get_last_dp_debug_info():
    """Return diagnostics from the most recent drop-panel detection pass."""
    return dict(_LAST_DP_DEBUG)


def get_shaft_opening_polygons(doc, slab_bbox, top_z, main_floor_id=None, slab_bottom_z=None):
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

            # Keep only openings relevant to this slab:
            # 1) hosted on the selected floor, OR
            # 2) unhosted (shaft-like) openings intersecting slab Z range.
            if host is not None:
                if not isinstance(host, Floor):
                    continue
                if main_floor_id is not None and host.Id.IntegerValue != main_floor_id.IntegerValue:
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
                # A shaft opening created with multiple holes in one Edit Boundary
                # session stores ALL hole boundaries as a flat curve list in
                # BoundaryCurves.  _extract_polygon_loops splits them back into
                # individual closed-loop polygons — one per hole.
                loop_polygons = _extract_polygon_loops(list(curves))
            else:
                # Fallback: use opening bounding box projection as rectangle.
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

                # If a loop polygon is self-intersecting (e.g. BoundaryCurves
                # stored diagonal pairs that chain but criss-cross), fall back
                # to its bounding box which is always convex and safe.
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

                # Safety net: skip any loop whose bbox still covers > 20% of the
                # slab (would indicate a non-structural zone boundary element).
                shaft_bbox_area = (s_max_x - s_min_x) * (s_max_y - s_min_y)
                if slab_bbox_area > 0 and shaft_bbox_area > slab_bbox_area * 0.20:
                    continue

                # Skip loops outside the slab bounding box.
                if (s_max_x < min_x - TOLERANCE or s_min_x > max_x + TOLERANCE
                        or s_max_y < min_y - TOLERANCE or s_min_y > max_y + TOLERANCE):
                    continue

                shaft_polygons.append(polygon)
        except Exception:
            continue

    return shaft_polygons


def _collect_drop_panel_data(doc, top_z, main_floor_id=None, slab_bbox=None, slab_thickness=None,
                             slab_bottom_z=None,
                             slab_polygon=None,
                             top_z_tolerance=DP_TOP_Z_TOLERANCE,
                             thickness_tolerance=DP_THICKNESS_TOLERANCE):
    """Internal DP collector for one tolerance pass."""
    dp_list = []
    debug = {
        'floors_scanned': 0,
        'accepted': 0,
        'rejected_main_floor': 0,
        'rejected_no_bbox': 0,
        'rejected_top_mismatch': 0,
        'rejected_no_sketch': 0,
        'rejected_no_profile': 0,
        'rejected_not_thicker': 0,
        'rejected_no_overlap': 0,
        'rejected_outside_slab': 0,
        'accepted_top_below_slab': 0,
        'closest_top_delta_mm': None,
        'max_allowed_top_below_mm': None,
    }

    try:
        # When a slab bounding box is available, use Revit's spatial index to
        # pre-filter floors by XY extents and Z range.  This avoids iterating
        # every floor in large models (100+ floors).  Falls back to a full scan
        # if the filter API is unavailable (older Revit versions or import error).
        try:
            from Autodesk.Revit.DB import BoundingBoxIntersectsFilter, Outline, XYZ
            if slab_bbox is not None:
                s_min_x, s_min_y, s_max_x, s_max_y = slab_bbox
                # Z range: from well below the slab bottom up to just above slab top.
                z_margin = max(top_z_tolerance * 4,
                               (slab_thickness or 0.5) * (DP_MAX_TOP_BELOW_MULTIPLIER + 1))
                outline = Outline(
                    XYZ(s_min_x, s_min_y, top_z - z_margin),
                    XYZ(s_max_x, s_max_y, top_z + top_z_tolerance * 2)
                )
                floors = list(
                    FilteredElementCollector(doc)
                    .OfClass(Floor)
                    .WherePasses(BoundingBoxIntersectsFilter(outline))
                    .ToElements()
                )
            else:
                floors = list(FilteredElementCollector(doc).OfClass(Floor).ToElements())
        except Exception:
            floors = list(FilteredElementCollector(doc).OfClass(Floor).ToElements())
    except Exception:
        return [], debug

    for floor in floors:
        try:
            debug['floors_scanned'] += 1

            if main_floor_id is not None and floor.Id.IntegerValue == main_floor_id.IntegerValue:
                debug['rejected_main_floor'] += 1
                continue

            bb = floor.get_BoundingBox(None)
            if bb is None:
                debug['rejected_no_bbox'] += 1
                continue

            # Typical DP condition: top aligned with slab top.
            # Also allow DP top below slab top (common separate-floor modeling),
            # up to about one slab thickness lower.
            top_delta = bb.Max.Z - top_z
            abs_top_delta = abs(top_delta)
            top_delta_mm = abs_top_delta / MM_TO_FEET
            if (debug['closest_top_delta_mm'] is None
                    or top_delta_mm < debug['closest_top_delta_mm']):
                debug['closest_top_delta_mm'] = round(top_delta_mm, 1)

            top_match = abs_top_delta <= top_z_tolerance
            if (not top_match) and (slab_thickness is not None and slab_thickness > 0):
                below = top_z - bb.Max.Z  # positive if candidate top is lower
                max_below = (DP_MAX_TOP_BELOW_MULTIPLIER * slab_thickness) + top_z_tolerance
                debug['max_allowed_top_below_mm'] = round(max_below / MM_TO_FEET, 1)
                if (-top_z_tolerance) <= below <= (max_below + TOLERANCE):
                    top_match = True

            if not top_match:
                debug['rejected_top_mismatch'] += 1
                continue

            sketch_id = getattr(floor, 'SketchId', None)
            if sketch_id is None or sketch_id.IntegerValue < 0:
                debug['rejected_no_sketch'] += 1
                continue

            sketch = floor.Document.GetElement(sketch_id)
            if sketch is None:
                debug['rejected_no_sketch'] += 1
                continue

            profile = sketch.Profile
            loops = []
            for curve_array in profile:
                polygon = _curve_array_to_polygon(curve_array)
                if len(polygon) >= 3:
                    area = polygon_area(polygon)
                    loops.append((area, polygon))

            if not loops:
                debug['rejected_no_profile'] += 1
                continue

            loops.sort(key=lambda t: t[0], reverse=True)
            outer_polygon = loops[0][1]

            # Thickness
            thickness = None
            floor_type = floor.FloorType
            for param_name in ('Default Thickness', 'Thickness', 'Structural Depth'):
                param = floor_type.LookupParameter(param_name)
                if param is not None and param.AsDouble() > 0:
                    thickness = param.AsDouble()
                    break
            if thickness is None:
                thickness = bb.Max.Z - bb.Min.Z

            # Accept as a DP if it is strictly thicker than the slab (full-depth
            # model) OR its bottom extends below the slab bottom (drop-only
            # model where the DP floor represents only the extra drop portion).
            # Reject only when neither condition holds, which covers thin topping
            # slabs, floor finishes, and other unrelated same-elevation floors.
            if slab_thickness is not None:
                dp_strictly_thicker = (thickness > slab_thickness + thickness_tolerance)
                dp_extends_below = (
                    slab_bottom_z is not None
                    and bb.Min.Z < slab_bottom_z - thickness_tolerance
                )
                if not dp_strictly_thicker and not dp_extends_below:
                    debug['rejected_not_thicker'] += 1
                    continue

            xs = [p[0] for p in outer_polygon]
            ys = [p[1] for p in outer_polygon]
            dp_bbox = (min(xs), min(ys), max(xs), max(ys))

            # Optional overlap filter with slab bbox.
            if slab_bbox is not None:
                s_min_x, s_min_y, s_max_x, s_max_y = slab_bbox
                d_min_x, d_min_y, d_max_x, d_max_y = dp_bbox
                if (d_max_x < s_min_x - TOLERANCE or d_min_x > s_max_x + TOLERANCE
                        or d_max_y < s_min_y - TOLERANCE or d_min_y > s_max_y + TOLERANCE):
                    debug['rejected_no_overlap'] += 1
                    continue

            # Tighten acceptance: drop panel should lie within (or at least intersect)
            # the selected slab footprint, not just its bounding box.
            if slab_polygon:
                center_x = (dp_bbox[0] + dp_bbox[2]) * 0.5
                center_y = (dp_bbox[1] + dp_bbox[3]) * 0.5
                center_inside = point_in_polygon_or_edge(center_x, center_y, slab_polygon)
                any_vertex_inside = any(
                    point_in_polygon_or_edge(x, y, slab_polygon) for x, y in outer_polygon
                )
                any_slab_vertex_inside_dp = any(
                    point_in_polygon_or_edge(x, y, outer_polygon) for x, y in slab_polygon
                )
                if not (center_inside or any_vertex_inside or any_slab_vertex_inside_dp):
                    debug['rejected_outside_slab'] += 1
                    continue

            dp_list.append({
                'polygon':   outer_polygon,
                'thickness': thickness,
                'bbox':      dp_bbox,
                'floor':     floor,
                'top_z':     bb.Max.Z,
                'bottom_z':  bb.Min.Z,
                'slab_top_z': top_z,
            })
            debug['accepted'] += 1
            if bb.Max.Z < top_z - top_z_tolerance:
                debug['accepted_top_below_slab'] += 1
        except Exception:
            continue

    return dp_list, debug


def get_drop_panel_data(doc, top_z, main_floor_id=None, slab_bbox=None, slab_thickness=None,
                        slab_bottom_z=None,
                        slab_polygon=None,
                        top_z_tolerance=DP_TOP_Z_TOLERANCE,
                        thickness_tolerance=DP_THICKNESS_TOLERANCE):
    """Return data for all drop panels modeled as separate floors.

    Pass 1: strict tolerances.
    Pass 2 (automatic fallback): relaxed tolerances if strict finds none.
    """
    global _LAST_DP_DEBUG

    strict_list, strict_debug = _collect_drop_panel_data(
        doc, top_z,
        main_floor_id=main_floor_id,
        slab_bbox=slab_bbox,
        slab_thickness=slab_thickness,
        slab_bottom_z=slab_bottom_z,
        slab_polygon=slab_polygon,
        top_z_tolerance=top_z_tolerance,
        thickness_tolerance=thickness_tolerance
    )

    if strict_list:
        strict_debug['used_relaxed_retry'] = 0
        strict_debug['top_z_tolerance_mm'] = round(top_z_tolerance / MM_TO_FEET, 1)
        strict_debug['thickness_tolerance_mm'] = round(thickness_tolerance / MM_TO_FEET, 1)
        _LAST_DP_DEBUG = strict_debug
        return strict_list

    relaxed_list, relaxed_debug = _collect_drop_panel_data(
        doc, top_z,
        main_floor_id=main_floor_id,
        slab_bbox=slab_bbox,
        slab_thickness=slab_thickness,
        slab_bottom_z=slab_bottom_z,
        slab_polygon=slab_polygon,
        top_z_tolerance=DP_RELAXED_TOP_Z_TOLERANCE,
        thickness_tolerance=DP_RELAXED_THICKNESS_TOLERANCE
    )

    relaxed_debug['used_relaxed_retry'] = 1
    relaxed_debug['top_z_tolerance_mm'] = round(DP_RELAXED_TOP_Z_TOLERANCE / MM_TO_FEET, 1)
    relaxed_debug['thickness_tolerance_mm'] = round(DP_RELAXED_THICKNESS_TOLERANCE / MM_TO_FEET, 1)
    _LAST_DP_DEBUG = relaxed_debug
    return relaxed_list


# ---------------------------------------------------------------------------
# Intersection / clipping geometry
# ---------------------------------------------------------------------------

def segment_polygon_intersections(fixed_val, vary_min, vary_max, polygon, axis):
    """Find all intersections of a horizontal/vertical scan line with polygon edges.

    axis='X': scan line is y=fixed_val, result X coordinates sorted ascending.
    axis='Y': scan line is x=fixed_val, result Y coordinates sorted ascending.

    Returns sorted list of coordinates along the *varying* axis.
    """
    intersections = []
    n = len(polygon)

    for i in range(n):
        j = (i + 1) % n
        if axis == 'X':
            # Scan line y = fixed_val
            y1, y2 = polygon[i][1], polygon[j][1]
            x1, x2 = polygon[i][0], polygon[j][0]
        else:
            # Scan line x = fixed_val  (roles of x/y are swapped)
            y1, y2 = polygon[i][0], polygon[j][0]
            x1, x2 = polygon[i][1], polygon[j][1]

        # Half-open interval convention: same as point_in_polygon's (yi > y) != (yj > y).
        # Counts each vertex exactly once — prevents duplicate points when the scan line
        # passes exactly through a polygon vertex.
        if not ((y1 > fixed_val) != (y2 > fixed_val)):
            continue

        dy = y2 - y1
        # dy cannot be zero here because the half-open test already excluded parallel edges
        t = (fixed_val - y1) / dy
        x_int = x1 + t * (x2 - x1)

        if vary_min - TOLERANCE <= x_int <= vary_max + TOLERANCE:
            intersections.append(x_int)

    intersections.sort()
    return intersections


def get_obstacle_intervals(fixed_val, vary_min, vary_max, polygon, axis):
    """Return sorted list of (enter, exit) intervals where the scan line is inside polygon."""
    pts = segment_polygon_intersections(fixed_val, vary_min, vary_max, polygon, axis)

    # Check whether each endpoint of the bar range is inside the polygon
    if axis == 'X':
        start_inside = point_in_polygon(vary_min, fixed_val, polygon)
        end_inside   = point_in_polygon(vary_max, fixed_val, polygon)
    else:
        start_inside = point_in_polygon(fixed_val, vary_min, polygon)
        end_inside   = point_in_polygon(fixed_val, vary_max, polygon)

    # Only prepend/append if the boundary point is not already present as an
    # intersection (within tolerance).  When vary_min/vary_max falls exactly on a
    # polygon edge, segment_polygon_intersections already returns that coordinate,
    # so adding it again creates a degenerate duplicate that collapses the interval.
    if start_inside and (not pts or pts[0] > vary_min + TOLERANCE):
        pts = [vary_min] + pts
    if end_inside and (not pts or pts[-1] < vary_max - TOLERANCE):
        pts = pts + [vary_max]

    intervals = []
    for i in range(0, len(pts) - 1, 2):
        enter = max(vary_min, pts[i])
        exit_ = min(vary_max, pts[i + 1])
        if exit_ > enter + TOLERANCE:
            intervals.append((enter, exit_))

    return intervals


def clip_bar_to_slab(fixed_val, vary_min, vary_max, outer_polygon, axis):
    """Return the (start, end) extent of the bar row inside the slab, or None."""
    pts = segment_polygon_intersections(fixed_val, vary_min, vary_max, outer_polygon, axis)

    if axis == 'X':
        start_inside = point_in_polygon(vary_min, fixed_val, outer_polygon)
        end_inside   = point_in_polygon(vary_max, fixed_val, outer_polygon)
    else:
        start_inside = point_in_polygon(fixed_val, vary_min, outer_polygon)
        end_inside   = point_in_polygon(fixed_val, vary_max, outer_polygon)

    if start_inside:
        pts = [vary_min] + pts
    if end_inside:
        pts = pts + [vary_max]

    if len(pts) < 2:
        return None

    # Return the full extent from first entry to last exit.
    # Interior re-entrant holes are handled as shaft/void obstacles elsewhere.
    return (pts[0], pts[-1])


def clip_bar_to_slab_intervals(fixed_val, vary_min, vary_max, outer_polygon, axis):
    """Return all inside intervals of a scanline within the slab outer polygon."""
    return get_obstacle_intervals(fixed_val, vary_min, vary_max, outer_polygon, axis)


# ---------------------------------------------------------------------------
# Support position extraction
# ---------------------------------------------------------------------------

def get_support_positions_2d(doc, slab_bbox, slab_z_range):
    """Return list of (x, y) support points within slab bounding box.

    Collects structural column centroids and structural wall midpoints that
    intersect the slab Z range.

    slab_bbox : (min_x, min_y, max_x, max_y)
    slab_z_range : (bottom_z, top_z)
    """
    min_x, min_y, max_x, max_y = slab_bbox
    z_bot, z_top = slab_z_range
    positions = []

    # Structural columns
    try:
        col_collector = (FilteredElementCollector(doc)
                         .OfClass(FamilyInstance)
                         .OfCategory(BuiltInCategory.OST_StructuralColumns)
                         .ToElements())
        for col in col_collector:
            try:
                bb = col.get_BoundingBox(None)
                if bb is None:
                    continue
                if bb.Max.Z < z_bot - TOLERANCE or bb.Min.Z > z_top + TOLERANCE:
                    continue
                cx = (bb.Min.X + bb.Max.X) * 0.5
                cy = (bb.Min.Y + bb.Max.Y) * 0.5
                if (min_x - TOLERANCE <= cx <= max_x + TOLERANCE
                        and min_y - TOLERANCE <= cy <= max_y + TOLERANCE):
                    positions.append((cx, cy))
            except Exception:
                continue
    except Exception:
        pass

    # Structural walls
    try:
        wall_collector = (FilteredElementCollector(doc)
                          .OfClass(Wall)
                          .ToElements())
        for wall in wall_collector:
            try:
                # Only structural walls
                struct_param = wall.LookupParameter('Structural Usage')
                if struct_param is None:
                    struct_param = wall.LookupParameter('Structural')
                if struct_param is not None and struct_param.AsInteger() == 0:
                    continue
                bb = wall.get_BoundingBox(None)
                if bb is None:
                    continue
                if bb.Max.Z < z_bot - TOLERANCE or bb.Min.Z > z_top + TOLERANCE:
                    continue
                cx = (bb.Min.X + bb.Max.X) * 0.5
                cy = (bb.Min.Y + bb.Max.Y) * 0.5
                if (min_x - TOLERANCE <= cx <= max_x + TOLERANCE
                        and min_y - TOLERANCE <= cy <= max_y + TOLERANCE):
                    positions.append((cx, cy))
            except Exception:
                continue
    except Exception:
        pass

    return positions


# ---------------------------------------------------------------------------
# Join management
# ---------------------------------------------------------------------------

def ensure_dp_joins(doc, main_floor, dp_data_list):
    """Ensure every detected DP is joined with the main slab, with DP winning the join.

    Correct state: DP floor *cuts* the slab (so DP bbox stays geometrically intact).
    - Not joined  → joins them (DP wins).
    - Joined, wrong order (slab cuts DP) → switches the join order.
    - Joined, correct order → no change.

    Returns a stats dict.
    """
    stats = {
        'already_correct': 0,
        'joined_new': 0,
        'switched': 0,
        'failed': 0,
        'skipped_no_floor': 0,
    }

    if not dp_data_list:
        return stats

    t = Transaction(doc, 'Fix DP-Slab Join Order')
    t.Start()
    try:
        for dp_data in dp_data_list:
            dp_floor = dp_data.get('floor')
            if dp_floor is None:
                stats['skipped_no_floor'] += 1
                continue
            try:
                are_joined = JoinGeometryUtils.AreElementsJoined(doc, main_floor, dp_floor)
                if are_joined:
                    # IsCuttingElementInJoin(doc, a, b) returns True if a cuts b
                    dp_cuts_slab = JoinGeometryUtils.IsCuttingElementInJoin(doc, dp_floor, main_floor)
                    if dp_cuts_slab:
                        stats['already_correct'] += 1
                    else:
                        # Slab is cutting DP — switch so DP cuts slab
                        JoinGeometryUtils.SwitchJoinOrder(doc, main_floor, dp_floor)
                        stats['switched'] += 1
                else:
                    # Not joined — JoinGeometry(doc, a, b): b cuts a
                    JoinGeometryUtils.JoinGeometry(doc, main_floor, dp_floor)
                    stats['joined_new'] += 1
            except Exception:
                stats['failed'] += 1
                continue
        t.Commit()
    except Exception:
        t.RollBack()
        raise

    return stats
