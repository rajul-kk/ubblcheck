"""docs/bylaws.md must agree with the by-laws the code cites, mark by mark.

Each mark has its own evidence:
  IFC checked      a rule module cites it (ifccheck, georules, travel, or what they use from ubbl)
  IFC data         the IDS pack's source, ids_build.py, cites it
  drawing checked  pdfcheck.py cites it (or what it uses from ubbl)
  drawing shown    a checklist item cites it
ubbl.py is shared, so only the definitions a side actually uses (`ubbl.NAME`) count for that side.
"""
import ast
import functools
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "ubblcheck"
PART_VII_VIII = set(range(133, 254))
# 'by-law 165(4)', 'by-laws 175-177', 'by-law 213, 216', 'by-law 162 and 163'.  A later number in
# the list counts only when punctuation, 'and' or the end follows it: in 'by-law 175, 200 persons'
# or 'by-law 136, 150 m2', 200 and 150 are not by-laws.
_FIRST = r"\d+(?:\(\w+\))*"
_MORE = r"\d+(?:\(\w+\))*(?=\s*(?:[-–,;:.)\]'\"]|and\b|$))"
CITE = re.compile(rf"by-laws? ({_FIRST}(?:\s*(?:-|–|,|and)\s*{_MORE})*)", re.I | re.M)
MARKS = {"checked", "data", "shown", "—"}


def numbers(text: str) -> set[int]:
    """By-law numbers in a citation, ranges expanded: '165, 175-177' -> {165, 175, 176, 177}."""
    out = set()
    text = re.sub(r"\(\w+\)", "", text)
    for a, b in re.findall(r"(\d+)\s*[-–]\s*(\d+)", text):
        out.update(range(int(a), int(b) + 1))
    out.update(int(n) for n in re.findall(r"\d+", text))
    return out


def cites(text: str) -> set[int]:
    return {n for m in CITE.finditer(text) for n in numbers(m.group(1))} & PART_VII_VIII


@functools.cache
def source(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


@functools.cache
def ubbl_definitions() -> dict[str, str]:
    """Top-level name in ubbl.py -> (its source with the comment lines just above it, the names its
    code refers to)."""
    text = source("ubbl.py")
    lines = text.splitlines()
    out = {}
    for node in ast.parse(text).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        else:
            continue
        start = node.lineno - 1
        while start > 0 and lines[start - 1].lstrip().startswith("#"):
            start -= 1
        seg = "\n".join(lines[start:node.end_lineno])
        refs = {x.id for x in ast.walk(node) if isinstance(x, ast.Name)} - set(names)
        for n in names:
            out[n] = (seg, refs)
    return out


def ubbl_uses(name: str) -> set[str]:
    """The ubbl names a module's code uses as `ubbl.NAME` (comments and strings don't count)."""
    return {x.attr for x in ast.walk(ast.parse(source(name)))
            if isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name) and x.value.id == "ubbl"}


def via_ubbl(files) -> set[int]:
    """By-laws cited by the ubbl definitions these files use, and by the ubbl names those use."""
    defs = ubbl_definitions()
    todo = {n for f in files for n in ubbl_uses(f) if n in defs}
    seen = set()
    while todo:
        n = todo.pop()
        seen.add(n)
        todo |= {m for m in defs[n][1] if m in defs} - seen
    return set().union(*(cites(defs[n][0]) for n in seen))


def evidence() -> dict[tuple[str, str], set[int]]:
    rules_ifc = ["ifccheck.py", "georules.py", "travel.py"]
    return {
        ("ifc", "checked"): set().union(*(cites(source(f)) for f in rules_ifc)) | via_ubbl(rules_ifc),
        ("ifc", "data"): cites(source("ids_build.py")),
        ("drawing", "checked"): cites(source("pdfcheck.py")) | via_ubbl(["pdfcheck.py"]),
        ("drawing", "shown"): cites(source("data/checklist.yaml")),
    }


def table() -> dict[int, tuple[str, str]]:
    rows = {}
    for line in (ROOT / "docs" / "bylaws.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"\| (\d+(?:–\d+)?) \| [^|]+ \| ([^|]+) \| ([^|]+) \|", line)
        if m:
            for n in numbers(m.group(1)):
                assert n not in rows, f"by-law {n} listed twice"
                rows[n] = (m.group(2).strip(), m.group(3).strip())
    return rows


def test_every_bylaw_is_listed_once():
    assert set(table()) == PART_VII_VIII


def test_marks_are_known():
    for n, marks in table().items():
        assert set(marks) <= MARKS, (n, marks)


def test_each_mark_is_backed_by_its_evidence():
    ev = evidence()
    wrong = [(n, side, mark) for n, (ifc, dr) in sorted(table().items())
             for side, mark in (("ifc", ifc), ("drawing", dr))
             if mark != "—" and n not in ev.get((side, mark), set())]
    assert not wrong, f"marks with no code behind them: {wrong}"


def test_what_the_code_cites_is_listed():
    """A by-law a side's code cites is not marked — on that side."""
    rows = table()
    missing = [(n, side, mark) for (side, mark), ns in evidence().items() for n in sorted(ns)
               if rows[n][0 if side == "ifc" else 1] == "—"]
    assert not missing, f"cited by the code but marked —: {missing}"


def test_cite_pattern():
    assert cites("UBBL by-law 136, 150 m2 limit") == {136}
    assert cites("By-law 165, 175-177") == {165, 175, 176, 177}
    assert cites("UBBL by-law 225(1), 237(1), Tenth Schedule") == {225, 237}
    assert cites("by-laws 162 and 163") == {162, 163}
    assert cites("UBBL by-law 175, 200 persons") == {175}
    assert cites("By-law 194 allows a single staircase") == {194}
    assert cites("by-law 213, 216, Ninth Schedule") == {213, 216}
