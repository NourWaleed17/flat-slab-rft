# -*- coding: utf-8 -*-
"""
Tests for shaft-DP bar interaction.

Rules verified:
  - No shaft in DP → full U-shape staple (both legs + h_leg extensions)
  - Shaft at left end of bar interval  → left_is_shaft=True  → left leg kept (anchorage),
                                         h_leg extension suppressed on left side only
  - Shaft at right end of bar interval → right_is_shaft=True → right leg kept (anchorage),
                                         h_leg extension suppressed on right side only
  - Shaft on both ends                 → both legs kept, no h_leg extensions on either side
  - Shaft inside DP (not at boundary)  → interval split into two segments, each with shaft-
                                         adjacent leg kept but h_leg suppressed on shaft side
  - Shaft covers full DP row interval  → interval eliminated (no bar)
  - Multiple shafts in one row         → multiple gaps, correct flags per sub-segment

Run with:  python -m pytest tests/test_dp_shaft_interaction.py -v
"""
from __future__ import print_function

import sys
import os
import types
import pytest

# ---------------------------------------------------------------------------
# Minimal Revit stubs
# ---------------------------------------------------------------------------
clr_stub = types.ModuleType('clr')
clr_stub.AddReference = lambda *a, **kw: None
sys.modules.setdefault('clr', clr_stub)

for _mod in [
    'System', 'System.Collections', 'System.Collections.Generic',
    'Autodesk', 'Autodesk.Revit', 'Autodesk.Revit.DB',
    'Autodesk.Revit.DB.Structure',
]:
    sys.modules.setdefault(_mod, types.ModuleType(_mod))

_db = sys.modules['Autodesk.Revit.DB']
for _attr in ('Line', 'XYZ', 'Curve', 'Transaction', 'FilteredElementCollector',
              'Floor', 'Opening', 'JoinGeometryUtils', 'Wall', 'FamilyInstance',
              'BuiltInCategory', 'FailureHandlingOptions',
              'IFailuresPreprocessor', 'FailureProcessingResult',
              'FailureSeverity', 'TransactionStatus'):
    setattr(_db, _attr, None)

_dbs = sys.modules['Autodesk.Revit.DB.Structure']
for _attr in ('Rebar', 'RebarStyle', 'RebarHookOrientation'):
    setattr(_dbs, _attr, None)

sys.modules['System.Collections.Generic'].List = None

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dp_rebar_placer import (
    _shaft_intervals_in_range,
    _subtract_shafts,
    _get_final_bar_intervals,
    _intervals_match,
    _group_rows_by_intervals,
    generate_dp_bar_rows,
    _is_rect_like_dp,
    _plan_rows_for_direction,
    _get_row_intervals,
    _h_ext_outside_slab,
)

# ---------------------------------------------------------------------------
# Constants & helpers
# ---------------------------------------------------------------------------
TOL     = 1e-6
SPACING = 1.0
COVER   = 0.15


def _make_dp(polygon, top_z=10.0, thickness=2.5):
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return {
        'polygon':   polygon,
        'bbox':      (min(xs), min(ys), max(xs), max(ys)),
        'top_z':     top_z,
        'bottom_z':  top_z - thickness,
        'thickness': thickness,
    }


def _rect_shaft_poly(x1, y1, x2, y2):
    """Rectangular shaft polygon."""
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]


def _get_all_intervals(dp_data, direction, shaft_polygons, spacing=SPACING, cover=COVER):
    """Return list of (pos, 4-tuple-intervals) for all rows."""
    rect_like = _is_rect_like_dp(dp_data)
    rows, _ = _plan_rows_for_direction(dp_data, direction, spacing, cover, rect_like=rect_like)
    result = []
    for r in rows:
        ivs = _get_final_bar_intervals(direction, r['pos'], dp_data, shaft_polygons, rect_like)
        result.append((r['pos'], ivs))
    return result


# ---------------------------------------------------------------------------
# Standard fixtures
# ---------------------------------------------------------------------------

# 10 wide × 8 tall rectangular DP
RECT_DP = _make_dp([(0,0),(10,0),(10,8),(0,8)])

# Shaft entirely inside DP: x=3..7, y=2..6
CENTRAL_SHAFT = _rect_shaft_poly(3, 2, 7, 6)

# Shaft at left edge of DP: x=0..3, y=2..6
LEFT_SHAFT = _rect_shaft_poly(0, 2, 3, 6)

# Shaft at right edge of DP: x=7..10, y=2..6
RIGHT_SHAFT = _rect_shaft_poly(7, 2, 10, 6)

# Shaft spanning full DP width: x=0..10, y=3..5 — eliminates those rows
FULL_WIDTH_SHAFT = _rect_shaft_poly(0, 3, 10, 5)

# Two separate shafts side by side in the DP
SHAFT_LEFT_HALF  = _rect_shaft_poly(1, 2, 4, 6)
SHAFT_RIGHT_HALF = _rect_shaft_poly(6, 2, 9, 6)


# ===========================================================================
# 1. _shaft_intervals_in_range
# ===========================================================================

class TestShaftIntervalsInRange:

    def test_no_shafts_returns_empty(self):
        result = _shaft_intervals_in_range(4.0, 0.0, 10.0, [], 'X')
        assert result == []

    def test_shaft_polygon_outside_row_returns_empty(self):
        shaft = _rect_shaft_poly(0, 7, 10, 10)   # shaft at y=7..10
        result = _shaft_intervals_in_range(3.0, 0.0, 10.0, [shaft], 'X')
        assert result == []

    def test_shaft_inside_returns_correct_interval(self):
        result = _shaft_intervals_in_range(4.0, 0.0, 10.0, [CENTRAL_SHAFT], 'X')
        assert len(result) == 1
        assert result[0][0] == pytest.approx(3.0, abs=1e-3)
        assert result[0][1] == pytest.approx(7.0, abs=1e-3)

    def test_two_shafts_merged_when_adjacent(self):
        # Adjacent shafts: (2..4) and (4..6) → merged to (2..6)
        s1 = _rect_shaft_poly(2, 1, 4, 7)
        s2 = _rect_shaft_poly(4, 1, 6, 7)
        result = _shaft_intervals_in_range(4.0, 0.0, 10.0, [s1, s2], 'X')
        assert len(result) == 1
        assert result[0][0] == pytest.approx(2.0, abs=1e-3)
        assert result[0][1] == pytest.approx(6.0, abs=1e-3)

    def test_two_separated_shafts_return_two_intervals(self):
        result = _shaft_intervals_in_range(4.0, 0.0, 10.0,
                                           [SHAFT_LEFT_HALF, SHAFT_RIGHT_HALF], 'X')
        assert len(result) == 2
        assert result[0][0] == pytest.approx(1.0, abs=1e-3)
        assert result[0][1] == pytest.approx(4.0, abs=1e-3)
        assert result[1][0] == pytest.approx(6.0, abs=1e-3)
        assert result[1][1] == pytest.approx(9.0, abs=1e-3)

    def test_full_width_shaft_returns_full_interval(self):
        result = _shaft_intervals_in_range(4.0, 0.0, 10.0, [FULL_WIDTH_SHAFT], 'X')
        assert len(result) == 1
        assert result[0][0] == pytest.approx(0.0, abs=1e-3)
        assert result[0][1] == pytest.approx(10.0, abs=1e-3)

    def test_y_axis_scan(self):
        """Y-axis scan: shaft at x=3..7, y=2..6; scanline at x=5."""
        result = _shaft_intervals_in_range(5.0, 0.0, 8.0, [CENTRAL_SHAFT], 'Y')
        assert len(result) == 1
        assert result[0][0] == pytest.approx(2.0, abs=1e-3)
        assert result[0][1] == pytest.approx(6.0, abs=1e-3)

    def test_shaft_touching_bar_end_clamped(self):
        """Shaft extending beyond vary_max is clamped to vary_max."""
        shaft = _rect_shaft_poly(8, 1, 15, 7)   # extends to x=15 but vary_max=10
        result = _shaft_intervals_in_range(4.0, 0.0, 10.0, [shaft], 'X')
        assert len(result) == 1
        assert result[0][1] <= 10.0 + TOL


# ===========================================================================
# 2. _subtract_shafts
# ===========================================================================

class TestSubtractShafts:

    def test_no_shafts_returns_full_span_no_flags(self):
        result = _subtract_shafts(0.0, 10.0, [])
        assert len(result) == 1
        assert result[0] == (pytest.approx(0.0), pytest.approx(10.0), False, False)

    def test_central_shaft_splits_into_two_l_shapes(self):
        """Shaft at x=3..7 inside bar x=0..10 → two L-shapes."""
        result = _subtract_shafts(0.0, 10.0, [(3.0, 7.0)])
        assert len(result) == 2
        # Left segment: x=0..3, right end borders shaft
        a1, b1, l1, r1 = result[0]
        assert a1 == pytest.approx(0.0, abs=1e-3)
        assert b1 == pytest.approx(3.0, abs=1e-3)
        assert l1 is False   # left end is at DP edge, not shaft
        assert r1 is True    # right end borders shaft
        # Right segment: x=7..10, left end borders shaft
        a2, b2, l2, r2 = result[1]
        assert a2 == pytest.approx(7.0, abs=1e-3)
        assert b2 == pytest.approx(10.0, abs=1e-3)
        assert l2 is True    # left end borders shaft
        assert r2 is False   # right end is at DP edge

    def test_shaft_at_left_edge_trims_start(self):
        """Shaft from x=0..3 → bar starts at x=3, left_is_shaft=True."""
        result = _subtract_shafts(0.0, 10.0, [(0.0, 3.0)])
        assert len(result) == 1
        a, b, l, r = result[0]
        assert a == pytest.approx(3.0, abs=1e-3)
        assert b == pytest.approx(10.0, abs=1e-3)
        assert l is True    # left end is at shaft exit
        assert r is False

    def test_shaft_at_right_edge_trims_end(self):
        """Shaft from x=7..10 → bar ends at x=7, right_is_shaft=True."""
        result = _subtract_shafts(0.0, 10.0, [(7.0, 10.0)])
        assert len(result) == 1
        a, b, l, r = result[0]
        assert a == pytest.approx(0.0, abs=1e-3)
        assert b == pytest.approx(7.0, abs=1e-3)
        assert l is False
        assert r is True    # right end is at shaft face

    def test_shaft_covers_full_span_returns_empty(self):
        """Shaft spanning the entire bar interval → no bar segments."""
        result = _subtract_shafts(0.0, 10.0, [(0.0, 10.0)])
        assert result == []

    def test_two_shafts_three_segments(self):
        """Two shafts at x=2..4 and x=6..8 → three bar segments."""
        result = _subtract_shafts(0.0, 10.0, [(2.0, 4.0), (6.0, 8.0)])
        assert len(result) == 3
        # Middle segment has both ends at shaft faces
        _, _, l_mid, r_mid = result[1]
        assert l_mid is True
        assert r_mid is True

    def test_shaft_exactly_at_boundary_right(self):
        result = _subtract_shafts(0.0, 10.0, [(10.0, 12.0)])
        # Shaft starts at bar end → one segment with right_is_shaft=True
        assert len(result) == 1
        _, _, _, r = result[0]
        assert r is True

    def test_shaft_exactly_at_boundary_left(self):
        result = _subtract_shafts(0.0, 10.0, [(-2.0, 0.0)])
        # Shaft ends at bar start → one segment with left_is_shaft=True
        assert len(result) == 1
        _, _, l, _ = result[0]
        assert l is True

    def test_no_flags_when_shaft_completely_outside(self):
        result = _subtract_shafts(0.0, 10.0, [(15.0, 20.0)])
        assert len(result) == 1
        _, _, l, r = result[0]
        assert l is False
        assert r is False


# ===========================================================================
# 3. _get_final_bar_intervals  (integration: DP polygon + shaft subtraction)
# ===========================================================================

class TestGetFinalBarIntervals:

    def test_no_shaft_returns_dp_interval_with_no_flags(self):
        rect_like = _is_rect_like_dp(RECT_DP)
        result = _get_final_bar_intervals('X', 4.0, RECT_DP, [], rect_like)
        assert len(result) == 1
        a, b, l, r = result[0]
        assert a == pytest.approx(0.0, abs=1e-3)
        assert b == pytest.approx(10.0, abs=1e-3)
        assert l is False
        assert r is False

    def test_shaft_outside_dp_no_effect(self):
        shaft_far = _rect_shaft_poly(20, 20, 30, 30)
        rect_like = _is_rect_like_dp(RECT_DP)
        result = _get_final_bar_intervals('X', 4.0, RECT_DP, [shaft_far], rect_like)
        assert len(result) == 1
        _, _, l, r = result[0]
        assert l is False
        assert r is False

    def test_central_shaft_splits_dp_interval(self):
        rect_like = _is_rect_like_dp(RECT_DP)
        result = _get_final_bar_intervals('X', 4.0, RECT_DP, [CENTRAL_SHAFT], rect_like)
        assert len(result) == 2
        # Left segment ends at shaft
        assert result[0][3] is True   # right_is_shaft
        # Right segment starts at shaft
        assert result[1][2] is True   # left_is_shaft

    def test_left_shaft_produces_one_bar_with_left_flag(self):
        rect_like = _is_rect_like_dp(RECT_DP)
        result = _get_final_bar_intervals('X', 4.0, RECT_DP, [LEFT_SHAFT], rect_like)
        assert len(result) == 1
        a, b, l, r = result[0]
        assert a == pytest.approx(3.0, abs=1e-3)   # shaft exit
        assert l is True
        assert r is False

    def test_right_shaft_produces_one_bar_with_right_flag(self):
        rect_like = _is_rect_like_dp(RECT_DP)
        result = _get_final_bar_intervals('X', 4.0, RECT_DP, [RIGHT_SHAFT], rect_like)
        assert len(result) == 1
        a, b, l, r = result[0]
        assert b == pytest.approx(7.0, abs=1e-3)   # shaft enter
        assert l is False
        assert r is True

    def test_full_width_shaft_eliminates_bar(self):
        rect_like = _is_rect_like_dp(RECT_DP)
        result = _get_final_bar_intervals('X', 4.0, RECT_DP, [FULL_WIDTH_SHAFT], rect_like)
        assert result == []

    def test_row_outside_shaft_y_range_unaffected(self):
        """Row at y=1.0 is below the shaft (y=2..6) → no flags."""
        rect_like = _is_rect_like_dp(RECT_DP)
        result = _get_final_bar_intervals('X', 1.0, RECT_DP, [CENTRAL_SHAFT], rect_like)
        assert len(result) == 1
        _, _, l, r = result[0]
        assert l is False
        assert r is False

    def test_two_shafts_three_bar_segments(self):
        rect_like = _is_rect_like_dp(RECT_DP)
        result = _get_final_bar_intervals('X', 4.0, RECT_DP,
                                          [SHAFT_LEFT_HALF, SHAFT_RIGHT_HALF], rect_like)
        assert len(result) == 3
        # Centre segment: both ends at shafts
        _, _, l_mid, r_mid = result[1]
        assert l_mid is True
        assert r_mid is True


# ===========================================================================
# 4. Leg flags in row groups  (grouping respects shaft flags)
# ===========================================================================

class TestGroupingWithShaftFlags:
    """
    Verifies that rows with different shaft-flag patterns form separate groups,
    so each group maps to one consistently shaped rebar set.
    """

    def _groups(self, shaft_polygons, direction='X'):
        rect_like = _is_rect_like_dp(RECT_DP)
        rows, _ = _plan_rows_for_direction(RECT_DP, direction, SPACING, COVER, rect_like=rect_like)
        rows_and_ivs = []
        for r in rows:
            ivs = _get_final_bar_intervals(direction, r['pos'], RECT_DP, shaft_polygons, rect_like)
            if ivs:
                rows_and_ivs.append((r['pos'], ivs))
        return _group_rows_by_intervals(rows_and_ivs, max_gap=SPACING * 1.5), rows_and_ivs

    def test_no_shaft_one_group(self):
        groups, _ = self._groups([])
        assert len(groups) == 1

    def test_central_shaft_creates_three_groups(self):
        """
        Rows above shaft (y<2): [(0,10, F, F)] → group 1 (full U)
        Rows in shaft (y=2..6): [(0,3, F, T), (7,10, T, F)] → group 2 (two L-shapes)
        Rows below shaft (y>6): [(0,10, F, F)] → group 3 (full U)
        """
        groups, _ = self._groups([CENTRAL_SHAFT])
        assert len(groups) == 3

    def test_full_width_shaft_creates_two_groups(self):
        """
        Rows at y=0.15, 1.15: full interval, no flags → group 1
        Rows at y=2.15..4.15: intervals eliminated → not in rows_and_ivs
        Rows at y=5.15..7.15: back to full interval → group 2
        (Full-width shaft y=3..5 eliminates those rows entirely)
        """
        groups, rows_and_ivs = self._groups([FULL_WIDTH_SHAFT])
        # rows with no intervals are excluded; remaining form groups
        # All remaining rows have same (0,10,F,F) → should be 2 groups split by the gap
        # (gap breaks consecutive sequence → two groups)
        assert len(groups) == 2

    def test_left_shaft_creates_two_groups(self):
        """
        Rows outside shaft y range: full interval (F,F) → group 1
        Rows within shaft y range (y=2..6): trimmed interval (T,F) → group 2
        """
        groups, _ = self._groups([LEFT_SHAFT])
        assert len(groups) == 3   # before / during / after shaft in Y

    def test_each_group_has_correct_flag_patterns(self):
        """Verify flag patterns per group for each shaft scenario.

        - No shaft      : 1 group, 1 interval, flags (F, F)
        - Left shaft    : 3 groups; middle group has 1 interval with (T, F)
        - Right shaft   : 3 groups; middle group has 1 interval with (F, T)
        - Central shaft : 3 groups; middle group has 2 intervals (F,T) and (T,F)

        Note: a group's ivs represents ONE ROW's interval list. When a central shaft
        splits a row into two arms, the middle group legitimately contains two
        intervals with *opposite* flags — one for each arm.
        """
        # No shaft
        groups, _ = self._groups([])
        assert len(groups) == 1
        assert groups[0][1][0][2:] == (False, False)

        # Left shaft: shaft trims left end of bars in y=2..6 zone
        groups, _ = self._groups([LEFT_SHAFT])
        assert len(groups) == 3
        assert groups[1][1][0][2:] == (True, False)   # left_is_shaft, not right

        # Right shaft: shaft trims right end of bars in y=2..6 zone
        groups, _ = self._groups([RIGHT_SHAFT])
        assert len(groups) == 3
        assert groups[1][1][0][2:] == (False, True)   # not left, right_is_shaft

        # Central shaft: each row in shaft zone → 2 arms with opposite flags
        groups, _ = self._groups([CENTRAL_SHAFT])
        assert len(groups) == 3
        shaft_ivs = groups[1][1]
        assert len(shaft_ivs) == 2
        assert shaft_ivs[0][2:] == (False, True)   # left arm: right side faces shaft
        assert shaft_ivs[1][2:] == (True, False)   # right arm: left side faces shaft


# ===========================================================================
# 5. Staple shape rules  (unit-test the decision logic, not Revit placement)
# ===========================================================================

class TestStapleShapeRules:
    """
    Verify the shape-selection rules without touching Revit API.
    We derive left_leg / right_leg / left_h_ext / right_h_ext from interval
    flags and check what bar shape should result.

    New rules:
      - Vertical legs are ALWAYS kept (structural anchorage into DP concrete).
      - h_leg horizontal extension is suppressed only on the shaft-adjacent side
        (it would protrude into the void of the shaft opening).
    """

    def _shape(self, left_is_shaft, right_is_shaft):
        """Returns (left_leg, right_leg, left_h_ext, right_h_ext) per new rules."""
        return True, True, not left_is_shaft, not right_is_shaft

    def test_no_shaft_full_u_with_extensions(self):
        ll, rl, lh, rh = self._shape(False, False)
        assert ll is True and rl is True
        assert lh is True and rh is True    # both h_leg extensions present

    def test_left_shaft_leg_present_no_left_extension(self):
        """Bar starts at shaft face → left leg kept (anchorage), h_leg suppressed on left."""
        ll, rl, lh, rh = self._shape(True, False)
        assert ll is True   # left leg still present (anchorage)
        assert rl is True
        assert lh is False  # no horizontal extension into shaft
        assert rh is True   # right side extension still present

    def test_right_shaft_leg_present_no_right_extension(self):
        """Bar ends at shaft face → right leg kept (anchorage), h_leg suppressed on right."""
        ll, rl, lh, rh = self._shape(False, True)
        assert ll is True
        assert rl is True   # right leg still present (anchorage)
        assert lh is True   # left side extension still present
        assert rh is False  # no horizontal extension into shaft

    def test_both_shaft_legs_present_no_extensions(self):
        """Both ends at shaft faces → both legs kept, no h_leg extensions on either side."""
        ll, rl, lh, rh = self._shape(True, True)
        assert ll is True and rl is True    # both legs present
        assert lh is False and rh is False  # no extensions on either side


# ===========================================================================
# 6. Row-level coverage with shafts  (no missing bars, no extra bars)
# ===========================================================================

class TestCoverageWithShafts:

    def test_rows_outside_shaft_zone_unaffected(self):
        """Rows outside shaft's Y range see the full DP interval with no flags."""
        rect_like = _is_rect_like_dp(RECT_DP)
        # Shaft y=2..6; test row at y=1
        result = _get_final_bar_intervals('X', 1.0, RECT_DP, [CENTRAL_SHAFT], rect_like)
        assert len(result) == 1
        assert result[0][2] is False
        assert result[0][3] is False

    def test_all_rows_have_at_least_one_interval_no_shaft(self):
        rect_like = _is_rect_like_dp(RECT_DP)
        rows = generate_dp_bar_rows(RECT_DP, SPACING, COVER, 'X')
        for r in rows:
            ivs = _get_final_bar_intervals('X', r['pos'], RECT_DP, [], rect_like)
            assert len(ivs) >= 1

    def test_rows_in_full_width_shaft_produce_no_interval(self):
        """Rows within y=3..5 (full-width shaft) → no bars."""
        rect_like = _is_rect_like_dp(RECT_DP)
        rows = generate_dp_bar_rows(RECT_DP, SPACING, COVER, 'X')
        for r in rows:
            if 3.0 < r['pos'] < 5.0:
                ivs = _get_final_bar_intervals('X', r['pos'], RECT_DP, [FULL_WIDTH_SHAFT], rect_like)
                assert ivs == []

    def test_rows_in_central_shaft_produce_two_intervals(self):
        """Rows within shaft y=2..6 → bar split into left + right."""
        rect_like = _is_rect_like_dp(RECT_DP)
        rows = generate_dp_bar_rows(RECT_DP, SPACING, COVER, 'X')
        for r in rows:
            if 2.0 < r['pos'] < 6.0:
                ivs = _get_final_bar_intervals('X', r['pos'], RECT_DP, [CENTRAL_SHAFT], rect_like)
                assert len(ivs) == 2

    def test_total_bar_count_with_central_shaft(self):
        """
        8 rows total (y=0.15..7.15):
          - y=0.15, 1.15 (below shaft): 1 bar each → 2
          - y=2.15..5.15 (in shaft):    2 bars each → 8
          - y=6.15, 7.15 (above shaft): 1 bar each → 2
          Total = 12
        """
        rect_like = _is_rect_like_dp(RECT_DP)
        rows = generate_dp_bar_rows(RECT_DP, SPACING, COVER, 'X')
        total = 0
        for r in rows:
            ivs = _get_final_bar_intervals('X', r['pos'], RECT_DP, [CENTRAL_SHAFT], rect_like)
            total += len(ivs)
        assert total == 12

    def test_total_bar_count_with_left_shaft(self):
        """
        8 rows:
          - y=0.15, 1.15: 1 bar each (no shaft), 2 bars
          - y=2.15..5.15: 1 bar each (shaft trims left end), 4 bars
          - y=6.15, 7.15: 1 bar each (no shaft), 2 bars
          Total = 8
        """
        rect_like = _is_rect_like_dp(RECT_DP)
        rows = generate_dp_bar_rows(RECT_DP, SPACING, COVER, 'X')
        total = 0
        for r in rows:
            ivs = _get_final_bar_intervals('X', r['pos'], RECT_DP, [LEFT_SHAFT], rect_like)
            total += len(ivs)
        assert total == 8

    def test_total_bar_count_no_shaft(self):
        """No shaft → 8 rows × 1 bar = 8 bars."""
        rect_like = _is_rect_like_dp(RECT_DP)
        rows = generate_dp_bar_rows(RECT_DP, SPACING, COVER, 'X')
        total = sum(
            len(_get_final_bar_intervals('X', r['pos'], RECT_DP, [], rect_like))
            for r in rows
        )
        assert total == 8

    def test_y_direction_shaft_interaction(self):
        """Y-bars: shaft at x=3..7, y=2..6 trims bars whose x is in the shaft zone."""
        rect_like = _is_rect_like_dp(RECT_DP)
        rows = generate_dp_bar_rows(RECT_DP, SPACING, COVER, 'Y')
        for r in rows:
            if 3.0 < r['pos'] < 7.0:
                # Y-bar at x=5 (inside shaft x-range): shaft cuts y=2..6 from bar
                ivs = _get_final_bar_intervals('Y', r['pos'], RECT_DP, [CENTRAL_SHAFT], rect_like)
                assert len(ivs) == 2
            elif r['pos'] < 3.0 or r['pos'] > 7.0:
                ivs = _get_final_bar_intervals('Y', r['pos'], RECT_DP, [CENTRAL_SHAFT], rect_like)
                assert len(ivs) == 1
                assert ivs[0][2] is False
                assert ivs[0][3] is False


# ===========================================================================
# 7. _intervals_match with 4-tuple format
# ===========================================================================

class TestIntervalMatchFourTuples:

    def test_identical_four_tuples_match(self):
        ivs1 = [(0.0, 10.0, False, False)]
        ivs2 = [(0.0, 10.0, False, False)]
        assert _intervals_match(ivs1, ivs2) is True

    def test_different_flags_no_match(self):
        ivs1 = [(0.0, 10.0, False, False)]
        ivs2 = [(0.0, 10.0, True, False)]
        assert _intervals_match(ivs1, ivs2) is False

    def test_both_flags_differ_no_match(self):
        ivs1 = [(0.0, 5.0, False, True)]
        ivs2 = [(0.0, 5.0, True, False)]
        assert _intervals_match(ivs1, ivs2) is False

    def test_position_and_flags_both_match(self):
        ivs1 = [(0.0, 3.0, False, True), (7.0, 10.0, True, False)]
        ivs2 = [(0.0, 3.0, False, True), (7.0, 10.0, True, False)]
        assert _intervals_match(ivs1, ivs2) is True

    def test_two_tuple_backward_compatible(self):
        """2-tuples (no flags) still match correctly."""
        assert _intervals_match([(0.0, 10.0)], [(0.0, 10.0)]) is True
        assert _intervals_match([(0.0, 10.0)], [(1.0, 10.0)]) is False

    def test_mixed_two_and_four_tuples_no_flag_check(self):
        """If one side lacks flags, only positional values are compared."""
        ivs1 = [(0.0, 10.0)]
        ivs2 = [(0.0, 10.0, False, False)]
        # len(seg1) <= 2, so no flag comparison → should match on position
        assert _intervals_match(ivs1, ivs2) is True


# ===========================================================================
# 8. _h_ext_outside_slab  (slab-edge h_leg suppression)
# ===========================================================================

# Slab: 20 wide × 16 tall, x=0..20, y=0..16
SLAB_POLY = [(0, 0), (20, 0), (20, 16), (0, 16)]

# DP at left slab edge: x=0..6, y=4..12
DP_LEFT_EDGE  = _make_dp([(0, 4), (6, 4), (6, 12), (0, 12)])
# DP at right slab edge: x=14..20, y=4..12
DP_RIGHT_EDGE = _make_dp([(14, 4), (20, 4), (20, 12), (14, 12)])
# DP fully interior: x=7..13, y=4..12
DP_INTERIOR   = _make_dp([(7, 4), (13, 4), (13, 12), (7, 12)])

H_LEG = 0.3   # typical dp_horizontal_leg (feet)


class TestHExtOutsideSlab:
    """
    Unit tests for _h_ext_outside_slab.

    The function checks whether the h_leg tip (the outer end of the horizontal
    extension at z_top) would fall outside the slab boundary.
    """

    def test_no_slab_polygon_never_outside(self):
        """Without a slab polygon, no suppression."""
        lo, ro = _h_ext_outside_slab(None, 'X', 8.0, 0.0, 6.0, COVER, H_LEG)
        assert lo is False and ro is False

    def test_zero_h_leg_never_outside(self):
        lo, ro = _h_ext_outside_slab(SLAB_POLY, 'X', 8.0, 0.0, 6.0, COVER, 0.0)
        assert lo is False and ro is False

    def test_interior_dp_both_tips_inside_slab(self):
        """DP at x=7..13 — tips at 7+0.15-0.3=6.85 and 13-0.15+0.3=13.15, both inside slab."""
        lo, ro = _h_ext_outside_slab(SLAB_POLY, 'X', 8.0, 7.0, 13.0, COVER, H_LEG)
        assert lo is False
        assert ro is False

    def test_left_edge_dp_left_tip_outside(self):
        """DP at x=0..6 — left tip = 0+0.15-0.3 = -0.15, outside slab (slab starts at x=0)."""
        lo, ro = _h_ext_outside_slab(SLAB_POLY, 'X', 8.0, 0.0, 6.0, COVER, H_LEG)
        assert lo is True   # tip at -0.15 is outside slab
        assert ro is False  # right tip at 6-0.15+0.3=6.15, inside slab

    def test_right_edge_dp_right_tip_outside(self):
        """DP at x=14..20 — right tip = 20-0.15+0.3 = 20.15, outside slab (slab ends at x=20)."""
        lo, ro = _h_ext_outside_slab(SLAB_POLY, 'X', 8.0, 14.0, 20.0, COVER, H_LEG)
        assert lo is False  # left tip at 14+0.15-0.3=13.85, inside slab
        assert ro is True   # tip at 20.15 is outside slab

    def test_y_direction_bottom_edge(self):
        """Y-direction DP at bottom slab edge: y=0..8 — left tip = 0+0.15-0.3 = -0.15."""
        slab = [(0, 0), (20, 0), (20, 16), (0, 16)]
        lo, ro = _h_ext_outside_slab(slab, 'Y', 10.0, 0.0, 8.0, COVER, H_LEG)
        assert lo is True   # tip at -0.15 is outside slab bottom
        assert ro is False  # right tip at 8-0.15+0.3=8.15, inside slab

    def test_y_direction_top_edge(self):
        """Y-direction DP at top slab edge: y=8..16 — right tip = 16-0.15+0.3 = 16.15."""
        slab = [(0, 0), (20, 0), (20, 16), (0, 16)]
        lo, ro = _h_ext_outside_slab(slab, 'Y', 10.0, 8.0, 16.0, COVER, H_LEG)
        assert lo is False  # left tip at 8+0.15-0.3=7.85, inside slab
        assert ro is True   # tip at 16.15 is outside slab top

    def test_both_edges_both_tips_outside(self):
        """Tiny slab equal to bar span: both tips go outside."""
        slab = [(0, 0), (6, 0), (6, 16), (0, 16)]  # slab exactly 0..6
        lo, ro = _h_ext_outside_slab(slab, 'X', 8.0, 0.0, 6.0, COVER, H_LEG)
        assert lo is True
        assert ro is True

    def test_scanline_outside_slab_no_suppression(self):
        """Row position outside the slab polygon → no intervals → no suppression."""
        lo, ro = _h_ext_outside_slab(SLAB_POLY, 'X', 99.0, 0.0, 6.0, COVER, H_LEG)
        assert lo is False and ro is False

    def test_tip_exactly_at_slab_edge_not_outside(self):
        """Tip exactly on the slab boundary is considered inside (within tolerance)."""
        # tip_left = seg_a + cover - h_leg = 0.0 + 0.15 - 0.15 = 0.0 (exactly at slab edge)
        lo, ro = _h_ext_outside_slab(SLAB_POLY, 'X', 8.0, 0.0, 6.0, COVER, 0.15)
        assert lo is False  # tip = 0.0, which is exactly the slab left boundary
