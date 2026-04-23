# -*- coding: utf-8 -*-
"""Stage 1: Split bar rows at slab boundary, shafts and drop panels."""
from __future__ import print_function

from geometry import get_obstacle_intervals, clip_bar_to_slab

TOLERANCE = 0.001      # feet
MIN_BAR_LENGTH = 0.5   # feet (~150 mm) — skip segments shorter than this


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
                  fixed_val=0.0, direction='X', z=0.0, index=0):
    """Split one clipped bar row into segments, inserting gaps at obstacles.

    Rules
    -----
    Slab edge    → hook at both ends of the overall bar line.
    Shaft        → hook on both approach and departure sides; bar skips the shaft.
    Drop panel   → bottom bars only: straight end after Ld penetration into DP,
                   new bar starts Ld back from exit face.  Top bars ignore DPs.
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
    start_hook    = True   # slab edge always gets a hook

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
            # Bar runs up to shaft edge with a hook
            seg = _make_segment(current_pos, obs_enter, fixed_val, direction, z, index,
                                 start_hook, True)
            if seg:
                segments.append(seg)
            current_pos = obs_exit
            start_hook  = True   # new bar after shaft always starts with hook

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
                         start_hook, True)
    if seg:
        segments.append(seg)

    return segments


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def process_bar_row(bar_row, outer_polygon, shaft_polygons, dp_data_list, params, mesh_layer):
    """Process one raw bar row through the full obstacle pipeline.

    Returns a list of segment dicts ready for Stage 2 (splice processing).
    """
    fixed_val = bar_row['fixed_val']
    vary_min  = bar_row['vary_min']
    vary_max  = bar_row['vary_max']
    direction = bar_row['direction']
    z         = bar_row.get('z', 0.0)
    index     = bar_row.get('index', 0)
    axis      = direction  # 'X' or 'Y' — same meaning in geometry functions

    # Step 1: Clip to slab outer boundary
    clipped = clip_bar_to_slab(fixed_val, vary_min, vary_max, outer_polygon, axis)
    if clipped is None:
        return []
    start, end = clipped
    if end - start < TOLERANCE:
        return []

    # Step 2: Shaft intervals (sketch voids + Opening shafts)
    shaft_intervals = []
    for shaft_poly in shaft_polygons:
        intervals = get_obstacle_intervals(fixed_val, start, end, shaft_poly, axis)
        shaft_intervals.extend(intervals)
    shaft_intervals = _merge_intervals(shaft_intervals)

    # Step 3: Drop panel intervals (bottom bars only)
    dp_intervals = []
    if mesh_layer == 'bottom':
        for dp_data in dp_data_list:
            intervals = get_obstacle_intervals(fixed_val, start, end, dp_data['polygon'], axis)
            dp_intervals.extend(intervals)
        dp_intervals = _merge_intervals(dp_intervals)

    # Step 4: Split
    return split_bar_row(
        start, end,
        shaft_intervals, dp_intervals,
        params, mesh_layer,
        fixed_val=fixed_val, direction=direction, z=z, index=index
    )
