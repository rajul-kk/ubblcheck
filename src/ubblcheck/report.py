"""PDF report of findings: summary, then one row per finding with the sheet
or object it concerns and the by-law or guide item behind it."""
from __future__ import annotations

import datetime

from fpdf import FPDF

from .findings import SEVERITIES, Finding, summary

SEV_COL = {"error": (190, 30, 30), "warning": (200, 120, 0), "info": (90, 90, 90)}
_SUBST = {"–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"',
          "≥": ">=", "≤": "<=", "·": "-", "…": "..."}


def _t(s) -> str:
    s = str(s)
    for k, v in _SUBST.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "replace").decode("latin-1")


class _Doc(FPDF):
    def __init__(self, title):
        super().__init__(orientation="P", format="A4")
        self.title_text = title
        self.set_auto_page_break(True, 15)
        self.set_margins(15, 15, 15)

    def header(self):
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 5, _t(self.title_text), align="L")
        self.ln(8)
        self.set_text_color(0, 0, 0)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 5, f"Page {self.page_no()} - pre-submission check, not an approval", align="C")


def write(findings: list[Finding], path: str, meta: dict) -> str:
    title = f"UBBL fire pre-submission check: {meta.get('project') or 'project'}"
    doc = _Doc(title)
    doc.add_page()
    doc.set_font("Helvetica", "B", 16)
    doc.multi_cell(0, 8, _t(title), new_x="LMARGIN", new_y="NEXT")
    doc.ln(2)
    doc.set_font("Helvetica", size=9)
    rows = [("Checked", datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))]
    rows += [(k.replace("_", " ").capitalize(), v) for k, v in meta.items() if k != "project" and v not in (None, "", [])]
    for k, v in rows:
        if isinstance(v, (list, tuple)):
            v = ", ".join(map(str, v))
        doc.set_font("Helvetica", "B", 9)
        doc.cell(40, 5, _t(k))
        doc.set_font("Helvetica", size=9)
        doc.multi_cell(0, 5, _t(v), new_x="LMARGIN", new_y="NEXT")
    doc.ln(3)
    s = summary(findings)
    for sev in SEVERITIES:
        doc.set_text_color(*SEV_COL[sev])
        doc.set_font("Helvetica", "B", 11)
        doc.cell(45, 7, f"{s[sev]} {sev}{'s' if s[sev] != 1 and sev != 'info' else ''}")
    doc.set_text_color(0, 0, 0)
    doc.ln(10)
    if not findings:
        doc.set_font("Helvetica", size=10)
        doc.multi_cell(0, 6, "No findings. The checks that ran found nothing to fix; items the tool "
                             "cannot read (scanned pages, geometry) still need a human check.",
                       new_x="LMARGIN", new_y="NEXT")
    order = {s: i for i, s in enumerate(SEVERITIES)}
    for n, f in enumerate(sorted(findings, key=lambda f: (order[f.severity], f.rule)), 1):
        if doc.get_y() > 260:
            doc.add_page()
        doc.set_font("Helvetica", "B", 9)
        doc.set_text_color(*SEV_COL[f.severity])
        doc.cell(22, 5, _t(f"{n}. {f.severity.upper()}"))
        doc.set_text_color(0, 0, 0)
        doc.cell(0, 5, _t(f.rule), new_x="LMARGIN", new_y="NEXT")
        doc.set_font("Helvetica", size=9)
        doc.set_x(37)
        doc.multi_cell(0, 4.5, _t(f.message), new_x="LMARGIN", new_y="NEXT")
        doc.set_font("Helvetica", "I", 8)
        doc.set_x(37)
        doc.multi_cell(0, 4, _t("Basis: " + f.clause), new_x="LMARGIN", new_y="NEXT")
        if f.where:
            doc.set_font("Helvetica", size=8)
            shown = f.where[:8]
            more = f" (+{len(f.where) - 8} more)" if len(f.where) > 8 else ""
            doc.set_x(37)
            doc.multi_cell(0, 4, _t("At: " + "; ".join(shown) + more), new_x="LMARGIN", new_y="NEXT")
        doc.ln(2)
    doc.output(path)
    return path
