"""Read a PDF drawing set into sheets: text layer, title, sheet number,
sheet type, floor level and coloured linework."""
from __future__ import annotations

import os
import re
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field

import pdfplumber

ORDINALS = {"ground": "L0", "first": "L1", "second": "L2", "third": "L3", "fourth": "L4",
            "fifth": "L5", "sixth": "L6", "seventh": "L7", "eighth": "L8", "ninth": "L9",
            "tenth": "L10", "bawah": "L0", "satu": "L1", "dua": "L2", "tiga": "L3", "empat": "L4",
            "lima": "L5", "enam": "L6", "tujuh": "L7", "lapan": "L8", "sembilan": "L9", "sepuluh": "L10"}

SHEET_TYPES = [
    ("schematic", r"schematic|skematik|riser diagram|single line"),
    ("site_plan", r"site plan|pelan tapak|key plan|location plan|pelan lokasi"),
    ("floor_plan", r"floor plan|pelan lantai|pelan tingkat|pelan aras|level \d+ plan|basement plan|roof plan|\bplan\b"),
    ("general", r"general|notes|nota|calculation|pengiraan|schedule|jadual|cover|perakuan|maklumat am"),
]

SHEET_NO = re.compile(r"(?:dwg|drawing|drg|sheet|lukisan)\.?\s*(?:no|number|bil)\.?\s*[:.]?\s*([A-Z0-9][A-Z0-9./-]{1,15})",
                      re.I)


@dataclass
class Sheet:
    file: str
    page: int                 # 1-based
    text: str
    title: str
    number: str | None
    kind: str                 # floor_plan | site_plan | schematic | general
    level: str | None         # L0, L1, B1, ROOF ... for floor plans
    colours: dict = field(default_factory=dict)   # name -> drawn length/area score
    has_text: bool = True
    set: str | None = None    # passive | active, filled in by the caller

    @property
    def ref(self) -> str:
        num = f" {self.number}" if self.number else ""
        return f"{self.file} p{self.page}{num} ({self.title})"


def level_of(title: str) -> str | None:
    t = title.lower()
    m = re.search(r"basement\s*(?:level|aras)?\s*(\d*)|lower ground|bawah tanah\s*(?:aras)?\s*(\d*)", t)
    if m:
        if "lower ground" in m.group(0):
            return "LG"
        return "B" + (m.group(1) or m.group(2) or "1")
    if re.search(r"\broof\b|bumbung", t):
        return "ROOF"
    m = re.search(r"\b(level|aras|tingkat|lantai)\s*(\d+)", t)
    if m:
        return f"L{int(m.group(2))}"
    m = re.search(r"\b(\d+)(st|nd|rd|th)\s+(floor|storey)", t)
    if m:
        return f"L{int(m.group(1))}"
    for word, key in ORDINALS.items():
        if re.search(rf"\b{word}\b", t):
            return key
    return None


def kind_of(title: str, text: str) -> str:
    for kind, pat in SHEET_TYPES:
        if re.search(pat, title, re.I):
            return kind
    for kind, pat in SHEET_TYPES[:2]:
        if re.search(pat, text[:400], re.I):
            return kind
    return "general"


def _rgb(c):
    """pdfplumber colour -> (r, g, b) in 0..1, or None."""
    if c is None:
        return None
    if isinstance(c, (int, float)):
        return (float(c),) * 3
    c = tuple(float(x) for x in c if isinstance(x, (int, float)))
    if len(c) == 1:
        return c * 3
    if len(c) == 3:
        return c
    if len(c) == 4:
        k = c[3]
        return tuple((1 - x) * (1 - k) for x in c[:3])
    return None


def colour_name(c) -> str | None:
    rgb = _rgb(c)
    if rgb is None:
        return None
    r, g, b = rgb
    if r > 0.75 and g > 0.65 and b < 0.4:
        return "yellow"
    if r > 0.65 and g < 0.35 and b < 0.35:
        return "red"
    return None


def _colours(page) -> dict:
    out: dict = {}
    for obj in page.rects + page.lines + page.curves:
        w = abs(obj.get("x1", 0) - obj.get("x0", 0))
        h = abs(obj.get("bottom", 0) - obj.get("top", 0))
        if obj.get("stroke"):
            n = colour_name(obj.get("stroking_color"))
            if n:
                out[n] = out.get(n, 0.0) + max(w, h)
        if obj.get("fill"):
            n = colour_name(obj.get("non_stroking_color"))
            if n:
                out[n] = out.get(n, 0.0) + max(w, h)
    return out


def _title(page, text: str) -> str:
    """The line set in the largest type, which on a drawing is the title."""
    chars = [c for c in page.chars if c.get("text", "").strip()]
    if not chars:
        return text.strip().splitlines()[0][:80] if text.strip() else "untitled"
    big = max(round(c["size"], 1) for c in chars)
    lines: dict = {}
    for c in chars:
        if round(c["size"], 1) >= big - 0.5:
            lines.setdefault(round(c["top"]), []).append(c)
    best = max(lines.values(), key=len)
    words = page.extract_words()
    top = round(best[0]["top"])
    line = [w for w in words if abs(round(w["top"]) - top) <= 2]
    s = " ".join(w["text"] for w in sorted(line, key=lambda w: w["x0"])) if line else "".join(c["text"] for c in best)
    return s.strip()[:120]


# Parsing a page (pdfminer, pure Python) is nearly all of the time, and pages are independent, so a
# long set is read by several processes, one pool for all its files.  Each holds its own parser,
# about 100 MB.
PARALLEL_FROM_PAGES = 8     # below this, starting the processes costs more than it saves
MAX_WORKERS = 4


def read_all(paths: list[str], names: list[str] | None = None) -> list[list[Sheet]]:
    """The sheets of each PDF, in page order."""
    names = names or [os.path.basename(p) for p in paths]
    counts = []
    for p in paths:
        with pdfplumber.open(p) as pdf:
            counts.append(len(pdf.pages))
    workers = min(MAX_WORKERS, (os.cpu_count() or 1) - 1) if sum(counts) >= PARALLEL_FROM_PAGES else 1
    if workers <= 1:
        return [_read_pages(p, nm, range(n)) for p, nm, n in zip(paths, names, counts)]
    # every k-th page of a file to each worker, so a run of heavy pages is shared out
    jobs = [(i, k) for i, n in enumerate(counts) for k in range(min(workers, n))]
    with ProcessPoolExecutor(workers) as pool:
        parts = pool.map(_read_pages, [paths[i] for i, _ in jobs], [names[i] for i, _ in jobs],
                         [range(k, counts[i], workers) for i, k in jobs])
        out = [[] for _ in paths]           # two files may share a name (passive/ and active/)
        for (i, _), part in zip(jobs, parts):
            out[i] += part
    return [sorted(sheets, key=lambda s: s.page) for sheets in out]


def _read_pages(path: str, name: str, pages) -> list[Sheet]:
    out = []
    with pdfplumber.open(path) as pdf:
        for i in pages:
            page = pdf.pages[i]
            text = page.extract_text() or ""
            has_text = len(text.strip()) > 20
            title = _title(page, text) if has_text else "no text layer"
            m = SHEET_NO.search(text)
            kind = kind_of(title, text) if has_text else "general"
            out.append(Sheet(file=name, page=i + 1, text=text, title=title,
                             number=m.group(1) if m else None, kind=kind,
                             level=level_of(title) if kind == "floor_plan" else None,
                             colours=_colours(page), has_text=has_text))
            page.close()                # drop its parsed objects: memory stays flat over a long set
    return out
