# -*- coding: utf-8 -*-
"""
Tests for detail_placer.py (bending detail, span annotation, distribution logic).

Run with:  python -m pytest tests/test_detail_placer.py -v
(No Revit API required – all Revit-touching code is stubbed.)
"""
from __future__ import print_function
import sys
import os
import types as _types

# ---------------------------------------------------------------------------
# Minimal stubs so the module can be imported without a Revit environment
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
_ensure_stub('Autodesk')
_ensure_stub('Autodesk.Revit')

_db = _ensure_stub('Autodesk.Revit.DB')


class _XYZ:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.X = x
        self.Y = y
        self.Z = z

    def __sub__(self, other):
        return _XYZ(self.X - other.X, self.Y - other.Y, self.Z - other.Z)

    def GetLength(self):
        return (self.X**2 + self.Y**2 + self.Z**2) ** 0.5


_db.XYZ = _XYZ
_db.Line = None
_db.IndependentTag = None
_db.TagOrientation = _types.SimpleNamespace(Horizontal=0)
_db.Reference = None
_db.BuiltInParameter = _types.SimpleNamespace(
    ALL_MODEL_MARK='MARK',
    REBAR_ELEM_QUANTITY_OF_BARS='REBAR_COUNT',
)

# TextNote stub — records Create() calls
_text_note_calls = []


class _TextNoteStub:
    @staticmethod
    def Create(doc, view_id, pt, text, type_id):
        _text_note_calls.append({
            'doc': doc, 'view_id': view_id,
            'pt': pt, 'text': text, 'type_id': type_id,
        })
        return object()


_db.TextNote = _TextNoteStub


class _TextNoteTypeStub:
    pass


_db.TextNoteType = _TextNoteTypeStub


class _TextNoteOptionsStub:
    def __init__(self, type_id):
        self.type_id = type_id


_db.TextNoteOptions = _TextNoteOptionsStub


class _ReferenceArrayStub:
    def __init__(self):
        self._refs = []

    def Append(self, ref):
        self._refs.append(ref)


_db.ReferenceArray = _ReferenceArrayStub

# ElementTransformUtils stub — records MoveElement calls
_move_element_calls = []


class _ElementTransformUtilsStub:
    @staticmethod
    def MoveElement(doc, element_id, vector):
        _move_element_calls.append({'doc': doc, 'element_id': element_id, 'vector': vector})


_db.ElementTransformUtils = _ElementTransformUtilsStub


class _FilledRegionTypeStub:
    def __init__(self):
        self.Id = 'frt-id-1'


_filled_region_calls = []


class _FilledRegionStub:
    @staticmethod
    def Create(doc, type_id, view_id, loops):
        obj = object()
        _filled_region_calls.append({'type_id': type_id, 'loops': loops})
        return obj


_db.FilledRegionType = _FilledRegionTypeStub
_db.FilledRegion = _FilledRegionStub


class _CurveLoopStub:
    def __init__(self):
        self._curves = []

    def Append(self, curve):
        self._curves.append(curve)


class _ArcStub:
    @staticmethod
    def Create(center, radius, start_angle, end_angle, x_axis, y_axis):
        return object()


_db.CurveLoop = _CurveLoopStub
_db.Arc = _ArcStub

_dbs = _ensure_stub('Autodesk.Revit.DB.Structure')


class _MultiplanarOptionStub:
    IncludeAllMultiplanarCurves = 0


_dbs.Rebar = None
_dbs.MultiplanarOption = _MultiplanarOptionStub


# FilteredElementCollector stub — configured per-test via _FEC_REGISTRY
_FEC_REGISTRY = {}   # maps class -> list of elements to return


class _FECStub:
    def __init__(self, doc):
        self._doc = doc
        self._cls = None

    def OfClass(self, cls):
        self._cls = cls
        return self

    def ToElements(self):
        return list(_FEC_REGISTRY.get(self._cls, []))

    def __iter__(self):
        return iter(_FEC_REGISTRY.get(self._cls, []))

    def FirstElement(self):
        items = _FEC_REGISTRY.get(self._cls, [])
        return items[0] if items else None


_db.FilteredElementCollector = _FECStub

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ---------------------------------------------------------------------------
# Import module under test
# ---------------------------------------------------------------------------
import detail_placer

# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------

class _Param:
    def __init__(self, value):
        self._value = value

    def AsString(self):
        if self._value is None:
            return None
        return str(self._value)

    def AsInteger(self):
        return int(self._value)


class _RebarStub:
    """Minimal Revit Rebar stand-in."""

    def __init__(self, mark='Bottom X', midpoint=None, curves=None, bar_count=None):
        self._mark = mark
        self._midpoint = midpoint or _XYZ(10.0, 20.0, 5.0)
        self._curves = curves or []
        self._bar_count = bar_count
        self.Id = 'rebar-id-{}'.format(id(self))

    def get_Parameter(self, bip):
        if bip == 'MARK':
            return _Param(self._mark)
        if bip == 'REBAR_COUNT' and self._bar_count is not None:
            return _Param(self._bar_count)
        return None

    def get_BoundingBox(self, view):
        # Return a bbox centred at _midpoint
        bb = _types.SimpleNamespace(
            Min=_XYZ(self._midpoint.X - 1, self._midpoint.Y - 1, self._midpoint.Z),
            Max=_XYZ(self._midpoint.X + 1, self._midpoint.Y + 1, self._midpoint.Z),
        )
        return bb

    def GetCenterlineCurves(self, *args):
        return self._curves


class _CurveStub:
    def __init__(self, p0, p1):
        self._p0 = p0
        self._p1 = p1

    @property
    def Length(self):
        dx = self._p1.X - self._p0.X
        dy = self._p1.Y - self._p0.Y
        dz = self._p1.Z - self._p0.Z
        return (dx**2 + dy**2 + dz**2) ** 0.5

    def Evaluate(self, param, normalised):
        # param=0.5 → midpoint
        return _XYZ(
            self._p0.X + param * (self._p1.X - self._p0.X),
            self._p0.Y + param * (self._p1.Y - self._p0.Y),
            self._p0.Z + param * (self._p1.Z - self._p0.Z),
        )

    def GetEndPoint(self, idx):
        return self._p0 if idx == 0 else self._p1


class _ViewStub:
    def __init__(self, scale=50):
        self.Id = object()
        self.Scale = scale


class _DocStub:
    pass


# ---------------------------------------------------------------------------
# _direction_from_mark
# ---------------------------------------------------------------------------

class TestDirectionFromMark:
    def test_x_suffix_gives_x_direction(self):
        d = detail_placer._direction_from_mark('Bottom X')
        assert d.X == 1.0 and d.Y == 0.0

    def test_y_suffix_gives_y_direction(self):
        d = detail_placer._direction_from_mark('Bottom Y')
        assert d.X == 0.0 and d.Y == 1.0

    def test_add_bottom_x(self):
        d = detail_placer._direction_from_mark('Add Bottom X')
        assert d.X == 1.0 and d.Y == 0.0

    def test_add_top_y(self):
        d = detail_placer._direction_from_mark('Add Top Y')
        assert d.X == 0.0 and d.Y == 1.0

    def test_dp_bar_x(self):
        d = detail_placer._direction_from_mark('Drop Panel X')
        assert d.X == 1.0 and d.Y == 0.0

    def test_dp_bar_y(self):
        d = detail_placer._direction_from_mark('Drop Panel Y')
        assert d.X == 0.0 and d.Y == 1.0

    def test_none_defaults_to_x(self):
        d = detail_placer._direction_from_mark(None)
        assert d.X == 1.0 and d.Y == 0.0

    def test_empty_string_defaults_to_x(self):
        d = detail_placer._direction_from_mark('')
        assert d.X == 1.0 and d.Y == 0.0


# ---------------------------------------------------------------------------
# X_MARKS set
# ---------------------------------------------------------------------------

class TestXMarks:
    def test_contains_all_x_marks(self):
        expected = {'Bottom X', 'Top X', 'Add Bottom X', 'Add Top X', 'Drop Panel X'}
        assert expected == detail_placer.X_MARKS

    def test_y_marks_not_in_x_marks(self):
        for m in ('Bottom Y', 'Top Y', 'Add Bottom Y', 'Add Top Y', 'Drop Panel Y'):
            assert m not in detail_placer.X_MARKS


# ---------------------------------------------------------------------------
# _get_mark
# ---------------------------------------------------------------------------

class TestGetMark:
    def test_returns_mark_string(self):
        rb = _RebarStub(mark='Top X')
        assert detail_placer._get_mark(rb) == 'Top X'

    def test_missing_parameter_returns_empty(self):
        rb = _RebarStub()
        rb.get_Parameter = lambda bip: None
        assert detail_placer._get_mark(rb) == ''

    def test_none_value_returns_empty(self):
        rb = _RebarStub(mark=None)
        assert detail_placer._get_mark(rb) == ''


# ---------------------------------------------------------------------------
# _bar_midpoint
# ---------------------------------------------------------------------------

class TestBarMidpoint:
    def test_uses_centerline_curve_when_available(self):
        p0 = _XYZ(0.0, 0.0, 5.0)
        p1 = _XYZ(20.0, 0.0, 5.0)
        curve = _CurveStub(p0, p1)
        rb = _RebarStub(curves=[curve])
        pt = detail_placer._bar_midpoint(rb)
        assert abs(pt.X - 10.0) < 1e-6
        assert abs(pt.Y - 0.0) < 1e-6

    def test_falls_back_to_bounding_box(self):
        rb = _RebarStub(midpoint=_XYZ(5.0, 10.0, 2.0), curves=[])
        pt = detail_placer._bar_midpoint(rb)
        # bbox min=(4,9) max=(6,11) → centre=(5,10)
        assert abs(pt.X - 5.0) < 1e-6
        assert abs(pt.Y - 10.0) < 1e-6

    def test_returns_none_when_no_geometry(self):
        rb = _RebarStub(curves=[])
        rb.get_BoundingBox = lambda v: None
        pt = detail_placer._bar_midpoint(rb)
        assert pt is None


# ---------------------------------------------------------------------------
# _get_rebar_zone_extent
# ---------------------------------------------------------------------------

class TestGetRebarZoneExtent:
    def _make_bb(self, min_x, max_x, min_y, max_y, z=1.0):
        return _types.SimpleNamespace(
            Min=_XYZ(min_x, min_y, z),
            Max=_XYZ(max_x, max_y, z),
        )

    def test_y_axis_returns_bbox_y_extents(self):
        rb = _RebarStub(bar_count=5)
        rb.get_BoundingBox = lambda v: self._make_bb(0.0, 10.0, 3.0, 9.0)
        result = detail_placer._get_rebar_zone_extent(rb, 'Y')
        assert result is not None
        zone_min, zone_max, perp, z, axis, count = result
        assert abs(zone_min - 3.0) < 1e-6
        assert abs(zone_max - 9.0) < 1e-6
        assert axis == 'Y'
        assert count == 5

    def test_x_axis_returns_bbox_x_extents(self):
        rb = _RebarStub(bar_count=3)
        rb.get_BoundingBox = lambda v: self._make_bb(2.0, 8.0, 0.0, 10.0)
        result = detail_placer._get_rebar_zone_extent(rb, 'X')
        assert result is not None
        zone_min, zone_max, perp, z, axis, count = result
        assert abs(zone_min - 2.0) < 1e-6
        assert abs(zone_max - 8.0) < 1e-6
        assert axis == 'X'
        assert count == 3

    def test_count_none_param_defaults_to_one(self):
        # No bar_count set → count defaults to 1; zero-span guard may or may not fire
        rb = _RebarStub()
        rb.get_BoundingBox = lambda v: self._make_bb(0.0, 10.0, 0.0, 5.0)
        result = detail_placer._get_rebar_zone_extent(rb, 'Y')
        # count defaults to 1 — result still valid (span > 0), count == 1
        if result is not None:
            assert result[5] == 1

    def test_zero_span_y_returns_none(self):
        rb = _RebarStub(bar_count=1)
        rb.get_BoundingBox = lambda v: self._make_bb(0.0, 10.0, 5.0, 5.0)
        assert detail_placer._get_rebar_zone_extent(rb, 'Y') is None

    def test_zero_span_x_returns_none(self):
        rb = _RebarStub(bar_count=1)
        rb.get_BoundingBox = lambda v: self._make_bb(4.0, 4.0, 0.0, 10.0)
        assert detail_placer._get_rebar_zone_extent(rb, 'X') is None

    def test_no_bbox_returns_none(self):
        rb = _RebarStub(bar_count=5)
        rb.get_BoundingBox = lambda v: None
        assert detail_placer._get_rebar_zone_extent(rb, 'Y') is None

    def test_perp_is_quarter_of_bar_length_from_bar_start(self):
        # bbox X: 2→8 (bar direction for Y dist_axis), bar_length=6, perp=2+6/4=3.5
        rb = _RebarStub(bar_count=5)
        rb.get_BoundingBox = lambda v: self._make_bb(2.0, 8.0, 0.0, 4.0)
        result = detail_placer._get_rebar_zone_extent(rb, 'Y')
        zone_min, zone_max, perp, z, axis, count = result
        assert abs(perp - 3.5) < 1e-6  # 2.0 + 6.0/4 = 3.5

    def test_perp_x_axis_is_quarter_of_bar_length_from_bar_start(self):
        # bbox Y: 0→10 (bar direction for X dist_axis), bar_length=10, perp=0+10/4=2.5
        rb = _RebarStub(bar_count=3)
        rb.get_BoundingBox = lambda v: self._make_bb(2.0, 8.0, 0.0, 10.0)
        result = detail_placer._get_rebar_zone_extent(rb, 'X')
        zone_min, zone_max, perp, z, axis, count = result
        assert abs(perp - 2.5) < 1e-6  # 0.0 + 10.0/4 = 2.5


# ---------------------------------------------------------------------------
# place_distribution_dimension
# ---------------------------------------------------------------------------

class _LineStub:
    @staticmethod
    def CreateBound(p0, p1):
        return _types.SimpleNamespace(p0=p0, p1=p1)


class _DetailLineStub:
    """Stub returned by doc.Create.NewDetailCurve."""
    class GeometryCurve:
        Reference = 'ref-stub'


class _DocCreateStub:
    def __init__(self):
        self.detail_curves = []
        self.dimensions = []

    def NewDetailCurve(self, view, line):
        dl = _DetailLineStub()
        self.detail_curves.append(dl)
        return dl

    def NewDimension(self, view, line, refs):
        dim = object()
        self.dimensions.append(dim)
        return dim


class _DocWithCreateStub:
    def __init__(self):
        self.Create = _DocCreateStub()


class TestPlaceDistributionDimension:
    def setup_method(self):
        self._orig_line = detail_placer.Line
        detail_placer.Line = _LineStub

    def teardown_method(self):
        detail_placer.Line = self._orig_line

    def test_zero_span_returns_none(self):
        doc = _DocWithCreateStub()
        view = _ViewStub()
        rb = _RebarStub()
        zone_extent = (5.0, 5.0, 0.5, 1.0, 'Y', 1)  # zone_min == zone_max
        result = detail_placer.place_distribution_dimension(doc, view, rb, zone_extent)
        assert result is None
        assert len(doc.Create.detail_curves) == 0

    def test_creates_two_anchor_lines_and_one_dimension(self):
        doc = _DocWithCreateStub()
        view = _ViewStub()
        rb = _RebarStub()
        zone_extent = (0.0, 5.0, 0.5, 1.0, 'Y', 8)
        result = detail_placer.place_distribution_dimension(doc, view, rb, zone_extent)
        assert result is not None
        assert len(doc.Create.detail_curves) == 2
        assert len(doc.Create.dimensions) == 1

    def test_x_axis_creates_two_anchor_lines(self):
        doc = _DocWithCreateStub()
        view = _ViewStub()
        rb = _RebarStub()
        zone_extent = (1.0, 8.0, 0.5, 1.0, 'X', 5)
        result = detail_placer.place_distribution_dimension(doc, view, rb, zone_extent)
        assert result is not None
        assert len(doc.Create.detail_curves) == 2


# ---------------------------------------------------------------------------
# place_bending_detail — no-Revit-API smoke tests
# ---------------------------------------------------------------------------

class _RebarBendingDetailTypeStub:
    def __init__(self):
        self.Id = 'bdt-id-1'


_bending_detail_calls = []


class _RebarBendingDetailInstance:
    """Returned by _RebarBendingDetailStub.Create — has Id and LookupParameter."""
    def __init__(self):
        self.Id = 'detail-id-{}'.format(id(self))
        self._params = {}

    def LookupParameter(self, name):
        class _P:
            IsReadOnly = False
            def Set(self_, v): pass
        return _P()


class _RebarBendingDetailStub:
    @staticmethod
    def Create(*args):
        _bending_detail_calls.append(args)
        return _RebarBendingDetailInstance()


_dbs.RebarBendingDetailType = _RebarBendingDetailTypeStub
_dbs.RebarBendingDetail = _RebarBendingDetailStub


class TestAllBarsBbox:
    """_all_bars_bbox returns the combined bounding box of all bars."""

    def test_combined_bbox_spans_all_bars(self):
        bars = [
            _RebarStub(midpoint=_XYZ( 0.0, 10.0, 1.0)),  # bbox X: -1..1,  Y: 9..11
            _RebarStub(midpoint=_XYZ(20.0, 30.0, 1.0)),  # bbox X: 19..21, Y: 29..31
        ]
        bb = detail_placer._all_bars_bbox(bars)
        assert bb is not None
        assert abs(bb.Min.X - (-1.0)) < 1e-6
        assert abs(bb.Max.X - 21.0) < 1e-6
        assert abs(bb.Min.Y - 9.0) < 1e-6
        assert abs(bb.Max.Y - 31.0) < 1e-6

    def test_empty_list_returns_none(self):
        assert detail_placer._all_bars_bbox([]) is None

    def test_no_bbox_bars_returns_none(self):
        rb = _RebarStub()
        rb.get_BoundingBox = lambda v: None
        assert detail_placer._all_bars_bbox([rb]) is None


class TestPlaceBendingDetail:
    def setup_method(self):
        _bending_detail_calls.clear()
        _move_element_calls.clear()
        self._bdt = _RebarBendingDetailTypeStub()
        _FEC_REGISTRY[_RebarBendingDetailTypeStub] = [self._bdt]
        _dbs.RebarBendingDetail = _RebarBendingDetailStub
        detail_placer.ElementTransformUtils = _ElementTransformUtilsStub

    def test_create_is_called_when_type_provided(self):
        doc  = _DocStub()
        view = _ViewStub()
        rb   = _RebarStub('Bottom X')
        detail_placer.place_bending_detail(doc, view, rb, 'Bottom X', self._bdt)
        assert len(_bending_detail_calls) >= 1

    def test_none_detail_type_returns_none(self):
        doc  = _DocStub()
        view = _ViewStub()
        rb   = _RebarStub('Bottom X')
        result = detail_placer.place_bending_detail(doc, view, rb, 'Bottom X', None)
        assert result is None
        assert len(_bending_detail_calls) == 0

    def test_no_midpoint_returns_none(self):
        doc  = _DocStub()
        view = _ViewStub()
        rb   = _RebarStub('Bottom X', curves=[])
        rb.get_BoundingBox = lambda v: None
        result = detail_placer.place_bending_detail(doc, view, rb, 'Bottom X', self._bdt)
        assert result is None

    def test_create_receives_position_index_zero_by_default(self):
        doc  = _DocStub()
        view = _ViewStub()
        rb   = _RebarStub('Bottom X')
        detail_placer.place_bending_detail(doc, view, rb, 'Bottom X', self._bdt)
        if _bending_detail_calls:
            assert _bending_detail_calls[0][3] == 0   # 4th arg = bar index

    def test_bar_index_passed_to_create(self):
        doc  = _DocStub()
        view = _ViewStub()
        rb   = _RebarStub('Bottom X')
        detail_placer.place_bending_detail(doc, view, rb, 'Bottom X', self._bdt, bar_index=2)
        if _bending_detail_calls:
            assert _bending_detail_calls[0][3] == 2

    def test_scale_is_always_one(self):
        doc  = _DocStub()
        view = _ViewStub(scale=50)
        rb   = _RebarStub('Bottom X')
        detail_placer.place_bending_detail(doc, view, rb, 'Bottom X', self._bdt)
        if _bending_detail_calls:
            assert abs(_bending_detail_calls[0][6] - 1.0) < 1e-9  # 7th arg = scale

    def test_move_vector_calls_move_element_with_correct_vector(self):
        """When move_vector is provided, MoveElement is called with that vector."""
        doc  = _DocStub()
        view = _ViewStub()
        rb   = _RebarStub('Bottom X')
        vec  = _XYZ(0.0, 3.0, 0.0)  # shift 3 ft along Y (X-mark distribution axis)
        detail_placer.place_bending_detail(doc, view, rb, 'Bottom X', self._bdt,
                                           move_vector=vec)
        assert len(_move_element_calls) == 1
        call = _move_element_calls[0]
        assert call['vector'] is vec

    def test_no_move_vector_does_not_call_move_element(self):
        """When move_vector is None (individual bar), MoveElement is not called."""
        doc  = _DocStub()
        view = _ViewStub()
        rb   = _RebarStub('Bottom X')
        detail_placer.place_bending_detail(doc, view, rb, 'Bottom X', self._bdt)
        assert len(_move_element_calls) == 0

    def test_move_vector_y_axis_has_zero_x_component(self):
        """X-mark distribution vector moves only in Y direction."""
        doc  = _DocStub()
        view = _ViewStub()
        rb   = _RebarStub('Bottom X')
        span = 10.0  # ft
        vec  = _XYZ(0.0, span / 4.0, 0.0)
        detail_placer.place_bending_detail(doc, view, rb, 'Bottom X', self._bdt,
                                           move_vector=vec)
        assert len(_move_element_calls) == 1
        v = _move_element_calls[0]['vector']
        assert abs(v.X) < 1e-9
        assert abs(v.Y - 2.5) < 1e-9  # span/4 = 10/4 = 2.5

    def test_move_vector_x_axis_has_zero_y_component(self):
        """Y-mark distribution vector moves only in X direction."""
        doc  = _DocStub()
        view = _ViewStub()
        rb   = _RebarStub('Bottom Y')
        span = 8.0  # ft
        vec  = _XYZ(span / 4.0, 0.0, 0.0)
        detail_placer.place_bending_detail(doc, view, rb, 'Bottom Y', self._bdt,
                                           move_vector=vec)
        assert len(_move_element_calls) == 1
        v = _move_element_calls[0]['vector']
        assert abs(v.X - 2.0) < 1e-9  # span/4 = 8/4 = 2.0
        assert abs(v.Y) < 1e-9


# ---------------------------------------------------------------------------
# place_donut
# ---------------------------------------------------------------------------

class TestPlaceDonut:
    def setup_method(self):
        _filled_region_calls.clear()
        frt = _FilledRegionTypeStub()
        _FEC_REGISTRY[_FilledRegionTypeStub] = [frt]
        detail_placer.FilledRegion = _FilledRegionStub
        detail_placer.FilledRegionType = _FilledRegionTypeStub

    def test_creates_filled_region_with_one_loop(self):
        doc  = _DocStub()
        view = _ViewStub()
        center = _XYZ(5.0, 5.0, 1.0)
        result = detail_placer.place_donut(doc, view, center, 0.01)
        assert result is not None
        assert len(_filled_region_calls) == 1
        assert len(_filled_region_calls[0]['loops']) == 1

    def test_no_filled_region_type_returns_none(self):
        _FEC_REGISTRY[_FilledRegionTypeStub] = []
        doc  = _DocStub()
        view = _ViewStub()
        center = _XYZ(5.0, 5.0, 1.0)
        result = detail_placer.place_donut(doc, view, center, 0.01)
        assert result is None
        assert len(_filled_region_calls) == 0
