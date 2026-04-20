# -*- coding: utf-8 -*-
"""Tests for dp_rebar_placer pure-Python grouping helpers."""
from __future__ import print_function
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# dp_rebar_placer imports Revit DLLs at module level; we patch the imports out.
import types

# --- stub Revit modules so dp_rebar_placer can be imported without Revit ---
for mod_name in ('clr', 'System', 'System.Collections.Generic',
                 'Autodesk', 'Autodesk.Revit', 'Autodesk.Revit.DB',
                 'Autodesk.Revit.DB.Structure'):
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

# Provide stub symbols used at import time
_sys = sys.modules['System']
_clr = sys.modules['clr']
_clr.AddReference = lambda *a, **kw: None

_sys_coll = sys.modules['System.Collections.Generic']

class _ListStub:
    def __init__(self, *a, **kw): pass
_sys_coll.List = _ListStub

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

_str = sys.modules['Autodesk.Revit.DB.Structure']
_str.Rebar = None
_str.RebarStyle = None
_str.RebarHookOrientation = None

# geometry is imported by dp_rebar_placer; only stub if not already imported.
import importlib, types as _t
if 'geometry' not in sys.modules:
    _geom_stub = _t.ModuleType('geometry')
    _geom_stub.get_obstacle_intervals = lambda *a, **kw: []
    sys.modules['geometry'] = _geom_stub

from dp_rebar_placer import _intervals_match, _group_rows_by_intervals


# ---------------------------------------------------------------------------
# _intervals_match
# ---------------------------------------------------------------------------

def test_intervals_match_identical():
    ivs = [(0.0, 5.0), (7.0, 12.0)]
    assert _intervals_match(ivs, ivs) is True


def test_intervals_match_within_tolerance():
    ivs1 = [(0.0, 5.0)]
    ivs2 = [(0.005, 5.005)]   # diff < 0.01
    assert _intervals_match(ivs1, ivs2) is True


def test_intervals_match_different_count():
    ivs1 = [(0.0, 5.0)]
    ivs2 = [(0.0, 5.0), (7.0, 10.0)]
    assert _intervals_match(ivs1, ivs2) is False


def test_intervals_match_shaft_flag_mismatch():
    ivs1 = [(0.0, 5.0, True, False)]
    ivs2 = [(0.0, 5.0, False, False)]
    assert _intervals_match(ivs1, ivs2) is False


# ---------------------------------------------------------------------------
# _group_rows_by_intervals
# ---------------------------------------------------------------------------

def test_group_rows_by_intervals_single_group():
    ivs = [(0.0, 10.0)]
    rows_and_ivs = [(float(i), ivs) for i in range(4)]
    groups = _group_rows_by_intervals(rows_and_ivs)
    assert len(groups) == 1
    assert len(groups[0][0]) == 4


def test_group_rows_by_intervals_split_on_change():
    ivs_a = [(0.0, 10.0)]
    ivs_b = [(0.0, 8.0)]
    rows_and_ivs = [
        (0.0, ivs_a),
        (1.0, ivs_a),
        (2.0, ivs_b),
        (3.0, ivs_b),
    ]
    groups = _group_rows_by_intervals(rows_and_ivs)
    assert len(groups) == 2


def test_group_rows_by_intervals_max_gap():
    """Gap > max_gap splits groups even if intervals are identical."""
    ivs = [(0.0, 10.0)]
    rows_and_ivs = [
        (0.0, ivs),
        (1.0, ivs),
        (5.0, ivs),   # gap = 4, max_gap = 1.5 → new group
        (6.0, ivs),
    ]
    groups = _group_rows_by_intervals(rows_and_ivs, max_gap=1.5)
    assert len(groups) == 2


def test_group_rows_by_intervals_shaft_split():
    """Different shaft flags force a new group."""
    ivs_no_shaft  = [(0.0, 10.0, False, False)]
    ivs_with_shaft = [(0.0, 10.0, True, False)]
    rows_and_ivs = [
        (0.0, ivs_no_shaft),
        (1.0, ivs_no_shaft),
        (2.0, ivs_with_shaft),
        (3.0, ivs_with_shaft),
    ]
    groups = _group_rows_by_intervals(rows_and_ivs)
    assert len(groups) == 2
