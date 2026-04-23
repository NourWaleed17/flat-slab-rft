# -*- coding: utf-8 -*-
"""Entry point for the Flat Slab Auto Reinforcement pyRevit button."""
from __future__ import print_function

from pyrevit import forms, revit, DB
from Autodesk.Revit.DB.Structure import Rebar, RebarStyle, RebarHookOrientation

import ui
import geometry
import bar_generator
import obstacle_processor
import splice_processor
import rebar_placer
import dp_rebar_placer


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
    # 2. Collect user inputs
    # ------------------------------------------------------------------
    params = ui.collect_inputs(revit.doc)
    params['stagger_splices'] = True

    # ------------------------------------------------------------------
    # 3. Extract slab geometry
    # ------------------------------------------------------------------
    slab_data = geometry.get_slab_data(floor)

    # Combine sketch voids + shaft openings → treated the same (hooks at both sides)
    shaft_polygons_sketch   = slab_data['sketch_void_polygons']
    shaft_polygons_openings = geometry.get_shaft_opening_polygons(
        revit.doc, slab_data['bbox'], slab_data['top_z']
    )
    shaft_polygons = shaft_polygons_sketch + shaft_polygons_openings

    dp_data_list = geometry.get_drop_panel_data(
        revit.doc,
        slab_data['top_z'],
        main_floor_id=floor.Id,
        slab_bbox=slab_data['bbox'],
        slab_thickness=slab_data['thickness']
    )

    # Expose slab top Z to dp_rebar_placer via params
    params['slab_top_z'] = slab_data['top_z']
    params['slab_bottom_z'] = slab_data['bottom_z']
    params['slab_thickness'] = slab_data['thickness']

    # ------------------------------------------------------------------
    # 4. Generate raw bar rows
    # ------------------------------------------------------------------
    bbox    = slab_data['bbox']
    spacing = params['spacing']
    cover   = params['cover']

    bottom_x_rows = bar_generator.generate_bar_rows(bbox, spacing, cover, 'X')
    bottom_y_rows = bar_generator.generate_bar_rows(bbox, spacing, cover, 'Y')
    top_x_rows    = bar_generator.generate_bar_rows(bbox, spacing, cover, 'X')
    top_y_rows    = bar_generator.generate_bar_rows(bbox, spacing, cover, 'Y')

    # ------------------------------------------------------------------
    # 5. Assign Z elevations
    # ------------------------------------------------------------------
    diameter = params['diameter']
    top_z    = slab_data['top_z']
    bottom_z = slab_data['bottom_z']

    z_bottom_x = bottom_z + cover              # lowest layer
    z_bottom_y = z_bottom_x + diameter         # one bar diameter above
    z_top_x    = top_z - cover                 # highest layer
    z_top_y    = z_top_x - diameter            # one bar diameter below

    # ------------------------------------------------------------------
    # 6. Stage 1 — obstacle splits
    # ------------------------------------------------------------------
    all_segments = []

    for row in bottom_x_rows:
        row['z'] = z_bottom_x
        segs = obstacle_processor.process_bar_row(
            row, slab_data['outer_polygon'], shaft_polygons, dp_data_list, params, 'bottom'
        )
        all_segments.extend(segs)

    for row in bottom_y_rows:
        row['z'] = z_bottom_y
        segs = obstacle_processor.process_bar_row(
            row, slab_data['outer_polygon'], shaft_polygons, dp_data_list, params, 'bottom'
        )
        all_segments.extend(segs)

    for row in top_x_rows:
        row['z'] = z_top_x
        segs = obstacle_processor.process_bar_row(
            row, slab_data['outer_polygon'], shaft_polygons, dp_data_list, params, 'top'
        )
        all_segments.extend(segs)

    for row in top_y_rows:
        row['z'] = z_top_y
        segs = obstacle_processor.process_bar_row(
            row, slab_data['outer_polygon'], shaft_polygons, dp_data_list, params, 'top'
        )
        all_segments.extend(segs)

    # ------------------------------------------------------------------
    # 7. Stage 2 — splice splits
    # ------------------------------------------------------------------
    final_segments = splice_processor.process_splices(all_segments, params)

    # ------------------------------------------------------------------
    # 8. Place main slab rebar
    # ------------------------------------------------------------------
    placed, failed, rebar_sets = rebar_placer.place_all_slab_bars(
        revit.doc, floor, final_segments,
        params['bar_type'], params['hook_type'], params
    )

    # ------------------------------------------------------------------
    # 9. Place drop panel rebar
    # ------------------------------------------------------------------
    dp_rebar_placer.place_all_dp_bars(revit.doc, dp_data_list, params)

    # ------------------------------------------------------------------
    # 10. Summary
    # ------------------------------------------------------------------
    msg = (
        'Flat slab reinforcement placed successfully!\n\n'
        'Main slab bars placed : {}\n'
        'Main slab bars failed : {}\n'
        'Main rebar sets       : {}\n'
        'Drop panels processed : {}'
    ).format(placed, failed, rebar_sets, len(dp_data_list))

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
