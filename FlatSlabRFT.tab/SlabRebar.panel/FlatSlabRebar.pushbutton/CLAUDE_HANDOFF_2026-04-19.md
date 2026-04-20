# FlatSlabRFT Handoff (2026-04-19)

## Why this change was needed
- pyRevit showed: `Can not find target file. Maybe deleted?`
- Root cause: `FlatSlabRebar.pushbutton` (outer pyRevit bundle folder) did not contain `script.py`.
- The executable code currently lives in nested path:
  - `FlatSlabRebar.pushbutton/FlatSlabRebar.pushbutton/script.py`

## What was changed
1. Added outer launcher script:
   - `FlatSlabRebar.pushbutton/script.py`
2. Launcher behavior:
   - Resolves inner folder `FlatSlabRebar.pushbutton/FlatSlabRebar.pushbutton`
   - Adds that folder to `sys.path`
   - Loads inner `script.py`
   - Calls its `main()`

## Why this is safe
- No business logic moved or rewritten.
- Existing inner implementation remains the single source of truth.
- This only restores pyRevit's expected entry-point location.

## Existing in-progress work (not part of launcher fix)
- `FlatSlabRebar.pushbutton/FlatSlabRebar.pushbutton/ui.py` is still modified and not committed.
- Recent continuation added:
  - Validation: bar length must be greater than spacing (mesh modes)
  - Add-RFT guard: explicit cancel message when no detail groups are found

## Suggested follow-up
1. Reload pyRevit and verify `Flat Slab RFT` button opens without loader error.
2. Smoke-test both buttons:
   - `Flat Slab RFT`
   - `Slab Rebar Views`
3. Decide whether to flatten folder structure later (optional cleanup), now that launcher unblocks execution.
