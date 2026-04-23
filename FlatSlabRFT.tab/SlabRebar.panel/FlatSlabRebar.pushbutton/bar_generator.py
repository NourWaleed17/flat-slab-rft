# -*- coding: utf-8 -*-
"""Bar row generation for flat slab rebar placement."""
from __future__ import print_function


def generate_bar_rows(bbox, spacing, cover, direction):
    """Generate equally spaced bar rows across the slab bounding box.

    For direction='X': rows have fixed Y, bars run in X direction.
    For direction='Y': rows have fixed X, bars run in Y direction.

    Each row dict:
        {
            'fixed_val': float,   # constant coordinate (Y for X-bars, X for Y-bars)
            'vary_min':  float,   # bar extent start
            'vary_max':  float,   # bar extent end
            'direction': 'X'/'Y',
            'index':     int,     # row number (used for stagger in splice logic)
        }
    """
    min_x, min_y, max_x, max_y = bbox
    rows = []

    if direction == 'X':
        vary_min = min_x
        vary_max = max_x
        y = min_y + cover
        i = 0
        while y <= max_y - cover + 1e-9:
            rows.append({
                'fixed_val': y,
                'vary_min':  vary_min,
                'vary_max':  vary_max,
                'direction': 'X',
                'index':     i,
            })
            y += spacing
            i += 1
    else:
        vary_min = min_y
        vary_max = max_y
        x = min_x + cover
        i = 0
        while x <= max_x - cover + 1e-9:
            rows.append({
                'fixed_val': x,
                'vary_min':  vary_min,
                'vary_max':  vary_max,
                'direction': 'Y',
                'index':     i,
            })
            x += spacing
            i += 1

    return rows
