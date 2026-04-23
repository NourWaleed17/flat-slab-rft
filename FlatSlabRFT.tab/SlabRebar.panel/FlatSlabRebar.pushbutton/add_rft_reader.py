# -*- coding: utf-8 -*-
"""Reader for additional rebar data from Revit detail groups.

Each detail group contains FamilyInstance detail components (DET-LINE-RFT-V3).
Key parameters read from each instance:
  - Label    : bar notation, e.g. 'T12-150' or 'T22-150+T12-150'
  - DIST.    : distribution zone size (vertical arrow), Revit LENGTH param
  - Bar X Visibility_Solid : which bar shape is active (A/B/C/D/E)
  - Bar Length X : arm length for the active bar shape, Revit LENGTH param

Geometry strategy (preferred over BasisX/BasisY math):
  The family instance geometry contains two key lines:
    - Bar line      : length ≈ bar_arm_ft, runs along the bar direction
    - Distribution line : length ≈ dist_ft, runs along the distribution direction
  Their world-coordinate endpoints are used directly as bar_start/bar_end and
  dist_start/dist_end, bypassing any BasisX/BasisY orientation ambiguity.
  If geometry is not accessible (e.g. in unit tests), a transform-based fallback
  computes the same values from Origin + -BasisY.
"""
from __future__ import print_function
import re

from Autodesk.Revit.DB import FamilyInstance, FilteredElementCollector
from Autodesk.Revit.DB.Structure import RebarBarType

MM_TO_FEET = 0.00328084

# Set to True to print one line per detail instance during add-rft reading.
# On a 275-instance run this adds measurable console I/O; keep False in production.
DEBUG_PER_INSTANCE = False

# Module-level diagnostic collector — populated by read_add_rft_group.
# Call get_last_group_diag() after each read_add_rft_group call to retrieve it.
_last_group_diag = []


def get_last_group_diag():
    """Return (and clear) the diagnostic lines from the most recent read_add_rft_group call."""
    lines = list(_last_group_diag)
    _last_group_diag[:] = []
    return lines


# ---------------------------------------------------------------------------
# Label parsing
# ---------------------------------------------------------------------------

def parse_label(label_str):
    """Parse a bar notation string into a list of (diameter_mm, spacing_mm).

    Examples
    --------
    'T12-150'           -> [(12, 150)]
    'T22-150+T12-150'   -> [(22, 150), (12, 150)]
    """
    result = []
    if not label_str:
        return result
    for part in label_str.split('+'):
        m = re.match(r'[A-Za-z]*(\d+)[- ]+(\d+)', part.strip())
        if m:
            result.append((int(m.group(1)), int(m.group(2))))
    return result


# ---------------------------------------------------------------------------
# Parameter helpers
# ---------------------------------------------------------------------------

def _read_number(element, name):
    """Return AsDouble() of a named parameter, or None if not found."""
    param = element.LookupParameter(name)
    if param is None:
        return None
    try:
        return param.AsDouble()
    except Exception:
        return None


def _read_string(element, name):
    """Return AsString() of a named parameter, or None if not found."""
    param = element.LookupParameter(name)
    if param is None:
        return None
    try:
        return param.AsString()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _try_add_line(obj, out_lines):
    """Append (start_xy, end_xy, 2d_length) to out_lines if obj is a straight line."""
    try:
        p0 = obj.GetEndPoint(0)
        p1 = obj.GetEndPoint(1)
        dx = p1.X - p0.X
        dy = p1.Y - p0.Y
        dz = p1.Z - p0.Z
        length_3d = (dx * dx + dy * dy + dz * dz) ** 0.5
        if length_3d < 1e-4:
            return
        # Skip lines that are mostly vertical in Z (not in-plan)
        if abs(dz) > length_3d * 0.3:
            return
        length_2d = (dx * dx + dy * dy) ** 0.5
        if length_2d < 1e-4:
            return
        out_lines.append(((p0.X, p0.Y), (p1.X, p1.Y), length_2d))
    except Exception:
        pass


def _get_instance_lines(family_instance):
    """Return list of (start_xy, end_xy, 2d_length) for all in-plan straight lines
    in the family instance's geometry.  Returns an empty list on failure.
    """
    lines = []
    try:
        from Autodesk.Revit.DB import Options
        doc  = family_instance.Document
        opts = Options()
        # Detail components need the owner view to expose their geometry.
        try:
            view_id = family_instance.OwnerViewId
            if view_id is not None:
                v = doc.GetElement(view_id)
                if v is not None:
                    opts.View = v
        except Exception:
            pass

        geom_elem = family_instance.get_Geometry(opts)
        if geom_elem is None:
            return lines

        for g in geom_elem:
            # GeometryInstance (most family instances) → get transformed sub-objects
            try:
                for obj in g.GetInstanceGeometry():
                    _try_add_line(obj, lines)
            except Exception:
                # Not a GeometryInstance — try directly
                _try_add_line(g, lines)
    except Exception:
        pass
    return lines


def _pick_line_by_length(lines, target, tol_ratio=0.15):
    """Return (start_xy, end_xy) of the line whose 2D length is closest to target,
    within tol_ratio * target tolerance.  Returns None if no match found.
    """
    tol = max(0.05, abs(target) * tol_ratio)
    best     = None
    best_diff = float('inf')
    for p0, p1, length in lines:
        diff = abs(length - target)
        if diff < tol and diff < best_diff:
            best_diff = diff
            best = (p0, p1)
    return best


# ---------------------------------------------------------------------------
# Single-item reader
# ---------------------------------------------------------------------------

def read_detail_item(family_instance):
    """Extract add-rft specs from one detail component FamilyInstance.

    Returns a list of spec dicts — one per bar set encoded in the Label
    (two dicts for a combined label like 'T22-150+T12-150').
    Returns an empty list if required parameters are missing or invalid.

    Spec dict keys
    --------------
    diam_mm    : int    bar diameter in mm
    spacing_ft : float  bar spacing in feet
    bar_start  : (x, y) world-coords start of bar arm (feet)
    bar_end    : (x, y) world-coords far end of bar arm (feet)
    dist_start : (x, y) world-coords start of distribution zone (feet)
    dist_end   : (x, y) world-coords end of distribution zone (feet)
    origin     : (x, y) insertion point (feet)
    dist_ft    : float  distribution zone size (feet)
    dist_dir   : (dx, dy) distribution direction unit vector (for debug)
    direction  : 'X' or 'Y'  — which world axis the bar runs along
    bar_arm_ft : float  active bar arm length in feet
    """
    try:
        label_str = _read_string(family_instance, 'Label')
        if not label_str:
            return []
        bar_sets = parse_label(label_str)
        if not bar_sets:
            return []

        # DIST. is a Revit LENGTH param — AsDouble() returns feet directly
        dist_ft = _read_number(family_instance, 'DIST.')
        if dist_ft is None or dist_ft <= 0:
            return []

        # Find the active bar arm length.  Multiple bars can be simultaneously
        # active (e.g. top U-bar families where Bar A Visibility_Solid=1 for the
        # short 500 mm return AND Bar C Visibility_Dash=1 for the 3850 mm main
        # span).  Collect ALL active arm lengths and select the LONGEST one — it
        # represents the main span (the bar run direction), not an end return.
        active_arms = []   # list of (length_ft, label)
        active_letters = []

        for _letter in ('A', 'B', 'C', 'D', 'E'):
            _active = False
            for _style in ('_Solid', '_Dash'):
                _vis = family_instance.LookupParameter(
                    'Bar {} Visibility{}'.format(_letter, _style)
                )
                if _vis is None:
                    continue
                try:
                    if _vis.AsInteger() == 1:
                        _active = True
                        break
                except Exception:
                    continue
            if not _active:
                continue
            active_letters.append(_letter)
            _lp = family_instance.LookupParameter('Bar Length {}'.format(_letter))
            if _lp is not None:
                try:
                    _v = _lp.AsDouble()
                    if _v is not None and _v > 0:
                        active_arms.append((_v, 'Bar Length {}'.format(_letter)))
                except Exception:
                    pass

        # Fallback: if no Bar Length found for any active bar, try generic dims
        if not active_arms and active_letters:
            for _arm_name in ('V_Top', 'H_Top', 'V_Bottom', 'H_Bottom'):
                _lp2 = family_instance.LookupParameter(_arm_name)
                if _lp2 is not None:
                    try:
                        _v = _lp2.AsDouble()
                        if _v > 0:
                            active_arms.append((_v, _arm_name))
                            break
                    except Exception:
                        pass

        if not active_arms:
            return []

        # Longest active arm = main bar span (Bar C).
        # Bar D/B (vertical leg) and Bar E/A (return) are shorter and never win.
        bar_arm_ft = max(arm for arm, _ in active_arms)

        # Determine which config is active, then read the return length from the
        # correct parameter:
        #   Config A (C+D+E): vertical leg = Bar D, return = Bar E
        #   Config B (C+B+A): vertical leg = Bar B, return = Bar A
        if 'D' in active_letters:
            _return_param = 'E'
        elif 'B' in active_letters:
            _return_param = 'A'
        else:
            _return_param = None   # no vertical leg → straight bar, do not read return length

        leg_ft = 0.0
        if _return_param is not None:
            _lp_ret = family_instance.LookupParameter('Bar Length {}'.format(_return_param))
            if _lp_ret is not None:
                try:
                    _v_ret = _lp_ret.AsDouble()
                    if _v_ret > 0:
                        leg_ft = _v_ret
                except Exception:
                    pass

        has_hook = _return_param is not None and leg_ft > 0

        _last_group_diag.append(
            '    active_letters={} arms={} -> bar_arm_ft={:.4f}ft '
            'return_param={} leg_ft={:.4f}ft has_hook={}'.format(
                active_letters,
                [('{:.3f}'.format(a), lbl) for a, lbl in active_arms],
                bar_arm_ft,
                _return_param if _return_param else 'None(straight)',
                leg_ft,
                has_hook,
            )
        )
        if DEBUG_PER_INSTANCE:
            try:
                _inst_id = family_instance.Id.IntegerValue
            except Exception:
                _inst_id = '?'
            print('[add_rft_reader] instance={} active_letters={} has_hook={} '
                  'leg_ft={:.4f}ft return_param={}'.format(
                      _inst_id,
                      active_letters,
                      has_hook,
                      leg_ft,
                      _return_param if _return_param else 'None',
                  ))

        transform = family_instance.GetTransform()
        origin    = transform.Origin
        bar_dir   = transform.BasisX   # family local-X = bar direction
        dist_dir_basis = transform.BasisY  # family local-Y

        # Which world axis does the bar run along?
        direction = 'X' if abs(bar_dir.X) > abs(bar_dir.Y) else 'Y'

        # hook_at_max: BasisX always points from origin (straight end) toward the
        # hook end.  So if BasisX.X > 0 for an X-bar the hook is at the higher X
        # coordinate (vary_max).  This is more reliable than endpoint distance
        # ordering because it comes directly from the family transform.
        if direction == 'X':
            hook_at_max = bar_dir.X > 0
        else:
            hook_at_max = bar_dir.Y > 0

        # ------------------------------------------------------------------
        # Try to read actual line geometry for precise endpoint locations.
        # This is orientation-independent: it doesn't matter how the family
        # is rotated or mirrored; the world-coord endpoints are always right.
        # ------------------------------------------------------------------
        lines = _get_instance_lines(family_instance)
        _last_group_diag.append(
            '    geom_lines={} lens=[{}]'.format(
                len(lines),
                ', '.join('{:.3f}'.format(l) for _, _, l in lines)
            )
        )

        bar_pts  = _pick_line_by_length(lines, bar_arm_ft)
        dist_pts = _pick_line_by_length(lines, dist_ft)
        _last_group_diag.append(
            '    bar_pts_found={} dist_pts_found={}'.format(
                bar_pts is not None, dist_pts is not None
            )
        )

        if bar_pts is not None:
            pt_a, pt_b = bar_pts[0], bar_pts[1]
            # Ensure bar_start is the endpoint closest to origin (the straight/
            # insertion end) and bar_end is the far end (hook end).
            _ox, _oy = origin.X, origin.Y
            _da2 = (pt_a[0] - _ox) ** 2 + (pt_a[1] - _oy) ** 2
            _db2 = (pt_b[0] - _ox) ** 2 + (pt_b[1] - _oy) ** 2
            if _da2 <= _db2:
                bar_start, bar_end = pt_a, pt_b
            else:
                bar_start, bar_end = pt_b, pt_a
        else:
            # Fallback: origin + arm * BasisX
            bar_start = (origin.X, origin.Y)
            bar_end   = (
                origin.X + bar_arm_ft * bar_dir.X,
                origin.Y + bar_arm_ft * bar_dir.Y,
            )

        if dist_pts is not None:
            dist_start = dist_pts[0]
            dist_end   = dist_pts[1]
            # Derive dist_dir for debug output
            ddx = dist_end[0] - dist_start[0]
            ddy = dist_end[1] - dist_start[1]
            _len = (ddx * ddx + ddy * ddy) ** 0.5 or 1.0
            dist_dir_world = (ddx / _len, ddy / _len)
        else:
            # Fallback: distribution arrow is in -BasisY direction from origin
            dist_dir_world = (-dist_dir_basis.X, -dist_dir_basis.Y)
            dist_start = (origin.X, origin.Y)
            dist_end   = (
                origin.X + dist_ft * dist_dir_world[0],
                origin.Y + dist_ft * dist_dir_world[1],
            )

        specs = []
        for diam_mm, spacing_mm in bar_sets:
            if spacing_mm <= 0:
                continue
            specs.append({
                'diam_mm':      diam_mm,
                'spacing_ft':   spacing_mm * MM_TO_FEET,
                'bar_start':    bar_start,
                'bar_end':      bar_end,
                'dist_start':   dist_start,
                'dist_end':     dist_end,
                'origin':       (origin.X, origin.Y),
                'dist_ft':      dist_ft,
                'dist_dir':     dist_dir_world,
                'direction':    direction,
                'bar_arm_ft':   bar_arm_ft,
                'leg_ft':       leg_ft,
                'has_hook':     has_hook,
                'hook_at_max':  hook_at_max,
                'return_param': _return_param,   # debug: 'E' for Config A, 'A' for Config B, None for straight
            })
        return specs

    except Exception:
        return []


# ---------------------------------------------------------------------------
# Diagnostic helper
# ---------------------------------------------------------------------------

def _diag_read_detail_item(family_instance):
    """Step through read_detail_item logic and collect diagnostic lines."""
    prefix = '    [diag]'

    def _log(msg):
        line = '{} {}'.format(prefix, msg)
        _last_group_diag.append(line)
        print(line)

    try:
        eid = family_instance.Id.IntegerValue
    except Exception:
        eid = '?'
    _log('--- FamilyInstance id={} ---'.format(eid))

    # 1. Label
    try:
        label_str = _read_string(family_instance, 'Label')
        _log('Label={!r}'.format(label_str))
        if not label_str:
            _log('FAIL: Label is empty or None')
            return
        bar_sets = parse_label(label_str)
        _log('bar_sets={}'.format(bar_sets))
        if not bar_sets:
            _log('FAIL: parse_label returned empty list')
            return
    except Exception as e:
        _log('FAIL reading Label: {}'.format(e))
        return

    # 2. DIST.
    try:
        dist_ft = _read_number(family_instance, 'DIST.')
        _log('DIST.={} ft'.format(dist_ft))
        if dist_ft is None or dist_ft <= 0:
            _log('FAIL: DIST. is None or <= 0')
            return
    except Exception as e:
        _log('FAIL reading DIST.: {}'.format(e))
        return

    # 3. Bar Visibility / Bar Length — collect all active arms, pick longest
    _diag_arms = []
    _active_letters = []
    for _letter in ('A', 'B', 'C', 'D', 'E'):
        _active = False
        for _style in ('_Solid', '_Dash'):
            try:
                _vis = family_instance.LookupParameter(
                    'Bar {} Visibility{}'.format(_letter, _style)
                )
                if _vis is None:
                    continue
                vis_val = _vis.AsInteger()
                _log('Bar {} Visibility{}={}'.format(_letter, _style, vis_val))
                if vis_val == 1:
                    _active = True
                    break
            except Exception as e:
                _log('FAIL reading Bar {} Visibility{}: {}'.format(_letter, _style, e))
        if not _active:
            continue
        _active_letters.append(_letter)
        _lp = family_instance.LookupParameter('Bar Length {}'.format(_letter))
        if _lp is not None:
            try:
                _v = _lp.AsDouble()
                if _v is not None and _v > 0:
                    _diag_arms.append((_v, 'Bar Length {}'.format(_letter)))
                    _log('Bar Length {}={:.4f} ft  <-- active arm'.format(_letter, _v))
            except Exception:
                pass
    if not _diag_arms and _active_letters:
        for _arm_name in ('V_Top', 'H_Top', 'V_Bottom', 'H_Bottom'):
            _lp2 = family_instance.LookupParameter(_arm_name)
            if _lp2 is not None:
                try:
                    _v = _lp2.AsDouble()
                    if _v > 0:
                        _diag_arms.append((_v, _arm_name))
                        _log('{}={:.4f} ft  <-- arm fallback'.format(_arm_name, _v))
                        break
                except Exception:
                    pass

    if _diag_arms:
        bar_arm_ft = max(arm for arm, _ in _diag_arms)
        _log('bar_arm_ft={:.4f} ft (max of {} active arms)'.format(bar_arm_ft, len(_diag_arms)))
    else:
        bar_arm_ft = None

    if bar_arm_ft is None or bar_arm_ft <= 0:
        _log('FAIL: no active bar arm found (bar_arm_ft={})'.format(bar_arm_ft))
        # Dump all parameter names that contain 'Bar' or 'Visibility' to find the right name
        try:
            for p in family_instance.Parameters:
                try:
                    pname = p.Definition.Name
                    if any(kw in pname for kw in ('Bar', 'Visibility', 'Solid', 'Length')):
                        try:
                            pval = p.AsInteger()
                        except Exception:
                            try:
                                pval = p.AsDouble()
                            except Exception:
                                pval = p.AsString()
                        _log('  param: {!r} = {}'.format(pname, pval))
                except Exception:
                    pass
        except Exception as e:
            _log('  could not iterate parameters: {}'.format(e))
        return

    # 4. Transform
    try:
        transform = family_instance.GetTransform()
        origin = transform.Origin
        _log('origin=({:.3f}, {:.3f}, {:.3f})'.format(origin.X, origin.Y, origin.Z))
        bar_dir = transform.BasisX
        _log('BasisX=({:.3f}, {:.3f}, {:.3f})'.format(bar_dir.X, bar_dir.Y, bar_dir.Z))
    except Exception as e:
        _log('FAIL reading transform: {}'.format(e))
        return

    # 5. Geometry lines
    try:
        lines = _get_instance_lines(family_instance)
        _log('geometry lines found: {}'.format(len(lines)))
        for i, (p0, p1, length) in enumerate(lines):
            _log('  line[{}] len={:.4f} ft'.format(i, length))
    except Exception as e:
        _log('FAIL reading geometry: {}'.format(e))

    _log('--- all checks passed, read_detail_item should succeed ---')


# ---------------------------------------------------------------------------
# Group reader
# ---------------------------------------------------------------------------

def read_add_rft_group(detail_group, mesh_layer, direction_hint=None):
    """Read all add-rft specs from a Revit detail group.

    Parameters
    ----------
    detail_group   : Revit Group element
    mesh_layer     : 'top' or 'bottom'
    direction_hint : 'X' or 'Y' chosen by the user in the UI (optional).
                     Stored on each spec for debugging; the actual direction is
                     auto-detected from BasisX in read_detail_item.

    Returns list of spec dicts with 'mesh_layer' and 'direction_hint' keys added.
    """
    _last_group_diag[:] = []
    specs = []
    fi_count = 0
    non_fi_types = {}
    first_fi = None
    try:
        doc = detail_group.Document
        for eid in detail_group.GetMemberIds():
            elem = doc.GetElement(eid)
            if not isinstance(elem, FamilyInstance):
                # Track what types ARE in the group for diagnostics
                try:
                    tname = type(elem).__name__
                except Exception:
                    tname = 'unknown'
                non_fi_types[tname] = non_fi_types.get(tname, 0) + 1
                continue
            fi_count += 1
            if first_fi is None:
                first_fi = elem
            item_specs = read_detail_item(elem)
            for s in item_specs:
                s['mesh_layer']     = mesh_layer
                s['direction_hint'] = direction_hint
            specs.extend(item_specs)
    except Exception as _e:
        msg = '  [grp err] {}'.format(_e)
        _last_group_diag.append(msg)
        print('[add_rft] read_add_rft_group error: {}'.format(_e))

    _last_group_diag.append('  fi_count={} non_fi={} specs={}'.format(
        fi_count, non_fi_types, len(specs)
    ))
    print('[add_rft] group fi_count={} non_fi={} specs={}'.format(
        fi_count, non_fi_types, len(specs)
    ))
    if fi_count > 0 and len(specs) == 0 and first_fi is not None:
        # Diagnose why read_detail_item returned nothing for the first instance
        _diag_read_detail_item(first_fi)
    elif fi_count == 0 and non_fi_types:
        _last_group_diag.append('  WARNING: no FamilyInstances in group — element types: {}'.format(non_fi_types))
    return specs


# ---------------------------------------------------------------------------
# Bar-row generator
# ---------------------------------------------------------------------------

def generate_add_rft_rows(specs, z_bottom_x, z_bottom_y, z_top_x, z_top_y):
    """Convert add-rft specs to bar_row dicts for obstacle_processor.

    Each spec produces one bar_row per bar position along the distribution zone.
    The zone is defined by dist_start and dist_end (world-coord endpoints of the
    distribution indicator line), so bar positions are orientation-independent.

    Returns list of bar_row dicts.
    """
    rows = []
    for spec_idx, spec in enumerate(specs):
        direction  = spec['direction']
        spacing_ft = spec['spacing_ft']
        bar_start  = spec['bar_start']
        bar_end    = spec['bar_end']
        mesh_layer = spec.get('mesh_layer', 'bottom')
        diam_mm    = spec['diam_mm']
        leg_ft     = spec.get('leg_ft', 0.0)

        if spacing_ft <= 0:
            continue

        # Z elevation — same plane as the corresponding main mesh layer
        if mesh_layer == 'bottom':
            z = z_bottom_x if direction == 'X' else z_bottom_y
        else:
            z = z_top_x if direction == 'X' else z_top_y

        # Bar vary range along its run direction.
        if direction == 'X':
            vary_min = min(bar_start[0], bar_end[0])
            vary_max = max(bar_start[0], bar_end[0])
        else:
            vary_min = min(bar_start[1], bar_end[1])
            vary_max = max(bar_start[1], bar_end[1])

        # hook_at_max: prefer the value computed in read_detail_item from BasisX
        # (more reliable than endpoint comparison, correctly handles mirrored families).
        # Fall back to endpoint comparison for backward-compat with manually built specs.
        if 'hook_at_max' in spec:
            hook_at_max = spec['hook_at_max']
        elif direction == 'X':
            hook_at_max = bar_end[0] > bar_start[0]
        else:
            hook_at_max = bar_end[1] > bar_start[1]

        if vary_min >= vary_max:
            continue

        _last_group_diag.append(
            '    spec[{}]: dir={} bar_arm={:.3f}ft vary=[{:.3f},{:.3f}]ft leg_ft={:.3f}ft hook_at_max={}'.format(
                spec_idx, direction, spec.get('bar_arm_ft', 0),
                vary_min, vary_max, leg_ft, hook_at_max
            )
        )

        # ----------------------------------------------------------------
        # Distribution zone: use dist_start / dist_end endpoints if present;
        # fall back to origin + dist_dir * dist_ft for backward compatibility.
        # ----------------------------------------------------------------
        dist_start = spec.get('dist_start')
        dist_end   = spec.get('dist_end')
        if dist_start is None or dist_end is None:
            # Backward-compat path (used in unit tests with manually built specs)
            origin   = spec['origin']
            dist_dir = spec['dist_dir']
            dist_ft  = spec['dist_ft']
            dist_start = origin
            dist_end   = (
                origin[0] + dist_ft * dist_dir[0],
                origin[1] + dist_ft * dist_dir[1],
            )

        # Fixed-val axis: Y for X-direction bars, X for Y-direction bars
        if direction == 'X':
            fv_a = dist_start[1]
            fv_b = dist_end[1]
        else:
            fv_a = dist_start[0]
            fv_b = dist_end[0]

        fv_min = min(fv_a, fv_b)
        fv_max = max(fv_a, fv_b)

        if fv_min >= fv_max:
            continue

        # Generate one row per spacing step across the distribution zone
        n = 0
        while True:
            fv = fv_min + n * spacing_ft
            if fv > fv_max + 1e-6:
                break
            rows.append({
                'fixed_val':   fv,
                'vary_min':    vary_min,
                'vary_max':    vary_max,
                'direction':   direction,
                'index':       spec_idx * 100000 + n,
                'z':           z,
                'diam_mm':     diam_mm,
                'mesh_layer':  mesh_layer,
                'no_hooks':    True,
                'skip_dp':     True,   # add RFT bars cross drop panels at full length
                'is_add_rft':  True,
                'spacing_ft':  spacing_ft,
                'leg_ft':      leg_ft,
                'has_hook':    spec.get('has_hook', False),
                'hook_at_max': hook_at_max,
            })
            n += 1

    return rows


# ---------------------------------------------------------------------------
# Bar-type lookup
# ---------------------------------------------------------------------------

def find_bar_type_by_diameter(doc, diameter_mm):
    """Return the RebarBarType whose nominal diameter is closest to diameter_mm.

    Returns None if no RebarBarType elements exist in the document.
    """
    target_ft = diameter_mm * MM_TO_FEET
    best      = None
    best_diff = float('inf')

    for bt in FilteredElementCollector(doc).OfClass(RebarBarType).ToElements():
        try:
            param = bt.LookupParameter('Bar Diameter')
            if param is None:
                param = bt.LookupParameter('Nominal Diameter')
            if param is None:
                continue
            diff = abs(param.AsDouble() - target_ft)
            if diff < best_diff:
                best_diff = diff
                best = bt
        except Exception:
            continue

    return best
