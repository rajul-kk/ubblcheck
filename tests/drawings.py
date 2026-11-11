"""Synthetic Bomba drawing sets as vector PDFs (fpdf2): a title in large type,
a title block with the drawing number, annotations and coloured linework."""
from fpdf import FPDF

COL = {"red": (220, 20, 20), "yellow": (255, 230, 0), "black": (0, 0, 0)}


def write_set(path, sheets):
    """sheets: [(title, number, [annotation lines], [colours])]"""
    pdf = FPDF(orientation="L", format="A3")
    pdf.set_auto_page_break(False)
    for title, number, notes, colours in sheets:
        pdf.add_page()
        for i, c in enumerate(colours):
            pdf.set_draw_color(*COL[c])
            pdf.set_line_width(1.5)
            y = 60 + 12 * i
            pdf.line(40, y, 300, y)
            pdf.line(300, y, 300, y + 80)
        pdf.set_draw_color(0, 0, 0)
        pdf.set_font("Helvetica", size=8)
        y = 40
        for n in notes:
            pdf.text(320, y, n)
            y += 5
        pdf.set_font("Helvetica", "B", 20)
        pdf.text(40, 270, title)
        pdf.set_font("Helvetica", size=9)
        pdf.text(330, 280, f"DWG NO: {number}")
    pdf.output(str(path))
    return str(path)


def floor_common(level_title, area="800 m2", td="38", tags=("FD1", "FD2", "HR1", "FE1", "ST1", "ST2")):
    return [f"FLOOR AREA {area}", f"TRAVEL DISTANCE = {td} m", "DEAD END 10 m",
            "STAIRCASE AND EXIT DOORS", "FIRE DOOR (FRD) 1 HR", "COMPARTMENT WALL SHOWN RED",
            "SMOKE LOBBY", "PORTABLE FIRE EXTINGUISHER", "EMERGENCY LIGHT", "EXIT SIGN 'KELUAR'",
            "HOSE REEL", "BREAK GLASS", "ALARM BELL", "TAGS: " + " ".join(tags)]


def passive_set(path, levels=("GROUND", "FIRST"), drop=(), ramp="1:12", td="38", extra_tags=None):
    sheets = [("GENERAL NOTES AND FIRE REQUIREMENTS", "A-001",
               ["FIRE REQUIREMENTS (KEHENDAK-KEHENDAK BOMBA)", "PERAKUAN: I hereby certify the above",
                "OCCUPANCY LOAD CALCULATION", "FIRE APPLIANCE ACCESS CALCULATION",
                "WALL TYPE: 115 mm brick wall", "STRUCTURE FRP 1 HR", "CO2 suppression to genset room"], []),
              ("SITE PLAN", "A-002",
               ["FIRE HYDRANT H1", "FIRE HYDRANT H2", "BREECHING INLET BI1", "FIRE ENGINE ACCESS ROAD 6m",
                f"RAMP GRADIENT {ramp}"], ["yellow"])]
    for i, lv in enumerate(levels):
        tags = ["FD1", "FD2", "HR1", "FE1", "ST1", "ST2"] + list((extra_tags or {}).get(lv, []))
        sheets.append((f"{lv} FLOOR PLAN", f"A-1{i:02d}", floor_common(lv, td=td, tags=tags), ["red"]))
    sheets = [(t, n, [x for x in notes if not any(d.lower() in x.lower() for d in drop)], c)
              for t, n, notes, c in sheets]
    return write_set(path, sheets)


def active_set(path, levels=("GROUND", "FIRST"), yellow=False, extra_tags=None, sprinklered=False,
               drop=()):
    sheets = [("SITE PLAN (FIRE PROTECTION)", "M-001",
               ["H1 H2 BI1", "WATER METER TO HYDRANT TANK", "HYDRANT MAIN 150 dia"],
               ["yellow"] if yellow else [])]
    for i, lv in enumerate(levels):
        tags = ["FD1", "FD2", "HR1", "FE1", "ST1", "ST2"] + list((extra_tags or {}).get(lv, []))
        notes = floor_common(lv, tags=tags) + ["PUMP ROOM", "SMOKE DETECTOR"]
        if sprinklered:
            notes.append("SPRINKLER HEADS AND BRANCH PIPES")
        sheets.append((f"{lv} FLOOR PLAN", f"M-1{i:02d}", notes, []))
    sheets.append(("HOSE REEL AND HYDRANT SCHEMATIC", "M-201",
                   ["HOSE REEL SCHEMATIC", "SPECIFICATION SCHEDULE", "MAIN FIRE ALARM CONTROL PANEL"], []))
    sheets = [(t, n, [x for x in notes if not any(d.lower() in x.lower() for d in drop)], c)
              for t, n, notes, c in sheets]
    return write_set(path, sheets)


def form(tmp, passive=None, active=None, **kw):
    import yaml
    d = {"project": "Test office", "state": "Johor", "purpose_group": "IV", "sprinklered": False,
         "top_floor_m": 8.0, "volume_m3": 12000, "storeys": 2, "floor_area_m2": 800,
         "gross_area_m2": 1600, "submission": "passive", "sets": {}}
    d.update(kw)
    if passive:
        d["sets"]["passive"] = [passive]
    if active:
        d["sets"]["active"] = [active]
    p = tmp / "project.yaml"
    p.write_text(yaml.safe_dump(d))
    return str(p)
