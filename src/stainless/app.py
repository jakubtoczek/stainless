"""The window. Widgets only: the measurement is analysis.py's, the experiment model.py's,
the slide view viewer.py's, the figure plot.py's, the options options.py's.

Tabs, in the order of the work: Sections (the slides, their regions, the settings,
Measure) → Results → Experiment (the record of what was done). Drop anything anywhere:
CZI scans or folders of them, a saved experiment (.json).
"""

from __future__ import annotations

import copy
import datetime
import json
import queue
import sys
import threading
import time
import traceback
from dataclasses import asdict, replace
from pathlib import Path

from PySide6.QtCore import QEvent, QLocale, QObject, QPointF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QAction, QColor, QIcon, QKeySequence, QPainter, QPixmap, QPolygonF,
                           QShortcut, QTransform)
from PySide6.QtWidgets import (QAbstractItemView, QAbstractScrollArea, QAbstractSpinBox,
                               QApplication, QCheckBox, QComboBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QFrame, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
                               QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                               QScrollArea, QSizePolicy, QSlider, QSplitter, QTableWidget,
                               QTableWidgetItem, QTabWidget, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from . import __version__, analysis, czi, model, options
from .options import ColorButton, hexa, raise_window
from .viewer import (REGION_COLOURS, SlideView, default_colour, display, level_for,
                     to_qimage, wb_lut)

APP_NAME = "Stainless"
_PKG = Path(__file__).resolve().parent
OPTIONS = options.FILE                                     # beside Stainless.bat
HINT = "Drop CZI scans or folders anywhere on this window, or a saved experiment (.json)."
RESOLUTIONS = ["scan", "½", "¼"]       # Settings.level 0, 1, 2
THUMB = 72
CHECKED = ("QPushButton{padding:4px 8px; border:1px solid #4a4a4a; border-radius:4px;}"
           "QPushButton:checked{background:#2f6ea5; color:#fff; border:1px solid #4aa3ff;}")


def app_icon():
    p = _PKG / "assets" / "stainless.ico"
    return QIcon(str(p)) if p.exists() else None


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


class Block(QFrame):
    """A titled, bordered block of the side panel; its title folds it."""

    def __init__(self, title: str, collapsed=False, on_reset=None):
        super().__init__(objectName="sec")
        self.setStyleSheet("QFrame#sec{border:1px solid #3a3a3a; border-radius:6px;}")
        col = QVBoxLayout(self)
        col.setContentsMargins(8, 4, 8, 6)
        col.setSpacing(5)
        head = QHBoxLayout()
        self.title = title
        self.head = QPushButton(flat=True, cursor=Qt.PointingHandCursor,
                                toolTip="fold or unfold")
        self.head.setStyleSheet("QPushButton{color:#8b98a5; font-weight:600; font-size:10px; "
                                "border:none; text-align:left; padding:2px 0;}")
        self.head.clicked.connect(lambda: self.fold(self.body.isVisible()))
        head.addWidget(self.head, 1)
        if on_reset is not None:
            b = QPushButton("Reset", flat=True, clicked=on_reset)
            b.setFixedHeight(18)
            b.setStyleSheet("color:#7fa8d0; border:none; font-size:11px;")
            head.addWidget(b)
            self.reset = b
        col.addLayout(head)
        self.body = QWidget()
        self.v = QVBoxLayout(self.body)
        self.v.setContentsMargins(0, 0, 0, 0)
        self.v.setSpacing(5)
        col.addWidget(self.body)
        self.fold(collapsed)

    def fold(self, on: bool):
        self.body.setVisible(not on)
        self.head.setText(("▸  " if on else "▾  ") + self.title.upper())


class Grip(QWidget):
    """A handle under a list: drag it to make the list taller or shorter."""

    def __init__(self, target: QWidget, on_done):
        super().__init__()
        self.target, self.on_done = target, on_done
        self.setFixedHeight(9)
        self.setCursor(Qt.SizeVerCursor)
        self.setToolTip("drag to resize")
        self._y = None

    def paintEvent(self, e):
        p = QPainter(self)
        p.setPen(QColor("#6a6a6a"))
        c = self.width() // 2
        for y in (3, 6):
            p.drawLine(c - 14, y, c + 14, y)

    def mousePressEvent(self, e):
        self._y, self._h = e.globalPosition().y(), self.target.height()

    def mouseMoveEvent(self, e):
        if self._y is not None:
            self.target.setFixedHeight(max(40, int(self._h + e.globalPosition().y() - self._y)))

    def mouseReleaseEvent(self, e):
        self._y = None
        self.on_done(self.target.height())


def segmented(options_, on_change):
    row = QWidget()
    row.setStyleSheet(CHECKED.replace("4px 8px", "5px"))
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(4)
    btns = []
    for i, text in enumerate(options_):
        b = QPushButton(text, checkable=True, autoExclusive=True)
        b.toggled.connect(lambda on, i=i: on and on_change(i))
        h.addWidget(b, 1)
        btns.append(b)
    return row, btns


def pill(text: str, tip: str, clickable=True) -> QPushButton:
    """A small button that reads as pushed when on; *clickable* False: an indicator."""
    b = QPushButton(text, checkable=True, toolTip=tip)
    b.setStyleSheet(CHECKED.replace("4px 8px", "1px 8px") +
                    "QPushButton{color:#8d8d8d; font-size:11px;}")
    b.setFixedHeight(20)
    if not clickable:
        b.setFocusPolicy(Qt.NoFocus)
        b.setAttribute(Qt.WA_TransparentForMouseEvents)
    return b


def link(text: str, on_click) -> QPushButton:
    b = QPushButton(text, flat=True, cursor=Qt.PointingHandCursor, clicked=on_click)
    b.setStyleSheet("QPushButton{color:#7fa8d0; border:none; text-align:left; font-size:11px;"
                    "padding:0;}")
    return b


class Switch(QCheckBox):
    """A checkbox drawn as an on/off slider."""
    W, H = 34, 16

    def __init__(self, text=""):
        super().__init__(text)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(self.H + 6)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def sizeHint(self):
        return QSize(self.W + 12 + self.fontMetrics().horizontalAdvance(self.text()),
                     self.H + 6)

    minimumSizeHint = sizeHint

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


def hbox(*ws, stretch_at=None) -> QHBoxLayout:
    h = QHBoxLayout()
    h.setContentsMargins(0, 0, 0, 0)
    for i, w in enumerate(ws):
        if i == stretch_at:
            h.addStretch(1)
        h.addWidget(w)
    return h


class _Bridge(QObject):
    call = Signal(object)


def fmt(v, nd=2) -> str:
    return "–" if v is None else f"{v:.{nd}f}"


# --- Sections tab ------------------------------------------------------------------------

class SectionsTab(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        o = win.opts
        self._busy = False                         # filling widgets: no edits back

        # left: the sections; the selected one's fields and slide label below
        self.list = QListWidget()
        self.list.setIconSize(QSize(THUMB, THUMB))
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setStyleSheet("QListWidget::item{padding:3px;}")
        self.list.currentRowChanged.connect(self._selected)
        self.alias = QLineEdit()
        self.alias.setToolTip("what this section is shown and reported as in this "
                              "experiment; empty: the scan's name")
        self.alias.editingFinished.connect(self._alias)
        shorten = QPushButton("Shorten", clicked=self.shorten,
                              toolTip="an alias for the selected sections (all, if one is "
                                      "selected): their names without the words every one "
                                      "has — the date, the study, the stain…")
        self.group = QComboBox(editable=True)
        self.group.lineEdit().setPlaceholderText("e.g. MI 7 d, sham")
        self.role = QComboBox()
        self.role.addItems(model.ROLES)
        self.note = QLineEdit(placeholderText="anything about this section")
        self.group.lineEdit().editingFinished.connect(
            lambda: self._field("group", self.group.currentText()))
        self.group.activated.connect(lambda _: self._field("group", self.group.currentText()))
        self.role.activated.connect(lambda _: self._field("role", self.role.currentText()))
        self.note.editingFinished.connect(lambda: self._field("note", self.note.text()))
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        ar = QHBoxLayout()
        ar.addWidget(self.alias, 1)
        ar.addWidget(shorten)
        form.addRow("alias", ar)
        for lab, w in (("group", self.group), ("role", self.role), ("note", self.note)):
            form.addRow(lab, w)
        self.scan = dim("")
        form.addRow("scan", self.scan)
        self.label = QLabel(alignment=Qt.AlignCenter)
        self.label.setMinimumHeight(60)
        self.label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.label.setToolTip("the slide's label, as the scanner photographed it")
        add = QPushButton("Add scans…", clicked=win.add_dialog, toolTip="CZI files (Ctrl+I)")
        rem = QPushButton("Remove", clicked=self.remove,
                          toolTip="take the selected sections out of the experiment (Del)")
        table = QPushButton("Table…", clicked=win.show_table,
                            toolTip="every section in a window of its own: alias, scan, group, "
                                    "role, state, results… (Ctrl+L)")
        top = QWidget()
        tv = QVBoxLayout(top)
        tv.setContentsMargins(0, 0, 0, 0)
        tv.addLayout(hbox(add, rem, table))
        tv.addWidget(self.list, 1)
        bottom = QWidget()
        bv = QVBoxLayout(bottom)
        bv.setContentsMargins(0, 4, 0, 0)
        bv.addLayout(form)
        bv.addWidget(self.label, 1)
        self.left = QSplitter(Qt.Vertical)
        self.left.addWidget(top)
        self.left.addWidget(bottom)
        self.left.setStretchFactor(0, 1)
        self.left.setSizes([560, 300])

        # centre: the slide
        self.view = SlideView()
        self.view.region_drawn.connect(self._region_drawn)
        self.view.region_clicked.connect(self._region_clicked)
        self.view.region_edited.connect(self._region_edited)
        self.view.draw_ended.connect(lambda: self.draw.setChecked(False))
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
        folded = o["panel"]["collapsed"]
        self.blocks = {}

        def block(title, on_reset=None):
            b = Block(title, folded.get(title, False), on_reset)
            self.blocks[title] = b
            pv.addWidget(b)
            return b.v

        b = block("Stain")
        row, self.prof_btns = segmented(["DAB", "SA-β-gal"], self._profile)
        self.prof_btns[0].setToolTip(analysis.PROFILES["dab"]["label"])
        self.prof_btns[1].setToolTip(analysis.PROFILES["sabg"]["label"])
        b.addWidget(row)

        b = block("Stained when", self._reset_rule)
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
        self.rule_more = QWidget()
        mv = QVBoxLayout(self.rule_more)
        mv.setContentsMargins(0, 4, 0, 0)
        row, self.tissue_od = slider_row(
            "tissue: OD ≥", 0.0, 0.6, 0.01, 2,
            "A pixel is tissue when its own summed OD reaches this (0: the whole outline, "
            "for a stain without counterstain). Shared by every section.",
            lambda v: self._setting("tissue_od", v))
        mv.addWidget(row)
        row, self.outline_od = slider_row(
            "outline: OD ≥", 0.0, 0.4, 0.005, 3,
            "The section's outline: the absorbance smoothed over ~16 µm reaches this. "
            "Lower: faint tissue in; higher: dust and glass smears out.",
            lambda v: self._setting("outline_od", v))
        mv.addWidget(row)
        mv.addWidget(dim("What counts as tissue (Show › not counted as tissue shows it)."))
        self.rule_more_link = link("", lambda: self._more("rule"))
        b.addWidget(self.rule_more_link)
        b.addWidget(self.rule_more)

        b = block("Resolution")
        row, self.res_btns = segmented(RESOLUTIONS, self._resolution)
        tips = ["the scan's own pixels: ~4× slower, and the camera's pixel noise reads as "
                "stain (a negative control +66 %; ×14 on a 10× scan)",
                "2 × 2 scan pixels averaged (the file's own ½ level): the same numbers as the "
                "scan's pixels smoothed by ~2 px, a quarter of the time. The default",
                "4 × 4 averaged: 4× faster again; thin stained structures start to fade"]
        for btn, tip in zip(self.res_btns, tips):
            btn.setToolTip(tip)
        b.addWidget(row)
        self.res_info = dim("")
        b.addWidget(self.res_info)

        b = block("Regions")
        self.kind = QComboBox(editable=True)
        self.kind.setToolTip("what the next region is: an ROI (by its name: ROIs of one name "
                             "are pooled) or an exclusion (folds, debris, bubbles: taken out "
                             "of the tissue). Picking one starts drawing")
        self.kind.activated.connect(lambda _: self.draw.setChecked(True))
        self.draw = QPushButton("Draw", checkable=True,
                                toolTip="click the corners of a polygon (double-click or "
                                        "Enter closes it), or hold the left button and trace; "
                                        "right-click or Esc cancels (D)")
        self.draw.setStyleSheet(CHECKED)
        self.draw.toggled.connect(self.view.set_drawing)
        b.addLayout(hbox(self.kind, self.draw))
        self.kind.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(2)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().resizeSection(1, 34)
        self.tree.setIndentation(14)
        self.tree.setFixedHeight(o["sizes"]["regions"])
        self.tree.currentItemChanged.connect(self._tree_selected)
        self.tree.itemChanged.connect(self._region_renamed)
        b.addWidget(self.tree)
        b.addWidget(Grip(self.tree, lambda h: self._size("regions", h)))
        b.addWidget(dim("Click a region to select it, then drag its points; click its line "
                        "to add one; Del removes the point (or the region). Double-click a "
                        "name to change it."))
        self.find_one = QPushButton("This section", clicked=lambda: self.find([self.current()]))
        self.find_all = QPushButton("All", clicked=lambda: self.find(None))
        for btn in (self.find_one, self.find_all):
            btn.setToolTip("folds, bubbles, debris: found and added as excluded regions "
                           "(found again: replaced). Options › Artefacts sets what is looked for")
        lab = QLabel("Find artefacts")
        b.addLayout(hbox(lab, self.find_one, self.find_all, stretch_at=1))

        b = block("Show")
        lay = win.opts["show"]
        self.view.layers.update(lay)
        self.sw = {}
        self.live = pill("live", "on when zoomed in far enough: the slide is read at the "
                                 "measured resolution and coloured, live, with the current "
                                 "rule. Zoomed out: what was measured", clickable=False)
        self.view.live_changed.connect(self._live)
        self.swatch = {}
        for key, text, extra in (("stained", "stained", True), ("tissue", "not counted as tissue", True),
                                 ("roi", "ROIs", False), ("excluded", "excluded", True),
                                 ("scalebar", "scale bar", True)):
            s = Switch(text)
            s.setChecked(lay[key])
            s.toggled.connect(lambda on, k=key: self._layer(k, on))
            self.sw[key] = s
            row = [s]
            if key == "stained":
                row.append(self.live)
            if extra:
                c = ColorButton("#000000", size=(22, 14))
                c.picked.connect(lambda col, k=key: self._pick_colour(k, col))
                self.swatch[key] = c
                row.append(c)
            b.addLayout(hbox(*row, stretch_at=1))
        self.wb = pill("auto", "white balance: the glass shown white, from each section's "
                               "own glass. Only what is shown; the measurement takes OD "
                               "against the glass anyway")
        self.wb.setChecked(lay["wb"])
        self.wb.toggled.connect(lambda on: self._layer("wb", on))
        b.addLayout(hbox(QLabel("white balance"), self.wb, stretch_at=1))

        b = block("Measure")
        self.go_one = QPushButton("This section", clicked=lambda: win.measure(self.current()))
        self.go_all = QPushButton("All", clicked=lambda: win.measure(None),
                                  toolTip="every section not measured yet, or measured with "
                                          "other settings or regions (Ctrl+M)")
        self.stop = QPushButton("Stop", clicked=win.stop_measure, enabled=False)
        b.addLayout(hbox(self.go_one, self.go_all, self.stop))
        self.bar = QProgressBar(textVisible=False, maximumHeight=8)
        b.addWidget(self.bar)
        self.progress = dim("")
        b.addWidget(self.progress)
        self.result = QTableWidget(0, 3)
        self.result.setHorizontalHeaderLabels(["region", "tissue mm²", "% stained"])
        self.result.verticalHeader().hide()
        self.result.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.result.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.result.setFixedHeight(o["sizes"]["measure"])
        b.addWidget(self.result)
        b.addWidget(Grip(self.result, lambda h: self._size("measure", h)))
        self.state = dim("")
        b.addWidget(self.state)
        self.measure_more_link = link("", lambda: self._more("measure"))
        b.addWidget(self.measure_more_link)
        self.more = {"rule": o["panel"]["more_rule"], "measure": o["panel"]["more_measure"]}
        pv.addStretch()
        scroll = QScrollArea(widgetResizable=True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(panel)
        scroll.setMinimumWidth(300)

        self.split = QSplitter()
        self.split.addWidget(self.left)
        self.split.addWidget(centre)
        self.split.addWidget(scroll)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([260, 900, 330])
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self.split)

        for key, fn in (("D", lambda: self.draw.toggle()), ("F", self.view.fit),
                        ("Ctrl+M", lambda: win.measure(None))):
            QShortcut(QKeySequence(key), self, activated=fn)
        QShortcut(QKeySequence.Delete, self, activated=self._delete)
        self.apply_options()
        self._more(None)
        self.sync_settings()

    # -- options
    def apply_options(self):
        """Colours, the names offered, the view's style: from the options."""
        cur = self.kind.currentText()
        self.kind.blockSignals(True)
        self.kind.clear()
        self.kind.addItems(["ROI", *self.win.preset(), "exclude"])
        self.kind.setCurrentText(cur if cur else "ROI")
        self.kind.blockSignals(False)
        self.view.set_style(self.win.view_style(), self.win.roi_colour)
        for k, c in self.swatch.items():
            c.set(self.win.colour(k))
        if self.current() is not None:
            self.fill_regions(self._region_index())
            self._show_label_again()

    def _show_label_again(self):
        s = self.current()
        if s is not None:
            self.win._show_label(s)

    def _pick_colour(self, key, col):
        o = self.win.opts
        if key == "stained":
            o["colours"]["stained_" + self.win.exp.settings.profile] = col
        else:
            o["colours"][key] = col
        self.win.save_options()
        self.win.apply_options("colours")

    def _size(self, key, h):
        self.win.opts["sizes"][key] = h
        self.win.save_options()

    def _more(self, which):
        if which:
            self.more[which] = not self.more[which]
        self.rule_more.setVisible(self.more["rule"])
        self.rule_more_link.setText("less ▴" if self.more["rule"] else "more ▾")
        self.measure_more_link.setText("less ▴" if self.more["measure"] else
                                       "more ▾  (whole section, excluded, details)")
        if which == "measure":
            self.fill_result()

    def _layer(self, key, on):
        self.view.set_layer(key, on)
        if key == "stained":
            self._live(self.view.live)

    def _live(self, on):
        self.live.setChecked(on and self.sw["stained"].isChecked())

    # -- helpers
    def current(self) -> model.Section | None:
        i = self.list.currentRow()
        secs = self.win.exp.sections
        return secs[i] if 0 <= i < len(secs) else None

    def sync_settings(self):
        s = self.current()
        exp = self.win.exp
        st = exp.settings_for(s) if s is not None else exp.settings
        boxes = (self.thr, self.minod, self.tissue_od, self.outline_od)
        for w in (*self.prof_btns, *self.res_btns, *boxes):
            w.blockSignals(True)
        self.prof_btns[list(analysis.PROFILES).index(st.profile)].setChecked(True)
        self.res_btns[min(max(st.level, 0), 2)].setChecked(True)
        for box, k in zip(boxes, ("threshold", "min_od", "tissue_od", "outline_od")):
            box.setValue(getattr(st, k))
        for w in (*self.prof_btns, *self.res_btns, *boxes):
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
        for box in boxes:                                   # the slider follows the box
            sl = box.parentWidget().findChild(QSlider)
            sl.blockSignals(True)
            sl.setValue(int(round(box.value() / box.singleStep())))
            sl.blockSignals(False)
        self.swatch["stained"].set(self.win.colour("stained"))
        self.view.set_settings(st)

    def refresh(self):
        """The list, then the selected section."""
        exp = self.win.exp
        row = max(0, self.list.currentRow())
        self.list.blockSignals(True)
        self.list.clear()
        for s in exp.sections:
            it = QListWidgetItem(self._item_text(s))
            it.setToolTip(s.name)
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
        return f"{s.shown}\n{s.group or '–'} · {s.role} · {tail}{' · own rule' if s.rule else ''}"

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
        self.alias.setText(s.alias)
        self.alias.setPlaceholderText(s.name)
        self.group.setCurrentText(s.group)
        self.role.setCurrentText(s.role)
        self.note.setText(s.note)
        self.scan.setText(s.scan)
        self._busy = False
        self.draw.setChecked(False)
        self.win.show_section(s, force=force)
        self.sync_settings()
        self._res_info()
        self.fill_regions()
        self.fill_result()

    def _region_index(self) -> int:
        it = self.tree.currentItem()
        return it.data(0, Qt.UserRole) if it is not None else -1

    def _labels(self, s) -> dict:
        """The ROIs' % stained, by region index, for the view."""
        if not s.result or self.win.exp.stale(s):
            return {}
        pct = {r["region"]: r["pct"] for r in s.result.get("rows", [])}
        return {i: f"{pct[r['name']]:.2f} %" for i, r in enumerate(s.regions)
                if r["kind"] == "roi" and pct.get(r["name"]) is not None}

    def fill_regions(self, select: int = -1):
        s = self.current()
        self.tree.blockSignals(True)
        open_ = self.tree.topLevelItemCount() and any(
            self.tree.topLevelItem(k).isExpanded() for k in range(self.tree.topLevelItemCount())
            if self.tree.topLevelItem(k).data(0, Qt.UserRole) == -1)
        self.tree.clear()
        excl_node, pick = None, None
        if s is not None:
            for i, r in enumerate(s.regions):
                it = QTreeWidgetItem([r["name"], ""])
                it.setData(0, Qt.UserRole, i)
                it.setFlags(it.flags() | Qt.ItemIsEditable)
                if r["kind"] == "exclude":
                    if excl_node is None:
                        excl_node = QTreeWidgetItem(["excluded", ""])
                        excl_node.setData(0, Qt.UserRole, -1)
                        excl_node.setFlags(excl_node.flags() & ~Qt.ItemIsEditable)
                    excl_node.addChild(it)
                    it.setToolTip(0, "excluded" + (" (found)" if r.get("auto") else ""))
                else:
                    self.tree.addTopLevelItem(it)
                    it.setForeground(0, self.win.roi_colour(r["name"]))
                    c = ColorButton(hexa(self.win.roi_colour(r["name"])), size=(22, 14),
                                    tip=f"the colour of every ROI named {r['name']}")
                    c.picked.connect(lambda col, n=r["name"]: self._roi_colour(n, col))
                    self.tree.setItemWidget(it, 1, c)
                if i == select:
                    pick = it
            if excl_node is not None:
                self.tree.addTopLevelItem(excl_node)
                n = excl_node.childCount()
                excl_node.setText(0, f"excluded ({n})")
                excl_node.setForeground(0, QColor(self.win.colour("excluded")))
                c = ColorButton(self.win.colour("excluded"), size=(22, 14),
                                tip="the colour of the excluded regions")
                c.picked.connect(lambda col: self._pick_colour("excluded", col))
                self.tree.setItemWidget(excl_node, 1, c)
                excl_node.setExpanded(bool(open_) or (pick is not None and pick.parent() is excl_node))
        if pick is not None:
            self.tree.setCurrentItem(pick)
        self.tree.blockSignals(False)
        self.view.set_regions(s.regions if s else [], select if pick is not None else -1,
                              self._labels(s) if s else {})

    def _roi_colour(self, name, col):
        self.win.opts["region_colours"][name] = col
        self.win.save_options()
        self.win.apply_options("region_colours")

    def fill_result(self):
        s = self.current()
        exp = self.win.exp
        rows = s.result.get("rows", []) if (s and s.result) else []
        more = self.more["measure"]
        shown = []
        if rows:
            whole, rest = rows[0], [r for r in rows[1:] if r["region"] != analysis.REST]
            tail = [r for r in rows[1:] if r["region"] == analysis.REST]
            shown = ([whole] if more or not rest else []) + rest + tail
            if more and s.result.get("excluded_mm2") is not None:
                shown.append({"region": "excluded", "tissue_mm2": s.result["excluded_mm2"],
                              "pct": None})
        self.result.setRowCount(len(shown))
        for i, r in enumerate(shown):
            name = analysis.WHOLE if r["region"] == "section" else r["region"]
            for j, v in enumerate((name, fmt(r["tissue_mm2"], 3), fmt(r["pct"]))):
                it = QTableWidgetItem(v)
                if j:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if r["region"] in (analysis.REST, "excluded"):
                    it.setForeground(QColor("#a8a8a8"))
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
                + (f"{r['um_per_px']:g} µm/px, {r['tiles']} tiles in {r['seconds']:g} s, "
                   f"glass RGB {', '.join(f'{g:.0f}' for g in r['glass'])}" if more else ""))

    # -- edits
    def _field(self, key, value):
        s = self.current()
        if self._busy or s is None or getattr(s, key) == value:
            return
        self.win.snap()
        for i in self._rows():
            setattr(self.win.exp.sections[i], key, value)
        self.win.changed()

    def _alias(self):
        s = self.current()
        text = self.alias.text().strip()
        if self._busy or s is None or text == s.alias:
            return
        self.win.snap()
        s.alias = text
        self.win.changed()

    def shorten(self, secs=None):
        """Aliases for *secs* (the selected sections; all, if one is selected): their names
        without the words they all share."""
        if secs is None:
            rows = self._rows()
            secs = ([self.win.exp.sections[i] for i in rows] if len(rows) > 1
                    else self.win.exp.sections)
        if len(secs) < 2:
            self.win.log("Shorten: needs two sections at least, to see what their names share")
            return
        self.win.snap()
        short = model.short_names([s.name for s in secs])
        for s, a in zip(secs, short):
            s.alias = a
        self.win.changed()
        self.win.log(f"✓ {len(secs)} aliases: " + ", ".join(short[:4]) + (" …" if len(secs) > 4 else ""))

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
        if self.list.hasFocus():
            self.remove()
        elif self.view.node is not None and self.view.delete_node():
            pass
        elif self._region_index() >= 0:
            self._remove_region()

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
        if key in ("tissue_od", "outline_od"):
            self.win.show_base(s)
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
        self.win.exp.settings = analysis.Settings.of(key, self.win.exp.settings.level)
        own = [s for s in self.win.exp.sections if s.rule]
        for s in own:                                     # another stain's numbers
            s.rule = {}
        if own:
            self.win.log(f"own rules cleared ({len(own)} sections): they were for the other stain")
        self.win.show_base(self.current())
        self.win.apply_options("colours")
        self.sync_settings()
        self.win.changed(view=False)

    def _reset_rule(self):
        self.win.snap()
        s = self.current()
        if s is not None and s.rule:                      # back to the shared rule
            s.rule = {}
        else:
            st = self.win.exp.settings
            self.win.exp.settings = analysis.Settings.of(st.profile, st.level)
            self.win.show_base(s)
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

    # -- regions
    def _region_drawn(self, pts, nodes):
        s = self.current()
        if s is None:
            return
        text = self.kind.currentText().strip() or "ROI"
        if text.lower() == "exclude":
            r = {"name": model.next_name(s.regions, "exclude"), "kind": "exclude"}
        elif text == "ROI":
            r = {"name": model.next_name(s.regions, "ROI"), "kind": "roi"}
        else:
            r = {"name": text, "kind": "roi"}
        r["points"] = pts
        if nodes is not None:
            r["nodes"] = nodes
        self.win.snap()
        s.regions.append(model.filled(r))            # crossing itself: no hole
        self.draw.setChecked(False)
        self.win.changed(view=False, select=len(s.regions) - 1)

    def _region_clicked(self, i):
        if i < 0:
            self.tree.setCurrentItem(None)
            self._tree_selected(None)
            return
        self.fill_regions(i)

    def _tree_selected(self, it, *_):
        s = self.current()
        if s is not None:
            i = it.data(0, Qt.UserRole) if it is not None else -1
            self.view.set_regions(s.regions, i, self.view.labels)

    def _region_edited(self, i, r):
        s = self.current()
        if s is None or not (0 <= i < len(s.regions)):
            return
        self.win.snap()
        r.pop("auto", None)                          # edited by hand: kept by Find artefacts
        s.regions[i] = model.filled(r)
        if s.regions[i] is not r:                    # dragged across itself: a new outline
            self.view.node = None
        self.win.changed(view=False, select=i)

    def _region_renamed(self, it, _col):
        s = self.current()
        i = it.data(0, Qt.UserRole)
        if s is None or i is None or not (0 <= i < len(s.regions)):
            return
        name = it.text(0).strip()
        if name and name != s.regions[i]["name"]:
            self.win.snap()
            s.regions[i]["name"] = name
            s.regions[i].pop("auto", None)
            self.win.changed(view=False, select=i)

    def _remove_region(self):
        s = self.current()
        i = self._region_index()
        if s is None or not (0 <= i < len(s.regions)):
            return
        self.win.snap()
        del s.regions[i]
        self.win.changed(view=False)

    def find(self, secs):
        """Folds, bubbles, debris: found off the GUI thread, added as excluded regions."""
        exp = self.win.exp
        secs = [s for s in (secs or exp.sections) if s is not None and s.scene is not None
                and s.role != "left out"]
        if not secs or not self.find_one.isEnabled():
            return
        a = self.win.opts["artefacts"]
        kinds = {k for k in options.ARTEFACTS if a[k]}
        jobs = [(s, s.scene, exp.settings_for(s)) for s in secs]
        for b in (self.find_one, self.find_all):
            b.setEnabled(False)
        self.win.log(f"looking for {', '.join(sorted(kinds)) or 'nothing'} in "
                     f"{len(jobs)} section(s)…")

        def work():
            out = []
            for s, sc, st in jobs:
                try:
                    out.append((s, [f for f in analysis.find_artefacts(sc, st, a["factor"],
                                                                       a["min_mm2"])
                                    if f[0] in kinds]))
                except Exception as e:
                    self.win.later(lambda s=s, e=e: self.win.log(f"✗ {s.shown}: {e}"))
            self.win.later(lambda: self._found(out))
        threading.Thread(target=work, daemon=True).start()

    def _found(self, out):
        for b in (self.find_one, self.find_all):
            b.setEnabled(True)
        out = [(s, f) for s, f in out if s in self.win.exp.sections]
        if not out:
            return
        self.win.snap()
        for s, found in out:
            keep = [r for r in s.regions if not r.get("auto")]
            mine = [QPolygonF([QPointF(*p) for p in r["points"]]) for r in keep
                    if r["kind"] == "exclude"]
            found = [(what, pts) for what, pts in found if not any(   # already excluded by hand
                m.containsPoint(QPolygonF([QPointF(*p) for p in pts]).boundingRect().center(),
                                Qt.WindingFill) for m in mine)]
            for what, pts in found:
                keep.append({"name": model.next_name(keep, {"fold": "tissue fold"}.get(what, what)),
                             "kind": "exclude", "auto": True, "points": pts, "nodes": []})
            s.regions = keep
            n = {w: sum(1 for x, _ in found if x == w) for w in options.ARTEFACTS}
            self.win.log(f"✓ {s.shown}: " + (", ".join(f"{v} {options.ARTEFACTS[k]}"
                                                       for k, v in n.items() if v)
                                             or "nothing found") + " — excluded")
        self.win.changed(view=False)


# --- the sections window -----------------------------------------------------------------

class SectionsWindow(QMainWindow):
    """Every section in a row: its alias, the scan, the group, the role, its state and
    results. Alias, group and note are edited in place; a click on a row shows that
    section. Right-click the header: the columns shown (at start: Options › Sections)."""
    EDIT = ("alias", "group", "note")

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle(f"{APP_NAME} — sections")
        self.resize(1150, 440)
        self.keys = list(options.TABLE)
        self.t = QTableWidget(0, len(self.keys))
        self.t.setHorizontalHeaderLabels(list(options.TABLE.values()))
        self.t.verticalHeader().hide()
        self.t.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.t.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.t.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)   # the list's order
        self.t.setSortingEnabled(True)
        self.t.itemChanged.connect(self._edited)
        self.t.currentCellChanged.connect(self._go)
        h = self.t.horizontalHeader()
        h.setContextMenuPolicy(Qt.CustomContextMenu)
        h.customContextMenuRequested.connect(self._columns)
        for j, k in enumerate(self.keys):
            self.t.setColumnHidden(j, not win.opts["table"][k])
        shorten = QPushButton("Shorten", clicked=lambda: win.sections_tab.shorten(self._picked()),
                              toolTip="aliases for the selected rows (all, if one is selected): "
                                      "their names without the words they all share")
        shorten.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        root = QWidget()
        v = QVBoxLayout(root)
        v.addLayout(hbox(shorten, dim("Double-click an alias, a group or a note to change it. "
                                      "Right-click the header: the columns shown."),
                         stretch_at=2))
        v.addWidget(self.t, 1)
        self.setCentralWidget(root)
        QShortcut(QKeySequence.Copy, self.t, activated=self.copy)
        self._busy = False

    def open(self):
        raise_window(self)
        self.refresh()

    def _values(self, s: model.Section) -> dict:
        sc = s.scene
        rows = s.result.get("rows", []) if s.result else []
        whole = rows[0] if rows else {}
        return {"alias": s.alias, "name": s.name, "group": s.group, "role": s.role,
                "note": s.note, "state": self.win.exp.state(s), "pct": whole.get("pct"),
                "tissue": whole.get("tissue_mm2"),
                "regions": " · ".join(f"{r['region']} {fmt(r['pct'])} %" for r in rows[1:]),
                "rois": sum(r["kind"] == "roi" for r in s.regions),
                "excluded": sum(r["kind"] == "exclude" for r in s.regions),
                "rule": "own" if s.rule else "shared", "px": sc.px_um if sc else None,
                "size": f"{sc.w * sc.px_um / 1000:.1f} × {sc.h * sc.px_um / 1000:.1f}"
                        if sc else "–",
                "file": s.scan, "folder": str(Path(s.path).parent)}

    def refresh(self):
        if not self.isVisible():
            return
        secs = self.win.exp.sections
        self._busy = True
        self.t.setSortingEnabled(False)
        self.t.setRowCount(len(secs))
        for i, s in enumerate(secs):
            for j, (k, v) in enumerate(self._values(s).items()):
                it = QTableWidgetItem()
                if isinstance(v, (int, float)):
                    it.setData(Qt.DisplayRole, round(v, 3) if isinstance(v, float) else v)
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                else:
                    it.setText("–" if v is None else str(v))
                if k == "alias":
                    it.setToolTip(f"empty: shown as {s.name}")
                if k not in self.EDIT:
                    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
                if k == "state" and v != "measured":
                    it.setForeground(QColor("#d9a441"))
                it.setData(Qt.UserRole, i)
                self.t.setItem(i, j, it)
        self.t.setSortingEnabled(True)
        self.t.resizeColumnsToContents()
        self._busy = False

    def _section(self, row):
        it = self.t.item(row, 0)
        secs = self.win.exp.sections
        i = it.data(Qt.UserRole) if it is not None else -1
        return i if 0 <= i < len(secs) else -1

    def _picked(self):
        rows = sorted({ix.row() for ix in self.t.selectionModel().selectedRows()})
        secs = [self.win.exp.sections[i] for i in map(self._section, rows) if i >= 0]
        return secs if len(secs) > 1 else self.win.exp.sections

    def _go(self, row, *_):
        i = self._section(row)
        if not self._busy and i >= 0:
            self.win.sections_tab.list.setCurrentRow(i)

    def _edited(self, it):
        if self._busy:
            return
        k = self.keys[it.column()]
        i = it.data(Qt.UserRole)
        secs = self.win.exp.sections
        text = it.text().strip()
        if k not in self.EDIT or not (0 <= i < len(secs)) or getattr(secs[i], k) == text:
            return

        def apply():                      # after the edit: the table is rebuilt
            self.win.snap()
            setattr(secs[i], k, text)
            self.win.changed()
        QTimer.singleShot(0, apply)

    def _columns(self, pos):
        m = QMenu(self)
        for j, k in enumerate(self.keys):
            a = m.addAction(options.TABLE[k])
            a.setCheckable(True)
            a.setChecked(not self.t.isColumnHidden(j))
            a.toggled.connect(lambda on, j=j: self.t.setColumnHidden(j, not on))
        m.exec(self.t.horizontalHeader().mapToGlobal(pos))

    def copy(self):
        cols = [j for j in range(len(self.keys)) if not self.t.isColumnHidden(j)]
        lines = ["\t".join(options.TABLE[self.keys[j]] for j in cols)]
        for i in range(self.t.rowCount()):
            lines.append("\t".join(self.t.item(i, j).text() for j in cols))
        QApplication.clipboard().setText("\n".join(lines))
        self.win.log(f"✓ {self.t.rowCount()} sections copied")


# --- Results tab -------------------------------------------------------------------------

class ResultsTab(QWidget):
    COLS = [("section", "section"), ("group", "group"), ("role", "role"),
            ("region", "region"), ("tissue_mm2", "tissue mm²"), ("stained_mm2", "stained mm²"),
            ("pct", "% stained"), ("mean_od", "mean OD"), ("threshold", "hue ≥"),
            ("min_od", "OD ≥"), ("rule", "rule"), ("state", "state"), ("scan", "scan")]

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

    def _stain(self):
        st = self.win.exp.settings
        return ("DAB" if st.profile == "dab" else "SA-β-gal",
                QColor(self.win.colour("stained")).getRgb()[:3])

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
        stain, colour = self._stain()
        if self.canvas is None:
            self.fig = plot.figure(sm, region, stain, colour)
            self.canvas = FigureCanvasQTAgg(self.fig)
            self.canvas_box.addWidget(self.canvas, 1)
        else:
            plot.figure(sm, region, stain, colour, fig=self.fig)
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
            region = self.region.currentText()
            stain, colour = self._stain()
            f = plot.figure(model.summary(self.win.exp.rows(), region), region, stain, colour,
                            dark=False)
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
        self.opts = options.load(OPTIONS)
        sys.excepthook = self._error
        self.exp = model.Experiment(settings=analysis.Settings.of(self.opts["new"]["profile"],
                                                                  self.opts["new"]["level"]))
        self.undo, self.redo = [], []
        self.dirty = False
        self.thumbs: dict = {}                     # section key -> QPixmap
        self.overviews: dict = {}                  # section key -> (rgb, zoom)
        self.bases: dict = {}                      # (section key, outline settings) -> glass, outline
        self.decoded: dict = {}                    # (section key, fingerprint) -> maps
        self.labels: dict = {}                     # file -> QPixmap or None
        self._bridge = _Bridge()
        self._bridge.call.connect(lambda f: f())
        self._cancel = None
        self._ahead: queue.Queue = queue.Queue()
        threading.Thread(target=self._read_ahead, daemon=True).start()
        self.options_window = self.table_window = None
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
        self.logbox = QPlainTextEdit(readOnly=True)
        self.logbox.setMinimumHeight(0)
        self.logbox.setPlaceholderText("What happened, and what looks wrong (✗ ⚠). "
                                       "Drag the bar above down to close it.")

        bar = self.addToolBar("main")
        bar.setMovable(False)
        for text, key, tip, fn in [
                ("Open…", QKeySequence.Open, "an experiment (.json)", self.open_dialog),
                ("Save", QKeySequence.Save, "the experiment as one .json: sections, regions, "
                                            "settings, results", self.save),
                ("Save as…", QKeySequence.SaveAs, "", self.save_as),
                (None, None, None, None),
                ("Export results…", "Ctrl+E", ".xlsx or .csv", self.export_dialog),
                (None, None, None, None),
                ("Options…", "Ctrl+,", "colours and lines, what the panel starts with, names "
                                       "offered for regions, artefacts…", self.show_options)]:
            if text is None:
                bar.addSeparator()
                continue
            a = QAction(text, self, triggered=fn, toolTip=f"{tip} ({QKeySequence(key).toString()})")
            a.setShortcut(QKeySequence(key))
            bar.addAction(a)
        for key, fn in ((QKeySequence.Undo, self.do_undo), (QKeySequence.Redo, self.do_redo),
                        (QKeySequence("Ctrl+Shift+Z"), self.do_redo),
                        (QKeySequence("Ctrl+I"), self.add_dialog),
                        (QKeySequence("Ctrl+L"), self.show_table)):
            self.addAction(QAction(self, shortcut=key, triggered=fn))

        self.vsplit = QSplitter(Qt.Vertical)
        self.vsplit.addWidget(self.tabs)
        self.vsplit.addWidget(self.logbox)
        self.vsplit.setCollapsible(0, False)
        self.vsplit.setStretchFactor(0, 1)
        self.vsplit.setSizes([800, 90])
        central = QWidget()
        v = QVBoxLayout(central)
        v.addWidget(self.vsplit)
        self.setCentralWidget(central)
        sz = self.opts["sizes"]
        for key, sp in (("main", self.sections_tab.split), ("log", self.vsplit),
                        ("left", self.sections_tab.left)):
            if len(sz[key]) == sp.count() and all(isinstance(x, int) for x in sz[key]):
                sp.setSizes(sz[key])
        guard_wheel(self)
        self.refresh_all()
        self._title()

    # -- options
    def save_options(self):
        if not options.save(self.opts, OPTIONS):
            self.log(f"⚠ options not saved: {OPTIONS}")

    def preset(self) -> list[str]:
        r = self.opts["regions"]
        return r["presets"].get(r["preset"], [])

    def colour(self, key: str) -> str:
        c = self.opts["colours"]
        return c["stained_" + self.exp.settings.profile] if key == "stained" else c[key]

    def roi_colour(self, name: str) -> QColor:
        c = self.opts["region_colours"].get(name)
        if c:
            return QColor(c)
        pre = self.preset()
        return QColor(REGION_COLOURS[pre.index(name) % len(REGION_COLOURS)]) if name in pre \
            else default_colour(name)

    def view_style(self) -> dict:
        v = self.opts["view"]
        return {"stained": self.colour("stained"),
                "tissue": self.colour("tissue"), "excluded": self.colour("excluded"),
                "scalebar": self.colour("scalebar"), "white": v["white"], "badge": v["badge"],
                "bar_pos": v["bar_pos"], "bar_um": v["bar_um"],
                "excl_as_tissue": v["excl_as_tissue"], "lines": self.opts["lines"]}

    def apply_options(self, topic):
        """An option changed (*topic*: its key, None: all): the window follows."""
        self.sections_tab.apply_options()
        if topic in (None, "colours"):
            self._refresh_tab()

    def show_options(self):
        if self.options_window is None:
            self.options_window = options.OptionsWindow(self)
        self.options_window.open()

    def show_table(self):
        if self.table_window is None:
            self.table_window = SectionsWindow(self)
        self.table_window.open()

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
            if line:
                self.logbox.appendPlainText(line)

    def later(self, fn):
        """Run *fn* on the GUI thread (from a worker)."""
        try:
            self._bridge.call.emit(fn)
        except RuntimeError:                      # the window is gone (closing)
            pass

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
        self.show_base(self.sections_tab.current())
        self.changed()

    def do_undo(self):
        if self.undo:
            self.redo.append(json.dumps(self.exp.to_dict(maps=False), default=float))
            self._restore(self.undo.pop())

    def do_redo(self):
        if self.redo:
            self.undo.append(json.dumps(self.exp.to_dict(maps=False), default=float))
            self._restore(self.redo.pop())

    def changed(self, view=True, select=None):
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
            st.fill_regions(st._region_index() if select is None else select)
            self.show_maps(st.current())
            self._refresh_tab()
            if self.table_window is not None:
                self.table_window.refresh()

    def refresh_all(self):
        self.sections_tab.refresh()
        self._refresh_tab()
        if self.table_window is not None:
            self.table_window.refresh()

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
                    # ponytail: always white-balanced (a preview); follows no toggle
                    shown = display(rgb, wb_lut(analysis.estimate_glass(rgb),
                                                self.opts["view"]["white"]))
                    shown[(rgb.max(2) <= 5) | (rgb.min(2) >= 250)] = 60     # padding: dark
                    img = to_qimage(shown).scaled(THUMB, THUMB, Qt.KeepAspectRatio,
                                                  Qt.SmoothTransformation)
                    self.later(lambda s=s, img=img: self._thumb_ready(s, img))
                except Exception as e:
                    self.later(lambda s=s, e=e: self.log(f"⚠ {s.shown}: no thumbnail ({e})"))
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
        self.apply_options("colours")
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
            sz = self.opts["sizes"]
            sz.update(main=self.sections_tab.split.sizes(), log=self.vsplit.sizes(),
                      left=self.sections_tab.left.sizes())
            self.save_options()
            e.accept()
        else:
            e.ignore()

    # -- the slide: its overview and base (glass, outline), read once, the next ones ahead
    def _base_key(self, s, st):
        return (s.key, st.profile, st.outline_od, st.tissue_od > 0)

    def _overview(self, sc):
        z = level_for(max(sc.w, sc.h), 2400)
        return czi.read(sc, 0, 0, sc.w, sc.h, z), z

    def _read_ahead(self):
        """The sections beside the one shown, read while it is looked at."""
        while True:
            s, st = self._ahead.get()
            try:
                if s.key not in self.overviews:
                    ov = self._overview(s.scene)
                    self.later(lambda s=s, ov=ov: self.overviews.setdefault(s.key, ov))
                k = self._base_key(s, st)
                if k not in self.bases:
                    b = analysis.base(s.scene, st)
                    self.later(lambda k=k, b=b: self.bases.setdefault(k, b))
            except Exception:
                pass                                  # read again, and reported, when shown

    def _trim(self):
        for d, n in ((self.overviews, 8), (self.bases, 16)):
            while len(d) > n:
                d.pop(next(iter(d)))

    def base_of(self, s):
        st = self.exp.settings_for(s)
        k = self._base_key(s, st)
        if k not in self.bases:
            self.bases[k] = analysis.base(s.scene, st)
            self._trim()
        return self.bases[k]

    def show_base(self, s):
        """The outline again (other tissue or outline settings, another stain)."""
        if s is not None and s.scene is not None and self.sections_tab.view.sc is s.scene:
            self.sections_tab.view.set_base(*self.base_of(s))

    def show_section(self, s: model.Section, force=True):
        view = self.sections_tab.view
        if s.scene is None:
            view.clear()
            return
        if view.sc is s.scene and not force:
            self.show_maps(s)
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if s.key not in self.overviews:
                self.overviews[s.key] = self._overview(s.scene)
            rgb, z = self.overviews[s.key]
            glass, outline = self.base_of(s)
            self._trim()
        except Exception as e:
            QApplication.restoreOverrideCursor()
            self.log(f"✗ {s.shown}: not readable ({e})")
            return
        QApplication.restoreOverrideCursor()
        view.set_settings(self.exp.settings_for(s))
        view.show_section(s.scene, rgb, z, glass, outline)
        self.show_maps(s)
        self._show_label(s)
        secs = self.exp.sections
        i = secs.index(s) if s in secs else -1
        for j in (i + 1, i - 1):
            if 0 <= j < len(secs) and secs[j].scene is not None:
                self._ahead.put((secs[j], self.exp.settings_for(secs[j])))

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
        if pm is None:
            lab.setPixmap(QPixmap())
            return
        pm = pm.transformed(QTransform().rotate(self.opts["view"]["label_rotation"]),
                            Qt.SmoothTransformation)
        lab.setPixmap(pm.scaled(max(60, lab.width() - 8), max(60, lab.height() - 8),
                                Qt.KeepAspectRatio, Qt.SmoothTransformation))

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
            text = (f"{i + 1} of {n}: {jobs[i][0].shown} — {100 * done / max(total, 1):.0f} %"
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
                    self.later(lambda s=s, e=e: self.log(f"✗ {s.shown}: {e}"))
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
        self.log(f"✓ {s.shown}: {fmt(row['pct'])} % of {row['tissue_mm2']:.2f} mm² stained"
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
