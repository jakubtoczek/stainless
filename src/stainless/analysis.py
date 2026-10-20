"""The measurement: stained area of a section, in the whole section and in drawn regions.
No Qt.

Each pixel's absorbance (optical density) is taken against the section's own glass, not
against 255: the scanner's glass is grey (~160), and an OD taken against white carries the
glass's colour into every pixel. A pixel is

  tissue   inside the section's outline (absorbance smoothed over ~16 µm, dust removed)
           and, with a counterstain, absorbing itself (summed OD ≥ the tissue OD;
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

Regions are ROIs or exclusions. ROIs of one name are one region (pooled); the tissue in no
ROI is reported as "remaining tissue"; exclusions take their area out of the tissue.

The per-pixel work is OpenCV's (it releases the GIL, numpy's fancy indexing doesn't), in
strips of a tile (small arrays: Windows' page faults on large fresh ones serialise the
threads): ~2× faster than numpy on the same tiles, same counts (2026-10-20).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, replace

import cv2
import numpy as np

from . import czi

ALGORITHM = 3             # bump when a change alters numbers: old results show as stale
OUTLINE_UM = 8.0          # resolution of the section outline and of the stored maps
ARTEFACT_UM = 4.0         # resolution folds, bubbles and debris are looked for at
MIN_PIECE_MM2 = 0.02      # tissue pieces smaller than this are dust
TILE = 2048               # full-res px per tile side (rounded to a whole number of map cells)
STRIP = 256               # processed rows at a time
WHOLE, REST = "whole section", "remaining tissue"

PROFILES = {   # w: the hue direction; outline_od: the smoothed OD outlining the section;
               # tissue_od: a pixel's own OD to count as tissue (0: the whole outline)
    "dab": {"label": "DAB (brown), hematoxylin counterstain", "w": (-1.0, 0.0, 1.0),
            "threshold": 0.03, "min_od": 0.20, "outline_od": 0.10, "tissue_od": 0.15,
            "colour": (0, 230, 118)},
    "sabg": {"label": "SA-β-gal (X-Gal, blue-green), no counterstain", "w": (1.0, 0.0, -1.0),
             "threshold": 0.35, "min_od": 0.08, "outline_od": 0.01, "tissue_od": 0.0,
             "colour": (255, 64, 160)},
}
PROFILE_KEYS = ("threshold", "min_od", "tissue_od", "outline_od")   # a stain's own numbers


@dataclass
class Settings:
    profile: str = "dab"
    threshold: float = PROFILES["dab"]["threshold"]
    min_od: float = PROFILES["dab"]["min_od"]
    level: int = 1                    # pixels averaged: 0 the scan's own, 1 2 × 2, 2 4 × 4
    tissue_od: float = PROFILES["dab"]["tissue_od"]
    outline_od: float = PROFILES["dab"]["outline_od"]

    @classmethod
    def of(cls, profile: str, level: int = 1) -> "Settings":
        """A stain's defaults."""
        return cls(profile, level=level, **{k: PROFILES[profile][k] for k in PROFILE_KEYS})

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        """Saved settings; what an older file lacks is its stain's default."""
        p = d.get("profile") if d.get("profile") in PROFILES else "dab"
        base = asdict(cls.of(p))
        return cls(**{k: type(base[k])(d[k]) if k in d else v for k, v in base.items()})

    def key(self) -> dict:
        return {**asdict(self), "algorithm": ALGORITHM}


# --- pixels ------------------------------------------------------------------------------

def od_lut(glass) -> np.ndarray:
    """256 × 3 table: a channel value -> its optical density against the glass (≥ 0)."""
    v = np.maximum(np.arange(256, dtype=np.float32), 1.0)[:, None]
    g = np.asarray(glass, np.float32)[None, :]
    return np.clip(-np.log10(v / g), 0, None).astype(np.float32)


def measure(rgb: np.ndarray, lut: np.ndarray, w) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per pixel: summed OD s, the absorbance toward the stain d = OD · w (hue = d / s), and
    scanned (not mosaic padding, 255/0)."""
    od = cv2.LUT(np.ascontiguousarray(rgb), lut.reshape(1, 256, 3))
    s = cv2.transform(od, np.ones((1, 3), np.float32))
    d = cv2.transform(od, np.asarray([w], np.float32))
    r, g, b = cv2.split(rgb)
    lo, hi = cv2.min(cv2.min(r, g), b), cv2.max(cv2.max(r, g), b)
    scanned = cv2.bitwise_and(cv2.compare(hi, 5, cv2.CMP_GT),     # padding is pure black
                              cv2.compare(lo, 250, cv2.CMP_LT))   # or pure white
    return s, d, scanned


def classify(s, d, scanned, st: Settings):
    """(tissue, stained), 255/0. hue ≥ t is d ≥ t · s (s > 0: min OD is kept above 0)."""
    tissue = cv2.bitwise_and(scanned, cv2.compare(s, st.tissue_od, cv2.CMP_GE))
    pos = cv2.bitwise_and(cv2.compare(cv2.scaleAdd(s, -st.threshold, d), 0, cv2.CMP_GE),
                          cv2.compare(s, max(st.min_od, 1e-6), cv2.CMP_GE))
    return tissue, cv2.bitwise_and(tissue, pos)


def estimate_glass(rgb: np.ndarray) -> np.ndarray:
    """The glass colour: the median of the brightest fifth of the scanned pixels."""
    flat = rgb.reshape(-1, 3)
    ok = (flat.max(1) > 5) & (flat.min(1) < 250)
    v = flat[ok].astype(np.float32)
    if len(v) < 100:
        return np.array([255.0, 255.0, 255.0], np.float32)
    br = v.mean(1)
    return np.median(v[br >= np.quantile(br, 0.8)], 0).astype(np.float32)


def outline(rgb: np.ndarray, glass, cell_um: float, st: Settings) -> np.ndarray:
    """The section's outline on a coarse read (*cell_um* per px): the absorbance smoothed
    over ~16 µm above the outline OD, closed, dust removed. With a per-pixel tissue test
    after it (a counterstain), grown by a cell so the border is the pixels'."""
    s, _, scanned = measure(rgb, od_lut(glass), (0, 0, 0))
    s = cv2.GaussianBlur(cv2.bitwise_and(s, s, mask=scanned), (0, 0), 16 / cell_um)
    m = ((scanned > 0) & (s >= st.outline_od)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    keep = stats[:, cv2.CC_STAT_AREA] * (cell_um / 1000) ** 2 >= MIN_PIECE_MM2
    keep[0] = False
    m = keep[lab].astype(np.uint8)
    return (cv2.dilate(m, np.ones((3, 3), np.uint8)) if st.tissue_od > 0 else m) > 0


def base(scene: czi.Scene, st: Settings) -> tuple[np.ndarray, np.ndarray]:
    """The section's glass and outline (bool, OUTLINE_UM per px over the scene): what the
    measurement and the view both start from."""
    ov = czi.read(scene, 0, 0, scene.w, scene.h, scene.px_um / OUTLINE_UM)
    glass = estimate_glass(ov)
    return glass, outline(ov, glass, OUTLINE_UM, st)


# --- regions -----------------------------------------------------------------------------

def kind(r: dict) -> str:
    """"roi" or "exclude" (a region from before kinds: by its name)."""
    return r.get("kind") or ("exclude" if r["name"].strip().lower() == "exclude" else "roi")


def rasterize(polys, shape, ox, oy, scale) -> np.ndarray:
    """Polygons (full-res scene px) filled into a 255/0 array covering (ox, oy) at *scale*:
    their union (filled together, two that overlap would cancel out where they do)."""
    m = np.zeros(shape, np.uint8)
    for p in polys:
        if len(p) >= 3:
            cv2.fillPoly(m, [np.round((np.asarray(p, np.float64) - (ox, oy)) * scale
                                      ).astype(np.int32)], 255)
    return m


def _bbox_hits(poly, x0, y0, x1, y1) -> bool:
    p = np.asarray(poly)
    return not (p[:, 0].max() < x0 or p[:, 0].min() > x1 or p[:, 1].max() < y0 or
                p[:, 1].min() > y1)


def groups(regions: list[dict]) -> tuple[list[str], dict[str, list], list]:
    """The ROI names in drawing order, their polygons, and the exclusions' polygons."""
    names, polys, excl = [], {}, []
    for r in regions:
        if kind(r) == "exclude":
            excl.append(r["points"])
        else:
            n = r["name"].strip() or "ROI"
            if n not in polys:
                names.append(n)
                polys[n] = []
            polys[n].append(r["points"])
    return names, polys, excl


# --- one section -------------------------------------------------------------------------

def _cells(m: np.ndarray, c: int, ny: int, nx: int) -> np.ndarray:
    """The 255/0 mask *m* as the fraction (× 255) covered in c × c cells, padded or cut to
    ny × nx cells."""
    h, w = m.shape
    m = m[:ny * c, :nx * c]
    if m.shape != (ny * c, nx * c):
        m = cv2.copyMakeBorder(m, 0, ny * c - m.shape[0], 0, nx * c - m.shape[1],
                               cv2.BORDER_CONSTANT, value=0)
    return cv2.resize(m, (nx, ny), interpolation=cv2.INTER_AREA)


def analyze(scene: czi.Scene, st: Settings, regions: list[dict], cancel=None,
            progress=None, threads: int | None = None) -> dict:
    """Measure one section. *regions*: [{"name", "kind", "points": [[x, y], …]}] in full-res
    scene px. Returns the counts per region (the whole section, the ROIs by name, the
    remaining tissue), the excluded tissue, the glass, the maps and the settings used."""
    t0 = time.perf_counter()
    w = PROFILES[st.profile]["w"]
    z = 0.5 ** st.level                                      # a pyramid level of the file
    px = scene.px_um / z                                     # µm per processed pixel
    cell = max(4, int(round(OUTLINE_UM / scene.px_um / 4)) * 4)   # full-res px per map cell
    cz = max(1, int(round(cell * z)))                         # processed px per map cell
    tile = cell * max(1, TILE // cell)
    ny, nx = -(-scene.h // cell), -(-scene.w // cell)

    glass, out_m = base(scene, st)
    out_m = cv2.resize(out_m.astype(np.uint8) * 255, (nx, ny), interpolation=cv2.INTER_NEAREST)
    lut = od_lut(glass)

    roi_names, roi_polys, excl = groups(regions)
    names = [WHOLE] + roi_names + ([REST] if roi_names else [])
    acc = np.zeros((len(names), 3), np.float64)             # tissue px, stained px, ΣOD stained
    excluded = [0]
    tmap = np.zeros((ny, nx), np.uint8)
    pmap = np.zeros((ny, nx), np.uint8)
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
        h, wd = rgb.shape[:2]
        cy0, cx0 = oy // cell, ox // cell
        cny, cnx = -(-th // cell), -(-tw // cell)
        gate = cv2.resize(out_m[cy0:cy0 + cny, cx0:cx0 + cnx], (wd, h),
                          interpolation=cv2.INTER_NEAREST)
        box = (ox, oy, ox + tw, oy + th)
        hit = [p for p in excl if _bbox_hits(p, *box)]
        ex = rasterize(hit, (h, wd), ox, oy, z) if hit else None
        masks = [rasterize([p for p in roi_polys[n] if _bbox_hits(p, *box)], (h, wd), ox, oy, z)
                 for n in roi_names]
        if masks:
            union = masks[0].copy()
            for m in masks[1:]:
                cv2.bitwise_or(union, m, dst=union)
            masks.append(cv2.bitwise_not(union))
        rows = np.zeros((len(names), 3), np.float64)
        n_ex = 0
        tis_t = np.empty((h, wd), np.uint8)
        pos_t = np.empty((h, wd), np.uint8)
        for y in range(0, h, STRIP):
            sl = slice(y, y + STRIP)
            s, d, scanned = measure(rgb[sl], lut, w)
            tis, pos = classify(s, d, cv2.bitwise_and(scanned, gate[sl]), st)
            if ex is not None:
                n_ex += cv2.countNonZero(cv2.bitwise_and(tis, ex[sl]))
                keep = cv2.bitwise_not(ex[sl])
                tis, pos = cv2.bitwise_and(tis, keep), cv2.bitwise_and(pos, keep)
            for j, m in enumerate([None] + masks):
                tj = tis if m is None else cv2.bitwise_and(tis, m[sl])
                pj = pos if m is None else cv2.bitwise_and(pos, m[sl])
                k = cv2.countNonZero(pj)
                rows[j] += (cv2.countNonZero(tj), k, cv2.mean(s, mask=pj)[0] * k if k else 0.0)
            tis_t[sl], pos_t[sl] = tis, pos
        tc, pc = _cells(tis_t, cz, cny, cnx), _cells(pos_t, cz, cny, cnx)
        with lock:
            acc[:] += rows
            excluded[0] += n_ex
            tmap[cy0:cy0 + cny, cx0:cx0 + cnx] = tc
            pmap[cy0:cy0 + cny, cx0:cx0 + cnx] = pc
            done[0] += 1
            if progress is not None:
                progress(done[0], len(tiles))

    with ThreadPoolExecutor(threads or min(12, os.cpu_count() or 4)) as ex:
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
    return {"rows": rows, "excluded_mm2": float(excluded[0] * area),
            "glass": [round(float(g), 1) for g in glass],
            "um_per_px": round(float(px), 4), "zoom": z, "tiles": len(tiles),
            "seconds": round(time.perf_counter() - t0, 1), "cell_px": cell,
            "maps": {"tissue": tmap, "stained": pmap}}


def fingerprint(scene: czi.Scene, st: Settings, regions: list[dict]) -> str:
    """What a result depends on: the file as it is, the settings, the regions (an
    exclusion's name doesn't count, an ROI's does: it is the row it goes to)."""
    try:
        f = os.stat(scene.path)
        ident = [f.st_size, int(f.st_mtime)]
    except OSError:
        ident = []
    blob = json.dumps([ident, scene.index, st.key(),
                       [[r["name"] if kind(r) == "roi" else "", kind(r),
                         np.round(np.asarray(r["points"]), 1).tolist()] for r in regions]],
                      sort_keys=True)
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


# --- folds, bubbles, debris --------------------------------------------------------------

def _smooth(a, m, sigma):
    """*a* averaged over the pixels of mask *m* (float 0/1) around each pixel."""
    num = cv2.GaussianBlur(a * m, (0, 0), sigma)
    return num / np.maximum(cv2.GaussianBlur(m, (0, 0), sigma), 1e-3)


def artefacts(rgb: np.ndarray, um: float, st: Settings, factor: float = 1.6,
              min_mm2: float = 0.004) -> list[tuple[str, np.ndarray]]:
    """Folds, bubbles and debris on a read at *um* µm/px: tissue absorbing *factor* times
    more than the tissue around it (~250 µm) and than the section's median, not toward the
    stain. A fold is tissue lying on itself: twice the absorbance, the same colour. Each
    piece: ("bubble", …) when it is a dark ring around a light disc, ("debris", …) when far
    darker than tissue, ("fold", …) else; with its outline (px of the read)."""
    glass = estimate_glass(rgb)
    s, d, scanned = measure(rgb, od_lut(glass), PROFILES[st.profile]["w"])
    tis = (outline(rgb, glass, um, st) & (scanned > 0) & (s >= max(st.tissue_od, 0.02)))
    if tis.sum() < 100:
        return []
    m = tis.astype(np.float32)
    med = float(np.median(s[tis]))
    sm = _smooth(s, m, 8 / um)
    hue = _smooth(d, m, 8 / um) / np.maximum(sm, 1e-6)
    k = 8                                                    # the surround, on a coarser grid
    small = (max(1, s.shape[1] // k), max(1, s.shape[0] // k))
    ms = cv2.resize(m, small, interpolation=cv2.INTER_AREA)
    loc = _smooth(cv2.resize(s * m, small, interpolation=cv2.INTER_AREA) /
                  np.maximum(ms, 1e-3), ms, 250 / um / k)
    loc = cv2.resize(loc, (s.shape[1], s.shape[0]), interpolation=cv2.INTER_LINEAR)
    cand = (tis & (sm >= factor * np.maximum(loc, med)) & (hue < st.threshold)).astype(np.uint8)
    cand = cv2.morphologyEx(cand, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(cand, connectivity=8)
    px_mm2 = (um / 1000) ** 2
    out = []
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] * px_mm2 < min_mm2:
            continue
        x, y, w, h = (stats[i, j] for j in (cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP,
                                            cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT))
        piece = (lab[y:y + h, x:x + w] == i).astype(np.uint8)
        piece = cv2.copyMakeBorder(piece, 3, 3, 3, 3, cv2.BORDER_CONSTANT, value=0)
        piece = cv2.dilate(piece, np.ones((5, 5), np.uint8))      # the fold's edge with it
        cs, _ = cv2.findContours(piece, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        c = max(cs, key=cv2.contourArea)
        filled, per = cv2.contourArea(c), cv2.arcLength(c, True)
        dark = float(s[y:y + h, x:x + w][lab[y:y + h, x:x + w] == i].mean())
        # ponytail: shape and darkness rules, looked at on the VCAM-1 slides only
        if (filled > 1.8 * stats[i, cv2.CC_STAT_AREA] and
                4 * np.pi * filled / max(per, 1) ** 2 > 0.6):
            what = "bubble"
        elif dark >= max(1.2, 3 * med):
            what = "debris"
        else:
            what = "fold"
        c = cv2.approxPolyDP(c, 1.0, True).reshape(-1, 2).astype(np.float64) + (x - 3, y - 3)
        if len(c) >= 3:
            out.append((what, c))
    return out


def find_artefacts(scene: czi.Scene, st: Settings, factor: float = 1.6,
                   min_mm2: float = 0.004) -> list[tuple[str, list]]:
    """artefacts() on the section, outlines in full-res scene px."""
    z = min(1.0, scene.px_um / ARTEFACT_UM)
    rgb = czi.read(scene, 0, 0, scene.w, scene.h, z)
    zy, zx = rgb.shape[0] / scene.h, rgb.shape[1] / scene.w
    return [(what, np.round((c + 0.5) / (zx, zy), 1).tolist())
            for what, c in artefacts(rgb, scene.px_um / z, st, factor, min_mm2)]


# --- self-check --------------------------------------------------------------------------

def _self_check() -> None:
    glass = (160, 163, 160)
    lut = od_lut(glass)
    px = np.array([[[160, 163, 160], [120, 100, 80], [90, 95, 140], [110, 112, 110]]], np.uint8)
    s, d, scanned = measure(px, lut, PROFILES["dab"]["w"])
    hue = d / np.maximum(s, 1e-6)
    assert s[0, 0] < 1e-6 and scanned.all()                   # glass: no absorbance
    assert hue[0, 1] > 0.1, hue                               # brown: toward DAB
    assert hue[0, 2] < -0.1, hue                              # blue: hematoxylin
    assert abs(hue[0, 3]) < 0.02, hue                         # grey: neither
    st = Settings()
    tis, pos = classify(s, d, scanned, st)
    assert (tis > 0).tolist() == [[False, True, True, True]], tis
    assert (pos > 0).tolist() == [[False, True, False, False]], pos
    s2, d2, _ = measure(px, lut, PROFILES["sabg"]["w"])
    assert d2[0, 2] / s2[0, 2] > 0.1                           # blue-green: toward X-Gal
    # settings: a stain's defaults, an old file's settings filled in
    sa = Settings.from_dict({"profile": "sabg", "threshold": 0.4, "level": 2})
    assert sa.threshold == 0.4 and sa.min_od == 0.08 and sa.tissue_od == 0.0 and sa.level == 2
    assert Settings.of("dab") == Settings()
    # glass estimate: mostly glass with a darker patch
    img = np.full((100, 100, 3), (158, 161, 158), np.uint8)
    img[20:60, 20:60] = (100, 90, 80)
    img[:, :5] = 0                                             # mosaic padding
    assert np.allclose(estimate_glass(img), (158, 161, 158))
    # region rasterization at half scale; ROIs pooled by name; exclusions apart
    m = rasterize([[[0, 0], [20, 0], [20, 20], [0, 20]]], (20, 20), 0, 0, 0.5)
    assert 90 <= (m > 0).sum() <= 121, (m > 0).sum()
    m = rasterize([[[0, 0], [20, 0], [20, 20], [0, 20]], [[5, 5], [15, 5], [15, 15], [5, 15]]],
                  (20, 20), 0, 0, 1)
    assert m[10, 10] == 255                                    # overlapping: both, no hole
    regs = [{"name": "infarct", "points": [[0, 0], [1, 0], [1, 1]]},
            {"name": "exclude", "points": [[0, 0], [1, 0], [1, 1]]},
            {"name": "fold 1", "kind": "exclude", "points": [[0, 0], [1, 0], [1, 1]]},
            {"name": "infarct", "kind": "roi", "points": [[0, 0], [2, 0], [2, 2]]}]
    names, polys, excl = groups(regs)
    assert names == ["infarct"] and len(polys["infarct"]) == 2 and len(excl) == 2
    sc = czi.Scene("nowhere.czi", 0, 0, 0, 10, 10, 0.5)
    renamed = [dict(r, name="debris 9", kind="exclude") if kind(r) == "exclude" else r
               for r in regs]
    assert fingerprint(sc, st, regs) == fingerprint(sc, st, renamed)    # exclusion names
    assert fingerprint(sc, st, regs) != fingerprint(sc, st, regs[:3])
    c = _cells(np.full((4, 4), 255, np.uint8), 2, 3, 3)
    assert c[:2, :2].min() == 255 and c[2].max() == 0 and c[:, 2].max() == 0, c
    assert decode_map(encode_map(np.eye(4, dtype=np.uint8) * 255)).tolist() == \
        (np.eye(4, dtype=np.uint8) * 255).tolist()
    # artefacts: a tissue disc with a fold (twice as dark, same colour) and a bubble
    um = 4.0
    img = np.full((400, 400, 3), glass, np.uint8)
    cv2.circle(img, (200, 200), 170, (118, 112, 140), -1)      # tissue, hematoxylin-blue
    cv2.line(img, (80, 150), (320, 170), (82, 74, 115), 6)      # a fold: OD doubled
    cv2.circle(img, (200, 270), 40, (40, 40, 40), 3)            # a bubble's dark rim
    found = artefacts(img, um, st)
    kinds = sorted(k for k, _ in found)
    assert kinds == ["bubble", "fold"], kinds
    fold = [c for k, c in found if k == "fold"][0]
    assert fold[:, 0].min() < 100 and fold[:, 0].max() > 300, fold
    print("analysis self-check OK")


if __name__ == "__main__":
    _self_check()
