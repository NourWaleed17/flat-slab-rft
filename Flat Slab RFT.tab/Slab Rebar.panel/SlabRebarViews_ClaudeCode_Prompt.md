# Claude Code Prompt — Slab Rebar Views (pyRevit Script)

## Project Overview

Create a new pyRevit pushbutton script (IronPython) that automatically generates 10 plan views for a flat slab's reinforcement detailing. Each view shows a specific bar set using visibility filters, and includes a bending detail aligned to a representative bar, a dimension showing the total distribution span, and a rebar tag.

This is a **separate button** from the rebar placement script, placed in the same panel.

---

## Button Location

Place this button inside the existing extension structure:

```
Flat Slab RFT.tab\
└── Slab Rebar.panel\
    ├── FlatSlabRebar.pushbutton\        ← existing (rebar placement)
    └── SlabRebarViews.pushbutton\       ← new (views + detailing)
        ├── script.py
        ├── ui.py
        ├── view_creator.py
        ├── filter_creator.py
        ├── detail_placer.py
        └── bundle.yaml
```

---

## Technology Stack

- pyRevit IronPython (Python 2.7 compatible)
- Revit API for view creation, filter creation, dimension placement, tag placement
- Revit 2025 native RebarBendingDetail API for bending detail placement

---

## Runtime User Inputs (ui.py)

Sequential inputs collected at script launch:

1. **Slab selection** — user picks a floor element in the model using `revit.pick_element()`
2. **View template** — `forms.SelectFromList` populated with all view templates available in the document
3. **Tag family** — `forms.SelectFromList` populated with all rebar tag families loaded in the document

### Return:
```python
{
    'slab': Floor element,
    'view_template_id': ElementId,
    'tag_family_symbol': FamilySymbol
}
```

---

## The 10 Views — Fixed Naming Convention

The script creates exactly these 10 views with exactly these mark filter values:

```python
VIEWS = [
    {'view_name': 'Slab Bottom X Bars',             'mark': 'Bottom X'},
    {'view_name': 'Slab Bottom Y Bars',             'mark': 'Bottom Y'},
    {'view_name': 'Slab Top X Bars',                'mark': 'Top X'},
    {'view_name': 'Slab Top Y Bars',                'mark': 'Top Y'},
    {'view_name': 'Slab Additional Bottom X Bars',  'mark': 'Add Bottom X'},
    {'view_name': 'Slab Additional Bottom Y Bars',  'mark': 'Add Bottom Y'},
    {'view_name': 'Slab Additional Top X Bars',     'mark': 'Add Top X'},
    {'view_name': 'Slab Additional Top Y Bars',     'mark': 'Add Top Y'},
    {'view_name': 'Slab Drop Panel X Bars',         'mark': 'DP Bar X'},
    {'view_name': 'Slab Drop Panel Y Bars',         'mark': 'DP Bar Y'},
]
```

No scanning of existing rebar marks — these values are hardcoded as the fixed naming convention.

---

## Module 1 — view_creator.py

### Function: `get_slab_level(slab)`
- Get the level the slab is associated with via `slab.LevelId`
- Return the `Level` element

### Function: `create_plan_view(doc, level, view_name, view_template_id)`
- Create a new `ViewPlan` at the given level
- Use `ViewPlan.Create(doc, level.Id, ViewType.FloorPlan)`  
- Set the view name to `view_name`
- Apply the view template: `view.ViewTemplateId = view_template_id`
- Return the created view

### Function: `create_all_views(doc, slab, view_template_id)`
- Get slab level
- For each entry in `VIEWS` list:
  - Call `create_plan_view()`
  - Store result in dict keyed by mark value
- Return dict: `{'Bottom X': ViewPlan, 'Bottom Y': ViewPlan, ...}`

---

## Module 2 — filter_creator.py

### Function: `create_mark_filter(doc, mark_value, view)`
- Create a `ParameterFilterElement` that filters rebar elements where `Mark` parameter equals `mark_value`
- Filter applies to category: `BuiltInCategory.OST_Rebar`
- Rule: `FilterStringRule` with `FilterStringEquals` on `BuiltInParameter.ALL_MODEL_MARK`
- Add filter to view via `view.AddFilter(filter_id)`
- Set filter visibility: show matching elements, use `view.SetFilterVisibility(filter_id, True)`
- Hide all other rebar not matching: achieved by also adding a second filter for rebar where mark does NOT equal this value, and setting its visibility to False

### Function: `apply_all_filters(doc, views_dict)`
- For each mark value and its corresponding view in views_dict:
  - Call `create_mark_filter()`

---

## Module 3 — detail_placer.py

### Function: `get_representative_bar(doc, mark_value)`
- Collect all `Rebar` elements in document using `FilteredElementCollector`
- Filter to elements where `Mark` parameter equals `mark_value`
- Return the first element found, or None if no bars with that mark exist

### Function: `place_bending_detail(doc, view, rebar_element)`
- Use Revit 2025 native `RebarBendingDetail` API
- Place bending detail on `rebar_element` in `view`
- Use align-to-bar orientation so detail is aligned with the bar direction in plan
- Return the placed bending detail element

### Function: `get_distribution_extent(doc, mark_value, slab_bbox)`
- Collect all rebar elements with the given mark value
- Find the first and last bar positions in the distribution direction:
  - For X bars (Bottom X, Top X, Add Bottom X, Add Top X, DP Bar X): distribution is along Y axis → find min Y and max Y of all bars in this set
  - For Y bars (Bottom Y, Top Y, Add Bottom Y, Add Top Y, DP Bar Y): distribution is along X axis → find min X and max X
- Return `(start_point, end_point)` as `XYZ` points at slab elevation for dimension placement

### Function: `place_distribution_dimension(doc, view, start_point, end_point, rebar_elements)`
- Create a `Dimension` in the view referencing the first and last rebar elements
- Use `doc.Create.NewDimension(view, dimension_line, references)`
- `dimension_line` = `Line.CreateBound(start_point, end_point)`
- `references` = `ReferenceArray` containing references to the first and last rebar elements in the set
- Offset the dimension line slightly from the bars so it doesn't overlap
- Return the placed dimension

### Function: `place_rebar_tag(doc, view, rebar_element, tag_family_symbol)`
- Place a rebar tag on the representative bar element
- Use `IndependentTag.Create(doc, view.Id, Reference(rebar_element), False, TagMode.TM_ADDBY_CATEGORY, TagOrientation.Horizontal, rebar_element_location)`
- Set the tag type to `tag_family_symbol`
- Return the placed tag

### Function: `place_all_details(doc, views_dict, tag_family_symbol)`
- For each mark value and view in views_dict:
  - Get representative bar → if None, skip this view silently
  - Place bending detail aligned to bar
  - Get distribution extent points
  - Place distribution dimension
  - Place rebar tag on representative bar

---

## script.py (Entry Point)

```python
from pyrevit import forms, revit
from Autodesk.Revit.DB import Transaction, TransactionGroup

import ui
import view_creator
import filter_creator
import detail_placer

def main():
    # 1. Collect user inputs
    inputs = ui.collect_inputs(revit.doc)
    if not inputs:
        return
    
    slab             = inputs['slab']
    view_template_id = inputs['view_template_id']
    tag_family_symbol = inputs['tag_family_symbol']
    
    with TransactionGroup(revit.doc, "Create Slab Rebar Views") as tg:
        tg.Start()
        
        # 2. Create 10 plan views
        with Transaction(revit.doc, "Create Plan Views") as t:
            t.Start()
            views_dict = view_creator.create_all_views(
                revit.doc, slab, view_template_id
            )
            t.Commit()
        
        # 3. Apply mark filters to each view
        with Transaction(revit.doc, "Apply Rebar Filters") as t:
            t.Start()
            filter_creator.apply_all_filters(revit.doc, views_dict)
            t.Commit()
        
        # 4. Place bending details, dimensions, and tags
        with Transaction(revit.doc, "Place Rebar Details") as t:
            t.Start()
            detail_placer.place_all_details(
                revit.doc, views_dict, tag_family_symbol
            )
            t.Commit()
        
        tg.Assimilate()
    
    forms.alert(
        "10 rebar views created successfully!",
        title="Slab Rebar Views"
    )

if __name__ == '__main__':
    main()
```

---

## Important Implementation Notes

### IronPython imports:
```python
from Autodesk.Revit.DB import (
    FilteredElementCollector, ViewPlan, ViewType,
    ParameterFilterElement, FilterStringRule,
    FilterStringEquals, ElementParameterFilter,
    BuiltInParameter, BuiltInCategory,
    ReferenceArray, IndependentTag, TagMode,
    TagOrientation, Line, XYZ, Transaction,
    TransactionGroup, ElementId
)
from Autodesk.Revit.DB.Structure import Rebar
import clr
clr.AddReference('System')
from System.Collections.Generic import List
```

### View template application:
```python
# Check view template exists before applying
if view_template_id != ElementId.InvalidElementId:
    view.ViewTemplateId = view_template_id
```

### Filter creation — correct API pattern:
```python
# Create filter for Mark = value
categories = List[ElementId]()
categories.Add(ElementId(BuiltInCategory.OST_Rebar))

param_id = ElementId(BuiltInParameter.ALL_MODEL_MARK)
rule = ParameterFilterRuleFactory.CreateEqualsRule(param_id, mark_value, False)
element_filter = ElementParameterFilter(rule)

filter_element = ParameterFilterElement.Create(
    doc, 
    "Filter_" + mark_value.replace(" ", "_"),
    categories,
    element_filter
)
```

### Determining bar direction from mark value:
```python
X_MARKS = ['Bottom X', 'Top X', 'Add Bottom X', 'Add Top X', 'DP Bar X']
Y_MARKS = ['Bottom Y', 'Top Y', 'Add Bottom Y', 'Add Top Y', 'DP Bar Y']

def get_distribution_axis(mark_value):
    if mark_value in X_MARKS:
        return 'Y'  # X bars are distributed along Y axis
    return 'X'      # Y bars are distributed along X axis
```

### Handling views where no rebar exists for that mark:
- Skip bending detail, dimension, and tag placement silently
- View is still created with filter applied
- Log skipped views and report to user at end

### View naming — handle duplicates:
- If a view with the same name already exists, append a number suffix
- e.g. `Slab Bottom X Bars (2)`

---

## Edge Cases to Handle

1. **No rebar with a given mark** — skip detail placement for that view, still create the view
2. **View template not found** — create view without template, warn user
3. **Tag family not loaded** — skip tag placement, warn user
4. **Duplicate view names** — append numeric suffix
5. **RebarBendingDetail API not available** — catch exception and warn user that Revit 2025 is required for bending details

---

## Deliverable

A complete pyRevit pushbutton folder `SlabRebarViews.pushbutton\` with all 6 files fully implemented, IronPython 2.7 compatible, ready to place inside the existing `Flat Slab RFT.tab\Slab Rebar.panel\` folder.
