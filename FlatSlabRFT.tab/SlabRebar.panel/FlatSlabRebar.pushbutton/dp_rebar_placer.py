# -*- coding: utf-8 -*-
"""Place drop panel rebar in Revit."""
from __future__ import print_function

import clr
clr.AddReference('System')
from System.Collections.Generic import List

from Autodesk.Revit.DB import (Line, XYZ, Curve, Transaction, FailureHandlingOptions,
                               IFailuresPreprocessor, FailureProcessingResult,
                               FailureSeverity, TransactionStatus)
from Autodesk.Revit.DB.Structure import Rebar, RebarStyle, RebarHookOrientation
from geometry import get_obstacle_intervals

ROW_EPS = 0.002  # feet (~0.6 mm), used to avoid edge-coincident scanline ambiguity


_PreprocessorBase = IFailuresPreprocessor if IFailuresPreprocessor is not None else object


class _SilentFailuresPreprocessor(_PreprocessorBase):
    """Silently resolve all Revit failure messages without showing modal dialogs."""
    def PreprocessFailures(self, failuresAccessor):
        has_unresolvable = False
        for msg in list(failuresAccessor.GetFailureMessages()):
            if msg.GetSeverity() == FailureSeverity.Warning:
                failuresAccessor.DeleteWarning(msg)
            elif msg.HasResolutions():
                try:
                    failuresAccessor.ResolveFailure(msg)
                except Exception:
                    has_unresolvable = True
            else:
                has_unresolvable = True
        if has_unresolvable:
            return FailureProcessingResult.ProceedWithRollBack
        return FailureProcessingResult.Continue


_PREPROCESSOR = _SilentFailuresPreprocessor()


def _configure_fast_transaction(t):
    """Suppress all failure dialogs so placement never blocks on a popup."""
    try:
        opts = t.GetFailureHandlingOptions()
        opts.SetFailuresPreprocessor(_PREPROCESSOR)
        opts.SetClearAfterRollback(True)
        opts.SetDelayedMiniWarnings(True)
        opts.SetForcedModalHandling(False)
        t.SetFailureHandlingOptions(opts)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Row generation
# ---------------------------------------------------------------------------

def generate_dp_bar_rows(dp_data, spacing, cover, direction, offset=0.0):
    """Sweep bar positions across the drop panel bbox."""
    min_x, min_y, max_x, max_y = dp_data['bbox']
    rows = []

    if direction == 'X':
        y = min_y + cover + max(0.0, offset)
        i = 0
        while y <= max_y - cover + 1e-9:
            rows.append({'pos': y, 'direction': 'X', 'index': i})
            y += spacing
            i += 1
    else:
        x = min_x + cover + max(0.0, offset)
        i = 0
        while x <= max_x - cover + 1e-9:
            rows.append({'pos': x, 'direction': 'Y', 'index': i})
            x += spacing
            i += 1

    return rows


# ---------------------------------------------------------------------------
# Geometry and create helpers
# ---------------------------------------------------------------------------

def _z_layer(dp_data, params, base_z):
    """Return safe bar Zs within drop-panel thickness."""
    dp_top_z = dp_data.get('top_z')
    dp_bottom_z = dp_data.get('bottom_z')

    if dp_top_z is None:
        # Prefer reconstructing from bottom_z + thickness rather than using base_z
        # (base_z is a bar position, not the DP ceiling).
        if dp_bottom_z is not None:
            dp_top_z = dp_bottom_z + max(dp_data.get('thickness', 0.0), 0.0)
        else:
            dp_top_z = base_z + max(dp_data.get('thickness', 0.0), 0.0)
    if dp_bottom_z is None:
        dp_bottom_z = dp_top_z - max(dp_data.get('thickness', 0.0), 0.0)

    cover = params['cover']
    z_bot = dp_bottom_z + cover

    # Vertical leg = dp_thickness - 2*cover  →  z_top = dp_top_z - cover
    thickness = max(dp_data.get('thickness', 0.0), 0.0)
    v_leg = max(0.0, thickness - 2.0 * cover)
    z_top = min(dp_top_z - cover, z_bot + v_leg)

    print('[DP z_layer] dp_bottom_z={:.4f}ft ({:.0f}mm)  dp_top_z={:.4f}ft ({:.0f}mm)  '
          'thickness={:.4f}ft ({:.0f}mm)  cover={:.4f}ft ({:.0f}mm)  '
          'v_leg={:.4f}ft ({:.0f}mm)  z_bot={:.4f}ft  z_top={:.4f}ft  delta={:.4f}ft ({:.0f}mm)'.format(
        dp_bottom_z, dp_bottom_z * 304.8,
        dp_top_z, dp_top_z * 304.8,
        thickness, thickness * 304.8,
        cover, cover * 304.8,
        v_leg, v_leg * 304.8,
        z_bot, z_top, z_top - z_bot, (z_top - z_bot) * 304.8,
    ))

    if z_top <= z_bot + 1e-6:
        print('[DP z_layer] REJECTED: z_top <= z_bot, cannot place staple')
        return None, None

    return z_bot, z_top


def _preferred_min_span(params):
    """Preferred clear span for staple bars inside one DP interval.

    Spans shorter than this are still considered for straight fallback bars,
    but staples are more likely to fail shape solving on very short pieces.
    """
    dia = max(0.0, params.get('diameter', 0.0))
    cover = max(0.0, params.get('cover', 0.0))
    return max(8.0 * dia, 2.0 * cover + 2.0 * dia, 0.20)


def _hard_min_span(params):
    """Hard clear-span floor below which bar creation is not attempted."""
    dia = max(0.0, params.get('diameter', 0.0))
    cover = max(0.0, params.get('cover', 0.0))
    return max(2.0 * dia + 2.0 * cover, 0.08)


def _sum_interval_lengths(intervals):
    total = 0.0
    for a, b in intervals:
        if b > a:
            total += (b - a)
    return total


def _intervals_match(ivs1, ivs2, tol=0.01):
    """True if two interval lists are identical within positional tolerance.

    Each element may be a 2-tuple (a, b) or a 4-tuple (a, b, left_flag, right_flag).
    Positional values are compared within tol; boolean flags must match exactly.
    """
    if len(ivs1) != len(ivs2):
        return False
    for seg1, seg2 in zip(ivs1, ivs2):
        if abs(seg1[0] - seg2[0]) > tol or abs(seg1[1] - seg2[1]) > tol:
            return False
        if len(seg1) > 2 and len(seg2) > 2 and seg1[2:] != seg2[2:]:
            return False
    return True


def _group_rows_by_intervals(rows_and_ivs, max_gap=None):
    """Group consecutive (pos, intervals) entries that share the same interval pattern.

    Returns list of (positions_list, representative_intervals).
    Each group becomes one rebar set per interval in the group.

    max_gap: if set, consecutive rows whose positions differ by more than max_gap
             are forced into separate groups even if their interval patterns match.
             Use spacing * 1.5 to split groups across shaft-eliminated row gaps.
    """
    if not rows_and_ivs:
        return []
    groups = []
    cur_positions = [rows_and_ivs[0][0]]
    cur_ivs = rows_and_ivs[0][1]
    for pos, ivs in rows_and_ivs[1:]:
        gap_break = max_gap is not None and (pos - cur_positions[-1]) > max_gap + 1e-6
        if _intervals_match(ivs, cur_ivs) and not gap_break:
            cur_positions.append(pos)
        else:
            groups.append((cur_positions, cur_ivs))
            cur_positions = [pos]
            cur_ivs = ivs
    groups.append((cur_positions, cur_ivs))
    return groups


def _polygon_area(polygon):
    """Unsigned polygon area."""
    n = len(polygon)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += polygon[i][0] * polygon[j][1]
        area -= polygon[j][0] * polygon[i][1]
    return abs(area) * 0.5


def _is_rect_like_dp(dp_data):
    """Return True when DP outline is close to axis-aligned rectangle.

    For these cases, using full bbox spans is more stable than scanline clipping.
    """
    polygon = dp_data.get('polygon') or []
    min_x, min_y, max_x, max_y = dp_data['bbox']
    bbox_area = max(0.0, (max_x - min_x) * (max_y - min_y))
    if bbox_area <= 1e-6:
        return False

    poly_area = _polygon_area(polygon)
    # 0.92+ means near-rectangular footprint (robust threshold for modeling noise).
    return (poly_area / bbox_area) >= 0.92


def _get_row_intervals(direction, fixed, min_x, min_y, max_x, max_y, polygon):
    """Get robust polygon-clipped intervals for one row.

    If the row lies exactly on a polygon edge, direct scanline can return empty.
    In that case, probe +/- small epsilon and keep the better result.
    """
    if direction == 'X':
        base = get_obstacle_intervals(fixed, min_x, max_x, polygon, 'X')
        if base:
            return base
        m = get_obstacle_intervals(fixed - ROW_EPS, min_x, max_x, polygon, 'X')
        p = get_obstacle_intervals(fixed + ROW_EPS, min_x, max_x, polygon, 'X')
    else:
        base = get_obstacle_intervals(fixed, min_y, max_y, polygon, 'Y')
        if base:
            return base
        m = get_obstacle_intervals(fixed - ROW_EPS, min_y, max_y, polygon, 'Y')
        p = get_obstacle_intervals(fixed + ROW_EPS, min_y, max_y, polygon, 'Y')

    return m if _sum_interval_lengths(m) >= _sum_interval_lengths(p) else p


def _plan_rows_for_direction(dp_data, direction, spacing, cover, rect_like):
    """Pick base or shifted rows based on usable-interval coverage."""
    min_x, min_y, max_x, max_y = dp_data['bbox']
    polygon = dp_data.get('polygon') or []

    base_rows = generate_dp_bar_rows(dp_data, spacing, cover, direction, offset=0.0)
    if rect_like:
        return base_rows, 'base'

    def _count_hits(rows):
        hits = 0
        for row in rows:
            intervals = _get_row_intervals(
                direction, row['pos'], min_x, min_y, max_x, max_y, polygon
            )
            if intervals:
                hits += 1
        return hits

    shifted_rows = generate_dp_bar_rows(
        dp_data, spacing, cover, direction, offset=spacing * 0.5
    )

    base_hits = _count_hits(base_rows)
    shifted_hits = _count_hits(shifted_rows)
    if shifted_hits > base_hits:
        return shifted_rows, 'shifted'
    return base_rows, 'base'


def _shaft_intervals_in_range(fixed_val, vary_min, vary_max, shaft_polygons, axis):
    """Return merged shaft intervals along [vary_min, vary_max] for the given scanline."""
    all_ivs = []
    for shaft_poly in shaft_polygons:
        all_ivs.extend(get_obstacle_intervals(fixed_val, vary_min, vary_max, shaft_poly, axis))
    if not all_ivs:
        return []
    all_ivs.sort(key=lambda t: t[0])
    merged = [list(all_ivs[0])]
    for a, b in all_ivs[1:]:
        if a <= merged[-1][1] + ROW_EPS:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def _subtract_shafts(dp_a, dp_b, shaft_ivs, tol=0.001):
    """Subtract shaft intervals from one DP bar span [dp_a, dp_b].

    Returns a list of 4-tuples (a, b, left_is_shaft, right_is_shaft):
      - left_is_shaft : True when this segment's start face borders a shaft
      - right_is_shaft: True when this segment's end   face borders a shaft

    A bar whose end borders a shaft should have NO vertical leg on that side
    (becomes a 90-degree / L-shape instead of a full U-shape staple).
    """
    if not shaft_ivs:
        return [(dp_a, dp_b, False, False)]

    result = []
    current = dp_a
    left_flag = False  # does the current segment start border a shaft?

    for s_a, s_b in sorted(shaft_ivs, key=lambda t: t[0]):
        # Skip shaft entirely outside dp span (no overlap at all)
        if s_a > dp_b + tol or s_b < dp_a - tol:
            continue
        s_a = max(s_a, dp_a)
        s_b = min(s_b, dp_b)

        if s_b <= current + tol:
            # Shaft already passed — advance past it, mark next left as shaft
            left_flag = True
            current = max(current, s_b)
            continue

        if s_a >= dp_b - tol:
            # Shaft begins at or beyond bar end — bar ends at shaft face
            if current < dp_b - tol:
                result.append((current, dp_b, left_flag, True))
            return result

        if s_a > current + tol:
            # Segment before this shaft; its right end faces the shaft
            result.append((current, s_a, left_flag, True))

        current = s_b
        left_flag = True  # next segment's left end faces the shaft

    if current < dp_b - tol:
        result.append((current, dp_b, left_flag, False))

    return result


def _get_final_bar_intervals(direction, fixed, dp_data, shaft_polygons, rect_like):
    """Compute final bar intervals for one scanline, subtracting shaft openings.

    Returns a list of 4-tuples (a, b, left_is_shaft, right_is_shaft).
    - Full U-shape staple : left_is_shaft=False, right_is_shaft=False
    - L-shape (right leg) : left_is_shaft=True,  right_is_shaft=False  (left end at shaft)
    - L-shape (left leg)  : left_is_shaft=False, right_is_shaft=True   (right end at shaft)
    - Straight bar        : both True (shaft on both ends — rare, falls back to straight)
    """
    min_x, min_y, max_x, max_y = dp_data['bbox']
    polygon = dp_data.get('polygon') or []

    # Step 1: polygon clipping for the DP shape
    if rect_like:
        dp_ivs = [(min_x, max_x)] if direction == 'X' else [(min_y, max_y)]
    else:
        dp_ivs = _get_row_intervals(direction, fixed, min_x, min_y, max_x, max_y, polygon)
    if not dp_ivs:
        return []

    # Step 2: shaft intervals across the full DP bbox span
    vary_min = min_x if direction == 'X' else min_y
    vary_max = max_x if direction == 'X' else max_y
    shaft_ivs = _shaft_intervals_in_range(fixed, vary_min, vary_max, shaft_polygons or [], direction)

    # Step 3: subtract shafts from each DP sub-interval
    result = []
    for dp_a, dp_b in dp_ivs:
        relevant = [(s_a, s_b) for s_a, s_b in shaft_ivs if s_b > dp_a and s_a < dp_b]
        result.extend(_subtract_shafts(dp_a, dp_b, relevant))
    return result


def _h_ext_outside_slab(slab_polygon, direction, row_pos, seg_a, seg_b, cover, h_leg):
    """Return (left_outside, right_outside): whether each h_leg tip falls outside the slab.

    The h_leg extends outward from the vertical leg:
      X-direction: left tip = seg_a + cover - h_leg,  right tip = seg_b - cover + h_leg
      Y-direction: same formula with seg_a/seg_b as Y coordinates

    If a tip falls outside every slab interval at row_pos, the corresponding extension
    should be suppressed (it would protrude into open air beyond the slab edge).
    Returns (False, False) when slab_polygon is None/empty or h_leg is negligible.
    """
    if not slab_polygon or h_leg <= 1e-6:
        return False, False

    xs = [p[0] for p in slab_polygon]
    ys = [p[1] for p in slab_polygon]

    if direction == 'X':
        slab_ivs = get_obstacle_intervals(row_pos, min(xs), max(xs), slab_polygon, 'X')
        tip_left  = seg_a + cover - h_leg
        tip_right = seg_b - cover + h_leg
    else:
        slab_ivs = get_obstacle_intervals(row_pos, min(ys), max(ys), slab_polygon, 'Y')
        tip_left  = seg_a + cover - h_leg
        tip_right = seg_b - cover + h_leg

    if not slab_ivs:
        return False, False

    tol = 1e-6
    left_outside  = not any(iv[0] - tol <= tip_left  <= iv[1] + tol for iv in slab_ivs)
    right_outside = not any(iv[0] - tol <= tip_right <= iv[1] + tol for iv in slab_ivs)
    return left_outside, right_outside


def _find_mark_param(element):
    """Return the first writable Mark parameter on element, or None."""
    # Approach 1: GetParameters — avoids IronPython overload-resolution issues
    try:
        result = element.GetParameters('Mark')
        for p in (list(result) if result is not None else []):
            if not p.IsReadOnly:
                return p
    except Exception:
        pass
    # Approach 2: LookupParameter
    try:
        p = element.LookupParameter('Mark')
        if p is not None and not p.IsReadOnly:
            return p
    except Exception:
        pass
    # Approach 3: BuiltInParameter
    try:
        from Autodesk.Revit.DB import BuiltInParameter
        p = element.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
        if p is not None and not p.IsReadOnly:
            return p
    except Exception:
        pass
    return None


def _set_mark_value(element, mark_text):
    """Best-effort setter for Mark value across Revit/IronPython quirks."""
    # Approach 1: GetParameters('Mark')
    try:
        result = element.GetParameters('Mark')
        for p in (list(result) if result is not None else []):
            if not p.IsReadOnly:
                p.Set(mark_text)
                return True
    except Exception:
        pass
    # Approach 2: LookupParameter
    try:
        p = element.LookupParameter('Mark')
        if p is not None and not p.IsReadOnly:
            p.Set(mark_text)
            return True
    except Exception:
        pass
    # Approach 3: BuiltInParameter
    try:
        from Autodesk.Revit.DB import BuiltInParameter
        p = element.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
        if p is not None and not p.IsReadOnly:
            p.Set(mark_text)
            return True
    except Exception:
        pass
    return False


def apply_dp_mark_queue(doc, mark_queue):
    """Apply (ElementId, mark_text) pairs in a dedicated transaction.

    Called AFTER the placement transaction commits so Revit's post-commit
    shape-registration regeneration cannot reset the marks.

    A second verification pass re-reads every element's mark after the first
    transaction commits.  Any element whose mark is still empty is written
    again in a retry transaction.  This guards against Revit's internal
    rebar-set layout regeneration silently clearing marks on set elements.
    """
    if not mark_queue or doc is None:
        return

    # --- First pass: set all marks ---
    t = Transaction(doc, 'Set DP Rebar Marks')
    try:
        status = t.Start()
    except Exception as e:
        print('[dp_rebar_placer] WARNING: mark transaction could not start: {}. Marks skipped.'.format(e))
        return
    if TransactionStatus is not None and status != TransactionStatus.Started:
        print('[dp_rebar_placer] WARNING: mark transaction did not start (status={}). Marks skipped.'.format(status))
        return
    _configure_fast_transaction(t)
    ok = failed = 0
    try:
        try:
            doc.Regenerate()
        except Exception:
            pass
        for eid, mark_text in mark_queue:
            try:
                elem = doc.GetElement(eid)
                if elem is None:
                    continue
                if _set_mark_value(elem, mark_text):
                    ok += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
        t.Commit()
    except Exception:
        t.RollBack()
        raise
    print('[dp_rebar] marks set (pass 1): ok={} failed={}'.format(ok, failed))

    # --- Verification pass: retry any element whose mark is still empty ---
    needs_retry = []
    for eid, mark_text in mark_queue:
        try:
            elem = doc.GetElement(eid)
            if elem is None:
                continue
            param = _find_mark_param(elem)
            if param is not None:
                val = param.AsString()
                if val is None or val.strip() == '':
                    needs_retry.append((eid, mark_text))
        except Exception:
            pass

    if not needs_retry:
        return

    print('[dp_rebar] {} marks empty after pass 1 — retrying...'.format(len(needs_retry)))
    t2 = Transaction(doc, 'Verify DP Rebar Marks')
    try:
        status2 = t2.Start()
    except Exception as e:
        print('[dp_rebar_placer] WARNING: retry mark transaction could not start: {}'.format(e))
        return
    if TransactionStatus is not None and status2 != TransactionStatus.Started:
        print('[dp_rebar_placer] WARNING: retry mark transaction did not start (status={})'.format(status2))
        return
    _configure_fast_transaction(t2)
    retry_ok = retry_failed = 0
    try:
        try:
            doc.Regenerate()
        except Exception:
            pass
        for eid, mark_text in needs_retry:
            try:
                elem = doc.GetElement(eid)
                if elem is None:
                    continue
                if _set_mark_value(elem, mark_text):
                    retry_ok += 1
                else:
                    retry_failed += 1
            except Exception:
                retry_failed += 1
        t2.Commit()
    except Exception:
        t2.RollBack()
        raise
    print('[dp_rebar] marks retry: ok={} failed={}'.format(retry_ok, retry_failed))

    # --- Final pass: one more verify+set to handle late shape-driven regen ---
    final_retry = []
    for eid, mark_text in mark_queue:
        try:
            elem = doc.GetElement(eid)
            if elem is None:
                continue
            param = _find_mark_param(elem)
            if param is not None:
                val = param.AsString()
                if val is None or val.strip() == '':
                    final_retry.append((eid, mark_text))
        except Exception:
            pass
    if not final_retry:
        return

    print('[dp_rebar] {} marks still empty after retry — final pass...'.format(len(final_retry)))
    t3 = Transaction(doc, 'Final DP Mark Fix')
    try:
        status3 = t3.Start()
    except Exception as e:
        print('[dp_rebar_placer] WARNING: final mark pass could not start: {}'.format(e))
        return
    if TransactionStatus is not None and status3 != TransactionStatus.Started:
        print('[dp_rebar_placer] WARNING: final mark pass did not start (status={})'.format(status3))
        return
    _configure_fast_transaction(t3)
    final_ok = final_failed = 0
    try:
        try:
            doc.Regenerate()
        except Exception:
            pass
        for eid, mark_text in final_retry:
            try:
                elem = doc.GetElement(eid)
                if elem is None:
                    continue
                if _set_mark_value(elem, mark_text):
                    final_ok += 1
                else:
                    final_failed += 1
            except Exception:
                final_failed += 1
        t3.Commit()
    except Exception:
        t3.RollBack()
        raise
    print('[dp_rebar] final mark pass: ok={} failed={}'.format(final_ok, final_failed))
    if final_failed > 0:
        print('[dp_rebar] WARNING: {} DP bars still have no mark after final pass'.format(final_failed))


def _create_and_validate(doc, create_fn):
    """Create rebar element; validation happens at transaction commit."""
    try:
        rb = create_fn()
    except Exception as e:
        print('[DP create] FAILED: {}'.format(e))
        return None, False

    if rb is None:
        print('[DP create] FAILED: CreateFromCurves returned None')
        return None, False

    return rb, False


def _place_staple(doc, dp_floor, direction, row_pos, a, b, z_bot, z_top, params, bar_type,
                  left_leg=True, right_leg=True,
                  left_h_ext=True, right_h_ext=True):
    """Place a staple (or staple rebar set) fully inside the DP footprint.

    left_leg / right_leg control which vertical legs are present:
      - Both True  → vertical legs on both sides (normal case)
      - left=False → right leg only (caller explicitly suppresses left leg)
      - right=False→ left leg only (caller explicitly suppresses right leg)
      - Both False → no staple possible; caller falls back to _place_straight

    left_h_ext / right_h_ext control the horizontal h_leg extension at the top:
      - True  → h_leg extension present on that side (normal case, no shaft)
      - False → h_leg extension suppressed (bar end borders a shaft opening;
                the vertical leg is still present for anchorage, but the
                horizontal extension would protrude into the shaft void)

    Shape table:
      No shaft        : U + h_leg both sides
      Shaft on left   : U + h_leg on right only  (left_h_ext=False)
      Shaft on right  : U + h_leg on left only   (right_h_ext=False)
      Shaft both sides: U shape, no h_leg on either side

    When count > 1 the bar is turned into a rebar set distributed along the
    perpendicular axis with set_spacing between bars.

    Shape (full U with horizontal extensions):
        ───┐               ┌───   z_top
           │               │
           └───────────────┘      z_bot
    """
    if not left_leg and not right_leg:
        return None, False   # degenerate — let caller use _place_straight

    side_cover = params['cover']
    h_leg = max(0.0, params.get('dp_horizontal_leg', 0.0))

    if direction == 'X':
        y = row_pos
        x_left  = a + side_cover
        x_right = b - side_cover
        if x_right - x_left <= 1e-6:
            return None, False

        curves = List[Curve]()
        # Left vertical leg (descends from z_top to z_bot)
        if left_leg:
            if h_leg > 1e-6 and left_h_ext:
                curves.Add(Line.CreateBound(XYZ(x_left - h_leg, y, z_top), XYZ(x_left, y, z_top)))
            curves.Add(Line.CreateBound(XYZ(x_left, y, z_top), XYZ(x_left, y, z_bot)))
        # Horizontal bottom leg
        curves.Add(Line.CreateBound(XYZ(x_left, y, z_bot), XYZ(x_right, y, z_bot)))
        # Right vertical leg (ascends from z_bot to z_top)
        if right_leg:
            curves.Add(Line.CreateBound(XYZ(x_right, y, z_bot), XYZ(x_right, y, z_top)))
            if h_leg > 1e-6 and right_h_ext:
                curves.Add(Line.CreateBound(XYZ(x_right, y, z_top), XYZ(x_right + h_leg, y, z_top)))
        normal = XYZ(0, 1, 0)
    else:
        x = row_pos
        y_bot = a + side_cover
        y_top = b - side_cover
        if y_top - y_bot <= 1e-6:
            return None, False

        curves = List[Curve]()
        # "Left" (bottom) vertical leg
        if left_leg:
            if h_leg > 1e-6 and left_h_ext:
                curves.Add(Line.CreateBound(XYZ(x, y_bot - h_leg, z_top), XYZ(x, y_bot, z_top)))
            curves.Add(Line.CreateBound(XYZ(x, y_bot, z_top), XYZ(x, y_bot, z_bot)))
        # Horizontal bottom leg
        curves.Add(Line.CreateBound(XYZ(x, y_bot, z_bot), XYZ(x, y_top, z_bot)))
        # "Right" (top) vertical leg
        if right_leg:
            curves.Add(Line.CreateBound(XYZ(x, y_top, z_bot), XYZ(x, y_top, z_top)))
            if h_leg > 1e-6 and right_h_ext:
                curves.Add(Line.CreateBound(XYZ(x, y_top, z_top), XYZ(x, y_top + h_leg, z_top)))
        normal = XYZ(1, 0, 0)

    _direction = direction
    print('[DP staple] dir={} z_bot={:.3f}ft ({:.0f}mm) z_top={:.3f}ft ({:.0f}mm) '
          'leg_h={:.3f}ft ({:.0f}mm) n_curves={}'.format(
        direction, z_bot, z_bot * 304.8, z_top, z_top * 304.8,
        z_top - z_bot, (z_top - z_bot) * 304.8, curves.Count))

    def _create():
        # False, True: don't match existing shape, always create a new one.
        # False, False is rejected by the Revit API ("both cannot be false").
        # Any Dmin/shape warnings are suppressed by _SilentFailuresPreprocessor.
        return Rebar.CreateFromCurves(
            doc,
            RebarStyle.Standard,
            bar_type,
            None,
            None,
            dp_floor,
            normal,
            curves,
            RebarHookOrientation.Left,
            RebarHookOrientation.Right,
            False,
            True,
        )

    return _create_and_validate(doc, _create)


def _place_straight(doc, dp_floor, direction, row_pos, a, b, z_bot, params, bar_type):
    """Fallback straight bar (or straight rebar set) inside one DP interval."""
    side_cover = params['cover']

    if direction == 'X':
        y = row_pos
        x_left = a + side_cover
        x_right = b - side_cover
        if x_right - x_left <= 1e-6:
            return None, False
        p1 = XYZ(x_left, y, z_bot)
        p2 = XYZ(x_right, y, z_bot)
        normal = XYZ(0, 1, 0)
    else:
        x = row_pos
        y_bot = a + side_cover
        y_top = b - side_cover
        if y_top - y_bot <= 1e-6:
            return None, False
        p1 = XYZ(x, y_bot, z_bot)
        p2 = XYZ(x, y_top, z_bot)
        normal = XYZ(1, 0, 0)

    curves = List[Curve]()
    curves.Add(Line.CreateBound(p1, p2))

    _direction = direction

    def _create():
        return Rebar.CreateFromCurves(
            doc,
            RebarStyle.Standard,
            bar_type,
            None,
            None,
            dp_floor,
            normal,
            curves,
            RebarHookOrientation.Left,
            RebarHookOrientation.Right,
            True,   # straight bar — match existing straight shape, no Dmin issue
            True,
        )

    return _create_and_validate(doc, _create)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _place_dp_direction(doc, dp_floor, dp_data, direction, bar_type, params, base_z,
                        shaft_polygons=None, slab_polygon=None, mark_queue=None):
    """Place all DP bars for one direction as rebar sets.

    Consecutive rows that share the same polygon interval pattern AND the same
    shaft-edge flags are grouped into a single rebar set.

    Shaft / slab-edge interaction:
      - Shaft inside DP → bar interval is split; each piece is its own group.
      - Shaft at bar end  → vertical leg kept (anchorage), h_leg extension suppressed.
      - Slab edge at bar end → same: vertical leg kept, h_leg suppressed because it
                               would protrude outside the slab.
      - No obstruction → both legs and both h_leg extensions present (full U-shape).
    """
    if mark_queue is None:
        mark_queue = []
    spacing = params['spacing']
    cover = params['cover']
    rect_like = _is_rect_like_dp(dp_data)
    rows, row_mode = _plan_rows_for_direction(dp_data, direction, spacing, cover, rect_like)
    stats = {
        'rows': len(rows),
        'rows_mode_shifted': 1 if row_mode == 'shifted' else 0,
        'bars_total': 0,
        'sets_placed': 0,
        'staple_ok': 0,
        'straight_primary': 0,
        'fallback_straight': 0,
        'too_short_skipped': 0,
        'regen_failed': 0,
        'failed': 0,
    }
    if not rows:
        return stats

    preferred_min_span = _preferred_min_span(params)
    hard_min_span = _hard_min_span(params)

    z_bot, z_top = _z_layer(dp_data, params, base_z)
    if z_bot is None:
        return stats

    # Collect per-row (pos, intervals) — intervals are 4-tuples including shaft flags.
    rows_and_ivs = []
    for row in rows:
        ivs = _get_final_bar_intervals(
            direction, row['pos'], dp_data, shaft_polygons or [], rect_like
        )
        if ivs:
            rows_and_ivs.append((row['pos'], ivs))

    if not rows_and_ivs:
        return stats

    # Group consecutive rows with identical interval+flag patterns → one set per group.
    # max_gap splits groups when shaft-eliminated rows leave a positional gap > 1.5 * spacing.
    groups = _group_rows_by_intervals(rows_and_ivs, max_gap=spacing * 1.5)

    for group_positions, group_ivs in groups:
        n_bars   = len(group_positions)
        first_pos = group_positions[0]

        for seg in group_ivs:
            seg_start, seg_end = seg[0], seg[1]
            left_is_shaft  = seg[2] if len(seg) > 2 else False
            right_is_shaft = seg[3] if len(seg) > 3 else False
            # Always keep the vertical leg (anchorage into DP concrete);
            # suppress h_leg extension on shaft-adjacent sides.
            left_h_ext  = not left_is_shaft
            right_h_ext = not right_is_shaft
            # Also suppress h_leg if the extension tip falls outside the slab boundary.
            h_leg = max(0.0, params.get('dp_horizontal_leg', 0.0))
            left_out, right_out = _h_ext_outside_slab(
                slab_polygon, direction, first_pos,
                seg_start, seg_end, cover, h_leg,
            )
            if left_out:
                left_h_ext = False
            if right_out:
                right_h_ext = False

            stats['bars_total'] += n_bars

            clear_span = (seg_end - seg_start) - 2.0 * cover
            if clear_span < hard_min_span:
                stats['too_short_skipped'] += n_bars
                continue
            force_straight = clear_span < preferred_min_span

            # ------------------------------------------------------------------
            # Place the base bar (single bar at first_pos), then expand to a
            # rebar set.  If set creation fails, place remaining rows individually
            # so that every position gets a bar rather than silently missing them.
            # ------------------------------------------------------------------
            rb = None
            regen_fail = False
            base_is_staple = False
            if not force_straight:
                rb, regen_fail = _place_staple(
                    doc, dp_floor, direction, first_pos, seg_start, seg_end,
                    z_bot, z_top, params, bar_type,
                    left_leg=True, right_leg=True,
                    left_h_ext=left_h_ext, right_h_ext=right_h_ext,
                )
                if regen_fail:
                    stats['regen_failed'] += 1
                base_is_staple = rb is not None
            if rb is None:
                rb, regen_fail = _place_straight(
                    doc, dp_floor, direction, first_pos, seg_start, seg_end,
                    z_bot, params, bar_type,
                )
                if regen_fail:
                    stats['regen_failed'] += 1

            if rb is None:
                stats['failed'] += n_bars
                continue

            mark_text = 'Drop Panel {}'.format(direction)
            mark_queue.append((rb.Id, mark_text))

            # Base bar placed — try to make a rebar set for the whole group.
            actual_placed = 1
            if n_bars > 1:
                set_ok = False
                try:
                    accessor = rb.GetShapeDrivenAccessor()
                    accessor.SetLayoutAsNumberWithSpacing(
                        n_bars, spacing, True, True, True
                    )
                    set_ok = True
                except Exception:
                    try:
                        array_len = spacing * (n_bars - 1)
                        accessor = rb.GetShapeDrivenAccessor()
                        accessor.SetLayoutAsMaximumSpacing(
                            spacing, array_len, True, True, True
                        )
                        set_ok = True
                    except Exception:
                        pass

                if set_ok:
                    actual_placed = n_bars
                else:
                    # Set failed — place remaining positions one by one.
                    for pos in group_positions[1:]:
                        rb2 = None
                        rf2 = False
                        if not force_straight:
                            rb2, rf2 = _place_staple(
                                doc, dp_floor, direction, pos, seg_start, seg_end,
                                z_bot, z_top, params, bar_type,
                                left_leg=True, right_leg=True,
                                left_h_ext=left_h_ext, right_h_ext=right_h_ext,
                            )
                        if rf2:
                            stats['regen_failed'] += 1
                        if rb2 is None:
                            rb2, rf2 = _place_straight(
                                doc, dp_floor, direction, pos,
                                seg_start, seg_end, z_bot, params, bar_type,
                            )
                            if rf2:
                                stats['regen_failed'] += 1
                        if rb2 is not None:
                            actual_placed += 1
                            mark_queue.append((rb2.Id, mark_text))
                        else:
                            stats['failed'] += 1

            if base_is_staple:
                stats['staple_ok'] += actual_placed
            else:
                stats['fallback_straight'] += actual_placed
            stats['sets_placed'] += 1

    print('[DP dir={}] staple_ok={} fallback_straight={} failed={} too_short={} sets={}'.format(
        direction,
        stats['staple_ok'], stats['fallback_straight'], stats['failed'],
        stats['too_short_skipped'], stats['sets_placed'],
    ))
    return stats


def place_all_dp_bars(doc, dp_data_list, params, shaft_polygons=None, slab_polygon=None):
    """Place all drop panel bars in a single Revit transaction."""
    bar_type = params['bar_type']
    cover = params['cover']
    diameter = params['diameter']

    stats = {
        'dp_count': len(dp_data_list),
        'x_rows': 0,
        'y_rows': 0,
        'x_rows_shifted': 0,
        'y_rows_shifted': 0,
        'x_total': 0,
        'y_total': 0,
        'x_sets': 0,
        'y_sets': 0,
        'x_staple': 0,
        'y_staple': 0,
        'x_straight': 0,
        'y_straight': 0,
        'x_straight_primary': 0,
        'y_straight_primary': 0,
        'x_too_short': 0,
        'y_too_short': 0,
        'x_regen_failed': 0,
        'y_regen_failed': 0,
        'x_failed': 0,
        'y_failed': 0,
        'staple_ok': 0,
        'straight_primary': 0,
        'fallback_straight': 0,
        'too_short_skipped': 0,
        'regen_failed': 0,
    }

    mark_queue = []   # collects (ElementId, mark_text) across all DPs

    t = Transaction(doc, 'Place Drop Panel Rebar')
    t.Start()
    _configure_fast_transaction(t)
    try:
        for dp_data in dp_data_list:
            dp_floor = dp_data.get('floor')
            if dp_floor is None:
                continue

            dp_bottom_z = dp_data.get('bottom_z')
            if dp_bottom_z is None:
                dp_top_z = dp_data.get('top_z', 0.0)
                dp_bottom_z = dp_top_z - max(dp_data.get('thickness', 0.0), 0.0)

            base_z = dp_bottom_z + cover
            base_z_y = base_z + (1.5 * diameter)

            x_stats = _place_dp_direction(
                doc, dp_floor, dp_data, 'X', bar_type, params, base_z,
                shaft_polygons=shaft_polygons, slab_polygon=slab_polygon,
                mark_queue=mark_queue,
            )
            y_stats = _place_dp_direction(
                doc, dp_floor, dp_data, 'Y', bar_type, params, base_z_y,
                shaft_polygons=shaft_polygons, slab_polygon=slab_polygon,
                mark_queue=mark_queue,
            )

            stats['x_rows'] += x_stats['rows']
            stats['y_rows'] += y_stats['rows']
            stats['x_rows_shifted'] += x_stats.get('rows_mode_shifted', 0)
            stats['y_rows_shifted'] += y_stats.get('rows_mode_shifted', 0)
            stats['x_total'] += x_stats['bars_total']
            stats['y_total'] += y_stats['bars_total']
            stats['x_sets'] += x_stats.get('sets_placed', 0)
            stats['y_sets'] += y_stats.get('sets_placed', 0)
            stats['x_staple'] += x_stats['staple_ok']
            stats['y_staple'] += y_stats['staple_ok']
            stats['x_straight'] += x_stats['fallback_straight']
            stats['y_straight'] += y_stats['fallback_straight']
            stats['x_straight_primary'] += x_stats['straight_primary']
            stats['y_straight_primary'] += y_stats['straight_primary']
            stats['x_too_short'] += x_stats['too_short_skipped']
            stats['y_too_short'] += y_stats['too_short_skipped']
            stats['x_regen_failed'] += x_stats['regen_failed']
            stats['y_regen_failed'] += y_stats['regen_failed']
            stats['x_failed'] += x_stats['failed']
            stats['y_failed'] += y_stats['failed']

        stats['staple_ok'] = stats['x_staple'] + stats['y_staple']
        stats['straight_primary'] = stats['x_straight_primary'] + stats['y_straight_primary']
        stats['fallback_straight'] = stats['x_straight'] + stats['y_straight']
        stats['too_short_skipped'] = stats['x_too_short'] + stats['y_too_short']
        stats['regen_failed'] = stats['x_regen_failed'] + stats['y_regen_failed']

        t.Commit()
    except Exception:
        t.RollBack()
        raise

    # Apply marks in a separate transaction AFTER all shapes are registered.
    print('[dp_rebar] applying {} marks...'.format(len(mark_queue)))
    apply_dp_mark_queue(doc, mark_queue)

    return stats
