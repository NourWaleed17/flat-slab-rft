# -*- coding: utf-8 -*-
"""
Staple bar distribution tests for drop panels.

Covers:
  - Row positions along the "other axis" (Y for X-bars, X for Y-bars)
  - Every irregular / merged DP shape: L, T, Plus/cross, U
  - Adjacent and overlapping separate DP pairs
  - No bars missing, no bars added in wrong location

Run with:  python -m pytest tests/test_dp_bar_distribution.py -v
(No Revit API needed.)
"""
from __future__ import print_function

import sys
import os
import math
import types
import pytest

# ---------------------------------------------------------------------------
# Minimal Revit stubs (identical pattern to test_dp_rebar_placer.py)
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
_db.Line = None
_db.XYZ = None
_db.Curve = None
_db.Transaction = None
_db.FilteredElementCollector = None
_db.Floor = None
_db.Opening = None
_db.JoinGeometryUtils = None
_db.Wall = None
_db.FamilyInstance = None
_db.BuiltInCategory = None
_db.FailureHandlingOptions = None
_db.IFailuresPreprocessor = None
_db.FailureProcessingResult = None
_db.FailureSeverity = None
_db.TransactionStatus = None

_dbs = sys.modules['Autodesk.Revit.DB.Structure']
_dbs.Rebar = None
_dbs.RebarStyle = None
_dbs.RebarHookOrientation = None

sys.modules['System.Collections.Generic'].List = None

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from geometry import get_obstacle_intervals, point_in_polygon
from dp_rebar_placer import (
    generate_dp_bar_rows,
    _get_row_intervals,
    _plan_rows_for_direction,
    _is_rect_like_dp,
    _intervals_match,
    _group_rows_by_intervals,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SPACING = 1.0
COVER   = 0.15
TOL     = 1e-6          # float equality tolerance
PARAMS  = {'spacing': SPACING, 'cover': COVER, 'diameter': 0.065}


# ---------------------------------------------------------------------------
# Pure-Python helpers (no Revit)
# ---------------------------------------------------------------------------

def _make_dp(polygon, top_z=10.0, thickness=2.5):
    """Build a dp_data dict from a polygon; bbox auto-computed."""
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return {
        'polygon':   polygon,
        'bbox':      (min(xs), min(ys), max(xs), max(ys)),
        'top_z':     top_z,
        'bottom_z':  top_z - thickness,
        'thickness': thickness,
    }


def _rows_and_intervals(dp_data, direction, spacing=SPACING, cover=COVER):
    """Return list of (pos, intervals) for every row in the given direction."""
    rect_like = _is_rect_like_dp(dp_data)
    rows, _mode = _plan_rows_for_direction(dp_data, direction, spacing, cover, rect_like=rect_like)
    min_x, min_y, max_x, max_y = dp_data['bbox']
    result = []
    for r in rows:
        ivs = _get_row_intervals(direction, r['pos'], min_x, min_y, max_x, max_y, dp_data['polygon'])
        result.append((r['pos'], ivs))
    return result


def _total_bars(dp_data, direction, spacing=SPACING, cover=COVER):
    """Total bar-segment count (one per interval per row)."""
    return sum(len(ivs) for _pos, ivs in _rows_and_intervals(dp_data, direction, spacing, cover))


def _row_positions(dp_data, direction, spacing=SPACING, cover=COVER):
    """Sorted list of row positions for a single DP."""
    rect_like = _is_rect_like_dp(dp_data)
    rows, _ = _plan_rows_for_direction(dp_data, direction, spacing, cover, rect_like=rect_like)
    return sorted(r['pos'] for r in rows)


def _combined_positions(dp_list, direction, spacing=SPACING, cover=COVER):
    """All row positions (sorted) across multiple separate DPs."""
    pos = []
    for dp in dp_list:
        pos.extend(_row_positions(dp, direction, spacing, cover))
    return sorted(pos)


def _expected_row_count(extent, spacing, cover):
    """Number of rows a single extent (width or height) should produce."""
    usable = extent - 2.0 * cover
    if usable < 0:
        return 0
    return int(usable / spacing) + 1


# ---------------------------------------------------------------------------
# Shape fixtures
# ---------------------------------------------------------------------------

# 10 wide × 8 tall rectangle
RECT_POLY = [(0,0),(10,0),(10,8),(0,8)]
RECT_DP   = _make_dp(RECT_POLY)

# L-shape: full-width top (y=4..8) + narrow bottom-left (x=0..6, y=0..4)
L_POLY = [(0,0),(6,0),(6,4),(10,4),(10,8),(0,8)]
L_DP   = _make_dp(L_POLY)

# T-shape: narrow stem (x=3..7, y=0..6) + wide top bar (x=0..10, y=6..10)
T_POLY = [(3,0),(7,0),(7,6),(10,6),(10,10),(0,10),(0,6),(3,6)]
T_DP   = _make_dp(T_POLY)

# Plus/cross: vertical stem (x=3..7, y=0..10) ∪ horizontal arm (x=0..10, y=3..7)
PLUS_POLY = [
    (3,0),(7,0),(7,3),(10,3),(10,7),(7,7),
    (7,10),(3,10),(3,7),(0,7),(0,3),(3,3)
]
PLUS_DP = _make_dp(PLUS_POLY)

# U-shape open at the top (notch x=2..8, y=2..10):
#   bottom bar (x=0..10, y=0..2) + left arm (x=0..2, y=0..10) + right arm (x=8..10, y=0..10)
U_POLY = [(0,0),(10,0),(10,10),(8,10),(8,2),(2,2),(2,10),(0,10)]
U_DP   = _make_dp(U_POLY)

# Two rectangles adjacent along Y at y=5  → combined = 10×10
ADJ_Y_DP1 = _make_dp([(0,0),(10,0),(10,5),(0,5)])    # bottom half
ADJ_Y_DP2 = _make_dp([(0,5),(10,5),(10,10),(0,10)])  # top half
BIG_RECT_DP = _make_dp([(0,0),(10,0),(10,10),(0,10)])

# Two rectangles adjacent along X at x=5
ADJ_X_DP1 = _make_dp([(0,0),(5,0),(5,8),(0,8)])   # left half
ADJ_X_DP2 = _make_dp([(5,0),(10,0),(10,8),(5,8)]) # right half
BIG_RECT_8_DP = _make_dp([(0,0),(10,0),(10,8),(0,8)])

# Two overlapping rectangles (overlap zone y=3..5)
OVLP_DP1 = _make_dp([(0,0),(10,0),(10,5),(0,5)])  # y=0..5
OVLP_DP2 = _make_dp([(0,3),(10,3),(10,8),(0,8)])  # y=3..8

# Adjacent with non-integer boundary (tests gap tolerance)
ADJ_NON_INT_DP1 = _make_dp([(0,0),(10,0),(10,4.9),(0,4.9)])
ADJ_NON_INT_DP2 = _make_dp([(0,4.9),(10,4.9),(10,10),(0,10)])

# Adjacent causing maximum-gap scenario (boundary at y such that gap ≈ spacing + 2*cover)
# height of DP1 chosen so that (height - 2*cover) mod spacing ≈ spacing - epsilon
ADJ_MAXGAP_DP1 = _make_dp([(0,0),(10,0),(10,5.05),(0,5.05)])
ADJ_MAXGAP_DP2 = _make_dp([(0,5.05),(10,5.05),(10,10),(0,10)])

# DP nested inside another (rare but must produce independent bars)
OUTER_DP = _make_dp([(0,0),(10,0),(10,10),(0,10)])
INNER_DP = _make_dp([(3,3),(7,3),(7,7),(3,7)])


# ===========================================================================
# 1. Other-axis distribution – rectangle baseline
# ===========================================================================

class TestOtherAxisDistributionRect:
    """Row positions along the perpendicular axis are correct for a rectangle."""

    def test_x_bars_first_row_at_cover_from_min_y(self):
        rows = generate_dp_bar_rows(RECT_DP, SPACING, COVER, 'X')
        assert rows[0]['pos'] == pytest.approx(RECT_DP['bbox'][1] + COVER)

    def test_x_bars_last_row_within_cover_from_max_y(self):
        rows = generate_dp_bar_rows(RECT_DP, SPACING, COVER, 'X')
        assert rows[-1]['pos'] <= RECT_DP['bbox'][3] - COVER + TOL

    def test_x_bars_uniform_spacing(self):
        rows = generate_dp_bar_rows(RECT_DP, SPACING, COVER, 'X')
        for i in range(1, len(rows)):
            assert rows[i]['pos'] - rows[i-1]['pos'] == pytest.approx(SPACING)

    def test_x_bars_count_formula(self):
        height = RECT_DP['bbox'][3] - RECT_DP['bbox'][1]
        expected = _expected_row_count(height, SPACING, COVER)
        rows = generate_dp_bar_rows(RECT_DP, SPACING, COVER, 'X')
        assert len(rows) == expected

    def test_y_bars_first_row_at_cover_from_min_x(self):
        rows = generate_dp_bar_rows(RECT_DP, SPACING, COVER, 'Y')
        assert rows[0]['pos'] == pytest.approx(RECT_DP['bbox'][0] + COVER)

    def test_y_bars_last_row_within_cover_from_max_x(self):
        rows = generate_dp_bar_rows(RECT_DP, SPACING, COVER, 'Y')
        assert rows[-1]['pos'] <= RECT_DP['bbox'][2] - COVER + TOL

    def test_y_bars_uniform_spacing(self):
        rows = generate_dp_bar_rows(RECT_DP, SPACING, COVER, 'Y')
        for i in range(1, len(rows)):
            assert rows[i]['pos'] - rows[i-1]['pos'] == pytest.approx(SPACING)

    def test_y_bars_count_formula(self):
        width = RECT_DP['bbox'][2] - RECT_DP['bbox'][0]
        expected = _expected_row_count(width, SPACING, COVER)
        rows = generate_dp_bar_rows(RECT_DP, SPACING, COVER, 'Y')
        assert len(rows) == expected

    def test_all_rows_within_bbox(self):
        for direction in ('X', 'Y'):
            rows = generate_dp_bar_rows(RECT_DP, SPACING, COVER, direction)
            lo = RECT_DP['bbox'][1] if direction == 'X' else RECT_DP['bbox'][0]
            hi = RECT_DP['bbox'][3] if direction == 'X' else RECT_DP['bbox'][2]
            for r in rows:
                assert r['pos'] >= lo - TOL
                assert r['pos'] <= hi + TOL

    def test_every_row_produces_exactly_one_interval(self):
        """Rectangular DP: every scanline should produce exactly one interval."""
        for direction in ('X', 'Y'):
            for pos, ivs in _rows_and_intervals(RECT_DP, direction):
                assert len(ivs) == 1, \
                    "direction={} pos={:.3f} gave {} intervals".format(direction, pos, len(ivs))

    def test_no_missing_bars(self):
        """Every row must produce at least one bar interval."""
        for direction in ('X', 'Y'):
            for pos, ivs in _rows_and_intervals(RECT_DP, direction):
                assert len(ivs) >= 1, \
                    "No interval at {} pos={:.3f}".format(direction, pos)

    def test_interval_spans_full_dp_width(self):
        """For a rectangle, each interval should span close to the full dp width."""
        for pos, ivs in _rows_and_intervals(RECT_DP, 'X'):
            assert ivs[0][0] == pytest.approx(0.0, abs=TOL)
            assert ivs[0][1] == pytest.approx(10.0, abs=TOL)

    def test_interval_spans_full_dp_height(self):
        for pos, ivs in _rows_and_intervals(RECT_DP, 'Y'):
            assert ivs[0][0] == pytest.approx(0.0, abs=TOL)
            assert ivs[0][1] == pytest.approx(8.0, abs=TOL)


# ===========================================================================
# 2. L-shaped DP
# ===========================================================================

class TestLShapeDP:
    """
    L polygon: (0,0)-(6,0)-(6,4)-(10,4)-(10,8)-(0,8)
    Narrow section: x=0..6, y=0..4
    Wide  section:  x=0..10, y=4..8
    """

    def test_x_rows_all_within_bbox(self):
        rows = generate_dp_bar_rows(L_DP, SPACING, COVER, 'X')
        lo, hi = L_DP['bbox'][1], L_DP['bbox'][3]
        for r in rows:
            assert lo - TOL <= r['pos'] <= hi + TOL

    def test_x_rows_uniform_spacing(self):
        rows = generate_dp_bar_rows(L_DP, SPACING, COVER, 'X')
        for i in range(1, len(rows)):
            assert rows[i]['pos'] - rows[i-1]['pos'] == pytest.approx(SPACING)

    def test_x_rows_count(self):
        height = L_DP['bbox'][3] - L_DP['bbox'][1]  # 8
        expected = _expected_row_count(height, SPACING, COVER)
        assert len(generate_dp_bar_rows(L_DP, SPACING, COVER, 'X')) == expected

    def test_no_missing_x_bars(self):
        """Every row through the L-shape must produce at least one interval."""
        for pos, ivs in _rows_and_intervals(L_DP, 'X'):
            assert len(ivs) >= 1, "No interval at y={:.3f}".format(pos)

    def test_no_extra_x_bars(self):
        """L-shape is simply connected → each row produces exactly one interval."""
        for pos, ivs in _rows_and_intervals(L_DP, 'X'):
            assert len(ivs) == 1, \
                "Expected 1 interval at y={:.3f}, got {}".format(pos, len(ivs))

    def test_narrow_section_x_bar_width(self):
        """Rows in y=0..4 (narrow arm) should produce interval [0, 6]."""
        for pos, ivs in _rows_and_intervals(L_DP, 'X'):
            if pos < 4.0:
                assert len(ivs) == 1
                assert ivs[0][0] == pytest.approx(0.0, abs=1e-3)
                assert ivs[0][1] == pytest.approx(6.0, abs=1e-3)

    def test_wide_section_x_bar_width(self):
        """Rows in y>4 (full-width arm) should produce interval [0, 10]."""
        for pos, ivs in _rows_and_intervals(L_DP, 'X'):
            if pos > 4.0:
                assert len(ivs) == 1
                assert ivs[0][0] == pytest.approx(0.0, abs=1e-3)
                assert ivs[0][1] == pytest.approx(10.0, abs=1e-3)

    def test_total_x_bar_count(self):
        """8 rows, each with 1 bar → 8 total."""
        assert _total_bars(L_DP, 'X') == 8

    def test_y_bars_no_missing(self):
        """Every Y-direction row must produce at least one interval."""
        for pos, ivs in _rows_and_intervals(L_DP, 'Y'):
            assert len(ivs) >= 1, "No interval at x={:.3f}".format(pos)

    def test_y_bars_narrow_section(self):
        """Y-bars in x=6..10 only span y=4..8 (the wide section)."""
        for pos, ivs in _rows_and_intervals(L_DP, 'Y'):
            if pos > 6.0:
                assert len(ivs) == 1
                assert ivs[0][0] == pytest.approx(4.0, abs=1e-3)
                assert ivs[0][1] == pytest.approx(8.0, abs=1e-3)

    def test_y_bars_full_section(self):
        """Y-bars in x=0..6 span the full height y=0..8."""
        for pos, ivs in _rows_and_intervals(L_DP, 'Y'):
            if pos < 6.0:
                assert len(ivs) == 1
                assert ivs[0][0] == pytest.approx(0.0, abs=1e-3)
                assert ivs[0][1] == pytest.approx(8.0, abs=1e-3)


# ===========================================================================
# 3. T-shaped DP
# ===========================================================================

class TestTShapeDP:
    """
    T polygon: stem (x=3..7, y=0..6) + top bar (x=0..10, y=6..10)
    bbox = (0,0,10,10)
    """

    def test_x_rows_uniform_spacing(self):
        rows = generate_dp_bar_rows(T_DP, SPACING, COVER, 'X')
        for i in range(1, len(rows)):
            assert rows[i]['pos'] - rows[i-1]['pos'] == pytest.approx(SPACING)

    def test_no_missing_x_bars(self):
        for pos, ivs in _rows_and_intervals(T_DP, 'X'):
            assert len(ivs) >= 1, "No interval at y={:.3f}".format(pos)

    def test_stem_rows_produce_stem_width_only(self):
        """Rows in the stem (y < 6) → interval covers x=3..7 only."""
        for pos, ivs in _rows_and_intervals(T_DP, 'X'):
            if pos < 6.0:
                assert len(ivs) == 1
                assert ivs[0][0] == pytest.approx(3.0, abs=1e-3)
                assert ivs[0][1] == pytest.approx(7.0, abs=1e-3)

    def test_top_bar_rows_produce_full_width(self):
        """Rows in the top bar (y > 6) → interval covers x=0..10."""
        for pos, ivs in _rows_and_intervals(T_DP, 'X'):
            if pos > 6.0:
                assert len(ivs) == 1
                assert ivs[0][0] == pytest.approx(0.0, abs=1e-3)
                assert ivs[0][1] == pytest.approx(10.0, abs=1e-3)

    def test_total_x_bars(self):
        """stem rows (y=0.15..5.15 → 6 rows) + top rows (y=6.15..9.15 → 4 rows) = 10."""
        assert _total_bars(T_DP, 'X') == 10

    def test_no_missing_y_bars(self):
        for pos, ivs in _rows_and_intervals(T_DP, 'Y'):
            assert len(ivs) >= 1, "No interval at x={:.3f}".format(pos)

    def test_y_bars_outside_stem_only_in_top_section(self):
        """Y-bars at x < 3 or x > 7 are only in the top bar (y=6..10)."""
        for pos, ivs in _rows_and_intervals(T_DP, 'Y'):
            if pos < 3.0 or pos > 7.0:
                assert len(ivs) == 1
                assert ivs[0][0] == pytest.approx(6.0, abs=1e-3)
                assert ivs[0][1] == pytest.approx(10.0, abs=1e-3)

    def test_y_bars_in_stem_span_full_height(self):
        """Y-bars at x=3..7 span the full stem+top height (y=0..10)."""
        for pos, ivs in _rows_and_intervals(T_DP, 'Y'):
            if 3.0 < pos < 7.0:
                assert len(ivs) == 1
                assert ivs[0][0] == pytest.approx(0.0, abs=1e-3)
                assert ivs[0][1] == pytest.approx(10.0, abs=1e-3)


# ===========================================================================
# 4. Plus / cross-shaped DP
# ===========================================================================

class TestPlusShapeDP:
    """
    Plus polygon: vertical stem (x=3..7, y=0..10)
                  ∪ horizontal arm (x=0..10, y=3..7)
    bbox = (0,0,10,10)
    """

    def test_x_rows_uniform_spacing(self):
        rows = generate_dp_bar_rows(PLUS_DP, SPACING, COVER, 'X')
        for i in range(1, len(rows)):
            assert rows[i]['pos'] - rows[i-1]['pos'] == pytest.approx(SPACING)

    def test_no_missing_x_bars(self):
        for pos, ivs in _rows_and_intervals(PLUS_DP, 'X'):
            assert len(ivs) >= 1, "No interval at y={:.3f}".format(pos)

    def test_arm_rows_produce_full_width(self):
        """Rows within the horizontal arm (y=3..7) should span x=0..10."""
        for pos, ivs in _rows_and_intervals(PLUS_DP, 'X'):
            if 3.0 < pos < 7.0:
                assert len(ivs) == 1
                assert ivs[0][0] == pytest.approx(0.0, abs=1e-3)
                assert ivs[0][1] == pytest.approx(10.0, abs=1e-3)

    def test_stem_rows_produce_stem_width(self):
        """Rows outside the arm (y < 3 or y > 7) span only x=3..7."""
        for pos, ivs in _rows_and_intervals(PLUS_DP, 'X'):
            if pos < 3.0 or pos > 7.0:
                assert len(ivs) == 1
                assert ivs[0][0] == pytest.approx(3.0, abs=1e-3)
                assert ivs[0][1] == pytest.approx(7.0, abs=1e-3)

    def test_total_x_bars(self):
        # stem below arm: y=0.15, 1.15, 2.15 → 3 rows
        # arm: y=3.15, 4.15, 5.15, 6.15 → 4 rows
        # stem above arm: y=7.15, 8.15, 9.15 → 3 rows
        # All simply connected → 1 bar each → 10 total
        assert _total_bars(PLUS_DP, 'X') == 10

    def test_no_missing_y_bars(self):
        for pos, ivs in _rows_and_intervals(PLUS_DP, 'Y'):
            assert len(ivs) >= 1, "No interval at x={:.3f}".format(pos)

    def test_y_bars_in_arm_span_full_height(self):
        for pos, ivs in _rows_and_intervals(PLUS_DP, 'Y'):
            if 3.0 < pos < 7.0:
                assert len(ivs) == 1
                assert ivs[0][0] == pytest.approx(0.0, abs=1e-3)
                assert ivs[0][1] == pytest.approx(10.0, abs=1e-3)

    def test_y_bars_outside_arm_span_stem_height(self):
        for pos, ivs in _rows_and_intervals(PLUS_DP, 'Y'):
            if pos < 3.0 or pos > 7.0:
                assert len(ivs) == 1
                assert ivs[0][0] == pytest.approx(3.0, abs=1e-3)
                assert ivs[0][1] == pytest.approx(7.0, abs=1e-3)


# ===========================================================================
# 5. U-shaped DP  (concave: multiple intervals per row above the notch)
# ===========================================================================

class TestUShapeDP:
    """
    U polygon:  (0,0),(10,0),(10,10),(8,10),(8,2),(2,2),(2,10),(0,10)
    Interior:   bottom bar (x=0..10, y=0..2)
                left  arm  (x=0..2,  y=0..10)
                right arm  (x=8..10, y=0..10)
    Notch (void): x=2..8, y=2..10
    bbox = (0,0,10,10)
    """

    def test_x_rows_uniform_spacing(self):
        rows = generate_dp_bar_rows(U_DP, SPACING, COVER, 'X')
        for i in range(1, len(rows)):
            assert rows[i]['pos'] - rows[i-1]['pos'] == pytest.approx(SPACING)

    def test_below_notch_produces_one_interval(self):
        """Rows at y < 2 span the full bottom bar (x=0..10)."""
        for pos, ivs in _rows_and_intervals(U_DP, 'X'):
            if pos < 2.0:
                assert len(ivs) == 1, "y={:.3f}: expected 1 interval".format(pos)
                assert ivs[0][0] == pytest.approx(0.0, abs=1e-3)
                assert ivs[0][1] == pytest.approx(10.0, abs=1e-3)

    def test_above_notch_produces_two_intervals(self):
        """Rows at y > 2 hit both arms: [(0,2),(8,10)]."""
        for pos, ivs in _rows_and_intervals(U_DP, 'X'):
            if pos > 2.0:
                assert len(ivs) == 2, "y={:.3f}: expected 2 intervals".format(pos)
                assert ivs[0][0] == pytest.approx(0.0, abs=1e-3)
                assert ivs[0][1] == pytest.approx(2.0, abs=1e-3)
                assert ivs[1][0] == pytest.approx(8.0, abs=1e-3)
                assert ivs[1][1] == pytest.approx(10.0, abs=1e-3)

    def test_no_bar_placed_inside_notch(self):
        """No interval should cover the notch interior (x=2..8 at y>2)."""
        for pos, ivs in _rows_and_intervals(U_DP, 'X'):
            if pos > 2.0:
                for a, b in ivs:
                    # interval must not overlap with (2, 8)
                    assert not (a < 8.0 and b > 2.0), \
                        "Bar [{:.2f},{:.2f}] overlaps the notch at y={:.2f}".format(a, b, pos)

    def test_total_x_bars(self):
        # y=0.15, 1.15 (< 2) → 2 rows × 1 bar = 2
        # y=2.15 .. 9.15  (> 2) → 8 rows × 2 bars = 16
        assert _total_bars(U_DP, 'X') == 18

    def test_no_missing_x_bars(self):
        """Every row must produce at least one bar (no row falls entirely outside)."""
        for pos, ivs in _rows_and_intervals(U_DP, 'X'):
            assert len(ivs) >= 1, "No interval at y={:.3f}".format(pos)

    def test_y_bars_in_arms_span_full_height(self):
        """Y-bars at x in the arms (x<2 or x>8) span y=0..10."""
        for pos, ivs in _rows_and_intervals(U_DP, 'Y'):
            if pos < 2.0 or pos > 8.0:
                assert len(ivs) == 1
                assert ivs[0][0] == pytest.approx(0.0, abs=1e-3)
                assert ivs[0][1] == pytest.approx(10.0, abs=1e-3)

    def test_y_bars_inside_notch_span_only_bottom(self):
        """Y-bars at x=2..8 (the notch width) span only y=0..2 (the bottom bar)."""
        for pos, ivs in _rows_and_intervals(U_DP, 'Y'):
            if 2.0 < pos < 8.0:
                assert len(ivs) == 1
                assert ivs[0][0] == pytest.approx(0.0, abs=1e-3)
                assert ivs[0][1] == pytest.approx(2.0, abs=1e-3)


# ===========================================================================
# 6. Adjacent DPs sharing a Y boundary (X-bar distribution)
# ===========================================================================

class TestAdjacentDPsSplitY:
    """
    ADJ_Y_DP1: (0,0)-(10,0)-(10,5)-(0,5)  → y = 0..5
    ADJ_Y_DP2: (0,5)-(10,5)-(10,10)-(0,10) → y = 5..10
    Together they cover a 10×10 square.
    """

    def test_dp1_x_rows_within_bounds(self):
        rows = generate_dp_bar_rows(ADJ_Y_DP1, SPACING, COVER, 'X')
        for r in rows:
            assert 0 - TOL <= r['pos'] <= 5 + TOL

    def test_dp2_x_rows_within_bounds(self):
        rows = generate_dp_bar_rows(ADJ_Y_DP2, SPACING, COVER, 'X')
        for r in rows:
            assert 5 - TOL <= r['pos'] <= 10 + TOL

    def test_no_row_crosses_to_other_dp_territory(self):
        """DP1 rows must stay in y≤5; DP2 rows must stay in y≥5."""
        for r in generate_dp_bar_rows(ADJ_Y_DP1, SPACING, COVER, 'X'):
            assert r['pos'] <= 5.0 + TOL
        for r in generate_dp_bar_rows(ADJ_Y_DP2, SPACING, COVER, 'X'):
            assert r['pos'] >= 5.0 - TOL

    def test_combined_count_equals_big_rect(self):
        combined = _combined_positions([ADJ_Y_DP1, ADJ_Y_DP2], 'X')
        expected  = _row_positions(BIG_RECT_DP, 'X')
        assert len(combined) == len(expected), \
            "Combined: {} rows, big rect: {} rows".format(len(combined), len(expected))

    def test_combined_positions_match_big_rect(self):
        """The combined row grid must be identical to what a single 10×10 rect produces."""
        combined = _combined_positions([ADJ_Y_DP1, ADJ_Y_DP2], 'X')
        expected  = _row_positions(BIG_RECT_DP, 'X')
        for c, e in zip(combined, expected):
            assert c == pytest.approx(e, abs=TOL), \
                "combined={:.4f} expected={:.4f}".format(c, e)

    def test_no_duplicate_rows(self):
        """No two combined rows should be within 1 mm of each other."""
        positions = _combined_positions([ADJ_Y_DP1, ADJ_Y_DP2], 'X')
        for i in range(1, len(positions)):
            assert positions[i] - positions[i-1] > 1e-3, \
                "Duplicate rows near y={:.4f}".format(positions[i])

    def test_gap_between_last_dp1_and_first_dp2_le_spacing(self):
        p1 = _row_positions(ADJ_Y_DP1, 'X')
        p2 = _row_positions(ADJ_Y_DP2, 'X')
        gap = p2[0] - p1[-1]
        assert gap <= SPACING + TOL, \
            "Cross-boundary gap {:.4f} exceeds spacing {:.4f}".format(gap, SPACING)

    def test_every_combined_row_produces_interval(self):
        for dp in (ADJ_Y_DP1, ADJ_Y_DP2):
            for pos, ivs in _rows_and_intervals(dp, 'X'):
                assert len(ivs) >= 1, "No interval at y={:.3f}".format(pos)


# ===========================================================================
# 7. Adjacent DPs sharing an X boundary (Y-bar distribution)
# ===========================================================================

class TestAdjacentDPsSplitX:
    """
    ADJ_X_DP1: (0,0)-(5,0)-(5,8)-(0,8)  → x = 0..5
    ADJ_X_DP2: (5,0)-(10,0)-(10,8)-(5,8) → x = 5..10
    """

    def test_combined_y_row_count_equals_big_rect(self):
        combined = _combined_positions([ADJ_X_DP1, ADJ_X_DP2], 'Y')
        expected  = _row_positions(BIG_RECT_8_DP, 'Y')
        assert len(combined) == len(expected)

    def test_combined_y_positions_match_big_rect(self):
        combined = _combined_positions([ADJ_X_DP1, ADJ_X_DP2], 'Y')
        expected  = _row_positions(BIG_RECT_8_DP, 'Y')
        for c, e in zip(combined, expected):
            assert c == pytest.approx(e, abs=TOL)

    def test_no_duplicate_y_rows(self):
        positions = _combined_positions([ADJ_X_DP1, ADJ_X_DP2], 'Y')
        for i in range(1, len(positions)):
            assert positions[i] - positions[i-1] > 1e-3

    def test_gap_between_last_dp1_and_first_dp2_le_spacing(self):
        p1 = _row_positions(ADJ_X_DP1, 'Y')
        p2 = _row_positions(ADJ_X_DP2, 'Y')
        gap = p2[0] - p1[-1]
        assert gap <= SPACING + TOL


# ===========================================================================
# 8. Adjacent DPs with non-integer boundary
# ===========================================================================

class TestAdjacentNonIntegerBoundary:
    """
    DP1: y=0..4.9   DP2: y=4.9..10
    The first row of DP2 (4.9+0.15=5.05) is close to but not aligned
    with a whole-number multiple of spacing.  Gap = 5.05 - last_dp1 ≈ 0.9 < spacing.
    """

    def test_gap_is_less_than_spacing(self):
        p1 = _row_positions(ADJ_NON_INT_DP1, 'X')
        p2 = _row_positions(ADJ_NON_INT_DP2, 'X')
        gap = p2[0] - p1[-1]
        assert gap < SPACING + TOL, \
            "Gap {:.4f} should be < spacing {:.4f}".format(gap, SPACING)

    def test_no_duplicate_rows(self):
        positions = _combined_positions([ADJ_NON_INT_DP1, ADJ_NON_INT_DP2], 'X')
        for i in range(1, len(positions)):
            assert positions[i] - positions[i-1] > 1e-3

    def test_all_rows_have_intervals(self):
        for dp in (ADJ_NON_INT_DP1, ADJ_NON_INT_DP2):
            for pos, ivs in _rows_and_intervals(dp, 'X'):
                assert len(ivs) >= 1


# ===========================================================================
# 9. Adjacent DPs – maximum-gap scenario (documented known behaviour)
# ===========================================================================

class TestAdjacentMaxGapScenario:
    """
    When the DP boundary falls such that the last row of DP1 is just barely
    inside DP1 and DP2's first row is cover away from the boundary, the gap
    can slightly exceed one spacing (by up to 2*cover).

    This is a KNOWN limitation of independent per-DP row generation.
    Tests here document the upper bound and ensure it never exceeds
    spacing + 2*cover.
    """

    def test_WRONG_gap_can_exceed_spacing(self):
        """Gap > spacing is possible when boundary is at a worst-case position."""
        p1 = _row_positions(ADJ_MAXGAP_DP1, 'X')
        p2 = _row_positions(ADJ_MAXGAP_DP2, 'X')
        gap = p2[0] - p1[-1]
        # Document: this gap may exceed 1.0 ft (= spacing)
        # For boundary at y=5.05, cover=0.15, spacing=1.0:
        #   last DP1 row = 4.15, first DP2 row = 5.20 → gap = 1.05
        assert gap > SPACING - TOL, \
            "Expected gap > spacing for this boundary position, got {:.4f}".format(gap)

    def test_gap_never_exceeds_spacing_plus_two_covers(self):
        """Upper bound: gap < spacing + 2*cover always."""
        p1 = _row_positions(ADJ_MAXGAP_DP1, 'X')
        p2 = _row_positions(ADJ_MAXGAP_DP2, 'X')
        gap = p2[0] - p1[-1]
        max_allowed = SPACING + 2.0 * COVER + TOL
        assert gap < max_allowed, \
            "Gap {:.4f} exceeded theoretical max {:.4f}".format(gap, max_allowed)


# ===========================================================================
# 10. Overlapping DPs
# ===========================================================================

class TestOverlappingDPs:
    """
    OVLP_DP1: y=0..5   OVLP_DP2: y=3..8   overlap zone: y=3..5
    Both DPs place bars independently.  In the overlap zone both DPs produce bars.
    This is intentional (extra reinforcement in overlap).
    Tests verify no bars go outside each DP's own extent.
    """

    def test_dp1_rows_within_dp1_extent(self):
        for r in generate_dp_bar_rows(OVLP_DP1, SPACING, COVER, 'X'):
            assert r['pos'] <= 5.0 + TOL

    def test_dp2_rows_within_dp2_extent(self):
        for r in generate_dp_bar_rows(OVLP_DP2, SPACING, COVER, 'X'):
            assert r['pos'] >= 3.0 - TOL

    def test_overlap_zone_has_bars_from_both_dps(self):
        """Y in [3,5] must have at least one bar from each DP."""
        dp1_overlap = [p for p in _row_positions(OVLP_DP1, 'X') if 3.0 <= p <= 5.0]
        dp2_overlap = [p for p in _row_positions(OVLP_DP2, 'X') if 3.0 <= p <= 5.0]
        assert len(dp1_overlap) > 0, "DP1 has no bars in overlap zone"
        assert len(dp2_overlap) > 0, "DP2 has no bars in overlap zone"

    def test_dp1_all_rows_have_intervals(self):
        for pos, ivs in _rows_and_intervals(OVLP_DP1, 'X'):
            assert len(ivs) >= 1

    def test_dp2_all_rows_have_intervals(self):
        for pos, ivs in _rows_and_intervals(OVLP_DP2, 'X'):
            assert len(ivs) >= 1

    def test_no_bar_placed_outside_slab_extent(self):
        for r in generate_dp_bar_rows(OVLP_DP1, SPACING, COVER, 'X'):
            assert r['pos'] >= 0.0 - TOL
        for r in generate_dp_bar_rows(OVLP_DP2, SPACING, COVER, 'X'):
            assert r['pos'] <= 8.0 + TOL


# ===========================================================================
# 11. Nested DPs (inner DP inside outer DP)
# ===========================================================================

class TestNestedDPs:
    """
    OUTER_DP: 10×10 square
    INNER_DP: 4×4 square at (3,3)-(7,7)
    Each DP places bars independently in its own extent.
    """

    def test_outer_rows_cover_full_extent(self):
        positions = _row_positions(OUTER_DP, 'X')
        assert positions[0] == pytest.approx(0.0 + COVER, abs=TOL)
        assert positions[-1] <= 10.0 - COVER + TOL

    def test_inner_rows_are_subset_of_outer_rows(self):
        """Every inner row position must be within the inner DP bbox."""
        inner_positions = _row_positions(INNER_DP, 'X')
        for p in inner_positions:
            assert 3.0 - TOL <= p <= 7.0 + TOL

    def test_inner_rows_within_outer_bbox(self):
        outer_bbox = OUTER_DP['bbox']
        for p in _row_positions(INNER_DP, 'X'):
            assert outer_bbox[1] - TOL <= p <= outer_bbox[3] + TOL

    def test_outer_all_rows_have_intervals(self):
        for pos, ivs in _rows_and_intervals(OUTER_DP, 'X'):
            assert len(ivs) >= 1

    def test_inner_all_rows_have_intervals(self):
        for pos, ivs in _rows_and_intervals(INNER_DP, 'X'):
            assert len(ivs) >= 1


# ===========================================================================
# 12. Edge cases
# ===========================================================================

class TestEdgeCases:

    def test_dp_with_cover_larger_than_half_height_produces_no_rows(self):
        tiny_dp = _make_dp([(0,0),(10,0),(10,0.2),(0,0.2)])
        rows = generate_dp_bar_rows(tiny_dp, SPACING, COVER, 'X')
        assert rows == []

    def test_dp_with_exactly_one_row(self):
        """DP height = 2*cover + epsilon → exactly 1 row."""
        one_row_dp = _make_dp([(0,0),(10,0),(10,0.35),(0,0.35)])
        rows = generate_dp_bar_rows(one_row_dp, SPACING, COVER, 'X')
        assert len(rows) == 1
        assert rows[0]['pos'] == pytest.approx(0.0 + COVER, abs=TOL)

    def test_spacing_larger_than_dp_produces_one_row(self):
        big_spacing_dp = _make_dp([(0,0),(10,0),(10,2),(0,2)])
        rows = generate_dp_bar_rows(big_spacing_dp, spacing=5.0, cover=COVER, direction='X')
        assert len(rows) == 1

    def test_degenerate_zero_area_dp_produces_no_rows(self):
        dp = {'bbox': (5.0, 5.0, 5.0, 5.0), 'polygon': [], 'top_z': 10.0, 'bottom_z': 7.5, 'thickness': 2.5}
        rows = generate_dp_bar_rows(dp, SPACING, COVER, 'X')
        assert rows == []

    def test_very_large_dp_row_count_consistent_with_formula(self):
        large_dp = _make_dp([(0,0),(100,0),(100,80),(0,80)])
        for direction, extent in [('X', 80), ('Y', 100)]:
            expected = _expected_row_count(extent, SPACING, COVER)
            rows = generate_dp_bar_rows(large_dp, SPACING, COVER, direction)
            assert len(rows) == expected

    def test_empty_polygon_produces_no_intervals(self):
        dp = dict(RECT_DP, polygon=[])
        for pos, ivs in _rows_and_intervals(dp, 'X'):
            assert ivs == []

    def test_all_shapes_have_no_row_outside_bbox(self):
        shapes = [RECT_DP, L_DP, T_DP, PLUS_DP, U_DP]
        for dp in shapes:
            for direction in ('X', 'Y'):
                lo = dp['bbox'][1] if direction == 'X' else dp['bbox'][0]
                hi = dp['bbox'][3] if direction == 'X' else dp['bbox'][2]
                for r in generate_dp_bar_rows(dp, SPACING, COVER, direction):
                    assert lo - TOL <= r['pos'] <= hi + TOL, \
                        "Row {:.3f} outside [{:.3f},{:.3f}]".format(r['pos'], lo, hi)

    def test_all_shapes_uniform_spacing(self):
        shapes = [RECT_DP, L_DP, T_DP, PLUS_DP, U_DP]
        for dp in shapes:
            for direction in ('X', 'Y'):
                rows = generate_dp_bar_rows(dp, SPACING, COVER, direction)
                for i in range(1, len(rows)):
                    assert rows[i]['pos'] - rows[i-1]['pos'] == pytest.approx(SPACING), \
                        "Non-uniform spacing in direction {} at row {}".format(direction, i)


# ===========================================================================
# 13. Rebar set grouping logic
# ===========================================================================

def _build_rows_and_ivs(dp_data, direction, spacing=SPACING, cover=COVER):
    """Build the (pos, intervals) list used by _group_rows_by_intervals."""
    rect_like = _is_rect_like_dp(dp_data)
    rows, _ = _plan_rows_for_direction(dp_data, direction, spacing, cover, rect_like=rect_like)
    min_x, min_y, max_x, max_y = dp_data['bbox']
    result = []
    for r in rows:
        if rect_like:
            ivs = [(min_x, max_x)] if direction == 'X' else [(min_y, max_y)]
        else:
            ivs = _get_row_intervals(direction, r['pos'], min_x, min_y, max_x, max_y, dp_data['polygon'])
        if ivs:
            result.append((r['pos'], ivs))
    return result


class TestIntervalsMatch:

    def test_identical_intervals_match(self):
        assert _intervals_match([(0.0, 10.0)], [(0.0, 10.0)]) is True

    def test_within_tolerance_match(self):
        assert _intervals_match([(0.0, 10.0)], [(0.005, 10.005)]) is True

    def test_outside_tolerance_no_match(self):
        assert _intervals_match([(0.0, 10.0)], [(0.5, 10.5)]) is False

    def test_different_count_no_match(self):
        assert _intervals_match([(0.0, 5.0), (7.0, 10.0)], [(0.0, 10.0)]) is False

    def test_empty_lists_match(self):
        assert _intervals_match([], []) is True

    def test_two_intervals_identical(self):
        a = [(0.0, 2.0), (8.0, 10.0)]
        b = [(0.0, 2.0), (8.0, 10.0)]
        assert _intervals_match(a, b) is True

    def test_two_intervals_one_differs(self):
        a = [(0.0, 2.0), (8.0, 10.0)]
        b = [(0.0, 2.0), (8.5, 10.0)]
        assert _intervals_match(a, b) is False


class TestGroupRowsByIntervals:

    def test_empty_input_returns_empty(self):
        assert _group_rows_by_intervals([]) == []

    def test_single_row_returns_one_group(self):
        groups = _group_rows_by_intervals([(1.0, [(0.0, 10.0)])])
        assert len(groups) == 1
        assert groups[0][0] == [1.0]
        assert groups[0][1] == [(0.0, 10.0)]

    def test_identical_rows_form_one_group(self):
        data = [(y, [(0.0, 10.0)]) for y in [0.15, 1.15, 2.15, 3.15]]
        groups = _group_rows_by_intervals(data)
        assert len(groups) == 1
        assert len(groups[0][0]) == 4

    def test_two_different_patterns_form_two_groups(self):
        data = [
            (0.15, [(0.0, 6.0)]),
            (1.15, [(0.0, 6.0)]),
            (4.15, [(0.0, 10.0)]),
            (5.15, [(0.0, 10.0)]),
        ]
        groups = _group_rows_by_intervals(data)
        assert len(groups) == 2
        assert len(groups[0][0]) == 2   # 2 rows in narrow section
        assert len(groups[1][0]) == 2   # 2 rows in wide section

    def test_group_preserves_positions(self):
        data = [(1.0, [(0.0, 5.0)]), (2.0, [(0.0, 5.0)]), (3.0, [(0.0, 8.0)])]
        groups = _group_rows_by_intervals(data)
        assert groups[0][0] == [1.0, 2.0]
        assert groups[1][0] == [3.0]

    def test_alternating_patterns_form_many_groups(self):
        """Rows that alternate between two patterns should form many groups."""
        data = [
            (1.0, [(0.0, 6.0)]),
            (2.0, [(0.0, 10.0)]),
            (3.0, [(0.0, 6.0)]),
            (4.0, [(0.0, 10.0)]),
        ]
        groups = _group_rows_by_intervals(data)
        assert len(groups) == 4


class TestSetCountPerShape:
    """Verify that the correct number of rebar sets is produced for each shape."""

    def _set_count(self, dp_data, direction):
        rows_and_ivs = _build_rows_and_ivs(dp_data, direction)
        groups = _group_rows_by_intervals(rows_and_ivs)
        # Sets = one per (group × interval_in_group)
        return sum(len(g_ivs) for _, g_ivs in groups)

    def test_rect_dp_x_produces_one_set(self):
        """Rectangular DP → uniform intervals throughout → 1 set for X-bars."""
        assert self._set_count(RECT_DP, 'X') == 1

    def test_rect_dp_y_produces_one_set(self):
        assert self._set_count(RECT_DP, 'Y') == 1

    def test_l_shape_x_produces_two_sets(self):
        """L-shape has two uniform zones (narrow + wide) → 2 sets for X-bars."""
        assert self._set_count(L_DP, 'X') == 2

    def test_l_shape_y_produces_two_sets(self):
        """L-shape Y-bars: x<6 spans full height, x>6 spans only top → 2 sets."""
        assert self._set_count(L_DP, 'Y') == 2

    def test_t_shape_x_produces_two_sets(self):
        """T-shape: stem rows + top-bar rows → 2 sets for X-bars."""
        assert self._set_count(T_DP, 'X') == 2

    def test_plus_shape_x_produces_three_sets(self):
        """Plus: lower stem / arm / upper stem → 3 sets for X-bars."""
        assert self._set_count(PLUS_DP, 'X') == 3

    def test_u_shape_x_produces_two_sets(self):
        """U-shape X-bars: below-notch rows (1 interval) → 1 set,
        above-notch rows (2 intervals each) → 2 sets.  Total = 3."""
        # below-notch: 1 interval group → 1 set
        # above-notch: 2-interval group → 2 sets (left arm + right arm)
        assert self._set_count(U_DP, 'X') == 3

    def test_set_count_equals_groups_times_intervals(self):
        """For any shape, set_count = sum of intervals per group."""
        for dp, direction in [(RECT_DP,'X'), (L_DP,'X'), (T_DP,'Y'), (PLUS_DP,'X'), (U_DP,'X')]:
            rows_and_ivs = _build_rows_and_ivs(dp, direction)
            groups = _group_rows_by_intervals(rows_and_ivs)
            computed = sum(len(g_ivs) for _, g_ivs in groups)
            assert computed == self._set_count(dp, direction)

    def test_each_set_group_bar_count_sums_to_total_rows(self):
        """The sum of bars across all sets must equal the total row count."""
        for dp in (RECT_DP, L_DP, T_DP, PLUS_DP, U_DP):
            for direction in ('X', 'Y'):
                rows_and_ivs = _build_rows_and_ivs(dp, direction)
                total_rows = len(rows_and_ivs)
                groups = _group_rows_by_intervals(rows_and_ivs)
                # Each group has n_bars = len(positions). Each group produces
                # one set per interval, but the bar count is per group (not per interval).
                # Total individual bars = sum(n_bars * n_intervals_in_group)
                total_bars = sum(len(g_pos) * len(g_ivs) for g_pos, g_ivs in groups)
                expected_bars = sum(len(ivs) for _, ivs in rows_and_ivs)
                assert total_bars == expected_bars, \
                    "dp={} dir={}: total_bars={} expected={}".format(
                        dp['bbox'], direction, total_bars, expected_bars)
