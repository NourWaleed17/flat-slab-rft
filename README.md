# FlatSlabRFT — pyRevit Extension

Autodesk Revit automation for placing reinforcement in flat-slab floors.
Written in IronPython / pyRevit.

---

## What it does

Two pushbuttons under **Flat Slab RFT → Slab Rebar**:

| Button | Purpose |
|---|---|
| **Flat Slab RFT** | Auto-places bottom + top mesh bars, drop-panel U-bars, and additional detail-component bars in one click |
| **Slab Rebar Views** | Generates 10 filtered plan views (one per rebar mark) with bending details, distribution dimensions, and donuts |

---

## Architecture

```
FlatSlabRFT.extension/
└── Flat Slab RFT.tab/
    └── Slab Rebar.panel/
        ├── FlatSlabRebar.pushbutton/      ← main placement tool
        │   ├── script.py                  entry point, orchestration
        │   ├── ui.py                      WinForms input dialogs
        │   ├── geometry.py                polygon math, DP/shaft extraction
        │   ├── bar_generator.py           grid-based row generation
        │   ├── obstacle_processor.py      slab clip + shaft/DP punch-outs
        │   ├── splice_processor.py        12 m bar-length splits + zone snapping
        │   ├── rebar_placer.py            Revit Rebar.CreateFromCurves wrapper
        │   ├── dp_rebar_placer.py         drop-panel staple/straight bars
        │   ├── add_rft_reader.py          detail-group → bar spec parser
        │   ├── debug_preview.py           preview DetailCurves
        │   └── tests/                     424 pytest tests (no Revit required)
        └── SlabRebarViews.pushbutton/     ← view generation tool
            ├── script.py
            ├── ui.py
            ├── view_creator.py
            ├── filter_creator.py
            ├── detail_placer.py
            └── tests/
```

### Data flow (FlatSlabRebar)

```
User input (ui.py)
      │ params dict
      ▼
Geometry extraction (geometry.py)
  slab polygon, shaft polygons, drop-panel data, support positions
      │
      ▼
Bar generation (bar_generator.py)
  list of row dicts {fixed_val, vary_min/max, direction, index}
      │
      ▼
Obstacle processing (obstacle_processor.py)   ← Stage 1
  clips each row to slab, punches gaps at shafts/DPs
  → list of segment dicts {start, end, hooks, mesh_layer, …}
      │
      ▼
Splice processing (splice_processor.py)        ← Stage 2
  splits segments longer than 12 m, snaps splices to structural zones
  → refined segment list
      │
      ▼
Rebar placement (rebar_placer.py / dp_rebar_placer.py)
  Revit transactions + failure handling
  → Rebar elements in model
```

---

## Prerequisites

| Requirement | Version |
|---|---|
| Autodesk Revit | 2022 – 2026 |
| pyRevit | 4.8+ (CPython or IronPython) |
| Python (for tests only) | 3.8+ |
| pytest (for tests only) | 7.0+ |

---

## Installation

### On the development / source PC

The extension is already installed at:
```
%APPDATA%\pyRevit-Master\extensions\FlatSlabRFT.extension\
```

To push to GitHub:
```bash
cd "%APPDATA%\pyRevit-Master\extensions\FlatSlabRFT.extension"
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USERNAME/FlatSlabRFT.extension.git
git push -u origin main
```

> The folder name **must** keep the `.extension` suffix — pyRevit uses it for discovery.

---

### On the test / work PC

**Step 1 — Install pyRevit**

Download from https://github.com/pyrevitlabs/pyRevit/releases and install.
Confirm it appears in Revit's ribbon after the first launch.

**Step 2 — Clone the extension**

Open a terminal and run:
```bash
cd "%APPDATA%\pyRevit-Master\extensions"
git clone https://github.com/YOUR_USERNAME/FlatSlabRFT.extension.git
```

The cloned folder name **must** be `FlatSlabRFT.extension` (git clone preserves the repo name by default).

**Step 3 — Reload pyRevit**

Inside Revit: **pyRevit tab → Reload** (or restart Revit).
The **Flat Slab RFT** tab should appear.

**Step 4 — Install Python + pytest (optional, for tests)**

```bash
pip install pytest
```

---

## Running tests (no Revit required)

All pure-Python logic is covered by 424+ unit tests that run without a Revit
installation. Every Revit API call is stubbed.

```bash
cd "%APPDATA%\pyRevit-Master\extensions\FlatSlabRFT.extension\Flat Slab RFT.tab\Slab Rebar.panel\FlatSlabRebar.pushbutton"
python -m pytest tests/ -v
```

Expected output: **424+ passed**.

To run a single test file:
```bash
python -m pytest tests/test_splice_logic.py -v
python -m pytest tests/test_obstacle_processor.py -v
python -m pytest tests/test_pipeline_integration.py -v
```

---

## Usage

### Flat Slab RFT button

1. Open a structural plan view.
2. Click **Flat Slab RFT**.
3. Select the main slab floor element.
4. Answer the input dialogs:
   - Placement type: *Mesh RFT*, *Add RFT*, or *Both*
   - Bar diameter, spacing, cover (mm)
   - Standard bar length (m) — default 12 m
   - Splice and Ld multipliers (× diameter)
   - Drop panel horizontal leg (mm)
   - Hook type and bar type
   - Run mode: *Place Directly* / *Preview + Confirm* / *Preview Only* / *Place DP Only*
5. Confirm the summary dialog.

### Slab Rebar Views button

1. Open the plan view to be duplicated.
2. Click **Slab Rebar Views**.
3. Select a view template and rebar tag family.
4. Choose which of the 10 mark views to generate.
5. Views are created in the Project Browser under the original view's category.

---

## Key design decisions

| Decision | Reason |
|---|---|
| Two-transaction mark fix | `createNewShape=True` triggers model regen at commit, wiping marks; marks are applied in a second transaction after all placement |
| `False, True` flags on `CreateFromCurves` | Only valid flag combo for bent/J-bar shapes; Dmin warnings suppressed by `_SilentFailuresPreprocessor` |
| `IFailuresPreprocessor` | Prevents all modal dialogs; resolves or discards warnings automatically |
| Batched transactions (`_BATCH_SIZE = 200`) | Prevents Revit from hanging on a single massive commit |
| Hook ext = `slab_thickness − 2 × cover` | Slab-edge hooks fold down the full internal depth; this is the total steel projection to deduct from the 12 m budget |
| DP detection two-pass (strict → relaxed) | Handles both flush-top and slightly-below-top DP modeling conventions |

---

## Known limitations

- Cannot place rebar in linked RVT files (Revit API limitation).
- `FamilySymbol.Activate()` is required inside a transaction before placing J-bar families.
- CefSharp removed in Revit 2026 — WebView2 must be used for any web views.
- "Can't solve Rebar Shape" errors from Revit fire as post-commit events; they cannot be caught by `IFailuresPreprocessor`.

---

## Updating on the test PC

```bash
cd "%APPDATA%\pyRevit-Master\extensions\FlatSlabRFT.extension"
git pull origin main
```

Then reload pyRevit inside Revit.

---

## Contributing / workflow

1. Develop on the dev PC, run `python -m pytest tests/ -q` before every commit.
2. Push to `main` (or a feature branch + PR).
3. Pull on test PC, reload pyRevit, smoke-test in Revit.
