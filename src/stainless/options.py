"""The options: what Stainless remembers between sessions, and the window that sets them.

One JSON file, `stainless_options.json` beside Stainless.bat, saved the moment anything
changes; whatever is missing or malformed in it keeps its default, so an old or hand-edited
file never stops the app. Topics on the left, a page each; Export… / Import… / Reset at the
foot. An experiment's own settings (the stain, the rule, the resolution) are saved with the
experiment; here are only the ones a new experiment starts from.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                               QListWidget, QMainWindow, QMessageBox, QPushButton,
                               QScrollArea, QSpinBox, QStackedWidget, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

FILE = Path(__file__).resolve().parents[2] / "stainless_options.json"   # beside Stainless.bat
GREY = "color:#8d8d8d;"
PANEL = ["Stain", "Stained when", "Resolution", "Regions", "Show", "Measure"]
SHOW = {"stained": "stained", "tissue": "not counted as tissue", "roi": "ROIs",
        "excluded": "excluded regions", "scalebar": "scale bar", "wb": "white balance (auto)"}
ARTEFACTS = {"fold": "tissue folds", "bubble": "bubbles", "debris": "debris"}
DEFAULTS = {
    "last_dir": "",
    "panel": {"collapsed": {k: k == "Resolution" for k in PANEL},
              "more_rule": False, "more_measure": False},
    "show": {"stained": True, "tissue": False, "roi": True, "excluded": True,
             "scalebar": True, "wb": True},
    "new": {"profile": "dab", "level": 1},
    "regions": {"preset": "", "presets": {"MI": ["infarct", "border zone", "remote"]}},
    "artefacts": {"factor": 1.6, "min_mm2": 0.004, "fold": True, "bubble": True,
                  "debris": True},
    "colours": {"stained_dab": "#00e676", "stained_sabg": "#ff40a0", "tissue": "#8b0000",
                "excluded": "#ff4d4d", "scalebar": "#ffffff"},
    "region_colours": {},                      # an ROI's name -> its colour
    "view": {"white": 245, "badge": True, "bar_pos": "bottom right", "bar_um": 0,
             "excl_as_tissue": False, "label_rotation": 90},
    "aliases": {},                             # a scan's name -> the alias it was given
    "sizes": {"regions": 120, "measure": 130, "main": [], "log": [], "left": []},
}
FREE = ("presets", "region_colours", "aliases")     # dicts of the user's own keys
CHOICES = {("new", "profile"): ("dab", "sabg"), ("new", "level"): (0, 1, 2),
           ("view", "bar_pos"): ("bottom right", "bottom left"),
           ("view", "label_rotation"): (0, 90, 180, 270)}


def _merge(default, got, key=""):
    """*got* where it has the default's type (any number for a number); a dict key by key,
    so a key a later version adds keeps its default."""
    if key in FREE:
        return {str(k): v for k, v in got.items()} if isinstance(got, dict) else copy.deepcopy(default)
    if isinstance(default, dict):
        got = got if isinstance(got, dict) else {}
        return {k: _merge(v, got.get(k), k) for k, v in default.items()}
    num = (int, float)
    if type(default) in num and type(got) in num:
        return type(default)(got)
    return copy.deepcopy(got if type(got) is type(default) else default)


def load(path: Path | None = None, got=None) -> dict:
    if got is None:
        try:
            got = json.loads((path or FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            got = {}
    o = _merge(DEFAULTS, got if isinstance(got, dict) else {})
    for (a, b), ok in CHOICES.items():
        if o[a][b] not in ok:
            o[a][b] = DEFAULTS[a][b]
    o["regions"]["presets"] = {k: [str(n) for n in v if str(n).strip()]
                               for k, v in o["regions"]["presets"].items() if isinstance(v, list)}
    if o["regions"]["preset"] not in o["regions"]["presets"]:
        o["regions"]["preset"] = ""
    for k in ("region_colours", "aliases"):
        o[k] = {n: v for n, v in o[k].items() if isinstance(v, str)}
    return o


def save(opts: dict, path: Path | None = None) -> bool:
    try:
        (path or FILE).write_text(json.dumps(opts, indent=1, ensure_ascii=False),
                                  encoding="utf-8")
        return True
    except OSError:
        return False


def _left(w: QWidget) -> QHBoxLayout:
    h = QHBoxLayout()
    h.addWidget(w)
    h.addStretch()
    return h


class ColorButton(QPushButton):
    """A colour swatch; a click picks another."""
    picked = Signal(str)

    def __init__(self, colour: str, tip: str = "", size=(28, 16)):
        super().__init__()
        self.setFixedSize(*size)
        self.setToolTip(tip or "click to change the colour")
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(self._pick)
        self.set(colour)

    def set(self, colour: str):
        self.colour = colour
        self.setStyleSheet(f"QPushButton{{background:{colour}; border:1px solid #6a6a6a; "
                           f"border-radius:3px;}}")

    def _pick(self):
        c = QColorDialog.getColor(QColor(self.colour), self, "Colour")
        if c.isValid():
            self.set(c.name())
            self.picked.emit(c.name())


class OptionsWindow(QMainWindow):
    """The options, by topic on the left. Saved as they change; one JSON file, which can be
    exported to another computer and imported there."""

    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.setWindowTitle("Stainless — options")
        self.resize(820, 600)
        self.topics = QListWidget()
        self.topics.setFixedWidth(160)
        self.pages = QStackedWidget()
        self.topics.currentRowChanged.connect(self.pages.setCurrentIndex)
        foot = QHBoxLayout()
        for text, tip, fn in (("Export…", "write every option to a JSON file", self._export),
                              ("Import…", "read the options from a JSON file", self._import),
                              ("Reset to defaults", "every option back to how Stainless "
                                                    "ships", self._reset)):
            foot.addWidget(QPushButton(text, toolTip=tip, clicked=fn))
        foot.addStretch(1)
        where = QLabel(f"saved as they change, in {FILE}")
        where.setStyleSheet(GREY)
        foot.addWidget(where)
        root = QWidget()
        v = QVBoxLayout(root)
        h = QHBoxLayout()
        h.addWidget(self.topics)
        h.addWidget(self.pages, 1)
        v.addLayout(h, 1)
        v.addLayout(foot)
        self.setCentralWidget(root)
        self._build()

    @property
    def o(self) -> dict:
        return self.main.opts

    def open(self, topic: str | None = None):
        self._build()
        if topic:
            hit = self.topics.findItems(topic, Qt.MatchExactly)
            if hit:
                self.topics.setCurrentItem(hit[0])
        self.show()
        self.raise_()
        self.activateWindow()

    def _set(self, path: tuple, value) -> None:
        """One option changed: saved, and the window follows."""
        d = self.o
        for k in path[:-1]:
            d = d[k]
        if d.get(path[-1]) == value:
            return
        d[path[-1]] = value
        self.main.save_options()
        self.main.apply_options(path[0])

    def _build(self) -> None:
        at = max(0, self.topics.currentRow())
        self.topics.clear()
        while self.pages.count():
            w = self.pages.widget(0)
            self.pages.removeWidget(w)
            w.deleteLater()
        for name, page in (("Side panel", self._panel_page()),
                           ("New experiment", self._new_page()),
                           ("Regions", self._regions_page()),
                           ("Artefacts", self._artefacts_page()),
                           ("View", self._view_page()),
                           ("Colours", self._colours_page()),
                           ("Sections", self._sections_page())):
            self.topics.addItem(name)
            sc = QScrollArea(widgetResizable=True)
            sc.setFrameShape(QScrollArea.NoFrame)
            sc.setWidget(page)
            self.pages.addWidget(sc)
        self.topics.setCurrentRow(at)
        from .app import guard_wheel
        guard_wheel(self.pages)

    # -- building blocks: a page is a title, a line of what it is, then labelled rows
    @staticmethod
    def _page(title, text):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(16, 4, 8, 4)
        t = QLabel(f"<b>{title}</b>")
        t.setStyleSheet("font-size:14px;")
        v.addWidget(t)
        g = QLabel(text, wordWrap=True)
        g.setStyleSheet(GREY)
        v.addWidget(g)
        f = QFormLayout()                    # one per page: its rows line up
        f.setLabelAlignment(Qt.AlignLeft)
        f.setHorizontalSpacing(18)
        f.setVerticalSpacing(7)
        v.addLayout(f)
        v.form = f
        return w, v

    @staticmethod
    def _form(v, title=None):
        """The page's form, under a heading *title*."""
        if title:
            h = QLabel(title.upper())
            h.setStyleSheet("color:#8b98a5; font-weight:600; font-size:10px; margin-top:10px;")
            v.form.addRow(h)
        return v.form

    def _tick(self, path, text, tip=""):
        d = self.o
        for k in path:
            d = d[k] if isinstance(d, dict) else d
        c = QCheckBox(text, toolTip=tip)
        c.setChecked(bool(d))
        c.toggled.connect(lambda on: self._set(path, on))
        return c

    def _choice(self, path, items):
        c = QComboBox()
        for val, text in items:
            c.addItem(text, val)
        c.setCurrentIndex(max(0, c.findData(self.o[path[0]][path[1]])))
        c.activated.connect(lambda i: self._set(path, c.itemData(i)))
        c.setFixedWidth(240)
        return c

    def _number(self, path, lo, hi, step, suffix="", decimals=None, special=""):
        val = self.o[path[0]][path[1]]
        s = QSpinBox() if isinstance(val, int) else QDoubleSpinBox(decimals=decimals or 3)
        s.setRange(lo, hi)
        s.setSingleStep(step)
        s.setValue(val)
        s.setSuffix(suffix)
        if special:
            s.setSpecialValueText(special)
        s.setKeyboardTracking(False)
        s.valueChanged.connect(lambda x: self._set(path, x))
        s.setFixedWidth(120)
        return s

    def _colour(self, path, tip=""):
        b = ColorButton(self.o[path[0]][path[1]], tip)
        b.picked.connect(lambda c: self._set(path, c))
        return b

    # -- pages
    def _panel_page(self):
        w, v = self._page("Side panel", "How the panel beside the slide starts: which blocks "
                          "are folded, and what is shown. Applies at the next start; click a "
                          "block's title to fold it meanwhile.")
        f = self._form(v, "Folded at start")
        for k in PANEL:
            f.addRow(k, self._tick(("panel", "collapsed", k), "folded"))
        f = self._form(v, "Open at start")
        f.addRow("Stained when", self._tick(("panel", "more_rule"), "more (tissue, outline)"))
        f.addRow("Measure", self._tick(("panel", "more_measure"),
                                       "more (whole section, excluded, details)"))
        f = self._form(v, "Shown at start")
        for k, text in SHOW.items():
            f.addRow(text, self._tick(("show", k), "on"))
        v.addStretch()
        return w

    def _new_page(self):
        w, v = self._page("New experiment", "What a new experiment starts with. An "
                          "experiment that is opened keeps its own.")
        f = self._form(v)
        f.addRow("stain", self._choice(("new", "profile"), [("dab", "DAB (brown), hematoxylin"),
                                                            ("sabg", "SA-β-gal (X-Gal)")]))
        f.addRow("resolution", self._choice(("new", "level"), [
            (0, "the scan's own pixels"), (1, "½ — 2 × 2 averaged (recommended)"),
            (2, "¼ — 4 × 4 averaged")]))
        v.addStretch()
        return w

    def _regions_page(self):
        w, v = self._page("Regions", "The names offered for an ROI when drawing. ROIs of one "
                          "name are one region in the results (pooled). Without a set of "
                          "names, each ROI is its own: ROI 1, ROI 2…")
        f = self._form(v)
        pre = self.o["regions"]
        c = QComboBox()
        c.addItem("none (ROI 1, ROI 2…)", "")
        for k in pre["presets"]:
            c.addItem(k, k)
        c.setCurrentIndex(max(0, c.findData(pre["preset"])))
        c.activated.connect(lambda i: self._set(("regions", "preset"), c.itemData(i)))
        c.setFixedWidth(240)
        f.addRow("names offered", c)
        f = self._form(v, "Sets of names")
        t = QTableWidget(0, 2)
        t.setHorizontalHeaderLabels(["set", "names, comma-separated"])
        t.verticalHeader().hide()
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        t.setMinimumHeight(170)
        for k, names in pre["presets"].items():
            t.insertRow(t.rowCount())
            t.setItem(t.rowCount() - 1, 0, QTableWidgetItem(k))
            t.setItem(t.rowCount() - 1, 1, QTableWidgetItem(", ".join(names)))

        def read(*_):
            sets = {}
            for r in range(t.rowCount()):
                k, n = t.item(r, 0), t.item(r, 1)
                if k and k.text().strip():
                    sets[k.text().strip()] = [x.strip() for x in (n.text() if n else "").split(",")
                                              if x.strip()]
            self.o["regions"]["presets"] = sets
            if self.o["regions"]["preset"] not in sets:
                self.o["regions"]["preset"] = ""
            self.main.save_options()
            self.main.apply_options("regions")

        t.itemChanged.connect(read)
        f.addRow(t)
        row = QHBoxLayout()
        add = QPushButton("+", fixedWidth=30, toolTip="a new set of names")
        add.clicked.connect(lambda: (t.insertRow(t.rowCount()),
                                     t.setItem(t.rowCount() - 1, 0, QTableWidgetItem("new set"))))
        rem = QPushButton("−", fixedWidth=30, toolTip="remove the selected set")
        rem.clicked.connect(lambda: (t.removeRow(t.currentRow()), read(), self._build())
                            if t.currentRow() >= 0 else None)
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch()
        f.addRow(row)
        v.addStretch()
        return w

    def _artefacts_page(self):
        w, v = self._page("Artefacts", "What Find artefacts looks for (Regions block): "
                          "tissue absorbing far more than the tissue around it and not "
                          "toward the stain — a fold is tissue lying on itself, twice as "
                          "dark. What it finds becomes excluded regions, which can be "
                          "edited or removed like any other.")
        f = self._form(v)
        f.addRow("darker than around, at least", self._number(("artefacts", "factor"), 1.1, 5,
                                                               0.1, " ×", 2))
        f.addRow("smallest piece", self._number(("artefacts", "min_mm2"), 0.0005, 1, 0.001,
                                                " mm²", 4))
        f = self._form(v, "Looked for")
        for k, text in ARTEFACTS.items():
            f.addRow(text, self._tick(("artefacts", k), "yes"))
        v.addStretch()
        return w

    def _view_page(self):
        w, v = self._page("View", "How the slide is shown. None of it changes what is "
                          "measured.")
        f = self._form(v, "White balance")
        f.addRow("the glass shown at", self._number(("view", "white"), 180, 255, 5, " / 255"))
        f = self._form(v, "Live detail")
        f.addRow("LIVE badge on the slide", self._tick(("view", "badge"), "shown",
                                                       "while zoomed in far enough for the live "
                                                       "detail"))
        f = self._form(v, "Scale bar")
        f.addRow("where", self._choice(("view", "bar_pos"), [("bottom right", "bottom right"),
                                                             ("bottom left", "bottom left")]))
        f.addRow("length", self._number(("view", "bar_um"), 0, 20000, 50, " µm", special="auto"))
        f = self._form(v, "Excluded regions")
        f.addRow("filled as", self._tick(("view", "excl_as_tissue"), "not counted as tissue",
                                         "in the colour of what is not counted as tissue"))
        v.addStretch()
        return w

    def _colours_page(self):
        w, v = self._page("Colours", "The overlays' colours. An ROI's colour is set beside "
                          "it in the Regions list, and remembered by its name.")
        f = self._form(v)
        for k, text in (("stained_dab", "stained, DAB"), ("stained_sabg", "stained, SA-β-gal"),
                        ("tissue", "not counted as tissue"), ("excluded", "excluded regions"),
                        ("scalebar", "scale bar")):
            f.addRow(text, self._colour(("colours", k)))
        names = self.o["region_colours"]
        if names:
            f = self._form(v, "ROIs")
            for n, c in sorted(names.items()):
                b = ColorButton(c)
                b.picked.connect(lambda col, n=n: (self.o["region_colours"].__setitem__(n, col),
                                                   self.main.save_options(),
                                                   self.main.apply_options("region_colours")))
                f.addRow(n, b)
            forget = QPushButton("Forget the ROIs' colours")
            forget.clicked.connect(lambda: (self.o["region_colours"].clear(),
                                            self.main.save_options(),
                                            self.main.apply_options("region_colours"),
                                            self._build()))
            f.addRow(_left(forget))
        v.addStretch()
        return w

    def _sections_page(self):
        w, v = self._page("Sections", "The slide label, and the aliases: the names a "
                          "section is shown and reported under. An alias is remembered by "
                          "the scan's name, and given again when that scan is added to "
                          "another experiment.")
        f = self._form(v)
        f.addRow("slide label, turned", self._choice(("view", "label_rotation"), [
            (0, "not turned"), (90, "90° clockwise"), (180, "180°"), (270, "90° anticlockwise")]))
        al = self.o["aliases"]
        f = self._form(v, f"Aliases remembered ({len(al)})")
        t = QTableWidget(len(al), 2)
        t.setHorizontalHeaderLabels(["scan's name", "alias"])
        t.verticalHeader().hide()
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setMinimumHeight(200)
        for i, (k, a) in enumerate(sorted(al.items())):
            t.setItem(i, 0, QTableWidgetItem(k))
            t.setItem(i, 1, QTableWidgetItem(a))
        f.addRow(t)
        forget = QPushButton("Forget them")
        forget.clicked.connect(lambda: (self.o["aliases"].clear(), self.main.save_options(),
                                        self._build()))
        f.addRow(_left(forget))
        v.addStretch()
        return w

    # -- the file
    def _export(self):
        p, _ = QFileDialog.getSaveFileName(self, "Export options", "stainless_options.json",
                                           "JSON (*.json)")
        if p and not save(self.o, Path(p)):
            QMessageBox.warning(self, "Stainless", f"Not written: {p}")

    def _import(self):
        p, _ = QFileDialog.getOpenFileName(self, "Import options", "", "JSON (*.json)")
        if p:
            self._replace(load(Path(p)))

    def _reset(self):
        if QMessageBox.question(self, "Stainless", "Every option back to its default?") \
                == QMessageBox.Yes:
            self._replace(load(got={"last_dir": self.o.get("last_dir", "")}))

    def _replace(self, new: dict):
        self.o.clear()
        self.o.update(new)
        self.main.save_options()
        self.main.apply_options(None)
        self._build()


if __name__ == "__main__":
    o = load(got={"view": {"white": 250.0, "bar_pos": "top"}, "regions": {"preset": "x"},
                  "aliases": {"a s0": "A", "b": 3}, "region_names": ["infarct"]})
    assert o["view"]["white"] == 250 and isinstance(o["view"]["white"], int)
    assert o["view"]["bar_pos"] == "bottom right"            # not a choice: the default
    assert o["regions"]["preset"] == "" and "MI" in o["regions"]["presets"]
    assert o["aliases"] == {"a s0": "A"} and "region_names" not in o
    assert load(got=[1, 2]) == load(got={})
    print("options self-check OK")
