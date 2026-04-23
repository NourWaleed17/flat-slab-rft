# -*- coding: utf-8 -*-
"""Geometry extraction and 2D math for Flat Slab Rebar."""
from __future__ import print_function

from Autodesk.Revit.DB import FilteredElementCollector, Floor, Opening

TOLERANCE = 0.001  # feet


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


def _curve_array_to_polygon(curve_array):
    """Convert a CurveArray (or CurveLoop) to a 2D polygon [(x, y), ...]."""
    points = []
    for curve in curve_array:
        p = curve.GetEndPoint(0)
        points.append((p.X, p.Y))
    return points


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


def get_shaft_opening_polygons(doc, slab_bbox, top_z):
    """Return 2-D polygons for all shaft openings overlapping the slab bbox."""
    shaft_polygons = []
    min_x, min_y, max_x, max_y = slab_bbox

    try:
        openings = list(FilteredElementCollector(doc).OfClass(Opening).ToElements())
    except Exception:
        return []

    for opening in openings:
        try:
            polygon = []
            curves = None
            try:
                curves = opening.BoundaryCurves
            except Exception:
                curves = None

            if curves is not None:
                for curve in curves:
                    p = curve.GetEndPoint(0)
                    polygon.append((p.X, p.Y))
            else:
                # Fallback: use opening bounding box projection as rectangle.
                bb = opening.get_BoundingBox(None)
                if bb is None:
                    continue
                polygon = [
                    (bb.Min.X, bb.Min.Y),
                    (bb.Max.X, bb.Min.Y),
                    (bb.Max.X, bb.Max.Y),
                    (bb.Min.X, bb.Max.Y),
                ]

            if len(polygon) < 3:
                continue

            shaft_xs = [p[0] for p in polygon]
            shaft_ys = [p[1] for p in polygon]
            s_min_x, s_max_x = min(shaft_xs), max(shaft_xs)
            s_min_y, s_max_y = min(shaft_ys), max(shaft_ys)

            # Skip shafts that don't overlap the slab bounding box
            if (s_max_x < min_x - TOLERANCE or s_min_x > max_x + TOLERANCE
                    or s_max_y < min_y - TOLERANCE or s_min_y > max_y + TOLERANCE):
                continue

            shaft_polygons.append(polygon)
        except Exception:
            continue

    return shaft_polygons


def get_drop_panel_data(doc, top_z, main_floor_id=None, slab_bbox=None, slab_thickness=None):
    """Return data for all drop panels modeled as separate floors.

    Typical modeling: drop-panel top is aligned with slab top, with extra thickness downward.
    """
    dp_list = []

    try:
        floors = list(FilteredElementCollector(doc).OfClass(Floor).ToElements())
    except Exception:
        return []

    for floor in floors:
        try:
            if main_floor_id is not None and floor.Id.IntegerValue == main_floor_id.IntegerValue:
                continue

            bb = floor.get_BoundingBox(None)
            if bb is None:
                continue

            # Typical DP condition: top aligned with slab top.
            if abs(bb.Max.Z - top_z) > TOLERANCE:
                continue

            sketch_id = getattr(floor, 'SketchId', None)
            if sketch_id is None or sketch_id.IntegerValue < 0:
                continue

            sketch = floor.Document.GetElement(sketch_id)
            if sketch is None:
                continue

            profile = sketch.Profile
            loops = []
            for curve_array in profile:
                polygon = _curve_array_to_polygon(curve_array)
                if len(polygon) >= 3:
                    area = polygon_area(polygon)
                    loops.append((area, polygon))

            if not loops:
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

            # DP should generally be thicker than the slab itself.
            if slab_thickness is not None and thickness <= slab_thickness + TOLERANCE:
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
        except Exception:
            continue

    return dp_list


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

    if start_inside:
        pts = [vary_min] + pts
    if end_inside:
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
