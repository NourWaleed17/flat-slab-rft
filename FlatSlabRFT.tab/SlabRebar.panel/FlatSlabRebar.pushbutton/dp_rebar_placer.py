# -*- coding: utf-8 -*-
"""Place staple-shaped drop panel rebar in Revit."""
from __future__ import print_function

import clr
clr.AddReference('System')
from System.Collections.Generic import List

from Autodesk.Revit.DB import Line, XYZ, Curve, Transaction
from Autodesk.Revit.DB.Structure import Rebar, RebarStyle, RebarHookOrientation


# ---------------------------------------------------------------------------
# Row generation
# ---------------------------------------------------------------------------

def generate_dp_bar_rows(dp_data, spacing, cover, direction):
    """Sweep bar positions across the drop panel bbox.

    Returns list of dicts: {'pos': float, 'direction': str, 'index': int}
    """
    min_x, min_y, max_x, max_y = dp_data['bbox']
    rows = []

    if direction == 'X':
        y = min_y + cover
        i = 0
        while y <= max_y - cover + 1e-9:
            rows.append({'pos': y, 'direction': 'X', 'index': i})
            y += spacing
            i += 1
    else:
        x = min_x + cover
        i = 0
        while x <= max_x - cover + 1e-9:
            rows.append({'pos': x, 'direction': 'Y', 'index': i})
            x += spacing
            i += 1

    return rows


# ---------------------------------------------------------------------------
# Single staple bar placement
# ---------------------------------------------------------------------------

def place_dp_bar(doc, dp_floor, row_pos, dp_data, direction, bar_type, params, base_z):
    """Place one staple-shaped DP bar lying in a vertical plane.

    The bar is placed from drop-panel bottom cover upward.
    End horizontal legs extend outward (toward main slab region).

    Curve chain (5 lines):
        hook_tip_left → left_top → left_bot → right_bot → right_top → hook_tip_right
    """
    v_leg  = params['dp_vertical_leg']
    h_leg  = params['dp_horizontal_leg']
    side_cover = params['cover']

    min_x, min_y, max_x, max_y = dp_data['bbox']
    dp_top_z = dp_data.get('top_z')
    dp_bottom_z = dp_data.get('bottom_z')

    # z_top/z_bot are both inside the drop-panel thickness.
    if dp_top_z is None:
        dp_top_z = base_z + v_leg
    if dp_bottom_z is None:
        dp_bottom_z = dp_top_z - max(dp_data.get('thickness', 0.0), 0.0)

    # Enforce top/bottom cover and allow optional upward shift (for Y layer).
    clear_bot = dp_bottom_z + params['cover']
    clear_top = dp_top_z - params['cover']
    if clear_top <= clear_bot:
        return None

    desired_shift = base_z - clear_bot
    if desired_shift < 0:
        desired_shift = 0.0
    max_shift = max(0.0, clear_top - clear_bot - 1e-6)
    z_bot = clear_bot + min(desired_shift, max_shift)
    z_top = clear_top

    if direction == 'X':
        # Bar spans from dp_left to dp_right at constant Y = row_pos
        y = row_pos
        x_left = min_x + side_cover
        x_right = max_x - side_cover
        if x_right <= x_left:
            return None
        # Horizontal legs point outward from the drop panel.
        pt_hook_tip_left = XYZ(x_left - h_leg, y, z_top)
        pt_left_top      = XYZ(x_left,         y, z_top)
        pt_left_bot      = XYZ(x_left,         y, z_bot)
        pt_right_bot     = XYZ(x_right,        y, z_bot)
        pt_right_top     = XYZ(x_right,        y, z_top)
        pt_hook_tip_right= XYZ(x_right + h_leg, y, z_top)
        # Normal = Y-axis (bar lies in XZ plane at fixed Y)
        normal = XYZ(0, 1, 0)

    else:  # direction == 'Y'
        # Bar spans from dp_bot_y to dp_top_y at constant X = row_pos
        x = row_pos
        y_bot = min_y + side_cover
        y_top = max_y - side_cover
        if y_top <= y_bot:
            return None
        pt_hook_tip_left = XYZ(x, y_bot - h_leg, z_top)
        pt_left_top      = XYZ(x, y_bot,         z_top)
        pt_left_bot      = XYZ(x, y_bot,         z_bot)
        pt_right_bot     = XYZ(x, y_top,         z_bot)
        pt_right_top     = XYZ(x, y_top,         z_top)
        pt_hook_tip_right= XYZ(x, y_top + h_leg, z_top)
        # Normal = X-axis (bar lies in YZ plane at fixed X)
        normal = XYZ(1, 0, 0)

    try:
        curves = List[Curve]()
        curves.Add(Line.CreateBound(pt_hook_tip_left,  pt_left_top))
        curves.Add(Line.CreateBound(pt_left_top,       pt_left_bot))
        curves.Add(Line.CreateBound(pt_left_bot,       pt_right_bot))
        curves.Add(Line.CreateBound(pt_right_bot,      pt_right_top))
        curves.Add(Line.CreateBound(pt_right_top,      pt_hook_tip_right))

        rebar = Rebar.CreateFromCurves(
            doc,
            RebarStyle.Standard,
            bar_type,
            None,   # hooks are built into the geometry itself
            None,
            dp_floor,
            normal,
            curves,
            RebarHookOrientation.Left,
            RebarHookOrientation.Right,
            True,
            True,
        )
        return rebar
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _place_dp_direction_as_set(doc, dp_floor, dp_data, direction, bar_type, params, base_z):
    """Place one direction as one shape-driven set when possible."""
    spacing = params['spacing']
    cover = params['cover']
    rows = generate_dp_bar_rows(dp_data, spacing, cover, direction)
    if not rows:
        return 0

    base_rebar = place_dp_bar(
        doc, dp_floor, rows[0]['pos'], dp_data, direction, bar_type, params, base_z
    )
    if base_rebar is None:
        return 0

    if len(rows) == 1:
        return 1

    try:
        accessor = base_rebar.GetShapeDrivenAccessor()
        accessor.SetLayoutAsNumberWithSpacing(len(rows), spacing, True, True, True)
        return len(rows)
    except Exception:
        try:
            accessor = base_rebar.GetShapeDrivenAccessor()
            accessor.SetLayoutAsMaximumSpacing(
                spacing, spacing * (len(rows) - 1), True, True, True
            )
            return len(rows)
        except Exception:
            # Fallback: place remaining rows as singles.
            placed = 1
            for row in rows[1:]:
                rb = place_dp_bar(
                    doc, dp_floor, row['pos'], dp_data, direction, bar_type, params, base_z
                )
                if rb is not None:
                    placed += 1
            return placed

def place_all_dp_bars(doc, dp_data_list, params):
    """Place all drop panel staple bars in a single Revit transaction.

    For each drop panel:
        - X-direction bars placed at base_z (lower layer)
        - Y-direction bars placed at base_z + diameter (upper layer)
    """
    bar_type  = params['bar_type']
    spacing   = params['spacing']
    cover     = params['cover']
    diameter  = params['diameter']
    t = Transaction(doc, 'Place Drop Panel Rebar')
    t.Start()
    try:
        for dp_data in dp_data_list:
            dp_floor = dp_data.get('floor')
            if dp_floor is None:
                continue

            dp_bottom_z = dp_data.get('bottom_z')
            if dp_bottom_z is None:
                dp_top_z = dp_data.get('top_z', 0.0)
                dp_bottom_z = dp_top_z - max(dp_data.get('thickness', 0.0), 0.0)

            # Place from DP bottom cover (not main slab cover).
            base_z   = dp_bottom_z + cover
            # Raise Y layer a bit more to reduce clashes with X set.
            base_z_y = base_z + (1.5 * diameter)

            # One set for each direction per drop panel.
            _place_dp_direction_as_set(
                doc, dp_floor, dp_data, 'X', bar_type, params, base_z
            )
            _place_dp_direction_as_set(
                doc, dp_floor, dp_data, 'Y', bar_type, params, base_z_y
            )

        t.Commit()
    except Exception:
        t.RollBack()
        raise
