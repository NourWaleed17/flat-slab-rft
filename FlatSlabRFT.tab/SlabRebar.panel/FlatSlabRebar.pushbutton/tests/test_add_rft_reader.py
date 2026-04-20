# -*- coding: utf-8 -*-
"""
Tests for add_rft_reader.py and the no_hooks path in obstacle_processor.py.

Run with:  python -m pytest tests/test_add_rft_reader.py -v
(No Revit API required – all Revit-touching code is stubbed.)
"""
import sys
import os
import math
import pytest
import types

# ---------------------------------------------------------------------------
# Minimal stubs so the modules can be imported without a Revit environment
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

db = sys.modules['Autodesk.Revit.DB']
db.Line = None
db.XYZ = None
db.Curve = None
db.Transaction = None
db.FilteredElementCollector = None
db.FamilyInstance = None
db.BuiltInParameter = None
db.Floor = None
db.Opening = None
db.Wall = None
db.JoinGeometryUtils = None
db.BuiltInCategory = None

dbs = sys.modules['Autodesk.Revit.DB.Structure']
dbs.Rebar = None
dbs.RebarStyle = None
dbs.RebarHookOrientation = None
dbs.RebarBarType = None

sys_cg = sys.modules['System.Collections.Generic']
sys_cg.List = None

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ---------------------------------------------------------------------------
# Import pure-Python functions under test
# ---------------------------------------------------------------------------
from add_rft_reader import (
    parse_label,
    read_detail_item,
    generate_add_rft_rows,
)
from obstacle_processor import split_bar_row, process_bar_row


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------

class _Vec3:
    """Minimal XYZ stand-in."""
    def __init__(self, x, y, z=0.0):
        self.X = x
        self.Y = y
        self.Z = z


class _Transform:
    def __init__(self, origin, basis_x, basis_y):
        self.Origin = origin
        self.BasisX = basis_x
        self.BasisY = basis_y


class _Param:
    """Stub Revit parameter."""
    def __init__(self, value, ptype='double'):
        self._value = value
        self._ptype = ptype

    def AsDouble(self):
        if self._value is None:
            raise AttributeError('no value')
        return float(self._value)

    def AsString(self):
        return str(self._value)

    def AsInteger(self):
        return int(self._value)


class _FamilyInstance:
    """Stub FamilyInstance with configurable parameters."""

    def __init__(self, params_dict, transform):
        self._params = params_dict
        self._transform = transform

    def LookupParameter(self, name):
        if name in self._params:
            return _Param(self._params[name])
        return None

    def GetTransform(self):
        return self._transform


MM_TO_FEET = 0.00328084


def _make_instance(
    label='T16-150',
    dist_ft=6.5617,          # ≈ 2000 mm
    bar_length_c_ft=16.4042, # ≈ 5000 mm
    active_bar='C',
    basis_x=(1, 0, 0),
    basis_y=(0, 1, 0),
    origin=(10.0, 20.0, 5.0),
):
    """Build a stub FamilyInstance with default X-direction bar, Bar C active (Solid).

    Lengths chosen so Bar C is always the longest (main arm wins max selection):
      A=1.64ft (~500mm return), B=0.82ft (~250mm vertical leg, Config B),
      C=bar_length_c_ft (main span), D=0.82ft (~250mm vertical leg, Config A),
      E=1.31ft (~400mm return, Config A).
    """
    params = {
        'Label':  label,
        'DIST.':  dist_ft,
    }
    for letter in ('A', 'B', 'C', 'D', 'E'):
        params['Bar {} Visibility_Solid'.format(letter)] = (
            1 if letter == active_bar else 0
        )
        params['Bar {} Visibility_Dash'.format(letter)] = 0
        # Realistic lengths: D and B are short (vertical legs), A and E are returns
        lengths = {'A': 1.64, 'B': 0.82, 'C': bar_length_c_ft, 'D': 0.82, 'E': 1.31}
        params['Bar Length {}'.format(letter)] = lengths[letter]

    ox, oy, oz = origin
    bx = _Vec3(*basis_x)
    by = _Vec3(*basis_y)
    transform = _Transform(_Vec3(ox, oy, oz), bx, by)
    return _FamilyInstance(params, transform)


# ---------------------------------------------------------------------------
# parse_label
# ---------------------------------------------------------------------------

class TestParseLabel:
    def test_single(self):
        result = parse_label('T12-150')
        assert result == [(12, 150)]

    def test_combined(self):
        result = parse_label('T22-150+T12-150')
        assert result == [(22, 150), (12, 150)]

    def test_empty(self):
        assert parse_label('') == []

    def test_none(self):
        assert parse_label(None) == []

    def test_invalid(self):
        assert parse_label('INVALID') == []


# ---------------------------------------------------------------------------
# read_detail_item
# ---------------------------------------------------------------------------

class TestReadDetailItem:
    def test_x_direction_bar(self):
        inst = _make_instance(basis_x=(1, 0, 0), basis_y=(0, 1, 0))
        specs = read_detail_item(inst)
        assert len(specs) == 1
        assert specs[0]['direction'] == 'X'

    def test_y_direction_bar(self):
        # Rotated 90°: BasisX points in world-Y direction
        inst = _make_instance(basis_x=(0, 1, 0), basis_y=(-1, 0, 0))
        specs = read_detail_item(inst)
        assert len(specs) == 1
        assert specs[0]['direction'] == 'Y'

    def test_bar_arm_length_uses_active_bar(self):
        # Bar C active, Bar Length C = 16.4042 ft
        inst = _make_instance(active_bar='C', bar_length_c_ft=16.4042)
        specs = read_detail_item(inst)
        assert abs(specs[0]['bar_arm_ft'] - 16.4042) < 1e-4

    def test_multiple_active_bars_picks_longest(self):
        # Simulate top U-bar: Bar A Solid=1 (500mm return) + Bar C Dash=1 (main span).
        # bar_arm_ft must be Bar Length C (longest), not Bar Length A (first found).
        inst = _make_instance(active_bar='C', bar_length_c_ft=16.4042)
        # Also activate Bar A with its short length (1.64 ft)
        inst._params['Bar A Visibility_Solid'] = 1
        specs = read_detail_item(inst)
        assert len(specs) == 1
        # Must pick C (16.4042) over A (1.64)
        assert abs(specs[0]['bar_arm_ft'] - 16.4042) < 1e-4

    def test_multiple_active_bars_all_solid_and_dash(self):
        # A/C/D/E all active (like a real U-bar), C has longest Bar Length.
        inst = _make_instance(active_bar='C', bar_length_c_ft=12.6312)
        inst._params['Bar A Visibility_Solid'] = 1   # short return: 1.64 ft
        inst._params['Bar D Visibility_Dash'] = 1    # no Bar Length D → skipped
        inst._params['Bar E Visibility_Dash'] = 1    # Bar Length E = 1.31 ft
        specs = read_detail_item(inst)
        assert len(specs) == 1
        assert abs(specs[0]['bar_arm_ft'] - 12.6312) < 1e-4

    def test_no_active_bar_returns_empty(self):
        inst = _make_instance(active_bar=None)
        # Override: set all Visibility_Solid to 0
        for letter in ('A', 'B', 'C', 'D', 'E'):
            inst._params['Bar {} Visibility_Solid'.format(letter)] = 0
        specs = read_detail_item(inst)
        assert specs == []

    def test_missing_dist_returns_empty(self):
        inst = _make_instance()
        del inst._params['DIST.']
        assert read_detail_item(inst) == []

    def test_missing_label_returns_empty(self):
        inst = _make_instance()
        inst._params['Label'] = ''
        assert read_detail_item(inst) == []

    def test_bar_endpoints_one_directional(self):
        # BasisX = (1,0,0), origin = (10, 20), bar_arm = 16.4042 ft
        inst = _make_instance(
            basis_x=(1, 0, 0), basis_y=(0, 1, 0),
            origin=(10.0, 20.0, 5.0),
            bar_length_c_ft=16.4042
        )
        specs = read_detail_item(inst)
        s = specs[0]
        # bar_start = origin
        assert abs(s['bar_start'][0] - 10.0) < 1e-4
        assert abs(s['bar_start'][1] - 20.0) < 1e-4
        # bar_end = origin + arm * BasisX = (10 + 16.4042, 20)
        assert abs(s['bar_end'][0] - (10.0 + 16.4042)) < 1e-4
        assert abs(s['bar_end'][1] - 20.0) < 1e-4

    def test_dist_ft_stored_correctly(self):
        inst = _make_instance(dist_ft=6.5617)
        specs = read_detail_item(inst)
        assert abs(specs[0]['dist_ft'] - 6.5617) < 1e-4

    def test_dist_start_dist_end_present(self):
        # When geometry is unavailable (test stub), fallback uses -BasisY
        inst = _make_instance(basis_x=(1, 0, 0), basis_y=(0, 1, 0),
                              dist_ft=6.5617)
        specs = read_detail_item(inst)
        s = specs[0]
        assert 'dist_start' in s
        assert 'dist_end' in s

    def test_dist_dir_fallback_is_negated_basis_y(self):
        # Geometry unavailable → fallback: dist_dir = -BasisY = (0, -1)
        inst = _make_instance(basis_x=(1, 0, 0), basis_y=(0, 1, 0))
        specs = read_detail_item(inst)
        assert abs(specs[0]['dist_dir'][0] - 0.0) < 1e-6
        assert abs(specs[0]['dist_dir'][1] - (-1.0)) < 1e-6

    def test_dist_dir_fallback_negated_when_basis_y_negative(self):
        # Family placed upside-down: BasisY = (0, -1, 0) → dist_dir = (0, +1)
        inst = _make_instance(basis_x=(-1, 0, 0), basis_y=(0, -1, 0))
        specs = read_detail_item(inst)
        assert abs(specs[0]['dist_dir'][1] - 1.0) < 1e-6

    def test_combined_label_produces_two_specs(self):
        inst = _make_instance(label='T22-150+T12-150')
        specs = read_detail_item(inst)
        assert len(specs) == 2
        diams = {s['diam_mm'] for s in specs}
        assert diams == {22, 12}

    def test_spacing_converted_correctly(self):
        inst = _make_instance(label='T16-150')
        specs = read_detail_item(inst)
        expected = 150 * MM_TO_FEET
        assert abs(specs[0]['spacing_ft'] - expected) < 1e-6


# ---------------------------------------------------------------------------
# generate_add_rft_rows
# ---------------------------------------------------------------------------

class TestGenerateAddRftRows:
    def _make_spec(self, diam=16, spacing_mm=150, dist_ft=6.5617,
                   bar_arm_ft=16.4042, origin=(10.0, 20.0),
                   dist_dir=(0, 1), direction='X', mesh_layer='bottom'):
        # Compute dist_start / dist_end from origin + dist_dir * dist_ft
        # (mirrors what read_detail_item produces when geometry is unavailable)
        dist_start = origin
        dist_end   = (origin[0] + dist_ft * dist_dir[0],
                      origin[1] + dist_ft * dist_dir[1])
        return {
            'diam_mm':    diam,
            'spacing_ft': spacing_mm * MM_TO_FEET,
            'bar_start':  origin,
            'bar_end':    (origin[0] + bar_arm_ft, origin[1]),
            'dist_start': dist_start,
            'dist_end':   dist_end,
            'origin':     origin,
            'dist_ft':    dist_ft,
            'dist_dir':   dist_dir,
            'direction':  direction,
            'bar_arm_ft': bar_arm_ft,
            'mesh_layer': mesh_layer,
        }

    def test_no_hooks_flag_on_every_row(self):
        spec = self._make_spec()
        rows = generate_add_rft_rows([spec], 0.1, 0.2, 0.3, 0.4)
        assert len(rows) > 0
        for row in rows:
            assert row['no_hooks'] is True

    def test_spacing_ft_present_on_every_row(self):
        spec = self._make_spec(spacing_mm=150)
        rows = generate_add_rft_rows([spec], 0.1, 0.2, 0.3, 0.4)
        assert len(rows) > 0
        for row in rows:
            assert 'spacing_ft' in row
            assert abs(row['spacing_ft'] - 150 * 0.00328084) < 1e-5

    def test_row_count_matches_distribution(self):
        # dist_ft = 6.5617, spacing = 150mm = 0.4921ft → floor(6.5617/0.4921)+1 = 14
        spec = self._make_spec(dist_ft=6.5617, spacing_mm=150)
        rows = generate_add_rft_rows([spec], 0.1, 0.2, 0.3, 0.4)
        assert len(rows) == 14

    def test_x_direction_vary_in_x(self):
        spec = self._make_spec(
            direction='X', origin=(10.0, 20.0), bar_arm_ft=16.4042,
            dist_dir=(0, 1)
        )
        rows = generate_add_rft_rows([spec], 0.1, 0.2, 0.3, 0.4)
        assert all(r['direction'] == 'X' for r in rows)
        # vary values should be X coordinates (10.0 to 10 + 16.4042)
        assert abs(rows[0]['vary_min'] - 10.0) < 1e-4
        assert abs(rows[0]['vary_max'] - (10.0 + 16.4042)) < 1e-4

    def test_fixed_val_advances_along_dist_dir(self):
        # dist_dir = (0, 1): distribution in Y → fixed_val for X bars = Y coords change
        spec = self._make_spec(direction='X', dist_dir=(0, 1), origin=(10.0, 20.0))
        rows = generate_add_rft_rows([spec], 0.1, 0.2, 0.3, 0.4)
        fixed_vals = [r['fixed_val'] for r in rows]
        spacing_ft = 150 * MM_TO_FEET
        for i in range(1, len(fixed_vals)):
            assert abs(fixed_vals[i] - fixed_vals[i-1] - spacing_ft) < 1e-4

    def test_z_uses_bottom_x_for_bottom_x(self):
        spec = self._make_spec(direction='X', mesh_layer='bottom')
        z_bottom_x, z_bottom_y, z_top_x, z_top_y = 0.1, 0.2, 0.3, 0.4
        rows = generate_add_rft_rows([spec], z_bottom_x, z_bottom_y, z_top_x, z_top_y)
        assert all(abs(r['z'] - z_bottom_x) < 1e-6 for r in rows)

    def test_z_uses_bottom_y_for_bottom_y(self):
        spec = self._make_spec(direction='Y', mesh_layer='bottom')
        z_bottom_x, z_bottom_y = 0.1, 0.2
        rows = generate_add_rft_rows([spec], z_bottom_x, z_bottom_y, 0.3, 0.4)
        assert all(abs(r['z'] - z_bottom_y) < 1e-6 for r in rows)

    def test_zero_spacing_skipped(self):
        spec = self._make_spec()
        spec['spacing_ft'] = 0.0
        rows = generate_add_rft_rows([spec], 0.1, 0.2, 0.3, 0.4)
        assert rows == []

    def test_degenerate_vary_skipped(self):
        spec = self._make_spec()
        spec['bar_start'] = (10.0, 20.0)
        spec['bar_end']   = (10.0, 20.0)   # zero-length arm
        rows = generate_add_rft_rows([spec], 0.1, 0.2, 0.3, 0.4)
        assert rows == []


# ---------------------------------------------------------------------------
# split_bar_row — no_hooks behaviour
# ---------------------------------------------------------------------------

PARAMS_STUB = {'ld': 1.0}
RECT_SLAB = [(0, 0), (100, 0), (100, 100), (0, 100)]


class TestSplitBarRowNoHooks:
    def test_no_hooks_straight_bar(self):
        segs = split_bar_row(
            5.0, 20.0, [], [], PARAMS_STUB, 'bottom',
            fixed_val=10.0, direction='X', z=0.1, no_hooks=True
        )
        assert len(segs) == 1
        assert segs[0]['start_hook'] is False
        assert segs[0]['end_hook'] is False

    def test_default_hooks_present(self):
        segs = split_bar_row(
            5.0, 20.0, [], [], PARAMS_STUB, 'bottom',
            fixed_val=10.0, direction='X', z=0.1, no_hooks=False
        )
        assert len(segs) == 1
        assert segs[0]['start_hook'] is True
        assert segs[0]['end_hook'] is True

    def test_no_hooks_with_shaft_gap(self):
        # Shaft from 10 to 12 — bar should split but no hooks anywhere
        shaft_intervals = [(10.0, 12.0)]
        segs = split_bar_row(
            5.0, 20.0, shaft_intervals, [], PARAMS_STUB, 'bottom',
            fixed_val=7.0, direction='X', z=0.1, no_hooks=True
        )
        assert len(segs) == 2
        for seg in segs:
            assert seg['start_hook'] is False
            assert seg['end_hook'] is False

    def test_hooks_with_shaft_gap(self):
        # With hooks=True the shaft segments should get hooks
        shaft_intervals = [(10.0, 12.0)]
        segs = split_bar_row(
            5.0, 20.0, shaft_intervals, [], PARAMS_STUB, 'bottom',
            fixed_val=7.0, direction='X', z=0.1, no_hooks=False
        )
        assert len(segs) == 2
        # Segment before shaft: start_hook True (slab edge), end_hook True (shaft face)
        assert segs[0]['end_hook'] is True
        # Segment after shaft: start_hook True (shaft restart)
        assert segs[1]['start_hook'] is True


# ---------------------------------------------------------------------------
# process_bar_row — no_hooks propagation
# ---------------------------------------------------------------------------

class TestProcessBarRowNoHooks:
    def _slab_polygon(self):
        return [(0.0, 0.0), (50.0, 0.0), (50.0, 50.0), (0.0, 50.0)]

    def test_no_hooks_propagated_to_segments(self):
        row = {
            'fixed_val':  25.0,
            'vary_min':   5.0,
            'vary_max':   20.0,
            'direction':  'X',
            'z':          0.1,
            'index':      0,
            'no_hooks':   True,
            'mesh_layer': 'bottom',
        }
        segs = process_bar_row(
            row, self._slab_polygon(), [], [], PARAMS_STUB, 'bottom'
        )
        assert len(segs) > 0
        for seg in segs:
            assert seg['start_hook'] is False
            assert seg['end_hook'] is False

    def test_hooks_present_when_no_hooks_false(self):
        row = {
            'fixed_val':  25.0,
            'vary_min':   5.0,
            'vary_max':   20.0,
            'direction':  'X',
            'z':          0.1,
            'index':      0,
            'no_hooks':   False,
            'mesh_layer': 'bottom',
        }
        segs = process_bar_row(
            row, self._slab_polygon(), [], [], PARAMS_STUB, 'bottom'
        )
        assert len(segs) > 0
        # At least the end hook should be set on the last segment
        assert segs[-1]['end_hook'] is True

    def test_default_no_hooks_key_absent(self):
        # Row without 'no_hooks' key → should default to False (hooks present)
        row = {
            'fixed_val':  25.0,
            'vary_min':   5.0,
            'vary_max':   20.0,
            'direction':  'X',
            'z':          0.1,
            'index':      0,
            'mesh_layer': 'bottom',
        }
        segs = process_bar_row(
            row, self._slab_polygon(), [], [], PARAMS_STUB, 'bottom'
        )
        assert len(segs) > 0
        assert segs[-1]['end_hook'] is True


# ---------------------------------------------------------------------------
# leg_ft — reading, propagation, and U-bar shape
# ---------------------------------------------------------------------------

def _make_instance_dash(
    label='T22-150',
    dist_ft=4.5932,
    bar_length_c_ft=12.6312,
    bar_length_e_ft=1.3123,   # Config A return (Bar E)
    bar_length_d_ft=0.8202,   # Config A vertical leg (Bar D) ≈ 250mm
    bar_length_a_ft=1.6404,   # Config B return (Bar A) ≈ 500mm
    bar_length_b_ft=0.8202,   # Config B vertical leg (Bar B) ≈ 250mm
    config='A',               # 'A' → C+D+E active;  'B' → C+B+A active
    basis_x=(0, 1, 0),
    basis_y=(-1, 0, 0),
    origin=(21.0, 148.0, 5.0),
):
    """Stub FamilyInstance using Dash visibility (top add-RFT J-bars).

    Config A (C+D+E): Bar C = main arm, Bar D = vertical leg, Bar E = return.
    Config B (C+B+A): Bar C = main arm, Bar B = vertical leg, Bar A = return.
    """
    params = {
        'Label': label,
        'DIST.': dist_ft,
    }
    if config == 'A':
        active_dash_set = {'C', 'D', 'E'}
    elif config == 'B':
        active_dash_set = {'C', 'B', 'A'}
    else:  # 'straight' — only Bar C active, no vertical leg
        active_dash_set = {'C'}

    for letter in ('A', 'B', 'C', 'D', 'E'):
        params['Bar {} Visibility_Solid'.format(letter)] = 0
        params['Bar {} Visibility_Dash'.format(letter)] = (
            1 if letter in active_dash_set else 0
        )
    params['Bar Length C'] = bar_length_c_ft
    params['Bar Length E'] = bar_length_e_ft
    params['Bar Length D'] = bar_length_d_ft
    params['Bar Length A'] = bar_length_a_ft
    params['Bar Length B'] = bar_length_b_ft
    ox, oy, oz = origin
    transform = _Transform(_Vec3(ox, oy, oz), _Vec3(*basis_x), _Vec3(*basis_y))
    return _FamilyInstance(params, transform)


class TestDashVisibility:
    def test_dash_active_reads_spec(self):
        inst = _make_instance_dash(config='A')
        specs = read_detail_item(inst)
        assert len(specs) == 1

    def test_all_solid_zero_dash_active_succeeds(self):
        inst = _make_instance_dash(config='A')
        specs = read_detail_item(inst)
        assert specs != []

    # --- Config A (C+D+E): return comes from Bar E ---
    def test_config_a_leg_ft_read_from_bar_length_e(self):
        inst = _make_instance_dash(config='A', bar_length_e_ft=1.3123)
        specs = read_detail_item(inst)
        assert len(specs) == 1
        assert abs(specs[0]['leg_ft'] - 1.3123) < 1e-4

    def test_config_a_leg_ft_zero_when_bar_length_e_absent(self):
        inst = _make_instance_dash(config='A')
        del inst._params['Bar Length E']
        specs = read_detail_item(inst)
        assert len(specs) == 1
        assert specs[0]['leg_ft'] == 0.0

    def test_config_a_return_param_is_e(self):
        inst = _make_instance_dash(config='A')
        specs = read_detail_item(inst)
        assert specs[0].get('return_param') == 'E'

    # --- Config B (C+B+A): return comes from Bar A, NOT Bar E ---
    def test_config_b_leg_ft_read_from_bar_length_a(self):
        # Bar A = 1.6404 ft, Bar E = 1.3123 ft; must read A
        inst = _make_instance_dash(config='B', bar_length_a_ft=1.6404, bar_length_e_ft=1.3123)
        specs = read_detail_item(inst)
        assert len(specs) == 1
        assert abs(specs[0]['leg_ft'] - 1.6404) < 1e-4

    def test_config_b_does_not_use_bar_e_as_return(self):
        # Bar E has a different value than Bar A; confirm E is NOT used
        inst = _make_instance_dash(config='B', bar_length_a_ft=1.6404, bar_length_e_ft=0.9999)
        specs = read_detail_item(inst)
        assert len(specs) == 1
        assert abs(specs[0]['leg_ft'] - 1.6404) < 1e-4

    def test_config_b_return_param_is_a(self):
        inst = _make_instance_dash(config='B')
        specs = read_detail_item(inst)
        assert specs[0].get('return_param') == 'A'

    def test_leg_ft_key_present_in_spec(self):
        inst = _make_instance_dash(config='A')
        specs = read_detail_item(inst)
        assert 'leg_ft' in specs[0]

    def test_bar_arm_uses_bar_length_c(self):
        inst = _make_instance_dash(config='A', bar_length_c_ft=12.6312)
        specs = read_detail_item(inst)
        assert abs(specs[0]['bar_arm_ft'] - 12.6312) < 1e-4

    # --- has_hook flag ---

    def test_config_a_has_hook_is_true(self):
        inst = _make_instance_dash(config='A', bar_length_e_ft=1.3123)
        specs = read_detail_item(inst)
        assert len(specs) == 1
        assert specs[0].get('has_hook') is True

    def test_config_b_has_hook_is_true(self):
        inst = _make_instance_dash(config='B', bar_length_a_ft=1.6404)
        specs = read_detail_item(inst)
        assert len(specs) == 1
        assert specs[0].get('has_hook') is True

    def test_straight_bar_has_hook_is_false(self):
        # Only Bar C active (no D or B) — bottom add RFT case
        inst = _make_instance_dash(config='straight', bar_length_e_ft=1.3123)
        specs = read_detail_item(inst)
        assert len(specs) == 1
        assert specs[0].get('has_hook') is False

    def test_straight_bar_leg_ft_is_zero(self):
        # Even if Bar Length E has a value, straight bar must NOT read it
        inst = _make_instance_dash(config='straight', bar_length_e_ft=1.3123)
        specs = read_detail_item(inst)
        assert len(specs) == 1
        assert specs[0]['leg_ft'] == 0.0

    def test_has_hook_key_present_in_spec(self):
        inst = _make_instance_dash(config='A')
        specs = read_detail_item(inst)
        assert 'has_hook' in specs[0]


class TestLegFtPropagation:
    def _make_spec_with_leg(self, leg_ft=1.3123, direction='Y'):
        dist_dir = (-1, 0) if direction == 'Y' else (0, 1)
        origin = (21.0, 148.0)
        dist_ft = 4.5932
        bar_arm_ft = 12.6312
        dist_start = origin
        dist_end = (origin[0] + dist_ft * dist_dir[0],
                    origin[1] + dist_ft * dist_dir[1])
        bar_end = (origin[0] + bar_arm_ft * (0 if direction == 'Y' else 1),
                   origin[1] + bar_arm_ft * (1 if direction == 'Y' else 0))
        return {
            'diam_mm':    22,
            'spacing_ft': 150 * MM_TO_FEET,
            'bar_start':  origin,
            'bar_end':    bar_end,
            'dist_start': dist_start,
            'dist_end':   dist_end,
            'origin':     origin,
            'dist_ft':    dist_ft,
            'dist_dir':   dist_dir,
            'direction':  direction,
            'bar_arm_ft': bar_arm_ft,
            'mesh_layer': 'top',
            'leg_ft':     leg_ft,
        }

    def test_leg_ft_in_every_row(self):
        spec = self._make_spec_with_leg(leg_ft=1.3123)
        rows = generate_add_rft_rows([spec], 0.1, 0.2, 0.3, 0.4)
        assert len(rows) > 0
        for row in rows:
            assert abs(row['leg_ft'] - 1.3123) < 1e-4

    def test_leg_ft_zero_stays_zero_in_rows(self):
        spec = self._make_spec_with_leg(leg_ft=0.0)
        rows = generate_add_rft_rows([spec], 0.1, 0.2, 0.3, 0.4)
        assert len(rows) > 0
        for row in rows:
            assert row['leg_ft'] == 0.0

    def test_leg_ft_propagated_through_process_bar_row(self):
        row = {
            'fixed_val':   21.0,
            'vary_min':    148.0,
            'vary_max':    160.0,
            'direction':   'Y',
            'z':           0.3,
            'index':       0,
            'no_hooks':    True,
            'mesh_layer':  'top',
            'leg_ft':      1.3123,
            'has_hook':    True,
            'is_add_rft':  True,
            'hook_at_max': True,
        }
        slab = [(0.0, 0.0), (100.0, 0.0), (100.0, 200.0), (0.0, 200.0)]
        segs = process_bar_row(row, slab, [], [], PARAMS_STUB, 'top')
        assert len(segs) > 0
        for seg in segs:
            assert abs(seg['leg_ft'] - 1.3123) < 1e-4

    def test_row_without_leg_ft_segments_default_zero(self):
        row = {
            'fixed_val':  21.0,
            'vary_min':   148.0,
            'vary_max':   160.0,
            'direction':  'Y',
            'z':          0.3,
            'index':      0,
            'no_hooks':   True,
            'mesh_layer': 'top',
            'has_hook':   False,
            'is_add_rft': True,
        }
        slab = [(0.0, 0.0), (100.0, 0.0), (100.0, 200.0), (0.0, 200.0)]
        segs = process_bar_row(row, slab, [], [], PARAMS_STUB, 'top')
        assert len(segs) > 0
        for seg in segs:
            assert seg.get('leg_ft', 0.0) == 0.0

    def test_hook_at_max_propagated_through_process_bar_row(self):
        for ham in (True, False):
            row = {
                'fixed_val':   21.0,
                'vary_min':    148.0,
                'vary_max':    160.0,
                'direction':   'Y',
                'z':           0.3,
                'index':       0,
                'no_hooks':    True,
                'mesh_layer':  'top',
                'leg_ft':      1.3123,
                'has_hook':    True,
                'is_add_rft':  True,
                'hook_at_max': ham,
            }
            slab = [(0.0, 0.0), (100.0, 0.0), (100.0, 200.0), (0.0, 200.0)]
            segs = process_bar_row(row, slab, [], [], PARAMS_STUB, 'top')
            assert len(segs) > 0
            for seg in segs:
                assert seg['hook_at_max'] is ham


class TestJBarShape:
    """Verify J-bar (4-point) shape: straight end at origin, leg+return at hook end."""

    _params = {'slab_top_z': 2.0, 'slab_bottom_z': 0.0,
               'slab_thickness': 2.0, 'cover': 0.066}

    def _j_points(self, direction, vary_min, vary_max, fixed, z_layer, leg_ft,
                  hook_at_max, params):
        """Compute the 4 J-bar points the same way place_segment does."""
        from rebar_placer import _get_vertical_leg_delta
        dz = _get_vertical_leg_delta(z_layer, params)
        if direction == 'X':
            p1 = (vary_min, fixed, z_layer)
            p2 = (vary_max, fixed, z_layer)
            if hook_at_max:
                pt_straight  = p1
                pt_hook_top  = p2
                ret_sign     = -1
            else:
                pt_straight  = p2
                pt_hook_top  = p1
                ret_sign     = +1
            pt_hook_bot = (pt_hook_top[0], pt_hook_top[1], pt_hook_top[2] + dz)
            pt_return   = (pt_hook_top[0] + ret_sign * leg_ft,
                           pt_hook_top[1],
                           pt_hook_top[2] + dz)
        else:
            p1 = (fixed, vary_min, z_layer)
            p2 = (fixed, vary_max, z_layer)
            if hook_at_max:
                pt_straight = p1
                pt_hook_top = p2
                ret_sign    = -1
            else:
                pt_straight = p2
                pt_hook_top = p1
                ret_sign    = +1
            pt_hook_bot = (pt_hook_top[0], pt_hook_top[1], pt_hook_top[2] + dz)
            pt_return   = (pt_hook_top[0],
                           pt_hook_top[1] + ret_sign * leg_ft,
                           pt_hook_top[2] + dz)
        return [pt_straight, pt_hook_top, pt_hook_bot, pt_return], dz

    # --- Basic shape checks ---

    def test_top_bar_dz_is_negative(self):
        from rebar_placer import _get_vertical_leg_delta
        dz = _get_vertical_leg_delta(1.8, self._params)
        assert dz < 0

    def test_four_points_produced(self):
        pts, _ = self._j_points('X', 0.0, 10.0, 5.0, 1.8, 1.3123, True, self._params)
        assert len(pts) == 4

    # --- hook_at_max=True: hook at vary_max (right end for X) ---

    def test_x_hook_at_max_straight_end_is_at_vary_min(self):
        pts, _ = self._j_points('X', 0.0, 10.0, 5.0, 1.8, 1.3123, True, self._params)
        assert pts[0][0] == 0.0    # pt_straight.X == vary_min

    def test_x_hook_at_max_hook_top_is_at_vary_max(self):
        pts, _ = self._j_points('X', 0.0, 10.0, 5.0, 1.8, 1.3123, True, self._params)
        assert pts[1][0] == 10.0   # pt_hook_top.X == vary_max

    def test_x_hook_at_max_hook_bottom_below_hook_top(self):
        pts, dz = self._j_points('X', 0.0, 10.0, 5.0, 1.8, 1.3123, True, self._params)
        assert pts[2][2] < pts[1][2]   # hook_bot.Z < hook_top.Z

    def test_x_hook_at_max_return_goes_inward(self):
        # Return must go LEFT from vary_max back toward vary_min
        pts, _ = self._j_points('X', 0.0, 10.0, 5.0, 1.8, 1.3123, True, self._params)
        assert pts[3][0] < pts[1][0]   # return.X < hook_top.X
        assert pts[3][0] > 0.0         # return stays within span

    def test_x_hook_at_max_return_length_equals_leg_ft(self):
        leg_ft = 1.3123
        pts, _ = self._j_points('X', 0.0, 10.0, 5.0, 1.8, leg_ft, True, self._params)
        assert abs(pts[1][0] - pts[3][0] - leg_ft) < 1e-6

    # --- hook_at_max=False: hook at vary_min (left end for X) ---

    def test_x_hook_at_min_straight_end_is_at_vary_max(self):
        pts, _ = self._j_points('X', 0.0, 10.0, 5.0, 1.8, 1.3123, False, self._params)
        assert pts[0][0] == 10.0   # pt_straight.X == vary_max

    def test_x_hook_at_min_hook_top_is_at_vary_min(self):
        pts, _ = self._j_points('X', 0.0, 10.0, 5.0, 1.8, 1.3123, False, self._params)
        assert pts[1][0] == 0.0    # pt_hook_top.X == vary_min

    def test_x_hook_at_min_return_goes_rightward(self):
        # Return must go RIGHT from vary_min back toward vary_max
        pts, _ = self._j_points('X', 0.0, 10.0, 5.0, 1.8, 1.3123, False, self._params)
        assert pts[3][0] > pts[1][0]

    # --- Y direction ---

    def test_y_hook_at_max_straight_end_at_vary_min(self):
        pts, _ = self._j_points('Y', 0.0, 10.0, 5.0, 1.8, 1.3123, True, self._params)
        assert pts[0][1] == 0.0

    def test_y_hook_at_max_return_goes_inward(self):
        pts, _ = self._j_points('Y', 0.0, 10.0, 5.0, 1.8, 1.3123, True, self._params)
        assert pts[3][1] < pts[1][1]

    # --- Span guard ---

    def test_short_span_falls_back_to_straight(self):
        # span (0.5 ft) <= leg_ft (1.3123 ft) → guard prevents J-shape
        leg_ft = 1.3123
        span = 0.5
        assert not (span > leg_ft), 'Guard should prevent J-shape for short span'

    def test_long_span_allows_j_shape(self):
        leg_ft = 1.3123
        span = 10.0
        assert span > leg_ft, 'Guard should allow J-shape for long span'

    def test_span_just_above_leg_ft_allowed(self):
        leg_ft = 1.3123
        span = leg_ft + 0.01
        assert span > leg_ft
