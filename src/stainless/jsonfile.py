"""The suite's JSON files (C:\\Code\\LRB_suite\\FORMATS.md): the header first, written whole or
not at all, read back from any older version. One copy in each app that keeps JSON files,
identical to the suite's (suite.py checks). Run it for its self-check.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

HEADER = ("format", "format_version", "id", "written_by", "written_at")


class FormatError(ValueError):
    """Not a file of this kind, or one made by a newer version than this app reads."""


def new_id() -> str:
    """A document's identity: a random UUID, given once, kept by every save and copy."""
    return str(uuid.uuid4())


def now() -> str:
    """This instant, ISO 8601 with its UTC offset, to the second."""
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def header(fmt: str, version: int, app: str, id: str | None = None) -> dict:
    h: dict = {"format": fmt, "format_version": version}
    if id is not None:
        h["id"] = id
    h["written_by"] = app
    h["written_at"] = now()
    return h


def dumps(data: dict, fmt: str, version: int, app: str, id: str | None = None,
          indent: int | None = 1) -> str:
    """The header, then `data` (its own header keys, if any, replaced)."""
    body = {k: v for k, v in data.items() if k not in HEADER}
    return json.dumps({**header(fmt, version, app, id), **body}, indent=indent,
                      ensure_ascii=False)


def write_text(path, text: str) -> None:
    """`text` to `path` whole or not at all: written beside it as <name>.part, flushed to the
    disk, then renamed over the old file — retried for a second while Windows (an antivirus,
    an indexer) holds one of them."""
    path = Path(path)
    tmp = path.with_name(path.name + ".part")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(20):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)
    finally:
        tmp.unlink(missing_ok=True)


def write(path, data: dict, fmt: str, version: int, app: str, id: str | None = None,
          indent: int | None = 1) -> None:
    write_text(path, dumps(data, fmt, version, app, id, indent))


def read(path) -> dict:
    """The JSON object in a file (a byte-order mark is fine); FormatError if it holds another
    JSON value."""
    d = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(d, dict):
        raise FormatError(f"{Path(path).name}: not a JSON object")
    return d


def version_of(d: dict, fmt: str, newest: int, before=lambda d: None) -> int:
    """The format version of `d`, a `fmt` this app reads. A file from before the header:
    `before(d)` says its version (None: not a `fmt` at all). FormatError for another format,
    or one newer than `newest`."""
    if "format" not in d:
        v = before(d)
        if v is None:
            raise FormatError(f"not a {fmt} file")
        return v
    if d["format"] != fmt:
        raise FormatError(f"not a {fmt} file (a {d['format']} file)")
    v = d.get("format_version")
    if not isinstance(v, int) or isinstance(v, bool) or v < 1:
        raise FormatError(f"{fmt}: format_version {v!r} not understood")
    if v > newest:
        raise FormatError(f"made by a newer version ({fmt} {v}; this app reads up to {newest})"
                          " — update the app")
    return v


def body(d: dict) -> dict:
    """`d` without its header."""
    return {k: v for k, v in d.items() if k not in HEADER}


def rel_path(path, doc) -> str:
    """`path` from the folder of the document `doc`, '/' between names; "" on another drive."""
    try:
        return Path(os.path.relpath(path, Path(doc).parent)).as_posix()
    except ValueError:
        return ""


def locate(path, rel, doc) -> Path | None:
    """A data file a document points to: beside the document as it was (`rel`: the folder
    moved), where it was (`path`: the document moved alone), or the end of its old path under
    the document's folder (`RAW/x.xlsx`, then `x.xlsx`: a folder moved with a document saved
    before `rel`). None: not found. Which file it is, its `sha256` says."""
    base = Path(doc).parent
    parts = Path(path).parts if path else ()
    tails = [base.joinpath(*parts[-n:]) for n in range(min(len(parts) - 1, 4), 0, -1)]
    cands = ([base / rel] if rel else []) + ([Path(path)] if path else []) + tails
    return next((c.resolve() for c in cands if c.is_file()), None)


# a format's own bookkeeping: not what its results are computed from
BOOKKEEPING = {"biodist-study": ("saved_sha256", "embedded")}


def inputs_sha256(d: dict) -> str:
    """The fingerprint of what a document's `results` were computed from: the document
    without its header, its `results` and its format's bookkeeping (FORMATS.md › Results).
    A document changed since its results were validated no longer gives their
    `inputs_sha256`."""
    drop = {*HEADER, "results", *BOOKKEEPING.get(d.get("format"), ())}
    body = {k: v for k, v in d.items() if k not in drop}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "a.json"
        uid = new_id()
        write(p, {"name": "x", "format": "ignored"}, "test-doc", 2, "Test 1", uid)
        d = read(p)
        assert list(d)[:5] == list(HEADER) and d["id"] == uid and d["name"] == "x", d
        assert d["format"] == "test-doc" and version_of(d, "test-doc", 2) == 2
        assert body(d) == {"name": "x"}
        assert not (Path(tmp) / "a.json.part").exists()
        assert b"\r\n" not in p.read_bytes()
        p.write_bytes(b"\xef\xbb\xbf" + json.dumps({"old": 1}).encode())   # a BOM, no header
        assert version_of(read(p), "test-doc", 2, lambda d: 1 if "old" in d else None) == 1
        for bad, why in (({"format": "other", "format_version": 1}, "not a test-doc"),
                         ({"format": "test-doc", "format_version": 3}, "newer"),
                         ({"format": "test-doc", "format_version": True}, "not understood"),
                         ({"x": 1}, "not a test-doc")):
            try:
                version_of(bad, "test-doc", 2)
            except FormatError as e:
                assert why in str(e), (bad, e)
            else:
                raise AssertionError(bad)
        held = open(p, "rb")                 # Windows: a reader holding the old file
        try:
            write(p, {"name": "y"}, "test-doc", 2, "Test 1")
        except PermissionError:
            pass                             # held without share-delete: refused, intact
        finally:
            held.close()
        assert read(p).get("name") in ("y", None) and not (Path(tmp) / "a.json.part").exists()
        old, new = Path(tmp) / "old", Path(tmp) / "new"     # a study and its RAW folder
        (old / "RAW").mkdir(parents=True)
        f = old / "RAW" / "c.xlsx"
        f.write_text("x")
        doc = old / "s.json"
        r = rel_path(f, doc)
        assert r == "RAW/c.xlsx" and locate(f, r, doc) == f.resolve()
        assert locate(f, r, new / "s.json") == f.resolve()           # the study alone moved
        os.replace(old, new)
        assert locate(f, r, new / "s.json") == (new / "RAW" / "c.xlsx").resolve()  # the folder
        assert locate(f, "", new / "s.json") == (new / "RAW" / "c.xlsx").resolve()  # before rel
        (new / "RAW" / "c.xlsx").replace(new / "c.xlsx")
        assert locate(f, "", new / "s.json") == (new / "c.xlsx").resolve()   # by its name
        assert locate(f, r, Path(tmp) / "s.json") is None
        doc = {"format": "biodist-study", "format_version": 2, "written_at": "x", "animals": [1],
               "results": {"inputs_sha256": "?"}, "saved_sha256": "y", "embedded": {"a": 1}}
        fp = inputs_sha256(doc)
        assert fp == inputs_sha256({**doc, "written_at": "later", "embedded": {}, "results": {}})
        assert fp != inputs_sha256({**doc, "animals": [2]}), "an input changed"
    print("jsonfile self-check ok")
