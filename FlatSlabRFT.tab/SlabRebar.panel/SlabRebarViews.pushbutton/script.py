# -*- coding: utf-8 -*-
"""Slab Rebar Views — entry point."""
from __future__ import print_function

import time

from pyrevit import forms, revit
from Autodesk.Revit.DB import Transaction, TransactionGroup

import views_ui as ui
import view_creator
import filter_creator
import detail_placer


def _t(label, t0):
    elapsed = time.time() - t0
    print('[TIMING] {:.<40} {:.2f}s'.format(label + ' ', elapsed))
    return time.time()


def main():
    # Validate active view is duplicable
    active_view = revit.active_view
    if active_view is None:
        forms.alert('No active view. Please open a plan view first.',
                    title='Slab Rebar Views')
        return
    if not active_view.CanViewBeDuplicated(
            __import__('Autodesk.Revit.DB', fromlist=['ViewDuplicateOption'])
            .ViewDuplicateOption.Duplicate):
        forms.alert('The active view cannot be duplicated. Please open a plan view.',
                    title='Slab Rebar Views')
        return

    # 1. Collect user inputs
    all_suffixes = [e['suffix'] for e in view_creator.VIEWS]
    inputs = ui.collect_inputs(revit.doc, all_suffixes)
    if not inputs:
        return

    view_template_id  = inputs['view_template_id']
    tag_family_symbol = inputs['tag_family_symbol']
    selected_suffixes = inputs['selected_suffixes']

    print('[TIMING] === SlabRebarViews run started ===')
    print('[TIMING] views requested: {}'.format(len(selected_suffixes)))
    run_start = time.time()

    skipped = []

    with TransactionGroup(revit.doc, 'Create Slab Rebar Views') as tg:
        tg.Start()

        # 2. Duplicate active view N times and rename
        t0 = time.time()
        with Transaction(revit.doc, 'Create Plan Views') as t:
            t.Start()
            try:
                views_dict = view_creator.create_all_views(
                    revit.doc, active_view, view_template_id, selected_suffixes
                )
            except Exception as e:
                import traceback
                t.RollBack()
                forms.alert(
                    '{}\n\n{}'.format(str(e), traceback.format_exc()),
                    title='Slab Rebar Views — Error'
                )
                return
            t.Commit()
        t0 = _t('Stage 1 — create/rename views ({} views)'.format(len(views_dict)), t0)

        # 3. Apply mark filters to each view
        t0 = time.time()
        with Transaction(revit.doc, 'Apply Rebar Filters') as t:
            t.Start()
            filter_creator.apply_all_filters(revit.doc, views_dict)
            t.Commit()
        t0 = _t('Stage 2 — apply visibility filters', t0)

        # 4. Place bending details, dimensions, and tags
        t0 = time.time()
        with Transaction(revit.doc, 'Place Rebar Details') as t:
            t.Start()
            skipped = detail_placer.place_all_details(
                revit.doc, views_dict, tag_family_symbol
            )
            t.Commit()
        _t('Stage 3 — place bending details + dims + tags', t0)

        tg.Assimilate()

    total = time.time() - run_start
    print('[TIMING] === TOTAL {:.2f}s ==='.format(total))

    # Report
    msg = '{} rebar view(s) created successfully.'.format(len(views_dict))
    if skipped:
        msg += '\n\nNo rebar found for the following marks (views created, details skipped):\n'
        msg += '\n'.join('  - ' + m for m in skipped)
    forms.alert(msg, title='Slab Rebar Views')


if __name__ == '__main__':
    main()
