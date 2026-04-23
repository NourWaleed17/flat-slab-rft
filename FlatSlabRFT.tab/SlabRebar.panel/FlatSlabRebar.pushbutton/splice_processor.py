# -*- coding: utf-8 -*-
"""Stage 2: Split long segments at standard bar length with splice overlap."""
from __future__ import print_function
import math

TOLERANCE = 0.001   # feet

MAX_BAR_TOTAL_M  = 12.0
FEET_PER_METER   = 0.3048

# Standard commercial stock bar lengths (metres).  Used to snap splice positions
# so that each sub-bar consumes exactly one stock bar with minimal waste.
# Can be overridden per-run via params['standard_bar_lengths_m'].
STANDARD_BAR_LENGTHS_M = [12.0, 9.0, 6.0]


# ---------------------------------------------------------------------------
# 12-m limit helpers
# ---------------------------------------------------------------------------

def _hook_ext(params):
    """Hook extension for one slab-edge hook = slab internal depth.

    For flat-slab edge bars the hook bends down the full clear height of the
    slab (slab_thickness - 2 x cover).  Returns 0 when slab_thickness is
    absent from params (e.g. in test stubs that have not set it).
    """
    return max(0.0, params.get('slab_thickness', 0.0) - 2.0 * params.get('cover', 0.0))


def _max_bar_body(params, n_hooks=0):
    """Max straight body so total bar (body + n_hooks x hook_ext) <= 12 m.

    n_hooks:
      0 -- hookless interior splice bar (full 12 m budget for the body)
      1 -- one hooked end  (first or last sub-bar in a split)
      2 -- both ends hooked (un-split single-bar segment)

    The result is also capped at params['bar_length'] so the user's chosen
    stock-bar length is always the binding constraint when it is shorter than
    12 m minus the hook deductions.
    """
    max_total_ft = MAX_BAR_TOTAL_M / FEET_PER_METER
    body = min(params['bar_length'], max_total_ft - n_hooks * _hook_ext(params))
    # Safety floor: never let max_body collapse below a trivial positive value.
    return max(body, params.get('diameter', 0.001))


def _seg_n_hooks(seg):
    """Number of standard end hooks on a segment (0, 1, or 2)."""
    return (1 if seg.get('start_hook') else 0) + (1 if seg.get('end_hook') else 0)


def _max_body_for_seg(seg, params):
    """Correct max-body limit for one specific segment.

    Accounts for:
    - Whether the segment actually has 0, 1, or 2 end hooks.
    - J-bars (leg_ft > 0): the hooked end is a full-depth vertical leg whose
      length is already stored in leg_ft; the opposite end may still carry a
      standard 90-degree hook if end_hook is set.

    This is used for the no-split threshold only.  Sub-bars produced by a
    split each carry at most one hook, so _max_bar_body(params, 1) is used
    for the splitting step.
    """
    max_total_ft = MAX_BAR_TOTAL_M / FEET_PER_METER
    leg = seg.get('leg_ft', 0.0)
    if leg > 0.0:
        # J-bar: one end is a vertical leg (leg_ft), the other may be a
        # standard hook.  Deduct both from the 12 m budget.
        other_hook = _hook_ext(params) if seg.get('end_hook') else 0.0
        deduction = leg + other_hook
    else:
        deduction = _seg_n_hooks(seg) * _hook_ext(params)
    body = min(params['bar_length'], max_total_ft - deduction)
    return max(body, params.get('diameter', 0.001))


# ---------------------------------------------------------------------------
# Support / bay helpers
# ---------------------------------------------------------------------------

def _support_positions_1d(support_positions, direction):
    """Return sorted unique 1-D support coords along the bar axis."""
    idx = 0 if direction == 'X' else 1
    return sorted(set(round(p[idx], 4) for p in (support_positions or [])))


def _bay_widths(support_1d, bar_start, bar_end):
    """List of (col_pos, zone_half) pairs: col +/- L_bay/3 is the preferred zone."""
    cols = [c for c in support_1d if bar_start - 1e-3 <= c <= bar_end + 1e-3]
    result = []
    for i, c in enumerate(cols):
        left  = (c - cols[i - 1]) if i > 0             else (c - bar_start)
        right = (cols[i + 1] - c) if i < len(cols) - 1 else (bar_end - c)
        zone_half = (left + right) / 2.0 / 3.0
        result.append((c, zone_half))
    return result


def _in_preferred_zone_bottom(pos, bay_list):
    return any(abs(pos - c) <= z for c, z in bay_list)


# ---------------------------------------------------------------------------
# Ideal-position finder
# ---------------------------------------------------------------------------

def _find_ideal_position(natural_pos, mesh_layer, bay_list, dp_intervals, max_body, seg_start):
    """Return (ideal_pos, in_danger)."""
    if mesh_layer == 'bottom':
        if _in_preferred_zone_bottom(natural_pos, bay_list):
            return natural_pos, False
        if not bay_list:
            return natural_pos, True
        best = min(bay_list, key=lambda t: abs(natural_pos - t[0]))
        candidate = best[0]
        waste = abs(candidate - natural_pos)
        if waste <= 0.2 * max_body:
            return candidate, False
        return natural_pos, True

    else:  # top
        in_dp = any(a - 1e-4 <= natural_pos <= b + 1e-4 for a, b in (dp_intervals or []))
        if not in_dp:
            return natural_pos, False
        # Always push to nearest DP edge (top splices must never be inside DP).
        best_edge, best_dist = natural_pos, float('inf')
        for a, b in (dp_intervals or []):
            if a - 1e-4 <= natural_pos <= b + 1e-4:
                for edge in (a, b):
                    d = abs(natural_pos - edge)
                    if d < best_dist:
                        best_dist, best_edge = d, edge
        return best_edge, (best_dist > 0.2 * max_body)


# ---------------------------------------------------------------------------
# Stock-length snap helper
# ---------------------------------------------------------------------------

def _snap_to_stock_boundary(prev_end, ideal_pos, max_body, hook_ext, stock_lengths_m):
    """Snap ideal_pos so the resulting sub-bar body matches a standard stock bar.

    For each stock bar S metres, the target body that uses exactly one stock bar
    with no waste is:  body = S/FEET_PER_METER - hook_ext.

    Snapping is only attempted when the candidate falls inside the valid body
    range [0.75 * max_body, max_body].  The closest candidate (by distance to
    ideal_pos) wins.  If no stock target falls in range, ideal_pos is returned
    unchanged.
    """
    lo = prev_end + 0.75 * max_body
    hi = prev_end + max_body

    best_pos  = ideal_pos
    best_dist = float('inf')
    for s_m in sorted(stock_lengths_m, reverse=True):
        target_body = s_m / FEET_PER_METER - hook_ext
        if target_body <= 0.0:
            continue
        candidate = prev_end + target_body
        if candidate < lo - TOLERANCE or candidate > hi + TOLERANCE:
            continue
        dist = abs(candidate - ideal_pos)
        if dist < best_dist:
            best_dist = dist
            best_pos  = max(lo, min(hi, candidate))
    return best_pos


# ---------------------------------------------------------------------------
# Core split
# ---------------------------------------------------------------------------

def _split_segment(seg, params, stagger_splices, support_1d, mesh_layer,
                   _ld=None, _max_body_1h=None, _max_body_0h=None,
                   stock_lengths_m=None):
    """Split one segment honouring max bar body, preferred splice zones, stagger.

    Bar-length optimisation strategy
    ---------------------------------
    Natural splice positions use a *greedy-fill* strategy: the first bar always
    consumes the full max_body before the splice starts.  This avoids short
    leading bars (e.g. a 10 m bar when a 12 m bar would fit) and minimises
    the number of cut bars needed.

    After placing natural positions, two optional snaps are applied in order:
      1. Zone snap  (_find_ideal_position) -- moves toward structural column
         zones (bottom) or DP edges (top).
      2. Stock snap (_snap_to_stock_boundary) -- nudges the position so the
         sub-bar body matches a standard commercial bar length (12 m, 9 m, 6 m …).
         Only applied when the candidate stays inside [0.75, 1.0] × max_body.

    Stagger is applied ONLY to splice joints i >= 1.  The first bar (i == 0)
    always fills to the full max_body so that the greedy fill is not shortened
    by the stagger phase offset.

    The min-body clamp is 0.75 × max_body (raised from the old 0.30) so that
    zone- or stock-snapping can never produce a bar shorter than 75 % of the
    chosen stock length.

    Pre-computed constants passed from process_splices (avoid recomputing
    per segment):
      _ld          -- development / lap length (feet)
      _max_body_1h -- max body for a sub-bar with one hooked end
      _max_body_0h -- (reserved; not used for clamping but available)
      stock_lengths_m -- list of standard bar lengths in metres
    """
    ld = _ld if _ld is not None else params.get('ld', params['splice_length'])
    stock_lengths_m = stock_lengths_m or params.get('standard_bar_lengths_m', STANDARD_BAR_LENGTHS_M)
    hook_ext = _hook_ext(params)

    # --- No-split check using per-segment hook accounting -------------------
    max_body_nosplit = _max_body_for_seg(seg, params)
    seg_len      = seg['end'] - seg['start']
    dp_intervals = seg.get('dp_intervals', [])

    if seg_len <= max_body_nosplit + TOLERANCE:
        return [seg]

    # --- Split needed.  Cap sub-bars at 1-hook max_body. --------------------
    max_body = _max_body_1h if _max_body_1h is not None else _max_bar_body(params, 1)

    effective_step = max(max_body - ld, max_body * 0.5)
    if effective_step < TOLERANCE:
        print('[splice] WARNING: effective_step={:.6f} too small, seg skipped '
              '(max_body={:.3f} ld={:.3f})'.format(effective_step, max_body, ld))
        return [seg]

    n_splices = max(1, int(math.ceil(seg_len / effective_step)) - 1)
    if n_splices > 20:
        print('[splice] WARNING: n_splices={} capped to 20 '
              '(seg_len={:.2f} effective_step={:.4f})'.format(
                  n_splices, seg_len, effective_step))
        n_splices = 20

    bay_list = _bay_widths(support_1d, seg['start'], seg['end'])

    # Greedy-fill natural positions: fill each bar to max_body before splicing.
    # This ensures the first bar is always as long as possible (up to max_body)
    # rather than splitting evenly, which would waste stock bars.
    natural_positions = []
    pos = seg['start']
    for _ in range(n_splices):
        pos += max_body
        natural_positions.append(pos)
        pos -= ld   # next bar starts with Ld overlap

    # Stagger phase: even/odd rows offset in opposite directions.
    # Applied only to splice joints i >= 1 so the first bar body is never
    # shortened below max_body by the stagger offset.
    if stagger_splices:
        row_phase = -0.5 if (seg.get('index', 0) % 2) == 0 else 0.5
    else:
        row_phase = 0.0

    # Resolve final splice positions
    splice_positions = []
    danger_flags     = []
    prev_end         = seg['start']

    for i, nat in enumerate(natural_positions):
        # 1. Zone snap (structural preference)
        ideal, danger = _find_ideal_position(
            nat, mesh_layer, bay_list, dp_intervals, max_body, seg['start']
        )

        # 2. Stock-length snap (minimise bar cutting waste).
        #    Only valid inside [0.75, 1.0] × max_body from prev_end.
        ideal = _snap_to_stock_boundary(prev_end, ideal, max_body, hook_ext, stock_lengths_m)

        # 3. Stagger offset — skip on the first splice to preserve greedy fill.
        phase = 0.0 if i == 0 else row_phase
        sp = ideal + phase * ld

        # 4. Clamp: minimum 75 % of max_body (raised from 30 %) to prevent
        #    short bars; maximum = full max_body.
        sp = max(sp, prev_end + max_body * 0.75)
        sp = min(sp, prev_end + max_body)
        sp = min(sp, seg['end'] - TOLERANCE)

        splice_positions.append(sp)
        danger_flags.append(danger)
        # Track prev_end using the ACTUAL splice length (1.3×ld when dangerous)
        # so the next sub-bar's greedy-fill clamping is correct.  Using plain ld
        # here would under-estimate the next bar's start, allowing it to exceed
        # max_body when a danger multiplier applies.
        actual_sl = 1.3 * ld if danger else ld
        prev_end = sp - actual_sl

    # Splice lengths (1.3x Ld at structurally dangerous positions)
    splice_lengths = [1.3 * ld if danger_flags[i] else ld
                      for i in range(len(splice_positions))]

    # Build sub-segments
    sub_segs = []
    current_start      = seg['start']
    current_start_hook = seg.get('start_hook', False)

    for sp, sl in zip(splice_positions, splice_lengths):
        sub = dict(seg)
        sub['start']      = current_start
        sub['end']        = sp
        sub['start_hook'] = current_start_hook
        sub['end_hook']   = False
        sub['splice_end'] = True
        sub['splice_length_used'] = sl
        sub_segs.append(sub)

        current_start      = sp - sl
        current_start_hook = False

        if current_start <= seg['start'] + TOLERANCE:
            break

    # Final sub-segment: guard against a degenerate zero-length tail that can
    # arise when clamping pushes the last splice very close to seg['end'].
    final_start = current_start
    final_end   = seg['end']
    if final_end - final_start >= TOLERANCE:
        sub = dict(seg)
        sub['start']      = final_start
        sub['end']        = final_end
        sub['start_hook'] = current_start_hook
        sub['end_hook']   = seg.get('end_hook', False)
        sub.pop('splice_end', None)
        sub_segs.append(sub)

    return sub_segs


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def process_splices(segments, params, support_positions=None):
    """Split any segment longer than its max_bar_body into lapped sub-segments.

    support_positions : list of (x, y) from geometry.get_support_positions_2d,
                        used to locate preferred splice zones.
    """
    stagger_splices = params.get('stagger_splices', False)
    stock_lengths_m = params.get('standard_bar_lengths_m', STANDARD_BAR_LENGTHS_M)

    # Precompute constants shared across all segments.
    _ld          = params.get('ld', params['splice_length'])
    _max_body_0h = _max_bar_body(params, 0)   # hookless bar (interior splice)
    _max_body_1h = _max_bar_body(params, 1)   # one hooked end (split sub-bars)
    _max_body_2h = _max_bar_body(params, 2)   # both ends hooked (un-split bar)

    print('[splice] n={} bar_length={:.3f}ft ({:.0f}mm)  '
          'hook_ext={:.1f}mm  '
          'max_body 0h={:.3f}ft ({:.0f}mm)  '
          '1h={:.3f}ft ({:.0f}mm)  '
          '2h={:.3f}ft ({:.0f}mm)  '
          'ld={:.3f}ft ({:.0f}mm)  '
          'stock={}m'.format(
              len(segments),
              params['bar_length'],         params['bar_length'] * 304.8,
              _hook_ext(params) * 304.8,
              _max_body_0h,                 _max_body_0h * 304.8,
              _max_body_1h,                 _max_body_1h * 304.8,
              _max_body_2h,                 _max_body_2h * 304.8,
              _ld,                          _ld          * 304.8,
              stock_lengths_m,
          ))

    # Precompute support 1-D lists once per direction -- NOT inside the loop.
    support_x = _support_positions_1d(support_positions, 'X')
    support_y = _support_positions_1d(support_positions, 'Y')

    result = []
    for i, seg in enumerate(segments):
        if i % 1000 == 0 and i > 0:
            print('[splice] processed {}/{} segments -> {} so far'.format(
                i, len(segments), len(result)))
        direction  = seg.get('direction', 'X')
        mesh_layer = seg.get('mesh_layer', 'bottom')
        support_1d = support_x if direction == 'X' else support_y
        result.extend(
            _split_segment(seg, params, stagger_splices, support_1d, mesh_layer,
                           _ld=_ld, _max_body_1h=_max_body_1h,
                           _max_body_0h=_max_body_0h,
                           stock_lengths_m=stock_lengths_m)
        )

    print('[splice] done: {} segments -> {} final'.format(len(segments), len(result)))
    return result
