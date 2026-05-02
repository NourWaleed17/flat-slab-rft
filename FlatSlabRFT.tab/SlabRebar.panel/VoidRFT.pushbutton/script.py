# -*- coding: utf-8 -*-
"""VoidRFT — place additional reinforcement around slab voids/openings.

Flow:
  1. User picks a floor slab in the model.
  2. Dialog collects bar types and placement parameters.
  3. Voids are detected (sketch holes + shaft Opening elements).
  4. Trimmer and diagonal segments are generated via void_geometry.
  5. Preview summary is shown; user confirms.
  6. Bars are placed via void_placer.
"""
from __future__ import print_function

import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

from Autodesk.Revit.DB import Floor
from Autodesk.Revit.UI.Selection import ObjectType

from pyrevit import script, forms, revit

import void_ui
from void_slab_helpers import (get_slab_data, get_shaft_opening_polygons,
                                get_floor_cover, bbox_from_polygon)
from void_geometry import generate_trimmer_segments, generate_diagonal_segments
from void_schedule import lookup_band
from void_placer import place_trimmer_segments, place_diagonal_segments


MM_TO_FT = 1.0 / 304.8


def main():
    doc    = revit.doc
    output = script.get_output()
    output.print_md('## VoidRFT')

    # ------------------------------------------------------------------
    # 1. Pick floor
    # ------------------------------------------------------------------
    output.print_md('_Click a floor slab in the model..._')
    try:
        ref   = revit.uidoc.Selection.PickObject(ObjectType.Element,
                                                  'Pick a floor slab')
        floor = doc.GetElement(ref.ElementId)
    except Exception:
        output.print_md('_Cancelled._')
        return

    if not isinstance(floor, Floor):
        forms.alert('The selected element is not a floor. Please run again '
                    'and click a floor/slab.', title='VoidRFT')
        return

    # ------------------------------------------------------------------
    # 2. Extract slab geometry
    # ------------------------------------------------------------------
    try:
        slab_data = get_slab_data(floor)
    except Exception as ex:
        forms.alert('Cannot read slab data:\n{}'.format(ex), title='VoidRFT')
        return

    # ------------------------------------------------------------------
    # 3. Show dialog (bar types + parameters)
    # ------------------------------------------------------------------
    params_ui = void_ui.show_dialog(doc, floor)
    if params_ui is None:
        output.print_md('_Cancelled by user._')
        return

    # ------------------------------------------------------------------
    # 4. Build unified params dict
    # ------------------------------------------------------------------
    cover_ft             = params_ui['cover_mm']              * MM_TO_FT
    trimmer_spacing_ft   = params_ui['trimmer_spacing_mm']    * MM_TO_FT
    inner_offset_ft      = params_ui['inner_offset_mm']       * MM_TO_FT
    min_trimmer_len_ft   = params_ui['min_trimmer_length_mm'] * MM_TO_FT

    params = {
        'cover':                 cover_ft,
        'ld_multiplier':         params_ui['ld_multiplier'],
        # Shift diagonal baseline closer to shaft/void corners.
        'diagonal_corner_offset_factor': 0.5,
        'trimmer_spacing_ft':    trimmer_spacing_ft,
        'inner_offset_ft':       inner_offset_ft,
        'min_trimmer_length_ft': min_trimmer_len_ft,
        'slab_top_z':            slab_data['top_z'],
        'slab_bottom_z':         slab_data['bottom_z'],
        'slab_thickness':        slab_data['thickness'],
        'slab_polygon':          slab_data['outer_polygon'],
        'floor_id':              floor.Id.IntegerValue,
    }

    # Bar type lookup dict: int mm -> RebarBarType
    bar_type_by_dia = {}
    for dia, key in ((16, 'bar_type_16'), (18, 'bar_type_18')):
        bt = params_ui.get(key)
        if bt is not None:
            bar_type_by_dia[dia] = bt

    if not bar_type_by_dia:
        forms.alert('No bar types were selected.', title='VoidRFT')
        return

    # ------------------------------------------------------------------
    # 5. Detect voids
    # ------------------------------------------------------------------
    output.print_md('_Detecting void openings..._')

    slab_bbox = slab_data['bbox']

    # Sketch holes (smaller loops inside the slab sketch)
    sketch_voids = slab_data['sketch_void_polygons']

    # Standalone shaft Opening elements intersecting this slab
    shaft_voids = get_shaft_opening_polygons(
        doc, slab_bbox, slab_data['top_z'],
        main_floor_id=floor.Id,
        slab_bottom_z=slab_data['bottom_z'],
    )

    all_void_polygons = sketch_voids + shaft_voids
    params['void_holes'] = all_void_polygons

    # ------------------------------------------------------------------
    # 6. Generate segments for each void
    # ------------------------------------------------------------------
    all_trimmer_segs  = []
    all_diagonal_segs = []
    active_count  = 0
    skipped_count = 0

    for void_idx, poly in enumerate(all_void_polygons, 1):
        void_id = 'V{:02d}'.format(void_idx)
        bbox = bbox_from_polygon(poly)
        segs  = generate_trimmer_segments(bbox,  params, lookup_band)
        diags = generate_diagonal_segments(bbox, params, lookup_band)
        if not segs and not diags:
            skipped_count += 1
        else:
            active_count += 1
            for seg in segs:
                seg['void_id'] = void_id
            for diag in diags:
                diag['void_id'] = void_id
            all_trimmer_segs.extend(segs)
            all_diagonal_segs.extend(diags)

    # ------------------------------------------------------------------
    # 7. Preview summary
    # ------------------------------------------------------------------
    output.print_md('### Void Detection Summary')
    output.print_md('- Floor: **{}**'.format(
        floor.Id.IntegerValue))
    output.print_md('- Sketch holes found: **{}**'.format(len(sketch_voids)))
    output.print_md('- Shaft openings found: **{}**'.format(len(shaft_voids)))
    output.print_md('- Voids to reinforce (>150 mm): **{}**'.format(
        active_count))
    output.print_md('- Voids skipped (<=150 mm / IGNORE band): **{}**'.format(
        skipped_count))
    output.print_md('- Trimmer segments to place: **{}**'.format(
        len(all_trimmer_segs)))
    output.print_md('- Diagonal segments to place: **{}**'.format(
        len(all_diagonal_segs)))

    if not all_trimmer_segs and not all_diagonal_segs:
        forms.alert('No voids require reinforcement under the current schedule.\n\n'
                    'Check that void dimensions are >= 200 mm.',
                    title='VoidRFT')
        return

    # ------------------------------------------------------------------
    # 8. Confirm placement
    # ------------------------------------------------------------------
    confirmed = forms.alert(
        '{} void(s) found that need reinforcement.\n\n'
        'Will place:\n'
        '  - {} trimmer bar segments\n'
        '  - {} diagonal bar segments\n\n'
        'Proceed?'.format(active_count,
                          len(all_trimmer_segs),
                          len(all_diagonal_segs)),
        title='VoidRFT — Confirm Placement',
        ok=True,
        cancel=True,
    )
    if not confirmed:
        output.print_md('_Cancelled by user._')
        return

    # ------------------------------------------------------------------
    # 9. Place bars
    # ------------------------------------------------------------------
    output.print_md('_Placing trimmer bars..._')
    t_placed, t_failed = place_trimmer_segments(
        doc, floor, all_trimmer_segs, bar_type_by_dia, params)

    output.print_md('_Placing diagonal bars..._')
    d_placed, d_failed = place_diagonal_segments(
        doc, floor, all_diagonal_segs, bar_type_by_dia, params)

    # ------------------------------------------------------------------
    # 10. Final summary
    # ------------------------------------------------------------------
    output.print_md('### Placement Complete')
    output.print_md('- Trimmers placed: **{}**  failed: **{}**'.format(
        t_placed, t_failed))
    output.print_md('- Diagonals placed: **{}**  failed: **{}**'.format(
        d_placed, d_failed))

    total_placed = t_placed + d_placed
    total_failed = t_failed + d_failed
    if total_failed == 0:
        output.print_md('**All {} bars placed successfully.**'.format(total_placed))
    else:
        output.print_md('**{} bars placed, {} failed** — check Revit warnings '
                        'and verify bar types cover the required diameters.'.format(
                            total_placed, total_failed))


if __name__ == '__main__':
    main()
