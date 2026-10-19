"""The slide view: a section's overview, what was measured over it, its regions. Zoomed in
past the overview, the pixels at full detail with what the current settings call stained,
redrawn as the threshold moves.
"""

from __future__ import annotations

import queue
import threading

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QImage, QPainter, QPainterPath, QPen, QPixmap,
                           QPolygonF)
from PySide6.QtWidgets import (QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsScene,
                               QGraphicsSimpleTextItem, QGraphicsView)

from . import analysis, czi

OVERVIEW_EDGE = 2400            # px: the overview's long side, at least
DETAIL_MAX_PX = 8_000_000       # px read for the detail, at most
REGION_COLOURS = ["#ffb000", "#4aa3ff", "#c77dff", "#7ee081", "#ff7f50", "#00d1c1"]
EXCLUDE = "#ff4d4d"


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


def unpadded(rgb: np.ndarray) -> np.ndarray:
    """The mosaic's padding (pure black or white where nothing was scanned) in the view's
    background colour."""
    out = rgb.copy()
    out[(rgb.max(2) <= 5) | (rgb.min(2) >= 250)] = (30, 30, 30)
    return out


def region_colour(name: str, i: int) -> QColor:
    return QColor(EXCLUDE if name.strip().lower() == "exclude" else
                  REGION_COLOURS[i % len(REGION_COLOURS)])


class _Bridge(QObject):
    done = Signal(object)


class SlideView(QGraphicsView):
    region_drawn = Signal(list)            # [[x, y], …] full-res scene px
    region_clicked = Signal(int)           # index into the regions, -1: none
    detail_changed = Signal(str)           # what the detail shows, for the status line

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
        self.items = {k: None for k in ("base", "map", "detail", "detail_ov")}
        self.region_items: list = []
        self.layers = {"stained": True, "tissue": False, "regions": True}
        self.settings = analysis.Settings()
        self.glass = None
        self.maps, self.cell_px = {}, 0
        self.regions, self.selected, self.labels = [], -1, {}
        self.drawing = False
        self._pts, self._trace, self._press = [], None, None
        self._detail = None                # (x, y, z, rgb, s, hue, scanned)
        self._gen = 0
        self._jobs: queue.Queue = queue.Queue()
        self._bridge = _Bridge()
        self._bridge.done.connect(self._detail_ready)
        threading.Thread(target=self._worker, daemon=True).start()
        self._timer = QTimer(self, singleShot=True, interval=160, timeout=self._want_detail)
        self.setMouseTracking(True)

    # -- content
    def clear(self):
        self.scene().clear()
        self.items = dict.fromkeys(self.items)
        self.region_items, self.sc, self._detail = [], None, None

    def show_section(self, sc: czi.Scene, overview: np.ndarray, ov_zoom: float, glass):
        self.clear()
        self.sc, self.ov_zoom, self.glass = sc, ov_zoom, glass
        self.scene().setSceneRect(QRectF(0, 0, sc.w, sc.h))
        it = QGraphicsPixmapItem(QPixmap.fromImage(to_qimage(unpadded(overview))))
        it.setTransformationMode(Qt.SmoothTransformation)
        it.setScale(1 / ov_zoom)
        self.scene().addItem(it)
        self.items["base"] = it
        self._draw_map()
        self._draw_regions()
        self.fit()

    def set_maps(self, maps: dict, cell_px: int):
        self.maps, self.cell_px = maps or {}, cell_px
        self._draw_map()

    def set_regions(self, regions: list, selected: int = -1, labels: dict | None = None):
        self.regions, self.selected, self.labels = regions, selected, labels or {}
        self._draw_regions()

    def set_settings(self, st: analysis.Settings, glass=None):
        new_read = (st.level != self.settings.level or st.profile != self.settings.profile)
        self.settings = st
        if glass is not None:
            self.glass = glass
        self._draw_map()
        if new_read:                                        # other pixels, or another hue
            self._detail = None
            self._put("detail_ov", None)
            self._want_detail()
        else:
            self._draw_detail_overlay()

    def set_layer(self, name: str, on: bool):
        self.layers[name] = on
        self._draw_map()
        self._draw_detail_overlay()
        self._draw_regions()

    def fit(self):
        if self.sc is not None:
            self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)
            self._want_detail()

    # -- layers
    def _colour(self):
        return analysis.PROFILES[self.settings.profile]["colour"]

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
        if key == "detail":
            it.setTransformationMode(Qt.SmoothTransformation)
        self.scene().addItem(it)
        self.items[key] = it

    def _draw_map(self):
        st, ti = self.maps.get("stained"), self.maps.get("tissue")
        if self.sc is None or st is None or not (self.layers["stained"] or self.layers["tissue"]):
            self._put("map", None)
            return
        rgba = np.zeros(st.shape + (4,), np.uint8)
        if self.layers["tissue"] and ti is not None:
            rgba[ti < 64] = (90, 90, 90, 150)        # not counted as tissue
        if self.layers["stained"]:
            on = st > 0
            rgba[on, :3] = self._colour()
            rgba[on, 3] = np.clip(110 + st[on].astype(np.int32) * 2, 0, 235)
        self._put("map", rgba, scale=self.cell_px, z=1)

    def _draw_detail_overlay(self):
        d = self._detail
        if d is None or self.sc is None:
            self._put("detail_ov", None)
            return
        x, y, z, rgb, s, hue, scanned = d
        tis, pos = analysis.classify(s, hue, scanned, self.settings)
        rgba = np.zeros(s.shape + (4,), np.uint8)
        if self.layers["tissue"]:
            rgba[~tis] = (90, 90, 90, 150)
        if self.layers["stained"]:
            rgba[pos] = (*self._colour(), 200)
        self._put("detail_ov", rgba, x, y, 1 / z, z=3)
        n = int(tis.sum())
        self.detail_changed.emit(
            f"in view, at {self.sc.px_um / z:.2f} µm/px: {100 * pos.sum() / n:.2f} % of "
            f"{n * (self.sc.px_um / z / 1000) ** 2:.3f} mm² tissue stained" if n else
            "in view: no tissue")

    def _draw_regions(self):
        for it in self.region_items:
            self.scene().removeItem(it)
        self.region_items = []
        if self.sc is None or not self.layers["regions"]:
            return
        k = 1 / max(self.transform().m11(), 1e-9)          # scene units per screen px
        for i, r in enumerate(self.regions):
            c = region_colour(r["name"], i)
            path = QPainterPath()
            path.addPolygon(QPolygonF([QPointF(*p) for p in r["points"]]))
            path.closeSubpath()
            item = QGraphicsPathItem(path)
            pen = QPen(QColor("#ffffff") if i == self.selected else c, 3 if i == self.selected
                       else 2)
            pen.setCosmetic(True)
            if r["name"].strip().lower() == "exclude":
                pen.setStyle(Qt.DashLine)
            item.setPen(pen)
            fill = QColor(c)
            fill.setAlpha(45 if i == self.selected else 22)
            item.setBrush(QBrush(fill))
            item.setZValue(5)
            self.scene().addItem(item)
            self.region_items.append(item)
            txt = r["name"] + (f"  {self.labels[i]}" if i in self.labels else "")
            t = QGraphicsSimpleTextItem(txt)
            t.setBrush(QColor("#ffffff"))
            f = t.font()
            f.setPointSize(10)
            f.setBold(True)
            t.setFont(f)
            t.setFlag(QGraphicsSimpleTextItem.ItemIgnoresTransformations)
            br = path.boundingRect()
            t.setPos(br.left(), br.top() - 18 * k)
            t.setZValue(6)
            self.scene().addItem(t)
            self.region_items.append(t)

    # -- detail, read and measured off the GUI thread (one thread: its reader stays open)
    def _want_detail(self):
        if self.sc is None:
            return
        vs = self.transform().m11()                        # screen px per full-res px
        if vs <= self.ov_zoom * 1.4:
            self._detail = None
            self._put("detail", None)
            self._put("detail_ov", None)
            self.detail_changed.emit("")
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
                        z, self.glass, analysis.PROFILES[self.settings.profile]["w"]))

    def _worker(self):
        while True:
            job = self._jobs.get()
            while not self._jobs.empty():                  # only the latest view counts
                job = self._jobs.get()
            gen, sc, x, y, w, h, z, glass, wv = job
            try:
                rgb = czi.read(sc, x, y, w, h, z)
                s, hue, scanned = analysis.measure(rgb, analysis.od_lut(glass), wv)
                self._bridge.done.emit((gen, sc, x, y, z, rgb, s, hue, scanned))
            except Exception as e:                          # a view that can't be read
                self._bridge.done.emit((gen, sc, None, str(e)))

    def _detail_ready(self, res):
        if res[0] != self._gen or res[1] is not self.sc:
            return
        if res[2] is None:
            self.detail_changed.emit(f"⚠ detail not read: {res[3]}")
            return
        _, _, x, y, z, rgb, s, hue, scanned = res
        self._detail = (x, y, z, rgb, s, hue, scanned)
        self._put("detail", unpadded(rgb), x, y, 1 / z, z=2)
        self._draw_detail_overlay()

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
        self._draw_regions()
        self._timer.start()

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self._timer.start()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._timer.start()

    def set_drawing(self, on: bool):
        self.drawing = on
        self.setDragMode(QGraphicsView.NoDrag if on else QGraphicsView.ScrollHandDrag)
        self.viewport().setCursor(Qt.CrossCursor if on else Qt.OpenHandCursor)
        self._cancel_trace()

    def _cancel_trace(self):
        if self._trace is not None:
            self.scene().removeItem(self._trace)
        self._trace, self._pts = None, []

    def mousePressEvent(self, e):
        self._press = e.position()
        if self.drawing and e.button() == Qt.LeftButton and self.sc is not None:
            p = self.mapToScene(e.position().toPoint())
            self._pts = [(p.x(), p.y())]
            self._trace = QGraphicsPathItem()
            pen = QPen(QColor("#ffffff"), 2)
            pen.setCosmetic(True)
            self._trace.setPen(pen)
            self._trace.setZValue(10)
            self.scene().addItem(self._trace)
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self.drawing and self._trace is not None:
            p = self.mapToScene(e.position().toPoint())
            self._pts.append((min(max(p.x(), 0), self.sc.w), min(max(p.y(), 0), self.sc.h)))
            path = QPainterPath()
            path.addPolygon(QPolygonF([QPointF(*q) for q in self._pts]))
            self._trace.setPath(path)
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self.drawing and self._trace is not None:
            pts = self._pts
            self._cancel_trace()
            if len(pts) >= 3:
                a = np.asarray(pts)
                area = 0.5 * abs(np.dot(a[:, 0], np.roll(a[:, 1], 1)) -
                                 np.dot(a[:, 1], np.roll(a[:, 0], 1)))
                if area * self.transform().m11() ** 2 > 100:          # not a slip
                    step = max(1, len(pts) // 400)                    # ≤ ~400 vertices
                    self.region_drawn.emit([[round(x, 1), round(y, 1)] for x, y in pts[::step]])
            return
        super().mouseReleaseEvent(e)
        if (self._press is not None and e.button() == Qt.LeftButton and
                (e.position() - self._press).manhattanLength() < 4 and self.sc is not None):
            p = self.mapToScene(e.position().toPoint())
            hit = -1
            for i, r in enumerate(self.regions):
                path = QPainterPath()
                path.addPolygon(QPolygonF([QPointF(*q) for q in r["points"]]))
                if path.contains(p):
                    hit = i
            self.region_clicked.emit(hit)
        self.viewport().setCursor(Qt.CrossCursor if self.drawing else Qt.OpenHandCursor)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self._trace is not None:
            self._cancel_trace()
            return
        super().keyPressEvent(e)
