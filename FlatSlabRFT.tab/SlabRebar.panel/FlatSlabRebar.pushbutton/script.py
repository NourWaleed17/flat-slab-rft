# -*- coding: utf-8 -*-
"""Entry point for the Flat Slab Auto Reinforcement pyRevit button."""
from __future__ import print_function

from pyrevit import forms, revit, DB
from Autodesk.Revit.DB import UpdaterRegistry
from Autodesk.Revit.DB.Structure import Rebar, RebarStyle, RebarHookOrientation


def _disable_third_party_updaters():
    """Temporarily disable updaters from third-party plugins (e.g. SOFiSTiK)
    that interfere with rebar placement by locking the document on failure.
    Returns a list of UpdaterIds that were disabled so they can be re-enabled.
    """
    disabled = []
    try:
        infos = UpdaterRegistry.GetRegisteredUpdaterInfos()
        for info in infos:
            try:
                name = str(info.UpdaterName).lower()
                if 'sofistik' in name or 'sofist' in name:
                    UpdaterRegistry.DisableUpdater(info.UpdaterId)
                    disabled.append(info.UpdaterId)
                    print('[FlatSlabRFT] Disabled updater: {}'.format(info.UpdaterName))
            except Exception:
                pass
    except Exception:
        pass
    return disabled


def _restore_updaters(disabled_ids):
    """Re-enable updaters that were disabled before placement."""
    for uid in disabled_ids:
        try:
            UpdaterRegistry.EnableUpdater(uid)
        except Exception:
            pass

import ui
import geometry
import bar_generator

_DEFAULT_COVER_MM  = 25.0
_DEFAULT_COVER_FT  = _DEFAULT_COVER_MM * 0.00328084


def _read_floor_cover(doc, floor):
    """Read reinforcement cover distances from the floor's Revit cover parameters.

    Tries available built-in cover parameters across Revit versions.
    Each stores an ElementId of a RebarCoverType whose
    CoverDistance property gives the actual value in internal feet.

    Returns (cover_bottom_ft, cover_top_ft).  Falls back to _DEFAULT_COVER_FT
    when a parameter is missing, zero, or the RebarCoverType cannot be found.
    """
    from Autodesk.Revit.DB import BuiltInParameter

    def _pick_bip(candidates):
        for name in candidates:
            try:
                bip = getattr(BuiltInParameter, name)
                return bip, name
            except Exception:
                continue
        return None, None

    def _cover_for_param(bip):
        try:
            if bip is None:
                return None
            param = floor.get_Parameter(bip)
            if param is None:
                return None
            eid = param.AsElementId()
            if eid is None:
                return None
            elem = doc.GetElement(eid)
            if elem is None:
                return None
            dist = elem.CoverDistance   # feet (Revit internal units)
            return dist if dist and dist > 1e-6 else None
        except Exception:
            return None

    bot_bip, bot_bip_name = _pick_bip([
        'CONCRETE_COVER_BOTTOM_FACE',
        'CLEAR_COVER_BOTTOM',
        'CLEAR_COVER_OTHER',
    ])
    top_bip, top_bip_name = _pick_bip([
        'CONCRETE_COVER_TOP_FACE',
        'CLEAR_COVER_TOP',
        'CLEAR_COVER_OTHER',
    ])

    bot = _cover_for_param(bot_bip) or _DEFAULT_COVER_FT
    top = _cover_for_param(top_bip) or _DEFAULT_COVER_FT

    if bot_bip_name is None or top_bip_name is None:
        print('[FlatSlabRFT] Cover parameter fallback in use (bottom={}, top={})'.format(
            bot_bip_name or 'default', top_bip_name or 'default'
        ))

    print('[FlatSlabRFT] Cover read from floor: bottom={:.0f}mm  top={:.0f}mm'.format(
        bot * 304.8, top * 304.8
    ))
    return bot, top
import obstacle_processor
import splice_processor
import rebar_placer
import dp_rebar_placer
import debug_preview
import add_rft_reader


def main():
    # ------------------------------------------------------------------
    # 1. User selects the main slab
    # ------------------------------------------------------------------
    floor = revit.pick_element('Select the flat slab floor element')
    if floor is None:
        forms.alert('No element selected. Exiting.', title='Cancelled')
        return

    # Verify it is a Floor element
    if not isinstance(floor, DB.Floor):
        forms.alert('Selected element is not a Floor. Please select the flat slab.', title='Error')
        return

    # ------------------------------------------------------------------
    # 2. Read cover from the floor element, then collect remaining inputs
    # ------------------------------------------------------------------
    cover_bottom_ft, cover_top_ft = _read_floor_cover(revit.doc, floor)

    params = ui.collect_inputs(revit.doc, revit.active_view)
    params['stagger_splices'] = True

    # Inject layer-specific covers read from Revit (override UI if present).
    params['cover_bottom'] = cover_bottom_ft
    params['cover_top']    = cover_top_ft
    # 'cover' kept as the minimum of the two for backward-compat code paths
    # (obstacle_processor lateral margin, splice hook_ext, DP placer).
    params['cover'] = min(cover_bottom_ft, cover_top_ft)

    # ------------------------------------------------------------------
    # 3. Extract slab geometry
    # ------------------------------------------------------------------
    print('[FlatSlabRFT] Reading slab geometry...')
    slab_data = geometry.get_slab_data(floor)
    print('[FlatSlabRFT] Slab: thickness={:.0f}mm  shafts+openings...'.format(
        slab_data['thickness'] * 304.8))

    # Combine sketch voids + shaft openings → treated the same (hooks at both sides)
    shaft_polygons_sketch   = slab_data['sketch_void_polygons']
    shaft_polygons_openings = geometry.get_shaft_opening_polygons(
        revit.doc,
        slab_data['bbox'],
        slab_data['top_z'],
        main_floor_id=floor.Id,
        slab_bottom_z=slab_data['bottom_z']
    )
    shaft_polygons = shaft_polygons_sketch + shaft_polygons_openings
    print('[FlatSlabRFT] Shafts found: {} (sketch:{} openings:{})  Scanning drop panels...'.format(
        len(shaft_polygons), len(shaft_polygons_sketch), len(shaft_polygons_openings)))

    dp_data_list = geometry.get_drop_panel_data(
        revit.doc,
        slab_data['top_z'],
        main_floor_id=floor.Id,
        slab_bbox=slab_data['bbox'],
        slab_bottom_z=slab_data['bottom_z'],
        slab_polygon=slab_data['outer_polygon'],
        slab_thickness=slab_data['thickness']
    )
    dp_debug = geometry.get_last_dp_debug_info()
    print('[FlatSlabRFT] Drop panels detected: {}  Scanning supports...'.format(len(dp_data_list)))

    support_positions = geometry.get_support_positions_2d(
        revit.doc,
        slab_data['bbox'],
        (slab_data['bottom_z'], slab_data['top_z'])
    )
    print('[FlatSlabRFT] Supports found: {}  Joining DP floors...'.format(len(support_positions)))

    # Ensure all detected DPs are joined with the slab floor, DP winning the join.
    join_stats = geometry.ensure_dp_joins(revit.doc, floor, dp_data_list)
    print('[FlatSlabRFT] DP joins done (new:{} switched:{})'.format(
        join_stats.get('joined_new', 0), join_stats.get('switched', 0)))

    run_mode       = params.get('run_mode', 'Place Directly')
    placement_type = params.get('placement_type', 'Both')
    place_mesh     = placement_type in ('Mesh RFT', 'Both')
    place_add_rft  = placement_type in ('Add RFT',  'Both')

    if run_mode == 'Place DP Only':
        dp_place_stats = dp_rebar_placer.place_all_dp_bars(
            revit.doc, dp_data_list, params,
            shaft_polygons=shaft_polygons,
            slab_polygon=slab_data['outer_polygon'],
        )
        msg = (
            'Drop-panel-only reinforcement placed.\n\n'
            'Drop panels processed : {}\n\n'
            'Join fix stats:\n'
            '  Already correct : {}\n'
            '  Newly joined    : {}\n'
            '  Order switched  : {}\n'
            '  Failed          : {}\n\n'
            'DP placement stats:\n'
            '  X rows              : {}\n'
            '  Y rows              : {}\n'
            '  X rows shifted mode : {}\n'
            '  Y rows shifted mode : {}\n'
            '  X bars total        : {}\n'
            '  Y bars total        : {}\n'
            '  X staple            : {}\n'
            '  Y staple            : {}\n'
            '  X straight fallback : {}\n'
            '  Y straight fallback : {}\n'
            '  X failed            : {}\n'
            '  Y failed            : {}\n'
            '  Staple OK           : {}\n'
            '  Straight primary    : {}\n'
            '  Fallback straight   : {}\n'
            '  Too short skipped   : {}\n'
            '  Regen failed        : {}\n\n'
            'DP detection (safe mode):\n'
            '  Relaxed retry used : {}\n'
            '  Top tol used (mm)  : {}\n'
            '  Thick tol used (mm): {}\n'
            '  Closest top dZ (mm): {}\n'
            '  Max top-below (mm) : {}\n'
            '  Floors scanned      : {}\n'
            '  Accepted            : {}\n'
            '  Accepted top-below  : {}\n'
            '  Rejected top mismatch: {}\n'
            '  Rejected not thicker: {}\n'
            '  Rejected no overlap : {}\n'
            '  Rejected outside slab: {}\n'
            '  Rejected no sketch  : {}\n'
            '  Rejected no profile : {}'
        ).format(
            len(dp_data_list),
            join_stats.get('already_correct', 0),
            join_stats.get('joined_new', 0),
            join_stats.get('switched', 0),
            join_stats.get('failed', 0),
            dp_place_stats.get('x_rows', 0),
            dp_place_stats.get('y_rows', 0),
            dp_place_stats.get('x_rows_shifted', 0),
            dp_place_stats.get('y_rows_shifted', 0),
            dp_place_stats.get('x_total', 0),
            dp_place_stats.get('y_total', 0),
            dp_place_stats.get('x_staple', 0),
            dp_place_stats.get('y_staple', 0),
            dp_place_stats.get('x_straight', 0),
            dp_place_stats.get('y_straight', 0),
            dp_place_stats.get('x_failed', 0),
            dp_place_stats.get('y_failed', 0),
            dp_place_stats.get('staple_ok', 0),
            dp_place_stats.get('straight_primary', 0),
            dp_place_stats.get('fallback_straight', 0),
            dp_place_stats.get('too_short_skipped', 0),
            dp_place_stats.get('regen_failed', 0),
            dp_debug.get('used_relaxed_retry', 0),
            dp_debug.get('top_z_tolerance_mm', 0),
            dp_debug.get('thickness_tolerance_mm', 0),
            dp_debug.get('closest_top_delta_mm', 'n/a'),
            dp_debug.get('max_allowed_top_below_mm', 'n/a'),
            dp_debug.get('floors_scanned', 0),
            dp_debug.get('accepted', 0),
            dp_debug.get('accepted_top_below_slab', 0),
            dp_debug.get('rejected_top_mismatch', 0),
            dp_debug.get('rejected_not_thicker', 0),
            dp_debug.get('rejected_no_overlap', 0),
            dp_debug.get('rejected_outside_slab', 0),
            dp_debug.get('rejected_no_sketch', 0),
            dp_debug.get('rejected_no_profile', 0),
        )
        forms.alert(msg, title='Done')
        return

    # Expose slab top Z to dp_rebar_placer via params
    params['slab_top_z'] = slab_data['top_z']
    params['slab_bottom_z'] = slab_data['bottom_z']
    params['slab_thickness'] = slab_data['thickness']

    # ------------------------------------------------------------------
    # 4–7. Main mesh bar rows, Z elevations, obstacle + splice splits
    # ------------------------------------------------------------------
    bbox         = slab_data['bbox']
    cover        = params['cover']          # min(bottom, top) — lateral bar inset
    cover_bottom = params['cover_bottom']
    cover_top    = params['cover_top']
    diameter     = params['diameter']
    top_z        = slab_data['top_z']
    bottom_z     = slab_data['bottom_z']

    z_bottom_x = bottom_z + cover_bottom
    z_bottom_y = z_bottom_x + diameter
    z_top_x    = top_z - cover_top
    z_top_y    = z_top_x - diameter

    all_segments   = []
    final_segments = []

    if place_mesh:
        spacing = params['spacing']
        print('[FlatSlabRFT] Generating mesh bar rows (spacing={:.0f}mm)...'.format(spacing * 304.8))

        bottom_x_rows = bar_generator.generate_bar_rows(bbox, spacing, cover, 'X')
        bottom_y_rows = bar_generator.generate_bar_rows(bbox, spacing, cover, 'Y')
        top_x_rows    = bar_generator.generate_bar_rows(bbox, spacing, cover, 'X')
        top_y_rows    = bar_generator.generate_bar_rows(bbox, spacing, cover, 'Y')

        # Pre-compute obstacle bounding boxes once for all 4 × N row loops.
        # Each row will skip the full polygon test for obstacles whose bbox
        # doesn't straddle the current scanline (O(1) vs O(polygon_vertices)).
        _obstacle_cache = obstacle_processor.build_obstacle_cache(shaft_polygons, dp_data_list)

        print('[FlatSlabRFT] Clipping Bottom X ({} rows)...'.format(len(bottom_x_rows)))
        for row in bottom_x_rows:
            row['z'] = z_bottom_x
            all_segments.extend(obstacle_processor.process_bar_row(
                row, slab_data['outer_polygon'], shaft_polygons, dp_data_list, params, 'bottom',
                obstacle_cache=_obstacle_cache
            ))
        print('[FlatSlabRFT] Clipping Bottom Y ({} rows)...'.format(len(bottom_y_rows)))
        for row in bottom_y_rows:
            row['z'] = z_bottom_y
            all_segments.extend(obstacle_processor.process_bar_row(
                row, slab_data['outer_polygon'], shaft_polygons, dp_data_list, params, 'bottom',
                obstacle_cache=_obstacle_cache
            ))
        print('[FlatSlabRFT] Clipping Top X ({} rows)...'.format(len(top_x_rows)))
        for row in top_x_rows:
            row['z'] = z_top_x
            all_segments.extend(obstacle_processor.process_bar_row(
                row, slab_data['outer_polygon'], shaft_polygons, dp_data_list, params, 'top',
                obstacle_cache=_obstacle_cache
            ))
        print('[FlatSlabRFT] Clipping Top Y ({} rows)...'.format(len(top_y_rows)))
        for row in top_y_rows:
            row['z'] = z_top_y
            all_segments.extend(obstacle_processor.process_bar_row(
                row, slab_data['outer_polygon'], shaft_polygons, dp_data_list, params, 'top',
                obstacle_cache=_obstacle_cache
            ))

        print('[FlatSlabRFT] {} raw segments → processing splices...'.format(len(all_segments)))
        final_segments = splice_processor.process_splices(
            all_segments, params, support_positions=support_positions
        )
        print('[FlatSlabRFT] {} final mesh segments ready.'.format(len(final_segments)))

    # ------------------------------------------------------------------
    # 7b. Stage 3 — Additional rebar from detail groups
    # ------------------------------------------------------------------
    # Collect segments grouped by diameter for later per-diameter placement.
    # Each key is diameter_mm (int); value is a list of spliced segments.
    add_rft_by_diam  = {}   # {diam_mm: [segments after splice]}
    add_rft_all_segs = []   # flat list for preview
    _add_rft_debug   = []   # one string per group processed

    # Build a unified list of (group, layer) pairs from either source:
    #   - 'add_rft_entries'      used by Add RFT mode (loop-picked groups)
    #   - 'add_rft_bottom_group' / 'add_rft_top_group'  used by Both mode
    _group_layer_pairs = []
    for _entry in params.get('add_rft_entries') or []:
        _group_layer_pairs.append((
            _entry['group'],
            _entry['layer'],
            _entry.get('direction'),
        ))
    # Only fall back to legacy keys if add_rft_entries produced nothing.
    # Both-mode sets both add_rft_entries AND the legacy keys; processing both
    # would place every group twice.
    if not _group_layer_pairs:
        _bg = params.get('add_rft_bottom_group')
        if _bg is not None:
            _group_layer_pairs.append((_bg, 'bottom', None))
        _tg = params.get('add_rft_top_group')
        if _tg is not None:
            _group_layer_pairs.append((_tg, 'top', None))

    _add_rft_debug.append('groups: {}'.format(len(_group_layer_pairs)))

    for _group, _mesh_layer, _direction_hint in _group_layer_pairs:
        try:
            _member_ids = list(_group.GetMemberIds())
        except Exception as _e:
            _add_rft_debug.append('  GetMemberIds failed: {}'.format(_e))
            continue

        _specs = add_rft_reader.read_add_rft_group(_group, _mesh_layer, _direction_hint)
        _add_rft_debug.append(
            '  layer={} members={} specs={}'.format(
                _mesh_layer, len(_member_ids), len(_specs)
            )
        )
        _add_rft_debug.extend(add_rft_reader.get_last_group_diag())

        _rows  = add_rft_reader.generate_add_rft_rows(
            _specs, z_bottom_x, z_bottom_y, z_top_x, z_top_y
        )
        _add_rft_debug.append('  rows={}'.format(len(_rows)))
        if _rows:
            _r0 = _rows[0]
            _rN = _rows[-1]
            _add_rft_debug.append(
                '  row[0] dir={} fixed={:.2f}ft vary=[{:.2f},{:.2f}]ft spacing={:.4f}ft'.format(
                    _r0['direction'],
                    _r0['fixed_val'],
                    _r0['vary_min'],
                    _r0['vary_max'],
                    _r0.get('spacing_ft', 0.0),
                )
            )
            _add_rft_debug.append(
                '  fixed range [{:.2f} .. {:.2f}]ft  ({} rows)'.format(
                    _r0['fixed_val'], _rN['fixed_val'], len(_rows)
                )
            )
            # Slab bbox for comparison
            _bb = slab_data['bbox']  # (min_x, min_y, max_x, max_y)
            _add_rft_debug.append(
                '  slab bbox X=[{:.2f},{:.2f}]ft Y=[{:.2f},{:.2f}]ft'.format(
                    _bb[0], _bb[2], _bb[1], _bb[3]
                )
            )
        # Show first spec origin + dist_dir so user can verify location
        if _specs:
            _s0 = _specs[0]
            _add_rft_debug.append(
                '  spec[0] origin=({:.2f},{:.2f})ft dist_dir=({:.3f},{:.3f}) dist={:.2f}ft'.format(
                    _s0.get('origin', (0, 0))[0],
                    _s0.get('origin', (0, 0))[1],
                    _s0.get('dist_dir', (0, 0))[0],
                    _s0.get('dist_dir', (0, 0))[1],
                    _s0.get('dist_ft', 0.0),
                )
            )

        # Group rows by diameter so splice processing uses matching params
        _rows_by_diam = {}
        for _row in _rows:
            _d = _row['diam_mm']
            _rows_by_diam.setdefault(_d, []).append(_row)

        # Build (or reuse) obstacle cache for add-rft rows.
        # _obstacle_cache is set above when place_mesh=True; if running
        # Add RFT only it won't exist yet, so build it here once.
        if '_obstacle_cache' not in dir():
            _obstacle_cache = obstacle_processor.build_obstacle_cache(shaft_polygons, dp_data_list)

        for _diam_mm, _diam_rows in _rows_by_diam.items():
            _pre_splice = []
            for _row in _diam_rows:
                _segs = obstacle_processor.process_bar_row(
                    _row, slab_data['outer_polygon'], shaft_polygons,
                    dp_data_list, params, _mesh_layer,
                    obstacle_cache=_obstacle_cache
                )
                _pre_splice.extend(_segs)

            _spliced = splice_processor.process_splices(
                _pre_splice, params, support_positions=support_positions
            )
            _add_rft_debug.append(
                '  diam={}mm pre={} spliced={}'.format(
                    _diam_mm, len(_pre_splice), len(_spliced)
                )
            )
            add_rft_by_diam.setdefault(_diam_mm, []).extend(_spliced)
            add_rft_all_segs.extend(_spliced)

    # ------------------------------------------------------------------
    # 8. Place main slab rebar
    # ------------------------------------------------------------------
    if run_mode in ('Preview + Confirm', 'Preview Only'):
        try:
            preview_info = debug_preview.draw_preview(
                revit.doc,
                revit.active_view,
                slab_data,
                shaft_polygons,
                dp_data_list,
                final_segments + add_rft_all_segs,
                params.get('preview_max_lines', 1200)
            )
        except Exception as ex:
            forms.alert(
                'Preview failed in this view.\n'
                'Switch to a plan/section view and try again.\n\n{}'.format(str(ex)),
                title='Preview Error'
            )
            return

        # Compute shaft obstacle diagnostics
        _FT2_M2 = 0.3048 ** 2
        _shaft_areas = []
        for _p in shaft_polygons:
            if _p:
                _xs = [v[0] for v in _p]
                _ys = [v[1] for v in _p]
                _shaft_areas.append(
                    round((max(_xs) - min(_xs)) * (max(_ys) - min(_ys)) * _FT2_M2, 1)
                )
        _shaft_areas.sort(reverse=True)
        _top5 = ', '.join(str(a) for a in _shaft_areas[:5]) if _shaft_areas else 'none'

        preview_msg = (
            'Preview generated in active view.\n\n'
            'Segments total    : {}\n'
            'Segments shown    : {}\n'
            'Segments skipped  : {}\n'
            'Preview elements  : {}\n'
            'Bad edges filtered: {}\n'
            'Longest edge (m)  : {}\n'
            'Drop panels used  : {}\n'
            'Shaft obstacles   : {} (sketch:{} + openings:{})\n'
            'Top-5 shaft areas : {} m2\n\n'
            'DP detection (safe mode):\n'
            '  Relaxed retry used : {}\n'
            '  Top tol used (mm)  : {}\n'
            '  Thick tol used (mm): {}\n'
            '  Closest top dZ (mm): {}\n'
            '  Max top-below (mm) : {}\n'
            '  Floors scanned      : {}\n'
            '  Accepted            : {}\n'
            '  Accepted top-below  : {}\n'
            '  Rejected top mismatch: {}\n'
            '  Rejected not thicker: {}\n'
            '  Rejected no overlap : {}\n'
            '  Rejected outside slab: {}\n'
            '  Rejected no sketch  : {}\n'
            '  Rejected no profile : {}'
        ).format(
            len(final_segments) + len(add_rft_all_segs),
            preview_info.get('segments_drawn', 0),
            preview_info.get('segments_skipped', 0),
            len(preview_info.get('created_ids', [])),
            preview_info.get('outline_edges_filtered', 0),
            preview_info.get('longest_outline_edge_m', 0.0),
            len(dp_data_list),
            len(shaft_polygons),
            len(shaft_polygons_sketch),
            len(shaft_polygons_openings),
            _top5,
            dp_debug.get('used_relaxed_retry', 0),
            dp_debug.get('top_z_tolerance_mm', 0),
            dp_debug.get('thickness_tolerance_mm', 0),
            dp_debug.get('closest_top_delta_mm', 'n/a'),
            dp_debug.get('max_allowed_top_below_mm', 'n/a'),
            dp_debug.get('floors_scanned', 0),
            dp_debug.get('accepted', 0),
            dp_debug.get('accepted_top_below_slab', 0),
            dp_debug.get('rejected_top_mismatch', 0),
            dp_debug.get('rejected_not_thicker', 0),
            dp_debug.get('rejected_no_overlap', 0),
            dp_debug.get('rejected_outside_slab', 0),
            dp_debug.get('rejected_no_sketch', 0),
            dp_debug.get('rejected_no_profile', 0),
        )

        if run_mode == 'Preview Only':
            forms.alert(preview_msg, title='Preview Only')
            return

        do_place = forms.alert(
            preview_msg + '\n\nPlace real rebar now?',
            title='Preview + Confirm',
            yes=True,
            no=True
        )
        if not do_place:
            return

        try:
            debug_preview.clear_preview(revit.doc, preview_info.get('created_ids', []))
        except Exception:
            pass

    placed = 0
    failed = 0
    rebar_sets = 0

    # Disable third-party DMU updaters (e.g. SOFiSTiK) for the duration of
    # all placement operations.  They lock the document when they fail on our
    # custom rebar, causing every subsequent transaction to be rejected.
    _disabled_updaters = _disable_third_party_updaters()
    try:
        if place_mesh:
            print('[FlatSlabRFT] Placing main mesh rebar ({} segments)...'.format(len(final_segments)))
            placed, failed, rebar_sets = rebar_placer.place_all_slab_bars(
                revit.doc, floor, final_segments,
                params['bar_type'], params
            )
            print('[FlatSlabRFT] Main mesh done: placed={} failed={} sets={}'.format(
                placed, failed, rebar_sets))

        # ------------------------------------------------------------------
        # 8b. Place additional rebar (one pass per diameter)
        # ------------------------------------------------------------------
        add_rft_placed = 0
        add_rft_failed = 0
        add_rft_sets   = 0
        for _diam_mm, _segs in add_rft_by_diam.items():
            if not _segs:
                continue
            if _diam_mm > 50:
                print('[FlatSlabRFT] Skip add RFT diam={}mm — not a valid bar diameter'
                      ' (label parsing error?). {} segments skipped.'.format(
                          _diam_mm, len(_segs)))
                add_rft_failed += len(_segs)
                continue
            _bt = add_rft_reader.find_bar_type_by_diameter(revit.doc, _diam_mm)
            if _bt is None:
                add_rft_failed += len(_segs)
                continue
            print('[FlatSlabRFT] Placing Add RFT {}mm ({} segments)...'.format(_diam_mm, len(_segs)))
            _p, _f, _s = rebar_placer.place_all_slab_bars(
                revit.doc, floor, _segs, _bt, params
            )
            add_rft_placed += _p
            add_rft_failed += _f
            add_rft_sets   += _s
            print('[FlatSlabRFT] Add RFT {}mm done: placed={} failed={} sets={}'.format(
                _diam_mm, _p, _f, _s))

        # ------------------------------------------------------------------
        # 9. Place drop panel rebar
        # ------------------------------------------------------------------
        dp_place_stats = {}
        if place_mesh:
            print('[FlatSlabRFT] Placing drop panel rebar ({} DPs)...'.format(len(dp_data_list)))
            dp_place_stats = dp_rebar_placer.place_all_dp_bars(
                revit.doc, dp_data_list, params,
                shaft_polygons=shaft_polygons,
                slab_polygon=slab_data['outer_polygon'],
            )
            print('[FlatSlabRFT] Drop panel rebar done.')
    finally:
        _restore_updaters(_disabled_updaters)

    # ------------------------------------------------------------------
    # 10. Summary
    # ------------------------------------------------------------------
    _add_rft_debug_str = '\n'.join(_add_rft_debug) if _add_rft_debug else 'none'

    msg = (
        'Flat slab reinforcement placed successfully!\n\n'
        'Main slab bars placed : {}\n'
        'Main slab bars failed : {}\n'
        'Main rebar sets       : {}\n'
        'Add RFT bars placed   : {}\n'
        'Add RFT bars failed   : {}\n'
        'Add RFT rebar sets    : {}\n'
        'Add RFT debug         :\n{}\n\n'
        'Drop panels processed : {}\n\n'
        'Join fix stats:\n'
        '  Already correct : {}\n'
        '  Newly joined    : {}\n'
        '  Order switched  : {}\n'
        '  Failed          : {}\n\n'
        'DP placement stats:\n'
        '  X rows              : {}\n'
        '  Y rows              : {}\n'
        '  X rows shifted mode : {}\n'
        '  Y rows shifted mode : {}\n'
        '  X bars total        : {}\n'
        '  Y bars total        : {}\n'
        '  X staple            : {}\n'
        '  Y staple            : {}\n'
        '  X straight fallback : {}\n'
        '  Y straight fallback : {}\n'
        '  X failed            : {}\n'
        '  Y failed            : {}\n'
        '  Staple OK           : {}\n'
        '  Straight primary    : {}\n'
        '  Fallback straight   : {}\n'
        '  Too short skipped   : {}\n'
        '  Regen failed        : {}\n\n'
        'DP detection (safe mode):\n'
        '  Relaxed retry used : {}\n'
        '  Top tol used (mm)  : {}\n'
        '  Thick tol used (mm): {}\n'
        '  Closest top dZ (mm): {}\n'
        '  Max top-below (mm) : {}\n'
        '  Floors scanned      : {}\n'
        '  Accepted            : {}\n'
        '  Accepted top-below  : {}\n'
        '  Rejected top mismatch: {}\n'
        '  Rejected not thicker: {}\n'
        '  Rejected no overlap : {}\n'
        '  Rejected outside slab: {}\n'
        '  Rejected no sketch  : {}\n'
        '  Rejected no profile : {}'
    ).format(
        placed,
        failed,
        rebar_sets,
        add_rft_placed,
        add_rft_failed,
        add_rft_sets,
        _add_rft_debug_str,
        len(dp_data_list),
        join_stats.get('already_correct', 0),
        join_stats.get('joined_new', 0),
        join_stats.get('switched', 0),
        join_stats.get('failed', 0),
        dp_place_stats.get('x_rows', 0),
        dp_place_stats.get('y_rows', 0),
        dp_place_stats.get('x_rows_shifted', 0),
        dp_place_stats.get('y_rows_shifted', 0),
        dp_place_stats.get('x_total', 0),
        dp_place_stats.get('y_total', 0),
        dp_place_stats.get('x_staple', 0),
        dp_place_stats.get('y_staple', 0),
        dp_place_stats.get('x_straight', 0),
        dp_place_stats.get('y_straight', 0),
        dp_place_stats.get('x_failed', 0),
        dp_place_stats.get('y_failed', 0),
        dp_place_stats.get('staple_ok', 0),
        dp_place_stats.get('straight_primary', 0),
        dp_place_stats.get('fallback_straight', 0),
        dp_place_stats.get('too_short_skipped', 0),
        dp_place_stats.get('regen_failed', 0),
        dp_debug.get('used_relaxed_retry', 0),
        dp_debug.get('top_z_tolerance_mm', 0),
        dp_debug.get('thickness_tolerance_mm', 0),
        dp_debug.get('closest_top_delta_mm', 'n/a'),
        dp_debug.get('max_allowed_top_below_mm', 'n/a'),
        dp_debug.get('floors_scanned', 0),
        dp_debug.get('accepted', 0),
        dp_debug.get('accepted_top_below_slab', 0),
        dp_debug.get('rejected_top_mismatch', 0),
        dp_debug.get('rejected_not_thicker', 0),
        dp_debug.get('rejected_no_overlap', 0),
        dp_debug.get('rejected_outside_slab', 0),
        dp_debug.get('rejected_no_sketch', 0),
        dp_debug.get('rejected_no_profile', 0),
    )

    forms.alert(msg, title='Done')


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        pass  # User cancelled a dialog — silent exit
    except Exception as ex:
        import traceback
        forms.alert(
            'An error occurred:\n\n{}\n\n{}'.format(str(ex), traceback.format_exc()),
            title='Flat Slab RFT Error'
        )
