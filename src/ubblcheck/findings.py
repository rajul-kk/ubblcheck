"""Finding records shared by the IFC and PDF checkers, and their JSON/BCF output."""
from __future__ import annotations

import datetime
import json
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from xml.sax.saxutils import escape

SEVERITIES = ("error", "warning", "info")


def order(severity: str, rule: str) -> tuple:
    """Sort key for findings: errors first, then by rule."""
    return SEVERITIES.index(severity), rule


@dataclass
class Finding:
    rule: str            # stable rule id, e.g. "IFC-COMP-AREA" or "PDF-P07"
    severity: str        # error | warning | info
    message: str
    clause: str          # by-law or guide reference the rule rests on
    where: list[str] = field(default_factory=list)   # GlobalIds or sheet refs
    detail: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.severity not in SEVERITIES:
            raise ValueError(self.severity)


def summary(findings: list[Finding]) -> dict:
    out = {s: 0 for s in SEVERITIES}
    for f in findings:
        out[f.severity] += 1
    return out


def to_json(findings: list[Finding], path: str, meta: dict | None = None) -> None:
    doc = {"meta": meta or {}, "summary": summary(findings), "findings": [asdict(f) for f in findings]}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)


_PRIORITY = {"error": "Critical", "warning": "Normal", "info": "Minor"}


def to_bcf(findings: list[Finding], path: str, author: str = "ubblcheck") -> int:
    """Write a BCF 2.1 zip: one topic per finding, IFC GlobalIds as selected
    components so a BIM tool can zoom to them.  Returns the topic count."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("bcf.version",
                   '<?xml version="1.0" encoding="UTF-8"?>\n'
                   '<Version VersionId="2.1"><DetailedVersion>2.1</DetailedVersion></Version>\n')
        for f in findings:
            tid = str(uuid.uuid4())
            vid = str(uuid.uuid4())
            guids = [w for w in f.where if _is_guid(w)]
            desc = f"{f.message}\n\nBasis: {f.clause}\nRule: {f.rule}"
            markup = (
                '<?xml version="1.0" encoding="UTF-8"?>\n<Markup>\n'
                f'  <Topic Guid="{tid}" TopicType="Issue" TopicStatus="Open">\n'
                f'    <Title>{escape(f"[{f.rule}] {f.message}"[:250])}</Title>\n'
                f'    <Priority>{_PRIORITY[f.severity]}</Priority>\n'
                f'    <Labels>{escape(f.clause)}</Labels>\n'
                f'    <CreationDate>{now}</CreationDate>\n'
                f'    <CreationAuthor>{escape(author)}</CreationAuthor>\n'
                f'    <Description>{escape(desc)}</Description>\n'
                '  </Topic>\n')
            if guids:
                markup += (f'  <Viewpoints Guid="{vid}"><Viewpoint>viewpoint.bcfv</Viewpoint></Viewpoints>\n')
            markup += '</Markup>\n'
            z.writestr(f"{tid}/markup.bcf", markup)
            if guids:
                comps = "".join(f'<Component IfcGuid="{g}"/>' for g in guids)
                z.writestr(f"{tid}/viewpoint.bcfv",
                           '<?xml version="1.0" encoding="UTF-8"?>\n'
                           f'<VisualizationInfo Guid="{vid}"><Components>'
                           f'<Selection>{comps}</Selection>'
                           '<Visibility DefaultVisibility="true"/></Components></VisualizationInfo>\n')
    return len(findings)


_B64 = set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$")


def _is_guid(s: str) -> bool:
    return len(s) == 22 and set(s) <= _B64
