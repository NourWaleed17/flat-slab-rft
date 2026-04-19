# -*- coding: utf-8 -*-
"""User input collection for Slab Rebar Views."""
from __future__ import print_function

from pyrevit import forms
from Autodesk.Revit.DB import (
    FilteredElementCollector, ViewPlan, ElementId,
    BuiltInCategory, FamilySymbol
)


def _get_view_templates(doc):
    """Return list of (name, ElementId) for all view templates."""
    collector = FilteredElementCollector(doc).OfClass(ViewPlan)
    templates = []
    for v in collector:
        if v.IsTemplate:
            templates.append((v.Name, v.Id))
    templates.sort(key=lambda t: t[0])
    return templates


def _get_rebar_tag_families(doc):
    """Return list of (label, FamilySymbol) for all rebar tag families."""
    collector = (
        FilteredElementCollector(doc)
        .OfClass(FamilySymbol)
        .OfCategory(BuiltInCategory.OST_RebarTags)
    )
    tags = []
    for sym in collector:
        try:
            label = '{} : {}'.format(sym.Family.Name, sym.Name)
            tags.append((label, sym))
        except Exception:
            pass
    tags.sort(key=lambda t: t[0])
    return tags


def collect_inputs(doc, all_view_suffixes):
    """Collect view selection, template, and tag inputs. Returns dict or None if cancelled.

    all_view_suffixes: list of suffix strings from view_creator.VIEWS (e.g. 'Bottom X Bars').
    """

    # 1. Which views to create
    selected_suffixes = forms.SelectFromList.show(
        all_view_suffixes,
        title='Select Views to Create',
        message='Choose which rebar views to create:',
        multiselect=True,
        button_name='Next'
    )
    if not selected_suffixes:
        return None

    # 2. View template
    templates = _get_view_templates(doc)
    template_names = ['<None>'] + [t[0] for t in templates]

    selected_template = forms.SelectFromList.show(
        template_names,
        title='Select View Template',
        message='Choose a view template to apply to the selected views (or <None>):',
        multiselect=False
    )
    if selected_template is None:
        return None

    if selected_template == '<None>':
        view_template_id = ElementId.InvalidElementId
    else:
        view_template_id = next(
            (tid for name, tid in templates if name == selected_template),
            ElementId.InvalidElementId
        )

    # 3. Rebar tag family
    tags = _get_rebar_tag_families(doc)
    tag_symbol = None
    if tags:
        tag_labels = ['<Skip tags>'] + [t[0] for t in tags]
        selected_tag = forms.SelectFromList.show(
            tag_labels,
            title='Select Rebar Tag Family',
            message='Choose a rebar tag family (or skip):',
            multiselect=False
        )
        if selected_tag is None:
            return None
        if selected_tag != '<Skip tags>':
            tag_symbol = next(
                (sym for label, sym in tags if label == selected_tag),
                None
            )

    return {
        'selected_suffixes': list(selected_suffixes),
        'view_template_id':  view_template_id,
        'tag_family_symbol': tag_symbol,
    }
