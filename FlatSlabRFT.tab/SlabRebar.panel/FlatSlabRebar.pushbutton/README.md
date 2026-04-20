# Flat Slab RFT (pyRevit Slab Rebar Panel)

This repository stores the panel-level layout with two sibling pyRevit button folders:

- `FlatSlabRebar.pushbutton`
- `SlabRebarViews.pushbutton`

## 1) Prerequisites

- Autodesk Revit (recommended: Revit 2024+)
- pyRevit installed and loaded
- Rebar types and hook types in the model

## 2) Install On Work PC

Clone directly into the `Slab Rebar.panel` folder:

```powershell
git clone https://github.com/NourWaleed17/flat-slab-rft.git `
  "C:\Users\<YOU>\AppData\Roaming\pyRevit-Master\extensions\FlatSlabRFT.extension\Flat Slab RFT.tab\Slab Rebar.panel"
```

If needed, create parent folders first:

```powershell
New-Item -ItemType Directory -Force `
  "C:\Users\<YOU>\AppData\Roaming\pyRevit-Master\extensions\FlatSlabRFT.extension\Flat Slab RFT.tab" | Out-Null
```

## 3) Update On Work PC

```powershell
cd "C:\Users\<YOU>\AppData\Roaming\pyRevit-Master\extensions\FlatSlabRFT.extension\Flat Slab RFT.tab\Slab Rebar.panel"
git pull
pyrevit reload
```

## 4) Buttons Loaded

After reload, the `Flat Slab RFT` tab / `Slab Rebar` panel should show:

- `Flat Slab RFT`
- `Slab Rebar Views`

## 5) Tests

Main test suite (for `FlatSlabRebar.pushbutton`) is in:

- `FlatSlabRebar.pushbutton/tests`

Run:

```powershell
cd "C:\Users\<YOU>\AppData\Roaming\pyRevit-Master\extensions\FlatSlabRFT.extension\Flat Slab RFT.tab\Slab Rebar.panel"
python -m pytest -q FlatSlabRebar.pushbutton/tests
```
