"""Check a 2D Bomba drawing set (PDF) before submission.

Four kinds of check, all on the PDF text layer and vector colours:
  completeness   items the passive / active sets must show (checklist.yaml)
  consistency    passive and active sets show the same levels, tags and areas
  numbers        ramp gradients and annotated travel distances against limits
  procedure      active set not before passive approval; no yellow access
                 road left on active drawings
"""
from __future__ import annotations

import os
import re
from collections import defaultdict
from dataclasses import dataclass, field

import yaml

from . import pdfread, ubbl
from .findings import Finding

DATA = os.path.join(os.path.dirname(__file__), "data", "checklist.yaml")
COLOUR_MIN = 40.0     # points of coloured linework before a colour counts as drawn


SUBMISSIONS = ("passive", "active", "both")
SETS = ("passive", "active")
NUMBER_FIELDS = {"top_floor_m": "topmost floor height", "volume_m3": "building volume"}
_YES = {"yes", "y", "true", "1", "ya"}
_NO = {"no", "n", "false", "0", "tidak", ""}


@dataclass
class Form:
    project: str = ""
    state: str = ""
    purpose_group: str = "IV"
    sub_use: str | None = None
    sprinklered: bool = False
    top_floor_m: float | None = None  # topmost floor above fire appliance access level; None = not given
    volume_m3: float | None = None
    submission: str = "passive"       # one of SUBMISSIONS
    passive_approval_ref: str | None = None
    sets: dict = field(default_factory=dict)   # passive/active -> [pdf paths]
    unreadable: dict = field(default_factory=dict)   # field -> raw value that could not be read

    @classmethod
    def from_dict(cls, d: dict, base: str = ".") -> "Form":
        """A form from parsed YAML or a web form.  Set paths are relative to `base`.
        Values are normalised; what cannot be read is kept in `unreadable` and reported by problems()."""
        d = dict(d or {})
        sets = {k: [v if os.path.isabs(v) else os.path.join(base, v) for v in (vs or [])]
                for k, vs in (d.pop("sets", {}) or {}).items()}
        f = cls(sets=sets, **{k: v for k, v in d.items() if k in cls.__dataclass_fields__ and k != "unreadable"})
        f.project = str(f.project or "")
        f.state = str(f.state or "").strip()
        f.purpose_group = str(f.purpose_group or "").strip().upper()
        f.sub_use = str(f.sub_use).strip() if f.sub_use not in (None, "") else None
        f.submission = str(f.submission or "").strip().lower()
        f.passive_approval_ref = str(f.passive_approval_ref).strip() if f.passive_approval_ref not in (None, "") else None
        if not isinstance(f.sprinklered, bool):
            raw = str(f.sprinklered).strip().lower()
            if raw not in _YES | _NO:
                f.unreadable["sprinklered"] = f.sprinklered
            f.sprinklered = raw in _YES
        for name in NUMBER_FIELDS:
            raw = getattr(f, name)
            try:
                setattr(f, name, None if raw in (None, "") else float(raw))
            except (TypeError, ValueError):
                f.unreadable[name] = raw
                setattr(f, name, None)
        return f

    @classmethod
    def load(cls, path: str) -> "Form":
        with open(path, encoding="utf-8") as fh:
            d = yaml.safe_load(fh) or {}
        return cls.from_dict(d, os.path.dirname(os.path.abspath(path)))

    def problems(self) -> list[Finding]:
        """Findings about the form itself: values the checks cannot use, and numbers not given."""
        out = []

        def bad(name, msg, severity="error"):
            out.append(Finding("PDF-FORM", severity, msg, "Project form", [], {"field": name}))

        if self.purpose_group not in ubbl.PURPOSE_GROUPS:
            bad("purpose_group", f"Purpose group {self.purpose_group!r} is not one of I-VIII; "
                                 "travel distances were not checked")
        elif self.sub_use:
            try:
                ubbl.use_key(self.purpose_group, self.sub_use)
            except KeyError:
                rows = sorted(k.split(".", 1)[1] for k in ubbl.TRAVEL if k.startswith(self.purpose_group + "."))
                bad("sub_use", f"Sub-use {self.sub_use!r} is not a Seventh Schedule row of group "
                               f"{self.purpose_group} ({', '.join(rows) or 'the group has one row; leave it blank'}); "
                               "travel distances were not checked")
        if self.submission not in SUBMISSIONS:
            bad("submission", f"Submission {self.submission!r} is not one of {', '.join(SUBMISSIONS)}; "
                              "submission order and set checks were not run")
        for name, raw in self.unreadable.items():
            if name not in NUMBER_FIELDS:
                bad(name, f"{name.replace('_', ' ').capitalize()} {raw!r} is not yes or no; taken as no", "warning")
        for name, what in NUMBER_FIELDS.items():
            if name in self.unreadable:
                bad(name, f"The {what} {self.unreadable[name]!r} is not a number; items that depend on it "
                          "were checked as if it were large")
            elif getattr(self, name) is None:
                bad(name, f"The {what} is not given; items that depend on it were checked as if it were large",
                    "warning")
        unknown = set(self.sets) - set(SETS)
        if unknown:
            bad("sets", f"Drawing set(s) {', '.join(sorted(unknown))} are not passive or active; they were "
                        "read but no checklist applies to them", "warning")
        return out


def load_checklist(path: str = DATA) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _over(value: float | None, threshold: float) -> bool:
    """value > threshold, where a value not given counts as over: a missing item is
    better reported than silently skipped."""
    return value is None or value > threshold


def _applies(item: dict, form: Form) -> bool:
    w = item.get("when") or {}
    if w.get("sprinklered") is not None and bool(form.sprinklered) != bool(w["sprinklered"]):
        return False
    if "min_top_floor_m" in w and not _over(form.top_floor_m, w["min_top_floor_m"]):
        return False
    if "min_volume_m3" in w and not _over(form.volume_m3, w["min_volume_m3"]):
        return False
    return True


def _hit(patterns, text) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


# ---------------------------------------------------------------- completeness

def completeness(sheets: list[pdfread.Sheet], form: Form, checklist: dict) -> list[Finding]:
    out = []
    by_set = defaultdict(list)
    for s in sheets:
        by_set[s.set].append(s)
    for item in checklist["items"]:
        st = item["set"]
        if st not in by_set or not _applies(item, form):
            continue
        pool = [s for s in by_set[st] if s.has_text and (item["sheet"] == "any" or s.kind == item["sheet"])]
        rule = f"PDF-{item['id']}"
        if not pool:
            if item["sheet"] != "any":
                out.append(Finding(rule, item["severity"],
                                   f"{st.title()} set has no {item['sheet'].replace('_', ' ')} sheet, "
                                   f"so cannot show: {item['what']}", item["basis"], []))
            continue
        pats = item["match"]
        extra = item.get("also")

        def ok(s):
            return _hit(pats, s.text) and (extra is None or _hit(extra, s.text))

        if item["scope"] == "each":
            missing = [s for s in pool if not ok(s)]
            if missing:
                out.append(Finding(rule, item["severity"],
                                   f"{item['what']}: not shown on {len(missing)} of {len(pool)} "
                                   f"{st} {item['sheet'].replace('_', ' ')} sheet(s)",
                                   item["basis"], [s.ref for s in missing]))
        elif not any(ok(s) for s in pool):
            out.append(Finding(rule, item["severity"], f"{item['what']}: not found in the {st} set",
                               item["basis"], [s.ref for s in pool][:6]))
        colour = item.get("colour")
        if colour and not any(s.colours.get(colour, 0) >= COLOUR_MIN for s in pool):
            out.append(Finding(rule + "-COLOUR", "warning",
                               f"{item['what']}: no {colour} linework found on the {st} "
                               f"{item['sheet'].replace('_', ' ')} sheets", item["basis"], []))
    return out


# ---------------------------------------------------------------- consistency

AREA = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s?(?:m2|m²|sq\.?\s?m)\b", re.I)


def _norm_tag(t: str) -> str:
    return re.sub(r"[\s.\-]", "", t.upper())


def tags_on(sheet: pdfread.Sheet, patterns: dict) -> dict[str, set]:
    return {k: {_norm_tag(m.group(0)) for m in re.finditer(p, sheet.text, re.I)} for k, p in patterns.items()}


def consistency(sheets: list[pdfread.Sheet], checklist: dict) -> list[Finding]:
    out = []
    pas = [s for s in sheets if s.set == "passive" and s.has_text]
    act = [s for s in sheets if s.set == "active" and s.has_text]
    if not pas or not act:
        return out
    basis = "IPM active floor/site 1: active drawings repeat every passive item; mismatch fails the application"

    def key(s):
        return (s.kind, s.level if s.kind == "floor_plan" else None)

    pk = defaultdict(list)
    ak = defaultdict(list)
    for s in pas:
        if s.kind in ("floor_plan", "site_plan"):
            pk[key(s)].append(s)
    for s in act:
        if s.kind in ("floor_plan", "site_plan"):
            ak[key(s)].append(s)

    def label(k):
        return "site plan" if k[0] == "site_plan" else f"floor plan {k[1] or '(level unknown)'}"

    for k in sorted(set(pk) | set(ak), key=str):
        if k not in ak:
            out.append(Finding("PDF-X-SHEET", "error", f"Passive {label(k)} has no active counterpart",
                               basis, [s.ref for s in pk[k]]))
            continue
        if k not in pk:
            out.append(Finding("PDF-X-SHEET", "warning", f"Active {label(k)} has no passive counterpart",
                               basis, [s.ref for s in ak[k]]))
            continue
        ptags = defaultdict(set)
        atags = defaultdict(set)
        for s in pk[k]:
            for t, v in tags_on(s, checklist["tags"]).items():
                ptags[t] |= v
        for s in ak[k]:
            for t, v in tags_on(s, checklist["tags"]).items():
                atags[t] |= v
        refs = [s.ref for s in pk[k] + ak[k]]
        for t in checklist["tags"]:
            only_p, only_a = ptags[t] - atags[t], atags[t] - ptags[t]
            if only_p or only_a:
                parts = []
                if only_p:
                    parts.append(f"only on passive: {', '.join(sorted(only_p))}")
                if only_a:
                    parts.append(f"only on active: {', '.join(sorted(only_a))}")
                out.append(Finding("PDF-X-TAG", "error",
                                   f"{label(k)}: {t.replace('_', ' ')} tags differ ({'; '.join(parts)})",
                                   basis, refs, {"tag": t, "passive_only": sorted(only_p),
                                                 "active_only": sorted(only_a)}))
        pa = {m.group(1).replace(",", "") for s in pk[k] for m in AREA.finditer(s.text)}
        aa = {m.group(1).replace(",", "") for s in ak[k] for m in AREA.finditer(s.text)}
        if pa and aa and pa != aa:
            out.append(Finding("PDF-X-AREA", "warning",
                               f"{label(k)}: area figures differ (passive {', '.join(sorted(pa - aa)) or '-'}; "
                               f"active {', '.join(sorted(aa - pa)) or '-'})", basis, refs))
    return out


# ---------------------------------------------------------------- numbers

RAMP_LINE = re.compile(r"ramp|tanjakan|landai|gradient|kecerunan", re.I)
GRADIENT = re.compile(r"\b1\s?:\s?(\d+(?:\.\d+)?)\b")
TRAVEL = re.compile(r"(travel distance|jarak perjalanan|\bT\.?D\.?)\s*(?:=|:|-)?\s*(\d+(?:\.\d+)?)\s?m\b", re.I)
DEADEND = re.compile(r"(dead.?end|jalan mati)\s*(?:distance)?\s*(?:=|:|-)?\s*(\d+(?:\.\d+)?)\s?m\b", re.I)


def numbers(sheets: list[pdfread.Sheet], form: Form, checklist: dict) -> list[Finding]:
    out = []
    rcfg = checklist["ramp"]
    limit = rcfg["by_state"].get((form.state or "").strip().lower(), rcfg["default"])
    for s in sheets:
        if not s.has_text:
            continue
        for line in s.text.splitlines():
            if not RAMP_LINE.search(line):
                continue
            for m in GRADIENT.finditer(line):
                n = float(m.group(1))
                if n < limit:
                    out.append(Finding("PDF-RAMP", "error",
                                       f"Ramp gradient 1:{m.group(1)} is steeper than 1:{limit}"
                                       f"{' (' + form.state + ')' if form.state else ''}: '{line.strip()[:80]}'",
                                       rcfg["basis"], [s.ref], {"gradient": n, "limit": limit}))
    try:
        lim = ubbl.travel_limit(form.purpose_group, form.sprinklered, form.sub_use)
    except KeyError:
        return out          # bad group or sub-use: Form.problems() reports it
    for s in sheets:
        if s.set != "passive" or s.kind != "floor_plan":
            continue
        for m in TRAVEL.finditer(s.text):
            d = float(m.group(2))
            if lim.travel_m is not None and d > lim.travel_m:
                out.append(Finding("PDF-TRAVEL", "error",
                                   f"Travel distance {d:g} m exceeds {lim.travel_m:g} m for purpose group "
                                   f"{form.purpose_group} ({'sprinklered' if form.sprinklered else 'unsprinklered'})",
                                   lim.clause, [s.ref], {"travel_m": d, "limit_m": lim.travel_m}))
        for m in DEADEND.finditer(s.text):
            d = float(m.group(2))
            if lim.dead_end_m is not None and d > lim.dead_end_m:
                out.append(Finding("PDF-DEADEND", "error",
                                   f"Dead end {d:g} m exceeds {lim.dead_end_m:g} m for purpose group {form.purpose_group}",
                                   lim.clause, [s.ref], {"dead_end_m": d, "limit_m": lim.dead_end_m}))
    return out


# ---------------------------------------------------------------- procedure

def procedure(sheets: list[pdfread.Sheet], form: Form) -> list[Finding]:
    out = []
    order = "IPM: the active set is submitted by the M&E engineer after passive approval"
    if form.submission == "active" and not form.passive_approval_ref:
        out.append(Finding("PDF-ORDER", "error",
                           "Active submission without a passive approval reference", order, []))
    if form.submission == "both":
        out.append(Finding("PDF-ORDER", "warning",
                           "Passive and active sets submitted together; Bomba approves passive first", order, []))
    for st in ("passive", "active"):
        if form.submission in (st, "both") and not any(s.set == st for s in sheets):
            out.append(Finding("PDF-SET", "error", f"Submission is {form.submission} but no {st} set was given",
                               "Project form", []))
    for s in sheets:
        if s.set == "active" and s.kind in ("floor_plan", "site_plan") and s.colours.get("yellow", 0) >= COLOUR_MIN:
            out.append(Finding("PDF-YELLOW", "error",
                               "Yellow (fire appliance access) linework left on an active drawing; "
                               "active sets show passive items in black and drop the yellow road",
                               "IPM active floor/site 1", [s.ref],
                               {"yellow_length_pt": round(s.colours["yellow"], 1)}))
        if not s.has_text:
            out.append(Finding("PDF-NOTEXT", "warning",
                               "Page has no text layer (scanned?); it was not checked. Export vector PDF from CAD",
                               "Tool limitation", [s.ref]))
    return out


# ---------------------------------------------------------------- entry

def read_sets(form: Form) -> list[pdfread.Sheet]:
    sheets = []
    for st, paths in form.sets.items():
        for p in paths:
            for s in pdfread.read(p):
                s.set = st
                sheets.append(s)
    return sheets


def check(form: Form, checklist: dict | None = None) -> tuple[list[Finding], list[pdfread.Sheet]]:
    checklist = checklist or load_checklist()
    sheets = read_sets(form)
    out = form.problems()
    out += procedure(sheets, form)
    out += completeness(sheets, form, checklist)
    out += consistency(sheets, checklist)
    out += numbers(sheets, form, checklist)
    return out, sheets
