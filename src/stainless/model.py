"""The experiment: its sections (a scene of a CZI file each), their regions, the record of
what was done, the settings, the results. Saved as one .json. No Qt.
"""

from __future__ import annotations

import csv
import math
import os
import re
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from . import __version__, analysis, czi

ROLES = ["sample", "negative control", "positive control", "left out"]
RECORD = ["title", "date", "operator", "project", "aim", "tissue", "target / antibody",
          "clone, supplier, catalog #", "dilution", "detection", "chromogen", "counterstain",
          "comment"]
HINT = {"title": "what this experiment is", "aim": "the question it answers",
        "tissue": "mouse heart, 7 d after MI…", "target / antibody": "VCAM-1, rabbit anti-mouse…",
        "dilution": "1:200", "detection": "HRP polymer, 30 min…", "chromogen": "DAB, 5 min",
        "counterstain": "hematoxylin"}
COLUMNS = ["section", "group", "role", "region", "tissue_mm2", "stained_mm2", "pct",
           "mean_od", "threshold", "min_od", "rule", "um_per_px", "state", "scan"]
RULE_KEYS = ("threshold", "min_od")          # what a section may set for itself


@dataclass
class Section:
    path: str
    index: int
    name: str                                        # the scan's: file name (+ scene)
    alias: str = ""                                  # what the results call it, if set
    group: str = ""
    role: str = "sample"
    note: str = ""
    regions: list = field(default_factory=list)      # [{"name", "kind", "points", …}]
    rule: dict = field(default_factory=dict)         # its own threshold / min_od, if any
    result: dict = field(default_factory=dict)       # the last analysis (+ its fingerprint)
    maps: dict = field(default_factory=dict)         # its maps, PNG in base64 (kept out of undo)
    scene: czi.Scene | None = field(default=None, repr=False, compare=False)

    @property
    def key(self) -> tuple[str, int]:
        return (os.path.normcase(self.path), self.index)

    @property
    def shown(self) -> str:
        return self.alias or self.name

    @property
    def scan(self) -> str:
        return f"{Path(self.path).name} #{self.index}"


# --- regions: a polygon of nodes, or a freehand trace with a few nodes on it -------------
# {"name", "kind": "roi" | "exclude", "points": [[x, y], …] (full-res scene px), "nodes":
# [i, …] (a freehand trace's draggable points; no key: every point is one), "auto": True
# (found by Find artefacts)}

def normalized(r: dict) -> dict:
    """A region with its kind; one from before kinds (a traced outline of up to ~400
    points) is freehand, without nodes."""
    r = dict(r, kind=analysis.kind(r))
    if "nodes" not in r and len(r["points"]) > 40 and "auto" not in r:
        r["nodes"] = []          # ponytail: >40 points = a trace, the old files' only kind
    return r


def is_node(r: dict, i: int) -> bool:
    return "nodes" not in r or i in r["nodes"]


def insert_node(r: dict, i: int, p) -> int:
    """A node at *p* on the edge from point *i* to the next. Returns its index."""
    r["points"].insert(i + 1, [round(p[0], 1), round(p[1], 1)])
    if "nodes" in r:
        r["nodes"] = sorted([j + 1 if j > i else j for j in r["nodes"]] + [i + 1])
    return i + 1


def delete_node(r: dict, i: int) -> bool:
    """Point *i* out (a region keeps 3 at least)."""
    if len(r["points"]) <= 3:
        return False
    del r["points"][i]
    if "nodes" in r:
        r["nodes"] = [j - 1 if j > i else j for j in r["nodes"] if j != i]
    return True


def grab(r: dict, i: int, p, span: float) -> int:
    """A freehand trace grabbed at *p* on its edge *i*: two nodes put *span* (scene px)
    along the trace on each side (or at the nearest node), the trace between them replaced
    by *p*, so dragging *p* bends that stretch only. Returns *p*'s index."""
    pts, n = r["points"], len(r["points"])
    k = (i - n // 2) % n                        # the edge to the middle: no wrapping
    pts = pts[k:] + pts[:k]
    nodes = {(j - k) % n for j in r.get("nodes", [])}
    i = n // 2
    a, run = i, math.dist(p, pts[i])
    while a > 0 and a not in nodes and run < span:
        run += math.dist(pts[a], pts[a - 1])
        a -= 1
    b, run = i + 1, math.dist(p, pts[i + 1])
    while b < n - 1 and b not in nodes and run < span:
        run += math.dist(pts[b], pts[b + 1])
        b += 1
    cut = b - a - 1
    r["points"] = pts[:a + 1] + [[round(p[0], 1), round(p[1], 1)]] + pts[b:]
    r["nodes"] = sorted({j if j <= a else j - cut + 1 for j in nodes if j <= a or j >= b}
                        | {a, a + 1, a + 2})
    return a + 1


def next_name(regions: list, stem: str) -> str:
    """stem 1, stem 2 … the first not taken."""
    taken = {r["name"] for r in regions}
    i = 1
    while f"{stem} {i}" in taken:
        i += 1
    return f"{stem} {i}"


def short_names(names: list[str]) -> list[str]:
    """The names without the words every one of them has (split at _ and spaces): the
    date, the study, the stain… "2026_04_08__9672_SABG_Lv21 s0" → "9672 Lv21 s0"."""
    words = [[w for w in re.split(r"[_\s]+", n) if w] for n in names]
    if len(names) < 2:
        return list(names)
    common = set(words[0]).intersection(*words[1:])
    out = [" ".join(w for w in ws if w not in common) for ws in words]
    return [o or n for o, n in zip(out, names)]


def guess_role(stem: str) -> str:
    s = stem.lower().replace("_", " ").replace("-", " ")
    if "neg" in s or "isotype" in s or "no primary" in s:
        return "negative control"
    if "posit" in s:
        return "positive control"
    return "sample"


@dataclass
class Experiment:
    sections: list = field(default_factory=list)
    record: dict = field(default_factory=lambda: {k: "" for k in RECORD})
    settings: analysis.Settings = field(default_factory=analysis.Settings)
    acquisition: dict = field(default_factory=dict)  # file name -> what the scan says
    path: str = ""

    # -- sections
    def add(self, paths, aliases: dict | None = None) -> tuple[list[Section], list[str]]:
        """CZI files, or folders of them: their scenes as sections (with the alias a name
        was given before, from *aliases*). Returns the new sections and what was left out,
        with why."""
        files, notes = [], []
        for p in map(Path, paths):
            if p.is_dir():
                files += sorted(p.glob("*.czi"))
            elif p.suffix.lower() == ".czi":
                files.append(p)
        known = {s.key for s in self.sections}
        new = []
        for f in files:
            try:
                scs, acq = czi.scenes(f)
            except Exception as e:
                notes.append(f"{f.name}: not readable ({e})")
                continue
            if scs and scs[0].px_um > czi.MIN_SCAN_UM:
                notes.append(f"{f.name}: {scs[0].px_um:.1f} µm/px — a preview or snapshot, "
                             f"not a scan (left out)")
                continue
            self.acquisition[f.name] = acq
            for sc in scs:
                name = f.stem + (f" s{sc.index}" if len(scs) > 1 else "")
                s = Section(str(f.resolve()), sc.index, name, role=guess_role(f.stem),
                            alias=(aliases or {}).get(name, ""), scene=sc)
                if s.key not in known:
                    known.add(s.key)
                    self.sections.append(s)
                    new.append(s)
        return new, notes

    def stale(self, s: Section) -> bool:
        return bool(s.result) and s.result.get("fingerprint") != self.fingerprint(s)

    def settings_for(self, s: Section) -> analysis.Settings:
        """The experiment's settings, with the section's own rule where it has one."""
        return replace(self.settings, **{k: v for k, v in s.rule.items() if k in RULE_KEYS})

    def fingerprint(self, s: Section) -> str:
        return analysis.fingerprint(s.scene, self.settings_for(s), s.regions) if s.scene else ""

    def state(self, s: Section) -> str:
        if s.role == "left out":
            return "left out"
        if not s.result:
            return "not measured"
        return "settings or regions changed" if self.stale(s) else "measured"

    # -- results
    def rows(self) -> list[dict]:
        out = []
        for s in self.sections:
            if s.role == "left out" or not s.result:
                continue
            st = self.state(s)
            used = s.result.get("settings", {})
            for r in s.result["rows"]:
                out.append({"section": s.shown, "group": s.group, "role": s.role,
                            "region": analysis.WHOLE if r["region"] == "section" else r["region"],
                            "tissue_mm2": r["tissue_mm2"],
                            "stained_mm2": r["stained_mm2"], "pct": r["pct"],
                            "mean_od": r["mean_od"], "threshold": used.get("threshold"),
                            "min_od": used.get("min_od"),
                            "rule": "own" if s.result.get("own_rule") else "shared",
                            "um_per_px": s.result["um_per_px"], "state": st,
                            "scan": s.scan})
        return out

    def settings_text(self) -> list[tuple[str, str]]:
        st = self.settings
        p = analysis.PROFILES[st.profile]
        res = ("the scan's own pixels" if not st.level else
               f"{2 ** st.level} × {2 ** st.level} scan pixels averaged (the file's "
               f"1/{2 ** st.level} pyramid level)")
        return [("stain", p["label"]),
                ("stained when", f"hue score ≥ {st.threshold:g} and summed OD ≥ {st.min_od:g} "
                                 f"(OD against each section's glass)"),
                ("tissue when", (f"summed OD ≥ {st.tissue_od:g}, " if st.tissue_od else "")
                 + f"inside the section's outline (OD smoothed over ~16 µm ≥ {st.outline_od:g}, "
                   f"pieces ≥ {analysis.MIN_PIECE_MM2:g} mm²); excluded regions taken out"),
                ("regions", "ROIs of one name pooled; the tissue in none of them is "
                            "'remaining tissue'"),
                ("resolution", res)] + (
            [("own rule", ", ".join(f"{s.shown} (hue ≥ {self.settings_for(s).threshold:g}, "
                                    f"OD ≥ {self.settings_for(s).min_od:g})"
                                    for s in self.sections if s.rule))]
            if any(s.rule for s in self.sections) else []) + [("Stainless", __version__)]

    def export_csv(self, path) -> None:
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, COLUMNS)
            w.writeheader()
            for r in self.rows():
                w.writerow({k: ("" if r[k] is None else round(r[k], 6) if isinstance(r[k], float)
                                else r[k]) for k in COLUMNS})

    def export_xlsx(self, path) -> None:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "results"
        ws.append(COLUMNS)
        for r in self.rows():
            ws.append([r[k] for k in COLUMNS])
        ws2 = wb.create_sheet("experiment")
        for k in RECORD:
            ws2.append([k, self.record.get(k, "")])
        ws2.append([])
        for k, v in self.settings_text():
            ws2.append([k, v])
        for name, acq in self.acquisition.items():
            ws2.append([])
            ws2.append(["scan", name])
            for k, v in acq.items():
                ws2.append([k, v])
        wb.save(path)

    # -- file
    def to_dict(self, maps: bool = True) -> dict:
        base = Path(self.path).parent if self.path else None
        secs = []
        for s in self.sections:
            d = asdict(s)
            d.pop("scene", None)
            if not maps:
                d.pop("maps", None)
            if base:
                try:          # the folder moved with its data opens the same
                    d["rel"] = Path(os.path.relpath(s.path, base)).as_posix()
                except ValueError:                        # another drive
                    d["rel"] = ""
            secs.append(d)
        return {"stainless": __version__, "record": self.record,
                "settings": asdict(self.settings), "acquisition": self.acquisition,
                "sections": secs}

    def save(self, path) -> None:
        import json
        self.path = str(Path(path).resolve())
        Path(path).write_text(json.dumps(self.to_dict(), indent=1, ensure_ascii=False,
                                         default=float), encoding="utf-8")

    @classmethod
    def from_dict(cls, d: dict, path: str = "", known: dict | None = None
                  ) -> tuple["Experiment", list[str]]:
        """The experiment, and what didn't come back. *known*: scenes already read, by
        section key (undo doesn't reopen every file)."""
        st = analysis.Settings.from_dict(d.get("settings", {}))
        rec = {k: "" for k in RECORD}
        rec.update({k: str(v) for k, v in d.get("record", {}).items()})
        e = cls(record=rec, settings=st, acquisition=d.get("acquisition", {}), path=path)
        base = Path(path).parent if path else None
        problems, scenes = [], {}
        for sd in d.get("sections", []):
            rel = sd.get("rel", "")
            cands = ([base / rel] if base and rel else []) + [Path(sd["path"])]
            if base:
                cands.append(base / Path(sd["path"]).name)
            hit = (known or {}).get((os.path.normcase(sd["path"]), sd["index"]))
            sd = {**sd, "regions": [normalized(r) for r in sd.get("regions", [])]}
            if hit is not None:
                known_s = {k: sd[k] for k in Section.__dataclass_fields__ if k in sd}
                e.sections.append(Section(**{**known_s, "scene": hit}))
                continue
            p = next((c for c in cands if c.is_file()), None)
            if p is None:
                problems.append(f"{Path(sd['path']).name}: not found (kept, can't be measured)")
                p = Path(sd["path"])
            elif str(p) not in scenes:
                try:
                    scenes[str(p)] = {sc.index: sc for sc in czi.scenes(p)[0]}
                except Exception as ex:
                    problems.append(f"{p.name}: not readable ({ex})")
                    scenes[str(p)] = {}
            known = {k: sd[k] for k in Section.__dataclass_fields__ if k in sd and k != "scene"}
            s = Section(**{**known, "path": str(p.resolve()) if p.is_file() else str(p)})
            s.scene = scenes.get(str(p), {}).get(s.index)
            e.sections.append(s)
        return e, problems

    @classmethod
    def open(cls, path) -> tuple["Experiment", list[str]]:
        import json
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        if "stainless" not in d:
            raise ValueError(f"{Path(path).name}: not a Stainless experiment")
        return cls.from_dict(d, str(Path(path).resolve()))


def summary(rows: list[dict], region: str) -> list[dict]:
    """Per group (controls apart): n, mean and SD of the % stained in *region*."""
    import statistics
    groups: dict[tuple, list[float]] = {}
    for r in rows:
        if r["region"] == region and r["pct"] is not None:
            g = r["group"] or "(no group)"
            groups.setdefault((r["role"] != "sample", g if r["role"] == "sample" else r["role"]),
                              []).append(r["pct"])
    out = []
    for (_, g), v in sorted(groups.items()):
        out.append({"group": g, "n": len(v), "mean": statistics.fmean(v),
                    "sd": statistics.stdev(v) if len(v) > 1 else None, "values": v})
    return out


if __name__ == "__main__":
    import json
    import tempfile
    assert guess_role("061026_IHC-VCAM1_MICID-MI-21 ctrl negatif") == "negative control"
    assert guess_role("x_ctrl_positif") == "positive control" and guess_role("MI-21") == "sample"
    e = Experiment()
    s = Section("C:/nowhere/a.czi", 0, "a s0", group="MI", rule={"threshold": 0.05}, regions=[
        {"name": "infarct", "points": [[0, 0], [10, 0], [10, 10]]}],
        result={"rows": [{"region": "section", "tissue_mm2": 2.0, "stained_mm2": 0.1,
                          "pct": 5.0, "mean_od": 0.5}], "um_per_px": 0.44, "fingerprint": ""})
    e.sections.append(s)
    assert e.rows()[0]["pct"] == 5.0 and e.state(s) == "measured"
    assert e.settings_for(s).threshold == 0.05 and e.settings_for(s).min_od == e.settings.min_od
    d = tempfile.mkdtemp()
    e.save(Path(d) / "x.json")
    e2, probs = Experiment.open(Path(d) / "x.json")
    assert probs and e2.sections[0].regions == [dict(s.regions[0], kind="roi")]
    assert e2.sections[0].group == "MI"
    assert e2.sections[0].rule == {"threshold": 0.05}
    assert json.loads(Path(d, "x.json").read_text(encoding="utf-8"))["stainless"] == __version__
    e.export_csv(Path(d) / "r.csv")
    e.export_xlsx(Path(d) / "r.xlsx")
    sm = summary(e.rows() + [{**e.rows()[0], "pct": 7.0}], analysis.WHOLE)
    assert sm[0]["n"] == 2 and sm[0]["mean"] == 6.0
    assert e2.sections[0].regions[0]["kind"] == "roi"
    # regions: a polygon's node inserted and deleted; a freehand trace grabbed
    r = {"name": "a", "kind": "roi", "points": [[0, 0], [10, 0], [10, 10], [0, 10]]}
    assert insert_node(r, 0, (5, 0)) == 1 and r["points"][1] == [5, 0] and "nodes" not in r
    assert delete_node(r, 1) and len(r["points"]) == 4
    fh = {"name": "b", "kind": "roi", "nodes": [0],
          "points": [[float(x), 0.0] for x in range(0, 100)] + [[99.0, 50.0], [0.0, 50.0]]}
    j = grab(fh, 50, (50.5, 3.0), 10)
    pts = fh["points"]
    assert pts[j] == [50.5, 3.0] and len(pts) == 89, len(pts)       # 14 trace points out
    assert [pts[i] for i in fh["nodes"]] == [[0.0, 0.0], [43.0, 0.0], [50.5, 3.0],
                                             [58.0, 0.0]], [pts[i] for i in fh["nodes"]]
    assert delete_node(fh, j) and len(fh["nodes"]) == 3
    old = normalized({"name": "exclude", "points": [[i, i % 7] for i in range(60)]})
    assert old["kind"] == "exclude" and old["nodes"] == []
    assert next_name([{"name": "ROI 1"}], "ROI") == "ROI 2"
    assert short_names(["2026_04_08__9672_SABG_Lv21 s0", "2026_04_08__9674_SABG_79 s1"]) == \
        ["9672 Lv21 s0", "9674 79 s1"]
    assert short_names(["a s0", "a s0"]) == ["a s0", "a s0"]
    print("model self-check OK")
