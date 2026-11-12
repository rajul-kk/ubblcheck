"""Check an IFC model: run the IDS rule pack, then the UBBL schedule rules.

The IDS pass checks the model *carries* the data; the rule pass checks the
data *complies*: compartment sizes (Fifth Schedule), declared travel distances,
occupant load against exit capacity (Seventh Schedule), compartment floors
above 30 m (by-law 137), fire door ratings (by-law 162), element fire resistance
(Ninth Schedule), and the fire-fighting systems the building height or sprinkler
flag calls for (by-law 229-231).
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass

import ifcopenshell
from ifctester import ids

from . import rooms, ubbl
from .findings import Finding
from .model import PSET, Facts, common, host_wall, is_floor_slab, label, named, not_floor, objects, prop, rating

IDS_DIR = os.path.join(os.path.dirname(__file__), "ids")


# ---------------------------------------------------------------- IDS pass

def _identifiers(path: str) -> list[str | None]:
    """ifctester drops the identifier attribute when parsing; read it here."""
    ns = "{http://standards.buildingsmart.org/IDS}"
    root = ET.parse(path).getroot()
    return [s.get("identifier") for s in root.iter(ns + "specification")]


def run_ids(model: ifcopenshell.file, ids_paths: list[str]) -> list[Finding]:
    out = []
    for path in ids_paths:
        spec_doc = ids.open(path)
        idents = _identifiers(path)
        spec_doc.validate(model)
        for k, spec in enumerate(spec_doc.specifications):
            if spec.status:
                continue
            ident = (idents[k] if k < len(idents) else None) or spec.name
            failed = getattr(spec, "failed_entities", set())
            if not spec.applicable_entities and spec.minOccurs and int(spec.minOccurs) > 0:
                out.append(Finding(f"IDS-{ident}", "error",
                                   f"{spec.name}: no applicable objects in the model ({spec.description})",
                                   spec.instructions or "", []))
                continue
            reasons = []
            for req in spec.requirements:
                for fail in getattr(req, "failures", []):
                    reasons.append(fail.get("reason") if isinstance(fail, dict) else getattr(fail, "reason", ""))
            where = [e.GlobalId for e in failed if hasattr(e, "GlobalId")]
            out.append(Finding(f"IDS-{ident}", "error",
                               f"{spec.name}: {len(where)} object(s) fail. {spec.description}",
                               spec.instructions or "", sorted(where),
                               {"reasons": sorted({r for r in reasons if r})[:5]}))
    return out


# ---------------------------------------------------------------- rule pass

def check_rules(model: ifcopenshell.file) -> list[Finding]:
    f = Facts(model)
    out: list[Finding] = []
    if f.group not in ubbl.PURPOSE_GROUPS:
        out.append(Finding("IFC-GROUP", "error",
                           (f"Building purpose group {f.group!r} is not one of I-VIII" if f.group else
                            "Building purpose group is not declared (MY_FireSafety.PurposeGroup); the UBBL "
                            "rules that depend on it were skipped"),
                           "UBBL by-law 134, Fifth Schedule",
                           [f.building.GlobalId] if f.building else []))
        return out
    if f.height_estimated:
        out.append(Finding("IFC-HEIGHT", "info",
                           f"BuildingHeight not given; using top storey elevation {f.height_m:.1f} m "
                           "(lower than the by-law 135(a) height)", "UBBL by-law 135(a)", []))
    for rule in (_compartments, _travel, _occupancy_uses, _exits, _compartment_floors, _fire_doors,
                 _door_closers, _ninth, _systems, _tenth, _tall_building_rooms, _foam_inlets, _smoke_vents):
        out += rule(f)
    return out


def _compartments(f: Facts):
    out = []
    lim = ubbl.compartment_limit(f.group, f.height_m, len(f.occupied) <= 1, f.sprinklered)
    if lim is None:
        return out
    sizes = f.sizes
    for (cid, sid), a in sorted(sizes.comp_storey_area.items(),
                                key=lambda kv: (kv[0][0], f.elev(f.model.by_id(kv[0][1])))):
        st = f.model.by_id(sid)
        if a > lim.area_m2:
            out.append(Finding("IFC-COMP-AREA", "error",
                               f"Compartment {cid} on {label(st)}: storey area {a:.0f} m2 exceeds "
                               f"{lim.area_m2:.0f} m2 for purpose group {f.group} at {f.height_m:.1f} m",
                               lim.clause, sizes.comp_members[cid], {"area_m2": a, "limit_m2": lim.area_m2}))
    for cid, v in sorted(sizes.comp_volume.items()):
        if v > lim.volume_m3:
            out.append(Finding("IFC-COMP-VOL", "error",
                               f"Compartment {cid}: volume {v:.0f} m3 exceeds {lim.volume_m3:.0f} m3",
                               lim.clause, sizes.comp_members[cid], {"volume_m3": v, "limit_m3": lim.volume_m3}))
    if sizes.no_volume and lim.volume_m3 != ubbl.INF:
        out.append(Finding("IFC-COMP-VOL-DATA", "warning",
                           f"Volume limit {lim.volume_m3:.0f} m3 applies but spaces in compartment(s) "
                           f"{', '.join(sorted(sizes.no_volume))} have no volume or height quantity",
                           lim.clause, []))
    return out


# ---------------------------------------------------------------- Ninth Schedule

def _compartments_of(f: Facts, el, st) -> list:
    """The compartments an element of structure is in or bounds, for the Ninth Schedule.

    In order: the element's own MY_FireSafety.CompartmentId (several separated by commas, for a
    wall between compartments); else the compartments of the spaces its plan footprint touches;
    else every compartment on its storey, so the element is held to the longest of their periods.
    [None] means the building is not divided into compartments."""
    sizes = f.sizes
    own = prop(el, PSET, "CompartmentId")
    if own is not None:
        ids_ = [c.strip() for c in re.split(r"[,;]", str(own)) if c.strip() in sizes.comp_area]
        if ids_:
            return ids_
    on_storey = sorted(sizes.storey_comps.get(st.id(), ()))
    if len(on_storey) > 1:
        fp = f.footprint(el)
        if fp is not None:
            near = fp.buffer(0.3)
            touched = set()
            for sid, sp, cid in sizes.comp_spaces:
                if sid == st.id() and cid not in touched:
                    sfp = f.footprint(sp)
                    if sfp is not None and sfp.intersects(near):
                        touched.add(cid)
            if touched:
                return sorted(touched)
    if on_storey:
        return on_storey
    return list(sizes.comp_area) or [None]


_KIND = {"IfcColumn": "column", "IfcBeam": "beam", "IfcSlab": "floor", "IfcWall": "wall"}


def _structure(f: Facts):
    """(element, kind, storey) for each element of structure the Ninth Schedule covers: columns,
    beams, floor slabs, and walls that are loadbearing, compartment or separating walls."""
    for cls, kind in _KIND.items():
        for el in f.model.by_type(cls):
            if kind == "floor" and not is_floor_slab(el):
                continue
            if kind == "wall" and not (common(el, "LoadBearing") is True or common(el, "Compartmentation") is True
                                       or prop(el, PSET, "SeparatingWall") is True):
                continue
            st = f.storey_of(el)
            if st is not None:
                yield el, kind, st


def _exempt_single_storey(el, kind, compartment) -> bool:
    """By-law 216(1): in a single-storey building, the structural frame, beams and columns, and internal
    loadbearing walls that are not compartment walls, need no Ninth Schedule period.  (A beam or
    column in or carrying a wall still needs that wall's period; the model can't say which those are,
    so the info finding names the condition.)"""
    if kind in ("column", "beam"):
        return True
    return (kind == "wall" and not compartment and common(el, "IsExternal") is not True
            and prop(el, PSET, "SeparatingWall") is not True)


def _wall_minimum(el) -> tuple[int, str]:
    """By-law 214(1): minutes a wall needs whatever its Ninth Schedule line, and why (0 if none)."""
    if prop(el, PSET, "SeparatingWall") is True:
        return ubbl.SEPARATING_WALL_MIN, "separating wall"
    if common(el, "IsExternal") is True and common(el, "LoadBearing") is True:
        return ubbl.EXTERNAL_WALL_MIN, "loadbearing external wall"
    return 0, ""


@dataclass(frozen=True)
class _Need:
    """The period one element needs, and where it comes from."""
    minutes: int
    clause: str
    line: str           # Ninth Schedule line, or what kind of wall by-law 214 holds it to
    basis: str = ""     # compartment or building the line was chosen from (Ninth Schedule only)


class _NinthLines:
    """Ninth Schedule lines by compartment, looked up once each.  Compartments larger than every
    line are collected in `too_big`."""

    def __init__(self, f: Facts):
        self.f = f
        self.lines, self.too_big = {}, []

    def __call__(self, cid):
        if cid not in self.lines:
            f = self.f
            label_, area, vol = f.sizes.basis(cid)
            self.lines[cid] = ubbl.fire_resistance(f.group, f.height_m, area, vol, f.sizes.storeys,
                                                   f.sprinklered, compartment=cid is not None)
            if self.lines[cid] is None:
                self.too_big.append((label_, area, vol))
        return self.lines[cid]


def _element_need(f: Facts, el, kind, st, compartment, lines: _NinthLines) -> _Need | None:
    """The strictest period over every compartment the element is in (by-law 216(2)) and, for a
    wall, by-law 214(1).  None when every compartment is oversized (IFC-FRP-SIZE)."""
    basement = f.is_basement(st)
    over_basement = (kind == "floor" and bool(f.basements)
                     and st == min(f.above_ground, key=f.elev, default=None))
    barea = f.sizes.storey_area.get(st.id()) if basement else None
    needs = []
    for cid in _compartments_of(f, el, st):
        req = lines(cid)
        if req is not None:
            needs.append(_Need(ubbl.element_frp(req, kind, basement or over_basement, compartment, barea),
                               ubbl.NINTH_CLAUSE, req.line, f.sizes.basis(cid)[0]))
    if kind == "wall":
        least, why = _wall_minimum(el)
        if least:
            needs.append(_Need(least, ubbl.WALL_MIN_CLAUSE, why))
    return max(needs, key=lambda n: n.minutes, default=None)


def _ninth(f: Facts):
    """Ninth Schedule periods for elements of structure.  The line is chosen from the size of the
    compartment each element is in (or bounds); without compartments, from the building."""
    if not f.sizes.storey_area:
        return []
    lines = _NinthLines(f)
    single = f.sizes.storeys <= 1 and not f.basements
    short, unread, exempt = defaultdict(list), defaultdict(list), defaultdict(int)
    for el, kind, st in _structure(f):
        compartment = common(el, "Compartmentation") is True
        if single and _exempt_single_storey(el, kind, compartment):
            exempt[kind] += 1
            continue
        have = rating(el)
        if have is None:
            if not compartment:     # compartment walls and floors: the IDS pack (MY-P08, MY-P09) reports them
                unread[kind].append(el)
            continue
        need = _element_need(f, el, kind, st, compartment, lines)
        if need is not None and have < need.minutes:
            short[(st, kind, need)].append((el, have))
    return (_ninth_notes(f, exempt, lines.too_big, unread)
            + [_frp_short(label(st), kind, need, els) for (st, kind, need), els
               in sorted(short.items(), key=lambda kv: (f.elev(kv[0][0]), kv[0][1], kv[0][2].minutes, kv[0][2].line))])


def _frp_short(storey, kind, need: _Need, els) -> Finding:
    worst = min(h for _, h in els)
    gids = [e.GlobalId for e, _ in els]
    if need.clause == ubbl.WALL_MIN_CLAUSE:
        return Finding("IFC-FRP", "error", f"{storey}: {len(els)} {need.line}(s) below the {need.minutes} min "
                       f"by-law 214(1) requires (lowest {worst} min)", need.clause, gids,
                       {"required_min": need.minutes, "lowest_min": worst, "line": "by-law 214(1)"})
    return Finding("IFC-FRP", "error", f"{storey}: {len(els)} {kind}(s) below the {need.minutes} min the Ninth "
                   f"Schedule requires (lowest {worst} min; {need.line}; {need.basis})", need.clause, gids,
                   {"required_min": need.minutes, "lowest_min": worst, "line": need.line, "basis": need.basis})


def _ninth_notes(f: Facts, exempt, too_big, unread) -> list[Finding]:
    """What the Ninth Schedule check left out, and why: exempt, oversized, or unreadable."""
    out = []
    if exempt:
        what = ", ".join(f"{n} {k}{'s' if n > 1 else ''}" for k, n in sorted(exempt.items()))
        out.append(Finding("IFC-FRP-SINGLE", "info",
                           f"Single-storey building: {what} need no fire resistance period (by-law 216(1)), "
                           "except a beam or column in or carrying a wall, which needs that wall's period",
                           "UBBL by-law 216(1)", [], {"exempt": dict(exempt)}))
    if too_big:
        what = "; ".join(f"{lb} ({area:.0f} m2 storey, {vol:.0f} m3)" for lb, area, vol in too_big[:6])
        more = f" and {len(too_big) - 6} more" if len(too_big) > 6 else ""
        out.append(Finding("IFC-FRP-SIZE", "info",
                           f"Larger than every Ninth Schedule line for group {f.group} at {f.height_m:.1f} m: "
                           f"{what}{more}. Each must be compartmented (see IFC-COMP-AREA / IFC-COMP-VOL), so the "
                           "periods of their elements are not checked", ubbl.NINTH_CLAUSE, [],
                           {"oversized": [{"basis": lb, "area_m2": round(area), "volume_m3": round(vol)}
                                          for lb, area, vol in too_big]}))
    if unread:
        n = sum(map(len, unread.values()))
        out.append(Finding("IFC-FRP-DATA", "warning",
                           f"{n} element(s) of structure have no FireRating the checker can read "
                           f"({', '.join(f'{len(v)} {k}' for k, v in sorted(unread.items()))}); their Ninth "
                           "Schedule period was not checked. Use minutes (60, FRP 60 min, REI 60) or hours (1 HR)",
                           ubbl.NINTH_CLAUSE, [e.GlobalId for v in unread.values() for e in v],
                           {"by_kind": {k: len(v) for k, v in unread.items()}}))
    return out


# ---------------------------------------------------------------- means of escape

def _travel(f: Facts):
    lim = f.travel
    if lim is None:
        return [Finding("IFC-TRAVEL-USE", "warning", f.travel_error, "UBBL Seventh Schedule", [])]
    out = []
    if lim.travel_m is None:
        return out
    for sp in f.model.by_type("IfcSpace"):
        d = prop(sp, PSET, "MaxTravelDistance")
        if d is None:
            continue
        d = float(d) * f.scale
        if d > lim.travel_m + 1e-9:
            out.append(Finding("IFC-TRAVEL", "error",
                               f"Space {label(sp)}: travel distance {d:.1f} m exceeds "
                               f"{lim.travel_m:g} m ({'sprinklered' if f.sprinklered else 'unsprinklered'}, "
                               f"group {f.group})", lim.clause, [sp.GlobalId],
                               {"travel_m": d, "limit_m": lim.travel_m}))
        dead = prop(sp, PSET, "DeadEndDistance")
        if dead is not None and lim.dead_end_m is not None and float(dead) * f.scale > lim.dead_end_m:
            out.append(Finding("IFC-DEADEND", "error",
                               f"Space {label(sp)}: dead end {float(dead) * f.scale:.1f} m "
                               f"exceeds {lim.dead_end_m:g} m", lim.clause, [sp.GlobalId]))
    return out


def _exits(f: Facts):
    out = []
    if f.group not in ubbl.EXIT_CAPACITY:
        return out
    if not f.exits_declared:
        return [Finding("IFC-EXIT-DATA", "warning", "No door states whether it is a fire exit "
                        "(Pset_DoorCommon.FireExit); exit count and width were not checked",
                        "UBBL by-law 175-177", [])]
    load = defaultdict(int)
    for sp in f.model.by_type("IfcSpace"):
        st = f.storey_of(sp)
        if st is not None:
            load[st] += f.persons(sp) or 0      # an unknown use is reported by _occupancy_uses
    for st, persons in sorted(load.items(), key=lambda kv: f.elev(kv[0])):
        storey = label(st)
        ds, via_stairs = f.exits.get(st, ([], False))
        gids = [d.GlobalId for d in ds]
        noun = "escape stair" if via_stairs else "exit door"
        if persons > 0 and len(ds) == 1 and f.single_stair_allowed:
            out.append(Finding("IFC-EXIT-SINGLE", "warning",
                               f"{storey}: one exit ({noun}). By-law 194 allows a single staircase here (topmost "
                               f"floor {f.top_floor_m:.1f} m, at most {ubbl.SINGLE_STAIR_TOP_M} m) provided that: "
                               + "; ".join(ubbl.SINGLE_STAIR_CONDITIONS),
                               "UBBL by-law 194", gids, {"top_floor_m": f.top_floor_m}))
        elif persons > 0 and len(ds) < ubbl.MIN_EXITS:
            out.append(Finding("IFC-EXIT-COUNT", "error",
                               f"{storey}: {len(ds)} {noun}(s) for {persons} persons; minimum is {ubbl.MIN_EXITS}",
                               "UBBL by-law 177(d)", gids))
        if via_stairs:
            continue                # stair widths are not in the model in a form the checker can read
        need = ubbl.exit_units(persons, f.group, "door", f.use) if persons else 0
        have = sum(ubbl.width_units((d.OverallWidth or 0) * f.scale * 1000) for d in ds)
        if have < need:
            out.append(Finding("IFC-EXIT-WIDTH", "error",
                               f"{storey}: occupant load {persons} needs {need} units of 550 mm exit width; "
                               f"exit doors give {have}", "UBBL by-law 175-176, Seventh Schedule", gids,
                               {"persons": persons, "units_needed": need, "units_given": have}))
    return out


def _occupancy_uses(f: Facts) -> list[Finding]:
    """Spaces whose OccupancyUse names no Seventh Schedule row: their occupants are not counted."""
    bad = [sp for sp in f.model.by_type("IfcSpace")
           if prop(sp, PSET, "OccupancyUse") is not None and not not_floor(sp)
           and ubbl.load_row(f.group, prop(sp, PSET, "OccupancyUse")) is None]
    if not bad:
        return []
    uses = sorted({str(prop(sp, PSET, "OccupancyUse")) for sp in bad})
    rows = sorted(k.split(".", 1)[1] for k in ubbl.OCCUPANT_LOAD if k.startswith(f"{f.group}."))
    return [Finding("IFC-OCCUPANCY", "error",
                    f"{len(bad)} space(s) have an OccupancyUse with no Seventh Schedule row for group {f.group} "
                    f"({', '.join(uses)}); their occupants are not counted in the exit checks. "
                    f"Rows for this group: {', '.join(rows) or 'none (leave OccupancyUse empty)'}",
                    "UBBL by-law 175, Seventh Schedule", [sp.GlobalId for sp in bad], {"uses": uses})]


# ---------------------------------------------------------------- compartments, doors

def _compartment_floors(f: Facts):
    out = []
    if f.height_m <= ubbl.COMPARTMENT_FLOOR_HEIGHT_M:
        return out
    for sl in f.model.by_type("IfcSlab"):
        if not is_floor_slab(sl):
            continue
        st = f.storey_of(sl)
        if st is None or f.elev(st) <= ubbl.COMPARTMENT_FLOOR_ABOVE_M:
            continue
        if common(sl, "Compartmentation") is not True:
            out.append(Finding("IFC-COMP-FLOOR", "error",
                               f"Building is {f.height_m:.1f} m high; floor on {label(st)} "
                               f"({f.elev(st):.1f} m above ground) must be a compartment floor",
                               "UBBL by-law 137", [sl.GlobalId]))
    return out


def _fire_doors(f: Facts):
    out = []
    for d in f.model.by_type("IfcDoor"):
        wall = host_wall(d)
        if wall is None or not wall.is_a("IfcWall") or common(wall, "Compartmentation") is not True:
            continue
        wr, dr = rating(wall), rating(d)
        name = label(d)
        if dr is None:
            out.append(Finding("IFC-FIREDOOR", "error",
                               f"Door {name} is in compartment wall {label(wall)} but has no fire rating",
                               "UBBL by-law 162(1)-(2)", [d.GlobalId, wall.GlobalId]))
        elif dr < ubbl.FIRE_DOOR_FRP_MIN:
            out.append(Finding("IFC-FIREDOOR", "error", f"Door {name}: FRP {dr} min is below half an hour",
                               "UBBL by-law 162(3)", [d.GlobalId]))
        elif wr is not None and dr < wr:
            out.append(Finding("IFC-FIREDOOR-WALL", "warning",
                               f"Door {name}: FRP {dr} min is less than its compartment wall's {wr} min; "
                               "confirm against the Ninth Schedule", "UBBL by-law 162(2), Ninth Schedule",
                               [d.GlobalId, wall.GlobalId]))
    return out


_CLOSER_CLAUSE = {"fire door": "164(1)", "exit door": "173(2)"}


def _door_closers(f: Facts):
    """Fire doors (rated half an hour or more, by-law 164(1)) and exit doors (by-law 173(2)) close by
    themselves.  Declared not self-closing is an error; doors that don't say are listed in one warning."""
    out, unknown = [], []
    for d in f.model.by_type("IfcDoor"):
        r = rating(d)
        why = [w for w, applies in (("fire door", r is not None and r >= ubbl.FIRE_DOOR_FRP_MIN),
                                    ("exit door", common(d, "FireExit") is True)) if applies]
        if not why:
            continue
        closing = common(d, "SelfClosing")
        if closing is False:
            out.append(Finding("IFC-DOORCLOSER", "error",
                               f"Door {label(d)} ({' and '.join(why)}) is declared not self-closing",
                               _closer_clause(why), [d.GlobalId]))
        elif closing is None:
            unknown.append((d, why))
    if unknown:
        kinds = sorted({w for _, why in unknown for w in why})
        out.append(Finding("IFC-DOORCLOSER-DATA", "warning",
                           f"{len(unknown)} {' or '.join(kinds)}(s) don't say whether they are self-closing "
                           "(Pset_DoorCommon.SelfClosing)", _closer_clause(kinds), [d.GlobalId for d, _ in unknown]))
    return out


def _closer_clause(why) -> str:
    return "UBBL by-law " + ", ".join(c for w, c in _CLOSER_CLAUSE.items() if w in why)


# ---------------------------------------------------------------- rooms found by name

def _tall_building_rooms(f: Facts) -> list[Finding]:
    """By-law 238: over 30.5 m, a command and control centre.  By-law 242(a): each fire-fighting
    access lobby at least 5.57 m2.  A missing room is a warning, since the checker only knows rooms
    by their names."""
    out = []
    spaces = f.model.by_type("IfcSpace")
    if f.height_m > ubbl.COMMAND_CENTRE_HEIGHT_M and not any(named(sp, rooms.COMMAND_CENTRE) for sp in spaces):
        out.append(Finding("IFC-COMMAND-CENTRE", "warning",
                           f"Building is {f.height_m:.1f} m high (over {ubbl.COMMAND_CENTRE_HEIGHT_M} m) and no space "
                           "is named as a command and control centre (e.g. 'Fire Command Centre', 'Bilik Kawalan "
                           "Bomba')", "UBBL by-law 238", [f.building.GlobalId] if f.building else []))
    for sp in spaces:
        if not named(sp, rooms.FIRE_LOBBY):
            continue
        a = f.floor_area(sp)
        if a <= 0:
            out.append(Finding("IFC-FIRE-LOBBY-DATA", "warning",
                               f"Fire-fighting access lobby {label(sp)} has no floor area the checker can read; "
                               f"its {ubbl.FIRE_LOBBY_MIN_M2} m2 minimum was not checked", "UBBL by-law 242(a)", [sp.GlobalId]))
        elif a < ubbl.FIRE_LOBBY_MIN_M2:
            out.append(Finding("IFC-FIRE-LOBBY", "error",
                               f"Fire-fighting access lobby {label(sp)}: {a:.2f} m2, "
                               f"under the {ubbl.FIRE_LOBBY_MIN_M2} m2 minimum", "UBBL by-law 242(a)",
                               [sp.GlobalId], {"area_m2": a}))
    return out


def _foam_inlets(f: Facts) -> list[Finding]:
    """By-law 233: in a building without automatic sprinklers, a boiler room or storage area below
    ground (below the ground storey) needs a foam inlet.  An inlet is a fire suppression terminal
    typed FOAMINLET (on itself or its type), or a distribution element named as a foam inlet, on
    itself or its type.  Any inlet in the model counts: models don't link inlets to the rooms they
    serve.  A warning: architectural models often leave inlets out."""
    if f.sprinklered:
        return []
    below = [sp for sp in f.model.by_type("IfcSpace")
             if (st := f.storey_of(sp)) is not None and f.elev(st) < 0 and not not_floor(sp)
             and named(sp, rooms.BOILER_OR_STORE)]
    if not below:
        return []
    if _terminals(f, "FOAMINLET") or any(named(e, rooms.FOAM_INLET) for e in f.model.by_type("IfcDistributionElement")):
        return []
    shown = ", ".join(label(sp) for sp in below[:6]) + (" ..." if len(below) > 6 else "")
    return [Finding("IFC-FOAM-INLET", "warning",
                    f"Boiler room or storage below ground ({shown}) in a building without automatic sprinklers, "
                    "and no foam inlet is modelled (IfcFireSuppressionTerminal typed FOAMINLET, or named "
                    "'foam inlet')", "UBBL by-law 233", [sp.GlobalId for sp in below])]


def _smoke_vents(f: Facts) -> list[Finding]:
    """By-law 249: underground storeys and windowless buildings have smoke venting for the exits.  A
    vent is any element named as a smoke vent or extract; any one in the model counts, since models
    don't link vents to the floors they serve.  Large-area factories are left out: the by-law sets no
    size.  A warning: architectural models often leave vents out."""
    under = [st for st in f.basements if st in f.floor_storeys]
    why = [f"underground storey {label(st)}" for st in under]
    if f.floor_storeys and not any(f.model.by_type(c) for c in ("IfcWindow", "IfcCurtainWall")):
        why.append("no windows or glazed walls in the building")
    if not why or any(named(e, rooms.SMOKE_VENT) for c in _VENT_CLASSES for e in f.model.by_type(c)):
        return []
    return [Finding("IFC-SMOKE-VENT", "warning",
                    f"Smoke venting is required ({'; '.join(why)}) and no smoke vent or extract is modelled "
                    "(an element named e.g. 'Smoke Vent', 'Smoke Extract Fan', 'Pelega Asap')", "UBBL by-law 249",
                    [st.GlobalId for st in under] or ([f.building.GlobalId] if f.building else []))]


# where vents are modelled: fans, dampers and air terminals, roof vents and openable windows, proxies
_VENT_CLASSES = ("IfcDistributionElement", "IfcWindow", "IfcDoor", "IfcBuildingElementProxy", "IfcOpeningElement")


# ---------------------------------------------------------------- active systems

def _terminals(f: Facts, ptype):
    return objects(f.model, "IfcFireSuppressionTerminal", (ptype,))


def _systems(f: Facts):
    out = []
    top = f.top_floor_m
    if f.sprinklered and not _terminals(f, "SPRINKLER"):
        out.append(Finding("IFC-SPRINKLER", "warning",
                           "Building is declared sprinklered but the model has no sprinkler heads "
                           "(fine for an architectural model; needed in the active model)",
                           "UBBL by-law 136 proviso, 225(1)", [f.building.GlobalId]))
    inlets = _terminals(f, "BREECHINGINLET")
    if top > ubbl.RISER_HEIGHT_M:
        if not inlets:
            out.append(Finding("IFC-RISER", "warning",
                               f"Topmost floor is {top:.1f} m above ground: a "
                               f"{'wet' if top > ubbl.WET_RISER_HEIGHT_M else 'dry'} rising system is required; "
                               "no breeching inlet is modelled", "UBBL by-law 229(1), 230(1), 231", []))
        if not any(prop(t, "Pset_TransportElementElevator", "FireFightingLift") is True
                   for t in f.model.by_type("IfcTransportElement")):
            out.append(Finding("IFC-FIRELIFT", "warning",
                               f"Topmost floor is {top:.1f} m above ground: a fire lift is required; none is "
                               "flagged (Pset_TransportElementElevator.FireFightingLift)", "UBBL by-law 229(5), 243", []))
    if top > ubbl.WET_RISER_HEIGHT_M and inlets:
        out.append(Finding("IFC-WETRISER", "info",
                           f"Topmost floor is {top:.1f} m above ground: the rising system must be wet, not dry",
                           "UBBL by-law 231", []))
    return out


# Objects that show a Tenth Schedule system is in the model: code -> (IFC class, predefined types, name)
_TENTH_OBJECTS = {
    "G": ("IfcFireSuppressionTerminal", ("HOSEREEL",), "hose reels (IfcFireSuppressionTerminal HOSEREEL)"),
    "H": ("IfcFireSuppressionTerminal", ("FIREHYDRANT",), "hydrants (IfcFireSuppressionTerminal FIREHYDRANT)"),
    "1": ("IfcSensor", ("SMOKESENSOR", "HEATSENSOR"), "smoke or heat detectors (IfcSensor)"),
    "2": ("IfcAlarm", ("BREAKGLASSBUTTON", "MANUALPULLBOX", "BELL", "SIREN"),
          "break glass units or alarm bells (IfcAlarm)"),
}


def tenth_inputs(f: Facts) -> dict:
    """The building's size as the Tenth Schedule reads it."""
    sizes = f.sizes
    occupied = {s.id() for s in f.occupied}
    rooms_ = prop(f.building, PSET, "Rooms") if f.building else None
    return {"storeys": len(f.occupied), "height_m": f.height_m,
            "floor_area_m2": sizes.building_area,
            "total_area_m2": sum(a for sid, a in sizes.storey_area.items() if sid in occupied),
            "volume_m3": max(sizes.comp_volume.values(), default=0.0) or sizes.building_volume,
            "rooms": int(rooms_) if rooms_ is not None else None}


def _tenth(f: Facts) -> list[Finding]:
    """Fire extinguishment and alarm systems the Tenth Schedule line requires, against the objects in
    the model.  Emergency lighting and alarm types 3-4 have no IFC object here and are not checked."""
    gid = [f.building.GlobalId] if f.building else []
    try:
        use = ubbl.tenth_use(f.group, f.use, prop(f.building, PSET, "TenthUse") if f.building else None)
    except KeyError as e:
        return [Finding("IFC-TENTH", "warning", f"{e.args[0]}: set MY_FireSafety.TenthUse on the building",
                        ubbl.TENTH_CLAUSE, gid)]
    look = ubbl.tenth(use, **tenth_inputs(f))
    if not look.decided:
        why = (f"needs {', '.join(look.missing)} to choose a line" if look.missing
               else "has no line for this building's size")
        return [Finding("IFC-TENTH", "info", f"Tenth Schedule, {look.title}: {why}; active systems not checked",
                        ubbl.TENTH_CLAUSE, gid, {"use": use, "missing": list(look.missing)})]
    row, out = look.row, []
    where = f"Tenth Schedule {row.ref} ({row.title})"
    detail = {"use": use, "line": row.ref, "requires": row.describe()}
    if row.refer:
        out.append(Finding("IFC-TENTH", "info", f"{where}: {row.refer}", row.clause, gid, detail))
    if row.requires("A") and not f.sprinklered:
        out.append(Finding("IFC-TENTH-A", "error",
                           f"{where} requires automatic sprinklers, but the building is not declared sprinklered "
                           "(Pset_BuildingCommon.SprinklerProtectionAutomatic); sprinklered limits were not used",
                           row.clause, gid, detail))
    for code, (cls, types, what) in _TENTH_OBJECTS.items():
        # sprinklers: IFC-TENTH-A above when undeclared, IFC-SPRINKLER when declared but no heads
        if row.requires(code) and not objects(f.model, cls, types):
            out.append(Finding(f"IFC-TENTH-{code}", "warning",
                               f"{where} requires {ubbl.EXTINGUISHING.get(code) or ubbl.ALARMS[code]}; the model has "
                               f"no {what} (fine for an architectural model; needed in the active model)",
                               row.clause, gid, detail))
    return out


# ---------------------------------------------------------------- entry

def default_ids() -> list[str]:
    d = os.path.abspath(IDS_DIR)
    return [os.path.join(d, n) for n in ("my-ubbl-passive.ids", "my-ubbl-active.ids")]


def check_model(model: ifcopenshell.file, ids_paths: list[str] | None = None, rules: bool = True) -> list[Finding]:
    out = run_ids(model, ids_paths if ids_paths is not None else default_ids())
    if rules:
        out += check_rules(model)
    return out


def check(path: str, ids_paths: list[str] | None = None, rules: bool = True) -> list[Finding]:
    return check_model(ifcopenshell.open(path), ids_paths, rules)
