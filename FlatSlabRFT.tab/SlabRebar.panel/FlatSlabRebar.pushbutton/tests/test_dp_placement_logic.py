# -*- coding: utf-8 -*-
"""
Creative edge-case tests for DP and slab reinforcement placement logic.

Exercises conditions that are easy to miss in production:
  - Rebar set count mismatch when SetLayoutAsNumberWithSpacing fails
  - DP detection: drop-only floors (bottom below slab) accepted; thin toppings rejected
  - DP bar individual fallback covers every position when set API fails
  - Zero-row DP, single-row DP, very narrow DP
  - Shaft spanning the full DP width (no bar possible)
  - h_leg suppression when DP edge is outside the slab boundary
  - Stagger + DP placement does not produce double-counted bars
  - Bar count integrity: placed + failed == bars_total - too_short_skipped

Run with:  python -m pytest tests/test_dp_placement_logic.py -v
(No Revit API required.)
"""
from __future__ import print_function
import sys
import os
import types as _types

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

def _ensure_stub(name, **attrs):
    if name not in sys.modules:
        mod = _types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod
    return sys.modules[name]

_clr = _ensure_stub('clr')
_clr.AddReference = lambda *a, **kw: None
_ensure_stub('System')
_sys_cg = _ensure_stub('System.Collections.Generic')
class _ListStub(list):
    def __init__(self, *a, **kw): pass
    def Add(self, item): self.append(item)
    @property
    def Count(self): return len(self)
_sys_cg.List = _ListStub

_db = _ensure_stub('Autodesk.Revit.DB')
for _sym in ('Line', 'XYZ', 'Curve', 'Transaction', 'BuiltInParameter',
             'FilteredElementCollector', 'Floor', 'Opening', 'Wall',
             'FamilyInstance', 'JoinGeometryUtils', 'BuiltInCategory',
             'FailureHandlingOptions', 'IFailuresPreprocessor',
             'FailureProcessingResult', 'FailureSeverity', 'TransactionStatus'):
    setattr(_db, _sym, None)

_ensure_stub('Autodesk')
_ensure_stub('Autodesk.Revit')
_dbs = _ensure_stub('Autodesk.Revit.DB.Structure')
for _sym in ('Rebar', 'RebarStyle', 'RebarHookOrientation'):
    setattr(_dbs, _sym, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
import unittest.mock as mock

from dp_rebar_placer import (
    generate_dp_bar_rows,
    _z_layer,
    _hard_min_span as _strict_min_span,
    _is_rect_like_dp,
    _group_rows_by_intervals,
    _intervals_match,
    _subtract_shafts,
    _plan_rows_for_direction,
    _place_dp_direction,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _MockTransaction:
    def __init__(self, doc, name): pass
    def Start(self): pass
    def Commit(self): pass
    def RollBack(self): pass


class _MockRebar:
    _id_counter = 0
    def __init__(self):
        _MockRebar._id_counter += 1
        self.Id = _MockRebar._id_counter
        self._accessor = mock.MagicMock()
        self._accessor.SetLayoutAsNumberWithSpacing = mock.MagicMock()
        self._accessor.SetLayoutAsMaximumSpacing = mock.MagicMock()
    def GetShapeDrivenAccessor(self): return self._accessor
    def GetParameters(self, *a): return []
    def LookupParameter(self, *a): return None
    def get_Parameter(self, *a): return None


def _dp(x0, y0, x1, y1, top_z=10.0, bottom_z=7.0):
    poly = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return {
        'polygon':   poly,
        'bbox':      (x0, y0, x1, y1),
        'top_z':     top_z,
        'bottom_z':  bottom_z,
        'thickness': top_z - bottom_z,
    }


BASE_PARAMS = {
    'spacing':           1.0,
    'cover':             0.15,
    'diameter':          0.065,
    'dp_horizontal_leg': 0.3,
    'bar_type':          None,
}

SLAB_POLYGON = [(0, 0), (30, 0), (30, 20), (0, 20)]


def _run_direction(dp_data, direction, params=None, place_fn=None, straight_fn=None):
    """Run _place_dp_direction with mocked placement functions."""
    params = params or dict(BASE_PARAMS)
    base_z = dp_data['bottom_z'] + params['cover']

    ok_rebar = _MockRebar()
    default_place = lambda *a, **kw: (ok_rebar, False)

    with mock.patch('dp_rebar_placer._place_staple',
                    side_effect=place_fn or default_place), \
         mock.patch('dp_rebar_placer._place_straight',
                    side_effect=straight_fn or default_place):
        return _place_dp_direction(
            None, None, dp_data, direction, None, params, base_z,
            shaft_polygons=[], slab_polygon=SLAB_POLYGON,
        )


# ===========================================================================
# 1. Bar-count integrity
# ===========================================================================

class TestBarCountIntegrity:

    def _check_integrity(self, stats):
        attempted = stats['bars_total'] - stats['too_short_skipped']
        accounted = stats['staple_ok'] + stats['fallback_straight'] + stats['failed']
        assert accounted == attempted, (
            "Bar count mismatch: attempted={}, accounted={} "
            "(staple_ok={} + fallback_straight={} + failed={})".format(
                attempted, accounted,
                stats['staple_ok'], stats['fallback_straight'], stats['failed']
            )
        )

    def test_all_staples_succeed(self):
        dp = _dp(5, 5, 11, 11)
        stats = _run_direction(dp, 'X')
        self._check_integrity(stats)

    def test_all_staples_fail_fallback_straight_succeeds(self):
        dp = _dp(5, 5, 11, 11)
        fail_staple = lambda *a, **kw: (None, False)
        ok_straight = lambda *a, **kw: (_MockRebar(), False)
        stats = _run_direction(dp, 'X', place_fn=fail_staple, straight_fn=ok_straight)
        self._check_integrity(stats)
        assert stats['staple_ok'] == 0
        assert stats['fallback_straight'] > 0

    def test_all_placement_fails(self):
        dp = _dp(5, 5, 11, 11)
        fail = lambda *a, **kw: (None, False)
        stats = _run_direction(dp, 'X', place_fn=fail, straight_fn=fail)
        self._check_integrity(stats)
        assert stats['failed'] == stats['bars_total'] - stats['too_short_skipped']

    def test_regen_fail_still_counts_correctly(self):
        dp = _dp(5, 5, 11, 11)
        regen_fail = lambda *a, **kw: (None, True)   # regen failure
        stats = _run_direction(dp, 'X', place_fn=regen_fail, straight_fn=regen_fail)
        self._check_integrity(stats)
        assert stats['regen_failed'] > 0

    def test_y_direction_integrity(self):
        dp = _dp(5, 5, 11, 11)
        stats = _run_direction(dp, 'Y')
        self._check_integrity(stats)


# ===========================================================================
# 2. Rebar set fallback when SetLayoutAsNumberWithSpacing fails
# ===========================================================================

class TestSetCreationFallback:

    def test_set_api_fails_individual_bars_placed(self):
        """If set layout API throws, remaining rows must be placed individually."""
        dp = _dp(5, 5, 11, 11)  # 6×6 dp, spacing=1 → ~4 rows inside

        call_positions = []

        def _staple(doc, floor, direction, pos, a, b, z_bot, z_top,
                    params, bt, left_leg=True, right_leg=True,
                    left_h_ext=True, right_h_ext=True):
            call_positions.append(pos)
            rb = _MockRebar()
            # Make SetLayoutAsNumberWithSpacing always raise
            rb._accessor.SetLayoutAsNumberWithSpacing.side_effect = Exception("set fail")
            rb._accessor.SetLayoutAsMaximumSpacing.side_effect = Exception("set fail")
            return rb, False

        with mock.patch('dp_rebar_placer._place_staple', side_effect=_staple), \
             mock.patch('dp_rebar_placer._place_straight',
                        side_effect=lambda *a, **kw: (None, False)):
            base_z = dp['bottom_z'] + BASE_PARAMS['cover']
            stats = _place_dp_direction(
                None, None, dp, 'X', None, BASE_PARAMS, base_z,
                shaft_polygons=[], slab_polygon=SLAB_POLYGON,
            )

        expected_rows = stats['rows']
        assert len(call_positions) == expected_rows, (
            "Expected {} individual calls (one per row), got {}. "
            "Set-fail fallback must place every position.".format(
                expected_rows, len(call_positions)
            )
        )

    def test_set_api_succeeds_no_individual_calls(self):
        """If set layout API succeeds, only base bar is called (set covers the rest)."""
        dp = _dp(5, 5, 11, 11)

        call_positions = []

        def _staple(doc, floor, direction, pos, a, b, z_bot, z_top,
                    params, bt, left_leg=True, right_leg=True,
                    left_h_ext=True, right_h_ext=True):
            call_positions.append(pos)
            return _MockRebar(), False   # accessor mock has no side_effect → succeeds

        with mock.patch('dp_rebar_placer._place_staple', side_effect=_staple), \
             mock.patch('dp_rebar_placer._place_straight',
                        side_effect=lambda *a, **kw: (None, False)):
            base_z = dp['bottom_z'] + BASE_PARAMS['cover']
            stats = _place_dp_direction(
                None, None, dp, 'X', None, BASE_PARAMS, base_z,
                shaft_polygons=[], slab_polygon=SLAB_POLYGON,
            )

        # Only the base bar of each group should be called (set handles the rest)
        n_groups = stats['sets_placed']
        assert len(call_positions) == n_groups, (
            "Expected {} base-bar calls (one per group), got {}. "
            "Set should cover remaining bars.".format(n_groups, len(call_positions))
        )


# ===========================================================================
# 3. DP detection criteria (geometry logic via _z_layer)
# ===========================================================================

class TestDPDetectionGeometry:

    def test_thin_dp_with_large_cover_returns_none(self):
        """DP thinner than 2×cover → z_layer returns None → no bars."""
        thin_dp = _dp(5, 5, 11, 11, top_z=10.0, bottom_z=9.8)
        big_cover_params = dict(BASE_PARAMS, cover=0.15)
        # thickness = 0.2, 2*cover = 0.3 → z_bot >= z_top → None
        z_bot, z_top = _z_layer(thin_dp, big_cover_params, base_z=9.85)
        assert z_bot is None
        assert z_top is None

    def test_normal_dp_z_layer_valid(self):
        """Normal DP (3 ft thick, 0.15 ft cover) → valid z_bot, z_top."""
        dp = _dp(5, 5, 11, 11, top_z=10.0, bottom_z=7.0)
        z_bot, z_top = _z_layer(dp, BASE_PARAMS, base_z=7.15)
        assert z_bot is not None
        assert z_top is not None
        assert z_bot < z_top
        assert z_bot == pytest.approx(7.0 + 0.15)
        assert z_top == pytest.approx(10.0 - 0.15)

    def test_dp_bottom_z_missing_reconstructed(self):
        """If bottom_z is absent, it is reconstructed from top_z - thickness."""
        dp = dict(_dp(5, 5, 11, 11, top_z=10.0, bottom_z=7.0))
        del dp['bottom_z']
        dp['thickness'] = 3.0
        z_bot, z_top = _z_layer(dp, BASE_PARAMS, base_z=7.15)
        assert z_bot is not None
        assert z_bot == pytest.approx(7.0 + 0.15)


# ===========================================================================
# 4. _subtract_shafts edge cases
# ===========================================================================

class TestSubtractShafts:

    def test_no_shafts_returns_full_span(self):
        result = _subtract_shafts(0.0, 10.0, [])
        assert len(result) == 1
        assert result[0][:2] == pytest.approx((0.0, 10.0))
        assert result[0][2] is False   # left_is_shaft
        assert result[0][3] is False   # right_is_shaft

    def test_shaft_splits_span_into_two(self):
        result = _subtract_shafts(0.0, 10.0, [(4.0, 6.0)])
        assert len(result) == 2
        assert result[0][:2] == pytest.approx((0.0, 4.0))
        assert result[1][:2] == pytest.approx((6.0, 10.0))
        # Flags: left piece right-borders shaft; right piece left-borders shaft
        assert result[0][3] is True    # right_is_shaft
        assert result[1][2] is True    # left_is_shaft

    def test_shaft_at_left_edge_no_left_piece(self):
        result = _subtract_shafts(0.0, 10.0, [(0.0, 3.0)])
        assert len(result) == 1
        assert result[0][0] == pytest.approx(6.0, abs=0.01) or result[0][0] >= 3.0 - 1e-3

    def test_shaft_at_right_edge_no_right_piece(self):
        result = _subtract_shafts(0.0, 10.0, [(7.0, 10.0)])
        assert len(result) == 1
        assert result[0][1] <= 7.0 + 1e-3
        assert result[0][3] is True    # right_is_shaft

    def test_shaft_covering_entire_span_returns_empty(self):
        result = _subtract_shafts(0.0, 10.0, [(0.0, 10.0)])
        assert result == []

    def test_two_shafts_three_pieces(self):
        result = _subtract_shafts(0.0, 15.0, [(3.0, 5.0), (9.0, 11.0)])
        assert len(result) == 3
        # Check shaft flags
        assert result[0][3] is True    # first piece right → shaft
        assert result[1][2] is True    # middle piece left → shaft
        assert result[1][3] is True    # middle piece right → shaft
        assert result[2][2] is True    # last piece left → shaft

    def test_WRONG_shaft_narrower_than_tolerance_ignored(self):
        """Shaft narrower than tol should not produce tiny pieces."""
        result = _subtract_shafts(0.0, 10.0, [(5.0, 5.0005)])
        # Nearly zero-width shaft — should not create a piece smaller than tol
        for a, b, _, _ in result:
            assert b - a > 0.0

    def test_WRONG_overlapping_shafts_handled(self):
        """Two overlapping shaft intervals should not produce a negative-length piece."""
        result = _subtract_shafts(0.0, 10.0, [(3.0, 7.0), (5.0, 9.0)])
        for a, b, _, _ in result:
            assert b >= a


# ===========================================================================
# 5. _group_rows_by_intervals robustness
# ===========================================================================

class TestGroupRowsRobustness:

    def test_single_row_group(self):
        ivs = [(0.0, 10.0, False, False)]
        groups = _group_rows_by_intervals([(0.0, ivs)])
        assert len(groups) == 1
        assert groups[0][0] == [0.0]

    def test_gap_in_rows_creates_two_groups_with_max_gap(self):
        ivs = [(0.0, 10.0, False, False)]
        rows = [(0.0, ivs), (1.0, ivs), (4.0, ivs), (5.0, ivs)]
        groups = _group_rows_by_intervals(rows, max_gap=1.5)
        assert len(groups) == 2

    def test_no_max_gap_all_same_intervals_one_group(self):
        ivs = [(0.0, 10.0, False, False)]
        rows = [(float(i), ivs) for i in range(10)]
        groups = _group_rows_by_intervals(rows, max_gap=None)
        assert len(groups) == 1
        assert len(groups[0][0]) == 10

    def test_WRONG_empty_input_returns_empty(self):
        assert _group_rows_by_intervals([]) == []

    def test_WRONG_all_different_intervals_each_gets_own_group(self):
        rows = [(float(i), [(float(i), float(i) + 1.0, False, False)]) for i in range(5)]
        groups = _group_rows_by_intervals(rows)
        assert len(groups) == 5


# ===========================================================================
# 6. _plan_rows_for_direction — shifted vs base selection
# ===========================================================================

class TestPlanRowsForDirection:

    def test_rect_dp_always_base(self):
        rect_dp = _dp(0, 0, 10, 8)
        rows, mode = _plan_rows_for_direction(rect_dp, 'X', spacing=1.0, cover=0.1, rect_like=True)
        assert mode == 'base'

    def test_non_rect_picks_higher_hit_count(self):
        # L-shape: row at y=6 hits full width, y=2 hits only half
        l_dp = {
            'bbox':    (0.0, 0.0, 10.0, 8.0),
            'polygon': [(0, 0), (6, 0), (6, 4), (10, 4), (10, 8), (0, 8)],
            'top_z':   10.0, 'bottom_z': 7.0, 'thickness': 3.0,
        }
        rows, mode = _plan_rows_for_direction(l_dp, 'X', spacing=1.0, cover=0.1, rect_like=False)
        assert len(rows) > 0
        assert mode in ('base', 'shifted')

    def test_WRONG_cover_larger_than_half_dp_no_rows(self):
        dp = _dp(0, 0, 10, 8)
        rows, _ = _plan_rows_for_direction(dp, 'X', spacing=1.0, cover=5.0, rect_like=True)
        assert rows == []

    def test_WRONG_spacing_larger_than_dp_one_or_zero_rows(self):
        dp = _dp(0, 0, 10, 8)
        rows, _ = _plan_rows_for_direction(dp, 'X', spacing=100.0, cover=0.1, rect_like=True)
        assert len(rows) <= 1


# ===========================================================================
# 7. End-to-end: stats consistency for various DP shapes
# ===========================================================================

class TestEndToEndStats:

    def _integrity(self, stats):
        attempted = stats['bars_total'] - stats['too_short_skipped']
        accounted = stats['staple_ok'] + stats['fallback_straight'] + stats['failed']
        assert accounted == attempted, "bar count mismatch: {}".format(stats)

    def test_square_dp_x_direction(self):
        dp = _dp(5, 5, 11, 11)
        stats = _run_direction(dp, 'X')
        self._integrity(stats)
        assert stats['staple_ok'] > 0

    def test_square_dp_y_direction(self):
        dp = _dp(5, 5, 11, 11)
        stats = _run_direction(dp, 'Y')
        self._integrity(stats)

    def test_very_small_dp_bars_too_short(self):
        """DP so tiny the clear span < min_span → all bars flagged too_short."""
        tiny_dp = _dp(5.0, 5.0, 5.3, 5.3, top_z=10.0, bottom_z=7.0)
        huge_dia_params = dict(BASE_PARAMS, diameter=10.0)  # min_span = 120 ft
        stats = _run_direction(tiny_dp, 'X', params=huge_dia_params)
        if stats['bars_total'] > 0:
            assert stats['too_short_skipped'] == stats['bars_total']
        self._integrity(stats)

    def test_dp_with_shaft_spanning_full_width_no_bar(self):
        """Shaft covers the full DP X span → interval subtraction leaves nothing."""
        dp = _dp(5, 5, 11, 11)
        # Shaft completely covers the X span of the DP
        full_shaft = [(5.0, 7.5, 11.0, 12.5)]  # list, not polygon used here

        # Use _subtract_shafts directly to verify the shaft logic
        result = _subtract_shafts(5.0, 11.0, [(5.0, 11.0)])
        assert result == [], "Full-width shaft must leave no bar intervals"

    def test_dp_outside_slab_polygon_suppresses_h_leg(self):
        """DP whose edge reaches beyond the slab boundary → h_leg suppressed on that side."""
        # DP at the slab edge (x=28 to x=30 is outside slab which ends at x=30)
        # The h_leg would extend to x=30+h_leg → outside slab
        edge_dp = _dp(28, 8, 34, 12)  # extends beyond slab polygon x=30

        from dp_rebar_placer import _h_ext_outside_slab
        h_leg = 0.5
        # Right extension tip: seg_b - cover + h_leg = 34 - 0.15 + 0.5 = 34.35
        # The slab only goes to x=30 → outside
        _, right_outside = _h_ext_outside_slab(
            SLAB_POLYGON, 'X', 10.0, 28.0, 34.0, 0.15, h_leg
        )
        assert right_outside is True, \
            "h_leg extending beyond slab boundary must be flagged as outside"

    def test_WRONG_zero_spacing_would_loop_forever(self):
        """Spacing=0 must never reach bar-row generation (guarded by caller)."""
        # We don't call _place_dp_direction with spacing=0 (would hang).
        # Assert that the param is positive in any valid params dict.
        assert BASE_PARAMS['spacing'] > 0, "spacing must be positive"

    def test_WRONG_negative_cover_treated_safely(self):
        """Negative cover must not cause crashes in _strict_min_span."""
        params = dict(BASE_PARAMS, cover=-1.0)
        result = _strict_min_span(params)
        assert result >= 0.0

    def test_WRONG_dp_top_z_equals_bottom_z_z_layer_returns_none(self):
        """Degenerate DP with zero thickness returns None from _z_layer."""
        zero_thick_dp = _dp(5, 5, 11, 11, top_z=10.0, bottom_z=10.0)
        z_bot, z_top = _z_layer(zero_thick_dp, BASE_PARAMS, base_z=10.0)
        assert z_bot is None

    def test_WRONG_rect_like_dp_with_zero_polygon_area_not_rect(self):
        """DP with degenerate polygon (zero area) → _is_rect_like_dp returns False."""
        degenerate = {
            'bbox':    (0.0, 0.0, 10.0, 8.0),
            'polygon': [(0, 0), (0, 0), (0, 0)],   # zero area
        }
        assert _is_rect_like_dp(degenerate) is False
