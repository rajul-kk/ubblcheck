"""One check run, from input to output files: used by the command line and the web UI.

    r = run.ifc("model.ifc", "out", heatmaps=True)
    r = run.pdf(pdfcheck.Form.load("project.yaml"), "out", stem="project")

Each returns a Run: the findings, the meta block printed at the top of the report,
and the files written (JSON, PDF report, and for IFC the BCF and heatmaps).
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

from . import findings as fnd

log = logging.getLogger("ubblcheck")


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
    def counts(self) -> str:
        s = self.summary
        return f"{s['error']} errors, {s['warning']} warnings, {s['info']} info"

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
    from .model import Facts
    t = [time.perf_counter()]
    model = ifcopenshell.open(path)
    t.append(time.perf_counter())
    facts = Facts(model) if rules else None     # read once for both rule passes
    fs = ifccheck.check_model(model, ids_paths, rules=rules, facts=facts)
    t.append(time.perf_counter())
    heat_dir = os.path.join(out, "heatmaps") if heatmaps and rules and geometry else None
    maps: dict = {}
    if rules and geometry:
        from . import georules
        fs += georules.check(model, heat_dir=heat_dir, cell=cell, heatmaps=maps, facts=facts)
    t.append(time.perf_counter())
    stem = stem or os.path.splitext(os.path.basename(path))[0]
    r = Run("ifc", stem, fs, {"project": stem, "model": os.path.basename(path)}, heatmaps=maps)
    os.makedirs(out, exist_ok=True)
    r.files["bcf"] = os.path.join(out, stem + ".bcf")
    fnd.to_bcf(fs, r.files["bcf"])
    _write(r, out)
    log.info("ifc %s: %d findings in %.1f s (open %.1f, IDS and rules %.1f, geometry %.1f, write %.1f)",
             os.path.basename(path), len(fs), time.perf_counter() - t[0], t[1] - t[0], t[2] - t[1],
             t[3] - t[2], time.perf_counter() - t[3])
    return r


def pdf(form, out: str, checklist: dict | None = None, stem: str = "project") -> Run:
    """Drawing-set checks for a filled-in pdfcheck.Form."""
    from . import pdfcheck
    t0 = time.perf_counter()
    fs, sheets = pdfcheck.check(form, checklist)
    t1 = time.perf_counter()
    meta = {"project": form.project, "state": form.state, "purpose_group": form.purpose_group,
            "sprinklered": "yes" if form.sprinklered else "no", "submission": form.submission,
            "sheets_read": f"{len(sheets)} ({sum(s.set == 'passive' for s in sheets)} passive, "
                           f"{sum(s.set == 'active' for s in sheets)} active)",
            "files": [os.path.basename(p) for ps in form.sets.values() for p in ps]}
    r = _write(Run("pdf", stem, fs, meta), out)
    log.info("pdf %s: %d sheets, %d findings in %.1f s (read and check %.1f, write %.1f)", stem, len(sheets),
             len(fs), time.perf_counter() - t0, t1 - t0, time.perf_counter() - t1)
    return r
