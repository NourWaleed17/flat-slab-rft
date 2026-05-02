# -*- coding: utf-8 -*-
"""Void-RFT band schedule and lookup.

Pure Python. No Revit API dependencies. Testable in standalone
IronPython or CPython 2.

The schedule maps opening dimensions to the approved slab opening
reinforcement table. This implementation applies the table minimums only;
equivalent cut-steel area checks are documented as a future enhancement.
"""
from __future__ import print_function


SCHEDULE_VERSION = 'v2-slab-opening-standard'

# Dimensions in millimeters. min_mm is exclusive for every band after IGNORE;
# max_mm is inclusive. This matches:
#   d <= 150          -> no additional reinforcement
#   150 < d <= 450    -> 2T16 edge bars, no diagonals
#   450 < d <= 1000   -> 3T16 edge bars, 2T16 diagonals
#   1000 < d <= 1500  -> 4T18 edge bars, 3T16 diagonals
#   d > 1500          -> 5T18 edge bars, 4T16 diagonals
#
# Each band carries:
#   edge_bar_count: how many trimmer bars per edge
#   edge_bar_dia_mm: diameter of each edge trimmer bar
#   diagonal_count: how many diagonal bars per corner (0 if none)
#   diagonal_bar_dia_mm: diameter of each diagonal bar (ignored if count=0)

SCHEDULE = [
    {
        'name': 'IGNORE',
        'min_mm': 0,
        'max_mm': 150,
        'edge_bar_count': 0,
        'edge_bar_dia_mm': 0,
        'diagonal_count': 0,
        'diagonal_bar_dia_mm': 0,
    },
    {
        'name': 'MEDIUM_2T16',
        'min_mm': 150,
        'max_mm': 450,
        'edge_bar_count': 2,
        'edge_bar_dia_mm': 16,
        'diagonal_count': 0,
        'diagonal_bar_dia_mm': 0,
    },
    {
        'name': 'LARGE_3T16',
        'min_mm': 450,
        'max_mm': 1000,
        'edge_bar_count': 3,
        'edge_bar_dia_mm': 16,
        'diagonal_count': 2,
        'diagonal_bar_dia_mm': 16,
    },
    {
        'name': 'LARGE_4T18',
        'min_mm': 1000,
        'max_mm': 1500,
        'edge_bar_count': 4,
        'edge_bar_dia_mm': 18,
        'diagonal_count': 3,
        'diagonal_bar_dia_mm': 16,
    },
    {
        'name': 'LARGE_5T18',
        'min_mm': 1500,
        'max_mm': 1000000,   # effectively no upper bound
        'edge_bar_count': 5,
        'edge_bar_dia_mm': 18,
        'diagonal_count': 4,
        'diagonal_bar_dia_mm': 16,
    },
]


def lookup_band(dim_mm):
    """Return the band dict for the given opening dimension in mm.

    For dim_mm <= 150 returns the IGNORE band (edge_bar_count=0).
    Negative dimensions or non-numeric inputs return the IGNORE band as well.
    """
    try:
        dim = float(dim_mm)
    except (TypeError, ValueError):
        print('[void_schedule] WARNING: non-numeric dim_mm={!r}; returning IGNORE'.format(dim_mm))
        return SCHEDULE[0]

    if dim < 0:
        print('[void_schedule] WARNING: negative dim_mm={}; returning IGNORE'.format(dim))
        return SCHEDULE[0]

    # Dimensions often round-trip through feet, so normalize tiny floating
    # drift before testing exact table boundaries such as 1000 and 1500 mm.
    dim = round(dim, 6)

    for idx, band in enumerate(SCHEDULE):
        if idx == 0:
            if band['min_mm'] <= dim <= band['max_mm']:
                return band
        elif band['min_mm'] < dim <= band['max_mm']:
            return band

    print('[void_schedule] WARNING: dim_mm={} matched no band; returning IGNORE'.format(dim))
    return SCHEDULE[0]


def describe_band(band):
    """Return a human-readable one-line description of a band rule."""
    if band['edge_bar_count'] == 0:
        return '{}: no rebar'.format(band['name'])

    base = '{}: edge = {}xPhi{}mm'.format(
        band['name'],
        band['edge_bar_count'],
        band['edge_bar_dia_mm'],
    )
    if band['diagonal_count'] > 0:
        base += ', diagonals = {}xPhi{}mm per corner'.format(
            band['diagonal_count'],
            band['diagonal_bar_dia_mm'],
        )
    else:
        base += ', no diagonals'
    return base


def _self_test():
    """Run hardcoded dimension probes and print expected vs actual."""
    cases = [
        # (input_mm, expected_band_name, edge_count, edge_dia, diag_count, diag_dia)
        (0,       'IGNORE',      0,  0, 0,  0),
        (100,     'IGNORE',      0,  0, 0,  0),
        (150,     'IGNORE',      0,  0, 0,  0),
        (151,     'MEDIUM_2T16', 2, 16, 0,  0),
        (450,     'MEDIUM_2T16', 2, 16, 0,  0),
        (451,     'LARGE_3T16',  3, 16, 2, 16),
        (1000,    'LARGE_3T16',  3, 16, 2, 16),
        (1001,    'LARGE_4T18',  4, 18, 3, 16),
        (1500,    'LARGE_4T18',  4, 18, 3, 16),
        (1501,    'LARGE_5T18',  5, 18, 4, 16),
        (2500,    'LARGE_5T18',  5, 18, 4, 16),
        (-50,     'IGNORE',      0,  0, 0,  0),
        ('abc',   'IGNORE',      0,  0, 0,  0),
    ]
    failed = 0
    for dim, name, edge_count, edge_dia, diag_count, diag_dia in cases:
        band = lookup_band(dim)
        got = (
            band['name'],
            band['edge_bar_count'],
            band['edge_bar_dia_mm'],
            band['diagonal_count'],
            band['diagonal_bar_dia_mm'],
        )
        expected = (name, edge_count, edge_dia, diag_count, diag_dia)
        status = 'PASS' if got == expected else 'FAIL'
        if got != expected:
            failed += 1
        print('  [{}] dim={!s:>6} expected={} got={}'.format(
            status, dim, expected, got))

    print('')
    print('Self-test: {} cases, {} failed'.format(len(cases), failed))
    return failed


if __name__ == '__main__':
    print('void_schedule {}'.format(SCHEDULE_VERSION))
    print('')
    print('Schedule contents:')
    for band in SCHEDULE:
        print('  ' + describe_band(band))
    print('')
    print('Self-test probes:')
    _self_test()
