# Stainless — user guide

What each part of the window does, in the order of the work, and how the measurement works
([How it measures](#how-it-measures)). Installing and the short path: [README.md](README.md).

- [The window](#the-window)
- [Sections](#sections)
- [The side panel](#the-side-panel): Stain · Stained when · Resolution · Regions · Show ·
  Measure
- [Results](#results)
- [Experiment](#experiment)
- [Saving and opening](#saving-and-opening)
- [Options](#options)
- [Shortcuts](#shortcuts)
- [When something looks wrong](#when-something-looks-wrong)

## The window

Three tabs, in the order of the work: **Sections** (the slides, their regions, the settings,
Measure) → **Results** → **Experiment** (the record of what was done). Drop anything anywhere
on the window: CZI scans, folders of them, or a saved experiment (`.json`).

Every bar between the parts of the window can be dragged, and the sizes are kept. The log at
the bottom says what happened and what looks wrong (✗ ⚠); drag the bar above it down to
close it.

## Sections

**Drop CZI scans or folders** on the window, or **Add scans…** (Ctrl+I). Each scene of a
file is a section. Slide previews and snapshots (coarser than 2 µm/px) are left out, with a
line in the log.

The list on the left shows each section with its thumbnail; below it, the selected section's
fields and the slide's label as the scanner photographed it (turned 90° by default: Options ›
Sections).

- **alias**: the name the section is shown and reported under in this experiment (empty: the
  scan's name). **Shorten** gives the selected sections (all, if one is selected) their names
  without the words they all share — the date, the study, the stain.
- **group**: what the figure compares (e.g. *MI 7 d*, *sham*).
- **role**: sample, negative control, positive control, or left out (not measured, not
  reported). A file named "…neg…" or "…posit…" starts as a control.
- **note**: anything about the section.

**Remove** (Del, in the list) takes the selected sections out of the experiment.

**Table…** (Ctrl+L) opens the sections in a window of their own, a row each: alias, scan's
name, group, role, note, state, % stained, tissue mm², each region's %, the number of ROIs
and exclusions, µm/px, size, file, folder. Double-click an alias, a group or a note to change
it; click a row to show that section; Ctrl+C copies the table. Right-click the header to show
or hide columns (which are shown at start: Options › Sections).

The slide: the mouse wheel zooms, dragging moves it, **F** fits it to the view.

## The side panel

The panel on the right is in blocks; click a block's title to fold it (which are folded at
start: Options › Side panel).

### Stain

**DAB** (brown, hematoxylin counterstain) or **SA-β-gal** (X-Gal, blue-green, no
counterstain). Changing it sets that stain's default rule.

### Stained when

The rule, **shared by every section**:

- **hue ≥**: how far the pixel's absorbance points toward the stain (0 = grey; hematoxylin is
  below 0 for DAB).
- **summed OD ≥**: dense enough to be stain rather than noise.

**Zoom in** and the view reads the scan in detail and colours, live, what the rule calls
stained; move the sliders and it follows. The line under the view gives the % stained in
view. Check the rule on a stained section and on a negative control.

A section stained more strongly, or with more background, can have **its own rule**: switch
on *this section only*, and the sliders set that section's alone. **Reset** puts it back on
the shared rule (or, on the shared rule, the stain's defaults). The list marks it *own rule*;
the results give every row's threshold and say *own* or *shared*.

**more** shows what counts as tissue, shared by every section: a pixel's own OD (**tissue:
OD ≥**, with a counterstain; 0 for SA-β-gal) and the OD outlining the section (**outline:
OD ≥**). *Show › not counted as tissue* shows the result.

### Resolution

The scan's own pixels, **½** (2 × 2 averaged, the default) or **¼** (4 × 4). At the scan's
own pixels the camera's noise reads as stain and it takes ~4× longer; ¼ is 4× faster again,
but thin stained structures start to fade.

### Regions

Without regions, the whole section is measured. Regions are **ROIs** (measured apart) or
**exclusions** (taken out of the tissue everywhere: folds, debris, bubbles).

**Drawing.** Pick what to draw in the list — *ROI*, a name (Options › Regions offers sets of
names, e.g. *MI*: infarct, border zone, remote), or *exclude* — and drawing starts (or
**Draw**, D; the button stays down while drawing). Then:

- **click** the corners of a polygon; double-click, press Enter, or click the first corner to
  close it;
- or **hold the left button and trace** the outline freehand.

Backspace takes the last corner back; **right-click** or Esc cancels. An outline that crosses
itself becomes the outline around all it encloses: no hole where it crossed.

**Names.** ROIs of one name are **one region** (pooled) in the results; without a name each
is its own (ROI 1, ROI 2…). Double-click a name in the list to change it. The exclusions are
listed under *excluded*, folded.

**Editing.** Click a region (or its name) to select it: its points show (the first click only
selects). Drag a point to move it; click the line to add one — on a traced outline, grabbing
the line puts two points beside it, so the drag bends that stretch only. Del removes the
point clicked last, or the region when no point was.

**Colours.** The square beside a name sets the colour of every ROI of that name, and how
opaque it is (alpha). The lines' style, width and fill: Options › Colours & lines.

**Find artefacts** (*This section* or *All*) looks for **tissue folds, bubbles and debris**
and adds them as exclusions (*tissue fold 1*, *bubble 1*…): tissue absorbing far more than
the tissue around it, and not toward the stain. Running it again replaces what it found;
the ones you edited or renamed stay (nothing is added inside them), a removed one comes back.
**Check what it adds**: dark tissue edges or a strongly
stained spot can be taken for a fold. What it looks for: Options › Artefacts.

### Show

What is drawn over the slide, each with its colour (and alpha) beside it:

- **stained**: what was measured; zoomed in, what the rule calls stained, **live** (the
  *live* button lights up and the slide says *LIVE*);
- **not counted as tissue**: glass, and what falls outside the section's outline;
- **ROIs**, **excluded**, **scale bar**.

**White balance** *auto* (lit: on) shows each section's glass white. It changes only what is
shown: the measurement takes the OD against the glass anyway.

### Measure

**This section**, or **All** — every section not measured yet, or measured with other
settings or regions (Ctrl+M). **Stop** keeps what is finished.

The table gives the ROIs and the **remaining tissue** (in none of them); **more** adds the
whole section, the excluded tissue, and how it was measured (µm/px, time, the glass). A
section measured with other settings than the current ones says so (in orange in the
results): measure it again.

## Results

A row per section and region (the whole section, each ROI, the remaining tissue): group,
role, tissue mm², stained mm², % stained, mean OD of the stained pixels, the rule it was
measured with, its state, the scan. Sections left out are not listed.

The figure shows one region (pick it above): a dot per section, mean ± SD per group, the
controls apart in grey.

- **Copy** (Ctrl+C): the table, to paste in a spreadsheet;
- **Export…** (Ctrl+E): `.xlsx` (with the experiment's record and settings, and what each scan
  says about itself) or `.csv`;
- **Save figure…**: PNG, SVG or PDF, on a white background.

## Experiment

What was done: title, date, operator, project, aim, tissue, target / antibody, clone,
dilution, detection, chromogen, counterstain, comment. Below: the settings it was measured
with, and what each scan says about itself (scanner, objective, date). All of it goes into
the `.xlsx` export.

## Saving and opening

**Save** (Ctrl+S) writes the experiment as one `.json`: the sections (with paths relative to
it, so a folder moved with its scans opens the same), regions, record, settings and results,
maps included. **Open…** (Ctrl+O), or drop it on the window. A scan that isn't found is kept
in the experiment, marked, and can't be shown or measured.

Ctrl+Z / Ctrl+Y undo and redo any change to the experiment.

## Options

**Options…** (Ctrl+,): topics on the left, a page each. Saved as they change, in
`stainless_options.json` beside `Stainless.bat`; **Export…** / **Import…** carry them to
another computer, **Reset to defaults** puts everything back.

| Topic | What it sets |
|---|---|
| Side panel | which blocks are folded at start, which *more* are open, what Show starts with |
| New experiment | the stain and resolution a new experiment starts with |
| Regions | the names offered for ROIs (sets of names, e.g. *MI*: infarct, border zone, remote) |
| Artefacts | how much darker than around a piece must be, the smallest piece, what is looked for |
| View | white balance level, the LIVE badge, the scale bar (where, length), exclusions shown as not tissue |
| Colours & lines | each overlay's colour and alpha; each line's style, width, colour and fill; the ROIs' colours |
| Sections | the slide label's rotation; the sections window's columns at start |

An experiment's own settings (stain, rule, resolution, aliases) are saved with the experiment,
not in the options.

## Shortcuts

| Keys | |
|---|---|
| Ctrl+I | add scans |
| Ctrl+O / Ctrl+S / Ctrl+Shift+S | open / save / save as |
| Ctrl+E | export the results |
| Ctrl+M | measure all |
| Ctrl+L | the sections window |
| Ctrl+, | options |
| Ctrl+Z / Ctrl+Y | undo / redo |
| Ctrl+C | copy the results table (Results) or the sections table |
| D | draw (on / off) |
| F | fit the slide to the view |
| Enter, double-click | close the polygon being drawn |
| Backspace | the last corner back, while drawing |
| Esc, right-click | cancel drawing |
| Del | the point clicked last, the selected region, or (in the list) the selected sections |

## When something looks wrong

- **A negative control shows stain**: zoom in on it and raise *hue ≥* or *summed OD ≥* until
  it is clean, then check the stained sections still are. Dark folds and debris: exclude them
  (Find artefacts, or draw them).
- **Glass counted as tissue** (seen with *Show › not counted as tissue*): raise *outline: OD ≥*
  under *Stained when › more*. SA-β-gal's outline is loose on glass that is unevenly lit.
- **"settings or regions changed"** on a section: it was measured with other settings; Measure
  again (All picks it up).
- **"not found"** on opening: the scan moved; keep the `.json` in the folder with its scans,
  or beside them, and open it again.
- **An internal error** (✗ in the log): its details are in `stainless_errors.log` beside
  `Stainless.bat`; send it along with a description.

## How it measures

Each pixel's absorbance (optical density, OD) is taken against **the section's own glass**,
measured on the slide (the brightest fifth of the scanned area), not against pure white: the
scanner's glass is grey-green (RGB ≈ 160, 162, 160), and an OD taken against 255 would carry
that tint into every pixel.

- **Tissue**: summed OD (R + G + B) ≥ 0.15, inside the section's outline (OD smoothed over
  ~16 µm ≥ 0.10; pieces under 0.02 mm² are dust), minus the exclusions (where two overlap,
  their union). The view, zoomed in, uses the same outline.
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
  the median), debris; else a fold. Tuned by eye on four DAB sections only: check what it
  adds.

Defaults, each set on real slides:
- **DAB**: hue ≥ 0.03, summed OD ≥ 0.20, tissue OD ≥ 0.15 — on the slides they were set on,
  a negative control section reads 0.2 % of its tissue positive.
- **SA-β-gal**: hue ≥ 0.35, summed OD ≥ 0.08; without counterstain the unstained tissue is
  barely darker than the glass, so the whole outline is tissue (outline: smoothed OD ≥ 0.01).
  On 13 sections of mouse A549 tumours it ranks them as the earlier SA-β-gal pipeline did
  (Spearman 0.97, same median), in 7 s per section instead of ~5 min.

Why not colour deconvolution's DAB channel: it measures darkness as much as brownness, so a
negative control's dark folds and nuclei force its threshold up. At the threshold leaving
0.5 % of a control positive, the DAB channel found 1.3–2.5 % of a stained region, the hue
score 9–11 %.

Why a shared threshold, not one computed per section: a threshold computed from each
section's own pixels adapts to its staining, so a control and a treated section are not
measured with the same ruler, and a negative section always yields "positives". Where a
section really differs, its own rule is a deliberate, recorded choice.

Why ½ by default: at the scan's own pixels the camera's pixel noise passes the hue test —
a negative control reads +66 %, a 10× scan up to 14 times more in one region — while
½ gives the same numbers as the scan's pixels smoothed by ~2 px, in a quarter of the time.
The scan's own pixels stay one click away.
