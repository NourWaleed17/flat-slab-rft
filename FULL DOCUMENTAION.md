FlatSlabRFT Extension — Complete Technical Documentation
1. Architecture Overview
The extension contains two independent pyRevit buttons under FlatSlabRFT.tab > SlabRebar.panel:


FlatSlabRFT.extension/
└── FlatSlabRFT.tab/
    └── SlabRebar.panel/
        ├── FlatSlabRebar.pushbutton/     ← Button 1: places all rebar in the slab
        └── SlabRebarViews.pushbutton/    ← Button 2: creates annotated plan views
They share no runtime state and can be used independently or in sequence. All code runs in IronPython 2.7 inside Revit's pyRevit engine. No CPython, no async, no threads.

2. Button 1 — FlatSlabRebar
2.1 Purpose
Automatically places reinforcement bars for an entire flat slab in one click. Handles: main mesh (Bottom X/Y, Top X/Y), additional rebar from detail groups, and drop panel rebar. Outputs: Rebar elements in the Revit model with marks set.

2.2 Module Map

script.py          ← orchestrator — owns the pipeline and all Revit transactions
│
├── ui.py          ← WPF dialog — collects user inputs, returns params dict
├── geometry.py    ← pure geometry — slab polygon, shafts, DPs, support positions
├── bar_generator.py  ← pure math — generates scanline row dicts (no Revit API)
├── obstacle_processor.py  ← pure math — clips rows to slab, splits at shafts/DPs
├── splice_processor.py    ← pure math — splits long segments at 12m limit with laps
├── rebar_placer.py        ← Revit API — creates Rebar elements, grouping into sets
├── dp_rebar_placer.py     ← Revit API — creates DP rebar (staple + straight bars)
├── add_rft_reader.py      ← Revit API read — parses detail group family instances
└── debug_preview.py       ← Revit API write — draws DetailLine preview, then clears
2.3 The params Dict — Central Data Contract
ui.collect_inputs() returns a params dict that flows through the entire pipeline:

Key	Type	Source	Used by
diameter	float (ft)	UI combo	bar_generator, rebar_placer, splice_processor
spacing	float (ft)	UI input	bar_generator, rebar_placer
bar_length	float (ft)	UI input	splice_processor max_body calculation
splice_length	float (ft)	UI input	splice_processor lap length
ld	float (ft)	derived from splice_length	obstacle_processor (DP penetration), splice_processor
bar_type	RebarBarType	UI combo	rebar_placer.place_segment
hook_type	RebarHookType	UI combo	rebar_placer (passed to Rebar.CreateFromCurves)
placement_type	str	UI radio	script.py (Mesh RFT / Add RFT / Both)
run_mode	str	UI radio	script.py (Place Directly / Preview + Confirm / Preview Only / Place DP Only)
cover_bottom	float (ft)	floor BIP	script.py Z elevation calc, rebar_placer J-bar check
cover_top	float (ft)	floor BIP	script.py Z elevation calc
cover	float (ft)	min(bottom,top)	bar_generator row inset, obstacle_processor lateral margin, splice hook_ext, dp_rebar_placer
slab_top_z	float (ft)	geometry.get_slab_data	rebar_placer._get_vertical_leg_delta
slab_bottom_z	float (ft)	geometry.get_slab_data	rebar_placer._get_vertical_leg_delta
slab_thickness	float (ft)	geometry.get_slab_data	splice_processor hook_ext, dp_rebar_placer
stagger_splices	bool	hardcoded True	splice_processor stagger phase, rebar_placer comment_queue
standard_bar_lengths_m	list[float]	UI or default [12,9,6]	splice_processor stock snap
add_rft_entries	list[dict]	UI group picker	script.py add-rft loop
preview_max_lines	int	UI	debug_preview
2.4 Pipeline — Step by Step
Step 1 — User selects the slab floor
revit.pick_element() → validates it is a DB.Floor → reads cover BIPs from floor element.

Cover is read from built-in parameters tried in order:

Bottom: CONCRETE_COVER_BOTTOM_FACE → CLEAR_COVER_BOTTOM → CLEAR_COVER_OTHER
Top: CONCRETE_COVER_TOP_FACE → CLEAR_COVER_TOP → CLEAR_COVER_OTHER
Fallback: 25mm if parameter missing
Step 2 — UI dialog (ui.collect_inputs)
Dark WPF dialog. User sets diameter, spacing, bar length, splice length, bar type, hook type, run mode, placement type, add-rft groups. Returns params dict. Cover values injected by script.py after the dialog closes (they override the UI if present).

Step 3 — Geometry extraction (geometry.get_slab_data)
Input: selected Floor element

Output:


{
    'outer_polygon':        [(x,y), ...],   # 2D polygon from sketch largest loop
    'sketch_void_polygons': [[(x,y),...]], # smaller sketch loops = openings
    'top_z':   float,   # bb.Max.Z
    'bottom_z': float,  # top_z - thickness
    'thickness': float, # from FloorType parameter, fallback bbox height
    'bbox':     (min_x, min_y, max_x, max_y),
}
Sketch Profile is a CurveArrArray. Each CurveArray is tessellated to a 2D polygon. Largest area = outer boundary; smaller areas = sketch voids.

Then three more geometry queries run:

get_shaft_opening_polygons → Opening collector + BoundaryCurves tessellation → list of 2D polygons
get_drop_panel_data → Floor collector with BoundingBoxIntersectsFilter → two-pass (strict then relaxed tolerance) DP detection → list of dp_data dicts
get_support_positions_2d → FamilyInstance (structural columns) + Wall (structural walls) → list of (x,y) midpoints
dp_data dict structure:


{
    'polygon':   [(x,y), ...],  # DP outer polygon from sketch
    'thickness': float,         # from FloorType
    'bbox':      (min_x, min_y, max_x, max_y),
    'floor':     Floor element, # for ensure_dp_joins
    'top_z':     float,
    'bottom_z':  float,
    'slab_top_z': float,
}
DP detection logic (two tolerance passes):

Strict: top_z match ±20mm, thickness tolerance 3mm
Relaxed (automatic fallback if strict finds nothing): ±120mm, 60mm
Acceptance criteria: top_z within tolerance OR bottom_z below slab_bottom (drop-only model)
Must be strictly thicker than slab OR extend below slab bottom
Center or any vertex must be inside slab polygon
ensure_dp_joins is called inside a Transaction to fix join order so DP cuts the slab (not the reverse).

Step 4 — Bar row generation (bar_generator.generate_bar_rows)
Input: bbox=(min_x,min_y,max_x,max_y), spacing (ft), cover (ft), direction='X'/'Y'

Output: list of row dicts


{
    'fixed_val': float,  # Y coordinate (X bars) or X coordinate (Y bars)
    'vary_min':  float,  # bbox min + cover
    'vary_max':  float,  # bbox max - cover
    'direction': 'X' or 'Y',
    'index':     int,    # row number — used for stagger offset
}
Pure math — no Revit API. Generates one dict per scanline from min+cover to max-cover at spacing intervals. Called 4 times: Bottom X, Bottom Y, Top X, Top Y.

Z elevation assignment (done by script.py after generation):


z_bottom_x = bottom_z + cover_bottom           ← first layer
z_bottom_y = z_bottom_x + diameter             ← second layer (above first)
z_top_x    = top_z    - cover_top              ← third layer
z_top_y    = z_top_x  - diameter               ← fourth layer (below third)
Step 5 — Obstacle processing (obstacle_processor.process_bar_row)
Input per row: row dict, outer_polygon, shaft_polygons, dp_data_list, params, mesh_layer

Output: list of segment dicts

Pre-computation (done ONCE before all rows, not per-row):


_obstacle_cache = build_obstacle_cache(shaft_polygons, dp_data_list)
# stores {shaft_bboxes: [...], dp_bboxes: [...]}
For each row, the pipeline is:

clip_bar_to_slab_intervals → clips scanline to slab outer polygon → [(start, end), ...] (multiple intervals for concave/stepped slabs)
For each slab interval: shaft intervals via get_obstacle_intervals (with bbox pre-filter O(1) skip), merged
DP intervals similarly (only for mesh_layer == 'bottom')
split_bar_row → produces segments with hook flags
split_bar_row rules:

Slab edge → start_hook=True, end_hook=True (unless no_hooks=True for add-rft bars)
Shaft → bar terminates at shaft edge with hook on both sides; gap; resumes after shaft with hook
Drop panel (bottom only) → bar penetrates ld (or dp_width/2) into DP, gap, bar resumes from dp_width/2 before exit face; both penetration ends are straight (no hook)
Segment dict structure:


{
    'start':      float,       # vary_axis start coordinate (ft)
    'end':        float,       # vary_axis end coordinate
    'fixed_val':  float,       # constant axis coordinate
    'direction':  'X' or 'Y',
    'z':          float,       # elevation (ft)
    'index':      int,         # row index (for stagger)
    'start_hook': bool,
    'end_hook':   bool,
    'mesh_layer': 'top'/'bottom',
    'dp_intervals': [(a,b),...],  # top bars only — for splice zone checking
    # propagated from bar_row if present:
    'spacing_ft': float,    # used by rebar_placer for set grouping
    'diam_mm':    int,      # add-rft only
    'is_add_rft': bool,     # add-rft only
    'leg_ft':     float,    # add-rft J-bar leg length
    'has_hook':   bool,     # add-rft
    'hook_at_max': bool,    # add-rft — which end the hook is on
}
Step 6 — Splice processing (splice_processor.process_splices)
Input: list of segments, params, support_positions

Output: list of segments (segments > max_bar_body are replaced by 2+ shorter segments)

Pre-computed once:

_ld = lap development length
_max_body_1h = max straight body when one end is hooked
support_x, support_y = sorted column/wall X or Y coordinates
For each segment, _split_segment:

Computes max_body_nosplit accounting for hooks and leg. If segment fits → return as-is.
Computes effective_step = max_body - ld — the net advance per bar
Generates greedy-fill natural splice positions: always fill to max_body first (not evenly)
Per splice position:
Zone snap: for bottom bars → move toward nearest column (±L_bay/3); for top bars → push away from DP zone boundaries
Stock snap: nudge so sub-bar body matches a standard length (12m, 9m, 6m) if within 75–100% of max_body
Stagger: for index % 2 == 0 rows offset by −0.5×ld; odd rows offset by +0.5×ld (first splice never staggered)
Clamp: [0.75 × max_body, max_body] from prev_end
Output sub-segment extra keys:


{
    'splice_end': True,           # this bar's end is a splice joint
    'splice_length_used': float,  # actual lap used (1.0× or 1.3× ld for danger zones)
}
Step 7 — Additional rebar (add_rft_reader)
add_rft_reader.read_add_rft_group(group, layer, direction_hint) reads each FamilyInstance inside the detail group. It:

Reads Label parameter → parse_label → [(diam_mm, spacing_mm), ...]
Reads geometry lines (bar line + distribution line) from instance geometry to find origin, bar_direction, dist_direction, dist_ft, bar_arm_ft
Returns list of spec dicts, one per bar diameter in the label
generate_add_rft_rows(specs, z_bx, z_by, z_tx, z_ty) converts specs to row dicts (same format as bar_generator rows, with is_add_rft=True, no_hooks=True). These rows flow through obstacle_processor and splice_processor same as mesh rows.

Add-rft rows are grouped by diam_mm so find_bar_type_by_diameter(doc, diam_mm) can look up the matching RebarBarType for each size.

Step 8 — Rebar placement (rebar_placer.place_all_slab_bars)
Input: doc, floor, all_segments (list), bar_type, hook_type, params

Output: (placed_count, failed_count, set_count)

Grouping into slice keys (pure Python, no API):


_slice_key(seg) = (direction, z_quantized, start_quantized, end_quantized, start_hook, end_hook, hook_at_max)
Segments with identical slice key are uniform bars at the same position → candidates for SetLayoutAsNumberWithSpacing.

_split_contiguous_blocks further splits each group by spacing continuity: a gap larger than spacing ± tol starts a new block.

Placement loop (in batches of 200):
For each batch → Transaction.Start() → _configure_fast_transaction (suppresses all failure dialogs via _SilentFailuresPreprocessor) → for each block:

Place base_seg → Rebar.CreateFromCurves(doc, RebarStyle.Standard, bar_type, None, None, floor, normal, curves, ...)
If block has multiple rows with uniform spacing → GetShapeDrivenAccessor().SetLayoutAsNumberWithSpacing(count, spacing, ...) — makes one rebar SET with N bars
Otherwise → place each segment individually
Append (ElementId, mark_text) to mark_queue (does NOT set mark inside this transaction) → Transaction.Commit()
After all batches: one final Transaction('Set Rebar Marks') applies all marks and stagger comments. This is deferred because Revit's shape-registration regeneration at commit resets marks set during placement.

Bar geometry:

Straight bar: [p1, p2]
Edge hook bar: [p1+dz_leg, p1, p2] or [p1, p2, p2+dz_leg]
J-bar (add-rft): [p_straight, p_hook_top, p_hook_bot, p_return]
Normal vector: XYZ(0,1,0) for X bars (bar in XZ plane), XYZ(1,0,0) for Y bars (bar in YZ plane).

Step 9 — Drop panel rebar (dp_rebar_placer.place_all_dp_bars)
For each dp_data, generates bar rows across the DP polygon in both X and Y using generate_dp_bar_rows. Two bar types per direction:

Staple bars: J-shaped bars that extend down the DP face and return — has_hook=True, leg_ft = dp_extra_thickness - cover
Straight fallback: when staple bar is too short — straight bars across DP zone
Same _SilentFailuresPreprocessor pattern. Runs inside Transaction('Place Drop Panel Rebar').

3. Button 2 — SlabRebarViews
3.1 Purpose
Creates annotated plan views for each rebar layer. One button click → up to 10 duplicated plan views, each showing only one mark's bars, with bending detail + distribution dimension + donut + tag for every rebar element.

3.2 Module Map

script.py          ← orchestrator, 3 transactions
views_ui.py        ← WPF dialog — selects views, template, tag family
view_creator.py    ← Revit API — duplicates active view, renames, applies template
filter_creator.py  ← Revit API — creates/reuses ParameterFilterElements per mark
detail_placer.py   ← Revit API — places bending details, dimensions, donuts, tags
3.3 Data Flow

views_ui.collect_inputs(doc, all_suffixes)
    └── returns {selected_suffixes, view_template_id, tag_family_symbol}

view_creator.create_all_views(doc, active_view, view_template_id, selected_suffixes)
    └── returns views_dict = {'Bottom X': View, 'Bottom Y': View, ...}

filter_creator.apply_all_filters(doc, views_dict)
    └── creates/reuses ParameterFilterElement per mark (2 per mark: show + hide)
    └── applies to each view: show matching mark, hide others

detail_placer.place_all_details(doc, views_dict, tag_family_symbol)
    └── ONE doc-scoped FilteredElementCollector(doc).OfClass(Rebar) → bars_by_mark dict
    └── for each mark/view: annotates every rebar element individually
3.4 views_dict — Central Data Structure

{
    'Bottom X':    View element,
    'Bottom Y':    View element,
    'Top X':       View element,
    'Top Y':       View element,
    'Add Bottom X': View element,
    'Add Bottom Y': View element,
    'Add Top X':   View element,
    'Add Top Y':   View element,
    'Drop Panel X': View element,
    'Drop Panel Y': View element,
}
Keys are mark strings (same values used as ALL_MODEL_MARK on rebar elements). Values are newly-duplicated ViewPlan elements.

3.5 Stage 1 — View Creation (view_creator)
_clear_copy_name_conflicts runs a FilteredElementCollector(doc).OfClass(ViewPlan) to rename any views named '<source> Copy N' to '_slabRFT_<id>' — prevents Revit naming collision when duplicating.

For each selected suffix:

active_view.Duplicate(ViewDuplicateOption.Duplicate) → new view id
doc.GetElement(new_view_id) → new view
_try_set_view_name → sets name via VIEW_NAME BIP (retries up to 200 times with (N) suffix on collision)
new_view.ViewTemplateId = view_template_id (if selected) — applies template settings
Timing note: each Duplicate triggers a Revit regeneration. Each ViewTemplateId assignment applies all template settings (visibility, filters, overrides) — expensive for complex templates.

3.6 Stage 2 — Filter Application (filter_creator)
Pre-collect ALL existing ParameterFilterElements into existing_filters = {name: element} dict (ONE scan).

For each mark, 2 filters are created/reused:

SlabRFT_{mark}_show → ParameterFilterRuleFactory.CreateEqualsRule(ALL_MODEL_MARK, mark_value) → SetFilterVisibility(True)
SlabRFT_{mark}_hide → CreateNotEqualsRule(...) → SetFilterVisibility(False)
Newly created filters are written back into existing_filters so subsequent marks can reuse them without another scan.

3.7 Stage 3 — Detail Placement (detail_placer)
Performance-critical initialization:


detail_type = FilteredElementCollector(doc).OfClass(RebarBendingDetailType).ToElements()[0]  # cached once
frt_cache   = FilteredElementCollector(doc).OfClass(FilledRegionType).FirstElement()          # cached once
bars_by_mark = {mark: [Rebar, ...]}  # ONE doc-scoped collector, grouped by mark
For each mark/view, all rebar elements are split:

rebar_sets = [b for b in all_bars if _rebar_qty(b) > 1]
individual_bars = [b for b in all_bars if _rebar_qty(b) == 1]
Then for bar in rebar_sets + individual_bars → _annotate_one_set(bar):

_annotate_one_set per element:

place_bending_detail → RebarBendingDetail.Create(doc, view.Id, rebar.Id, bar_index, detail_type, origin, scale) → sets Align to Bar=1, Angle=0, Tag Position=Top, Tag Alignment=View, Tag Offset=2mm
_get_rebar_zone_extent(bar, dist_axis) → from get_BoundingBox(None):
X bars (dist_axis='Y'): zone_min=bb.Min.Y, zone_max=bb.Max.Y, perp=bb.Min.X + (bb.Max.X-bb.Min.X)/4
Returns None for qty=1 (zone span ~0 = single bar has no distribution zone)
If zone exists → place_distribution_dimension → 2 NewDetailCurve anchors + doc.Create.NewDimension
If zone exists → place_donut at zone_min + span/3.0 → FilledRegion.Create(doc, frt.Id, view.Id, [outer_loop])
ONE rebar tag per mark: IndependentTag.Create(doc, tag_symbol.Id, view.Id, Reference(all_bars[0]), ...)

4. Cross-Button Data Flow Diagram

User clicks FlatSlabRebar
    │
    ├─ ui.collect_inputs() ──────────────────────────────── params dict
    ├─ geometry.get_slab_data(floor) ───────────────────── slab_data dict
    ├─ geometry.get_shaft_opening_polygons() ────────────── shaft_polygons
    ├─ geometry.get_drop_panel_data() ───────────────────── dp_data_list
    ├─ geometry.get_support_positions_2d() ──────────────── support_positions
    │
    ├─ bar_generator.generate_bar_rows() ── row dicts [fixed_val, vary_min, vary_max, direction, index]
    │       ↓
    ├─ obstacle_processor.process_bar_row() ── segment dicts [start, end, fixed_val, z, direction, hooks]
    │       ↓
    ├─ splice_processor.process_splices() ── final_segments [+ splice_end, splice_length_used]
    │       ↓
    ├─ rebar_placer.place_all_slab_bars() ── Rebar elements in Revit model with marks
    │
    └─ dp_rebar_placer.place_all_dp_bars() ── DP Rebar elements in Revit model

User clicks SlabRebarViews
    │
    ├─ view_creator.create_all_views() ── views_dict {mark: View}
    ├─ filter_creator.apply_all_filters() ── ParameterFilterElements on views
    └─ detail_placer.place_all_details() ── RebarBendingDetail, Dimension, FilledRegion, IndependentTag
5. Key Design Decisions
Decision	Reason
_SilentFailuresPreprocessor in every placement transaction	Prevents Revit blocking modal dialogs from "rebar out of host" or "Can't solve Rebar Shape" errors. Without it, one bad bar locks the document.
Marks applied in a separate post-placement transaction	Revit's shape-registration regeneration at commit resets the Mark parameter. Setting it after all shapes register prevents this.
Greedy-fill splice positions (first bar fills to max_body)	Prevents short leading bars; minimises the number of cut bars needed from stock.
Two-pass DP detection (strict then relaxed)	Handles both "DP modeled at same elevation as slab" and "DP modeled as extra-depth floor below slab" without user configuration.
build_obstacle_cache pre-computation	Avoids O(polygon_vertices) intersection test for every obstacle on every row. For 100 rows × 10 shafts, reduces from 1000 polygon tests to ~100 (only obstacles whose bbox straddles the scanline).
Batch placement (200 groups per transaction)	Spreads Revit's model regeneration across many small commits instead of one enormous commit that would take minutes and potentially run out of memory.
IronPython-compatible code throughout	No types.SimpleNamespace (Python 3.3+), no f-strings, no walrus operator. All .format() strings.
Unique module names per pushbutton (views_ui.py)	pyRevit's IronPython engine shares a module namespace across all pushbuttons in a session. Generic names like ui.py would collide.
6. Performance Hotspots for Next Optimization Phase
Button 1 — FlatSlabRebar
Hotspot	Location	Cost	Mitigation
Rebar.CreateFromCurves × N	rebar_placer.place_segment	High — Revit DB write + geometry registration	Already batched; _SilentFailuresPreprocessor prevents blocking. Consider larger batch size (500+).
SetLayoutAsNumberWithSpacing	rebar_placer._place_blocks	Medium — one API call replaces N individual bars	Already used; failing silently to SetLayoutAsMaximumSpacing
apply_mark_queue — N individual mark sets	rebar_placer.apply_mark_queue	Medium — N param.Set() calls in one transaction	Could use param.Set() on sets only (1 set = 1 mark) instead of per-bar
FilteredElementCollector.OfClass(Floor) for DP detection	geometry._collect_drop_panel_data	Medium — scanned twice (strict + relaxed pass)	Already uses BoundingBoxIntersectsFilter; relaxed pass only runs if strict finds nothing
polygon_area + _curve_array_to_polygon for every curve array	geometry.get_slab_data / _collect_drop_panel_data	Low-medium	Called once; not a bottleneck
_split_segment per segment	splice_processor	Low — pure Python math	Could vectorize with a lookup table for common segment lengths
Button 2 — SlabRebarViews
Hotspot	Location	Cost	Mitigation
active_view.Duplicate() × N	view_creator	Very high — each triggers Revit regeneration	Can only parallelize inside one transaction. Skip if view name already exists (re-run optimization).
new_view.ViewTemplateId = ... × N	view_creator	High — applies all template settings per view	Move to separate transaction; try assigning template before Duplicate via source view
ParameterFilterElement.Create × 20	filter_creator	Medium — DB writes, 2 per mark	Already cached (1 scan, existing filters reused)
RebarBendingDetail.Create × (sets_per_mark × marks)	detail_placer	High — complex Revit annotation + geometry	origin computed from GetCenterlineCurves — can skip; use bbox midpoint since Align to Bar repositions it
GetCenterlineCurves per SET in place_bending_detail	detail_placer	Medium — 3D geometry computation	Replace with get_BoundingBox(None) midpoint
NewDetailCurve × 2 + NewDimension × 1 per SET	place_distribution_dimension	Medium — 3 element creations with Reference geometry	Already minimal; cannot avoid if dimension is required
_clear_copy_name_conflicts scan	view_creator	Low-medium — full ViewPlan scan + rename loop	Add name check before scan; skip if no <source> Copy N views exist
7. Input Requirements Summary
Button 1 — FlatSlabRebar
A selected Floor element with a sketch (slab boundary)
Bar diameter (from RebarBarType families loaded in the model)
Spacing (mm) — applied uniformly to all mesh directions
Bar length (mm) — stock bar length (typically 12000mm)
Splice/lap length (mm) — development length Ld
Run mode: Place Directly / Preview + Confirm / Preview Only / Place DP Only
Placement type: Mesh RFT / Add RFT / Both
(Optional) Add-RFT detail group(s) — FamilyInstance detail components in the model
Slab must have CONCRETE_COVER_BOTTOM_FACE/TOP_FACE parameters set, or defaults to 25mm
Drop panels modeled as separate Floor elements (same or adjacent elevation, thicker or extending below)
Button 2 — SlabRebarViews
An active duplicable plan view (source for all 10 duplicates)
Rebar elements already placed in the model with ALL_MODEL_MARK parameter set to one of the 10 known mark strings (Bottom X, Bottom Y, Top X, Top Y, Add Bottom X/Y, Add Top X/Y, Drop Panel X/Y)
(Optional) A ViewPlan view template to apply to each created view
(Optional) A rebar tag FamilySymbol loaded in the model (category OST_RebarTags)