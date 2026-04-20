# -*- coding: utf-8 -*-
"""Create and apply Mark-based visibility filters to views."""
from __future__ import print_function

import clr
clr.AddReference('System')
from System.Collections.Generic import List

from Autodesk.Revit.DB import (
    FilteredElementCollector, ParameterFilterElement,
    ElementParameterFilter, ParameterFilterRuleFactory,
    BuiltInParameter, BuiltInCategory, ElementId,
    OverrideGraphicSettings
)


def _rebar_category_list():
    cat_ids = List[ElementId]()
    cat_ids.Add(ElementId(BuiltInCategory.OST_Rebar))
    return cat_ids


def _filter_name(mark_value):
    return 'SlabRFT_{}'.format(mark_value.replace(' ', '_'))


def _get_or_create_filter(doc, filter_name, mark_value, match, existing_filters=None):
    """Return existing filter with filter_name or create a new one.

    match=True  → Mark equals mark_value
    match=False → Mark does not equal mark_value (for hiding others)
    existing_filters: dict {name: element} pre-collected by the caller to avoid
                      repeated FilteredElementCollector scans per call.
    """
    if existing_filters is not None and filter_name in existing_filters:
        return existing_filters[filter_name]

    # Fallback scan (used when no cache is provided).
    collector = FilteredElementCollector(doc).OfClass(ParameterFilterElement)
    for f in collector:
        if f.Name == filter_name:
            return f

    param_id = ElementId(BuiltInParameter.ALL_MODEL_MARK)
    if match:
        rule = ParameterFilterRuleFactory.CreateEqualsRule(
            param_id, mark_value, False
        )
    else:
        rule = ParameterFilterRuleFactory.CreateNotEqualsRule(
            param_id, mark_value, False
        )

    element_filter = ElementParameterFilter(rule)
    new_filter = ParameterFilterElement.Create(
        doc, filter_name, _rebar_category_list(), element_filter
    )
    if existing_filters is not None:
        existing_filters[filter_name] = new_filter
    return new_filter


def create_mark_filter(doc, mark_value, view, existing_filters=None):
    """Apply two filters to view: show matching rebar, hide all other rebar."""

    # Filter 1: show bars matching this mark
    show_filter = _get_or_create_filter(
        doc,
        _filter_name(mark_value) + '_show',
        mark_value,
        match=True,
        existing_filters=existing_filters,
    )
    if show_filter.Id not in view.GetFilters():
        view.AddFilter(show_filter.Id)
    view.SetFilterVisibility(show_filter.Id, True)

    # Filter 2: hide bars NOT matching this mark
    hide_filter = _get_or_create_filter(
        doc,
        _filter_name(mark_value) + '_hide',
        mark_value,
        match=False,
        existing_filters=existing_filters,
    )
    if hide_filter.Id not in view.GetFilters():
        view.AddFilter(hide_filter.Id)
    view.SetFilterVisibility(hide_filter.Id, False)


def apply_all_filters(doc, views_dict):
    """Apply mark filters to every view in views_dict."""
    # Pre-collect all existing ParameterFilterElements once so that
    # _get_or_create_filter never repeats the same collector scan per mark.
    existing_filters = {
        f.Name: f
        for f in FilteredElementCollector(doc).OfClass(ParameterFilterElement)
    }
    for mark_value, view in views_dict.items():
        try:
            create_mark_filter(doc, mark_value, view, existing_filters=existing_filters)
        except Exception as e:
            print('Warning: could not apply filter for {}: {}'.format(mark_value, e))
