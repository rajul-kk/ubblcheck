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

from . import geom, rooms, travel, ubbl
from .findings import Finding
from .model import PSET, Facts, common, host_wall, is_floor_slab, label, labels, named, not_floor, objects, prop, rating

ABUT_M = 10.0          # assumption: a face "abuts" access within this distance (not in UBBL)

def _z(product, scale) -> float:
    if getattr(product, "ObjectPlacement", None) is not None:
        return float(uplace.get_local_placement(product.ObjectPlacement)[2, 3]) * scale
    return float(getattr(product, "Elevation", 0.0) or 0.0) * scale


class Plan:
    """One storey in plan: floor spaces, cut walls, doors, and its exits.  The exits are the ones
    the exit count uses (Facts.exits): exit doors, or on a storey with none, the escape stairs coming
    up to it, reached at their plan outline."""

    def __init__(self, f: Facts, storey, by_storey):
        """by_storey: the storey's elements by class (_by_storey)."""
        self.storey = storey
        self.z = _z(storey, f.scale)
        cut = self.z + geom.CUT_HEIGHT
        self.spaces = []
        for sp in by_storey["IfcSpace"]:
            if not_floor(sp):
                continue
            fp = f.footprint(sp)
            if fp is not None and fp.area > 0.05:
                self.spaces.append((sp, fp))
        cuts = [(e, geom.section(f.mesh(e), cut)) for t in ("IfcWall", "IfcColumn", "IfcCurtainWall")
                for e in by_storey[t]]
        self.walls = [(e, c) for e, c in cuts if c is not None and not e.is_a("IfcColumn")]   # walls, curtain walls
        self.openings = list(by_storey["IfcDoor"]) + list(by_storey["IfcWindow"])   # with or without geometry
        self.obstacles = [c for _, c in cuts]
        self.doors = []
        for d in by_storey["IfcDoor"]:
            o = geom.door_outline(d)
            if o is not None and not o.is_empty:
                self.doors.append((d, o))
        elements, self.via_stairs = f.exits.get(storey, ([], False))
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
        return label(self.storey)

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


def plans(f: Facts) -> list[Plan]:
    """Every storey in plan, lowest first."""
    bys = _by_storey(f)
    return [Plan(f, s, bys[s.id()]) for s in f.storeys]


def _heatmap_key(pl: Plan, taken: dict) -> str:
    """The storey's name, with its GlobalId when another storey has the same name."""
    return pl.name if pl.name not in taken else f"{pl.name} ({pl.storey.GlobalId})"


def _heatmap_path(heat_dir: str, pl: Plan, taken: dict) -> str:
    """travel-<storey>.png, with the GlobalId added when that file is already another storey's."""
    stem = os.path.join(heat_dir, "travel-" + re.sub(r"[^A-Za-z0-9_-]", "_", pl.name))
    path = stem + ".png"
    if path in taken.values():
        path = f"{stem}-{re.sub(r'[^A-Za-z0-9_-]', '_', pl.storey.GlobalId)}.png"
    return path


def _travel(f: Facts, plans, heat_dir, cell, heatmaps):
    out = []
    lim = f.travel           # None: IFC-TRAVEL-USE reports why
    if lim is None or lim.travel_m is None:
        return out
    open_plan = (f.use or "").endswith("open_plan")
    if not f.exits_declared:
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
                               f"{pl.name}: its exits ({', '.join(label(e) for e in pl.exits_without_plan)}) "
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
            heatmaps[_heatmap_key(pl, heatmaps)] = travel.heatmap(fld, lim.travel_m, _heatmap_path(heat_dir, pl, heatmaps))
        for sp, poly in pl.spaces:
            rd = [o for d, o in pl.doors if o.buffer(travel.CLEARANCE).intersects(poly)]
            r = travel.space_travel(fld, poly, rd)
            if r is not None:
                out += _space_travel_findings(f, f"{pl.name} {label(sp)}", sp, poly, r, fld, lim,
                                              open_plan or prop(sp, PSET, "OpenPlan") is True)
    return out


def _exit_spacing(f: Facts, plans) -> list[Finding]:
    """By-law 174(1): where a storey needs two exits, two of them are at least 5 m apart, measured
    between the nearest edges of the openings.  Not where by-law 194 allows a single staircase."""
    if f.single_stair_allowed:
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


TOUCH_M = 0.3           # a wall this close to a room's outline may enclose it ...
ENCLOSES_M = 1.0        # ... if it runs at least this far along the outline, and further than a wall
                        # butting on end-first would (its thickness plus TOUCH_M each side)


def _encloses(outline, cut) -> bool:
    """Does a wall's cut run along a room's outline, rather than butt onto it?"""
    rect = cut.minimum_rotated_rectangle
    if isinstance(rect, Polygon) and not rect.is_empty:
        r = rect.exterior.coords
        thick = min(Point(r[0]).distance(Point(r[1])), Point(r[1]).distance(Point(r[2])))
    else:                   # a cut with no area (a line or a point) has no thickness
        thick = 0.0
    along = outline.intersection(cut.buffer(TOUCH_M)).length
    return along >= max(ENCLOSES_M, thick + 2 * TOUCH_M + 0.1)


def _risk_rooms(f: Facts, plans) -> list[Finding]:
    """By-law 139: a fire risk room is separated by fire-resisting construction.  Its enclosing walls
    (those running along its plan outline) are rated at least RISK_AREA_FRP_MIN, and the doors and
    windows in them at least half an hour.  Floors and ceilings are not judged.  Warnings, since the
    local authority sets the period and the rooms are found by name."""
    out = []
    for pl in plans:
        for sp, poly in pl.spaces:
            if not named(sp, rooms.RISK_ROOM):
                continue
            where = f"{pl.name} {label(sp)}"
            walls = [w for w, c in pl.walls if _encloses(poly.boundary, c)]
            if not walls:
                out.append(Finding("GEO-RISK-ROOM", "warning",
                                   f"{where}: fire risk room, but no wall was found along its outline, so its "
                                   "separation was not checked", "UBBL by-law 139", [sp.GlobalId]))
                continue
            ids = {w.id() for w in walls}
            openings = [o for o in pl.openings if (h := host_wall(o)) is not None and h.id() in ids]
            need = [(w, ubbl.RISK_AREA_FRP_MIN) for w in walls] + [(o, ubbl.FIRE_DOOR_FRP_MIN[0]) for o in openings]
            weak = [(e, m, n) for e, n in need if (m := rating(e)) is None or m < n]
            if weak:
                what = "; ".join(f"{label(e)} {'unrated or unreadable' if m is None else f'{m} min'}"
                                 f" (needs {need})" for e, m, need in weak[:6]) + (" ..." if len(weak) > 6 else "")
                out.append(Finding("GEO-RISK-ROOM", "warning",
                                   f"{where}: fire risk room not separated by fire-resisting construction: {what}. "
                                   "The local authority sets the period", "UBBL by-law 139",
                                   [sp.GlobalId] + [e.GlobalId for e, _, _ in weak]))
    return out


def _small_room(f: Facts, sp, area) -> bool:
    """A room for ubbl.ROOM_PERSONS or fewer, which by-law 165(3) measures from its door."""
    persons = f.persons(sp, area)
    return persons is not None and persons <= ubbl.ROOM_PERSONS


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
    declared = prop(sp, PSET, "MaxTravelDistance")
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
            fp = f.footprint(e)
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
        if prop(e, PSET, "FireApplianceAccess") is True:
            fp = f.footprint(e)
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
        r = ubbl.ACCESS_ROAD_MIN_M / 2
        wide = acc.buffer(-r + 1e-6).buffer(r)      # parts at least 12 m wide
        ring = outline.exterior
        near = ring.intersection(wide.buffer(ABUT_M)) if not wide.is_empty else None
        share = (near.length / ring.length) if near is not None and not near.is_empty else 0.0
        detail = {"volume_m3": round(vol or 0), "volume_basis": how, "perimeter_m": round(ring.length, 1),
                  "share": round(share, 3), "required": need, "abut_m": ABUT_M}
        if wide.is_empty:
            out.append(Finding("GEO-ACCESS-WIDTH", "error" if need else "warning",
                               f"Fire appliance access is nowhere {ubbl.ACCESS_ROAD_MIN_M:g} m wide", clause,
                               [e.GlobalId for e, _ in access]))
        elif share + 1e-9 < need:
            out.append(Finding("GEO-ACCESS", "error",
                               f"{share:.0%} of the building perimeter ({ring.length:.0f} m) is within {ABUT_M:g} m of "
                               f"a >= {ubbl.ACCESS_ROAD_MIN_M:g} m access; volume {vol:,.0f} m3 needs {need:.0%}",
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
            if ds and min(d for d, _ in ds) > ubbl.HYDRANT_DISTANCE_M:
                d, h = min(ds, key=lambda x: x[0])
                out.append(Finding("GEO-HYDRANT", "error",
                                   f"Nearest hydrant is {d:.0f} m from fire brigade access; limit {ubbl.HYDRANT_DISTANCE_M} m",
                                   "UBBL by-law 225(2)", [h.GlobalId]))
    return out


def _groups(f: Facts):
    out = []
    by_group = defaultdict(float)
    by_comp = defaultdict(lambda: defaultdict(float))
    comp_members = defaultdict(list)
    for sp in f.model.by_type("IfcSpace"):
        g = rooms.use_group(" ".join(labels(sp)))
        if g is None:
            continue
        a = f.floor_area(sp)
        by_group[g] += a
        cid = prop(sp, PSET, "CompartmentId")
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
        area = f.sizes.storey_area.get(b.id(), 0.0)
        if area <= ubbl.BASEMENT_FLOOR_M2:
            continue
        above = min((s for s in f.storeys if f.elev(s) > f.elev(b)), key=f.elev, default=ground)
        where = (f"Basement {label(b)} is {area:.0f} m2 (> {ubbl.BASEMENT_FLOOR_M2} m2); the floor over it "
                 f"({label(above) if above else '?'})")
        slabs = [s for s in f.model.by_type("IfcSlab")
                 if f.storey_of(s) == above and is_floor_slab(s)]
        declared = {s: common(s, "Compartmentation") for s in slabs}
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


def check(model: ifcopenshell.file, heat_dir: str | None = None, cell: float = 0.2,
          heatmaps: dict | None = None) -> list[Finding]:
    """heat_dir: write a travel-distance PNG per storey there, and record each in `heatmaps`
    (storey name -> path) when given."""
    f = Facts(model)
    out = []
    if f.group in ubbl.PURPOSE_GROUPS:
        pls = plans(f)
        out += _travel(f, pls, heat_dir, cell, {} if heatmaps is None else heatmaps)
        out += _exit_spacing(f, pls)
        out += _risk_rooms(f, pls)
    out += _access(f)
    out += _groups(f)
    out += _basement(f)
    return out
