# -*- coding: utf-8 -*-
"""Create plan views by duplicating the active view."""
from __future__ import print_function

from Autodesk.Revit.DB import (
    FilteredElementCollector, ViewPlan,
    ElementId, BuiltInParameter
)
from Autodesk.Revit.DB import ViewDuplicateOption

VIEWS = [
    {'suffix': 'Bottom X Bars',    'mark': 'Bottom X'},
    {'suffix': 'Bottom Y Bars',    'mark': 'Bottom Y'},
    {'suffix': 'Top X Bars',       'mark': 'Top X'},
    {'suffix': 'Top Y Bars',       'mark': 'Top Y'},
    {'suffix': 'Add Bottom X Bars','mark': 'Add Bottom X'},
    {'suffix': 'Add Bottom Y Bars','mark': 'Add Bottom Y'},
    {'suffix': 'Add Top X Bars',   'mark': 'Add Top X'},
    {'suffix': 'Add Top Y Bars',   'mark': 'Add Top Y'},
    {'suffix': 'Drop Panel X',     'mark': 'Drop Panel X'},
    {'suffix': 'Drop Panel Y',     'mark': 'Drop Panel Y'},
]


def _get_view_name(view):
    try:
        p = view.get_Parameter(BuiltInParameter.VIEW_NAME)
        if p is not None:
            return p.AsString() or ''
    except Exception:
        pass
    return ''


def _try_set_view_name(view, desired_name):
    """Set view name, appending (N) suffix until Revit accepts it."""
    name = desired_name
    n = 2
    while n < 200:
        try:
            param = view.get_Parameter(BuiltInParameter.VIEW_NAME)
            if param is not None and not param.IsReadOnly:
                param.Set(name)
            else:
                view.Name = name
            return name
        except Exception:
            name = '{} ({})'.format(desired_name, n)
            n += 1
    return desired_name


def _clear_copy_name_conflicts(doc, active_view):
    """Rename any existing views named '<active_view> Copy N' to a temp name.

    When Revit duplicates a view it auto-names the copy '<source> Copy 1'.
    If that name already exists from a previous run, Revit raises a naming
    error at transaction commit.  Renaming the old copies beforehand avoids
    the conflict without deleting the user's views.
    """
    try:
        base_name = active_view.Name
    except Exception:
        base_name = _get_view_name(active_view)
    if not base_name:
        return
    prefix = base_name + ' Copy'
    renamed = 0
    failed = 0
    for v in FilteredElementCollector(doc).OfClass(ViewPlan):
        try:
            name = v.Name
        except Exception:
            name = _get_view_name(v)
        if not name.startswith(prefix):
            continue
        new_name = '_slabRFT_{}'.format(v.Id.IntegerValue)
        try:
            v.Name = new_name
            renamed += 1
        except Exception:
            try:
                p = v.get_Parameter(BuiltInParameter.VIEW_NAME)
                if p is not None and not p.IsReadOnly:
                    p.Set(new_name)
                    renamed += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
    print('[view_creator] pre-rename: {} Copy-N views renamed, {} failed'.format(renamed, failed))


def create_all_views(doc, active_view, view_template_id, selected_suffixes=None):
    """Duplicate active_view for each selected entry in VIEWS, return dict keyed by mark.

    selected_suffixes: list of suffix strings to create. If None or empty, creates all.
    """
    _clear_copy_name_conflicts(doc, active_view)

    try:
        source_name = active_view.Name
    except Exception:
        source_name = _get_view_name(active_view)

    entries = VIEWS
    if selected_suffixes:
        entries = [e for e in VIEWS if e['suffix'] in selected_suffixes]

    views_dict = {}
    for entry in entries:
        new_view_id = active_view.Duplicate(ViewDuplicateOption.Duplicate)
        new_view = doc.GetElement(new_view_id)

        desired_name = '{} {}'.format(source_name, entry['suffix'])
        _try_set_view_name(new_view, desired_name)

        if (view_template_id is not None
                and view_template_id != ElementId.InvalidElementId):
            try:
                new_view.ViewTemplateId = view_template_id
            except Exception:
                pass

        views_dict[entry['mark']] = new_view

    return views_dict
