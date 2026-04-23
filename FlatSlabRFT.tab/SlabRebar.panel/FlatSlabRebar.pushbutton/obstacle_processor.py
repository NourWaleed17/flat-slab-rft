# -*- coding: utf-8 -*-
"""Stage 1: Split bar rows at slab boundary, shafts and drop panels."""
from __future__ import print_function

from geometry import get_obstacle_intervals, clip_bar_to_slab_intervals

TOLERANCE = 0.001      # feet
MIN_BAR_LENGTH = 0.25  # feet (~75 mm) — skip segments shorter than this


# ---------------------------------------------------------------------------
# Interval helpers
# ---------------------------------------------------------------------------

def _merge_intervals(intervals):
    """Merge a list of (enter, exit) intervals, returning sorted non-overlapping list."""
    if not intervals:
        return []
    intervals = sorted(intervals, key=lambda t: t[0])
    merged = [intervals[0]]
    for enter, exit_ in intervals[1:]:
        last_enter, last_exit = merged[-1]
        if enter <= last_exit + TOLERANCE:
            merged[-1] = (last_enter, max(last_exit, exit_))
        else:
            merged.append((enter, exit_))
    return merged


def _resolve_obstacle_overlaps(obstacles):
    """Resolve overlaps between shaft and DP obstacles.

    Shaft takes priority: DP intervals are split around any shaft sub-interval.
    Returns a sorted list of (type, enter, exit) tuples with no overlaps.
    """
    shafts = [(t, a, b) for t, a, b in obstacles if t == 'shaft']
    dps    = [(t, a, b) for t, a, b in obstacles if t == 'dp']

    # Subtract shaft regions from each DP interval
    resolved_dps = []
    for _, dp_enter, dp_exit in dps:
        remaining = [(dp_enter, dp_exit)]
        for _, shaft_enter, shaft_exit in shafts:
            new_remaining = []
            for seg_a, seg_b in remaining:
                if shaft_exit <= seg_a or shaft_enter >= seg_b:
                    new_remaining.append((seg_a, seg_b))
                else:
                    if seg_a < shaft_enter:
                        new_remaining.append((seg_a, shaft_enter))
                    if shaft_exit < seg_b:
                        new_remaining.append((shaft_exit, seg_b))
            remaining = new_remaining
        for a, b in remaining:
            if b - a > TOLERANCE:
                resolved_dps.append(('dp', a, b))

    result = shafts + resolved_dps
    result.sort(key=lambda t: t[1])
    return result


# ---------------------------------------------------------------------------
# Segment factory
# ---------------------------------------------------------------------------

def _make_segment(start, end, fixed_val, direction, z, index, start_hook, end_hook):
    """Return a segment dict, or None if shorter than the minimum bar length."""
    if end - start < MIN_BAR_LENGTH:
        return None
    return {
        'start':      start,
        'end':        end,
        'fixed_val':  fixed_val,
        'direction':  direction,
        'z':          z,
        'index':      index,
        'start_hook': start_hook,
        'end_hook':   end_hook,
    }


# ---------------------------------------------------------------------------
# Core splitting logic
# ---------------------------------------------------------------------------

def split_bar_row(start, end, shaft_intervals, dp_intervals, params, mesh_layer,
                  fixed_val=0.0, direction='X', z=0.0, index=0, no_hooks=False):
    """Split one clipped bar row into segments, inserting gaps at obstacles.

    Rules
    -----
    Slab edge    → hook at both ends of the overall bar line (unless no_hooks=True).
    Shaft        → hook on both approach and departure sides; bar skips the shaft.
    Drop panel   → bottom bars only: straight end after Ld penetration into DP,
                   new bar starts Ld back from exit face.  Top bars ignore DPs.

    no_hooks     → when True all hook flags are forced False (used for add RFT bars
                   which are interior bars and must be placed as straight elements).
    """
    ld = params['ld']

    # Build combined obstacle list
    obstacles = []
    for enter, exit_ in shaft_intervals:
        obstacles.append(('shaft', enter, exit_))
    if mesh_layer == 'bottom':
        for enter, exit_ in dp_intervals:
            obstacles.append(('dp', enter, exit_))

    obstacles.sort(key=lambda t: t[1])
    obstacles = _resolve_obstacle_overlaps(obstacles)

    segments = []
    current_pos   = start
    start_hook    = False if no_hooks else True   # slab edge hook only for main bars

    for obs_type, obs_enter, obs_exit in obstacles:
        obs_exit = min(obs_exit, end)   # clamp to bar end

        # Skip obstacles entirely behind current position
        if obs_exit <= current_pos + TOLERANCE:
            continue

        # Skip obstacles that start at or beyond bar end
        if obs_enter >= end - TOLERANCE:
            break

        # If we are already inside this obstacle (e.g. bar started inside a DP)
        if obs_enter <= current_pos + TOLERANCE:
            if obs_type == 'shaft':
                current_pos = obs_exit
                start_hook  = True
            else:  # dp
                # Treat as if bar starts Ld before exit face
                new_start = obs_exit - min(ld, (obs_exit - current_pos) / 2.0)
                current_pos = max(new_start, current_pos)
                start_hook  = False
            continue

        # ----- Segment before the obstacle -----
        if obs_type == 'shaft':
            # Bar runs up to shaft edge with a hook (suppressed for no_hooks bars)
            seg = _make_segment(current_pos, obs_enter, fixed_val, direction, z, index,
                                 start_hook, False if no_hooks else True)
            if seg:
                segments.append(seg)
            current_pos = obs_exit
            start_hook  = False if no_hooks else True   # new bar after shaft

        elif obs_type == 'dp' and mesh_layer == 'bottom':
            dp_width = obs_exit - obs_enter
            # Cap penetration so both sides don't overlap in a narrow DP
            max_pen  = min(ld, dp_width / 2.0)
            seg_end  = obs_enter + max_pen

            seg = _make_segment(current_pos, seg_end, fixed_val, direction, z, index,
                                 start_hook, False)
            if seg:
                segments.append(seg)

            # Next bar starts Ld back from exit face (straight at DP side)
            current_pos = obs_exit - max_pen
            start_hook  = False

    # Final segment to slab far edge
    seg = _make_segment(current_pos, end, fixed_val, direction, z, index,
                         start_hook, False if no_hooks else True)
    if seg:
        segments.append(seg)

    return segments


# ---------------------------------------------------------------------------
# Obstacle bbox cache  (pre-computed once per batch, passed into process_bar_row)
# ---------------------------------------------------------------------------

def build_obstacle_cache(shaft_polygons, dp_data_list):
    """Pre-compute obstacle bounding boxes for fast scanline pre-filtering.

    For each shaft/DP polygon, store its (min_x, min_y, max_x, max_y) bbox.
    A scanline at fixed_val only needs to call get_obstacle_intervals() for
    obstacles whose bbox actually straddles that scanline.  Non-overlapping
    obstacles are skipped in O(1) instead of O(polygon_vertices).

    Call once before iterating rows, then pass the returned dict to every
    process_bar_row() call via obstacle_cache=.

    Drop-panel bboxes are read from dp_data['bbox'] which geometry.py already
    computes when building dp_data_list, so no extra work is done there.
    """
    shaft_bboxes = []
    for poly in (shaft_polygons or []):
        if poly:
            xs = [p[0] for p in poly]
            ys = [p[1] for p in poly]
            shaft_bboxes.append((min(xs), min(ys), max(xs), max(ys)))
        else:
            shaft_bboxes.append(None)

    dp_bboxes = [dp.get('bbox') for dp in (dp_data_list or [])]

    return {'shaft_bboxes': shaft_bboxes, 'dp_bboxes': dp_bboxes}


def _scanline_hits_bbox(bbox, fixed_val, axis):
    """Return True when the axis-aligned scanline at fixed_val intersects bbox.

    axis='X' means rows run along X, scanline is a horizontal line at Y=fixed_val.
    axis='Y' means rows run along Y, scanline is a vertical line at X=fixed_val.
    Returns True when bbox is None (unknown) so the caller falls back to the
    full polygon check.
    """
    if bbox is None:
        return True
    min_x, min_y, max_x, max_y = bbox
    if axis == 'X':
        return min_y - TOLERANCE <= fixed_val <= max_y + TOLERANCE
    else:
        return min_x - TOLERANCE <= fixed_val <= max_x + TOLERANCE


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def process_bar_row(bar_row, outer_polygon, shaft_polygons, dp_data_list, params, mesh_layer,
                    obstacle_cache=None):
    """Process one raw bar row through the full obstacle pipeline.

    Returns a list of segment dicts ready for Stage 2 (splice processing).

    obstacle_cache  : optional dict returned by build_obstacle_cache().
                      When supplied, obstacles whose bounding box does not
                      straddle the current scanline are skipped before the
                      O(polygon_vertices) intersection test is performed.
                      When omitted, every obstacle is tested unconditionally
                      (original behaviour, fully backwards compatible).
    """
    fixed_val = bar_row['fixed_val']
    vary_min  = bar_row['vary_min']
    vary_max  = bar_row['vary_max']
    direction = bar_row['direction']
    z         = bar_row.get('z', 0.0)
    index     = bar_row.get('index', 0)
    no_hooks  = bar_row.get('no_hooks', False)
    skip_dp   = bar_row.get('skip_dp', False)
    axis      = direction  # 'X' or 'Y' — same meaning in geometry functions

    # Unpack pre-computed bboxes (None entries → unconditional test, safe fallback).
    _shaft_bboxes = (obstacle_cache or {}).get('shaft_bboxes') or []
    _dp_bboxes    = (obstacle_cache or {}).get('dp_bboxes')    or []

    # Step 1: Clip to slab outer boundary (supports multiple inside intervals
    # for concave / stepped slab outlines).
    slab_intervals = clip_bar_to_slab_intervals(
        fixed_val, vary_min, vary_max, outer_polygon, axis
    )
    if not slab_intervals:
        return []

    segments = []
    for start, end in slab_intervals:
        if end - start < TOLERANCE:
            continue

        # Step 2: Shaft intervals — skip if scanline misses obstacle bbox.
        shaft_intervals = []
        for i, shaft_poly in enumerate(shaft_polygons):
            _bbox = _shaft_bboxes[i] if i < len(_shaft_bboxes) else None
            if not _scanline_hits_bbox(_bbox, fixed_val, axis):
                continue
            intervals = get_obstacle_intervals(fixed_val, start, end, shaft_poly, axis)
            shaft_intervals.extend(intervals)
        shaft_intervals = _merge_intervals(shaft_intervals)

        # Step 3: Drop panel intervals — same bbox pre-filter.
        dp_intervals = []
        for i, dp_data in enumerate(dp_data_list):
            _bbox = _dp_bboxes[i] if i < len(_dp_bboxes) else None
            if not _scanline_hits_bbox(_bbox, fixed_val, axis):
                continue
            intervals = get_obstacle_intervals(fixed_val, start, end, dp_data['polygon'], axis)
            dp_intervals.extend(intervals)
        dp_intervals = _merge_intervals(dp_intervals)

        # Step 4: Split (bottom bars avoid DP penetration; top bars pass through)
        new_segs = split_bar_row(
            start, end,
            shaft_intervals, dp_intervals if (mesh_layer == 'bottom' and not skip_dp) else [],
            params, mesh_layer,
            fixed_val=fixed_val, direction=direction, z=z, index=index,
            no_hooks=no_hooks
        )

        # Attach metadata to all segments for splice_processor.
        for seg in new_segs:
            seg['mesh_layer'] = mesh_layer
            if mesh_layer == 'top' and dp_intervals:
                seg['dp_intervals'] = dp_intervals
            # Propagate per-row metadata needed for rebar set grouping.
            for _key in ('spacing_ft', 'diam_mm', 'is_add_rft', 'leg_ft', 'has_hook', 'hook_at_max'):
                if _key in bar_row:
                    seg[_key] = bar_row[_key]

        segments.extend(new_segs)

    return segments
