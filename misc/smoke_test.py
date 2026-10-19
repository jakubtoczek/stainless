"""The whole window, driven without a screen: add a scan, a region traced with the mouse,
the control's role, measure, the live detail, results and figure, export, save and reopen,
undo. Points the options file at a scratch copy, never the user's.

    .venv/Scripts/python misc/smoke_test.py [CZI] [--shots DIR]

CZI: a scan with two sections, stained (scene 0) and its negative control (scene 1);
default the MI-21 VCAM-1 slide.
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
DEFAULT = Path(r"U:\LRB\Microscopie_images\Cardio\MICID\081026_IHC anti VCAM1"
               r"\061026_IHC-VCAM1_MICID-MI-21.czi")


def main(argv) -> int:
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    from stainless import __main__ as entry
    from stainless import app as qapp
    from stainless import model

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

    def shot(name):
        a.processEvents()
        if shots:
            shots.mkdir(parents=True, exist_ok=True)
            w.grab().save(str(shots / f"{name}.png"))

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

    # trace a region with the mouse: a box over the lower-left infarct
    tab.region_name.setCurrentText("infarct")
    tab.draw.setChecked(True)
    view = tab.view
    pts = [(0.10, 0.55), (0.50, 0.55), (0.50, 0.95), (0.10, 0.95)]
    path = []
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        for k in range(10):
            path.append((x0 + (x1 - x0) * k / 10, y0 + (y1 - y0) * k / 10))
    vp = view.viewport()

    def ev(kind, fx, fy, button=Qt.LeftButton):
        p = view.mapFromScene(QPointF(fx * sc.w, fy * sc.h))
        e = QMouseEvent(kind, QPointF(p), QPointF(vp.mapToGlobal(p)), button,
                        button if kind != QEvent.MouseButtonRelease else Qt.NoButton,
                        Qt.NoModifier)
        QApplication.sendEvent(vp, e)

    ev(QEvent.MouseButtonPress, *path[0])
    for p in path[1:]:
        ev(QEvent.MouseMove, *p)
    ev(QEvent.MouseButtonRelease, *path[-1])
    assert len(s0.regions) == 1 and s0.regions[0]["name"] == "infarct", s0.regions
    assert not tab.draw.isChecked()
    shot("2_region")

    # undo / redo the region
    w.do_undo()
    assert not w.exp.sections[0].regions
    w.do_redo()
    s0 = w.exp.sections[0]
    assert len(s0.regions) == 1

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
    print("stained:", r0, "control:", r1)
    assert r0["infarct"] > 5 * r1["section"], (r0, r1)
    assert w.exp.state(s0) == "measured"
    shot("3_measured")

    # the live detail: zoom into the infarct
    view.fitInView(0.17 * sc.w, 0.60 * sc.h, 0.06 * sc.w, 0.04 * sc.h, Qt.KeepAspectRatio)
    view._want_detail()
    wait(lambda: view._detail is not None, 60)
    assert "stained" in tab.inview.text(), tab.inview.text()
    shot("4_detail")
    before = tab.inview.text()
    tab.thr.setValue(0.10)                         # stricter: fewer stained, results stale
    assert tab.inview.text() != before
    assert w.exp.state(s0) == "settings or regions changed"
    w.do_undo()
    assert abs(w.exp.settings.threshold - 0.03) < 1e-9 and w.exp.state(w.exp.sections[0]) == "measured"
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
    rows = [r for r in w.exp.rows() if r["section"] == s1.name]
    assert rows[0]["rule"] == "own" and rows[0]["threshold"] == 0.06, rows
    print("control with its own rule (hue >= 0.06):", round(rows[0]["pct"], 3), "%")
    tab.list.setCurrentRow(0)
    assert not tab.own.isChecked()

    # results, figure, export
    w.tabs.setCurrentWidget(w.results_tab)
    rt = w.results_tab
    assert rt.table.rowCount() == 3, rt.table.rowCount()       # 2 sections + 1 region
    rt.region.setCurrentText("section")
    rt._plot()
    shot("5_results")
    w.exp.export_xlsx(out / "r.xlsx")
    w.exp.export_csv(out / "r.csv")
    rt.copy()
    assert QApplication.clipboard().text().count("\n") == 3

    # the record
    w.tabs.setCurrentWidget(w.record_tab)
    w.record_tab.fields["title"].setText("VCAM-1, MI-21")
    w.record_tab._edit("title")
    assert w.exp.record["title"] == "VCAM-1, MI-21"
    shot("6_experiment")

    # save, reopen
    w.exp.path = str(out / "exp.json")
    assert w.save()
    w2 = qapp.MainWindow()
    w2.open(out / "exp.json")
    e2 = w2.exp
    assert len(e2.sections) == 2 and e2.sections[1].role == "negative control"
    assert e2.state(e2.sections[0]) == "measured", e2.state(e2.sections[0])
    assert e2.sections[0].maps and e2.record["title"] == "VCAM-1, MI-21"
    w2.dirty = False
    w.dirty = False
    print(f"smoke test OK ({out})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
