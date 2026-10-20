# Stainless

<img src="src/stainless/assets/stainless.png" width="96" align="right" alt="Stainless icon">

Desktop GUI for the **stained area of brightfield whole-slide scans** (Zeiss CZI): drop the
scans, draw the regions to measure, and get the % of tissue stained in each section and
region. One rule is shared by the sections (a section can have its own when its staining
calls for it), and the view shows, live and at full detail, what the rule calls stained.

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

Options (**Options…**, Ctrl+,) are kept in `stainless_options.json` beside `Stainless.bat`;
an internal error's details go to `stainless_errors.log` there (send it along with a problem
report).

## Use

1. **Drop CZI scans** (or folders of them) on the window. Each scene of a file is a section;
   give each a group and a role (sample or control).
2. **Choose the stain** (DAB or SA-β-gal) and check the rule: zoom in on a stained section
   and on a control, and the view colours, live, what the rule calls stained.
3. **Draw regions** if you need more than the whole section: ROIs to measure apart, and
   exclusions (folds, bubbles, debris) to take out of the tissue. **Find artefacts** finds
   folds, bubbles and debris for you.
4. **Measure**: *All* measures every section not measured yet.
5. **Results**: a row per section and region, a figure per group; **Export…** to Excel or
   CSV.
6. **Save** the experiment (one `.json`, results included).

Hover anything for what it does; the log at the bottom says what happened and what looks
wrong (✗ ⚠). The details, the shortcuts and the options are in the
**[user guide](docs/USER_GUIDE.md)**.

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

## Dev

```bash
uv sync                                    # or the launcher, once
uv run python -m stainless                 # the GUI
uv run python -m stainless.analysis        # each module with logic has a self-check
uv run python misc/smoke_test.py CZI       # the whole window, headless (QT_QPA_PLATFORM=offscreen)
python misc/bump.py                        # stamp today's date as the version, before committing
python misc/make_icon.py                   # redraw the icon
```

`analysis.py` (the measurement, no Qt) · `czi.py` (scenes, reads, label) · `model.py` (the
experiment, its .json, exports, region editing) · `viewer.py` (the slide view) · `plot.py`
(the figure) · `options.py` (the options and their window) · `app.py` (the window).
Version: CalVer `YYYY.M.D`, the date the shipping code last changed.
