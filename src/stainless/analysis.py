"""The measurement: stained area of a section, in the whole section and in drawn regions.
No Qt.

Each pixel's absorbance (optical density) is taken against the section's own glass, not
against 255: the scanner's glass is grey (~160), and an OD taken against white carries the
glass's colour into every pixel. A pixel is

  tissue   inside the section's outline (absorbance smoothed over ~16 µm, dust removed)
           and, with a counterstain, absorbing itself (summed OD ≥ the profile's tissue OD;
           without one, unstained tissue is barely darker than glass: the outline is all);
  stained  when its absorbance points toward the stain (its hue score ≥ the threshold)
           and it is dense enough to be stain rather than noise (summed OD ≥ min OD).

The hue score is (OD · w) / ΣOD: which way the absorbance points, independent of how much
there is. For DAB, w = blue − red: brown absorbs blue more than red, hematoxylin the other
way, grey tissue neither. On the MI-21 VCAM-1 slide it found 9–11 % of the infarct stained
where colour deconvolution's DAB channel found 1.3–2.5 %, both at the threshold leaving
0.5 % of the negative control's infarct positive: deconvolution follows darkness, so the
control's dark folds and nuclei set its threshold (devlog, 2026-10-19).

One rule shared by an experiment's sections (a section may be given its own, recorded with
its result): a control and a treated section are measured with the same ruler.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass

import cv2
import numpy as np

from . import czi

ALGORITHM = 1             # bump when a change alters numbers: old results show as stale
OUTLINE_UM = 8.0          # resolution of the section outline and of the stored maps
MIN_PIECE_MM2 = 0.02      # tissue pieces smaller than this are dust
TILE = 2048               # full-res px per tile side (rounded to a whole number of map cells)

PROFILES = {   # w: the hue direction; outline_od: the smoothed OD outlining the section;
               # tissue_od: a pixel's own OD to count as tissue (0: the whole outline)
    "dab": {"label": "DAB (brown), hematoxylin counterstain", "w": (-1.0, 0.0, 1.0),
            "threshold": 0.03, "min_od": 0.20, "outline_od": 0.10, "tissue_od": 0.15,
            "colour": (0, 230, 118)},
    "sabg": {"label": "SA-β-gal (X-Gal, blue-green), no counterstain", "w": (1.0, 0.0, -1.0),
             "threshold": 0.35, "min_od": 0.08, "outline_od": 0.01, "tissue_od": 0.0,
             "colour": (255, 64, 160)},
}


@dataclass
class Settings:
    profile: str = "dab"
    threshold: float = PROFILES["dab"]["threshold"]
    min_od: float = PROFILES["dab"]["min_od"]
    level: int = 1                    # pixels averaged: 0 the scan's own, 1 2 × 2, 2 4 × 4

    def key(self) -> dict:
        return {**asdict(self), "algorithm": ALGORITHM}


# --- pixels ------------------------------------------------------------------------------

def od_lut(glass) -> np.ndarray:
    """256 × 3 table: a channel value -> its optical density against the glass (≥ 0)."""
    v = np.maximum(np.arange(256, dtype=np.float32), 1.0)[:, None]
    g = np.asarray(glass, np.float32)[None, :]
    return np.clip(-np.log10(v / g), 0, None).astype(np.float32)


def measure(rgb: np.ndarray, lut: np.ndarray, w) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per pixel: summed OD, hue score toward the stain, and scanned (not mosaic padding)."""
    r, g, b = lut[rgb[..., 0], 0], lut[rgb[..., 1], 1], lut[rgb[..., 2], 2]
    s = r + g + b
    hue = (r * w[0] + g * w[1] + b * w[2]) / np.maximum(s, 1e-6)
    lo, hi = rgb.min(2), rgb.max(2)
    scanned = (hi > 5) & (lo < 250)          # padding is pure black or pure white
    return s, hue, scanned


def classify(s, hue, scanned, st: Settings):
    tissue = scanned & (s >= PROFILES[st.profile]["tissue_od"])
    return tissue, tissue & (hue >= st.threshold) & (s >= st.min_od)


def estimate_glass(rgb: np.ndarray) -> np.ndarray:
    """The glass colour: the median of the brightest fifth of the scanned pixels."""
    flat = rgb.reshape(-1, 3)
    ok = (flat.max(1) > 5) & (flat.min(1) < 250)
    v = flat[ok].astype(np.float32)
    if len(v) < 100:
        return np.array([255.0, 255.0, 255.0], np.float32)
    br = v.mean(1)
    return np.median(v[br >= np.quantile(br, 0.8)], 0).astype(np.float32)


def outline(rgb: np.ndarray, glass, cell_um: float, profile: str = "dab") -> np.ndarray:
    """The section's outline on a coarse read (*cell_um* per px): the absorbance smoothed
    over ~16 µm above the profile's outline OD, closed, dust removed. With a per-pixel
    tissue test after it (a counterstain), grown by a cell so the border is the pixels'."""
    p = PROFILES[profile]
    s, _, scanned = measure(rgb, od_lut(glass), (0, 0, 0))
    s = cv2.GaussianBlur(np.where(scanned, s, 0).astype(np.float32), (0, 0), 16 / cell_um)
    m = (scanned & (s >= p["outline_od"])).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    keep = stats[:, cv2.CC_STAT_AREA] * (cell_um / 1000) ** 2 >= MIN_PIECE_MM2
    keep[0] = False
    m = keep[lab].astype(np.uint8)
    return (cv2.dilate(m, np.ones((3, 3), np.uint8)) if p["tissue_od"] > 0 else m) > 0


# --- regions -----------------------------------------------------------------------------

def rasterize(polys, shape, ox, oy, scale) -> np.ndarray:
    """Polygons (full-res scene px) filled into an array covering (ox, oy) at *scale*."""
    m = np.zeros(shape, np.uint8)
    pts = [np.round((np.asarray(p, np.float64) - (ox, oy)) * scale).astype(np.int32)
           for p in polys if len(p) >= 3]
    if pts:
        cv2.fillPoly(m, pts, 1)
    return m.astype(bool)


def _bbox_hits(poly, x0, y0, x1, y1) -> bool:
    p = np.asarray(poly)
    return not (p[:, 0].max() < x0 or p[:, 0].min() > x1 or p[:, 1].max() < y0 or
                p[:, 1].min() > y1)


# --- one section -------------------------------------------------------------------------

def _cells(a: np.ndarray, c: int, ny: int, nx: int) -> np.ndarray:
    """Sum of *a* over c × c cells, padded to ny × nx cells."""
    h, w = a.shape
    pad = np.zeros((ny * c, nx * c), np.float32)
    pad[:min(h, ny * c), :min(w, nx * c)] = a[:ny * c, :nx * c]
    return pad.reshape(ny, c, nx, c).sum((1, 3))


def analyze(scene: czi.Scene, st: Settings, regions: list[dict], cancel=None,
            progress=None, threads: int | None = None) -> dict:
    """Measure one section. *regions*: [{"name", "points": [[x, y], …]}] in full-res scene
    px; a region named "exclude" is taken out of the tissue everywhere. Returns the counts
    per region (the whole section first), the glass, the maps and the settings used."""
    t0 = time.perf_counter()
    prof = PROFILES[st.profile]
    w = prof["w"]
    z = 0.5 ** st.level                                      # a pyramid level of the file
    px = scene.px_um / z                                     # µm per processed pixel
    cell = max(4, int(round(OUTLINE_UM / scene.px_um / 4)) * 4)   # full-res px per map cell
    cz = max(1, int(round(cell * z)))                         # processed px per map cell
    tile = cell * max(1, TILE // cell)
    ny, nx = -(-scene.h // cell), -(-scene.w // cell)

    ov = czi.read(scene, 0, 0, scene.w, scene.h, scene.px_um / OUTLINE_UM)
    glass = estimate_glass(ov)
    out_m = outline(ov, glass, OUTLINE_UM, st.profile)
    out_m = cv2.resize(out_m.astype(np.uint8), (nx, ny), interpolation=cv2.INTER_NEAREST) > 0
    lut = od_lut(glass)

    excl = [r["points"] for r in regions if r["name"].strip().lower() == "exclude"]
    meas = [r for r in regions if r["name"].strip().lower() != "exclude"]
    names = ["section"] + [r["name"] for r in meas]
    acc = np.zeros((len(names), 3), np.float64)             # tissue px, stained px, ΣOD stained
    tmap = np.zeros((ny, nx), np.float32)
    pmap = np.zeros((ny, nx), np.float32)
    tiles = [(ox, oy) for oy in range(0, scene.h, tile) for ox in range(0, scene.w, tile)
             if out_m[oy // cell:(oy + tile) // cell + 1, ox // cell:(ox + tile) // cell + 1].any()]
    lock = threading.Lock()
    done = [0]

    def one(t):
        if cancel is not None and cancel.is_set():
            return
        ox, oy = t
        tw, th = min(tile, scene.w - ox), min(tile, scene.h - oy)
        rgb = czi.read(scene, ox, oy, tw, th, z)
        s, hue, scanned = measure(rgb, lut, w)
        cy0, cx0 = oy // cell, ox // cell
        cny, cnx = -(-th // cell), -(-tw // cell)
        gate = cv2.resize(out_m[cy0:cy0 + cny, cx0:cx0 + cnx].astype(np.uint8),
                          (rgb.shape[1], rgb.shape[0]), interpolation=cv2.INTER_NEAREST)
        scanned &= gate > 0
        tis, pos = classify(s, hue, scanned, st)
        if excl:
            hit = [p for p in excl if _bbox_hits(p, ox, oy, ox + tw, oy + th)]
            if hit:
                ex = rasterize(hit, tis.shape, ox, oy, z)
                tis &= ~ex
                pos &= ~ex
        so = np.where(pos, s, 0)
        rows = [(tis.sum(), pos.sum(), float(so.sum()))]
        for r in meas:
            if _bbox_hits(r["points"], ox, oy, ox + tw, oy + th):
                m = rasterize([r["points"]], tis.shape, ox, oy, z)
                rows.append(((tis & m).sum(), (pos & m).sum(), float(so[m].sum())))
            else:
                rows.append((0, 0, 0.0))
        tc = _cells(tis.astype(np.float32), cz, cny, cnx)
        pc = _cells(pos.astype(np.float32), cz, cny, cnx)
        with lock:
            acc[:] += np.asarray(rows, np.float64)
            tmap[cy0:cy0 + cny, cx0:cx0 + cnx] += tc
            pmap[cy0:cy0 + cny, cx0:cx0 + cnx] += pc
            done[0] += 1
            if progress is not None:
                progress(done[0], len(tiles))

    with ThreadPoolExecutor(threads or min(8, os.cpu_count() or 4)) as ex:
        list(ex.map(one, tiles))
    if cancel is not None and cancel.is_set():
        return {}

    area = (px / 1000) ** 2                                   # mm² per processed pixel
    rows = []
    for name, (tp, pp, so) in zip(names, acc):
        rows.append({"region": name, "tissue_mm2": float(tp * area),
                     "stained_mm2": float(pp * area),
                     "pct": float(100 * pp / tp) if tp else None,
                     "mean_od": float(so / pp) if pp else None})
    full = cz * cz
    return {"rows": rows, "glass": [round(float(g), 1) for g in glass],
            "um_per_px": round(float(px), 4), "zoom": z, "tiles": len(tiles),
            "seconds": round(time.perf_counter() - t0, 1), "cell_px": cell,
            "maps": {"tissue": np.clip(tmap / full * 255, 0, 255).astype(np.uint8),
                     "stained": np.clip(pmap / full * 255, 0, 255).astype(np.uint8)}}


def fingerprint(scene: czi.Scene, st: Settings, regions: list[dict]) -> str:
    """What a result depends on: the file as it is, the settings, the regions."""
    try:
        f = os.stat(scene.path)
        ident = [f.st_size, int(f.st_mtime)]
    except OSError:
        ident = []
    blob = json.dumps([ident, scene.index, st.key(),
                       [[r["name"], np.round(np.asarray(r["points"]), 1).tolist()]
                        for r in regions]], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def encode_map(a: np.ndarray) -> str:
    import base64
    return base64.b64encode(cv2.imencode(".png", a)[1].tobytes()).decode("ascii")


def decode_map(s: str) -> np.ndarray | None:
    import base64
    try:
        return cv2.imdecode(np.frombuffer(base64.b64decode(s), np.uint8), cv2.IMREAD_UNCHANGED)
    except Exception:
        return None


# --- self-check --------------------------------------------------------------------------

def _self_check() -> None:
    glass = (160, 163, 160)
    lut = od_lut(glass)
    px = np.array([[[160, 163, 160], [120, 100, 80], [90, 95, 140], [110, 112, 110]]], np.uint8)
    s, hue, scanned = measure(px, lut, PROFILES["dab"]["w"])
    assert s[0, 0] < 1e-6 and scanned.all()                   # glass: no absorbance
    assert hue[0, 1] > 0.1, hue                               # brown: toward DAB
    assert hue[0, 2] < -0.1, hue                              # blue: hematoxylin
    assert abs(hue[0, 3]) < 0.02, hue                         # grey: neither
    st = Settings()
    tis, pos = classify(s, hue, scanned, st)
    assert tis.tolist() == [[False, True, True, True]] and pos.tolist() == [[False, True, False, False]]
    s2, hue2, _ = measure(px, lut, PROFILES["sabg"]["w"])
    assert hue2[0, 2] > 0.1                                    # blue-green: toward X-Gal
    # glass estimate: mostly glass with a darker patch
    img = np.full((100, 100, 3), (158, 161, 158), np.uint8)
    img[20:60, 20:60] = (100, 90, 80)
    img[:, :5] = 0                                             # mosaic padding
    assert np.allclose(estimate_glass(img), (158, 161, 158))
    # region rasterization at half scale
    m = rasterize([[[0, 0], [20, 0], [20, 20], [0, 20]]], (20, 20), 0, 0, 0.5)
    assert 90 <= m.sum() <= 121, m.sum()
    assert _cells(np.ones((5, 7), np.float32), 2, 3, 4).sum() == 35
    assert decode_map(encode_map(np.eye(4, dtype=np.uint8) * 255)).tolist() == \
        (np.eye(4, dtype=np.uint8) * 255).tolist()
    print("analysis self-check OK")


if __name__ == "__main__":
    _self_check()
