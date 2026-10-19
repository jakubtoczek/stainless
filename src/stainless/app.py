"""The window. Widgets only: the measurement is analysis.py's, the experiment model.py's,
the slide view viewer.py's, the figure plot.py's.

Tabs, in the order of the work: Sections (the slides, their regions, the settings,
Measure) → Results → Experiment (the record of what was done). Drop anything anywhere:
CZI scans or folders of them, a saved experiment (.json).
"""

from __future__ import annotations

import copy
import datetime
import json
import sys
import threading
import time
import traceback
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
from PySide6.QtCore import QEvent, QLocale, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QColor, QIcon, QKeySequence, QPainter, QPixmap,
                           QShortcut)
from PySide6.QtWidgets import (QAbstractItemView, QAbstractScrollArea, QAbstractSpinBox,
                               QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QFrame, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                               QScrollArea, QSizePolicy, QSlider, QSplitter, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from . import __version__, analysis, czi, model
from .viewer import SlideView, level_for, region_colour, to_qimage

APP_NAME = "Stainless"
_PKG = Path(__file__).resolve().parent
OPTIONS = _PKG.parents[1] / "stainless_options.json"       # beside Stainless.bat
DEFAULT_OPTIONS = {"last_dir": "", "region_names": model.REGION_NAMES}
HINT = "Drop CZI scans or folders anywhere on this window, or a saved experiment (.json)."
RESOLUTIONS = ["scan", "½", "¼"]       # Settings.level 0, 1, 2
THUMB = 72


def app_icon():
    p = _PKG / "assets" / "stainless.ico"
    return QIcon(str(p)) if p.exists() else None


def load_options() -> dict:
    try:
        d = json.loads(OPTIONS.read_text(encoding="utf-8"))
        return {**copy.deepcopy(DEFAULT_OPTIONS), **(d if isinstance(d, dict) else {})}
    except (OSError, ValueError):
        return copy.deepcopy(DEFAULT_OPTIONS)


# --- small widgets (the suite's look) ----------------------------------------------------

class WheelGuard(QObject):
    """The wheel scrolls the page, never a value it only passes over."""

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Wheel and isinstance(obj, (QComboBox, QAbstractSpinBox,
                                                          QSlider)):
            w = obj.parentWidget()
            while w is not None and not isinstance(w, QAbstractScrollArea):
                w = w.parentWidget()
            if w is not None:
                QApplication.sendEvent(w.verticalScrollBar(), e)
            return True
        return False


_guard = None


def guard_wheel(root: QWidget) -> None:
    global _guard
    _guard = _guard or WheelGuard()
    for w in (root.findChildren(QComboBox) + root.findChildren(QAbstractSpinBox) +
              root.findChildren(QSlider)):
        w.installEventFilter(_guard)


def dim(text: str) -> QLabel:
    lab = QLabel(text, wordWrap=True)
    lab.setStyleSheet("color:#8d8d8d; border:none;")
    return lab


def section(title: str, on_reset=None):
    """A titled, bordered block of the side panel; returns (frame, body layout)."""
    frame = QFrame(objectName="sec")
    frame.setStyleSheet("QFrame#sec{border:1px solid #3a3a3a; border-radius:6px;}")
    col = QVBoxLayout(frame)
    col.setContentsMargins(8, 6, 8, 8)
    col.setSpacing(5)
    head = QHBoxLayout()
    lab = QLabel(title.upper())
    lab.setStyleSheet("color:#8b98a5; font-weight:600; font-size:10px; border:none;")
    head.addWidget(lab)
    head.addStretch(1)
    if on_reset is not None:
        b = QPushButton("Reset", flat=True)
        b.setFixedHeight(18)
        b.setStyleSheet("color:#7fa8d0; border:none; font-size:11px;")
        b.clicked.connect(on_reset)
        head.addWidget(b)
    col.addLayout(head)
    return frame, col


def segmented(options, on_change):
    row = QWidget()
    row.setStyleSheet(
        "QPushButton{padding:5px; border:1px solid #4a4a4a; border-radius:4px;}"
        "QPushButton:checked{background:#2f6ea5; color:#fff; border:1px solid #4aa3ff;}")
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(4)
    btns = []
    for i, text in enumerate(options):
        b = QPushButton(text, checkable=True, autoExclusive=True)
        b.toggled.connect(lambda on, i=i: on and on_change(i))
        h.addWidget(b, 1)
        btns.append(b)
    return row, btns


class Switch(QCheckBox):
    """A checkbox drawn as an on/off slider."""
    W, H = 34, 16

    def __init__(self, text=""):
        super().__init__(text)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(self.H + 6)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, e):
        w, h = self.W, self.H
        y = (self.height() - h) // 2
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#2f6ea5" if self.isChecked() else "#4a4a4a"))
        p.drawRoundedRect(0, y, w, h, h / 2, h / 2)
        p.setBrush(QColor("#eaeaea"))
        p.drawEllipse(2 + (w - h if self.isChecked() else 0), y + 2, h - 4, h - 4)
        p.setPen(QColor("#c8c8c8"))
        p.drawText(w + 8, 0, self.width() - w - 8, self.height(),
                   Qt.AlignVCenter | Qt.AlignLeft, self.text())


def slider_row(label, lo, hi, step, decimals, tip, on_change):
    """Label, slider, number box; the box is the value. Returns (row, box)."""
    k = 1 / step
    s = QSlider(Qt.Horizontal)
    s.setRange(int(round(lo * k)), int(round(hi * k)))
    box = QDoubleSpinBox(decimals=decimals, singleStep=step, keyboardTracking=False)
    box.setRange(lo, hi)
    box.setFixedWidth(64)
    s.valueChanged.connect(lambda v: box.setValue(v / k))

    def follow(v):
        s.blockSignals(True)
        s.setValue(int(round(v * k)))
        s.blockSignals(False)
        on_change(v)

    box.valueChanged.connect(follow)
    name = QLabel(label)
    name.setFixedWidth(92)
    row = QWidget(toolTip=tip)
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.addWidget(name)
    h.addWidget(s, 1)
    h.addWidget(box)
    return row, box


class _Bridge(QObject):
    call = Signal(object)


def fmt(v, nd=2) -> str:
    return "–" if v is None else f"{v:.{nd}f}"


# --- Sections tab ------------------------------------------------------------------------

class SectionsTab(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self._busy = False                         # filling widgets: no edits back

        # left: the sections, the selected one's fields, its slide label
        self.list = QListWidget()
        self.list.setIconSize(QSize(THUMB, THUMB))
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setStyleSheet("QListWidget::item{padding:3px;}")
        self.list.currentRowChanged.connect(self._selected)
        self.name = QLineEdit(placeholderText="what the results call it")
        self.group = QComboBox(editable=True)
        self.group.lineEdit().setPlaceholderText("e.g. MI 7 d, sham")
        self.role = QComboBox()
        self.role.addItems(model.ROLES)
        self.note = QLineEdit(placeholderText="anything about this section")
        self.name.editingFinished.connect(lambda: self._field("name", self.name.text()))
        self.group.lineEdit().editingFinished.connect(
            lambda: self._field("group", self.group.currentText()))
        self.group.activated.connect(lambda _: self._field("group", self.group.currentText()))
        self.role.activated.connect(lambda _: self._field("role", self.role.currentText()))
        self.note.editingFinished.connect(lambda: self._field("note", self.note.text()))
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        for lab, w in (("name", self.name), ("group", self.group), ("role", self.role),
                       ("note", self.note)):
            form.addRow(lab, w)
        self.label = QLabel(alignment=Qt.AlignCenter)
        self.label.setMinimumHeight(90)
        self.label.setToolTip("the slide's label, as the scanner photographed it")
        add = QPushButton("Add scans…", clicked=win.add_dialog)
        rem = QPushButton("Remove", clicked=self.remove,
                          toolTip="take the selected sections out of the experiment (Del)")
        hb = QHBoxLayout()
        hb.addWidget(add)
        hb.addWidget(rem)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addLayout(hb)
        lv.addWidget(self.list, 1)
        lv.addLayout(form)
        lv.addWidget(self.label)

        # centre: the slide
        self.view = SlideView()
        self.view.region_drawn.connect(self._region_drawn)
        self.view.region_clicked.connect(self._region_clicked)
        self.empty = QLabel(HINT, alignment=Qt.AlignCenter, wordWrap=True)
        self.empty.setStyleSheet("color:#8d8d8d; font-size:14px;")
        self.inview = dim("")
        self.view.detail_changed.connect(self.inview.setText)
        centre = QWidget()
        cv = QVBoxLayout(centre)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.addWidget(self.empty, 1)
        cv.addWidget(self.view, 1)
        cv.addWidget(self.inview)
        self.view.hide()

        # right: the settings, regions, layers, Measure
        panel = QWidget()
        pv = QVBoxLayout(panel)
        pv.setContentsMargins(4, 0, 4, 0)
        pv.setSpacing(8)
        f, b = section("Stain")
        row, self.prof_btns = segmented(["DAB", "SA-β-gal"], self._profile)
        self.prof_btns[0].setToolTip(analysis.PROFILES["dab"]["label"])
        self.prof_btns[1].setToolTip(analysis.PROFILES["sabg"]["label"])
        b.addWidget(row)
        pv.addWidget(f)

        f, b = section("Stained when", self._reset_rule)
        row, self.thr = slider_row(
            "hue ≥", -0.10, 0.40, 0.005, 3,
            "How far the pixel's absorbance points toward the stain: (OD · w) / ΣOD, "
            "w = blue − red for DAB. 0 = grey; hematoxylin is below 0.",
            lambda v: self._setting("threshold", v))
        b.addWidget(row)
        row, self.minod = slider_row(
            "summed OD ≥", 0.0, 1.0, 0.01, 2,
            "Dense enough to be stain, not noise: the pixel's absorbance summed over R, G, B, "
            "against the section's glass.", lambda v: self._setting("min_od", v))
        b.addWidget(row)
        self.own = Switch("this section only")
        self.own.setToolTip("give this section its own rule (stronger staining, more "
                            "background…); the others keep the shared one")
        self.own.toggled.connect(self._own_rule)
        b.addWidget(self.own)
        self.rule_info = dim("")
        b.addWidget(self.rule_info)
        pv.addWidget(f)

        f, b = section("Resolution")
        row, self.res_btns = segmented(RESOLUTIONS, self._resolution)
        tips = ["the scan's own pixels: ~4× slower, and the camera's pixel noise reads as "
                "stain (the negative control +66 % on MI-21; ×14 on a 10× scan)",
                "2 × 2 scan pixels averaged (the file's own ½ level): the same numbers as the "
                "scan's pixels smoothed by ~2 px, a quarter of the time. The default",
                "4 × 4 averaged: 4× faster again; thin stained structures start to fade"]
        for btn, tip in zip(self.res_btns, tips):
            btn.setToolTip(tip)
        b.addWidget(row)
        self.res_info = dim("")
        b.addWidget(self.res_info)
        pv.addWidget(f)

        f, b = section("Regions")
        rr = QHBoxLayout()
        self.region_name = QComboBox(editable=True)
        self.region_name.addItems(win.opts.get("region_names") or model.REGION_NAMES)
        self.region_name.setToolTip("the name of the next region drawn; 'exclude' takes "
                                    "its area out of the tissue (folds, debris)")
        self.draw = QPushButton("Draw", checkable=True,
                                toolTip="draw a region around what to measure: hold the "
                                        "left button and trace (D; Esc cancels)")
        self.draw.toggled.connect(self.view.set_drawing)
        rr.addWidget(self.region_name, 1)
        rr.addWidget(self.draw)
        b.addLayout(rr)
        self.regions = QListWidget()
        self.regions.setMaximumHeight(110)
        self.regions.currentRowChanged.connect(self._region_selected)
        self.regions.itemChanged.connect(self._region_renamed)
        b.addWidget(self.regions)
        b.addWidget(dim("Double-click a name to change it; Del removes the selected one."))
        pv.addWidget(f)

        f, b = section("Show")
        self.sw = {}
        for key, text in (("stained", "stained"), ("tissue", "not counted as tissue"),
                          ("regions", "regions")):
            s = Switch(text)
            s.setChecked(self.view.layers[key])
            s.toggled.connect(lambda on, k=key: self.view.set_layer(k, on))
            b.addWidget(s)
            self.sw[key] = s
        pv.addWidget(f)

        f, b = section("Measure")
        hb = QHBoxLayout()
        self.go_one = QPushButton("This section", clicked=lambda: win.measure(self.current()))
        self.go_all = QPushButton("All", clicked=lambda: win.measure(None),
                                  toolTip="every section not measured yet, or measured with "
                                          "other settings or regions (Ctrl+M)")
        self.stop = QPushButton("Stop", clicked=win.stop_measure, enabled=False)
        for w in (self.go_one, self.go_all, self.stop):
            hb.addWidget(w)
        b.addLayout(hb)
        self.bar = QProgressBar(textVisible=False, maximumHeight=8)
        b.addWidget(self.bar)
        self.progress = dim("")
        b.addWidget(self.progress)
        self.result = QTableWidget(0, 3)
        self.result.setHorizontalHeaderLabels(["region", "tissue mm²", "% stained"])
        self.result.verticalHeader().hide()
        self.result.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.result.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.result.setMaximumHeight(130)
        b.addWidget(self.result)
        self.state = dim("")
        b.addWidget(self.state)
        pv.addWidget(f)
        pv.addStretch()
        scroll = QScrollArea(widgetResizable=True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(300)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(centre)
        split.addWidget(scroll)
        split.setStretchFactor(1, 1)
        split.setSizes([260, 900, 320])
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(split)

        for key, fn in (("D", lambda: self.draw.toggle()), ("F", self.view.fit),
                        ("Ctrl+M", lambda: win.measure(None))):
            QShortcut(QKeySequence(key), self, activated=fn)
        QShortcut(QKeySequence.Delete, self, activated=self._delete)
        self.sync_settings()

    # -- helpers
    def current(self) -> model.Section | None:
        i = self.list.currentRow()
        secs = self.win.exp.sections
        return secs[i] if 0 <= i < len(secs) else None

    def sync_settings(self):
        s = self.current()
        exp = self.win.exp
        st = exp.settings_for(s) if s is not None else exp.settings
        for w in (*self.prof_btns, *self.res_btns, self.thr, self.minod):
            w.blockSignals(True)
        self.prof_btns[list(analysis.PROFILES).index(st.profile)].setChecked(True)
        self.res_btns[min(max(st.level, 0), 2)].setChecked(True)
        self.thr.setValue(st.threshold)
        self.minod.setValue(st.min_od)
        for w in (*self.prof_btns, *self.res_btns, self.thr, self.minod):
            w.blockSignals(False)
        own = s is not None and bool(s.rule)
        self.own.blockSignals(True)
        self.own.setChecked(own)
        self.own.setEnabled(s is not None)
        self.own.blockSignals(False)
        g = exp.settings
        self.rule_info.setText(
            f"This section only; the others: hue ≥ {g.threshold:g}, summed OD ≥ {g.min_od:g}."
            if own else "Shared by every section. Zoom in: the view shows, live, what the rule "
                        "calls stained.")
        for box in (self.thr, self.minod):                  # the slider follows the box
            sl = box.parentWidget().findChild(QSlider)
            sl.blockSignals(True)
            sl.setValue(int(round(box.value() / box.singleStep())))
            sl.blockSignals(False)
        self.view.set_settings(st)

    def refresh(self):
        """The list, then the selected section."""
        exp = self.win.exp
        row = max(0, self.list.currentRow())
        self.list.blockSignals(True)
        self.list.clear()
        for s in exp.sections:
            it = QListWidgetItem(self._item_text(s))
            pm = self.win.thumbs.get(s.key)
            if pm is not None:
                it.setIcon(QIcon(pm))
            self.list.addItem(it)
        self.list.blockSignals(False)
        has = bool(exp.sections)
        self.empty.setVisible(not has)
        self.view.setVisible(has)
        groups = sorted({s.group for s in exp.sections if s.group})
        self.group.blockSignals(True)
        self.group.clear()
        self.group.addItems(groups)
        self.group.blockSignals(False)
        if has:
            self.list.setCurrentRow(min(row, len(exp.sections) - 1))
            self._selected(self.list.currentRow(), force=False)
        self._todo()

    def _item_text(self, s):
        exp = self.win.exp
        st = exp.state(s)
        pct = s.result["rows"][0]["pct"] if s.result and s.result["rows"] else None
        tail = (f"{fmt(pct)} % stained" if st == "measured" else
                f"{fmt(pct)} % — {st}" if s.result else st)
        return f"{s.name}\n{s.group or '–'} · {s.role} · {tail}{' · own rule' if s.rule else ''}"

    def _todo(self):
        exp = self.win.exp
        n = sum(1 for s in exp.sections if s.role != "left out" and s.scene is not None
                and exp.state(s) != "measured")
        self.go_all.setText(f"All ({n} to do)" if n else "All (done)")

    # -- selection
    def _selected(self, i, force=True):
        s = self.current()
        if s is None:
            return
        self._busy = True
        self.name.setText(s.name)
        self.group.setCurrentText(s.group)
        self.role.setCurrentText(s.role)
        self.note.setText(s.note)
        self._busy = False
        self.win.show_section(s, force=force)
        self.sync_settings()
        self._res_info()
        self.fill_regions()
        self.fill_result()

    def fill_regions(self, select: int = -1):
        s = self.current()
        self.regions.blockSignals(True)
        self.regions.clear()
        labels = {}
        if s is not None:
            res = {r["region"]: r for r in (s.result.get("rows", []) if s.result else [])}
            for i, r in enumerate(s.regions):
                it = QListWidgetItem(r["name"])
                it.setFlags(it.flags() | Qt.ItemIsEditable)
                it.setForeground(region_colour(r["name"], i))
                self.regions.addItem(it)
                m = res.get(r["name"])
                if m and m["pct"] is not None and not self.win.exp.stale(s):
                    labels[i] = f"{m['pct']:.2f} %"
            self.regions.setCurrentRow(select)
        self.regions.blockSignals(False)
        self.view.set_regions(s.regions if s else [], select, labels)

    def fill_result(self):
        s = self.current()
        exp = self.win.exp
        rows = s.result.get("rows", []) if (s and s.result) else []
        self.result.setRowCount(len(rows))
        for i, r in enumerate(rows):
            for j, v in enumerate((r["region"], fmt(r["tissue_mm2"], 3), fmt(r["pct"]))):
                it = QTableWidgetItem(v)
                if j:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.result.setItem(i, j, it)
        if s is None:
            self.state.setText("")
        elif s.scene is None:
            self.state.setText("⚠ its file is not found: it can't be shown or measured")
        elif not s.result:
            self.state.setText("not measured yet")
        else:
            r = s.result
            self.state.setText(
                ("⚠ settings or regions changed since: Measure again. " if exp.stale(s) else "")
                + f"{r['um_per_px']:g} µm/px, {r['tiles']} tiles in {r['seconds']:g} s, "
                  f"glass RGB {', '.join(f'{g:.0f}' for g in r['glass'])}")

    # -- edits
    def _field(self, key, value):
        s = self.current()
        if self._busy or s is None or getattr(s, key) == value:
            return
        self.win.snap()
        for i in self._rows():
            if key == "name" and i != self.list.currentRow():
                continue                                  # a name is one section's
            setattr(self.win.exp.sections[i], key, value)
        self.win.changed()

    def _rows(self) -> list[int]:
        rows = sorted({self.list.row(it) for it in self.list.selectedItems()})
        return rows or ([self.list.currentRow()] if self.list.currentRow() >= 0 else [])

    def remove(self):
        rows = self._rows()
        if not rows:
            return
        self.win.snap()
        for i in reversed(rows):
            del self.win.exp.sections[i]
        self.win.changed()

    def _delete(self):
        if self.regions.hasFocus() or self.regions.currentRow() >= 0 and not self.list.hasFocus():
            self._remove_region()
        elif self.list.hasFocus():
            self.remove()

    def _setting(self, key, value):
        exp = self.win.exp
        s = self.current()
        if s is not None and s.rule and key in model.RULE_KEYS:     # this section's own rule
            if s.rule.get(key) == value:
                return
            self.win.snap()
            s.rule[key] = value
        else:
            if getattr(exp.settings, key) == value:
                return
            self.win.snap()
            exp.settings = replace(exp.settings, **{key: value})
        self.view.set_settings(exp.settings_for(s) if s is not None else exp.settings)
        self.win.changed(view=False)

    def _own_rule(self, on):
        s = self.current()
        if s is None or on == bool(s.rule):
            return
        self.win.snap()
        g = self.win.exp.settings
        s.rule = {"threshold": g.threshold, "min_od": g.min_od} if on else {}
        self.sync_settings()
        self.win.changed(view=False)

    def _profile(self, i):
        key = list(analysis.PROFILES)[i]
        if key == self.win.exp.settings.profile:
            return
        self.win.snap()
        p = analysis.PROFILES[key]
        self.win.exp.settings = replace(self.win.exp.settings, profile=key,
                                        threshold=p["threshold"], min_od=p["min_od"])
        own = [s for s in self.win.exp.sections if s.rule]
        for s in own:                                     # another stain's numbers
            s.rule = {}
        if own:
            self.win.log(f"own rules cleared ({len(own)} sections): they were for the other stain")
        self.sync_settings()
        self.win.changed(view=False)

    def _reset_rule(self):
        p = analysis.PROFILES[self.win.exp.settings.profile]
        self.win.snap()
        s = self.current()
        if s is not None and s.rule:                      # back to the shared rule
            s.rule = {}
        else:
            self.win.exp.settings = replace(self.win.exp.settings, threshold=p["threshold"],
                                            min_od=p["min_od"])
        self.sync_settings()
        self.win.changed(view=False)

    def _resolution(self, i):
        self._setting("level", i)
        self._res_info()

    def _res_info(self):
        s = self.current()
        if s is not None and s.scene is not None:
            lv = self.win.exp.settings.level
            self.res_info.setText(f"this scan: {s.scene.px_um:.2f} µm/px → measured at "
                                  f"{s.scene.px_um * 2 ** lv:.2f} µm/px")

    def _region_drawn(self, pts):
        s = self.current()
        if s is None:
            return
        name = self.region_name.currentText().strip() or "region"
        self.win.snap()
        s.regions.append({"name": name, "points": pts})
        names = self.win.opts.setdefault("region_names", list(model.REGION_NAMES))
        if name not in names:
            names.append(name)
            self.region_name.addItem(name)
            self.win.save_options()
        self.draw.setChecked(False)
        self.win.changed(view=False)
        self.fill_regions(len(s.regions) - 1)

    def _region_clicked(self, i):
        self.regions.setCurrentRow(i)

    def _region_selected(self, i):
        s = self.current()
        if s is not None:
            self.view.set_regions(s.regions, i, self.view.labels)

    def _region_renamed(self, it):
        s = self.current()
        i = self.regions.row(it)
        if s is None or not (0 <= i < len(s.regions)):
            return
        name = it.text().strip()
        if name and name != s.regions[i]["name"]:
            self.win.snap()
            s.regions[i]["name"] = name
            self.win.changed(view=False)
            self.fill_regions(i)

    def _remove_region(self):
        s = self.current()
        i = self.regions.currentRow()
        if s is None or not (0 <= i < len(s.regions)):
            return
        self.win.snap()
        del s.regions[i]
        self.win.changed(view=False)
        self.fill_regions()


# --- Results tab -------------------------------------------------------------------------

class ResultsTab(QWidget):
    COLS = [("section", "section"), ("group", "group"), ("role", "role"),
            ("region", "region"), ("tissue_mm2", "tissue mm²"), ("stained_mm2", "stained mm²"),
            ("pct", "% stained"), ("mean_od", "mean OD"), ("threshold", "hue ≥"),
            ("min_od", "OD ≥"), ("rule", "rule"), ("state", "state")]

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.region = QComboBox()
        self.region.activated.connect(lambda _: self._plot())
        top = QHBoxLayout()
        top.addWidget(QLabel("figure:"))
        top.addWidget(self.region)
        top.addStretch()
        for text, fn, tip in (("Copy", self.copy, "the table, to paste in a spreadsheet (Ctrl+C)"),
                              ("Export…", win.export_dialog, "the table as .xlsx (with the "
                               "experiment and settings) or .csv"),
                              ("Save figure…", self.save_figure, "PNG, SVG or PDF")):
            top.addWidget(QPushButton(text, clicked=fn, toolTip=tip))
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels([t for _, t in self.COLS])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.summary = dim("")
        self.canvas_box = QVBoxLayout()
        right = QWidget()
        right.setLayout(self.canvas_box)
        self.canvas_box.addWidget(self.summary)
        split = QSplitter()
        split.addWidget(self.table)
        split.addWidget(right)
        split.setSizes([800, 520])
        v = QVBoxLayout(self)
        v.addLayout(top)
        v.addWidget(split, 1)
        QShortcut(QKeySequence.Copy, self.table, activated=self.copy)
        self.canvas = self.fig = None

    def refresh(self):
        rows = self.win.exp.rows()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            for j, (k, _) in enumerate(self.COLS):
                v = r[k]
                it = QTableWidgetItem()
                if isinstance(v, float):
                    it.setData(Qt.DisplayRole, round(v, 3 if k.endswith("mm2") else 2))
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                else:
                    it.setText("–" if v is None else str(v))
                if r["state"] != "measured":
                    it.setForeground(QColor("#d9a441"))
                self.table.setItem(i, j, it)
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()
        regions = []
        for r in rows:
            if r["region"] not in regions:
                regions.append(r["region"])
        cur = self.region.currentText()
        self.region.clear()
        self.region.addItems(regions)
        if cur in regions:
            self.region.setCurrentText(cur)
        self._plot()

    def _plot(self):
        region = self.region.currentText()
        rows = self.win.exp.rows()
        sm = model.summary(rows, region) if region else []
        res = sorted({r["um_per_px"] for r in rows})
        self.summary.setText(("  ·  ".join(
            f"{g['group']}: {g['mean']:.2f} ± {fmt(g['sd'])} % (n={g['n']})" for g in sm)
            if sm else "Measure sections to see their results here.") +
            (f"<br>⚠ measured at different resolutions ({', '.join(f'{v:g}' for v in res)} "
             f"µm/px): scans made with different objectives" if len(res) > 1 else ""))
        if not sm:
            if self.canvas is not None:
                self.canvas.hide()
            return
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from . import plot
        st = self.win.exp.settings
        stain = "DAB" if st.profile == "dab" else "SA-β-gal"
        if self.canvas is None:
            self.fig = plot.figure(sm, region, stain, analysis.PROFILES[st.profile]["colour"])
            self.canvas = FigureCanvasQTAgg(self.fig)
            self.canvas_box.addWidget(self.canvas, 1)
        else:
            plot.figure(sm, region, stain, analysis.PROFILES[st.profile]["colour"], fig=self.fig)
            self.canvas.draw_idle()
        self.canvas.show()

    def copy(self):
        lines = ["\t".join(t for _, t in self.COLS)]
        for i in range(self.table.rowCount()):
            lines.append("\t".join(self.table.item(i, j).text() if self.table.item(i, j)
                                   else "" for j in range(self.table.columnCount())))
        QApplication.clipboard().setText("\n".join(lines))
        self.win.log(f"✓ {self.table.rowCount()} rows copied")

    def save_figure(self):
        if self.fig is None:
            return
        p, _ = QFileDialog.getSaveFileName(self, "Save figure", str(Path(
            self.win.opts.get("last_dir") or "") / f"{self.win.stem()}_figure.png"),
            "PNG (*.png);;SVG (*.svg);;PDF (*.pdf)")
        if p:
            from . import plot
            st = self.win.exp.settings
            region = self.region.currentText()
            f = plot.figure(model.summary(self.win.exp.rows(), region), region,
                            "DAB" if st.profile == "dab" else "SA-β-gal",
                            analysis.PROFILES[st.profile]["colour"], dark=False)
            f.savefig(p, dpi=300)
            self.win.log(f"✓ figure saved: {p}")


# --- Experiment tab ----------------------------------------------------------------------

class RecordTab(QScrollArea):
    def __init__(self, win):
        super().__init__(widgetResizable=True)
        self.win = win
        self.setFrameShape(QFrame.NoFrame)
        w = QWidget()
        v = QVBoxLayout(w)
        form = QFormLayout()
        self.fields = {}
        for k in model.RECORD:
            e = QPlainTextEdit() if k == "comment" else QLineEdit()
            if k == "comment":
                e.setMaximumHeight(90)
                e.textChanged.connect(lambda k=k: self._edit(k))
            else:
                e.setPlaceholderText(model.HINT.get(k, ""))
                e.editingFinished.connect(lambda k=k: self._edit(k))
            form.addRow(k, e)
            self.fields[k] = e
        v.addLayout(form)
        v.addWidget(QLabel("<b>Measured with</b>"))
        self.settings = dim("")
        self.settings.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.settings)
        v.addWidget(QLabel("<b>Scans</b>"))
        self.scans = dim("")
        self.scans.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.scans)
        v.addStretch()
        self.setWidget(w)
        self._busy = False

    def refresh(self):
        exp = self.win.exp
        self._busy = True
        for k, e in self.fields.items():
            val = exp.record.get(k, "")
            if isinstance(e, QPlainTextEdit):
                if e.toPlainText() != val:
                    e.setPlainText(val)
            else:
                e.setText(val)
        self._busy = False
        self.settings.setText("<br>".join(f"{k}: {v}" for k, v in exp.settings_text()))
        self.scans.setText("<br>".join(
            f"<b>{n}</b>: " + ", ".join(f"{k} {v}" for k, v in a.items())
            for n, a in exp.acquisition.items()) or "–")

    def _edit(self, k):
        if self._busy:
            return
        e = self.fields[k]
        val = e.toPlainText() if isinstance(e, QPlainTextEdit) else e.text()
        if self.win.exp.record.get(k, "") != val:
            self.win.snap()
            self.win.exp.record[k] = val
            self.win.changed(view=False)


# --- the window --------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        QLocale.setDefault(QLocale.c())
        super().__init__()
        self.opts = load_options()
        sys.excepthook = self._error
        self.exp = model.Experiment()
        self.undo, self.redo = [], []
        self.dirty = False
        self.thumbs: dict = {}                     # section key -> QPixmap
        self.overviews: dict = {}                  # section key -> (rgb, zoom, glass)
        self.decoded: dict = {}                    # (section key, fingerprint) -> maps
        self.labels: dict = {}                     # file -> QPixmap or None
        self._bridge = _Bridge()
        self._bridge.call.connect(lambda f: f())
        self._cancel = None
        self.resize(1500, 920)
        self.setAcceptDrops(True)

        self.tabs = QTabWidget()
        self.sections_tab = SectionsTab(self)
        self.results_tab = ResultsTab(self)
        self.record_tab = RecordTab(self)
        for w, name in ((self.sections_tab, "Sections"), (self.results_tab, "Results"),
                        (self.record_tab, "Experiment")):
            self.tabs.addTab(w, name)
        self.tabs.currentChanged.connect(lambda _: self._refresh_tab())
        self.logbox = QPlainTextEdit(readOnly=True, maximumHeight=90)
        self.logbox.setPlaceholderText("What happened, and what looks wrong (✗ ⚠)")

        bar = self.addToolBar("main")
        bar.setMovable(False)
        for text, key, tip, fn in [
                ("Open…", QKeySequence.Open, "an experiment (.json)", self.open_dialog),
                ("Save", QKeySequence.Save, "the experiment as one .json: sections, regions, "
                                            "settings, results", self.save),
                ("Save as…", QKeySequence.SaveAs, "", self.save_as),
                (None, None, None, None),
                ("Add scans…", "Ctrl+I", "CZI files, or a folder of them", self.add_dialog),
                ("Export results…", "Ctrl+E", ".xlsx or .csv", self.export_dialog)]:
            if text is None:
                bar.addSeparator()
                continue
            a = QAction(text, self, triggered=fn, toolTip=f"{tip} ({QKeySequence(key).toString()})")
            a.setShortcut(QKeySequence(key))
            bar.addAction(a)
        for key, fn in ((QKeySequence.Undo, self.do_undo), (QKeySequence.Redo, self.do_redo),
                        (QKeySequence("Ctrl+Shift+Z"), self.do_redo)):
            self.addAction(QAction(self, shortcut=key, triggered=fn))

        central = QWidget()
        v = QVBoxLayout(central)
        v.addWidget(self.tabs, 1)
        v.addWidget(self.logbox)
        self.setCentralWidget(central)
        guard_wheel(self)
        self.refresh_all()
        self._title()

    # -- bookkeeping
    def _error(self, kind, err, tb):
        text = "".join(traceback.format_exception(kind, err, tb))
        if sys.stderr:
            sys.stderr.write(text)
        try:
            with OPTIONS.with_name("stainless_errors.log").open("a", encoding="utf-8") as fh:
                fh.write(f"--- {datetime.datetime.now():%Y-%m-%d %H:%M:%S} Stainless "
                         f"{__version__}\n{text}")
        except OSError:
            pass
        at = traceback.extract_tb(tb)[-1] if tb else None
        self.log(f"✗ internal error — {kind.__name__}: {err}"
                 + (f" ({Path(at.filename).name}:{at.lineno})" if at else "")
                 + " — details in stainless_errors.log")

    def log(self, *lines):
        for line in lines:
            self.logbox.appendPlainText(line)

    def later(self, fn):
        """Run *fn* on the GUI thread (from a worker)."""
        try:
            self._bridge.call.emit(fn)
        except RuntimeError:                      # the window is gone (closing)
            pass

    def save_options(self):
        try:
            OPTIONS.write_text(json.dumps(self.opts, indent=1, ensure_ascii=False),
                               encoding="utf-8")
        except OSError as e:
            self.log(f"⚠ options not saved: {e}")

    def stem(self) -> str:
        if self.exp.path:
            return Path(self.exp.path).stem
        return self.exp.record.get("title") or "stainless"

    def _title(self):
        name = Path(self.exp.path).name if self.exp.path else "new experiment"
        self.setWindowTitle(f"{APP_NAME} {__version__} — {name}{' •' if self.dirty else ''}")

    def snap(self):
        self.undo.append(json.dumps(self.exp.to_dict(maps=False), default=float))
        del self.undo[:-100]
        self.redo.clear()

    def _restore(self, text):
        old = {s.key: s for s in self.exp.sections}
        e = model.Experiment.from_dict(json.loads(text), self.exp.path,
                                       known={k: s.scene for k, s in old.items()})[0]
        for s in e.sections:
            if s.key in old and old[s.key].result.get("fingerprint") == s.result.get("fingerprint"):
                s.maps = old[s.key].maps
        e.acquisition = self.exp.acquisition
        self.exp = e
        self.sections_tab.sync_settings()
        self.changed()

    def do_undo(self):
        if self.undo:
            self.redo.append(json.dumps(self.exp.to_dict(maps=False), default=float))
            self._restore(self.undo.pop())

    def do_redo(self):
        if self.redo:
            self.undo.append(json.dumps(self.exp.to_dict(maps=False), default=float))
            self._restore(self.redo.pop())

    def changed(self, view=True):
        self.dirty = True
        self._title()
        if view:
            self.refresh_all()
        else:                                   # the list's states and the side panel
            st = self.sections_tab
            for i, s in enumerate(self.exp.sections):
                if i < st.list.count():
                    st.list.item(i).setText(st._item_text(s))
            st._todo()
            st.fill_result()
            st.fill_regions(st.regions.currentRow())
            self.show_maps(st.current())
            self._refresh_tab()

    def refresh_all(self):
        self.sections_tab.refresh()
        self._refresh_tab()

    def _refresh_tab(self):
        w = self.tabs.currentWidget()
        if w is not self.sections_tab:
            w.refresh()

    # -- files
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.open_paths([Path(u.toLocalFile()) for u in e.mimeData().urls()])

    def open_paths(self, paths):
        js = [p for p in paths if p.suffix.lower() == ".json"]
        if js:
            self.open(js[0])
        rest = [p for p in paths if p.suffix.lower() != ".json"]
        if rest:
            self.add(rest)

    def add_dialog(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add scans", self.opts.get("last_dir", ""),
                                                "Zeiss CZI (*.czi)")
        if files:
            self.add([Path(f) for f in files])

    def add(self, paths):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.snap()
            new, notes = self.exp.add(paths)
        finally:
            QApplication.restoreOverrideCursor()
        self.log(*[f"⚠ {n}" for n in notes])
        if paths:
            self.opts["last_dir"] = str(Path(paths[0]).parent if Path(paths[0]).is_file()
                                        else paths[0])
            self.save_options()
        if not new:
            self.undo.pop()
            self.log("nothing new to add" if not notes else "")
            return
        self.log(f"✓ {len(new)} section(s) added")
        self.changed()
        self._load_thumbs(new)

    def _load_thumbs(self, secs):
        todo = [s for s in secs if s.scene is not None and s.key not in self.thumbs]

        def work():
            for s in todo:
                try:
                    sc = s.scene
                    z = level_for(max(sc.w, sc.h), THUMB * 2)
                    rgb = czi.read(sc, 0, 0, sc.w, sc.h, z)
                    rgb[(rgb.max(2) <= 5) | (rgb.min(2) >= 250)] = 60     # padding: dark
                    img = to_qimage(rgb).scaled(THUMB, THUMB, Qt.KeepAspectRatio,
                                                Qt.SmoothTransformation)
                    self.later(lambda s=s, img=img: self._thumb_ready(s, img))
                except Exception as e:
                    self.later(lambda s=s, e=e: self.log(f"⚠ {s.name}: no thumbnail ({e})"))
        threading.Thread(target=work, daemon=True).start()

    def _thumb_ready(self, s, img):
        self.thumbs[s.key] = QPixmap.fromImage(img)
        st = self.sections_tab
        if s in self.exp.sections:
            i = self.exp.sections.index(s)
            if i < st.list.count():
                st.list.item(i).setIcon(QIcon(self.thumbs[s.key]))

    def open_dialog(self):
        p, _ = QFileDialog.getOpenFileName(self, "Open experiment", self.opts.get("last_dir", ""),
                                           "Stainless experiment (*.json)")
        if p:
            self.open(Path(p))

    def open(self, p: Path):
        if not self._may_discard():
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            exp, problems = model.Experiment.open(p)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            self.log(f"✗ {p.name}: {e}")
            return
        QApplication.restoreOverrideCursor()
        self.exp = exp
        self.undo.clear()
        self.redo.clear()
        self.dirty = False
        self.opts["last_dir"] = str(p.parent)
        self.save_options()
        self.log(f"✓ opened {p.name}", *[f"⚠ {x}" for x in problems])
        self.sections_tab.sync_settings()
        self.refresh_all()
        self._title()
        self._load_thumbs(exp.sections)

    def save(self):
        if not self.exp.path:
            return self.save_as()
        try:
            self.exp.save(self.exp.path)
        except OSError as e:
            self.log(f"✗ not saved: {e}")
            return False
        self.dirty = False
        self._title()
        self.log(f"✓ saved {self.exp.path}")
        return True

    def save_as(self):
        start = self.exp.path or str(Path(self.opts.get("last_dir", "")) / f"{self.stem()}.json")
        p, _ = QFileDialog.getSaveFileName(self, "Save experiment", start,
                                           "Stainless experiment (*.json)")
        if not p:
            return False
        self.exp.path = str(Path(p).resolve())
        return self.save()

    def export_dialog(self):
        if not self.exp.rows():
            self.log("nothing measured yet")
            return
        p, _ = QFileDialog.getSaveFileName(
            self, "Export results", str(Path(self.opts.get("last_dir", "")) /
                                        f"{self.stem()}_results.xlsx"),
            "Excel (*.xlsx);;CSV (*.csv)")
        if not p:
            return
        try:
            (self.exp.export_csv if p.lower().endswith(".csv") else self.exp.export_xlsx)(p)
            self.log(f"✓ results exported: {p}")
        except OSError as e:
            self.log(f"✗ not exported (open in Excel?): {e}")

    def _may_discard(self) -> bool:
        if not self.dirty or not self.exp.sections:
            return True
        r = QMessageBox.question(self, APP_NAME, "Save the changes to this experiment first?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        return r == QMessageBox.Discard or (r == QMessageBox.Save and bool(self.save()))

    def closeEvent(self, e):
        if self._cancel is not None:
            self._cancel.set()
        if self._may_discard():
            e.accept()
        else:
            e.ignore()

    # -- the slide
    def show_section(self, s: model.Section, force=True):
        view = self.sections_tab.view
        if s.scene is None:
            view.clear()
            return
        if view.sc is s.scene and not force:
            self.show_maps(s)
            return
        ov = self.overviews.get(s.key)
        if ov is None:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                sc = s.scene
                z = level_for(max(sc.w, sc.h), 2400)
                rgb = czi.read(sc, 0, 0, sc.w, sc.h, z)
                ov = (rgb, z, analysis.estimate_glass(rgb))
                self.overviews[s.key] = ov
                while len(self.overviews) > 6:
                    self.overviews.pop(next(iter(self.overviews)))
            except Exception as e:
                QApplication.restoreOverrideCursor()
                self.log(f"✗ {s.name}: not readable ({e})")
                return
            QApplication.restoreOverrideCursor()
        rgb, z, glass = ov
        glass = s.result.get("glass", glass) if s.result else glass
        view.show_section(s.scene, rgb, z, np.asarray(glass, np.float32))
        view.set_settings(self.exp.settings_for(s))
        self.show_maps(s)
        self._show_label(s)

    def show_maps(self, s):
        view = self.sections_tab.view
        if s is None or not s.maps or not s.result:
            view.set_maps({}, 0)
            return
        k = (s.key, s.result.get("fingerprint"))
        if k not in self.decoded:
            self.decoded[k] = {n: analysis.decode_map(v) for n, v in s.maps.items()}
        view.set_maps(self.decoded[k], s.result["cell_px"])

    def _show_label(self, s):
        lab = self.sections_tab.label
        if s.path not in self.labels:
            self.labels[s.path] = None

            def work(path=s.path):
                a = czi.label(path)
                if a is not None:
                    img = to_qimage(a)
                    self.later(lambda: self._label_ready(path, img))
            threading.Thread(target=work, daemon=True).start()
        pm = self.labels.get(s.path)
        lab.setPixmap(pm.scaled(240, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                      if pm is not None else QPixmap())

    def _label_ready(self, path, img):
        self.labels[path] = QPixmap.fromImage(img)
        cur = self.sections_tab.current()
        if cur is not None and cur.path == path:
            self._show_label(cur)

    # -- measuring, off the GUI thread
    def measure(self, only: model.Section | None):
        if self._cancel is not None:
            return
        exp = self.exp
        if only is not None:
            todo = [only] if only.scene is not None else []
        else:
            todo = [s for s in exp.sections if s.role != "left out" and s.scene is not None
                    and exp.state(s) != "measured"]
        if not todo:
            self.log("nothing to measure: every section is measured with these settings")
            return
        st = copy.deepcopy(exp.settings)
        jobs = [(s, s.scene, copy.deepcopy(s.regions), exp.settings_for(s), bool(s.rule))
                for s in todo]
        self._cancel = cancel = threading.Event()
        tab = self.sections_tab
        tab.go_one.setEnabled(False)
        tab.go_all.setEnabled(False)
        tab.stop.setEnabled(True)
        t0 = time.perf_counter()
        self.log(f"measuring {len(jobs)} section(s), "
                 f"{'the scan' if not st.level else f'{2 ** st.level}×{2 ** st.level} averaged'}"
                 f" pixels…")

        def progress(i, n, done, total):
            el = time.perf_counter() - t0
            frac = (i + done / max(total, 1)) / n
            left = el / frac - el if frac > 0.02 else None
            text = (f"{i + 1} of {n}: {jobs[i][0].name} — {100 * done / max(total, 1):.0f} %"
                    + (f" · ~{left / 60:.0f} min left" if left and left > 90 else
                       f" · ~{left:.0f} s left" if left else ""))
            self.later(lambda: (tab.bar.setValue(int(1000 * frac)), tab.progress.setText(text)))

        def work():
            for i, (s, sc, regions, sst, own) in enumerate(jobs):
                if cancel.is_set():
                    break
                try:
                    r = analysis.analyze(sc, sst, regions, cancel=cancel,
                                         progress=lambda d, t, i=i: progress(i, len(jobs), d, t))
                except Exception as e:
                    self.later(lambda s=s, e=e: self.log(f"✗ {s.name}: {e}"))
                    continue
                if not r:
                    break
                r["fingerprint"] = analysis.fingerprint(sc, sst, regions)
                r["settings"] = asdict(sst)
                r["own_rule"] = own
                r["stainless"] = __version__
                maps = r.pop("maps")
                enc = {k: analysis.encode_map(v) for k, v in maps.items()}
                self.later(lambda s=s, r=r, maps=maps, enc=enc: self._measured(s, r, maps, enc))
            self.later(self._measure_done)
        tab.bar.setMaximum(1000)
        tab.bar.setValue(0)
        threading.Thread(target=work, daemon=True).start()

    def _measured(self, s, r, maps, enc):
        if s not in self.exp.sections:
            return
        s.result, s.maps = r, enc
        self.decoded[(s.key, r["fingerprint"])] = maps
        row = r["rows"][0]
        self.log(f"✓ {s.name}: {fmt(row['pct'])} % of {row['tissue_mm2']:.2f} mm² stained"
                 + "".join(f"; {x['region']} {fmt(x['pct'])} %" for x in r["rows"][1:])
                 + f"  ({r['seconds']:g} s)")
        self.changed(view=False)

    def _measure_done(self):
        tab = self.sections_tab
        stopped = self._cancel is not None and self._cancel.is_set()
        self._cancel = None
        tab.go_one.setEnabled(True)
        tab.go_all.setEnabled(True)
        tab.stop.setEnabled(False)
        tab.bar.setValue(0 if stopped else tab.bar.maximum())
        tab.progress.setText("stopped" if stopped else "done")
        if stopped:
            self.log("stopped: the sections finished before Stop keep their results")
        tab._todo()

    def stop_measure(self):
        if self._cancel is not None:
            self._cancel.set()
