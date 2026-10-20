# Stainless

<img src="src/stainless/assets/stainless.png" width="96" align="right" alt="Stainless icon">

Desktop GUI for the **stained area of brightfield whole-slide scans** (Zeiss CZI): drop the
scans, draw the regions to measure (an infarct, a border zone, remote tissue), and get the
% of tissue stained in each section and region — one rule shared by the sections, which a
section can override when its staining calls for it — with a view that shows, live and at
full detail, what the rule calls stained.

Two stains:
- **DAB** immunohistochemistry with a **hematoxylin** counterstain (brown on blue);
- **SA-β-gal** (X-Gal, blue-green) without counterstain.

Made on Axio Scan.Z1 scans (Plan-Apochromat 20×/0.8 and 10×/0.45); any brightfield RGB CZI
mosaic is read.

## Install (Windows)

No admin rights, no PATH or registry changes, nothing written into your user profile.

1. On the GitHub page: **Code ▸ Download ZIP**.
2. **Right-click the ZIP ▸ Properties ▸ tick *Unblock* ▸ OK** — without it, SmartScreen stops
   the launcher with *"Windows protected your PC"*.
3. Extract it where it can stay (`C:\Users\Public\Stainless` serves every account).
4. Double-click **`Stainless.bat`**. The first time it downloads
   [`uv`](https://github.com/astral-sh/uv), Python 3.13 and the libraries (a few hundred MB, a
   minute or two) into the shared folder `C:\ProgramData\PyApps`, which other tools installed
   this way reuse, and puts a **Stainless** shortcut on the Desktop. Use the shortcut from then
   on; to pin it, right-click its taskbar button while it runs ▸ *Pin to taskbar*.

Self-contained instead: run **`Stainless.bat private`** the first time, and everything goes
into `C:\Users\Public\Stainless` (`STAINLESS_RUNTIME` picks another place).
**`uninstall.bat`** removes it: the whole private folder, or only Stainless's part of the
shared one. Python 3.13, not newer: the CZI reader (pylibCZIrw) has no newer build.

What Stainless remembers (**Options…**, Ctrl+,: the colours, what the side panel starts
with, the names offered for regions, what Find artefacts looks for, the aliases, the last
folder) is in `stainless_options.json` beside `Stainless.bat`; an internal error's details,
in `stainless_errors.log` there (send it along with a problem report).

## Use

**Drop CZI scans or folders anywhere on the window**, or **Add scans…** (Ctrl+I). Each scene
of a file is a section. Slide previews and snapshots (coarser than 2 µm/px) are left out,
with a line in the log. Hover anything for what it does; the log at the bottom says what
happened and what looks wrong (✗ ⚠) — drag the bar above it down to close it. Every bar
between the parts of the window can be dragged; the sizes are kept.

1. **Sections** — the list on the left, with each section's thumbnail and the slide's label as
   the scanner photographed it (turned 90°: Options › Sections). Give each a *group* (what
   the figure compares) and a *role*: sample, negative control, positive control, or left
   out. A file named "…neg…" or "…posit…" starts as a control. An **alias** is the name a
   section is shown and reported under (the scan's name stays beside it); **Shorten** gives
   the selected sections (all, if one is selected) their names without the words they all
   share — the date, the study, the stain. An alias is remembered and given again when the
   same scan is added to another experiment.

   The panel on the right is in blocks; click a block's title to fold it (which are folded
   at start: Options › Side panel).
   - **Stain**: DAB or SA-β-gal.
   - **Stained when**: the rule (below), shared by every section. **Zoom in** (mouse wheel)
     and the view reads the scan in detail and colours, live, what the rule calls stained;
     move the sliders and it follows. The line under the view gives the % in view.
     A section stained more strongly, or with more background, can have **its own rule**:
     switch on *this section only*, and the sliders set that section's alone (*Reset* puts
     it back on the shared rule). The list marks it *own rule*; the results give every row's
     threshold and say *own* or *shared*. **more** shows what counts as tissue: a pixel's
     own OD (with a counterstain) and the OD outlining the section.
   - **Resolution**: the scan's own pixels, ½ (2 × 2 averaged, the default) or ¼.
   - **Regions** are **ROIs** or **exclusions**. Pick one in the list — *ROI*, a name
     (Options › Regions offers sets of names, e.g. *MI*: infarct, border zone, remote) or
     *exclude* — and drawing starts (or **Draw**, D; the button stays down while drawing):
     **click** the corners of a polygon and double-click, press Enter or click the first
     corner to close it; or **hold the left button and trace**. Backspace takes the last
     corner back; **right-click** or Esc cancels.
     ROIs of one name are **one region** (pooled) in the results; without a name each is
     its own (ROI 1, ROI 2…). An exclusion takes its area out of the tissue everywhere.
     Click a region (or its name) to select it: its points show. Drag a point to move it;
     click its line to add one — on a traced outline, grabbing the line puts two points
     beside it, so the drag bends that stretch only; Del removes the point, or the region
     when no point was clicked. The exclusions are listed under *excluded*, folded. The
     square beside a name sets that ROI's colour (every ROI of the name).
     **Find artefacts** (this section, or all) looks for **tissue folds, bubbles and debris**
     and adds them as exclusions (*tissue fold 1*, *bubble 1*…; found again: replaced) —
     tissue absorbing far more than the tissue around it and not toward the stain. Check
     them: an edited or removed one stays so. What is looked for: Options › Artefacts.
   - **Show**: what is stained (the measured map; zoomed in, **live** — the button lights up
     and the slide says *LIVE*), what is not counted as tissue, the ROIs, the exclusions, a
     scale bar, each with its colour beside it. **White balance** *auto* (on: lit) shows
     each section's glass white; it changes only what is shown.
   - **Measure**: *This section*, or *All* — every section not measured yet, or measured with
     other settings or regions (Ctrl+M). Stop keeps what is finished. The table gives the
     ROIs and the **remaining tissue** (in none of them); **more** adds the whole section,
     the excluded tissue and how it was measured. A section measured with other settings
     than the current ones says so (in orange in the results).
2. **Results** — a row per section and region (the whole section, each ROI, the remaining
   tissue): tissue mm², stained mm², % stained, mean OD of the stained pixels, the scan. The
   figure shows one region: a dot per section, mean ± SD per group, controls apart in grey.
   **Copy** (Ctrl+C), **Export…** (Ctrl+E: `.xlsx` with the experiment and the settings, or
   `.csv`), **Save figure…** (PNG, SVG, PDF).
3. **Experiment** — what was done: title, operator, tissue, antibody, dilution, detection,
   chromogen, counterstain…; below, the settings it was measured with and what each scan says
   about itself (scanner, objective, date).

**Save** (Ctrl+S) writes the experiment as one `.json`: the sections (with paths relative to
it, so a folder moved with its scans opens the same), regions, record, settings and results,
maps included. **Open…** (Ctrl+O) or drop it on the window. Ctrl+Z / Ctrl+Y undo and redo.

## How it measures

Each pixel's absorbance (optical density, OD) is taken against **the section's own glass**,
measured on the slide (the brightest fifth of the scanned area), not against pure white: the
scanner's glass is grey-green (RGB ≈ 160, 162, 160), and an OD taken against 255 would carry
that tint into every pixel.

- **Tissue**: summed OD (R + G + B) ≥ 0.15, inside the section's outline (OD smoothed over
  ~16 µm ≥ 0.10; pieces under 0.02 mm² are dust), minus the exclusions. The view, zoomed in,
  uses the same outline.
- **Stained**: the absorbance **points toward the stain** — hue score ≥ the threshold — and is
  **dense enough** to be stain — summed OD ≥ the minimum. The hue score is (OD · w) / ΣOD:
  which way the absorbance points, independent of how much there is. For DAB, w = blue − red:
  brown absorbs blue more than red, hematoxylin the other way round, unstained grey tissue
  neither (0). For SA-β-gal, w = red − blue.
- **% stained** = stained px / tissue px × 100, in the whole section, in each ROI (ROIs of
  one name together) and in the remaining tissue (in no ROI).
- **Artefacts** (found on request, at 4 µm/px): tissue whose OD, smoothed over ~8 µm, is
  ≥ 1.6 × that of the tissue within ~250 µm and of the section's median, and doesn't point
  toward the stain. A fold is tissue lying on itself: twice the absorbance, the same colour.
  A dark ring around a light disc is a bubble; far darker than tissue (OD ≥ 1.2 and ≥ 3 ×
  the median), debris; else a fold. Tuned by eye on the VCAM-1 MI slides only.

Defaults, each set on real slides:
- **DAB**: hue ≥ 0.03, summed OD ≥ 0.20, tissue OD ≥ 0.15 — on the VCAM-1 MI slides, the
  negative control section reads 0.2 % of its tissue positive.
- **SA-β-gal**: hue ≥ 0.35, summed OD ≥ 0.08; without counterstain the unstained tissue is
  barely darker than the glass, so the whole outline is tissue (outline: smoothed OD ≥ 0.01).
  On 13 sections of mouse A549 tumours it ranks them as the earlier SA-β-gal pipeline did
  (Spearman 0.97, same median), in 7 s per section instead of ~5 min.

Why not colour deconvolution's DAB channel: on the VCAM-1 slides it measures darkness as
much as brownness, so the negative control's dark folds and nuclei force its threshold up. At
the threshold leaving 0.5 % of the control's infarct positive, the DAB channel found 1.3–2.5 %
of the stained infarct, the hue score 9–11 %.

Why a shared threshold, not one computed per section: a threshold computed from each
section's own pixels adapts to its staining, so a control and a treated section are not
measured with the same ruler, and a negative section always yields "positives". Where a
section really differs, its own rule is a deliberate, recorded choice.

Why ½ by default: at the scan's own pixels the camera's pixel noise passes the hue test —
the MI-21 negative control reads +66 %, a 10× scan up to 14 times more in one region — while
½ gives the same numbers as the scan's pixels smoothed by ~2 px, in a quarter of the time.
The scan's own pixels stay one click away.

## Dev

```bash
uv sync                                    # or the launcher, once
uv run python -m stainless                 # the GUI
uv run python -m stainless.analysis        # each module with logic has a self-check
uv run python misc/smoke_test.py [CZI]     # the whole window, headless (QT_QPA_PLATFORM=offscreen)
python misc/bump.py                        # stamp today's date as the version, before committing
python misc/make_icon.py                   # redraw the icon
```

`analysis.py` (the measurement, no Qt) · `czi.py` (scenes, reads, label) · `model.py` (the
experiment, its .json, exports, region editing) · `viewer.py` (the slide view) · `plot.py`
(the figure) · `options.py` (the options and their window) · `app.py` (the window). Version: CalVer `YYYY.M.D`, the date the shipping code last changed.
