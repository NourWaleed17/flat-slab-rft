# -*- coding: utf-8 -*-
"""User input collection for Flat Slab Rebar placement."""
from __future__ import print_function

import clr
clr.AddReference('System.Windows.Forms')
clr.AddReference('System.Drawing')

from pyrevit import forms
from Autodesk.Revit.DB import FilteredElementCollector, BuiltInParameter
from Autodesk.Revit.DB.Structure import RebarBarType, RebarHookType
from System.Windows.Forms import (Form, Label, TextBox, Button, DialogResult,
                                   FormBorderStyle, FormStartPosition)
from System.Drawing import Size, Point

MM_TO_FEET = 0.00328084
M_TO_FEET = 3.28084


def _type_name(element):
    """Return the display name of a Revit ElementType via its built-in parameter.

    Avoids the .Name property which is inaccessible on RebarHookType /
    RebarBarType in IronPython. Falls back to the element Id integer value
    as a string so the list is never empty.
    """
    param = element.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
    if param is not None:
        val = param.AsString()
        if val:
            return val
    return str(element.Id.IntegerValue)


def _ask_float(prompt, title, default):
    """Ask user for a float value via a WinForms dialog; returns None if cancelled."""
    frm = Form()
    frm.Text = title
    frm.ClientSize = Size(400, 125)
    frm.FormBorderStyle = FormBorderStyle.FixedDialog
    frm.MaximizeBox = False
    frm.MinimizeBox = False
    frm.StartPosition = FormStartPosition.CenterScreen
    frm.TopMost = True

    lbl = Label()
    lbl.Text = prompt
    lbl.Location = Point(10, 10)
    lbl.Size = Size(380, 40)

    tb = TextBox()
    tb.Text = str(default)
    tb.Location = Point(10, 55)
    tb.Size = Size(380, 24)

    ok_btn = Button()
    ok_btn.Text = 'OK'
    ok_btn.Location = Point(215, 88)
    ok_btn.Size = Size(80, 26)
    ok_btn.DialogResult = DialogResult.OK

    cancel_btn = Button()
    cancel_btn.Text = 'Cancel'
    cancel_btn.Location = Point(305, 88)
    cancel_btn.Size = Size(85, 26)
    cancel_btn.DialogResult = DialogResult.Cancel

    frm.Controls.Add(lbl)
    frm.Controls.Add(tb)
    frm.Controls.Add(ok_btn)
    frm.Controls.Add(cancel_btn)
    frm.AcceptButton = ok_btn
    frm.CancelButton = cancel_btn
    frm.ActiveControl = tb

    dlg_result = frm.ShowDialog()
    val_str = str(tb.Text).strip()
    frm.Dispose()

    if dlg_result != DialogResult.OK:
        return None
    if not val_str:
        return None
    try:
        return float(val_str)
    except (ValueError, TypeError):
        forms.alert(
            'Invalid number: "{}"\nPlease enter a numeric value.'.format(val_str),
            title='Input Error'
        )
        return None


def collect_inputs(doc):
    """Collect all rebar parameters from the user via pyRevit forms.

    Returns a dict with all values converted to Revit internal units (feet).
    Raises SystemExit if the user cancels any dialog.
    """
    # 1. Bar diameter
    diameter_mm = _ask_float('Enter bar diameter (mm):', 'Bar Diameter', 12)
    if diameter_mm is None:
        raise SystemExit('User cancelled.')
    if diameter_mm <= 0:
        raise ValueError('Bar diameter must be positive.')

    # 2. Bar spacing
    spacing_mm = _ask_float('Enter bar spacing (mm):', 'Bar Spacing', 200)
    if spacing_mm is None:
        raise SystemExit('User cancelled.')
    if spacing_mm <= 0:
        raise ValueError('Bar spacing must be positive.')

    # 3. Concrete cover
    cover_mm = _ask_float('Enter concrete cover (mm):', 'Concrete Cover', 25)
    if cover_mm is None:
        raise SystemExit('User cancelled.')
    if cover_mm <= 0:
        raise ValueError('Concrete cover must be positive.')

    # 4. Standard bar length
    bar_length_m = _ask_float('Enter standard bar length (m):', 'Standard Bar Length', 12)
    if bar_length_m is None:
        raise SystemExit('User cancelled.')
    if bar_length_m <= 0:
        raise ValueError('Bar length must be positive.')

    # 5. Splice length multiplier
    splice_mult = _ask_float(
        'Enter splice length multiplier (\u00d7 diameter):',
        'Splice Length Multiplier', 50
    )
    if splice_mult is None:
        raise SystemExit('User cancelled.')
    if splice_mult <= 0:
        raise ValueError('Splice multiplier must be positive.')

    # 6. Ld multiplier
    ld_mult = _ask_float(
        'Enter development length multiplier Ld (\u00d7 diameter):',
        'Ld Multiplier', 40
    )
    if ld_mult is None:
        raise SystemExit('User cancelled.')
    if ld_mult <= 0:
        raise ValueError('Ld multiplier must be positive.')

    # 7. Drop panel vertical leg length
    dp_vert_mm = _ask_float(
        'Enter drop panel vertical leg length (mm):',
        'DP Vertical Leg', 150
    )
    if dp_vert_mm is None:
        raise SystemExit('User cancelled.')
    if dp_vert_mm <= 0:
        raise ValueError('DP vertical leg must be positive.')

    # 8. Drop panel horizontal leg length
    dp_horiz_mm = _ask_float(
        'Enter drop panel horizontal leg length (mm):',
        'DP Horizontal Leg', 100
    )
    if dp_horiz_mm is None:
        raise SystemExit('User cancelled.')
    if dp_horiz_mm <= 0:
        raise ValueError('DP horizontal leg must be positive.')

    # 9. Hook type selection
    hook_types = list(FilteredElementCollector(doc).OfClass(RebarHookType).ToElements())
    if not hook_types:
        raise Exception('No RebarHookType elements found in document.')
    hook_type_dict = {}
    for ht in hook_types:
        hook_type_dict[_type_name(ht)] = ht

    selected_hook_name = forms.SelectFromList.show(
        sorted(hook_type_dict.keys()),
        title='Select Hook Type',
        multiselect=False
    )
    if selected_hook_name is None:
        raise SystemExit('User cancelled.')
    hook_type = hook_type_dict[selected_hook_name]

    # 10. Bar type selection — filtered by entered diameter
    bar_types = list(FilteredElementCollector(doc).OfClass(RebarBarType).ToElements())
    matching = {}
    for bt in bar_types:
        try:
            bt_diam_param = bt.LookupParameter('Bar Diameter')
            if bt_diam_param is None:
                bt_diam_param = bt.LookupParameter('Nominal Diameter')
            if bt_diam_param is not None:
                bt_diam_mm = bt_diam_param.AsDouble() / MM_TO_FEET
                if abs(bt_diam_mm - diameter_mm) < 1.0:
                    matching[_type_name(bt)] = bt
        except Exception:
            pass

    # Fallback: show all bar types if none match the diameter
    if not matching:
        for bt in bar_types:
            matching[_type_name(bt)] = bt

    if not matching:
        raise Exception('No RebarBarType elements found in document.')

    selected_bar_name = forms.SelectFromList.show(
        sorted(matching.keys()),
        title='Select Bar Type (Diameter: {}mm)'.format(int(diameter_mm)),
        multiselect=False
    )
    if selected_bar_name is None:
        raise SystemExit('User cancelled.')
    bar_type = matching[selected_bar_name]

    # Validate bar length vs spacing
    if bar_length_m * M_TO_FEET <= spacing_mm * MM_TO_FEET:
        raise ValueError('Bar length must be greater than spacing.')

    # Convert everything to Revit internal units (feet)
    diameter_ft      = diameter_mm * MM_TO_FEET
    spacing_ft       = spacing_mm  * MM_TO_FEET
    cover_ft         = cover_mm    * MM_TO_FEET
    bar_length_ft    = bar_length_m * M_TO_FEET
    splice_length_ft = splice_mult * diameter_ft
    ld_ft            = ld_mult * diameter_ft
    dp_vert_ft       = dp_vert_mm  * MM_TO_FEET
    dp_horiz_ft      = dp_horiz_mm * MM_TO_FEET

    return {
        'diameter':         diameter_ft,
        'spacing':          spacing_ft,
        'cover':            cover_ft,
        'bar_length':       bar_length_ft,
        'splice_multiplier': splice_mult,
        'splice_length':    splice_length_ft,
        'ld_multiplier':    ld_mult,
        'ld':               ld_ft,
        'dp_vertical_leg':  dp_vert_ft,
        'dp_horizontal_leg': dp_horiz_ft,
        'hook_type':        hook_type,
        'bar_type':         bar_type,
    }
