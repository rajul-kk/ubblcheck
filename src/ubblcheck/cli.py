"""ubblcheck ifc MODEL.ifc [-o OUT]       IFC model: IDS rule pack, UBBL rules, geometric rules -> BCF, JSON, PDF
ubblcheck pdf PROJECT.yaml [-o OUT]     2D drawing set(s) named in a project form -> JSON, PDF
ubblcheck ids [DIR]                     write the IFC-MY IDS files
ubblcheck form [FILE]                   write a blank project form
"""
from __future__ import annotations

import argparse
import os
import sys

from . import findings as fnd

FORM_TEMPLATE = """\
# Project form for `ubblcheck pdf`.  Paths are relative to this file.
project: Proposed 4-storey office, Lot 123, Mukim X
state: Selangor               # ramp limit 1:15 in Selangor, 1:12 elsewhere
purpose_group: IV             # UBBL Fifth Schedule, I-VIII
sub_use:                      # Seventh Schedule row if the group has several: hospital, school, hotel, flat, parking ...
sprinklered: false            # automatic sprinkler installation
top_floor_m: 14.0             # topmost floor above fire appliance access level (by-law 229)
volume_m3: 18000              # building volume (by-law 140)
submission: passive           # passive | active | both
passive_approval_ref:         # Bomba passive approval reference, needed for an active submission
sets:
  passive: [drawings/passive.pdf]
  active: []
"""


def _out(args, name):
    os.makedirs(args.out, exist_ok=True)
    return os.path.join(args.out, name)


def _finish(fs, args, stem, meta):
    from . import report
    j = _out(args, stem + ".json")
    fnd.to_json(fs, j, meta)
    p = report.write(fs, _out(args, stem + "-report.pdf"), meta)
    s = fnd.summary(fs)
    print(f"{s['error']} errors, {s['warning']} warnings, {s['info']} info")
    print(f"  {j}\n  {p}")
    return 1 if s["error"] else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ubblcheck", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ifc")
    a.add_argument("model")
    a.add_argument("-o", "--out", default="out")
    a.add_argument("--ids", nargs="*", help="IDS files (default: packaged pack)")
    a.add_argument("--no-rules", action="store_true", help="IDS only")
    a.add_argument("--no-geometry", action="store_true", help="skip geometric rules (travel, access, ...)")
    a.add_argument("--cell", type=float, default=0.2, help="travel grid cell in metres (default 0.2)")
    a.add_argument("--heatmaps", action="store_true", help="write a travel-distance PNG per storey")
    b = sub.add_parser("pdf")
    b.add_argument("form")
    b.add_argument("-o", "--out", default="out")
    b.add_argument("--checklist", help="alternative checklist.yaml")
    c = sub.add_parser("ids")
    c.add_argument("dir", nargs="?", default="ids")
    d = sub.add_parser("form")
    d.add_argument("file", nargs="?", default="project.yaml")
    args = ap.parse_args(argv)

    if args.cmd == "ids":
        from . import ids_build
        for p in ids_build.build(args.dir):
            print(p)
        return 0
    if args.cmd == "form":
        if os.path.exists(args.file):
            print(f"{args.file} exists; not overwritten", file=sys.stderr)
            return 2
        with open(args.file, "w", encoding="utf-8") as fh:
            fh.write(FORM_TEMPLATE)
        print(args.file)
        return 0
    if args.cmd == "ifc":
        from . import ifccheck
        fs = ifccheck.check(args.model, args.ids, rules=not args.no_rules)
        if not args.no_rules and not args.no_geometry:
            import ifcopenshell
            from . import georules
            heat = _out(args, "heatmaps") if args.heatmaps else None
            fs += georules.check(ifcopenshell.open(args.model), heat_dir=heat, cell=args.cell)
        stem = os.path.splitext(os.path.basename(args.model))[0]
        n = fnd.to_bcf(fs, _out(args, stem + ".bcf"))
        print(f"  {_out(args, stem + '.bcf')} ({n} topics)")
        return _finish(fs, args, stem, {"project": stem, "model": os.path.basename(args.model)})
    if args.cmd == "pdf":
        from . import pdfcheck
        form = pdfcheck.Form.load(args.form)
        cl = pdfcheck.load_checklist(args.checklist) if args.checklist else None
        fs, sheets = pdfcheck.check(form, cl)
        meta = {"project": form.project, "state": form.state, "purpose_group": form.purpose_group,
                "sprinklered": "yes" if form.sprinklered else "no", "submission": form.submission,
                "sheets_read": f"{len(sheets)} ({sum(s.set == 'passive' for s in sheets)} passive, "
                               f"{sum(s.set == 'active' for s in sheets)} active)",
                "files": [os.path.basename(p) for ps in form.sets.values() for p in ps]}
        stem = os.path.splitext(os.path.basename(args.form))[0]
        return _finish(fs, args, stem, meta)
    return 2


if __name__ == "__main__":
    sys.exit(main())
