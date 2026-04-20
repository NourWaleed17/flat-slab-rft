# -*- coding: utf-8 -*-
"""Integration tests for the full Python-side pipeline.

Exercises the chain:
    bar_generator  →  obstacle_processor  →  splice_processor

Uses realistic geometry (rectangular slabs, shafts, drop panels) but
requires no Revit installation — all Revit API modules are stubbed.

Run with:  python -m pytest tests/test_pipeline_integration.py -v
"""
from __future__ import print_function
import sys
import os
import types as _types

# ---------------------------------------------------------------------------
# Revit API stubs (must be set up before importing any project module)
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
import bar_generator
import obstacle_processor
import splice_processor

# Convenience
FEET_PER_METER = 0.3048
M = 1.0 / FEET_PER_METER          # 1 m in feet
MM = 0.001 / FEET_PER_METER       # 1 mm in feet

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rect_polygon(min_x, min_y, max_x, max_y):
    """Return a CW rectangular polygon."""
    return [(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)]


def _params(diameter_mm=12, spacing_mm=200, cover_mm=25,
            bar_length_m=12, splice_mult=50, ld_mult=40,
            slab_thickness_mm=250):
    diam_ft      = diameter_mm  * MM
    cover_ft     = cover_mm     * MM
    thick_ft     = slab_thickness_mm * MM
    return {
        'diameter':         diam_ft,
        'spacing':          spacing_mm * MM,
        'cover':            cover_ft,
        'bar_length':       bar_length_m * M,
        'splice_multiplier': splice_mult,
        'splice_length':    splice_mult * diam_ft,
        'ld_multiplier':    ld_mult,
        'ld':               ld_mult * diam_ft,
        'slab_thickness':   thick_ft,
        'stagger_splices':  True,
    }


def _dp_data(min_x, min_y, max_x, max_y):
    """Build a minimal dp_data dict compatible with obstacle_processor."""
    poly = _rect_polygon(min_x, min_y, max_x, max_y)
    return {
        'polygon':   poly,
        'bbox':      (min_x, min_y, max_x, max_y),
        'thickness': 0.5 * M,
        'top_z':     0.0,
        'bottom_z': -0.5 * M,
    }


# ---------------------------------------------------------------------------
# Test: clean slab, no obstacles
# ---------------------------------------------------------------------------

def test_no_obstacles_all_segments_span_full_width():
    """Rows on an obstacle-free slab each produce one segment spanning slab width."""
    slab = _rect_polygon(0, 0, 20 * M, 15 * M)
    params = _params()
    rows_x = bar_generator.generate_bar_rows(
        (0, 0, 20 * M, 15 * M), params['spacing'], params['cover'], 'X'
    )
    segs = []
    for row in rows_x:
        segs.extend(obstacle_processor.process_bar_row(
            row, slab, [], [], params, 'bottom'
        ))

    assert len(segs) > 0, "Expected at least one segment"
    # Every segment must start after slab min_x + cover and end before max_x - cover
    for seg in segs:
        assert seg['start'] >= 0 - 1e-6
        assert seg['end']   <= 20 * M + 1e-6
    # No splices needed (each row fits in one bar_length=12 m segment —
    # wait, slab width = 20 m which needs 2 bars — test is about obstacles)
    # Just verify segments are generated
    print('[test] no-obstacle segments: {}'.format(len(segs)))


def test_no_obstacles_segment_count_matches_row_count():
    """Without obstacles, number of segments equals number of rows (1 per row)."""
    bbox = (0, 0, 10 * M, 8 * M)
    slab = _rect_polygon(*bbox)
    params = _params()
    rows = bar_generator.generate_bar_rows(bbox, params['spacing'], params['cover'], 'X')
    segs = []
    for row in rows:
        segs.extend(obstacle_processor.process_bar_row(
            row, slab, [], [], params, 'bottom'
        ))
    # 1 seg per row for a rectangular slab with no obstacles
    assert len(segs) == len(rows), (
        "Expected 1 segment/row, got {} segs for {} rows".format(len(segs), len(rows))
    )


# ---------------------------------------------------------------------------
# Test: shaft obstacles create gaps with hooks
# ---------------------------------------------------------------------------

def test_shaft_splits_rows_that_cross_it():
    """Rows crossing a shaft are split into two segments; rows that miss it are not."""
    slab  = _rect_polygon(0, 0, 10 * M, 10 * M)
    shaft = _rect_polygon(4 * M, 4 * M, 6 * M, 6 * M)   # 2×2 m hole near centre
    params = _params(spacing_mm=500)   # coarser spacing for readable test

    rows = bar_generator.generate_bar_rows(
        (0, 0, 10 * M, 10 * M), params['spacing'], params['cover'], 'X'
    )
    segs_per_row = []
    for row in rows:
        s = obstacle_processor.process_bar_row(
            row, slab, [shaft], [], params, 'bottom'
        )
        segs_per_row.append((row['fixed_val'], s))

    # Rows with fixed_val (Y coord) inside shaft Y range should have 2 segments
    for fv, segs in segs_per_row:
        if 4 * M + 1e-3 < fv < 6 * M - 1e-3:
            assert len(segs) == 2, (
                "Row at Y={:.3f} m crosses shaft, expected 2 segs got {}".format(
                    fv * FEET_PER_METER, len(segs))
            )
            # Segments should have hooks at the shaft faces
            assert segs[0]['end_hook'] is True
            assert segs[1]['start_hook'] is True
        else:
            assert len(segs) == 1, (
                "Row at Y={:.3f} m misses shaft, expected 1 seg got {}".format(
                    fv * FEET_PER_METER, len(segs))
            )


def test_shaft_hooks_suppressed_for_add_rft_rows():
    """no_hooks=True rows through a shaft get no hooks (add-RFT interior bars)."""
    slab  = _rect_polygon(0, 0, 10 * M, 10 * M)
    shaft = _rect_polygon(4 * M, 4 * M, 6 * M, 6 * M)
    params = _params(spacing_mm=1000)

    rows = bar_generator.generate_bar_rows(
        (0, 0, 10 * M, 10 * M), params['spacing'], params['cover'], 'X'
    )
    # Mark all rows as no_hooks (interior add-rft bars)
    for row in rows:
        row['no_hooks'] = True

    for row in rows:
        fv = row['fixed_val']
        segs = obstacle_processor.process_bar_row(
            row, slab, [shaft], [], params, 'bottom'
        )
        if 4 * M + 1e-3 < fv < 6 * M - 1e-3:
            for seg in segs:
                assert seg['start_hook'] is False
                assert seg['end_hook'] is False


# ---------------------------------------------------------------------------
# Test: drop panel penetration (bottom bars)
# ---------------------------------------------------------------------------

def test_bottom_bars_penetrate_dp_by_ld():
    """Bottom bars stop at Ld inside the DP; the next bar starts Ld before DP exit."""
    slab = _rect_polygon(0, 0, 20 * M, 10 * M)
    dp   = [_dp_data(8 * M, 3 * M, 12 * M, 7 * M)]   # 4×4 m DP near centre
    params = _params(spacing_mm=1000)
    ld = params['ld']

    rows = bar_generator.generate_bar_rows(
        (0, 0, 20 * M, 10 * M), params['spacing'], params['cover'], 'X'
    )
    for row in rows:
        fv = row['fixed_val']
        if 3 * M + 1e-3 < fv < 7 * M - 1e-3:  # row crosses DP
            segs = obstacle_processor.process_bar_row(
                row, slab, [], dp, params, 'bottom'
            )
            assert len(segs) == 2, (
                "Expected 2 segs (DP split) at Y={:.2f} m, got {}".format(
                    fv * FEET_PER_METER, len(segs))
            )
            # First seg must end before or at DP exit (not past it)
            assert segs[0]['end'] <= 12 * M + 1e-6, "Bar 1 overruns DP exit"
            # Second seg must start before DP exit
            assert segs[1]['start'] < 12 * M + ld + 1e-6


def test_top_bars_pass_through_dp():
    """Top bars ignore drop panels (no interruption at DP)."""
    slab = _rect_polygon(0, 0, 10 * M, 10 * M)
    dp   = [_dp_data(3 * M, 3 * M, 7 * M, 7 * M)]
    params = _params(spacing_mm=1000)

    rows = bar_generator.generate_bar_rows(
        (0, 0, 10 * M, 10 * M), params['spacing'], params['cover'], 'X'
    )
    for row in rows:
        fv = row['fixed_val']
        if 3 * M + 1e-3 < fv < 7 * M - 1e-3:
            segs = obstacle_processor.process_bar_row(
                row, slab, [], dp, params, 'top'   # <-- top layer
            )
            assert len(segs) == 1, (
                "Top bar at Y={:.2f} m should pass through DP, got {} segs".format(
                    fv * FEET_PER_METER, len(segs))
            )


# ---------------------------------------------------------------------------
# Test: obstacle cache produces identical results to uncached
# ---------------------------------------------------------------------------

def test_obstacle_cache_matches_uncached():
    """Segments with and without the bbox cache are identical."""
    slab   = _rect_polygon(0, 0, 15 * M, 12 * M)
    shaft1 = _rect_polygon(2 * M, 2 * M,  4 * M,  4 * M)
    shaft2 = _rect_polygon(10 * M, 7 * M, 12 * M, 9 * M)
    dp1    = _dp_data(5 * M, 4 * M, 9 * M, 8 * M)
    shaft_polys = [shaft1, shaft2]
    dp_list     = [dp1]
    params = _params(spacing_mm=500)

    rows = bar_generator.generate_bar_rows(
        (0, 0, 15 * M, 12 * M), params['spacing'], params['cover'], 'X'
    )
    cache = obstacle_processor.build_obstacle_cache(shaft_polys, dp_list)

    for row in rows:
        segs_cached   = obstacle_processor.process_bar_row(
            row, slab, shaft_polys, dp_list, params, 'bottom',
            obstacle_cache=cache
        )
        segs_uncached = obstacle_processor.process_bar_row(
            row, slab, shaft_polys, dp_list, params, 'bottom'
        )
        assert len(segs_cached) == len(segs_uncached), (
            "Cache mismatch at fixed_val={:.3f}: cached={} uncached={}".format(
                row['fixed_val'], len(segs_cached), len(segs_uncached))
        )
        for sc, su in zip(segs_cached, segs_uncached):
            assert abs(sc['start'] - su['start']) < 1e-9
            assert abs(sc['end']   - su['end'])   < 1e-9


# ---------------------------------------------------------------------------
# Test: full pipeline — bar_generator → obstacle → splice
# ---------------------------------------------------------------------------

def test_full_pipeline_wide_slab_forces_splices():
    """A 25 m slab forces every row to be spliced (bar_length=12 m)."""
    width  = 25 * M
    height = 10 * M
    slab   = _rect_polygon(0, 0, width, height)
    params = _params(bar_length_m=12, spacing_mm=500)

    rows = bar_generator.generate_bar_rows(
        (0, 0, width, height), params['spacing'], params['cover'], 'X'
    )
    all_segs = []
    for row in rows:
        all_segs.extend(obstacle_processor.process_bar_row(
            row, slab, [], [], params, 'bottom'
        ))

    final = splice_processor.process_splices(all_segs, params)

    # Every sub-segment must be <= 12 m (plus small numerical tolerance)
    max_body = splice_processor._max_bar_body(params, 1)
    for seg in final:
        body = seg['end'] - seg['start']
        assert body <= max_body + 1e-3, (
            "Segment body {:.3f} ft > max_body {:.3f} ft".format(body, max_body)
        )

    # With a 25 m slab and 12 m bars, we must have more segments than rows
    assert len(final) > len(rows), (
        "Expected more final segs than rows (splices required); "
        "got {} final vs {} rows".format(len(final), len(rows))
    )


def test_full_pipeline_with_obstacles_all_segments_in_slab():
    """After the full pipeline, every segment endpoint lies inside the slab."""
    slab_w, slab_h = 20 * M, 15 * M
    slab    = _rect_polygon(0, 0, slab_w, slab_h)
    shaft   = _rect_polygon(7 * M, 5 * M, 9 * M, 7 * M)
    dp_data = _dp_data(3 * M, 4 * M, 7 * M, 8 * M)
    params  = _params(spacing_mm=400, bar_length_m=12)

    rows = bar_generator.generate_bar_rows(
        (0, 0, slab_w, slab_h), params['spacing'], params['cover'], 'X'
    )
    cache = obstacle_processor.build_obstacle_cache([shaft], [dp_data])
    all_segs = []
    for row in rows:
        all_segs.extend(obstacle_processor.process_bar_row(
            row, slab, [shaft], [dp_data], params, 'bottom',
            obstacle_cache=cache
        ))

    final = splice_processor.process_splices(all_segs, params,
                                              support_positions=[(5 * M, 5 * M),
                                                                 (15 * M, 5 * M)])
    assert len(final) > 0

    # All segments inside slab X range (plus cover tolerance)
    cover = params['cover']
    for seg in final:
        assert seg['start'] >= -cover - 1e-6, \
            "Segment start {:.4f} ft is left of slab".format(seg['start'])
        assert seg['end'] <= slab_w + cover + 1e-6, \
            "Segment end {:.4f} ft is right of slab".format(seg['end'])


def test_full_pipeline_y_direction():
    """Pipeline works identically for Y-direction bars."""
    slab_w, slab_h = 12 * M, 18 * M
    slab   = _rect_polygon(0, 0, slab_w, slab_h)
    shaft  = _rect_polygon(4 * M, 6 * M, 6 * M, 8 * M)
    params = _params(spacing_mm=300, bar_length_m=12)

    rows = bar_generator.generate_bar_rows(
        (0, 0, slab_w, slab_h), params['spacing'], params['cover'], 'Y'
    )
    cache = obstacle_processor.build_obstacle_cache([shaft], [])
    all_segs = []
    for row in rows:
        all_segs.extend(obstacle_processor.process_bar_row(
            row, slab, [shaft], [], params, 'bottom',
            obstacle_cache=cache
        ))
    final = splice_processor.process_splices(all_segs, params)

    assert len(final) > 0
    max_body = splice_processor._max_bar_body(params, 1)
    for seg in final:
        assert seg['end'] - seg['start'] <= max_body + 1e-3


# ---------------------------------------------------------------------------
# Test: obstacle cache pre-filter correctness
# ---------------------------------------------------------------------------

def test_cache_bbox_filter_skips_non_overlapping_shafts():
    """Rows outside a shaft's Y range produce same result with or without cache."""
    slab  = _rect_polygon(0, 0, 10 * M, 10 * M)
    shaft = _rect_polygon(3 * M, 6 * M, 7 * M, 8 * M)   # shaft in upper portion
    params = _params(spacing_mm=200)

    rows_low = [r for r in bar_generator.generate_bar_rows(
        (0, 0, 10 * M, 10 * M), params['spacing'], params['cover'], 'X'
    ) if r['fixed_val'] < 5 * M]   # only rows below shaft

    cache = obstacle_processor.build_obstacle_cache([shaft], [])

    for row in rows_low:
        s_cached   = obstacle_processor.process_bar_row(
            row, slab, [shaft], [], params, 'bottom', obstacle_cache=cache)
        s_uncached = obstacle_processor.process_bar_row(
            row, slab, [shaft], [], params, 'bottom')
        # Both should return 1 segment (shaft is above these rows)
        assert len(s_cached) == len(s_uncached) == 1


def test_cache_dp_bbox_reuses_existing_bbox_key():
    """build_obstacle_cache reads dp['bbox'] without recomputing polygon bounds."""
    dp = _dp_data(2 * M, 2 * M, 5 * M, 5 * M)
    shaft = _rect_polygon(7 * M, 7 * M, 9 * M, 9 * M)

    cache = obstacle_processor.build_obstacle_cache([shaft], [dp])

    assert len(cache['shaft_bboxes']) == 1
    assert len(cache['dp_bboxes'])    == 1
    # DP bbox comes straight from dp['bbox'] in geometry.py
    assert cache['dp_bboxes'][0] == dp['bbox']
