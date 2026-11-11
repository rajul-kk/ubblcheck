"""ubblcheck ifc MODEL.ifc [-o OUT]       IFC model: IDS rule pack, UBBL rules, geometric rules -> BCF, JSON, PDF
ubblcheck pdf PROJECT.yaml [-o OUT]     2D drawing set(s) named in a project form -> JSON, PDF
ubblcheck ids [DIR]                     write the IFC-MY IDS files
ubblcheck form [FILE]                   write a blank project form
ubblcheck serve [--port 8765]           local web page for both checks
"""
from __future__ import annotations

import argparse
import os
import sys

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


def _report(r) -> int:
    s = r.summary
    print(f"{s['error']} errors, {s['warning']} warnings, {s['info']} info")
    for role in ("bcf", "json", "report"):
        if role in r.files:
            print(f"  {r.files[role]}")
    for p in r.heatmaps:
        print(f"  {p}")
    return r.exit_code


def cmd_ifc(args) -> int:
    from . import run
    return _report(run.ifc(args.model, args.out, args.ids, rules=not args.no_rules,
                           geometry=not args.no_geometry, heatmaps=args.heatmaps, cell=args.cell))


def cmd_pdf(args) -> int:
    from . import pdfcheck, run
    form = pdfcheck.Form.load(args.form)
    cl = pdfcheck.load_checklist(args.checklist) if args.checklist else None
    return _report(run.pdf(form, args.out, cl, stem=os.path.splitext(os.path.basename(args.form))[0]))


def cmd_ids(args) -> int:
    from . import ids_build
    for p in ids_build.build(args.dir):
        print(p)
    return 0


def cmd_form(args) -> int:
    if os.path.exists(args.file):
        print(f"{args.file} exists; not overwritten", file=sys.stderr)
        return 2
    with open(args.file, "w", encoding="utf-8") as fh:
        fh.write(FORM_TEMPLATE)
    print(args.file)
    return 0


def cmd_serve(args) -> int:
    from . import web
    web.serve(args.host, args.port, args.work)
    return 0


def parser() -> argparse.ArgumentParser:
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
    a.set_defaults(func=cmd_ifc)
    b = sub.add_parser("pdf")
    b.add_argument("form")
    b.add_argument("-o", "--out", default="out")
    b.add_argument("--checklist", help="alternative checklist.yaml")
    b.set_defaults(func=cmd_pdf)
    c = sub.add_parser("ids")
    c.add_argument("dir", nargs="?", default="ids")
    c.set_defaults(func=cmd_ids)
    d = sub.add_parser("form")
    d.add_argument("file", nargs="?", default="project.yaml")
    d.set_defaults(func=cmd_form)
    e = sub.add_parser("serve", help="local web page (needs the [web] extra)")
    e.add_argument("--host", default="127.0.0.1")
    e.add_argument("--port", type=int, default=8765)
    e.add_argument("--work", help="folder for uploads and results (default: a temporary folder)")
    e.set_defaults(func=cmd_serve)
    return ap


def main(argv=None):
    args = parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
