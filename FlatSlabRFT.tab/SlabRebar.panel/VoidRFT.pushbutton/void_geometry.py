# -*- coding: utf-8 -*-
"""Void-RFT geometry generation.

Pure Python. No Revit API. Generates segment dicts for edge trimmers
and diagonal dicts for corner diagonals, given a void bounding box and
plugin params.
"""
from __future__ import print_function
import math


FT_PER_MM = 1.0 / 304.8
MM_PER_FT = 304.8

_INV_SQRT2 = 1.0 / math.sqrt(2.0)


def _ft_to_mm(v_ft):
    return v_ft * MM_PER_FT


def _mm_to_ft(v_mm):
    return v_mm * FT_PER_MM


def _point_in_polygon(px, py, polygon):
    """Ray-casting point-in-polygon test. Returns True if (px, py) is inside."""
    n = len(polygon)
    inside = False
    xj, yj = polygon[-1]
    for i in range(n):
        xi, yi = polygon[i]
        if (yi > py) != (yj > py):
            x_cross = (xj - xi) * (py - yi) / float(yj - yi) + xi
            if px < x_cross:
                inside = not inside
        xj, yj = xi, yi
    return inside


def _point_on_segment(px, py, ax, ay, bx, by, tol=1e-9):
    """Return True if point P lies on segment AB (within tolerance)."""
    cross = (px - ax) * (by - ay) - (py - ay) * (bx - ax)
    if abs(cross) > tol:
        return False
    dot = (px - ax) * (bx - ax) + (py - ay) * (by - ay)
    if dot < -tol:
        return False
    sq_len = (bx - ax) * (bx - ax) + (by - ay) * (by - ay)
    if dot - sq_len > tol:
        return False
    return True


def _point_in_or_on_polygon(px, py, polygon):
    """Return True if point is strictly inside polygon OR on its boundary."""
    n = len(polygon)
    for i in range(n):
        ax, ay = polygon[i]
        bx, by = polygon[(i + 1) % n]
        if _point_on_segment(px, py, ax, ay, bx, by):
            return True
    return _point_in_polygon(px, py, polygon)


def _segment_polygon_intersections_t(p1x, p1y, dx, dy, polygon):
    """Return all t-parameters where P(t)=p1+t*d intersects polygon edges."""
    t_hits = []
    n = len(polygon)
    for i in range(n):
        ax, ay = polygon[i]
        bx, by = polygon[(i + 1) % n]
        ex = bx - ax
        ey = by - ay
        denom = dx * ey - dy * ex
        if abs(denom) < 1e-12:
            continue
        fx = ax - p1x
        fy = ay - p1y
        t = (fx * ey - fy * ex) / denom
        u = (fx * dy - fy * dx) / denom
        if -1e-9 <= u <= 1.0 + 1e-9 and -1e-9 <= t <= 1.0 + 1e-9:
            t_hits.append(max(0.0, min(1.0, t)))
    return t_hits


def _clip_bar_to_region(p1_xy, p2_xy, outer_polygon, blocked_polygons=None):
    """Clip a 2D segment to concrete region: inside outer minus blocked holes.

    Returns (clipped_p1, clipped_p2, start_clipped_flag, end_clipped_flag)
    or None if the segment is fully outside or results in zero length.

    Algorithm: parameterise the segment as P(t) = p1 + t*(p2-p1), t in [0,1].
    Find all t-values where it crosses outer or blocked polygon edges.
    Test each sub-interval midpoint against:
      inside outer_polygon AND not inside any blocked polygon.
    Merge adjacent valid intervals and return the longest one.
    """
    if blocked_polygons is None:
        blocked_polygons = []

    p1x, p1y = p1_xy
    p2x, p2y = p2_xy
    dx = p2x - p1x
    dy = p2y - p1y
    if math.sqrt(dx * dx + dy * dy) < 1e-9:
        return None

    t_hits = []
    t_hits.extend(_segment_polygon_intersections_t(p1x, p1y, dx, dy, outer_polygon))
    for hole in blocked_polygons:
        if hole:
            t_hits.extend(_segment_polygon_intersections_t(p1x, p1y, dx, dy, hole))

    # Build breakpoints and test each sub-interval's midpoint.
    all_t = sorted(set([0.0, 1.0] + t_hits))
    inside_intervals = []
    for i in range(len(all_t) - 1):
        mid_t = (all_t[i] + all_t[i + 1]) * 0.5
        mx = p1x + mid_t * dx
        my = p1y + mid_t * dy
        in_outer = _point_in_or_on_polygon(mx, my, outer_polygon)
        in_hole = False
        if in_outer and blocked_polygons:
            for hole in blocked_polygons:
                if hole and _point_in_or_on_polygon(mx, my, hole):
                    in_hole = True
                    break
        if in_outer and not in_hole:
            inside_intervals.append((all_t[i], all_t[i + 1]))

    if not inside_intervals:
        return None

    # Merge adjacent intervals, then take the longest contiguous run.
    merged = [[inside_intervals[0][0], inside_intervals[0][1]]]
    for ta, tb in inside_intervals[1:]:
        if abs(ta - merged[-1][1]) < 1e-9:
            merged[-1][1] = tb
        else:
            merged.append([ta, tb])

    best = max(merged, key=lambda seg: seg[1] - seg[0])
    t_min, t_max = best[0], best[1]

    start_clipped = t_min > 1e-6
    end_clipped = t_max < 1.0 - 1e-6

    clp1 = (p1x + t_min * dx, p1y + t_min * dy)
    clp2 = (p1x + t_max * dx, p1y + t_max * dy)
    return clp1, clp2, start_clipped, end_clipped


def _clip_bar_to_slab(p1_xy, p2_xy, slab_polygon):
    """Back-compat wrapper: clip against slab polygon with no internal holes."""
    return _clip_bar_to_region(p1_xy, p2_xy, slab_polygon, [])


def _make_trimmer_seg(direction, start, end, fixed_val, z, mesh_layer,
                      edge_name,
                      start_hook, end_hook, index, band, params):
    """Assemble one trimmer segment dict matching the rebar_placer schema."""
    return {
        'direction':   direction,
        'start':       start,
        'end':         end,
        'fixed_val':   fixed_val,
        'z':           z,
        # Void additional reinforcement is always straight.  Clip flags only
        # trim the extents; they must not become vertical hook legs.
        'start_hook':  False,
        'end_hook':    False,
        'hook_at_max': True,
        'mesh_layer':  mesh_layer,
        'spacing_ft':  params['trimmer_spacing_ft'],
        'diam_mm':     int(band['edge_bar_dia_mm']),
        'is_add_rft':  True,
        'leg_ft':      0.0,
        'has_hook':    False,
        'index':       index,
        'edge':        edge_name,
        'band_name':   band['name'],
        'source':      'void_trimmer',
    }


def generate_trimmer_segments(void_bbox, params, schedule_lookup):
    """Generate edge-trimmer segments for all 4 void edges.

    void_bbox: (x_min, y_min, x_max, y_max) in feet (Revit model units).

    Ld is computed per edge as  ld_multiplier * edge_bar_dia_mm  (in mm),
    then converted to feet. Each edge may have a different Ld depending on
    which band applies.

    Returns a flat list of trimmer segment dicts — top AND bottom layer for
    each surviving bar.
    """
    x_min, y_min, x_max, y_max = void_bbox
    ld_mult   = params.get('ld_multiplier', 57.0)
    inner     = params['inner_offset_ft']
    spacing   = params['trimmer_spacing_ft']
    top_z     = params['slab_top_z']
    bot_z     = params['slab_bottom_z']
    cover     = params['cover']
    min_len   = params['min_trimmer_length_ft']
    slab_poly = params['slab_polygon']
    hole_polys = params.get('void_holes', [])

    void_width_mm  = _ft_to_mm(x_max - x_min)
    void_height_mm = _ft_to_mm(y_max - y_min)

    # (edge_name, bar_direction, fixed_base, offset_sign, perp_dim_mm)
    # N/S edges run in X; their perpendicular dimension is the void HEIGHT (Y-extent).
    # E/W edges run in Y; their perpendicular dimension is the void WIDTH (X-extent).
    # bar_start and bar_end are computed inside the loop after the band is known,
    # because Ld depends on the band's bar diameter.
    edges = [
        ('N', 'X', y_max, +1, void_height_mm),
        ('S', 'X', y_min, -1, void_height_mm),
        ('E', 'Y', x_max, +1, void_width_mm),
        ('W', 'Y', x_min, -1, void_width_mm),
    ]

    result = []
    for edge_name, direction, fixed_base, sign, perp_mm in edges:
        band   = schedule_lookup(perp_mm)
        n_bars = band['edge_bar_count']
        if n_bars == 0:
            continue

        # Ld for this edge = multiplier × bar diameter, converted to feet.
        ld_edge = ld_mult * band['edge_bar_dia_mm'] * FT_PER_MM
        if direction == 'X':
            bar_s, bar_e = x_min - ld_edge, x_max + ld_edge
        else:
            bar_s, bar_e = y_min - ld_edge, y_max + ld_edge

        for i in range(n_bars):
            fixed_val = fixed_base + sign * (inner + i * spacing)

            if direction == 'X':
                p1_xy = (bar_s, fixed_val)
                p2_xy = (bar_e, fixed_val)
            else:
                p1_xy = (fixed_val, bar_s)
                p2_xy = (fixed_val, bar_e)

            clipped = _clip_bar_to_region(p1_xy, p2_xy, slab_poly, hole_polys)
            if clipped is None:
                print('[void_geometry] {} edge bar {} clipped to zero — skipping'.format(
                    edge_name, i))
                continue

            c1, c2, start_clip, end_clip = clipped
            s_val = c1[0] if direction == 'X' else c1[1]
            e_val = c2[0] if direction == 'X' else c2[1]

            bar_len = abs(e_val - s_val)
            if bar_len < min_len:
                print('[void_geometry] {} edge bar {} too short '
                      '({:.3f}ft < {:.3f}ft min) — skipping'.format(
                          edge_name, i, bar_len, min_len))
                continue

            for mesh_layer, z in (('top', top_z - cover), ('bottom', bot_z + cover)):
                result.append(_make_trimmer_seg(
                    direction=direction,
                    start=s_val,
                    end=e_val,
                    fixed_val=fixed_val,
                    z=z,
                    mesh_layer=mesh_layer,
                    edge_name=edge_name,
                    start_hook=start_clip,
                    end_hook=end_clip,
                    index=i,
                    band=band,
                    params=params,
                ))

    return result


def generate_diagonal_segments(void_bbox, params, schedule_lookup):
    """Generate corner-diagonal segments for all 4 void corners.

    Rules implemented:
    - Diagonal bar length is exactly 2*Ld.
    - Baseline diagonal is closer to the void corner (midpoint at
      corner_offset_factor*Ld on outward corner bisector), then subsequent
      bars shift outward by spacing.
    - Bars are clipped to concrete only: inside slab outer polygon and
      outside all void polygons.
    """
    x_min, y_min, x_max, y_max = void_bbox
    ld_mult = params.get('ld_multiplier', 57.0)
    corner_offset_factor = params.get('diagonal_corner_offset_factor', 0.5)
    spacing = params['trimmer_spacing_ft']
    top_z = params['slab_top_z']
    bot_z = params['slab_bottom_z']
    cover = params['cover']
    min_len = params['min_trimmer_length_ft']
    slab_poly = params.get('slab_polygon')
    hole_polys = params.get('void_holes', [])

    void_width_mm = _ft_to_mm(x_max - x_min)
    void_height_mm = _ft_to_mm(y_max - y_min)

    band_ns = schedule_lookup(void_height_mm)
    band_ew = schedule_lookup(void_width_mm)
    corner_band = band_ns if band_ns['edge_bar_count'] >= band_ew['edge_bar_count'] else band_ew

    n_diags = corner_band['diagonal_count']
    if n_diags == 0:
        return []

    dia_mm = corner_band['diagonal_bar_dia_mm']
    ld_diag = ld_mult * dia_mm * FT_PER_MM
    # Keep offset non-negative to avoid flipping baseline inside the void.
    base_corner_offset = max(0.0, corner_offset_factor * ld_diag)

    corners = [
        ('NE', x_max, y_max, 1, 1),
        ('NW', x_min, y_max, -1, 1),
        ('SE', x_max, y_min, 1, -1),
        ('SW', x_min, y_min, -1, -1),
    ]

    result = []
    for corner_name, cx, cy, sx, sy in corners:
        # Outward corner-bisector and perpendicular in-plane bar direction.
        bx = sx * _INV_SQRT2
        by = sy * _INV_SQRT2
        px = -sx * _INV_SQRT2
        py = sy * _INV_SQRT2

        for i in range(n_diags):
            # Midpoint starts closer to corner, then shifts out by spacing.
            mid_x = cx + (base_corner_offset + i * spacing) * bx
            mid_y = cy + (base_corner_offset + i * spacing) * by

            # Total diagonal length = 2*Ld.
            x_start = mid_x - ld_diag * px
            y_start = mid_y - ld_diag * py
            x_end = mid_x + ld_diag * px
            y_end = mid_y + ld_diag * py

            if slab_poly is not None:
                clipped = _clip_bar_to_region(
                    (x_start, y_start), (x_end, y_end), slab_poly, hole_polys)
                if clipped is None:
                    print('[void_geometry] diagonal {} corner {} clipped to zero - skipping'.format(
                        i, corner_name))
                    continue
                (x_start, y_start), (x_end, y_end), _, _ = clipped
                bar_len = math.sqrt((x_end - x_start) ** 2 + (y_end - y_start) ** 2)
                if bar_len < min_len:
                    print('[void_geometry] diagonal {} corner {} too short ({:.3f}ft) - skipping'.format(
                        i, corner_name, bar_len))
                    continue

            for layer, z in (('top', top_z - cover), ('bottom', bot_z + cover)):
                result.append({
                    'p1': (x_start, y_start, z),
                    'p2': (x_end, y_end, z),
                    'diam_mm': int(dia_mm),
                    'band_name': corner_band['name'],
                    'corner': corner_name,
                    'layer': layer,
                    'source': 'void_diagonal',
                })

    return result

def _self_test():
    """Five test configurations. Prints counts, asserts expected values.

    Returns failure count (0 = all pass).
    """
    from void_schedule import lookup_band

    cover   = _mm_to_ft(40.0)
    spacing = _mm_to_ft(100.0)
    inner   = _mm_to_ft(50.0)
    min_len = _mm_to_ft(300.0)

    params_big = {
        'cover':                 cover,
        'ld_multiplier':         57.0,   # Ld = 57 x bar_dia_mm
        'trimmer_spacing_ft':    spacing,
        'inner_offset_ft':       inner,
        'min_trimmer_length_ft': min_len,
        'slab_top_z':            10.0,
        'slab_bottom_z':          9.0,
        'slab_polygon':          [(0, 0), (100, 0), (100, 100), (0, 100)],
    }

    params_small = dict(params_big)
    params_small['slab_polygon'] = [(0, 0), (5, 0), (5, 5), (0, 5)]

    failures = [0]

    def _check(label, got, expected):
        ok = (got == expected)
        if not ok:
            failures[0] += 1
        print('  [{}] {}: got={} expected={}'.format(
            'PASS' if ok else 'FAIL', label, got, expected))

    def _check_no_hooks(label, segs):
        hooked = [s for s in segs if s.get('start_hook') or s.get('end_hook')]
        _check(label, len(hooked), 0)

    def _check_diams(label, items, key_name, expected):
        got = sorted(set([int(item[key_name]) for item in items]))
        _check(label, got, expected)

    cx, cy = 50.0, 50.0

    # ------------------------------------------------------------------
    # Case 1: 400x400mm — Band 1 on all edges, no diagonals
    # 4 edges × 2 bars × 2 layers = 16 trimmers; diagonal_count=0 → 0 diags
    # ------------------------------------------------------------------
    half1 = _mm_to_ft(200.0)
    bbox1 = (cx - half1, cy - half1, cx + half1, cy + half1)
    segs1  = generate_trimmer_segments(bbox1, params_big, lookup_band)
    diags1 = generate_diagonal_segments(bbox1, params_big, lookup_band)
    print('Case 1 (400x400mm, Medium 2T16): trimmers={} diagonals={}'.format(
        len(segs1), len(diags1)))
    _check('trimmers', len(segs1), 16)
    _check('diagonals', len(diags1), 0)
    _check_diams('trimmer diameters', segs1, 'diam_mm', [16])
    _check_no_hooks('trimmer hooks', segs1)

    # ------------------------------------------------------------------
    # Case 2: 800x800mm — Band 2 on all edges, 2 diagonals/corner
    # 4 edges × 3 bars × 2 layers = 24 trimmers
    # 4 corners × 2 diags × 2 layers = 16 diagonals
    # ------------------------------------------------------------------
    half2 = _mm_to_ft(400.0)
    bbox2 = (cx - half2, cy - half2, cx + half2, cy + half2)
    segs2  = generate_trimmer_segments(bbox2, params_big, lookup_band)
    diags2 = generate_diagonal_segments(bbox2, params_big, lookup_band)
    print('Case 2 (800x800mm, Large 3T16): trimmers={} diagonals={}'.format(
        len(segs2), len(diags2)))
    _check('trimmers', len(segs2), 24)
    _check('diagonals', len(diags2), 16)
    _check_diams('trimmer diameters', segs2, 'diam_mm', [16])
    _check_diams('diagonal diameters', diags2, 'diam_mm', [16])
    _check_no_hooks('trimmer hooks', segs2)

    # ------------------------------------------------------------------
    # Case 3: 1500x1500mm — Band 3 on all edges, 3 diagonals/corner
    # 4 edges × 4 bars × 2 layers = 32 trimmers
    # 4 corners × 3 diags × 2 layers = 24 diagonals
    # ------------------------------------------------------------------
    half3 = _mm_to_ft(750.0)
    bbox3 = (cx - half3, cy - half3, cx + half3, cy + half3)
    segs3  = generate_trimmer_segments(bbox3, params_big, lookup_band)
    diags3 = generate_diagonal_segments(bbox3, params_big, lookup_band)
    print('Case 3 (1500x1500mm, Large 4T18): trimmers={} diagonals={}'.format(
        len(segs3), len(diags3)))
    _check('trimmers', len(segs3), 32)
    _check('diagonals', len(diags3), 24)
    _check_diams('trimmer diameters', segs3, 'diam_mm', [18])
    _check_diams('diagonal diameters', diags3, 'diam_mm', [16])
    _check_no_hooks('trimmer hooks', segs3)

    # ------------------------------------------------------------------
    # Case 4: 400x1000mm asymmetric
    # X-dim=400mm → Band1 (E/W edges); Y-dim=1000mm → Band2 (N/S edges)
    # Trimmers: N+S = 2×3×2=12, E+W = 2×2×2=8 → total 20
    # Corners: max(Band2, Band1) = Band2 → 2 diags/corner → 4×2×2=16
    # ------------------------------------------------------------------
    hw_x = _mm_to_ft(200.0)
    hw_y = _mm_to_ft(500.0)
    bbox4 = (cx - hw_x, cy - hw_y, cx + hw_x, cy + hw_y)
    segs4  = generate_trimmer_segments(bbox4, params_big, lookup_band)
    diags4 = generate_diagonal_segments(bbox4, params_big, lookup_band)
    print('Case 4 (400x1000mm, asymmetric): trimmers={} diagonals={}'.format(
        len(segs4), len(diags4)))

    void_w_mm = _ft_to_mm(bbox4[2] - bbox4[0])
    void_h_mm = _ft_to_mm(bbox4[3] - bbox4[1])
    ns_segs = [s for s in segs4 if s['direction'] == 'X']
    ew_segs = [s for s in segs4 if s['direction'] == 'Y']
    print('  N/S edges (perp=Y-dim={:.0f}mm -> {}): {} segs'.format(
        void_h_mm, lookup_band(void_h_mm)['name'], len(ns_segs)))
    print('  E/W edges (perp=X-dim={:.0f}mm -> {}): {} segs'.format(
        void_w_mm, lookup_band(void_w_mm)['name'], len(ew_segs)))

    _check('trimmers', len(segs4), 20)
    _check('diagonals', len(diags4), 16)
    _check_diams('trimmer diameters', segs4, 'diam_mm', [16])
    _check_diams('diagonal diameters', diags4, 'diam_mm', [16])
    _check_no_hooks('trimmer hooks', segs4)

    half4b = _mm_to_ft(800.0)
    bbox4b = (cx - half4b, cy - half4b, cx + half4b, cy + half4b)
    segs4b  = generate_trimmer_segments(bbox4b, params_big, lookup_band)
    diags4b = generate_diagonal_segments(bbox4b, params_big, lookup_band)
    print('Case 4b (1600x1600mm, Large 5T18): trimmers={} diagonals={}'.format(
        len(segs4b), len(diags4b)))
    _check('trimmers', len(segs4b), 40)
    _check('diagonals', len(diags4b), 32)
    _check_diams('trimmer diameters', segs4b, 'diam_mm', [18])
    _check_diams('diagonal diameters', diags4b, 'diam_mm', [16])
    _check_no_hooks('trimmer hooks', segs4b)

    # ------------------------------------------------------------------
    # Case 5: 400x400mm near SW corner of a 5ft slab
    # S and W bars fall outside slab → skipped.
    # N edge: 2 bars × 2 layers = 4 segs; E edge: 2 bars × 2 layers = 4 segs
    # Total: 8 trimmers; Band1 → 0 diagonals
    # ------------------------------------------------------------------
    void_ft = _mm_to_ft(400.0)
    bbox5 = (0.1, 0.1, 0.1 + void_ft, 0.1 + void_ft)
    print('Case 5 (near-edge 400x400mm, 5ft slab): clipping warnings below:')
    segs5  = generate_trimmer_segments(bbox5, params_small, lookup_band)
    diags5 = generate_diagonal_segments(bbox5, params_small, lookup_band)
    print('Case 5 result: trimmers={} diagonals={}'.format(
        len(segs5), len(diags5)))
    print('  (vs unclipped: 16 trimmers; {} clipped/skipped)'.format(
        16 - len(segs5)))
    _check('trimmers >= 4', len(segs5) >= 4, True)
    _check('trimmers', len(segs5), 8)
    _check('diagonals', len(diags5), 0)
    _check_diams('trimmer diameters', segs5, 'diam_mm', [16])
    _check_no_hooks('trimmer hooks after clipping', segs5)

    print('')
    print('Self-test: {} failure(s)'.format(failures[0]))
    return failures[0]


if __name__ == '__main__':
    import sys
    rc = _self_test()
    sys.exit(rc)
