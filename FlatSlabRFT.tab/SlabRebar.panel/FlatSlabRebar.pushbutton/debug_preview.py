# -*- coding: utf-8 -*-
"""Preview utilities for slab, drop panels, and generated bar segments."""
from __future__ import print_function
import math

from Autodesk.Revit.DB import Line, XYZ, Transaction


def _draw_polyline(doc, view, points, z, max_edge_len=None):
    """Draw a closed polygon as detail curves in the active view.

    max_edge_len : optional upper bound (feet) for individual edge lengths.
                  Edges longer than this are silently skipped.  Used to
                  suppress diagonal 'spider web' lines that appear when an
                  opening's BoundaryCurves are stored in non-sequential order.
    Returns (ids, filtered_count, longest_edge_ft).
    """
    ids = []
    filtered = 0
    longest = 0.0

    if not points or len(points) < 3:
        return ids, filtered, longest

    for i in range(len(points)):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % len(points)]
        edge_len = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
        if edge_len > longest:
            longest = edge_len
        if max_edge_len is not None and edge_len > max_edge_len:
            filtered += 1
            continue
        p1 = XYZ(x1, y1, z)
        p2 = XYZ(x2, y2, z)
        if p1.DistanceTo(p2) <= 1e-6:
            continue
        curve = doc.Create.NewDetailCurve(view, Line.CreateBound(p1, p2))
        ids.append(curve.Id)
    return ids, filtered, longest


def _segment_to_points(seg):
    """Convert one segment dict to XYZ endpoints."""
    z = seg.get('z', 0.0)
    if seg['direction'] == 'X':
        p1 = XYZ(seg['start'], seg['fixed_val'], z)
        p2 = XYZ(seg['end'], seg['fixed_val'], z)
    else:
        p1 = XYZ(seg['fixed_val'], seg['start'], z)
        p2 = XYZ(seg['fixed_val'], seg['end'], z)
    return p1, p2


def _sample_segments(segments, limit):
    """Return an evenly sampled subset from segments."""
    total = len(segments)
    if limit <= 0 or total <= limit:
        return list(segments)

    sampled = []
    step = float(total - 1) / float(limit - 1)
    used = set()
    for i in range(limit):
        idx = int(round(i * step))
        if idx < 0:
            idx = 0
        if idx >= total:
            idx = total - 1
        if idx in used:
            continue
        used.add(idx)
        sampled.append(segments[idx])
    return sampled


def _slab_dims(slab_data):
    """Return (slab_width_ft, slab_height_ft) from bbox, or (None, None)."""
    bbox = slab_data.get('bbox')
    if not bbox or len(bbox) < 4:
        return None, None
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def draw_preview(doc, view, slab_data, shaft_polygons, dp_data_list, final_segments, max_segment_lines):
    """Draw preview detail curves and return stats with created element ids."""
    info = {
        'created_ids': [],
        'segments_total': len(final_segments),
        'segments_drawn': 0,
        'segments_skipped': 0,
        'outlines_drawn': 0,
        'outline_edges_filtered': 0,
        'longest_outline_edge_m': 0.0,
    }
    if view is None:
        return info

    z = slab_data.get('top_z', 0.0)
    seg_limit = int(max(1, max_segment_lines))
    segs_to_draw = _sample_segments(final_segments, seg_limit)
    info['segments_drawn'] = len(segs_to_draw)
    info['segments_skipped'] = max(0, len(final_segments) - len(segs_to_draw))

    slab_w, slab_h = _slab_dims(slab_data)
    FT_TO_M = 0.3048

    # Per-polygon-type max edge limits (feet).
    # Shaft/void openings and DPs are physically much smaller than the slab,
    # so any edge longer than these limits is an artefact of wrong curve order.
    if slab_w and slab_h:
        slab_short = min(slab_w, slab_h)
        # Slab outer boundary: allow edges up to the full short dimension.
        max_slab_edge  = slab_short
        # Shaft openings: must fit within 20 % of the slab short dimension.
        max_shaft_edge = slab_short * 0.20
        # Drop panels: must fit within 10 % of the slab short dimension.
        max_dp_edge    = slab_short * 0.10
    else:
        max_slab_edge = max_shaft_edge = max_dp_edge = None

    def _record(ids, filt, longest):
        info['created_ids'].extend(ids)
        info['outline_edges_filtered'] += filt
        longest_m = longest * FT_TO_M
        if longest_m > info['longest_outline_edge_m']:
            info['longest_outline_edge_m'] = round(longest_m, 1)
        info['outlines_drawn'] += 1

    t = Transaction(doc, 'Flat Slab Rebar Preview')
    t.Start()
    try:
        # Slab outer boundary (long edges allowed — it's a big polygon)
        _record(*_draw_polyline(doc, view, slab_data.get('outer_polygon', []), z, max_slab_edge))

        # Shaft/void boundaries — draw as bounding-box rectangles to avoid
        # spider-web artefacts from Opening.BoundaryCurves stored in
        # non-sequential order.
        for poly in shaft_polygons:
            if poly:
                xs = [p[0] for p in poly]
                ys = [p[1] for p in poly]
                bbox_rect = [
                    (min(xs), min(ys)),
                    (max(xs), min(ys)),
                    (max(xs), max(ys)),
                    (min(xs), max(ys)),
                ]
                _record(*_draw_polyline(doc, view, bbox_rect, z, None))

        # Drop panel boundaries
        for dp in dp_data_list:
            _record(*_draw_polyline(doc, view, dp.get('polygon', []), z, max_dp_edge))

        # Rebar centerline preview
        for seg in segs_to_draw:
            p1, p2 = _segment_to_points(seg)
            if p1.DistanceTo(p2) <= 1e-6:
                continue
            curve = doc.Create.NewDetailCurve(view, Line.CreateBound(p1, p2))
            info['created_ids'].append(curve.Id)

        t.Commit()
    except Exception:
        t.RollBack()
        raise

    return info


def clear_preview(doc, element_ids):
    """Delete preview elements created by draw_preview."""
    if not element_ids:
        return
    t = Transaction(doc, 'Clear Flat Slab Rebar Preview')
    t.Start()
    try:
        for eid in element_ids:
            try:
                doc.Delete(eid)
            except Exception:
                continue
        t.Commit()
    except Exception:
        t.RollBack()
        raise
