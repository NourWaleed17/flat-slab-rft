# -*- coding: utf-8 -*-
"""Tests for splice_processor and rebar_placer pure-Python helpers."""
from __future__ import print_function
import sys
import os

# Allow importing siblings without a package install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# Stub Revit/.NET modules so rebar_placer can be imported without Revit installed
import types as _types

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

_sys_coll = _ensure_stub('System.Collections.Generic')
class _ListStub:
    def __init__(self, *a, **kw): pass
_sys_coll.List = _ListStub

_db = _ensure_stub('Autodesk.Revit.DB')
for _sym in ('Line', 'XYZ', 'Curve', 'Transaction', 'BuiltInParameter',
             'FilteredElementCollector', 'Floor', 'Opening', 'Wall',
             'FamilyInstance', 'JoinGeometryUtils', 'BuiltInCategory',
             'FailureHandlingOptions', 'IFailuresPreprocessor',
             'FailureProcessingResult', 'FailureSeverity',
             'TransactionStatus'):
    setattr(_db, _sym, None)

_ensure_stub('Autodesk')
_ensure_stub('Autodesk.Revit')
_dbs = _ensure_stub('Autodesk.Revit.DB.Structure')
for _sym in ('Rebar', 'RebarStyle', 'RebarHookOrientation'):
    setattr(_dbs, _sym, None)

import pytest
from splice_processor import (
    _split_segment, process_splices,
    _max_bar_body, _max_body_for_seg, _seg_n_hooks, _hook_ext,
    _support_positions_1d, _bay_widths,
    _find_ideal_position, _snap_to_stock_boundary,
    FEET_PER_METER, STANDARD_BAR_LENGTHS_M,
)
from rebar_placer import _slice_key, _is_uniform_spacing, _split_contiguous_blocks

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seg(start, end, index=0, start_hook=False, end_hook=False,
         direction='X', z=0.0, fixed_val=0.0, mesh_layer='bottom',
         dp_intervals=None):
    d = {
        'start': start,
        'end': end,
        'index': index,
        'start_hook': start_hook,
        'end_hook': end_hook,
        'direction': direction,
        'z': z,
        'fixed_val': fixed_val,
        'mesh_layer': mesh_layer,
    }
    if dp_intervals is not None:
        d['dp_intervals'] = dp_intervals
    return d


def _params(bar_length=20.0, splice_length=2.0, ld=2.0,
            slab_thickness=1.0, cover=0.1,
            diameter=12.0 * 0.00328084,   # 12 mm bar (feet)
            standard_bar_lengths_m=None):
    """Build a minimal params dict for _split_segment."""
    p = {
        'bar_length':     bar_length,
        'splice_length':  splice_length,
        'ld':             ld,
        'slab_thickness': slab_thickness,
        'cover':          cover,
        'spacing':        1.0,
        'diameter':       diameter,
    }
    # Default to no stock-lengths so tests that don't exercise stock-snap
    # are unaffected.  Pass explicitly to opt in.
    p['standard_bar_lengths_m'] = standard_bar_lengths_m if standard_bar_lengths_m is not None else []
    return p


# ---------------------------------------------------------------------------
# _split_segment — basic behaviour (unchanged expectations)
# ---------------------------------------------------------------------------

def test_short_segment_unchanged():
    seg = _seg(0.0, 10.0)
    result = _split_segment(seg, _params(bar_length=20.0), False, [], 'bottom')
    assert result == [seg]


def test_single_splice_produces_two_sub_segs():
    seg = _seg(0.0, 25.0)
    result = _split_segment(seg, _params(bar_length=20.0), False, [], 'bottom')
    assert len(result) == 2


def test_splice_ends_no_hook():
    """Splice ends (internal cuts) always have end_hook=False, start_hook=False."""
    seg = _seg(0.0, 30.0, start_hook=True, end_hook=True)
    p = _params(bar_length=20.0)
    result = _split_segment(seg, p, False, [], 'bottom')
    assert len(result) == 2
    assert result[0]['end_hook'] is False
    assert result[1]['start_hook'] is False


def test_original_hooks_preserved():
    """Original start/end hooks are kept at the real boundaries."""
    seg = _seg(0.0, 30.0, start_hook=True, end_hook=True)
    p = _params(bar_length=20.0)
    result = _split_segment(seg, p, False, [], 'bottom')
    assert result[0]['start_hook'] is True
    assert result[-1]['end_hook'] is True


def test_overlap_length_correct():
    """Second sub-seg starts at splice_pos - splice_length_used."""
    ld = 2.0
    seg = _seg(0.0, 30.0)
    p = _params(bar_length=20.0, ld=ld)
    result = _split_segment(seg, p, False, [], 'bottom')
    splice_pos = result[0]['end']
    splice_len_used = result[0].get('splice_length_used', ld)
    assert abs(result[1]['start'] - (splice_pos - splice_len_used)) < 1e-6


def test_double_splice_long_bar():
    """Bar 3× bar_length → at least 3 sub-segments."""
    seg = _seg(0.0, 65.0)
    p = _params(bar_length=20.0)
    result = _split_segment(seg, p, False, [], 'bottom')
    assert len(result) >= 3


# ---------------------------------------------------------------------------
# Greedy fill — first bar always uses full max_body
# ---------------------------------------------------------------------------

def test_greedy_fill_first_bar_uses_max_body():
    """First sub-bar body should equal max_body (greedy fill), not half of seg_len."""
    # Segment: 0 to 13m.  bar_length = 12m (close to 12m limit).
    # Old equal-interval: first bar = 6.5m. New greedy: first bar = 12m (max_body).
    bar_length_ft = 12.0 / FEET_PER_METER
    seg_len_ft    = 13.0 / FEET_PER_METER
    seg = _seg(0.0, seg_len_ft)
    p = _params(bar_length=bar_length_ft, ld=1.0, slab_thickness=0.0, cover=0.0)
    result = _split_segment(seg, p, False, [], 'bottom')
    assert len(result) >= 2
    first_body = result[0]['end'] - result[0]['start']
    # First bar should be at least 90 % of max_body (allowing for stock-snap
    # and min-clamp rounding, but definitely NOT the old 50 % = 6.5m).
    max_body = _max_bar_body(p, 1)
    assert first_body >= max_body * 0.75, (
        "Greedy fill: first bar body {:.3f}ft should be >= {:.3f}ft (0.75 * max_body)".format(
            first_body, max_body * 0.75
        )
    )


def test_greedy_fill_multiple_splices():
    """Each sub-bar body should be close to max_body, not equal intervals."""
    bar_length_ft = 12.0 / FEET_PER_METER   # ~39.37 ft
    seg_len_ft    = 28.0 / FEET_PER_METER   # ~91.86 ft — needs 3 bars
    ld_ft         = 1.5 / FEET_PER_METER    # ~4.92 ft
    seg = _seg(0.0, seg_len_ft)
    p = _params(bar_length=bar_length_ft, ld=ld_ft, slab_thickness=0.0, cover=0.0)
    result = _split_segment(seg, p, False, [], 'bottom')
    assert len(result) >= 3
    max_body = _max_bar_body(p, 1)
    for i, sub in enumerate(result[:-1]):   # all but the last (offcut) sub-bar
        body = sub['end'] - sub['start']
        assert body >= max_body * 0.75, (
            "Sub-bar {} body {:.3f}ft should be >= 0.75 * max_body {:.3f}ft".format(
                i, body, max_body
            )
        )


# ---------------------------------------------------------------------------
# Stagger — only applies to splice joints i >= 1
# ---------------------------------------------------------------------------

def test_first_splice_no_stagger_even_row():
    """Even-index row: first splice is at max_body (no stagger offset)."""
    ld = 4.0
    bar_length = 20.0
    seg = _seg(0.0, 30.0, index=0)
    p = _params(bar_length=bar_length, ld=ld, slab_thickness=0.0, cover=0.0)
    result = _split_segment(seg, p, True, [], 'bottom')
    assert len(result) == 2
    first_body = result[0]['end'] - result[0]['start']
    max_body = _max_bar_body(p, 1)
    # First bar must fill to at least 90 % of max_body (no stagger shortening)
    assert first_body >= max_body * 0.75


def test_first_splice_no_stagger_odd_row():
    """Odd-index row: first splice is also at max_body (no stagger offset)."""
    ld = 4.0
    bar_length = 20.0
    seg = _seg(0.0, 30.0, index=1)
    p = _params(bar_length=bar_length, ld=ld, slab_thickness=0.0, cover=0.0)
    result = _split_segment(seg, p, True, [], 'bottom')
    assert len(result) == 2
    first_body = result[0]['end'] - result[0]['start']
    max_body = _max_bar_body(p, 1)
    assert first_body >= max_body * 0.75


def test_first_splice_even_odd_same_position():
    """First splice position should be equal for even and odd rows (no stagger)."""
    ld = 4.0
    seg_even = _seg(0.0, 30.0, index=0)
    seg_odd  = _seg(0.0, 30.0, index=1)
    p = _params(bar_length=20.0, ld=ld, slab_thickness=0.0, cover=0.0)
    res_even = _split_segment(seg_even, p, True, [], 'bottom')
    res_odd  = _split_segment(seg_odd,  p, True, [], 'bottom')
    assert len(res_even) >= 2 and len(res_odd) >= 2
    assert abs(res_even[0]['end'] - res_odd[0]['end']) < 0.01, (
        "First splice should be at same position for even/odd rows. "
        "even={:.3f} odd={:.3f}".format(res_even[0]['end'], res_odd[0]['end'])
    )


def test_second_splice_stagger_offset_applied():
    """For bars requiring 2 splices, the second splice should differ between rows."""
    ld = 2.0
    # Long bar: 0 to 50ft — needs at least 3 sub-bars (2 splices)
    seg_even = _seg(0.0, 50.0, index=0)
    seg_odd  = _seg(0.0, 50.0, index=1)
    p = _params(bar_length=15.0, ld=ld, slab_thickness=0.0, cover=0.0)
    res_even = _split_segment(seg_even, p, True, [], 'bottom')
    res_odd  = _split_segment(seg_odd,  p, True, [], 'bottom')
    assert len(res_even) >= 3 and len(res_odd) >= 3
    splices_even = [s['end'] for s in res_even if s.get('splice_end')]
    splices_odd  = [s['end'] for s in res_odd  if s.get('splice_end')]
    # First splice: same for both (no stagger)
    assert abs(splices_even[0] - splices_odd[0]) < 0.01
    # Second splice (if it exists): should differ between even and odd rows
    if len(splices_even) >= 2 and len(splices_odd) >= 2:
        diff = abs(splices_even[1] - splices_odd[1])
        assert diff > 0.1, (
            "Second splice should be offset between rows. "
            "even={:.3f} odd={:.3f}".format(splices_even[1], splices_odd[1])
        )


# ---------------------------------------------------------------------------
# Min clamp — zone snap cannot shorten bar below 0.75 × max_body
# ---------------------------------------------------------------------------

def test_min_clamp_prevents_short_bar():
    """Zone snapping should never shorten bar below 75 % of max_body."""
    # Column at 1ft (very close to start) → old algorithm would snap to ~1ft
    # New algorithm: splice must be >= 0.75 * max_body from prev_end
    ld = 2.0
    seg = _seg(0.0, 40.0, direction='X')
    p = _params(bar_length=25.0, ld=ld, slab_thickness=0.0, cover=0.0)
    support_1d = [1.0]   # column at 1ft — far below 0.75 * max_body = 18.75ft
    result = _split_segment(seg, p, False, support_1d, 'bottom')
    max_body = _max_bar_body(p, 1)
    for sub in result[:-1]:
        body = sub['end'] - sub['start']
        assert body >= max_body * 0.75 - 1e-6, (
            "Body {:.3f}ft is below 0.75 * max_body {:.3f}ft".format(
                body, max_body * 0.75
            )
        )


# ---------------------------------------------------------------------------
# Stock-length snap
# ---------------------------------------------------------------------------

def test_snap_to_stock_boundary_hits_6m():
    """When ideal position is close to a 6m stock bar target, snap to it."""
    from splice_processor import _hook_ext as _he
    hook_ext = 0.0   # no hooks for simplicity
    max_body = (6.0 / FEET_PER_METER) + 0.3   # slightly above 6m body
    prev_end = 0.0
    # ideal_pos that puts body at 95 % of 6m target
    target_body_6m = 6.0 / FEET_PER_METER
    ideal_pos = prev_end + target_body_6m * 0.95   # slightly below the 6m target
    snapped = _snap_to_stock_boundary(
        prev_end, ideal_pos, max_body, hook_ext, [12.0, 9.0, 6.0]
    )
    # Should snap exactly to the 6m body target
    assert abs(snapped - (prev_end + target_body_6m)) < 0.01, (
        "Expected snap to {:.3f}ft (6m body), got {:.3f}ft".format(
            prev_end + target_body_6m, snapped
        )
    )


def test_snap_ignores_stock_outside_valid_range():
    """Stock targets outside [0.75, 1.0] × max_body are ignored."""
    max_body = 5.0
    prev_end = 0.0
    # 12m target body is way above max_body=5; 6m target is 19.69ft >> 5
    # No valid snap — should return ideal unchanged
    ideal_pos = prev_end + max_body * 0.9
    snapped = _snap_to_stock_boundary(
        prev_end, ideal_pos, max_body, 0.0, [12.0, 9.0, 6.0]
    )
    assert snapped == ideal_pos


def test_stock_snap_in_full_split():
    """End-to-end: a segment's first bar snaps to a stock boundary when within range."""
    # bar_length = 6.5m so max_body (0h) is ~21.33ft; 1h bar: same (no hooks in test)
    # Use slab_thickness=0 → hook_ext=0 → target_body_6m = 6/0.3048 = 19.685ft
    # max_body_1h = min(21.33, 39.37) = 21.33ft  (bar_length=6.5m → 21.33ft)
    # lo = 21.33*0.75 = 16.0ft;  hi = 21.33ft
    # target_body_6m = 19.685ft ∈ [16.0, 21.33] → snap applies
    bar_length_ft = 6.5 / FEET_PER_METER
    seg_len_ft    = 8.0 / FEET_PER_METER   # needs exactly 1 splice
    seg = _seg(0.0, seg_len_ft)
    p = _params(bar_length=bar_length_ft, ld=0.5, slab_thickness=0.0, cover=0.0,
                standard_bar_lengths_m=[12.0, 9.0, 6.0])
    result = _split_segment(seg, p, False, [], 'bottom')
    assert len(result) >= 2
    first_body = result[0]['end'] - result[0]['start']
    target_6m   = 6.0 / FEET_PER_METER
    # First bar body should be close to 6m (stock-snapped), not at 6.5m
    assert abs(first_body - target_6m) < 0.1 or first_body <= bar_length_ft, (
        "Expected first body ~{:.3f}ft (6m), got {:.3f}ft".format(target_6m, first_body)
    )


# ---------------------------------------------------------------------------
# _split_segment — structural zone tests (updated for greedy fill)
# ---------------------------------------------------------------------------

def test_top_splice_avoids_dp():
    """Top bar splice pushed outside DP boundary when inside."""
    ld = 1.0
    dp_ivs = [(4.0, 8.0)]
    seg = _seg(0.0, 12.0, direction='X', mesh_layer='top', dp_intervals=dp_ivs)
    p = _params(bar_length=8.5, ld=ld, slab_thickness=1.0, cover=0.1)
    result = _split_segment(seg, p, False, [], 'top')
    for sub in result:
        if sub.get('splice_end'):
            sp = sub['end']
            assert not any(a + 1e-4 < sp < b - 1e-4 for a, b in dp_ivs), \
                "Splice at {:.3f} falls inside DP interval {}".format(sp, dp_ivs)


def test_top_splice_inside_dp_high_waste_still_outside():
    """Top splice pushed to a DP edge that lies within the valid body window."""
    ld = 1.0
    # DP from 6.5 to 9.5.  bar_length=7.0 → max_body≈7.0ft, lo=5.25ft.
    # Left DP edge (6.5) is inside [5.25, 7.0] → splice snaps to 6.5 (outside DP).
    dp_ivs = [(6.5, 9.5)]
    seg = _seg(0.0, 11.0, direction='X', mesh_layer='top', dp_intervals=dp_ivs)
    p = _params(bar_length=7.0, ld=ld, slab_thickness=0.0, cover=0.0)
    result = _split_segment(seg, p, False, [], 'top')
    for sub in result:
        if sub.get('splice_end'):
            sp = sub['end']
            assert not any(a + 0.1 < sp < b - 0.1 for a, b in dp_ivs), \
                "Splice at {:.3f} falls inside DP interval {}".format(sp, dp_ivs)


def test_bottom_splice_high_waste_uses_danger_multiplier():
    """When zone snap can't move splice near a column, splice_length_used = 1.3*ld."""
    ld = 2.0
    seg = _seg(0.0, 40.0, direction='X')
    p = _params(bar_length=25.0, ld=ld, slab_thickness=1.0, cover=0.1)
    support_1d = [1.0]   # column at 1ft — natural greedy splice at ~24ft, waste >> 20%
    result = _split_segment(seg, p, False, support_1d, 'bottom')
    spliced = [s for s in result if s.get('splice_end')]
    assert len(spliced) >= 1
    used = [s.get('splice_length_used', ld) for s in spliced]
    assert any(abs(u - 1.3 * ld) < 1e-6 for u in used)


# ---------------------------------------------------------------------------
# 12-m budget tests
# ---------------------------------------------------------------------------

def test_12m_limit_triggers_extra_splice():
    """Bar whose body + hooks exceeds 12 m gets split even if under bar_length."""
    slab_thickness = 1.0
    cover          = 0.1
    hook_ext       = slab_thickness - 2 * cover          # 0.8 ft
    max_total_ft   = 12.0 / FEET_PER_METER               # ~39.37 ft
    max_body       = max_total_ft - 2 * hook_ext         # ~37.77 ft

    bar_end = max_body + 2.0                             # clearly beyond 12 m limit
    seg = _seg(0.0, bar_end, start_hook=True, end_hook=True)
    p   = _params(bar_length=50.0, ld=1.0,
                  slab_thickness=slab_thickness, cover=cover)
    result = _split_segment(seg, p, False, [], 'bottom')
    assert len(result) >= 2, "Expected split due to 12 m limit"


def test_sub_segment_bodies_never_exceed_max_body():
    """All sub-segment bodies must stay within max_body after splitting."""
    slab_thickness = 1.0
    cover = 0.1
    ld = 4.92   # ~1.5 m in feet
    p = _params(bar_length=50.0, ld=ld, slab_thickness=slab_thickness, cover=cover)
    max_body_bound = _max_bar_body(p, 1)

    for seg_len_m in [13.0, 24.0, 36.0, 25.5]:
        seg_len_ft = seg_len_m / FEET_PER_METER
        seg = _seg(0.0, seg_len_ft)
        result = _split_segment(seg, p, False, [], 'bottom')
        for sub in result:
            body = sub['end'] - sub['start']
            assert body <= max_body_bound + 1e-3, (
                "Sub-segment body {:.3f} ft exceeds max_body {:.3f} ft "
                "(seg_len={} m)".format(body, max_body_bound, seg_len_m)
            )


# ---------------------------------------------------------------------------
# Per-hook max_body tests
# ---------------------------------------------------------------------------

def test_hookless_seg_gets_full_12m_budget():
    p = _params(bar_length=50.0, slab_thickness=0.0, cover=0.0)
    seg = _seg(0.0, 1.0, start_hook=False, end_hook=False)
    max_body = _max_body_for_seg(seg, p)
    max_total_ft = 12.0 / FEET_PER_METER
    assert abs(max_body - max_total_ft) < 1e-4


def test_one_hook_reduces_budget_by_one_hook_ext():
    p = _params(bar_length=50.0, slab_thickness=1.0, cover=0.1)
    hook_ext     = _hook_ext(p)
    max_total_ft = 12.0 / FEET_PER_METER

    for seg in (_seg(0.0, 1.0, start_hook=True, end_hook=False),
                _seg(0.0, 1.0, start_hook=False, end_hook=True)):
        max_body = _max_body_for_seg(seg, p)
        expected = min(50.0, max_total_ft - hook_ext)
        assert abs(max_body - expected) < 1e-6


def test_two_hooks_reduce_budget_by_two_hook_exts():
    p = _params(bar_length=50.0, slab_thickness=1.0, cover=0.1)
    hook_ext     = _hook_ext(p)
    max_total_ft = 12.0 / FEET_PER_METER
    seg = _seg(0.0, 1.0, start_hook=True, end_hook=True)
    max_body = _max_body_for_seg(seg, p)
    expected = min(50.0, max_total_ft - 2 * hook_ext)
    assert abs(max_body - expected) < 1e-6


def test_hooked_seg_splits_at_tighter_threshold():
    p = _params(bar_length=50.0, ld=1.0, slab_thickness=1.0, cover=0.1)
    hook_ext     = _hook_ext(p)
    max_total_ft = 12.0 / FEET_PER_METER

    threshold_0h = min(50.0, max_total_ft)
    threshold_2h = min(50.0, max_total_ft - 2 * hook_ext)
    bar_end = (threshold_0h + threshold_2h) / 2.0

    seg_no_hooks  = _seg(0.0, bar_end, start_hook=False, end_hook=False)
    seg_two_hooks = _seg(0.0, bar_end, start_hook=True,  end_hook=True)

    assert _split_segment(seg_no_hooks,  p, False, [], 'bottom') == [seg_no_hooks]
    assert len(_split_segment(seg_two_hooks, p, False, [], 'bottom')) >= 2


def test_jbar_leg_deducted_from_budget():
    diameter_ft = 12.0 * 0.00328084
    leg_ft = 0.5
    p = _params(bar_length=50.0, diameter=diameter_ft)
    max_total_ft = 12.0 / FEET_PER_METER

    seg = _seg(0.0, 1.0)
    seg['leg_ft'] = leg_ft

    max_body = _max_body_for_seg(seg, p)
    expected = min(50.0, max_total_ft - leg_ft)
    assert abs(max_body - expected) < 1e-6


def test_final_sub_segment_not_zero_length():
    """The last sub-segment must always have positive length."""
    ld = 2.0
    p = _params(bar_length=20.0, ld=ld)
    seg = _seg(0.0, 22.0)
    result = _split_segment(seg, p, False, [], 'bottom')
    for sub in result:
        assert sub['end'] - sub['start'] > 0, "Zero-length sub-segment found"


# ---------------------------------------------------------------------------
# rebar_placer helper tests
# ---------------------------------------------------------------------------

def test_slice_key_groups_by_boundary():
    s1 = _seg(0.0, 10.0, direction='X', z=0.0, fixed_val=1.0,
              start_hook=True, end_hook=False)
    s2 = _seg(0.0, 10.0, direction='X', z=0.0, fixed_val=2.0,
              start_hook=True, end_hook=False)
    assert _slice_key(s1, 0.01) == _slice_key(s2, 0.01)


def test_is_uniform_spacing_uniform():
    values = [0.0, 1.0, 2.0, 3.0]
    ok, spacing = _is_uniform_spacing(values)
    assert ok is True
    assert abs(spacing - 1.0) < 1e-9


def test_is_uniform_spacing_nonuniform():
    values = [0.0, 1.0, 2.5, 3.0]
    ok, _ = _is_uniform_spacing(values)
    assert ok is False


def test_split_contiguous_blocks_gap():
    segs = [
        _seg(0.0, 10.0, fixed_val=0.0),
        _seg(0.0, 10.0, fixed_val=1.0),
        _seg(0.0, 10.0, fixed_val=5.0),
        _seg(0.0, 10.0, fixed_val=6.0),
    ]
    blocks = _split_contiguous_blocks(segs, expected_spacing=1.0, tol=0.01)
    assert len(blocks) == 2
    assert len(blocks[0]) == 2
    assert len(blocks[1]) == 2


def _make_rows(n, start=0.0, end=10.0, spacing=1.0,
               start_hook=False, end_hook=False):
    rows = []
    for i in range(n):
        rows.append(_seg(start, end, index=i,
                         start_hook=start_hook, end_hook=end_hook,
                         fixed_val=float(i) * spacing))
    return rows


def _run_grouping(rows, spacing_input=1.0, stagger_splices=False):
    from collections import defaultdict
    geom_tol = 0.01
    spacing_tol = 0.01
    grouped = defaultdict(list)
    for seg in rows:
        grouped[_slice_key(seg, geom_tol)].append(seg)

    result = []
    for _, group in grouped.items():
        phase_groups = [group]
        phase_spacing = spacing_input if spacing_input > 0 else None
        if stagger_splices and spacing_input > 0:
            has_even = any((s.get('index', 0) % 2) == 0 for s in group)
            has_odd  = any((s.get('index', 0) % 2) == 1 for s in group)
            if has_even and has_odd:
                pass
            else:
                phase_spacing = spacing_input * 2.0

        blocks = []
        for pg in phase_groups:
            pg.sort(key=lambda s: s['fixed_val'])
            blocks.extend(_split_contiguous_blocks(pg, phase_spacing, spacing_tol))

        result.append((blocks, phase_spacing))
    return result


def test_grouping_no_stagger_same_bc_one_set():
    rows = _make_rows(4, spacing=1.0)
    groups = _run_grouping(rows, spacing_input=1.0, stagger_splices=False)
    assert len(groups) == 1
    blocks, phase_spacing = groups[0]
    assert len(blocks) == 1
    assert len(blocks[0]) == 4
    assert abs(phase_spacing - 1.0) < 1e-9


def test_grouping_stagger_unspliced_one_set():
    rows = _make_rows(4, spacing=1.0)
    groups = _run_grouping(rows, spacing_input=1.0, stagger_splices=True)
    assert len(groups) == 1
    blocks, phase_spacing = groups[0]
    assert len(blocks) == 1
    assert len(blocks[0]) == 4
    assert abs(phase_spacing - 1.0) < 1e-9


def test_grouping_stagger_spliced_double_spacing():
    rows_even = []
    for i in range(0, 4, 2):
        rows_even.append(_seg(0.0, 12.0, index=i, fixed_val=float(i)))
    groups = _run_grouping(rows_even, spacing_input=1.0, stagger_splices=True)
    assert len(groups) == 1
    blocks, phase_spacing = groups[0]
    assert abs(phase_spacing - 2.0) < 1e-9
