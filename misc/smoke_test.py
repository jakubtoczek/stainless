"""The whole window, driven without a screen: add a scan, an ROI drawn with clicks, one traced
freehand, nodes dragged, added and deleted, right-click cancelling, a trace crossing itself
(no hole); the control's role, artefacts found, aliases, the sections window, measure, the
live detail (and the outline in it), results and figure, export, options (alpha, lines, a
minimized window opened again), save and reopen (an old crossing outline made whole), undo. Points the options file at a scratch copy,
never the user's.

    .venv/Scripts/python misc/smoke_test.py [CZI] [--shots DIR]

CZI: a scan with two sections, stained (scene 0) and its negative control (scene 1);
default the MI-21 VCAM-1 slide.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
DEFAULT = Path(r"U:\LRB\Microscopie_images\Cardio\MICID\081026_IHC anti VCAM1"
               r"\061026_IHC-VCAM1_MICID-MI-21.czi")


def main(argv) -> int:
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    import numpy as np
    from PySide6.QtWidgets import QGraphicsPathItem

    from stainless import __main__ as entry
    from stainless import app as qapp
    from stainless import analysis, model

    assert "matplotlib" not in sys.modules, "the window module must not load matplotlib"
    args = [a for a in argv if not a.startswith("--")]
    shots = Path(argv[argv.index("--shots") + 1]) if "--shots" in argv else None
    if shots:
        args = [a for a in args if Path(a) != shots]
    czi_path = Path(args[0]) if args else DEFAULT
    out = Path(tempfile.mkdtemp(prefix="stainless_smoke_"))
    qapp.OPTIONS = out / "options.json"

    a = QApplication.instance() or QApplication([])
    entry._apply_dark(a)
    w = qapp.MainWindow()
    w.setAttribute(Qt.WA_DontShowOnScreen)
    w.resize(1500, 950)
    w.show()
    a.processEvents()

    def shot(name, widget=None):
        a.processEvents()
        if shots:
            shots.mkdir(parents=True, exist_ok=True)
            (widget or w).grab().save(str(shots / f"{name}.png"))

    def wait(cond, secs=600):
        t = time.time()
        while not cond():
            a.processEvents()
            time.sleep(0.05)
            assert time.time() - t < secs, "timed out"

    shot("0_empty")
    w.open_paths([czi_path])
    tab = w.sections_tab
    assert len(w.exp.sections) == 2, w.exp.sections
    wait(lambda: len(w.thumbs) == 2, 60)
    shot("1_added")

    # the control: scene 1
    tab.list.setCurrentRow(1)
    tab.role.setCurrentText("negative control")
    tab.role.activated.emit(tab.role.currentIndex())
    assert w.exp.sections[1].role == "negative control"
    tab.list.setCurrentRow(0)
    s0 = w.exp.sections[0]
    sc = s0.scene
    view = tab.view
    vp = view.viewport()

    def ev(kind, fx, fy, button=Qt.LeftButton):
        p = view.mapFromScene(QPointF(fx * sc.w, fy * sc.h))
        e = QMouseEvent(kind, QPointF(p), QPointF(vp.mapToGlobal(p)), button,
                        button if kind != QEvent.MouseButtonRelease else Qt.NoButton,
                        Qt.NoModifier)
        QApplication.sendEvent(vp, e)

    def click(fx, fy, button=Qt.LeftButton):
        ev(QEvent.MouseButtonPress, fx, fy, button)
        ev(QEvent.MouseButtonRelease, fx, fy, button)

    # an ROI named "infarct", drawn with clicks: the box over the lower-left infarct
    tab.kind.setCurrentText("infarct")
    tab.kind.activated.emit(0)                       # picking a kind starts drawing
    assert tab.draw.isChecked() and view.drawing
    box = [(0.10, 0.55), (0.50, 0.55), (0.50, 0.95), (0.10, 0.95)]
    for p in box:
        click(*p)
    click(*box[0])                                   # the first node again: closed
    assert len(s0.regions) == 1 and s0.regions[0]["name"] == "infarct", s0.regions
    r = s0.regions[0]
    assert r["kind"] == "roi" and len(r["points"]) == 4 and "nodes" not in r, r
    assert not tab.draw.isChecked()
    shot("2_region")

    # right-click cancels drawing
    tab.draw.setChecked(True)
    click(0.6, 0.2)
    click(0.7, 0.2, Qt.RightButton)
    assert not tab.draw.isChecked() and len(s0.regions) == 1

    # select it (a click inside), drag a node, add one on an edge, delete it
    click(0.3, 0.75)
    assert view.selected == 0 and tab._region_index() == 0
    first = list(s0.regions[0]["points"][0])
    ev(QEvent.MouseButtonPress, 0.10, 0.55)
    ev(QEvent.MouseMove, 0.11, 0.56)
    ev(QEvent.MouseButtonRelease, 0.11, 0.56)
    p0 = s0.regions[0]["points"][0]
    assert abs(p0[0] - 0.11 * sc.w) < 0.005 * sc.w, p0
    w.do_undo()
    s0 = w.exp.sections[0]
    assert s0.regions[0]["points"][0] == first, s0.regions[0]["points"]
    click(0.3, 0.75)
    click(0.30, 0.55)                                 # on the top edge: a node there
    assert len(w.exp.sections[0].regions[0]["points"]) == 5
    view.setFocus()
    tab._delete()                                     # Del: that node out
    assert len(w.exp.sections[0].regions[0]["points"]) == 4
    s0 = w.exp.sections[0]

    # an exclusion traced freehand (held), then bent by grabbing its line
    tab.kind.setCurrentText("exclude")
    tab.kind.activated.emit(tab.kind.currentIndex())
    ring = [(0.60 + 0.03 * k / 10, 0.30) for k in range(10)] + \
           [(0.63, 0.30 + 0.03 * k / 10) for k in range(10)] + \
           [(0.63 - 0.03 * k / 10, 0.33) for k in range(10)]
    ev(QEvent.MouseButtonPress, *ring[0])
    for p in ring[1:]:
        ev(QEvent.MouseMove, *p)
    ev(QEvent.MouseButtonRelease, *ring[-1])
    ex = s0.regions[1]
    assert ex["kind"] == "exclude" and ex["name"] == "exclude 1" and ex["nodes"] == [], ex
    n = len(ex["points"])
    click(0.615, 0.315)                               # select it
    assert view.selected == 1, view.selected
    ev(QEvent.MouseButtonPress, 0.615, 0.30)          # grab its top line, bend it
    ev(QEvent.MouseMove, 0.615, 0.29)
    ev(QEvent.MouseButtonRelease, 0.615, 0.29)
    ex = w.exp.sections[0].regions[1]
    assert len(ex["nodes"]) == 3 and len(ex["points"]) < n + 1, (ex["nodes"], n)
    assert tab.tree.topLevelItemCount() == 2          # infarct, excluded (1)
    assert tab.tree.topLevelItem(1).text(0) == "excluded (1)"
    w.do_undo()
    w.do_undo()
    s0 = w.exp.sections[0]
    assert len(s0.regions) == 1

    # a trace crossing itself: around a square, then on inside it across its own line —
    # one outline around it all, no hole (even-odd filling made one)
    def trace(pts):
        dense = [pts[0]]
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            dense += [(x0 + (x1 - x0) * k / 12, y0 + (y1 - y0) * k / 12) for k in range(1, 13)]
        ev(QEvent.MouseButtonPress, *dense[0])
        for q in dense[1:]:
            ev(QEvent.MouseMove, *q)
        ev(QEvent.MouseButtonRelease, *dense[-1])

    tab.draw.setChecked(True)
    trace([(0.70, 0.30), (0.76, 0.30), (0.76, 0.36), (0.70, 0.36), (0.70, 0.30), (0.76, 0.33),
           (0.72, 0.33), (0.72, 0.31), (0.74, 0.31), (0.74, 0.35)])
    s0 = w.exp.sections[0]
    lo = s0.regions[1]
    pa = np.asarray(lo["points"], float)
    area = 0.5 * abs(np.dot(pa[:, 0], np.roll(pa[:, 1], 1)) - np.dot(pa[:, 1], np.roll(pa[:, 0], 1)))
    square = (0.06 * sc.w) * (0.06 * sc.h)
    assert not model.crosses(pa) and 0.95 < area / square < 1.05, (area / square, len(pa))
    assert lo["nodes"] == [] and len(lo["points"]) < 40, lo
    shot("2a_crossing_trace")
    w.do_undo()
    s0 = w.exp.sections[0]
    assert len(s0.regions) == 1

    # artefacts on the control
    tab.list.setCurrentRow(1)
    tab.find([w.exp.sections[1]])
    wait(lambda: tab.find_one.isEnabled(), 60)
    s1 = w.exp.sections[1]
    found = [r for r in s1.regions if r.get("auto")]
    assert found and all(r["kind"] == "exclude" for r in found), s1.regions
    print("artefacts on the control:", ", ".join(r["name"] for r in found[:6]),
          "…" if len(found) > 6 else "")
    shot("2b_artefacts")
    tab.list.setCurrentRow(0)

    # aliases: shortened; the experiment's own (none in the options)
    tab.shorten()
    assert [s.alias for s in w.exp.sections] == ["s0", "s1"], [s.alias for s in w.exp.sections]
    assert "aliases" not in w.opts
    print("aliases:", [s.alias for s in w.exp.sections])

    # the sections window: a row each, an alias edited there, a row clicked shows it
    w.show_table()
    tw = w.table_window
    keys = tw.keys
    assert tw.t.rowCount() == 2 and tw.t.isColumnHidden(keys.index("folder"))
    assert not tw.t.isColumnHidden(keys.index("alias"))
    row1 = next(r for r in range(2) if tw.t.item(r, 0).data(Qt.UserRole) == 1)
    tw.t.item(row1, keys.index("alias")).setText("control")
    wait(lambda: w.exp.sections[1].alias == "control", 5)
    assert tw.t.rowCount() == 2
    assert tab.list.item(1).text().startswith("control")
    row1 = next(r for r in range(2) if tw.t.item(r, 0).data(Qt.UserRole) == 1)
    tw.t.setCurrentCell(row1, keys.index("name"))
    assert tab.list.currentRow() == 1
    shot("2c_table", tw)
    tw.showMinimized()
    a.processEvents()
    w.show_table()                                    # minimized: opened again
    assert not tw.isMinimized() and tw.isVisible()
    w.do_undo()
    assert w.exp.sections[1].alias == "s1"
    tab.list.setCurrentRow(0)

    # measure all, 4 × 4 averaged for speed
    tab.res_btns[2].click()
    assert w.exp.settings.level == 2
    t = time.time()
    w.measure(None)
    wait(lambda: w._cancel is None)
    print(f"measured 2 sections in {time.time() - t:.0f} s")
    s0, s1 = w.exp.sections
    r0 = {r["region"]: r["pct"] for r in s0.result["rows"]}
    r1 = {r["region"]: r["pct"] for r in s1.result["rows"]}
    print("stained:", r0, "control:", r1, "control excluded:",
          round(s1.result["excluded_mm2"], 3), "mm²")
    assert r0["infarct"] > 5 * r1[analysis.WHOLE], (r0, r1)
    assert analysis.REST in r0 and s1.result["excluded_mm2"] > 0
    assert w.exp.state(s0) == "measured"
    assert tab.result.rowCount() == 2                 # infarct, remaining tissue
    tab._more("measure")
    assert tab.result.rowCount() == 4                 # + whole section, excluded
    shot("3_measured")

    # the live detail: zoom into the infarct
    view.fitInView(0.17 * sc.w, 0.60 * sc.h, 0.06 * sc.w, 0.04 * sc.h, Qt.KeepAspectRatio)
    view._want_detail()
    wait(lambda: view._detail is not None, 60)
    assert "stained" in tab.inview.text(), tab.inview.text()
    assert view.live and tab.live.isChecked() and view.badge.isVisible()
    shot("4_detail")
    before = tab.inview.text()
    tab.thr.setValue(0.10)                         # stricter: fewer stained, results stale
    assert tab.inview.text() != before
    assert w.exp.state(s0) == "settings or regions changed"
    w.do_undo()
    assert abs(w.exp.settings.threshold - 0.03) < 1e-9 and w.exp.state(w.exp.sections[0]) == "measured"

    # the outline in the live detail: glass outside it is not tissue, even for a stain
    # without counterstain (1.png: SA-β-gal called the glass tissue when zoomed in). On
    # this slide SA-β-gal's outline (0.01) takes glass in by the section; at 0.05, none
    tab.prof_btns[1].click()
    assert w.exp.settings.profile == "sabg" and w.exp.settings.tissue_od == 0
    tab._more("rule")
    tab.outline_od.setValue(0.05)
    assert w.exp.settings.outline_od == 0.05
    view.fitInView(0.06 * sc.w, 0.45 * sc.h, 0.05 * sc.w, 0.04 * sc.h, Qt.KeepAspectRatio)
    view._detail = None
    view._want_detail()
    wait(lambda: view._detail is not None, 60)
    x, y, z, rgb, s, d, scanned = view._detail
    assert ((rgb.max(2) > 5) & (rgb.min(2) < 250)).mean() > 0.9   # glass, not padding
    frac = (scanned > 0).mean()
    print(f"SA-beta-gal, glass in view: {100 * frac:.1f} % counted as tissue ({tab.inview.text()})")
    assert frac < 0.05, frac
    w.do_undo()
    w.do_undo()
    assert w.exp.settings.profile == "dab" and w.exp.settings.outline_od == 0.10
    view.fit()

    # a section's own rule: the control, stricter; the other keeps the shared one
    tab.list.setCurrentRow(1)
    tab.own.setChecked(True)
    s0, s1 = w.exp.sections
    assert s1.rule == {"threshold": 0.03, "min_od": 0.2}, s1.rule
    tab.thr.setValue(0.06)
    assert s1.rule["threshold"] == 0.06 and abs(w.exp.settings.threshold - 0.03) < 1e-9
    assert w.exp.state(s1) != "measured" and w.exp.state(s0) == "measured"
    assert "own rule" in tab.list.item(1).text()
    w.measure(None)
    wait(lambda: w._cancel is None)
    rows = [r for r in w.exp.rows() if r["section"] == s1.shown]
    assert rows[0]["rule"] == "own" and rows[0]["threshold"] == 0.06, rows
    print("control with its own rule (hue >= 0.06):", round(rows[0]["pct"], 3), "%")
    tab.list.setCurrentRow(0)
    assert not tab.own.isChecked()

    # the side panel: a block folds; the layers and white balance switch
    blk = tab.blocks["Stained when"]
    blk.head.click()
    assert not blk.body.isVisible()
    blk.head.click()
    tab.sw["tissue"].setChecked(True)
    assert view.items["nottissue"] is not None
    tab.wb.setChecked(False)
    tab.wb.setChecked(True)
    assert view.bar.isVisible()
    shot("4b_panel")

    # results, figure, export
    w.tabs.setCurrentWidget(w.results_tab)
    rt = w.results_tab
    assert rt.table.rowCount() == 4, rt.table.rowCount()     # s0: 3 rows, s1: 1
    rt.region.setCurrentText(analysis.WHOLE)
    rt._plot()
    shot("5_results")
    w.exp.export_xlsx(out / "r.xlsx")
    w.exp.export_csv(out / "r.csv")
    rt.copy()
    assert QApplication.clipboard().text().count("\n") == 4

    # the record
    w.tabs.setCurrentWidget(w.record_tab)
    w.record_tab.fields["title"].setText("VCAM-1, MI-21")
    w.record_tab._edit("title")
    assert w.exp.record["title"] == "VCAM-1, MI-21"
    shot("6_experiment")
    w.tabs.setCurrentWidget(tab)

    # options: a colour, saved to the scratch file, and the view follows
    w.show_options()
    ow = w.options_window
    for i in range(ow.topics.count()):
        ow.topics.setCurrentRow(i)
        shot(f"7_options_{i}", ow)
    ow._set(("colours", "tissue"), "#80123456")       # with its alpha
    assert json.loads(qapp.OPTIONS.read_text(encoding="utf-8"))["colours"]["tissue"] == "#80123456"
    assert view.style["tissue"] == "#80123456"
    ow._set(("lines", "roi", "width"), 5)
    ow._set(("lines", "roi", "style"), "dot")
    pen = [it for it in view.region_items if isinstance(it, QGraphicsPathItem)][0].pen()
    assert pen.width() == 5 and pen.style() == Qt.DotLine, (pen.width(), pen.style())
    shot("7b_lines")
    ow.showMinimized()
    a.processEvents()
    w.show_options()                                  # minimized: opened again
    assert not ow.isMinimized() and ow.isVisible()
    ow._set(("regions", "preset"), "MI")
    assert [tab.kind.itemText(i) for i in range(tab.kind.count())] == \
        ["ROI", "infarct", "border zone", "remote", "exclude"]
    ow.close()

    # save, reopen; a region from before, crossing itself, comes back whole
    w.exp.sections[1].regions.append({"name": "old", "kind": "roi", "nodes": [], "points": [
        [x * sc.w / 100, y * sc.h / 100] for x, y in
        [(10, 10), (20, 10), (20, 20), (10, 20), (10, 10), (20, 15), (12, 15), (12, 11),
         (16, 11), (16, 19)]]})
    w.exp.path = str(out / "exp.json")
    assert w.save()
    w2 = qapp.MainWindow()
    w2.open(out / "exp.json")
    e2 = w2.exp
    assert len(e2.sections) == 2 and e2.sections[1].role == "negative control"
    assert e2.state(e2.sections[0]) == "measured", e2.state(e2.sections[0])
    assert e2.sections[0].maps and e2.record["title"] == "VCAM-1, MI-21"
    assert e2.sections[0].alias == w.exp.sections[0].alias
    assert any(r.get("auto") for r in e2.sections[1].regions)
    assert not model.crosses(np.asarray(e2.sections[1].regions[-1]["points"], float))
    assert "crossed their own outline" in w2.logbox.toPlainText()
    w2.dirty = False
    w.dirty = False
    print(f"smoke test OK ({out})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
