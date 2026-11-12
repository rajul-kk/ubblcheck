"""One check run, from input to output files: used by the command line and the web UI.

    r = run.ifc("model.ifc", "out", heatmaps=True)
    r = run.pdf(pdfcheck.Form.load("project.yaml"), "out", stem="project")

Each returns a Run: the findings, the meta block printed at the top of the report,
and the files written (JSON, PDF report, and for IFC the BCF and heatmaps).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import findings as fnd


@dataclass
class Run:
    kind: str                       # "ifc" | "pdf"
    stem: str
    findings: list
    meta: dict
    files: dict = field(default_factory=dict)      # role -> path: json, report, bcf
    heatmaps: dict = field(default_factory=dict)   # storey name -> travel-distance PNG

    @property
    def summary(self) -> dict:
        return fnd.summary(self.findings)

    @property
    def exit_code(self) -> int:
        return 1 if self.summary["error"] else 0


def _write(r: Run, out: str) -> Run:
    from . import report
    os.makedirs(out, exist_ok=True)
    r.files["json"] = os.path.join(out, r.stem + ".json")
    fnd.to_json(r.findings, r.files["json"], r.meta)
    r.files["report"] = report.write(r.findings, os.path.join(out, r.stem + "-report.pdf"), r.meta)
    return r


def ifc(path: str, out: str, ids_paths: list[str] | None = None, rules: bool = True, geometry: bool = True,
        heatmaps: bool = False, cell: float = 0.2, stem: str | None = None) -> Run:
    """IDS pack, schedule rules and (unless geometry=False) geometric rules on an IFC model."""
    import ifcopenshell
    from . import ifccheck
    model = ifcopenshell.open(path)
    fs = ifccheck.check_model(model, ids_paths, rules=rules)
    heat_dir = os.path.join(out, "heatmaps") if heatmaps and rules and geometry else None
    maps: dict = {}
    if rules and geometry:
        from . import georules
        fs += georules.check(model, heat_dir=heat_dir, cell=cell, heatmaps=maps)
    stem = stem or os.path.splitext(os.path.basename(path))[0]
    r = Run("ifc", stem, fs, {"project": stem, "model": os.path.basename(path)}, heatmaps=maps)
    os.makedirs(out, exist_ok=True)
    r.files["bcf"] = os.path.join(out, stem + ".bcf")
    fnd.to_bcf(fs, r.files["bcf"])
    return _write(r, out)


def pdf(form, out: str, checklist: dict | None = None, stem: str = "project") -> Run:
    """Drawing-set checks for a filled-in pdfcheck.Form."""
    from . import pdfcheck
    fs, sheets = pdfcheck.check(form, checklist)
    meta = {"project": form.project, "state": form.state, "purpose_group": form.purpose_group,
            "sprinklered": "yes" if form.sprinklered else "no", "submission": form.submission,
            "sheets_read": f"{len(sheets)} ({sum(s.set == 'passive' for s in sheets)} passive, "
                           f"{sum(s.set == 'active' for s in sheets)} active)",
            "files": [os.path.basename(p) for ps in form.sets.values() for p in ps]}
    return _write(Run("pdf", stem, fs, meta), out)
