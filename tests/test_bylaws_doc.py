"""docs/bylaws.md must agree with the by-laws the code cites."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "ubblcheck"
IFC = ["ifccheck.py", "georules.py", "ubbl.py", "ids/my-ubbl-passive.ids", "ids/my-ubbl-active.ids"]
DRAWINGS = ["pdfcheck.py", "data/checklist.yaml", "ubbl.py"]
PART_VII_VIII = range(133, 254)
CITE = re.compile(r"by-laws? ((?:\d+(?:\(\w+\))*(?:\s*(?:-|–|,|and)\s*)?)+)", re.I)


def numbers(text: str) -> set[int]:
    """By-law numbers in a citation, ranges expanded: '165, 175-177' -> {165, 175, 176, 177}."""
    out = set()
    text = re.sub(r"\(\w+\)", "", text)
    for a, b in re.findall(r"(\d+)\s*[-–]\s*(\d+)", text):
        out.update(range(int(a), int(b) + 1))
    out.update(int(n) for n in re.findall(r"\d+", text))
    return out


def cited(files) -> set[int]:
    found = set()
    for name in files:
        for m in CITE.finditer((SRC / name).read_text(encoding="utf-8")):
            found |= numbers(m.group(1))
    return found & set(PART_VII_VIII)


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
    assert set(table()) == set(PART_VII_VIII)


def test_marks_are_known():
    for n, marks in table().items():
        assert set(marks) <= {"checked", "data", "shown", "—"}, (n, marks)


def test_what_the_code_cites_is_listed_as_covered():
    rows = table()
    assert not [n for n in cited(IFC) if rows[n][0] == "—"], "IFC code cites by-laws the table marks —"
    assert not [n for n in cited(DRAWINGS) - cited(["ubbl.py"]) if rows[n][1] == "—"], \
        "drawing checks cite by-laws the table marks —"


def test_what_the_table_claims_is_cited_by_the_code():
    rows = table()
    assert not [n for n, (ifc, _) in rows.items() if ifc != "—" and n not in cited(IFC)]
    assert not [n for n, (_, dr) in rows.items() if dr != "—" and n not in cited(DRAWINGS)]
