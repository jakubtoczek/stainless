"""Zeiss CZI whole-slide scans: the sections (scenes) a file holds, region reads at a pyramid
level, the slide label. No Qt.

Pixels are read with pylibCZIrw. A read at a zoom that is not a pyramid level (1, ½, ¼ …)
is served by subsampling the level above it, so only powers of two are offered: at 1/3
the numbers are exactly those of ½ (measured on 2026-10-19).
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass

import numpy as np

MIN_SCAN_UM = 2.0          # coarser than this: a slide preview or snapshot, not a scan


@dataclass(frozen=True)
class Scene:
    path: str
    index: int
    x: int                 # bounding rectangle, full-resolution pixels, global frame
    y: int
    w: int
    h: int
    px_um: float           # µm per full-resolution pixel

    @property
    def mm(self) -> tuple[float, float]:
        return self.w * self.px_um / 1000, self.h * self.px_um / 1000


def pixel_size_um(raw_metadata: str) -> float | None:
    m = re.search(r'<Distance Id="X">.*?<Value>([^<]+)</Value>', raw_metadata, re.S)
    try:
        return float(m.group(1)) * 1e6 if m else None
    except ValueError:
        return None


def acquisition(raw_metadata: str) -> dict[str, str]:
    """What the scan says about itself, for the experiment's record (found fields only)."""
    out = {}
    for tag, key in (("AcquisitionDateAndTime", "acquired"), ("ObjectiveName", "objective"),
                     ("NominalMagnification", "magnification"), ("CameraName", "camera")):
        m = re.search(rf"<{tag}[^>]*>([^<]+)</{tag}>", raw_metadata)
        if m:
            out[key] = m.group(1).strip()
    m = re.search(r'<Microscope[^>]*\sName="([^"]+)"', raw_metadata)
    if m:
        out["microscope"] = m.group(1)
    return out


def scenes(path) -> tuple[list[Scene], dict[str, str]]:
    """The file's scenes (one per tissue section) and its acquisition fields."""
    from pylibCZIrw import czi as pyczi
    with pyczi.open_czi(str(path)) as doc:
        md = doc.raw_metadata
        px = pixel_size_um(md) or 0.0
        rects = doc.scenes_bounding_rectangle or {0: doc.total_bounding_rectangle}
        out = [Scene(str(path), int(i), r.x, r.y, r.w, r.h, px) for i, r in sorted(rects.items())]
    return out, acquisition(md)


_local = threading.local()


def reader(path: str):
    """One open reader per thread and file (pylibCZIrw readers are not shared)."""
    from pylibCZIrw import czi as pyczi
    d = getattr(_local, "readers", None)
    if d is None:
        d = _local.readers = {}
    if path not in d:
        d[path] = pyczi.CziReader(path)
    return d[path]


def read(scene: Scene, x: int, y: int, w: int, h: int, zoom: float) -> np.ndarray:
    """RGB uint8 of the scene-relative full-res rectangle (x, y, w, h), read at *zoom*."""
    a = reader(scene.path).read(roi=(scene.x + x, scene.y + y, w, h), zoom=zoom,
                                pixel_type="Bgr24")
    return np.ascontiguousarray(a[..., 2::-1])


def label(path) -> np.ndarray | None:
    """The slide label photographed by the scanner (uint8 RGB), or None."""
    try:
        import czifile
        with czifile.CziFile(str(path)) as f:
            for att in f.attachments():
                if att.attachment_entry.name == "Label":
                    a = np.asarray(att.data())
                    if a.ndim == 2:
                        a = np.stack([a] * 3, -1)
                    a = a[..., :3]
                    if a.dtype != np.uint8:
                        hi = float(a.max()) or 1.0
                        a = (a.astype(np.float32) * (255.0 / hi if hi > 255 else 1)).clip(0, 255)
                    return np.ascontiguousarray(a.astype(np.uint8)[..., ::-1])   # BGR -> RGB
    except Exception:
        return None
    return None


if __name__ == "__main__":
    assert Scene("x", 0, 0, 0, 1000, 2000, 0.5).mm == (0.5, 1.0)
    assert abs(pixel_size_um('<Distance Id="X"><Value>2.194e-07</Value>') - 0.2194) < 1e-9
    print("czi self-check OK")
