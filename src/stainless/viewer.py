"""The slide view: a section's overview, what was measured over it, its regions. Zoomed in
past the overview, the pixels at full detail with what the current settings call stained,
redrawn as the threshold moves ("live").

Regions: in draw mode, clicks put the nodes of a polygon (double-click, Enter or a click on
the first node closes it), a drag traces freehand; right-click or Esc cancels. A selected
region shows its nodes: drag one to move it, click its line to add one (on a freehand trace,
grabbing the line puts two nodes beside it, so the drag bends that stretch only), Del
removes the node clicked last.
"""

from __future__ import annotations

import math
import queue
import threading
import zlib

import cv2
import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen,
                           QPixmap, QPolygonF, QTransform)
from PySide6.QtWidgets import (QGraphicsItem, QGraphicsPathItem, QGraphicsPixmapItem,
                               QGraphicsRectItem, QGraphicsScene, QGraphicsSimpleTextItem,
                               QGraphicsView, QLabel, QWidget)

from . import analysis, czi, model, options

OVERVIEW_EDGE = 2400            # px: the overview's long side, at least
DETAIL_MAX_PX = 8_000_000       # px read for the detail, at most
LIVE_FROM = 1.4                 # the detail is read past this zoom of the overview
REGION_COLOURS = ["#ffb000", "#4aa3ff", "#c77dff", "#7ee081", "#ff7f50", "#00d1c1"]
PAD = (30, 30, 30)              # the mosaic's padding, in the view's background colour
NODE_PX, HIT_PX = 4, 7          # a node's half-size, and how near a click is a hit (screen px)
_C = options.DEFAULTS["colours"]
STYLE = {"stained": _C["stained_dab"], "tissue": _C["tissue"], "excluded": _C["excluded"],
         "scalebar": _C["scalebar"], "white": 245, "badge": True, "bar_pos": "bottom right",
         "bar_um": 0, "excl_as_tissue": False, "lines": options.DEFAULTS["lines"]}
PEN = {"solid": Qt.SolidLine, "dash": Qt.DashLine, "dot": Qt.DotLine,
       "dash-dot": Qt.DashDotLine}
FULL = 235                      # the stained overlay's opacity at its densest, for alpha 255


def level_for(edge_px: int, want: int) -> float:
    """The pyramid level (1, ½, ¼ …) giving at least *want* px on an *edge_px* side."""
    z = 1.0
    while edge_px * z / 2 >= want:
        z /= 2
    return z


def to_qimage(a: np.ndarray) -> QImage:
    a = np.ascontiguousarray(a)
    h, w = a.shape[:2]
    fmt = QImage.Format_RGBA8888 if a.shape[2] == 4 else QImage.Format_RGB888
    return QImage(a.data, w, h, a.strides[0], fmt).copy()


def wb_lut(glass, white: float) -> np.ndarray:
    """1 × 256 × 3 table taking the glass to *white*: the slide's white balance."""
    g = white / np.maximum(np.asarray(glass, np.float32), 1.0)
    return np.clip(np.arange(256, dtype=np.float32)[:, None] * g, 0, 255
                   ).astype(np.uint8).reshape(1, 256, 3)


def display(rgb: np.ndarray, lut: np.ndarray | None) -> np.ndarray:
    """What is shown of a read: white-balanced (with *lut*), its padding in the view's
    background colour."""
    pad = (rgb.max(2) <= 5) | (rgb.min(2) >= 250)
    out = cv2.LUT(rgb, lut) if lut is not None else rgb.copy()
    out[pad] = PAD
    return out


def default_colour(name: str) -> QColor:
    return QColor(REGION_COLOURS[zlib.crc32(name.encode()) % len(REGION_COLOURS)])


def nice_um(um: float) -> float:
    """The largest 1, 2, 5 × 10ⁿ µm not above *um*."""
    if um <= 0:
        return 0.0
    e = 10 ** math.floor(math.log10(um))
    return max(m * e for m in (1, 2, 5) if m * e <= um)


class _Bridge(QObject):
    done = Signal(object)


class ScaleBar(QWidget):
    """A bar of a round length on the view, its length written above it."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.px, self.text, self.colour = 0, "", QColor("#ffffff")
        self.resize(220, 34)

    def paintEvent(self, e):
        if self.px <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        x0 = (self.width() - self.px) / 2
        shadow = QColor(0, 0, 0, 150)
        for c, d in ((shadow, 1), (self.colour, 0)):
            p.fillRect(QRectF(x0 + d, 22 + d, self.px, 5), c)
            p.setPen(c)
            p.drawText(QRectF(d, d, self.width(), 20), Qt.AlignHCenter | Qt.AlignBottom,
                       self.text)


class SlideView(QGraphicsView):
    region_drawn = Signal(list, object)    # points (full-res scene px), nodes (None: all)
    region_clicked = Signal(int)           # index into the regions, -1: none
    region_edited = Signal(int, dict)      # index, the region as edited
    draw_ended = Signal()                  # right-click or Esc: out of draw mode
    detail_changed = Signal(str)           # what the detail shows, for the status line
    live_changed = Signal(bool)            # the live detail is on screen, or not any more

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QColor("#1e1e1e"))
        self.setFrameShape(QGraphicsView.NoFrame)
        self.sc: czi.Scene | None = None
        self.ov_zoom = 1.0
        self.items = {k: None for k in ("base", "nottissue", "map", "detail", "detail_ov")}
        self.region_items: list = []
        self.layers = {"stained": True, "tissue": False, "roi": True, "excluded": True,
                       "scalebar": True, "wb": True}
        self.style = dict(STYLE)
        self.roi_colour = default_colour
        self.settings = analysis.Settings()
        self.glass = None
        self.outline = None                 # bool, OUTLINE_UM per px over the scene
        self._ov = None                     # the overview: rgb, s, scanned, outline on it
        self.maps, self.cell_px = {}, 0
        self.regions, self.selected, self.labels = [], -1, {}
        self.node = None                    # the selected region's node clicked last
        self.drawing = False
        self._pts, self._nodes, self._trace, self._trace_marks = [], [], None, []
        self._held = self._dragged = self._fresh = False
        self._press = None
        self._edit = self._drag = None      # the region being edited, its node dragged
        self._sel_items: list = []
        self._detail = None                 # (x, y, z, rgb, s, d, scanned)
        self.live = False
        self._gen = 0
        self._jobs: queue.Queue = queue.Queue()
        self._bridge = _Bridge()
        self._bridge.done.connect(self._detail_ready)
        threading.Thread(target=self._worker, daemon=True).start()
        self._timer = QTimer(self, singleShot=True, interval=160, timeout=self._want_detail)
        self.setMouseTracking(True)
        self.badge = QLabel(self.viewport())
        self.badge.setStyleSheet("background:rgba(20,20,20,190); color:#ffffff; "
                                 "border:1px solid #4aa3ff; border-radius:9px; "
                                 "padding:2px 9px; font-weight:600;")
        self.badge.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.badge.hide()
        self.bar = ScaleBar(self.viewport())
        self.bar.hide()

    # -- content
    def clear(self):
        self.scene().clear()
        self.items = dict.fromkeys(self.items)
        self.region_items, self._sel_items, self._trace_marks = [], [], []
        self._trace = None
        self.sc, self._detail, self._ov = None, None, None
        self._set_live(False)
        self.bar.hide()

    def show_section(self, sc: czi.Scene, overview: np.ndarray, ov_zoom: float, glass,
                     outline: np.ndarray):
        self.clear()
        self.sc, self.ov_zoom = sc, ov_zoom
        self.scene().setSceneRect(QRectF(0, 0, sc.w, sc.h))
        self._ov = {"rgb": overview}
        self.set_base(glass, outline, redraw=False)
        self._draw_regions()
        self.fit()

    def set_base(self, glass, outline, redraw=True):
        """The section's glass and outline (other settings, or measured)."""
        self.glass = np.asarray(glass, np.float32)
        self.outline = outline
        if self._ov is not None:
            rgb = self._ov["rgb"]
            s, _, scanned = analysis.measure(rgb, analysis.od_lut(self.glass), (0, 0, 0))
            on = cv2.resize(outline.astype(np.uint8), (rgb.shape[1], rgb.shape[0]),
                            interpolation=cv2.INTER_NEAREST)
            self._ov.update(s=s, scanned=scanned > 0, inside=on > 0)
            self._draw_base()
            self._draw_nottissue()
            self._draw_map()
        if redraw:
            self._detail = None
            self._put("detail", None)
            self._put("detail_ov", None)
            self._want_detail()

    def set_maps(self, maps: dict, cell_px: int):
        self.maps, self.cell_px = maps or {}, cell_px
        self._draw_map()

    def set_regions(self, regions: list, selected: int = -1, labels: dict | None = None):
        if selected != self.selected or regions is not self.regions:
            self.node = None
        self.regions, self.selected, self.labels = regions, selected, labels or {}
        self._edit = self._drag = None
        self._draw_regions()

    def set_settings(self, st: analysis.Settings):
        new_read = (st.level != self.settings.level or st.profile != self.settings.profile)
        tissue = st.tissue_od != self.settings.tissue_od
        self.settings = st
        if tissue:
            self._draw_nottissue()
        if new_read:                                        # other pixels, or another hue
            self._detail = None
            self._put("detail_ov", None)
            self._want_detail()
        else:
            self._draw_detail_overlay()

    def set_layer(self, name: str, on: bool):
        self.layers[name] = on
        if name == "wb":
            self._draw_base()
            self._redraw_detail()
        elif name == "scalebar":
            self._place_overlays()
        elif name in ("roi", "excluded"):
            self._draw_regions()
        else:
            self._draw_map()
            self._draw_nottissue()
            self._draw_detail_overlay()
        self._place_overlays()

    def set_style(self, style: dict, roi_colour=None):
        self.style.update(style)
        if roi_colour is not None:
            self.roi_colour = roi_colour
        self._draw_base()
        self._redraw_detail()
        self._draw_nottissue()
        self._draw_map()
        self._draw_regions()
        self._place_overlays()

    def fit(self):
        if self.sc is not None:
            self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)
            self._want_detail()
            self._place_overlays()

    # -- layers
    def _put(self, key, img: np.ndarray | None, x=0.0, y=0.0, scale=1.0, z=0.0):
        old = self.items.get(key)
        if old is not None:
            self.scene().removeItem(old)
            self.items[key] = None
        if img is None:
            return
        it = QGraphicsPixmapItem(QPixmap.fromImage(to_qimage(img)))
        it.setPos(x, y)
        it.setScale(scale)
        it.setZValue(z)
        if key in ("base", "detail"):
            it.setTransformationMode(Qt.SmoothTransformation)
        self.scene().addItem(it)
        self.items[key] = it

    def _lut(self):
        if not self.layers["wb"] or self.glass is None:
            return None
        return wb_lut(self.glass, self.style["white"])

    def _draw_base(self):
        if self._ov is not None:
            self._put("base", display(self._ov["rgb"], self._lut()), scale=1 / self.ov_zoom)

    @staticmethod
    def _rgba(mask: np.ndarray, colour: str) -> np.ndarray:
        out = np.zeros(mask.shape + (4,), np.uint8)
        out[mask] = QColor(colour).getRgb()
        return out

    def _draw_nottissue(self):
        """Not counted as tissue, on the overview: scanned, but outside the outline or (with
        a counterstain) not absorbing enough."""
        ov = self._ov
        if ov is None or "s" not in ov or not self.layers["tissue"]:
            self._put("nottissue", None)
            return
        out = ~ov["inside"] | (ov["s"] < self.settings.tissue_od)
        self._put("nottissue", self._rgba(out & ov["scanned"], self.style["tissue"]),
                  scale=1 / self.ov_zoom, z=1)

    def _draw_map(self):
        st = self.maps.get("stained")
        if self.sc is None or st is None or not self.layers["stained"]:
            self._put("map", None)
            return
        rgba = np.zeros(st.shape + (4,), np.uint8)
        on = st > 0
        *rgb, a = QColor(self.style["stained"]).getRgb()
        rgba[on, :3] = rgb
        rgba[on, 3] = np.clip(110 + st[on].astype(np.int32) * 2, 0, FULL) * a // 255
        self._put("map", rgba, scale=self.cell_px, z=1.5)

    def _redraw_detail(self):
        d = self._detail
        if d is not None:
            x, y, z, rgb = d[:4]
            self._put("detail", display(rgb, self._lut()), x, y, 1 / z, z=2)

    def _draw_detail_overlay(self):
        d = self._detail
        if d is None or self.sc is None:
            self._put("detail_ov", None)
            return
        x, y, z, rgb, s, dd, scanned = d
        tis, pos = analysis.classify(s, dd, scanned, self.settings)
        rgba = np.zeros(s.shape + (4,), np.uint8)
        if self.layers["tissue"]:
            pad = (rgb.max(2) <= 5) | (rgb.min(2) >= 250)
            rgba[(tis == 0) & ~pad] = QColor(self.style["tissue"]).getRgb()
        if self.layers["stained"]:
            *c, a = QColor(self.style["stained"]).getRgb()
            rgba[pos > 0] = (*c, 200 * a // 255)
        self._put("detail_ov", rgba, x, y, 1 / z, z=3)
        n = cv2.countNonZero(tis)
        self.detail_changed.emit(
            f"in view, at {self.sc.px_um / z:.2f} µm/px: {100 * cv2.countNonZero(pos) / n:.2f} % "
            f"of {n * (self.sc.px_um / z / 1000) ** 2:.3f} mm² tissue stained" if n else
            "in view: no tissue")

    # -- overlays on the viewport: the LIVE badge, the scale bar
    def _set_live(self, on: bool):
        if on != self.live:
            self.live = on
            self.live_changed.emit(on)
        self._place_overlays()

    def _place_overlays(self):
        show = self.live and self.layers["stained"] and self.style["badge"] and self.sc
        if show:
            z = self._detail[2] if self._detail else 0.5 ** self.settings.level
            self.badge.setText(f"● LIVE   stained at {self.sc.px_um / z:.2f} µm/px")
            self.badge.adjustSize()
            self.badge.move(10, 10)
        self.badge.setVisible(bool(show))
        if self.sc is None or not self.layers["scalebar"]:
            self.bar.hide()
            return
        um_px = self.sc.px_um / max(self.transform().m11(), 1e-9)   # µm per screen px
        want = self.style["bar_um"] or nice_um(120 * um_px)
        px = want / um_px
        if px > self.viewport().width() * 0.6 or px < 4:
            want = nice_um(120 * um_px)
            px = want / um_px
        b = self.bar
        b.px, b.colour = px, QColor(self.style["scalebar"])
        b.text = f"{want / 1000:g} mm" if want >= 1000 else f"{want:g} µm"
        b.resize(int(px) + 40, 34)
        vw, vh = self.viewport().width(), self.viewport().height()
        x = 14 if self.style["bar_pos"].endswith("left") else vw - b.width() - 14
        b.move(x, vh - b.height() - 10)
        b.show()
        b.update()

    # -- regions
    def _colour(self, r: dict) -> QColor:
        return (QColor(self.style["excluded"]) if r["kind"] == "exclude"
                else self.roi_colour(r["name"]))

    def _shown(self, r: dict) -> bool:
        return self.layers["excluded" if r["kind"] == "exclude" else "roi"]

    @staticmethod
    def _path(points, closed=True) -> QPainterPath:
        path = QPainterPath()
        path.setFillRule(Qt.WindingFill)        # a loop dragged over the outline: no hole
        path.addPolygon(QPolygonF([QPointF(*p) for p in points]))
        if closed:
            path.closeSubpath()
        return path

    def _pen(self, key: str, colour: QColor | None = None) -> QPen:
        """A line as Options › Colours & lines has it."""
        ln = self.style["lines"][key]
        pen = QPen(QColor(colour or ln["colour"]), ln["width"])
        pen.setStyle(PEN[ln["style"]])
        pen.setCosmetic(True)
        return pen

    def _region_item(self, r, i):
        c = self._colour(r)
        sel = i == self.selected
        key = "selected" if sel else "excluded" if r["kind"] == "exclude" else "roi"
        item = QGraphicsPathItem(self._path(r["points"]))
        item.setPen(self._pen(key, None if sel else c))
        if r["kind"] == "exclude" and self.style["excl_as_tissue"]:
            fill = QColor(self.style["tissue"])
        else:
            fill = QColor(c)
            fill.setAlphaF(c.alphaF() * self.style["lines"][key]["fill"] / 100)
        item.setBrush(QBrush(fill))
        item.setZValue(5 + sel)
        return item

    def _label(self, text, x, y, colour):
        """A name tag above (x, y): white on dark, edged in the region's colour, readable
        on a white-balanced slide as on a dark one."""
        t = QGraphicsSimpleTextItem(text)
        t.setBrush(QColor("#ffffff"))
        f = QFont(t.font())
        f.setPointSize(9)
        f.setBold(True)
        t.setFont(f)
        br = t.boundingRect()
        tag = QGraphicsRectItem(br.adjusted(-5, -1, 5, 1))
        tag.setBrush(QColor(20, 20, 20, 190))
        pen = QPen(colour, 1)
        pen.setCosmetic(True)
        tag.setPen(pen)
        t.setParentItem(tag)
        tag.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        tag.setPos(x, y)
        tag.setTransform(QTransform.fromTranslate(5, -br.height() - 6))   # above, screen px
        tag.setZValue(7)
        return tag

    def _handle(self, x, y, colour, on=False):
        h = QGraphicsRectItem(-NODE_PX - on, -NODE_PX - on, 2 * (NODE_PX + on), 2 * (NODE_PX + on))
        h.setFlag(QGraphicsItem.ItemIgnoresTransformations)
        h.setPos(x, y)
        pen = QPen(QColor("#ffffff"), 1)
        pen.setCosmetic(True)
        h.setPen(pen)
        h.setBrush(QBrush(QColor("#ffffff") if on else QColor(colour.rgb())))   # opaque
        h.setZValue(9)
        return h

    def _draw_regions(self):
        for it in self.region_items + self._sel_items:
            self.scene().removeItem(it)
        self.region_items, self._sel_items = [], []
        if self.sc is None:
            return
        for i, r in enumerate(self.regions):
            if not self._shown(r) or i == self.selected:
                continue
            self._add(self._region_item(r, i))
            if r["kind"] == "roi":
                self._add(self._label_for(r, i))
        self._draw_selected()

    def _label_for(self, r, i):
        p = np.asarray(r["points"])
        txt = r["name"] + (f"  {self.labels[i]}" if i in self.labels else "")
        return self._label(txt, p[:, 0].min(), p[:, 1].min(), self._colour(r))

    def _add(self, it, sel=False):
        self.scene().addItem(it)
        (self._sel_items if sel else self.region_items).append(it)

    def _draw_selected(self):
        for it in self._sel_items:
            self.scene().removeItem(it)
        self._sel_items = []
        i = self.selected
        if not (0 <= i < len(self.regions)) or not self._shown(self.regions[i]):
            return
        r = self._edit if self._edit is not None else self.regions[i]
        self._add(self._region_item(r, i), True)
        self._add(self._label_for(r, i), True)
        c = self._colour(r)
        for j, p in enumerate(r["points"]):
            if model.is_node(r, j):
                self._add(self._handle(*p, c, j == self.node), True)

    def _selected_region(self):
        i = self.selected
        if 0 <= i < len(self.regions) and self._shown(self.regions[i]):
            return self.regions[i]
        return None

    def _hit_node(self, r, pos) -> int | None:
        best, at = HIT_PX + 1, None
        for j, p in enumerate(r["points"]):
            if model.is_node(r, j):
                q = self.mapFromScene(QPointF(*p))
                d = math.hypot(q.x() - pos.x(), q.y() - pos.y())
                if d < best:
                    best, at = d, j
        return at

    def _hit_edge(self, r, p: QPointF) -> int | None:
        a = np.asarray(r["points"], np.float64)
        b = np.roll(a, -1, axis=0)
        ab = b - a
        t = np.clip(((p.x() - a[:, 0]) * ab[:, 0] + (p.y() - a[:, 1]) * ab[:, 1]) /
                    np.maximum((ab ** 2).sum(1), 1e-9), 0, 1)
        d = np.hypot(a[:, 0] + t * ab[:, 0] - p.x(), a[:, 1] + t * ab[:, 1] - p.y())
        j = int(d.argmin())
        return j if d[j] * self.transform().m11() <= HIT_PX else None

    def _region_at(self, p: QPointF) -> int:
        hit = -1
        for i, r in enumerate(self.regions):
            if self._shown(r) and (self._path(r["points"]).contains(p) or
                                   self._hit_edge(r, p) is not None):
                hit = i
        return hit

    def delete_node(self) -> bool:
        """Del on the node clicked last: out of its region."""
        r = self._selected_region()
        if r is None or self.node is None or self.node >= len(r["points"]):
            return False
        e = {**r, "points": [list(q) for q in r["points"]]}
        if "nodes" in r:
            e["nodes"] = list(r["nodes"])
        if not model.delete_node(e, self.node):
            return False
        self.node = None
        self.region_edited.emit(self.selected, e)
        return True

    # -- detail, read and measured off the GUI thread (one thread: its reader stays open)
    def _want_detail(self):
        if self.sc is None:
            return
        vs = self.transform().m11()                        # screen px per full-res px
        if vs <= self.ov_zoom * LIVE_FROM:
            self._detail = None
            self._put("detail", None)
            self._put("detail_ov", None)
            self.detail_changed.emit("")
            self._set_live(False)
            return
        r = self.mapToScene(self.viewport().rect()).boundingRect() & self.sceneRect()
        if r.isEmpty():
            return
        z = 0.5 ** self.settings.level                     # never finer than what is measured
        while z / 2 >= vs:
            z /= 2
        while r.width() * r.height() * z * z > DETAIL_MAX_PX and z > self.ov_zoom:
            z /= 2
        x, y = int(r.left()), int(r.top())
        w, h = int(r.width()) + 2, int(r.height()) + 2
        self._gen += 1
        self._jobs.put((self._gen, self.sc, x, y, min(w, self.sc.w - x), min(h, self.sc.h - y),
                        z, self.glass, analysis.PROFILES[self.settings.profile]["w"],
                        self.outline, self._lut()))

    def _worker(self):
        while True:
            job = self._jobs.get()
            while not self._jobs.empty():                  # only the latest view counts
                job = self._jobs.get()
            gen, sc, x, y, w, h, z, glass, wv, outline, lut = job
            try:
                rgb = czi.read(sc, x, y, w, h, z)
                s, d, scanned = analysis.measure(rgb, analysis.od_lut(glass), wv)
                if outline is not None:                    # the section's outline, as measured
                    oh, ow = outline.shape
                    rows = ((y + (np.arange(rgb.shape[0]) + 0.5) * h / rgb.shape[0])
                            * oh / sc.h).astype(int).clip(0, oh - 1)
                    cols = ((x + (np.arange(rgb.shape[1]) + 0.5) * w / rgb.shape[1])
                            * ow / sc.w).astype(int).clip(0, ow - 1)
                    scanned &= outline[np.ix_(rows, cols)].astype(np.uint8) * 255
                self._bridge.done.emit((gen, sc, x, y, z, rgb, s, d, scanned,
                                        display(rgb, lut)))
            except Exception as e:                          # a view that can't be read
                self._bridge.done.emit((gen, sc, None, str(e)))

    def _detail_ready(self, res):
        if res[0] != self._gen or res[1] is not self.sc:
            return
        if res[2] is None:
            self.detail_changed.emit(f"⚠ detail not read: {res[3]}")
            return
        _, _, x, y, z, rgb, s, d, scanned, shown = res
        self._detail = (x, y, z, rgb, s, d, scanned)
        self._put("detail", shown, x, y, 1 / z, z=2)
        self._draw_detail_overlay()
        self._set_live(True)

    # -- interaction
    def wheelEvent(self, e):
        if self.sc is None:
            return
        f = 1.25 ** (e.angleDelta().y() / 120)
        s = self.transform().m11() * f
        fit = min(self.viewport().width() / self.sc.w, self.viewport().height() / self.sc.h)
        if s < fit * 0.5 or s > 4:
            return
        self.scale(f, f)
        self._place_overlays()
        self._timer.start()

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self._timer.start()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_overlays()
        self._timer.start()

    def set_drawing(self, on: bool):
        self.drawing = on
        self.setDragMode(QGraphicsView.NoDrag if on else QGraphicsView.ScrollHandDrag)
        self.viewport().setCursor(Qt.CrossCursor if on else Qt.OpenHandCursor)
        self._cancel_trace()

    def _cancel_trace(self):
        for it in ([self._trace] if self._trace is not None else []) + self._trace_marks:
            self.scene().removeItem(it)
        self._trace, self._trace_marks, self._pts, self._nodes = None, [], [], []
        self._held = self._dragged = False

    def _scene_pt(self, pos):
        p = self.mapToScene(pos.toPoint())
        return (round(min(max(p.x(), 0), self.sc.w), 1), round(min(max(p.y(), 0), self.sc.h), 1))

    def _draw_trace(self, cursor=None):
        if self._trace is None:
            self._trace = QGraphicsPathItem()
            self._trace.setPen(self._pen("trace"))
            self._trace.setZValue(10)
            self.scene().addItem(self._trace)
        self._trace.setPath(self._path(self._pts + ([cursor] if cursor else []), closed=False))

    def _mark_trace(self):
        for it in self._trace_marks:
            self.scene().removeItem(it)
        self._trace_marks = [self._handle(*self._pts[j], QColor("#2f6ea5"), j == 0)
                             for j in self._nodes]
        for it in self._trace_marks:
            self.scene().addItem(it)

    def _finish_trace(self):
        pts, nodes = self._pts, self._nodes
        self._cancel_trace()
        if len(pts) < 3:
            return
        a = np.asarray(pts)
        area = 0.5 * abs(np.dot(a[:, 0], np.roll(a[:, 1], 1)) - np.dot(a[:, 1], np.roll(a[:, 0], 1)))
        if area * self.transform().m11() ** 2 > 100:                  # not a slip
            self.region_drawn.emit([list(p) for p in pts],
                                   None if len(nodes) == len(pts) else nodes)

    def _near_first(self, pos) -> bool:
        if len(self._pts) < 3:
            return False
        q = self.mapFromScene(QPointF(*self._pts[0]))
        return math.hypot(q.x() - pos.x(), q.y() - pos.y()) <= HIT_PX + 2

    def mousePressEvent(self, e):
        self._press = e.position()
        if self.sc is None:
            return super().mousePressEvent(e)
        if e.button() == Qt.RightButton and self.drawing:
            self._cancel_trace()
            self.draw_ended.emit()
            return
        if e.button() != Qt.LeftButton:
            return super().mousePressEvent(e)
        p = self._scene_pt(e.position())
        if self.drawing:
            if self._near_first(e.position()):
                self._finish_trace()
                return
            self._fresh = not self._pts
            self._pts.append(p)
            self._nodes.append(len(self._pts) - 1)
            self._held, self._dragged = True, False
            self._draw_trace()
            self._mark_trace()
            return
        r = self._selected_region()
        if r is not None:
            j = self._hit_node(r, e.position())
            edge = None if j is not None else self._hit_edge(r, QPointF(*p))
            if j is not None or edge is not None:
                self._edit = {**r, "points": [list(q) for q in r["points"]]}
                if "nodes" in r:
                    self._edit["nodes"] = list(r["nodes"])
                if j is None:                             # on the line: a node there
                    j = (model.grab(self._edit, edge, p, 30 / self.transform().m11())
                         if "nodes" in r else model.insert_node(self._edit, edge, p))
                    self._edit["_new"] = True
                self.node = self._drag = j
                self._draw_selected()
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.sc is None:
            return super().mouseMoveEvent(e)
        if self._drag is not None:
            self._edit["points"][self._drag] = list(self._scene_pt(e.position()))
            self._edit["_moved"] = True
            self._draw_selected()
            return
        if self.drawing:
            p = self._scene_pt(e.position())
            if self._held and self._pts:
                if (e.position() - self._press).manhattanLength() > 4:
                    self._dragged = True
                last = self.mapFromScene(QPointF(*self._pts[-1]))
                if self._dragged and math.hypot(last.x() - e.position().x(),
                                                last.y() - e.position().y()) >= 3:
                    self._pts.append(p)                     # freehand: a point, not a node
                self._draw_trace()
            elif self._pts:
                self._draw_trace(p)                         # the next edge, to the cursor
            return
        if not e.buttons():
            r = self._selected_region()
            cur = Qt.OpenHandCursor
            if r is not None:
                if self._hit_node(r, e.position()) is not None:
                    cur = Qt.SizeAllCursor
                elif self._hit_edge(r, self.mapToScene(e.position().toPoint())) is not None:
                    cur = Qt.PointingHandCursor
            self.viewport().setCursor(cur)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self.sc is None:
            return super().mouseReleaseEvent(e)
        if self._drag is not None:
            r = self._edit
            self._drag = None
            if r.pop("_new", False) | r.pop("_moved", False):
                self.region_edited.emit(self.selected, r)
            else:
                self._edit = None
                self._draw_selected()
            return
        if self.drawing:
            if e.button() == Qt.LeftButton and self._held:
                self._held = False
                if self._dragged and self._fresh:            # traced in one go: done
                    self._nodes = []
                    self._finish_trace()
            return
        super().mouseReleaseEvent(e)
        if (self._press is not None and e.button() == Qt.LeftButton and
                (e.position() - self._press).manhattanLength() < 4):
            hit = self._region_at(self.mapToScene(e.position().toPoint()))
            if hit != self.selected or hit < 0:
                self.node = None
            self.region_clicked.emit(hit)
        self.viewport().setCursor(Qt.CrossCursor if self.drawing else Qt.OpenHandCursor)

    def mouseDoubleClickEvent(self, e):
        if self.drawing and e.button() == Qt.LeftButton:
            self._finish_trace()
            return
        super().mouseDoubleClickEvent(e)

    def keyPressEvent(self, e):
        if self.drawing:
            if e.key() == Qt.Key_Escape:
                self._cancel_trace()
                self.draw_ended.emit()
                return
            if e.key() in (Qt.Key_Return, Qt.Key_Enter):
                self._finish_trace()
                return
            if e.key() == Qt.Key_Backspace and self._nodes:   # the last node, and after it
                last = self._nodes.pop()
                del self._pts[last:]
                if not self._pts:
                    self._cancel_trace()
                else:
                    self._draw_trace()
                    self._mark_trace()
                return
        super().keyPressEvent(e)
