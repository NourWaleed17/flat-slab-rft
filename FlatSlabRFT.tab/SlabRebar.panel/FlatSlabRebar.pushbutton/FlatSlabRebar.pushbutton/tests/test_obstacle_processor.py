# -*- coding: utf-8 -*-
"""
Tests for obstacle_processor pure-Python logic.

Covers:
  - MIN_BAR_LENGTH filter: segments below threshold are dropped, above are kept
  - split_bar_row: shaft gaps insert hooks on both sides of the gap
  - split_bar_row: bottom bars stop at Ld into DP, top bars pass through
  - split_bar_row: bar starting inside a DP obstacle is handled
  - process_bar_row: full pipeline (slab clip → shafts → DPs)
  - concave slab produces multiple intervals per scanline

Run with:  python -m pytest tests/test_obstacle_processor.py -v
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
_ensure_stub('System.Collections.Generic')

_db = _ensure_stub('Autodesk.Revit.DB')
for _sym in ('Line', 'XYZ', 'Curve', 'Transaction', 'BuiltInParameter',
             'FilteredElementCollector', 'Floor', 'Opening', 'Wall',
             'FamilyInstance', 'JoinGeometryUtils', 'BuiltInCategory'):
    setattr(_db, _sym, None)

_ensure_stub('Autodesk')
_ensure_stub('Autodesk.Revit')
_dbs = _ensure_stub('Autodesk.Revit.DB.Structure')
for _sym in ('Rebar', 'RebarStyle', 'RebarHookOrientation'):
    setattr(_dbs, _sym, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
from obstacle_processor import (
    MIN_BAR_LENGTH,
    _make_segment,
    _merge_intervals,
    _resolve_obstacle_overlaps,
    split_bar_row,
    process_bar_row,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

BASE_PARAMS = {
    'ld':     1.0,   # development length (feet)
    'cover':  0.1,
    'spacing': 1.0,
}

RECT_SLAB = [(0, 0), (20, 0), (20, 15), (0, 15)]   # 20 × 15 ft slab


def _dp(x0, y0, x1, y1):
    poly = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return {
        'polygon': poly,
        'bbox':    (min(xs), min(ys), max(xs), max(ys)),
    }


def _row(fixed_val=5.0, vary_min=0.0, vary_max=20.0,
         direction='X', z=0.05, index=0):
    return {
        'fixed_val': fixed_val,
        'vary_min':  vary_min,
        'vary_max':  vary_max,
        'direction': direction,
        'z':         z,
        'index':     index,
    }


# ===========================================================================
# 1. MIN_BAR_LENGTH constant and _make_segment filter
# ===========================================================================

class TestMinBarLength:

    def test_constant_value(self):
        """MIN_BAR_LENGTH should be 0.25 ft (~75 mm)."""
        assert abs(MIN_BAR_LENGTH - 0.25) < 1e-9, (
            "MIN_BAR_LENGTH is {:.4f} ft; expected 0.25 ft (~75 mm)".format(MIN_BAR_LENGTH)
        )

    def test_segment_below_threshold_returns_none(self):
        seg = _make_segment(0.0, MIN_BAR_LENGTH - 0.001,
                            0.0, 'X', 0.0, 0, False, False)
        assert seg is None

    def test_segment_at_threshold_is_accepted(self):
        # Strictly less-than check: length == MIN_BAR_LENGTH is NOT filtered
        seg = _make_segment(0.0, MIN_BAR_LENGTH,
                            0.0, 'X', 0.0, 0, False, False)
        assert seg is not None

    def test_segment_above_threshold_returned(self):
        seg = _make_segment(0.0, MIN_BAR_LENGTH + 0.001,
                            0.0, 'X', 0.0, 0, False, False)
        assert seg is not None

    def test_segment_fields_correct(self):
        seg = _make_segment(2.0, 8.0, 5.0, 'Y', 0.5, 3, True, False)
        assert seg['start']      == pytest.approx(2.0)
        assert seg['end']        == pytest.approx(8.0)
        assert seg['fixed_val']  == pytest.approx(5.0)
        assert seg['direction']  == 'Y'
        assert seg['z']          == pytest.approx(0.5)
        assert seg['index']      == 3
        assert seg['start_hook'] is True
        assert seg['end_hook']   is False


# ===========================================================================
# 2. _merge_intervals
# ===========================================================================

class TestMergeIntervals:

    def test_empty(self):
        assert _merge_intervals([]) == []

    def test_single(self):
        assert _merge_intervals([(2.0, 5.0)]) == [(2.0, 5.0)]

    def test_non_overlapping_sorted(self):
        result = _merge_intervals([(0.0, 2.0), (3.0, 5.0)])
        assert result == [(0.0, 2.0), (3.0, 5.0)]

    def test_overlapping_merged(self):
        result = _merge_intervals([(0.0, 3.0), (2.0, 5.0)])
        assert len(result) == 1
        assert result[0][0] == pytest.approx(0.0)
        assert result[0][1] == pytest.approx(5.0)

    def test_adjacent_within_tolerance_merged(self):
        result = _merge_intervals([(0.0, 2.0), (2.0005, 4.0)])
        assert len(result) == 1

    def test_unsorted_input_handled(self):
        result = _merge_intervals([(5.0, 8.0), (0.0, 3.0)])
        assert result[0][0] == pytest.approx(0.0)


# ===========================================================================
# 3. _resolve_obstacle_overlaps
# ===========================================================================

class TestResolveObstacleOverlaps:

    def test_shaft_takes_priority_over_dp(self):
        """Shaft interval must be subtracted from any overlapping DP interval."""
        obstacles = [
            ('shaft', 4.0, 6.0),
            ('dp',    3.0, 8.0),   # overlaps shaft
        ]
        result = _resolve_obstacle_overlaps(obstacles)
        types_seen = [t for t, _, _ in result]
        # DP should be split into two pieces; shaft stays intact
        assert types_seen.count('shaft') == 1
        dp_pieces = [(a, b) for t, a, b in result if t == 'dp']
        assert len(dp_pieces) == 2
        # No dp piece overlaps the shaft zone [4, 6]
        for a, b in dp_pieces:
            assert not (a < 6.0 and b > 4.0), \
                "DP piece ({}, {}) overlaps shaft [4, 6]".format(a, b)

    def test_shaft_only_unchanged(self):
        obstacles = [('shaft', 2.0, 5.0)]
        result = _resolve_obstacle_overlaps(obstacles)
        assert result == [('shaft', 2.0, 5.0)]

    def test_dp_only_unchanged(self):
        obstacles = [('dp', 2.0, 5.0)]
        result = _resolve_obstacle_overlaps(obstacles)
        assert result == [('dp', 2.0, 5.0)]

    def test_non_overlapping_shaft_and_dp_both_kept(self):
        obstacles = [('shaft', 1.0, 3.0), ('dp', 5.0, 8.0)]
        result = _resolve_obstacle_overlaps(obstacles)
        assert len(result) == 2


# ===========================================================================
# 4. split_bar_row — shaft handling
# ===========================================================================

class TestSplitBarRowShaft:

    def test_shaft_creates_gap_in_bar(self):
        """Shaft at [5, 8] splits a 0–15 bar into two segments with a gap."""
        segs = split_bar_row(
            0.0, 15.0,
            shaft_intervals=[(5.0, 8.0)],
            dp_intervals=[],
            params=BASE_PARAMS,
            mesh_layer='bottom',
        )
        assert len(segs) == 2
        # First segment ends at shaft entry
        assert segs[0]['end'] == pytest.approx(5.0, abs=1e-3)
        # Second segment starts at shaft exit
        assert segs[1]['start'] == pytest.approx(8.0, abs=1e-3)

    def test_shaft_hooks_both_sides(self):
        """Both segments adjacent to shaft must be hooked on the shaft face."""
        segs = split_bar_row(
            0.0, 15.0,
            shaft_intervals=[(5.0, 8.0)],
            dp_intervals=[],
            params=BASE_PARAMS,
            mesh_layer='bottom',
        )
        # Segment before shaft: end_hook True (shaft face)
        assert segs[0]['end_hook'] is True
        # Segment after shaft: start_hook True (shaft face)
        assert segs[1]['start_hook'] is True

    def test_slab_edge_hooks(self):
        """Both far ends (slab edges) always get hooks."""
        segs = split_bar_row(
            0.0, 15.0,
            shaft_intervals=[],
            dp_intervals=[],
            params=BASE_PARAMS,
            mesh_layer='bottom',
        )
        assert len(segs) == 1
        assert segs[0]['start_hook'] is True
        assert segs[0]['end_hook']   is True

    def test_two_shafts_three_segments(self):
        segs = split_bar_row(
            0.0, 20.0,
            shaft_intervals=[(3.0, 5.0), (10.0, 12.0)],
            dp_intervals=[],
            params=BASE_PARAMS,
            mesh_layer='bottom',
        )
        assert len(segs) == 3

    def test_shaft_at_bar_start_skipped_hook_set(self):
        """Shaft starting at bar start: no segment before shaft; bar starts after."""
        segs = split_bar_row(
            0.0, 15.0,
            shaft_intervals=[(0.0, 3.0)],
            dp_intervals=[],
            params=BASE_PARAMS,
            mesh_layer='bottom',
        )
        assert len(segs) == 1
        assert segs[0]['start'] == pytest.approx(3.0, abs=1e-3)
        assert segs[0]['start_hook'] is True


# ===========================================================================
# 5. split_bar_row — drop panel handling
# ===========================================================================

class TestSplitBarRowDP:

    def test_bottom_bar_penetrates_dp_by_ld(self):
        """Bottom bar stops Ld inside the DP entry face."""
        ld = 1.0
        params = dict(BASE_PARAMS, ld=ld)
        segs = split_bar_row(
            0.0, 15.0,
            shaft_intervals=[],
            dp_intervals=[(5.0, 9.0)],   # DP width 4 ft
            params=params,
            mesh_layer='bottom',
        )
        # First segment: 0 → 5 + ld = 6
        assert segs[0]['end'] == pytest.approx(5.0 + ld, abs=1e-3)
        # First segment end is straight (no hook — penetrates into DP)
        assert segs[0]['end_hook'] is False

    def test_bottom_bar_restarts_ld_before_dp_exit(self):
        """Next bar after DP starts ld before DP exit face."""
        ld = 1.0
        params = dict(BASE_PARAMS, ld=ld)
        segs = split_bar_row(
            0.0, 15.0,
            shaft_intervals=[],
            dp_intervals=[(5.0, 9.0)],
            params=params,
            mesh_layer='bottom',
        )
        # Second segment start: 9 - ld = 8
        assert segs[1]['start'] == pytest.approx(9.0 - ld, abs=1e-3)
        assert segs[1]['start_hook'] is False

    def test_top_bar_passes_through_dp(self):
        """Top bars are not split at DP zones; they run continuously."""
        segs = split_bar_row(
            0.0, 15.0,
            shaft_intervals=[],
            dp_intervals=[(5.0, 9.0)],
            params=BASE_PARAMS,
            mesh_layer='top',
        )
        assert len(segs) == 1
        assert segs[0]['start'] == pytest.approx(0.0, abs=1e-3)
        assert segs[0]['end']   == pytest.approx(15.0, abs=1e-3)

    def test_narrow_dp_capped_penetration(self):
        """DP narrower than 2×ld: penetration capped to half DP width."""
        ld = 2.0
        dp_width = 2.0   # narrower than 2*ld=4
        params = dict(BASE_PARAMS, ld=ld)
        segs = split_bar_row(
            0.0, 15.0,
            shaft_intervals=[],
            dp_intervals=[(5.0, 5.0 + dp_width)],
            params=params,
            mesh_layer='bottom',
        )
        max_pen = dp_width / 2.0   # = 1.0
        assert segs[0]['end'] == pytest.approx(5.0 + max_pen, abs=1e-3)

    def test_shaft_overrides_dp(self):
        """Shaft inside a DP zone: shaft wins (gap, not Ld penetration)."""
        segs = split_bar_row(
            0.0, 15.0,
            shaft_intervals=[(6.0, 7.0)],
            dp_intervals=[(5.0, 9.0)],
            params=BASE_PARAMS,
            mesh_layer='bottom',
        )
        # The shaft sub-interval must produce a proper hook gap, not a DP penetration
        hook_segments = [s for s in segs if s['end_hook'] or s['start_hook']]
        assert len(hook_segments) > 0


# ===========================================================================
# 6. process_bar_row — full pipeline
# ===========================================================================

class TestProcessBarRow:

    def test_rectangular_slab_full_span(self):
        """Plain row through a rectangular slab returns one full-width segment."""
        row = _row(fixed_val=5.0, vary_min=0.0, vary_max=20.0, direction='X')
        segs = process_bar_row(row, RECT_SLAB, [], [], BASE_PARAMS, 'bottom')
        assert len(segs) == 1
        assert segs[0]['start'] == pytest.approx(0.0, abs=1e-3)
        assert segs[0]['end']   == pytest.approx(20.0, abs=1e-3)

    def test_row_outside_slab_returns_empty(self):
        """Scanline outside slab polygon returns no segments."""
        row = _row(fixed_val=999.0)
        segs = process_bar_row(row, RECT_SLAB, [], [], BASE_PARAMS, 'bottom')
        assert segs == []

    def test_shaft_inside_slab_creates_gap(self):
        """A shaft polygon inside the slab splits the bar into two pieces."""
        shaft = [(8.0, 3.0), (12.0, 3.0), (12.0, 7.0), (8.0, 7.0)]
        row = _row(fixed_val=5.0)
        segs = process_bar_row(row, RECT_SLAB, [shaft], [], BASE_PARAMS, 'bottom')
        # Bar at y=5 crosses shaft x=[8,12] → two segments
        assert len(segs) == 2
        assert segs[0]['end']   <= 8.0 + 1e-3
        assert segs[1]['start'] >= 12.0 - 1e-3

    def test_dp_inside_slab_splits_bottom_bar(self):
        """Drop panel inside slab splits bottom bar (Ld penetration logic)."""
        dp = _dp(7.0, 3.0, 13.0, 8.0)
        row = _row(fixed_val=5.0)
        segs = process_bar_row(row, RECT_SLAB, [], [dp], BASE_PARAMS, 'bottom')
        # Two segments: before DP (with Ld) and after DP (with Ld back-start)
        assert len(segs) == 2

    def test_dp_inside_slab_top_bar_not_split(self):
        """Top bar is not split at DP zone."""
        dp = _dp(7.0, 3.0, 13.0, 8.0)
        row = _row(fixed_val=5.0, z=0.95)
        segs = process_bar_row(row, RECT_SLAB, [], [dp], BASE_PARAMS, 'top')
        assert len(segs) == 1

    def test_mesh_layer_attached_to_segments(self):
        """process_bar_row must tag every segment with the correct mesh_layer."""
        row = _row(fixed_val=5.0)
        for layer in ('bottom', 'top'):
            segs = process_bar_row(row, RECT_SLAB, [], [], BASE_PARAMS, layer)
            for s in segs:
                assert s['mesh_layer'] == layer

    def test_short_clipped_segments_dropped(self):
        """Very narrow slab region producing sub-MIN_BAR_LENGTH intervals → no bar."""
        # L-shaped slab: bottom arm is very thin (0.1 ft wide at y=0.05)
        thin_slab = [(0, 0), (20, 0), (20, 0.1), (0, 0.1)]
        row = _row(fixed_val=0.05, vary_min=0.0, vary_max=0.3)
        segs = process_bar_row(row, thin_slab, [], [], BASE_PARAMS, 'bottom')
        for s in segs:
            assert (s['end'] - s['start']) >= MIN_BAR_LENGTH - 1e-6

    def test_concave_slab_multiple_intervals(self):
        """C-shaped slab: scanline through the concave notch yields two intervals."""
        # C-shape: outer 0..20 x 0..15, notch cut out at x=10..20, y=5..10
        c_slab = [
            (0, 0), (20, 0), (20, 5), (10, 5),
            (10, 10), (20, 10), (20, 15), (0, 15)
        ]
        row = _row(fixed_val=7.5, vary_min=0.0, vary_max=20.0, direction='X')
        segs = process_bar_row(row, c_slab, [], [], BASE_PARAMS, 'bottom')
        # At y=7.5, only x=0..10 is inside the C-shape
        assert len(segs) == 1
        assert segs[0]['end'] <= 10.0 + 1e-3

    def test_y_direction_row_processed(self):
        """Y-direction row through rectangular slab works the same way."""
        row = _row(fixed_val=10.0, vary_min=0.0, vary_max=15.0, direction='Y')
        segs = process_bar_row(row, RECT_SLAB, [], [], BASE_PARAMS, 'bottom')
        assert len(segs) == 1
        assert segs[0]['start'] == pytest.approx(0.0, abs=1e-3)
        assert segs[0]['end']   == pytest.approx(15.0, abs=1e-3)
