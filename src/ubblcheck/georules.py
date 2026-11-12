"""Geometric UBBL rules on an IFC model.

Where ifccheck compares numbers the designer declared, these rules measure
them from the model's geometry:

  GEO-TRAVEL / -ROOM / -NOROUTE / -DIRECT  walking distance to storey exits, by-law 165
  GEO-DECLARED      declared MaxTravelDistance is shorter than the measured one
  GEO-AREA          declared floor area disagrees with the space geometry
  GEO-ACCESS        share of the building perimeter on a >= 12 m fire appliance access, by-law 140
  GEO-HYDRANT       a hydrant within 91.5 m of fire brigade access, by-law 225(2)
  GEO-GROUP         purpose group inferred from room use, by-law 134
  GEO-MIXED         spaces of different purpose groups share a compartment, by-law 138(c)
  GEO-BASEMENT      floor over a basement larger than 100 m2 is a compartment floor, by-law 138(d)
"""
from __future__ import annotations

import math
import os
import re
from collections import defaultdict

import ifcopenshell
import ifcopenshell.util.placement as uplace
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from . import geom, travel, ubbl
from .findings import Finding
from .ifccheck import (_FLOOR_SLABS, PSET, Facts, _prop, exits_declared, frp_minutes, host_wall, named, not_floor,
                       objects, space_labels, storey_exits)

ABUT_M = 10.0          # assumption: a face "abuts" access within this distance (not in UBBL)
ACCESS_WIDTH_M = ubbl.ACCESS_ROAD_MIN_M
HYDRANT_M = 91.5
ROOM_PERSONS = 6       # by-law 165(3)

# Room-use words -> purpose group.  Ancillary rooms carry no group.
USE_WORDS = [
    ("II", r"ward|wad|patient|pesakit|nursing|hospital|nursery|taska|creche|old folk"),
    ("III", r"bed ?room|bilik tidur|hotel|guest ?room|apartment|flat|dormitor|asrama|hostel|suite|living|dining"),
    ("IV", r"office|pejabat|admin|meeting|mesyuarat|conference|work ?station|clerk"),
    ("V", r"shop|kedai|retail|restaurant|restoran|cafe|kafe|food court|salon|barber|showroom|mart"),
    ("VI", r"factory|kilang|production|pengeluaran|workshop|bengkel|assembly line|process"),
    ("VII", r"\bhall\b|dewan|auditorium|cinema|panggung|theatre|surau|mosque|masjid|church|gereja|temple|"
            r"classroom|bilik darjah|lecture|gym|function room|banquet|lounge"),
    ("VIII", r"store|stor|storage|warehouse|gudang|car ?park|parking|letak kereta|garage|archive|loading"),
]
ANCILLARY = re.compile(r"toilet|tandas|wc|lobby|lobi|corridor|koridor|hallway|(entry|entrance) hall|foyer|"
                       r"passage|laluan|stair|tangga|lift|riser|shaft|"
                       r"plant|m&e|electrical|genset|pump|pam|janitor|pantry|ahu|duct|void", re.I)


def use_group(space) -> str | None:
    label = " ".join(space_labels(space))
    if not label or ANCILLARY.search(label):
        return None
    for g, pat in USE_WORDS:
        if re.search(pat, label, re.I):
            return g
    return None


def _z(product, scale) -> float:
    if getattr(product, "ObjectPlacement", None) is not None:
        return float(uplace.get_local_placement(product.ObjectPlacement)[2, 3]) * scale
    return float(getattr(product, "Elevation", 0.0) or 0.0) * scale


class Plan:
    """One storey in plan: floor spaces, cut walls, doors, and its exits.  The exits are the ones
    ifccheck counts (storey_exits): exit doors, or on a storey with none, the escape stairs coming
    up to it, reached at their plan outline."""

    def __init__(self, f: Facts, storey, by_storey, exits):
        """exits: (elements, by stair), this storey's entry of storey_exits(f)."""
        self.storey = storey
        self.z = _z(storey, f.scale)
        cut = self.z + geom.CUT_HEIGHT
        self.spaces = []
        for sp in by_storey["IfcSpace"]:
            if not_floor(sp):
                continue
            fp = geom.footprint(geom.mesh(sp))
            if fp is not None and fp.area > 0.05:
                self.spaces.append((sp, fp))
        cuts = [(e, geom.section(geom.mesh(e), cut)) for t in ("IfcWall", "IfcColumn", "IfcCurtainWall")
                for e in by_storey[t]]
        self.walls = [(e, c) for e, c in cuts if c is not None and not e.is_a("IfcColumn")]   # walls, curtain walls
        self.windows = list(by_storey["IfcWindow"])
        self.obstacles = [c for _, c in cuts]
        self.doors = []
        for d in by_storey["IfcDoor"]:
            o = geom.door_outline(d)
            if o is not None and not o.is_empty:
                self.doors.append((d, o))
        elements, self.via_stairs = exits
        outline = dict((d.id(), o) for d, o in self.doors)
        self.exit_elements, self.exits_without_plan = [], []
        for e in elements:
            o = outline.get(e.id()) if e.is_a("IfcDoor") else geom.outline(e)
            if o is not None and not o.is_empty:
                self.exit_elements.append((e, o))
            else:
                self.exits_without_plan.append(e)
        self.field = None

    @property
    def exits(self):
        return [o for _, o in self.exit_elements]

    @property
    def name(self):
        return self.storey.Name or self.storey.GlobalId

    def solve(self, cell):
        if not self.spaces or not self.exits:
            return None
        # a stair is walkable even where the space over it is a void left out of self.spaces
        walk = unary_union([p for _, p in self.spaces] + (self.exits if self.via_stairs else []))
        self.field = travel.field(walk, [o for o in self.obstacles if o is not None], self.exits,
                                  [o for _, o in self.doors], cell)
        return self.field


def _by_storey(f: Facts):
    out = defaultdict(lambda: defaultdict(list))
    for t in ("IfcSpace", "IfcWall", "IfcColumn", "IfcCurtainWall", "IfcDoor", "IfcWindow"):
        for e in f.model.by_type(t):
            st = f.storey_of(e)
            if st is not None:
                out[st.id()][t].append(e)
    return out


def _travel(f: Facts, plans, heat_dir, cell):
    out = []
    try:
        lim = ubbl.travel_limit(f.group, f.sprinklered, f.use)
    except KeyError:
        return out
    if lim.travel_m is None:
        return out
    open_plan = (f.use or "").endswith("open_plan")
    if not exits_declared(f):
        if any(pl.spaces for pl in plans):
            out.append(Finding("GEO-NOEXIT", "warning", "No door states whether it is a fire exit "
                               "(Pset_DoorCommon.FireExit), so no travel distance can be measured",
                               "UBBL by-law 165, 167", []))
        return out
    for pl in plans:
        if not pl.spaces:
            continue
        if not pl.exits and pl.exits_without_plan:
            out.append(Finding("GEO-EXIT-GEOM", "warning",
                               f"{pl.name}: its exits ({', '.join(e.Name or e.GlobalId for e in pl.exits_without_plan)}) "
                               "have no plan geometry the checker can read, so no travel distance can be measured",
                               "UBBL by-law 165", [e.GlobalId for e in pl.exits_without_plan]))
            continue
        if not pl.exits:
            out.append(Finding("GEO-NOEXIT", "error",
                               f"{pl.name}: no fire exit door or escape stair serves this storey, so no travel distance "
                               "can be measured",
                               "UBBL by-law 165, 167", [pl.storey.GlobalId]))
            continue
        fld = pl.solve(cell)
        if heat_dir:
            os.makedirs(heat_dir, exist_ok=True)
            travel.heatmap(fld, lim.travel_m, os.path.join(heat_dir, f"travel-{re.sub(r'[^A-Za-z0-9_-]', '_', pl.name)}.png"))
        for sp, poly in pl.spaces:
            rd = [o for d, o in pl.doors if o.buffer(travel.CLEARANCE).intersects(poly)]
            r = travel.space_travel(fld, poly, rd)
            if r is not None:
                out += _space_travel_findings(f, f"{pl.name} {sp.Name or sp.GlobalId}", sp, poly, r, fld, lim,
                                              open_plan or _prop(sp, PSET, "OpenPlan") is True)
    return out


def _exit_spacing(f: Facts, plans) -> list[Finding]:
    """By-law 174(1): where a storey needs two exits, two of them are at least 5 m apart, measured
    between the nearest edges of the openings.  Not where by-law 194 allows a single staircase."""
    if ubbl.single_stair_allowed(f.group, f.use, f.top_floor_m):
        return []
    out = []
    for pl in plans:
        if not pl.spaces or pl.via_stairs or len(pl.exit_elements) < ubbl.MIN_EXITS:
            continue                # no floor; stairs are not openings; too few exits is IFC-EXIT-COUNT
        doors = pl.exit_elements
        widest = max(a.distance(b) for i, (_, a) in enumerate(doors) for _, b in doors[i + 1:])
        if widest < ubbl.EXIT_SPACING_M:
            out.append(Finding("GEO-EXIT-SPACING", "error",
                               f"{pl.name}: the {len(doors)} exit doors are at most {widest:.1f} m apart; "
                               f"two of them must be at least {ubbl.EXIT_SPACING_M} m apart",
                               "UBBL by-law 174(1)", [d.GlobalId for d, _ in doors], {"widest_m": widest}))
    return out


# by-law 139(a)-(f), and the plant rooms Bomba treats the same way, in English and Malay
_RISK_ROOM = re.compile(r"boiler|laundr|linen|\bLPG\b|gas (store|storage|room)|gen-?set|generator room|transformer"
                        r"|substation|hazardous|\bdobi\b|dandang|janakuasa|pengubah|pencawang|bilik (linen|gas|genset)",
                        re.I)
TOUCH_M = 0.3           # a wall this close to a room's outline may enclose it ...
ENCLOSES_M = 1.0        # ... if it runs at least this far along the outline (a partition butting on is shorter)
_PSET = {"IfcWall": "Pset_WallCommon", "IfcCurtainWall": "Pset_CurtainWallCommon", "IfcDoor": "Pset_DoorCommon",
         "IfcWindow": "Pset_WindowCommon"}


def _risk_rooms(f: Facts, plans) -> list[Finding]:
    """By-law 139: a fire risk room is separated by fire-resisting construction.  Its enclosing walls
    (those running along its plan outline) are rated at least RISK_AREA_FRP_MIN, and the doors and
    windows in them at least half an hour.  Floors and ceilings are not judged.  Warnings, since the
    local authority sets the period and the rooms are found by name."""
    out = []
    for pl in plans:
        rating = {}

        def minutes(e):
            if e.id() not in rating:
                rating[e.id()] = frp_minutes(_prop(e, _PSET.get(e.is_a(), ""), "FireRating"))
            return rating[e.id()]

        for sp, poly in pl.spaces:
            if not named(sp, _RISK_ROOM):
                continue
            label = f"{pl.name} {sp.LongName or sp.Name or sp.GlobalId}"
            walls = [w for w, c in pl.walls if poly.boundary.intersection(c.buffer(TOUCH_M)).length >= ENCLOSES_M]
            if not walls:
                out.append(Finding("GEO-RISK-ROOM", "warning",
                                   f"{label}: fire risk room, but no wall was found along its outline, so its "
                                   "separation was not checked", "UBBL by-law 139", [sp.GlobalId]))
                continue
            ids = {w.id() for w in walls}
            openings = [o for o in [d for d, _ in pl.doors] + pl.windows
                        if (h := host_wall(o)) is not None and h.id() in ids]
            weak = [(e, minutes(e), need) for e, need in [(w, ubbl.RISK_AREA_FRP_MIN) for w in walls]
                    + [(o, ubbl.FIRE_DOOR_FRP_MIN[0]) for o in openings]
                    if minutes(e) is None or minutes(e) < need]
            if weak:
                what = "; ".join(f"{e.Name or e.GlobalId} {'unrated or unreadable' if m is None else f'{m} min'}"
                                 f" (needs {need})" for e, m, need in weak[:6]) + (" ..." if len(weak) > 6 else "")
                out.append(Finding("GEO-RISK-ROOM", "warning",
                                   f"{label}: fire risk room not separated by fire-resisting construction: {what}. "
                                   "The local authority sets the period", "UBBL by-law 139",
                                   [sp.GlobalId] + [e.GlobalId for e, _, _ in weak]))
    return out


def _small_room(f: Facts, sp, area) -> bool:
    """A room for ROOM_PERSONS or fewer, which by-law 165(3) measures from its door."""
    persons = f.persons(sp, area)
    return persons is not None and persons <= ROOM_PERSONS


def _space_travel_findings(f: Facts, nm, sp, poly, r, fld, lim, open_plan) -> list[Finding]:
    """By-law 165 findings for one space, from its measured travel r (travel.SpaceTravel)."""
    out = []
    gid = [sp.GlobalId]
    tol = fld.tolerance(lim.travel_m)
    if r.unreachable > 0.05:
        out.append(Finding("GEO-NOROUTE", "warning",
                           f"{nm}: {r.unreachable:.0%} of the floor has no route to an exit "
                           "(missing door, or door not cut into its wall?)",
                           "UBBL by-law 165(1)", gid, {"unreachable": r.unreachable}))
    measured, how = r.max_m, "from the most remote point"
    if _small_room(f, sp, poly.area) and r.door_m is not None and r.in_room_m is not None:
        measured, how = r.door_m, "from the room door (room for 6 or fewer, by-law 165(3))"
        if r.in_room_m > ubbl.ROOM_TRAVEL_M:
            out.append(Finding("GEO-ROOM", "error",
                               f"{nm}: {r.in_room_m:.1f} m from the remote point to the room door "
                               f"exceeds {ubbl.ROOM_TRAVEL_M} m", "UBBL by-law 165(3)", gid))
    if not math.isfinite(measured):
        return out
    detail = {"travel_m": round(measured, 2), "limit_m": lim.travel_m, "tolerance_m": round(tol, 2)}
    if measured > lim.travel_m + tol:
        out.append(Finding("GEO-TRAVEL", "error",
                           f"{nm}: walking distance {how} is {measured:.1f} m; limit {lim.travel_m:g} m",
                           lim.clause, gid, detail))
    elif measured > lim.travel_m:
        out.append(Finding("GEO-TRAVEL", "warning",
                           f"{nm}: walking distance {measured:.1f} m is over the {lim.travel_m:g} m "
                           f"limit by less than the method tolerance ({tol:.1f} m); measure by hand",
                           lim.clause, gid, detail))
    declared = _prop(sp, PSET, "MaxTravelDistance")
    if declared is not None and measured > float(declared) * f.scale + tol:
        out.append(Finding("GEO-DECLARED", "warning",
                           f"{nm}: declared travel distance {float(declared) * f.scale:.1f} m, "
                           f"measured {measured:.1f} m", "UBBL by-law 165(1)", gid, detail))
    if open_plan and r.direct_m > ubbl.DIRECT_DISTANCE_FRACTION * lim.travel_m + fld.cell:
        out.append(Finding("GEO-DIRECT", "error",
                           f"{nm}: open-plan direct distance {r.direct_m:.1f} m exceeds two-thirds "
                           f"of {lim.travel_m:g} m", "UBBL by-law 165(2)", gid))
    q, area = f.area(sp), poly.area
    if q is not None and abs(q - area) > max(0.05 * area, 0.5):
        out.append(Finding("GEO-AREA", "warning",
                           f"{nm}: declared floor area {q:.1f} m2, geometry gives {area:.1f} m2",
                           "UBBL by-law 135(b)", gid))
    return out


def _building_outline(f: Facts):
    parts = []
    for t in ("IfcSlab", "IfcWall"):
        for e in f.model.by_type(t):
            fp = geom.footprint(geom.mesh(e))
            if fp is not None:
                parts.append(fp)
    if not parts:
        return None
    u = unary_union(parts).buffer(0.05).buffer(-0.05)
    polys = geom.polygons(u)
    if not polys:
        return None
    return unary_union([Polygon(p.exterior) for p in polys])   # fill courtyards: outer perimeter only


def _building_volume(f: Facts, outline):
    vs = [geom.volume(geom.mesh(sp)) for sp in f.model.by_type("IfcSpace")]
    vs = [v for v in vs if v]
    if vs:
        return sum(vs), "sum of space volumes"
    if outline is not None and f.storeys:
        h = (f.height or max(f.elev(s) for s in f.storeys) + 3.0)
        return outline.area * h, "footprint x height"
    return None, None


def _access(f: Facts):
    out = []
    outline = _building_outline(f)
    vol, how = _building_volume(f, outline)
    access = []
    for e in f.model.by_type("IfcProduct"):
        if _prop(e, PSET, "FireApplianceAccess") is True:
            fp = geom.footprint(geom.mesh(e))
            if fp is not None:
                access.append((e, fp))
    need = ubbl.access_perimeter_share(vol or 0)
    clause = "UBBL by-law 140"
    if need and not access:
        out.append(Finding("GEO-ACCESS", "warning",
                           f"Building volume {vol:,.0f} m3 ({how}) needs {need:.0%} of its perimeter on fire appliance "
                           "access, but no element is flagged MY_FireSafety.FireApplianceAccess", clause, []))
    if access and outline is not None:
        acc = unary_union([p for _, p in access])
        r = ACCESS_WIDTH_M / 2
        wide = acc.buffer(-r + 1e-6).buffer(r)      # parts at least 12 m wide
        ring = outline.exterior
        near = ring.intersection(wide.buffer(ABUT_M)) if not wide.is_empty else None
        share = (near.length / ring.length) if near is not None and not near.is_empty else 0.0
        detail = {"volume_m3": round(vol or 0), "volume_basis": how, "perimeter_m": round(ring.length, 1),
                  "share": round(share, 3), "required": need, "abut_m": ABUT_M}
        if wide.is_empty:
            out.append(Finding("GEO-ACCESS-WIDTH", "error" if need else "warning",
                               f"Fire appliance access is nowhere {ACCESS_WIDTH_M:g} m wide", clause,
                               [e.GlobalId for e, _ in access]))
        elif share + 1e-9 < need:
            out.append(Finding("GEO-ACCESS", "error",
                               f"{share:.0%} of the building perimeter ({ring.length:.0f} m) is within {ABUT_M:g} m of "
                               f"a >= {ACCESS_WIDTH_M:g} m access; volume {vol:,.0f} m3 needs {need:.0%}",
                               clause, [e.GlobalId for e, _ in access], detail))
        hyd = objects(f.model, "IfcFireSuppressionTerminal", ("FIREHYDRANT",))
        if not hyd:
            out.append(Finding("GEO-HYDRANT", "error", "No fire hydrant in the model",
                               "UBBL by-law 225(2)", []))
        else:
            ds = []
            for h in hyd:
                p = geom.location(h)
                if p is not None:
                    ds.append((acc.distance(Point(p)), h))
            if ds and min(d for d, _ in ds) > HYDRANT_M:
                d, h = min(ds, key=lambda x: x[0])
                out.append(Finding("GEO-HYDRANT", "error",
                                   f"Nearest hydrant is {d:.0f} m from fire brigade access; limit {HYDRANT_M} m",
                                   "UBBL by-law 225(2)", [h.GlobalId]))
    return out


def _groups(f: Facts):
    out = []
    by_group = defaultdict(float)
    by_comp = defaultdict(lambda: defaultdict(float))
    comp_members = defaultdict(list)
    for sp in f.model.by_type("IfcSpace"):
        g = use_group(sp)
        if g is None:
            continue
        a = f.floor_area(sp)
        by_group[g] += a
        cid = _prop(sp, PSET, "CompartmentId")
        if cid is not None:
            by_comp[cid][g] += a
            comp_members[cid].append(sp.GlobalId)
    if by_group:
        main = max(by_group, key=by_group.get)
        share = by_group[main] / sum(by_group.values())
        if f.group in ubbl.PURPOSE_GROUPS and main != f.group:
            out.append(Finding("GEO-GROUP", "warning",
                               f"Declared purpose group {f.group} ({ubbl.PURPOSE_GROUPS[f.group]}), but room names "
                               f"suggest {main} ({ubbl.PURPOSE_GROUPS[main]}) for {share:.0%} of named floor area",
                               "UBBL by-law 134, Fifth Schedule",
                               [f.building.GlobalId] if f.building else [],
                               {"by_group_m2": {k: round(v, 1) for k, v in by_group.items()}}))
    for cid, gs in sorted(by_comp.items(), key=lambda kv: str(kv[0])):
        if len(gs) < 2:
            continue
        tot = sum(gs.values())
        main = max(gs, key=gs.get)
        others = {g: a for g, a in gs.items() if g != main and a >= 0.1 * tot}
        if others:
            out.append(Finding("GEO-MIXED", "warning",
                               f"Compartment {cid}: mainly {ubbl.PURPOSE_GROUPS[main]} but "
                               + ", ".join(f"{a:.0f} m2 {ubbl.PURPOSE_GROUPS[g]}" for g, a in others.items())
                               + "; parts used for a different purpose group need compartment separation",
                               "UBBL by-law 134 proviso, 138(c)", comp_members[cid]))
    return out


def _basement(f: Facts):
    out = []
    if not f.basements:
        return out
    ground = min(f.above_ground, key=f.elev, default=None)
    for b in f.basements:
        area = sum(f.floor_area(sp) for sp in f.model.by_type("IfcSpace") if f.storey_of(sp) == b)
        if area <= 100:
            continue
        above = min((s for s in f.storeys if f.elev(s) > f.elev(b)), key=f.elev, default=ground)
        where = f"Basement {b.Name} is {area:.0f} m2 (> 100 m2); the floor over it ({above.Name if above else '?'})"
        slabs = [s for s in f.model.by_type("IfcSlab")
                 if f.storey_of(s) == above and (s.PredefinedType or "") in _FLOOR_SLABS]
        declared = {s: _prop(s, "Pset_SlabCommon", "Compartmentation") for s in slabs}
        no = [s.GlobalId for s, v in declared.items() if v is False]
        unknown = [s.GlobalId for s, v in declared.items() if v is None]
        if no:
            out.append(Finding("GEO-BASEMENT", "error", f"{where} must be a compartment floor, but is declared "
                               "not to be (Pset_SlabCommon.Compartmentation)", "UBBL by-law 138(d)", no))
        if unknown:
            out.append(Finding("GEO-BASEMENT", "warning", f"{where} must be a compartment floor; declare it with "
                               "Pset_SlabCommon.Compartmentation", "UBBL by-law 138(d)", unknown))
        if not slabs:
            out.append(Finding("GEO-BASEMENT", "warning", f"{where} must be a compartment floor, but no floor "
                               "slab is placed on that storey", "UBBL by-law 138(d)", []))
    return out


def check(model: ifcopenshell.file, heat_dir: str | None = None, cell: float = 0.2) -> list[Finding]:
    f = Facts(model)
    out = []
    if f.group in ubbl.PURPOSE_GROUPS:
        bys = _by_storey(f)
        exits = storey_exits(f)
        plans = [Plan(f, s, bys[s.id()], exits.get(s, ([], False))) for s in f.storeys]
        out += _travel(f, plans, heat_dir, cell)
        out += _exit_spacing(f, plans)
        out += _risk_rooms(f, plans)
    out += _access(f)
    out += _groups(f)
    out += _basement(f)
    return out
