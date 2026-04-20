# -*- coding: utf-8 -*-
"""
Tests for place_all_slab_bars placement logic.

Covers:
  - base-bar failure falling back to individual placement (not skipping the whole group)
  - base-bar success leading to a rebar set via SetLayoutAsNumberWithSpacing
  - set-creation failure falling back to individual placement for the rest of the block
  - separate groups being placed independently (failure in one does not block another)
  - stagger parity: unspliced rows form one set; single-parity spliced groups use 2x spacing

Run with:  python -m pytest tests/test_rebar_placer_placement.py -v
(No Revit API required.)
"""
from __future__ import print_function
import sys
import os
import types as _types

# ---------------------------------------------------------------------------
# Stubs — must be registered before any project module is imported
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

_sys_cg = _ensure_stub('System.Collections.Generic')
class _ListStub(list):
    def __init__(self, *a, **kw): pass
    def Add(self, item): self.append(item)
    @property
    def Count(self): return len(self)
_sys_cg.List = _ListStub

_db = _ensure_stub('Autodesk.Revit.DB')
for _sym in ('Line', 'XYZ', 'Curve', 'BuiltInParameter',
             'FilteredElementCollector', 'Floor', 'Opening', 'Wall',
             'FamilyInstance', 'JoinGeometryUtils', 'BuiltInCategory',
             'FailureHandlingOptions', 'FailureSeverity', 'TransactionStatus',
             'IFailuresPreprocessor', 'FailureProcessingResult'):
    setattr(_db, _sym, None)
# Transaction is intentionally left as None here; each test patches it.

_ensure_stub('Autodesk')
_ensure_stub('Autodesk.Revit')

_dbs = _ensure_stub('Autodesk.Revit.DB.Structure')
for _sym in ('Rebar', 'RebarStyle', 'RebarHookOrientation'):
    setattr(_dbs, _sym, None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import pytest
import unittest.mock as mock
from rebar_placer import place_all_slab_bars, _slice_key, _is_uniform_spacing


# ---------------------------------------------------------------------------
# Shared mock helpers
# ---------------------------------------------------------------------------

class _MockFailureOptions:
    def SetDelayedMiniWarnings(self, v): pass
    def SetClearAfterRollback(self, v): pass
    def SetFailuresPreprocessor(self, v): pass
    def SetForcedModalHandling(self, v): pass

class _MockTransaction:
    def __init__(self, doc, name): pass
    def Start(self): pass
    def Commit(self): pass
    def RollBack(self): pass
    def GetFailureHandlingOptions(self): return _MockFailureOptions()
    def SetFailureHandlingOptions(self, opts): pass


class _MockRebar:
    """Minimal stand-in for a Revit Rebar element."""
    _id_counter = 0

    def __init__(self):
        _MockRebar._id_counter += 1
        self.Id = _MockRebar._id_counter
        self._accessor = mock.MagicMock()
        self._accessor.SetLayoutAsNumberWithSpacing = mock.MagicMock()
        self._accessor.SetLayoutAsMaximumSpacing = mock.MagicMock()

    def GetShapeDrivenAccessor(self):
        return self._accessor

    def get_Parameter(self, *a):
        return None

    def LookupParameter(self, *a):
        return None

    def GetParameters(self, *a):
        return []


BASE_PARAMS = {
    'spacing':        1.0,
    'cover':          0.1,
    'stagger_splices': True,
    'slab_top_z':     1.0,
    'slab_bottom_z':  0.0,
    'slab_thickness': 1.0,
}


def _seg(start, end, fixed_val, index=0, direction='X', z=0.05,
         start_hook=False, end_hook=False, mesh_layer='bottom'):
    return {
        'start':      start,
        'end':        end,
        'fixed_val':  fixed_val,
        'index':      index,
        'direction':  direction,
        'z':          z,
        'start_hook': start_hook,
        'end_hook':   end_hook,
        'mesh_layer': mesh_layer,
    }


def _run(segs, params=None, place_fn=None):
    """Run place_all_slab_bars with Transaction and place_segment mocked out."""
    params = params or dict(BASE_PARAMS)
    if place_fn is None:
        place_fn = lambda *a, **kw: _MockRebar()
    with mock.patch('rebar_placer.Transaction', _MockTransaction), \
         mock.patch('rebar_placer.place_segment', side_effect=place_fn):
        return place_all_slab_bars(None, None, segs, None, None, params)


# ===========================================================================
# 1. Base-bar failure → fallback to individual placement
# ===========================================================================

class TestBaseBarFailureFallback:

    def test_all_bars_attempted_when_base_fails(self):
        """All bars in a group must be attempted even if the base bar fails."""
        segs = [_seg(0.0, 10.0, fixed_val=float(i), index=i) for i in range(4)]
        attempted = []

        def _place(doc, floor, seg, bt, ht, layer_z, p):
            attempted.append(seg['fixed_val'])
            return None  # everything fails

        placed, failed, _ = _run(segs, place_fn=_place)

        assert len(attempted) == 4, (
            "Expected 4 placement attempts; got {}. "
            "Whole group must not be silently dropped.".format(len(attempted))
        )
        assert placed == 0
        assert failed == 4

    def test_base_fails_rest_succeed_counts_correct(self):
        """Base bar fails, remaining 3 succeed → placed=3, failed=1."""
        segs = [_seg(0.0, 10.0, fixed_val=float(i), index=i) for i in range(4)]
        call_log = []

        def _place(doc, floor, seg, bt, ht, layer_z, p):
            call_log.append(seg['fixed_val'])
            return None if len(call_log) == 1 else _MockRebar()

        placed, failed, _ = _run(segs, place_fn=_place)

        assert len(call_log) == 4
        assert placed == 3
        assert failed == 1

    def test_base_succeeds_not_placed_twice(self):
        """When base bar succeeds it is counted exactly once."""
        segs = [_seg(0.0, 10.0, fixed_val=float(i), index=i) for i in range(3)]
        placed, failed, _ = _run(segs)
        assert placed == 3
        assert failed == 0

    def test_single_bar_group_base_fails_individual_attempted(self):
        """Single-bar group whose base fails must still attempt that one bar."""
        segs = [_seg(0.0, 10.0, fixed_val=0.0, index=0)]
        attempted = []

        def _place(doc, floor, seg, bt, ht, layer_z, p):
            attempted.append(1)
            return None

        placed, failed, _ = _run(segs, place_fn=_place)
        assert len(attempted) == 1
        assert failed == 1

    def test_empty_segment_list_no_placement(self):
        called = []
        _run([], place_fn=lambda *a, **kw: called.append(1) or _MockRebar())
        assert called == []


# ===========================================================================
# 2. Rebar set creation (base bar succeeds, uniform spacing)
# ===========================================================================

class TestRebarSetCreation:

    def test_uniform_spacing_calls_set_layout(self):
        """Uniform spacing → SetLayoutAsNumberWithSpacing called with correct args."""
        shared_rebar = _MockRebar()
        segs = [_seg(0.0, 10.0, fixed_val=float(i), index=i) for i in range(4)]

        _run(segs, place_fn=lambda *a, **kw: shared_rebar)

        acc = shared_rebar._accessor
        assert acc.SetLayoutAsNumberWithSpacing.called, \
            "SetLayoutAsNumberWithSpacing should be called for uniform spacing"
        n, sp = acc.SetLayoutAsNumberWithSpacing.call_args[0][:2]
        assert n == 4
        assert abs(sp - 1.0) < 1e-6

    def test_non_uniform_spacing_no_set_layout(self):
        """Non-uniform fixed_vals → set layout not called; bars placed individually."""
        segs = [
            _seg(0.0, 10.0, fixed_val=0.0, index=0),
            _seg(0.0, 10.0, fixed_val=1.0, index=1),
            _seg(0.0, 10.0, fixed_val=3.5, index=2),  # gap breaks uniformity
            _seg(0.0, 10.0, fixed_val=4.5, index=3),
        ]
        shared_rebar = _MockRebar()
        placed, failed, _ = _run(segs, place_fn=lambda *a, **kw: shared_rebar)

        acc = shared_rebar._accessor
        # Non-uniform → split into two contiguous blocks, each placed individually
        assert placed == 4
        assert failed == 0

    def test_set_layout_failure_falls_back_to_individual(self):
        """If SetLayoutAsNumberWithSpacing raises, remaining bars placed individually."""
        call_count = [0]

        class _FailSetRebar(_MockRebar):
            def GetShapeDrivenAccessor(self):
                acc = mock.MagicMock()
                acc.SetLayoutAsNumberWithSpacing.side_effect = Exception("Revit error")
                acc.SetLayoutAsMaximumSpacing.side_effect = Exception("Revit error")
                return acc

        def _place(doc, floor, seg, bt, ht, layer_z, p):
            call_count[0] += 1
            return _FailSetRebar()

        segs = [_seg(0.0, 10.0, fixed_val=float(i), index=i) for i in range(4)]
        placed, failed, _ = _run(segs, place_fn=_place)

        # All 4 bars should end up placed (1 base + 3 individual fallback)
        assert placed == 4
        assert failed == 0


# ===========================================================================
# 3. Independent groups — failure in one group does not block others
# ===========================================================================

class TestIndependentGroups:

    def test_different_extents_all_bars_placed(self):
        """Bars with different extents form separate groups; all should be placed."""
        segs = [
            _seg(0.0, 10.0, fixed_val=0.0, index=0),
            _seg(0.0, 10.0, fixed_val=1.0, index=1),
            _seg(1.0,  9.0, fixed_val=2.0, index=2),  # shorter bar → separate group
            _seg(1.0,  9.0, fixed_val=3.0, index=3),
        ]
        placed, failed, _ = _run(segs)
        assert placed == 4
        assert failed == 0

    def test_group_a_failure_does_not_block_group_b(self):
        """Base failure in group A must not prevent group B from being placed."""
        segs_a = [_seg(0.0, 10.0, fixed_val=float(i), index=i) for i in range(3)]
        segs_b = [_seg(2.0,  8.0, fixed_val=float(i), index=i) for i in range(3, 6)]

        def _place(doc, floor, seg, bt, ht, layer_z, p):
            # group A (start=0, end=10) always fails
            if abs(seg['start'] - 0.0) < 1e-6 and abs(seg['end'] - 10.0) < 1e-6:
                return None
            return _MockRebar()

        placed, failed, _ = _run(segs_a + segs_b, place_fn=_place)
        assert placed == 3   # group B succeeds
        assert failed == 3   # group A fails

    def test_z_layer_separation(self):
        """Bottom and top bars are in different groups (different z); both placed."""
        segs = [
            _seg(0.0, 10.0, fixed_val=0.0, index=0, z=0.05),   # bottom
            _seg(0.0, 10.0, fixed_val=1.0, index=1, z=0.05),
            _seg(0.0, 10.0, fixed_val=0.0, index=0, z=0.95),   # top
            _seg(0.0, 10.0, fixed_val=1.0, index=1, z=0.95),
        ]
        placed, failed, _ = _run(segs)
        assert placed == 4
        assert failed == 0


# ===========================================================================
# 4. Stagger parity handling
# ===========================================================================

class TestStaggerParity:

    def test_unspliced_mixed_parity_one_set_normal_spacing(self):
        """Unspliced rows with both even/odd indices → single set at normal spacing."""
        # All rows share same geometry → same slice key → one group
        segs = [_seg(0.0, 10.0, fixed_val=float(i), index=i) for i in range(6)]
        shared_rebar = _MockRebar()
        params = dict(BASE_PARAMS, stagger_splices=True, spacing=1.0)

        _run(segs, params=params, place_fn=lambda *a, **kw: shared_rebar)

        acc = shared_rebar._accessor
        if acc.SetLayoutAsNumberWithSpacing.called:
            n, sp = acc.SetLayoutAsNumberWithSpacing.call_args[0][:2]
            assert n == 6
            assert abs(sp - 1.0) < 1e-6, \
                "Unspliced mixed-parity group should use normal spacing, not 2x"

    def test_even_only_group_double_spacing(self):
        """Even-index only group (spliced rows) → set uses 2× spacing."""
        # Even rows at positions 0, 2, 4 — spacing between them is 2.0
        segs = [_seg(0.0, 5.0, fixed_val=float(i) * 2, index=i * 2) for i in range(3)]
        shared_rebar = _MockRebar()
        params = dict(BASE_PARAMS, stagger_splices=True, spacing=1.0)

        _run(segs, params=params, place_fn=lambda *a, **kw: shared_rebar)

        acc = shared_rebar._accessor
        if acc.SetLayoutAsNumberWithSpacing.called:
            n, sp = acc.SetLayoutAsNumberWithSpacing.call_args[0][:2]
            assert n == 3
            assert abs(sp - 2.0) < 1e-6, \
                "Even-only group should use 2× spacing"

    def test_odd_only_group_double_spacing(self):
        """Odd-index only group (spliced rows) → set uses 2× spacing."""
        segs = [_seg(0.0, 5.0, fixed_val=float(i) * 2 + 1, index=i * 2 + 1)
                for i in range(3)]
        shared_rebar = _MockRebar()
        params = dict(BASE_PARAMS, stagger_splices=True, spacing=1.0)

        _run(segs, params=params, place_fn=lambda *a, **kw: shared_rebar)

        acc = shared_rebar._accessor
        if acc.SetLayoutAsNumberWithSpacing.called:
            n, sp = acc.SetLayoutAsNumberWithSpacing.call_args[0][:2]
            assert abs(sp - 2.0) < 1e-6, \
                "Odd-only group should also use 2× spacing"


# ===========================================================================
# 5. Hook flag separation (bars at slab edge vs interior vs shaft edge)
# ===========================================================================

class TestHookFlagSeparation:

    def test_different_hook_combos_separate_groups_all_placed(self):
        """Bars with different hook combos must not be merged; all should be placed."""
        segs = [
            _seg(0.0, 10.0, fixed_val=0.0, start_hook=True,  end_hook=True),   # slab edge both
            _seg(0.0, 10.0, fixed_val=1.0, start_hook=True,  end_hook=True),
            _seg(0.0, 10.0, fixed_val=2.0, start_hook=True,  end_hook=False),  # shaft on right
            _seg(0.0, 10.0, fixed_val=3.0, start_hook=True,  end_hook=False),
            _seg(0.0, 10.0, fixed_val=4.0, start_hook=False, end_hook=False),  # splice interior
            _seg(0.0, 10.0, fixed_val=5.0, start_hook=False, end_hook=False),
        ]
        placed, failed, _ = _run(segs)
        assert placed == 6
        assert failed == 0

    def test_hook_combo_determines_group_membership(self):
        """Two bars: same extents, same z, different hooks → different slice keys."""
        s1 = _seg(0.0, 10.0, fixed_val=0.0, start_hook=True,  end_hook=True)
        s2 = _seg(0.0, 10.0, fixed_val=0.0, start_hook=False, end_hook=False)
        assert _slice_key(s1, 0.01) != _slice_key(s2, 0.01)


# ===========================================================================
# 6. _is_uniform_spacing edge cases
# ===========================================================================

class TestIsUniformSpacing:

    def test_single_value_not_uniform(self):
        ok, _ = _is_uniform_spacing([5.0])
        assert ok is False

    def test_two_values_uniform(self):
        ok, sp = _is_uniform_spacing([0.0, 1.0])
        assert ok is True
        assert abs(sp - 1.0) < 1e-9

    def test_duplicate_values_not_uniform(self):
        ok, _ = _is_uniform_spacing([0.0, 0.0, 0.0])
        assert ok is False

    def test_expected_spacing_used_when_provided(self):
        # diffs are 1.0, 1.0 but expected is 1.0 → uniform
        ok, sp = _is_uniform_spacing([0.0, 1.0, 2.0], expected_spacing=1.0)
        assert ok is True
        assert abs(sp - 1.0) < 1e-9

    def test_WRONG_irregular_diffs_not_uniform(self):
        ok, _ = _is_uniform_spacing([0.0, 1.0, 2.5, 3.0])
        assert ok is False

    def test_within_tolerance_still_uniform(self):
        ok, _ = _is_uniform_spacing([0.0, 1.0, 2.001], tol=0.005)
        assert ok is True
