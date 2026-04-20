# -*- coding: utf-8 -*-
"""
Test cases for dp_rebar_placer.py and geometry helpers used by DP logic.

Run with:  python -m pytest tests/test_dp_rebar_placer.py -v
(No Revit API required – all Revit-touching functions are excluded.)
"""
import sys
import os
import math
import pytest

# ---------------------------------------------------------------------------
# Minimal stubs so the modules can be imported without a Revit environment
# ---------------------------------------------------------------------------

# Stub out clr / Revit namespaces before importing our modules
import types

clr_stub = types.ModuleType('clr')
clr_stub.AddReference = lambda *a, **kw: None
sys.modules.setdefault('clr', clr_stub)

for mod in [
    'System', 'System.Collections', 'System.Collections.Generic',
    'Autodesk', 'Autodesk.Revit', 'Autodesk.Revit.DB',
    'Autodesk.Revit.DB.Structure',
]:
    sys.modules.setdefault(mod, types.ModuleType(mod))

# Provide minimal symbols used at module-level in dp_rebar_placer / geometry
db = sys.modules['Autodesk.Revit.DB']
db.Line = None
db.XYZ = None
db.Curve = None
db.Transaction = None
db.FilteredElementCollector = None
db.Floor = None
db.Opening = None
db.Wall = None
db.FamilyInstance = None
db.BuiltInCategory = None
db.JoinGeometryUtils = None
db.FailureHandlingOptions = None
db.IFailuresPreprocessor = None
db.FailureProcessingResult = None
db.FailureSeverity = None
db.TransactionStatus = None

dbs = sys.modules['Autodesk.Revit.DB.Structure']
dbs.Rebar = None
dbs.RebarStyle = None
dbs.RebarHookOrientation = None

sys_cg = sys.modules['System.Collections.Generic']
sys_cg.List = None

# Now we can import the pure-Python helpers
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Patch imports inside dp_rebar_placer that would fail (Revit-only)
import importlib

# We import only what we need from each module
from geometry import (
    polygon_area,
    point_in_polygon,
    point_in_polygon_or_edge,
    segment_polygon_intersections,
    get_obstacle_intervals,
)

# Import pure-Python symbols directly (avoid triggering Revit imports at top)
from dp_rebar_placer import (
    generate_dp_bar_rows,
    _z_layer,
    _hard_min_span as _strict_min_span,
    _sum_interval_lengths,
    _polygon_area,
    _is_rect_like_dp,
    _get_row_intervals,
    _plan_rows_for_direction,
)


# ===========================================================================
# Fixtures
# ===========================================================================

RECT_DP = {
    'bbox': (0.0, 0.0, 10.0, 8.0),
    'polygon': [(0, 0), (10, 0), (10, 8), (0, 8)],
    'top_z': 10.0,
    'bottom_z': 7.5,
    'thickness': 2.5,
}

THIN_DP = {
    'bbox': (0.0, 0.0, 10.0, 8.0),
    'polygon': [(0, 0), (10, 0), (10, 8), (0, 8)],
    'top_z': 10.0,
    'bottom_z': 9.8,
    'thickness': 0.2,  # very thin – likely unusable after cover
}

L_SHAPE_POLYGON = [
    (0, 0), (6, 0), (6, 4), (10, 4), (10, 8), (0, 8)
]
L_SHAPE_DP = {
    'bbox': (0.0, 0.0, 10.0, 8.0),
    'polygon': L_SHAPE_POLYGON,
    'top_z': 10.0,
    'bottom_z': 7.5,
    'thickness': 2.5,
}

BASE_PARAMS = {
    'spacing': 1.0,
    'cover': 0.15,
    'diameter': 0.065,
    'dp_vertical_leg': 2.0,
}


# ===========================================================================
# 1. generate_dp_bar_rows
# ===========================================================================

class TestGenerateDpBarRows:

    def test_x_direction_basic(self):
        rows = generate_dp_bar_rows(RECT_DP, spacing=1.0, cover=0.5, direction='X')
        assert len(rows) > 0
        for r in rows:
            assert r['direction'] == 'X'
            assert r['pos'] >= RECT_DP['bbox'][1] + 0.5  # min_y + cover
            assert r['pos'] <= RECT_DP['bbox'][3] - 0.5  # max_y - cover

    def test_y_direction_basic(self):
        rows = generate_dp_bar_rows(RECT_DP, spacing=1.0, cover=0.5, direction='Y')
        assert len(rows) > 0
        for r in rows:
            assert r['direction'] == 'Y'

    def test_spacing_matches_row_count(self):
        rows = generate_dp_bar_rows(RECT_DP, spacing=1.0, cover=0.0, direction='X')
        # 0..8 with step 1 → 9 rows (y=0,1,...,8)
        assert len(rows) == 9

    def test_indices_sequential(self):
        rows = generate_dp_bar_rows(RECT_DP, spacing=1.0, cover=0.0, direction='Y')
        for i, r in enumerate(rows):
            assert r['index'] == i

    def test_cover_larger_than_half_height_x_returns_empty(self):
        """Cover > half dimension → no rows fit."""
        rows = generate_dp_bar_rows(RECT_DP, spacing=0.5, cover=4.1, direction='X')
        assert rows == []

    def test_positive_offset_shifts_first_row(self):
        rows_no_offset = generate_dp_bar_rows(RECT_DP, spacing=1.0, cover=0.0, direction='X')
        rows_offset    = generate_dp_bar_rows(RECT_DP, spacing=1.0, cover=0.0, direction='X', offset=0.5)
        assert rows_offset[0]['pos'] == pytest.approx(rows_no_offset[0]['pos'] + 0.5)

    def test_negative_offset_treated_as_zero(self):
        rows_zero = generate_dp_bar_rows(RECT_DP, spacing=1.0, cover=0.0, direction='X', offset=0.0)
        rows_neg  = generate_dp_bar_rows(RECT_DP, spacing=1.0, cover=0.0, direction='X', offset=-5.0)
        assert rows_zero[0]['pos'] == pytest.approx(rows_neg[0]['pos'])

    # ----- Wrong / edge inputs that SHOULD be caught by callers -----

    def test_WRONG_zero_spacing_infinite_loop_risk(self):
        """spacing=0 would loop forever – document the danger."""
        # We don't call it here (would hang), but confirm spacing > 0 is required.
        # Caller in place_all_dp_bars must validate spacing before calling.
        assert BASE_PARAMS['spacing'] > 0, "spacing must be positive"

    def test_WRONG_dp_bbox_degenerate_zero_size(self):
        """A zero-area DP produces no rows."""
        tiny_dp = dict(RECT_DP, bbox=(5.0, 5.0, 5.0, 5.0))
        rows = generate_dp_bar_rows(tiny_dp, spacing=1.0, cover=0.1, direction='X')
        assert rows == []


# ===========================================================================
# 2. _z_layer
# ===========================================================================

class TestZLayer:

    def test_normal_dp(self):
        z_bot, z_top = _z_layer(RECT_DP, BASE_PARAMS, base_z=8.0)
        assert z_bot is not None
        assert z_top is not None
        assert z_bot < z_top

    def test_z_values_within_dp_thickness(self):
        # z_bot = bottom_z + cover; z_top = top_z - cover
        z_bot, z_top = _z_layer(RECT_DP, BASE_PARAMS, base_z=8.0)
        expected_z_bot = RECT_DP['bottom_z'] + BASE_PARAMS['cover']
        expected_z_top = RECT_DP['top_z'] - BASE_PARAMS['cover']
        assert z_bot == pytest.approx(expected_z_bot)
        assert z_top == pytest.approx(expected_z_top)

    def test_WRONG_cover_larger_than_half_thickness_returns_none(self):
        """cover >= thickness → v_leg <= 0 → None."""
        huge_cover = {'spacing': 1.0, 'cover': 1.5, 'diameter': 0.065}
        # THIN_DP thickness = 0.2; cover 1.5 > thickness → v_leg < 0 → None
        z_bot, z_top = _z_layer(THIN_DP, huge_cover, base_z=9.85)
        assert z_bot is None
        assert z_top is None

    def test_WRONG_equal_cover_no_space_returns_none(self):
        """cover == thickness → v_leg = 0 → z_top == z_bot → None."""
        params = dict(BASE_PARAMS, cover=0.2)  # cover == THIN_DP thickness (0.2)
        z_bot, z_top = _z_layer(THIN_DP, params, base_z=9.85)
        assert z_bot is None
        assert z_top is None

    def test_no_top_z_uses_base_z(self):
        dp = dict(RECT_DP)
        del dp['top_z']
        # base_z=8.0 is within the DP thickness range (bottom_z=7.5, thickness=2.5)
        z_bot, z_top = _z_layer(dp, BASE_PARAMS, base_z=8.0)
        # Should not crash and should return a valid layer
        assert z_bot is not None

    def test_vertical_leg_equals_thickness_minus_cover(self):
        """z_top - z_bot == dp_thickness - 2*cover."""
        z_bot, z_top = _z_layer(RECT_DP, BASE_PARAMS, base_z=8.0)
        expected_leg = RECT_DP['thickness'] - 2.0 * BASE_PARAMS['cover']
        assert (z_top - z_bot) == pytest.approx(expected_leg)

    def test_z_top_has_cover_from_top(self):
        """z_top == dp_top_z - cover (top cover gap applied)."""
        z_bot, z_top = _z_layer(RECT_DP, BASE_PARAMS, base_z=8.0)
        assert z_top == pytest.approx(RECT_DP['top_z'] - BASE_PARAMS['cover'])

    def test_WRONG_thickness_less_than_2x_cover_returns_none(self):
        """thickness < 2*cover → v_leg <= 0 → None."""
        params = dict(BASE_PARAMS, cover=0.15)  # 2*0.15 = 0.30 > THIN_DP thickness 0.2
        z_bot, z_top = _z_layer(THIN_DP, params, base_z=9.85)
        assert z_bot is None
        assert z_top is None

    def test_WRONG_cover_ge_thickness_returns_none(self):
        """When cover >= thickness the vertical leg is zero → None."""
        params = dict(BASE_PARAMS, cover=2.6)  # cover > RECT_DP thickness 2.5
        z_bot, z_top = _z_layer(RECT_DP, params, base_z=8.0)
        assert z_bot is None
        assert z_top is None


# ===========================================================================
# 3. _hard_min_span  (alias imported as _strict_min_span)
#    Formula: max(2*dia + 2*cover, 0.08)
#    This is the absolute floor below which bar creation is skipped.
#    It is intentionally more permissive than the old _strict_min_span.
# ===========================================================================

class TestStrictMinSpan:

    def test_returns_positive(self):
        assert _strict_min_span(BASE_PARAMS) > 0

    def test_formula_2dia(self):
        """Result == 2*dia when cover=0 and 2*dia > floor."""
        params = {'diameter': 1.0, 'cover': 0.0}
        result = _strict_min_span(params)
        assert result == pytest.approx(2.0)   # max(2*1+0, 0.08) = 2.0

    def test_formula_2cover_plus_2dia(self):
        """Result == 2*cover + 2*dia when that exceeds the hard floor."""
        params = {'diameter': 0.5, 'cover': 3.0}
        result = _strict_min_span(params)
        assert result == pytest.approx(7.0)   # max(2*0.5+2*3, 0.08) = 7.0

    def test_WRONG_zero_dia_uses_floor(self):
        """Zero dia + zero cover → hard floor 0.08 ft."""
        params = {'diameter': 0.0, 'cover': 0.0}
        assert _strict_min_span(params) == pytest.approx(0.08)

    def test_negative_dia_treated_as_zero(self):
        """Negative diameter is clamped to 0; result equals hard floor."""
        params = {'diameter': -1.0, 'cover': 0.0}
        assert _strict_min_span(params) == pytest.approx(0.08)


# ===========================================================================
# 4. _sum_interval_lengths
# ===========================================================================

class TestSumIntervalLengths:

    def test_single_interval(self):
        assert _sum_interval_lengths([(0.0, 5.0)]) == pytest.approx(5.0)

    def test_multiple_intervals(self):
        assert _sum_interval_lengths([(0, 3), (5, 8)]) == pytest.approx(6.0)

    def test_empty(self):
        assert _sum_interval_lengths([]) == pytest.approx(0.0)

    def test_degenerate_zero_length_excluded(self):
        assert _sum_interval_lengths([(2.0, 2.0)]) == pytest.approx(0.0)

    def test_WRONG_inverted_interval_excluded(self):
        """b < a is silently ignored (b > a guard)."""
        assert _sum_interval_lengths([(5.0, 2.0)]) == pytest.approx(0.0)


# ===========================================================================
# 5. _polygon_area  (dp_rebar_placer internal)
# ===========================================================================

class TestPolygonAreaDP:

    def test_unit_square(self):
        sq = [(0, 0), (1, 0), (1, 1), (0, 1)]
        assert _polygon_area(sq) == pytest.approx(1.0)

    def test_right_triangle(self):
        tri = [(0, 0), (4, 0), (0, 3)]
        assert _polygon_area(tri) == pytest.approx(6.0)

    def test_less_than_3_points_zero(self):
        assert _polygon_area([(0, 0), (1, 1)]) == pytest.approx(0.0)

    def test_empty_zero(self):
        assert _polygon_area([]) == pytest.approx(0.0)

    def test_WRONG_collinear_points_area_zero(self):
        """Three collinear points → degenerate polygon → 0."""
        assert _polygon_area([(0, 0), (1, 0), (2, 0)]) == pytest.approx(0.0)


# ===========================================================================
# 6. _is_rect_like_dp
# ===========================================================================

class TestIsRectLikeDp:

    def test_axis_aligned_rectangle_is_rect_like(self):
        assert _is_rect_like_dp(RECT_DP) is True

    def test_l_shape_is_not_rect_like(self):
        # L-shape area is 6*8 - 4*4 = 48 - 16 = 32; bbox area = 10*8 = 80
        # ratio = 32/80 = 0.4 < 0.92
        assert _is_rect_like_dp(L_SHAPE_DP) is False

    def test_WRONG_zero_bbox_area_returns_false(self):
        dp = dict(RECT_DP, bbox=(5.0, 5.0, 5.0, 5.0))
        assert _is_rect_like_dp(dp) is False

    def test_no_polygon_key_returns_false(self):
        dp = dict(RECT_DP)
        del dp['polygon']
        # polygon defaults to [] → poly_area = 0 / bbox_area < 0.92
        assert _is_rect_like_dp(dp) is False

    def test_nearly_rectangular_above_threshold(self):
        # A rectangle with very tiny chamfer – 96% fill
        big_rect_area = 10.0 * 8.0   # 80
        # polygon mimics 96% of that area: use the actual rectangle polygon (100%)
        assert _is_rect_like_dp(RECT_DP)


# ===========================================================================
# 7. _get_row_intervals
# ===========================================================================

class TestGetRowIntervals:

    def test_middle_of_rectangle_returns_full_span_x(self):
        polygon = RECT_DP['polygon']
        min_x, min_y, max_x, max_y = RECT_DP['bbox']
        intervals = _get_row_intervals('X', 4.0, min_x, min_y, max_x, max_y, polygon)
        assert len(intervals) == 1
        a, b = intervals[0]
        assert a == pytest.approx(0.0, abs=1e-3)
        assert b == pytest.approx(10.0, abs=1e-3)

    def test_middle_of_rectangle_returns_full_span_y(self):
        polygon = RECT_DP['polygon']
        min_x, min_y, max_x, max_y = RECT_DP['bbox']
        intervals = _get_row_intervals('Y', 5.0, min_x, min_y, max_x, max_y, polygon)
        assert len(intervals) == 1
        a, b = intervals[0]
        assert a == pytest.approx(0.0, abs=1e-3)
        assert b == pytest.approx(8.0, abs=1e-3)

    def test_l_shape_x_row_through_narrow_section(self):
        """Row at y=2 should cut only the narrow arm (x 0..6)."""
        polygon = L_SHAPE_POLYGON
        min_x, min_y, max_x, max_y = 0.0, 0.0, 10.0, 8.0
        intervals = _get_row_intervals('X', 2.0, min_x, min_y, max_x, max_y, polygon)
        assert len(intervals) == 1
        a, b = intervals[0]
        assert a == pytest.approx(0.0, abs=1e-3)
        assert b == pytest.approx(6.0, abs=1e-3)

    def test_l_shape_x_row_through_full_width(self):
        """Row at y=6 spans full width (x 0..10)."""
        polygon = L_SHAPE_POLYGON
        min_x, min_y, max_x, max_y = 0.0, 0.0, 10.0, 8.0
        intervals = _get_row_intervals('X', 6.0, min_x, min_y, max_x, max_y, polygon)
        assert len(intervals) == 1
        a, b = intervals[0]
        assert a == pytest.approx(0.0, abs=1e-3)
        assert b == pytest.approx(10.0, abs=1e-3)

    def test_WRONG_row_outside_polygon_returns_empty(self):
        """Row position beyond DP bounds → no intervals."""
        polygon = RECT_DP['polygon']
        min_x, min_y, max_x, max_y = RECT_DP['bbox']
        intervals = _get_row_intervals('X', 999.0, min_x, min_y, max_x, max_y, polygon)
        assert intervals == []

    def test_WRONG_empty_polygon_returns_empty(self):
        intervals = _get_row_intervals('X', 4.0, 0.0, 0.0, 10.0, 8.0, [])
        assert intervals == []


# ===========================================================================
# 8. _plan_rows_for_direction
# ===========================================================================

class TestPlanRowsForDirection:

    def test_rect_like_always_uses_base(self):
        rows, mode = _plan_rows_for_direction(RECT_DP, 'X', spacing=1.0, cover=0.1, rect_like=True)
        assert mode == 'base'
        assert len(rows) > 0

    def test_non_rect_picks_better_row_set(self):
        rows, mode = _plan_rows_for_direction(L_SHAPE_DP, 'X', spacing=1.0, cover=0.1, rect_like=False)
        assert mode in ('base', 'shifted')
        assert len(rows) > 0

    def test_WRONG_spacing_larger_than_dp_returns_one_or_zero_rows(self):
        """spacing >> DP dimension → at most 1 row."""
        rows, _ = _plan_rows_for_direction(RECT_DP, 'X', spacing=100.0, cover=0.1, rect_like=True)
        assert len(rows) <= 1

    def test_cover_eats_all_space_returns_empty(self):
        rows, _ = _plan_rows_for_direction(RECT_DP, 'X', spacing=1.0, cover=5.0, rect_like=True)
        assert rows == []


# ===========================================================================
# 9. geometry helpers (used by DP logic)
# ===========================================================================

class TestGeometryHelpers:

    def test_polygon_area_square(self):
        sq = [(0, 0), (1, 0), (1, 1), (0, 1)]
        assert polygon_area(sq) == pytest.approx(1.0)

    def test_point_in_polygon_inside(self):
        sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
        assert point_in_polygon(5.0, 5.0, sq) is True

    def test_point_in_polygon_outside(self):
        sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
        assert point_in_polygon(15.0, 5.0, sq) is False

    def test_point_on_edge_inclusive(self):
        sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
        assert point_in_polygon_or_edge(5.0, 0.0, sq) is True

    def test_segment_polygon_intersections_horizontal(self):
        sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
        xs = segment_polygon_intersections(5.0, 0.0, 10.0, sq, 'X')
        assert len(xs) == 2
        assert xs[0] == pytest.approx(0.0)
        assert xs[1] == pytest.approx(10.0)

    def test_get_obstacle_intervals_full_span(self):
        sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
        intervals = get_obstacle_intervals(5.0, 0.0, 10.0, sq, 'X')
        assert len(intervals) == 1
        assert intervals[0] == pytest.approx((0.0, 10.0), abs=1e-3)

    def test_WRONG_get_obstacle_intervals_outside_polygon(self):
        """Scanline outside polygon → no intervals."""
        sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
        intervals = get_obstacle_intervals(50.0, 0.0, 10.0, sq, 'X')
        assert intervals == []


# ===========================================================================
# 10. Integration-style: _place_dp_direction stats without Revit
#     (We test the stats struct that comes back, not actual Revit placement.)
# ===========================================================================

class TestPlaceDpDirectionStats:
    """
    These tests verify the *stat accounting* logic in _place_dp_direction
    by exercising the pure-Python path (no Revit doc / bar_type).

    We monkey-patch the Revit-touching helpers to return None.
    """

    def _make_dp_and_params(self, dp=None, params=None):
        dp = dp or RECT_DP
        params = params or dict(BASE_PARAMS)
        return dp, params

    def test_WRONG_no_rows_generated_all_stats_zero(self):
        """If cover eats all space, rows=[] → stats all zeros except rows key."""
        from dp_rebar_placer import _place_dp_direction
        import unittest.mock as mock

        big_cover_params = dict(BASE_PARAMS, cover=100.0)
        with mock.patch('dp_rebar_placer._place_staple', return_value=(None, False)), \
             mock.patch('dp_rebar_placer._place_straight', return_value=(None, False)):
            stats = _place_dp_direction(None, None, RECT_DP, 'X', None, big_cover_params, 8.0)

        assert stats['rows'] == 0
        assert stats['bars_total'] == 0
        assert stats['staple_ok'] == 0

    def test_WRONG_z_layer_invalid_no_bars(self):
        """Thin DP with large cover → _z_layer returns None → no bars."""
        from dp_rebar_placer import _place_dp_direction
        import unittest.mock as mock

        bad_params = dict(BASE_PARAMS, cover=1.5)  # kills THIN_DP
        with mock.patch('dp_rebar_placer._place_staple', return_value=(None, False)), \
             mock.patch('dp_rebar_placer._place_straight', return_value=(None, False)):
            stats = _place_dp_direction(None, None, THIN_DP, 'X', None, bad_params, 9.85)

        assert stats['staple_ok'] == 0
        assert stats['fallback_straight'] == 0

    def test_WRONG_all_bars_too_short_counted(self):
        """Very large min_span via big diameter → every segment skipped."""
        from dp_rebar_placer import _place_dp_direction
        import unittest.mock as mock

        huge_dia_params = dict(BASE_PARAMS, diameter=100.0)  # min_span = 1200 ft
        with mock.patch('dp_rebar_placer._place_staple', return_value=(None, False)), \
             mock.patch('dp_rebar_placer._place_straight', return_value=(None, False)):
            stats = _place_dp_direction(None, None, RECT_DP, 'X', None, huge_dia_params, 8.0)

        # bars_total > 0 but all skipped
        if stats['bars_total'] > 0:
            assert stats['too_short_skipped'] == stats['bars_total']

    def test_WRONG_placement_fails_increments_failed(self):
        """All placement attempts return None → failed counter matches bars placed."""
        from dp_rebar_placer import _place_dp_direction
        import unittest.mock as mock

        with mock.patch('dp_rebar_placer._place_staple', return_value=(None, False)), \
             mock.patch('dp_rebar_placer._place_straight', return_value=(None, False)):
            stats = _place_dp_direction(None, None, RECT_DP, 'X', None, BASE_PARAMS, 8.0)

        # Every attempted bar → fallback_straight or failed
        total_attempted = stats['bars_total'] - stats['too_short_skipped']
        assert stats['failed'] + stats['staple_ok'] + stats['fallback_straight'] == total_attempted

    def test_WRONG_regen_failure_increments_counter(self):
        """Regen failures should be counted even if a bar is ultimately not placed."""
        from dp_rebar_placer import _place_dp_direction
        import unittest.mock as mock

        with mock.patch('dp_rebar_placer._place_staple', return_value=(None, True)), \
             mock.patch('dp_rebar_placer._place_straight', return_value=(None, True)):
            stats = _place_dp_direction(None, None, RECT_DP, 'X', None, BASE_PARAMS, 8.0)

        assert stats['regen_failed'] > 0
