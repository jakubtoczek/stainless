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
**[GUIDE.md](GUIDE.md)**, with how it measures.

## Development

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

## Licence

MIT — see [LICENSE](LICENSE).