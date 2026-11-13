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
from dataclasses import dataclass
from functools import cached_property

import ifcopenshell
import ifcopenshell.util.element as uel
import ifcopenshell.util.placement as uplace
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union
from shapely.strtree import STRtree

from . import geom, rooms, travel, ubbl
from .findings import Finding
from .model import PSET, Facts, host_wall, is_floor_slab, label, labels, named, not_floor, objects
from .ratings import flame_class

ABUT_M = 10.0          # assumption: a face "abuts" access within this distance (not in UBBL)

def _z(product, scale) -> float:
    if getattr(product, "ObjectPlacement", None) is not None:
        return float(uplace.get_local_placement(product.ObjectPlacement)[2, 3]) * scale
    return float(getattr(product, "Elevation", 0.0) or 0.0) * scale


class _Wall:
    """A wall (or curtain wall) cut in plan, with what the enclosure checks ask of it, worked out once."""

    def __init__(self, el, cut, external: bool):
        self.el, self.cut, self.external = el, cut, external
        self.near = cut.buffer(TOUCH_M)
        # a thin strip's area is about half its perimeter times its thickness, whatever its shape
        # (straight, L-shaped, curved); a cut with no area has none
        self.thick = 2 * cut.area / cut.length if cut.area > 0 else 0.0


class Plan:
    """One storey in plan: floor spaces, cut walls, doors, and its exits.  The exits are the ones
    the exit count uses (Facts.exits): exit doors, or on a storey with none, the escape stairs coming
    up to it, reached at their plan outline."""

    def __init__(self, f: Facts, storey, by_storey):
        """by_storey: the storey's elements by class (_by_storey)."""
        self.storey, self.facts = storey, f
        self.z = _z(storey, f.scale)
        cut = self.z + geom.CUT_HEIGHT
        self.all_spaces = list(by_storey["IfcSpace"])    # voids and area zones too
        self.spaces = []                                 # (floor space, outline)
        for sp in self.all_spaces:
            if not not_floor(sp) and (fp := self.outline(sp)) is not None and fp.area > 0.05:
                self.spaces.append((sp, fp))
        cuts = [(e, geom.section(f.mesh(e), cut)) for t in ("IfcWall", "IfcColumn", "IfcCurtainWall")
                for e in by_storey[t]]
        self.walls = [_Wall(e, c, f.common(e, "IsExternal") is True) for e, c in cuts if c is not None and not e.is_a("IfcColumn")]
        self._wall_index = STRtree([w.near for w in self.walls]) if self.walls else None
        self.openings = [(o, f.door_outline(o)) for t in ("IfcDoor", "IfcWindow")
                         for o in by_storey[t]]      # (door or window, outline or None)
        self._coverings = by_storey["IfcCovering"]
        self.obstacles = [c for _, c in cuts]
        self.doors = [(d, o) for d, o in self.openings
                      if d.is_a("IfcDoor") and o is not None and not o.is_empty]
        elements, self.via_stairs = f.exits.get(storey, ([], False))
        outline = dict((d.id(), o) for d, o in self.doors)
        self.exit_elements, self.exits_without_plan = [], []
        for e in elements:
            o = outline.get(e.id()) if e.is_a("IfcDoor") else f.outline(e)
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

    def walls_along(self, poly) -> list["_Wall"]:
        """The walls enclosing a room: those running along its outline (_encloses)."""
        if self._wall_index is None:
            return []
        return [self.walls[i] for i in sorted(self._wall_index.query(poly.buffer(TOUCH_M)))
                if _encloses(poly, self.walls[i])]

    @cached_property
    def _ceilings(self):
        """(ceiling coverings, their outlines, an index of the outlines), worked out when first asked."""
        cs = [(c, fp) for c in self._coverings if uel.get_predefined_type(c) == "CEILING"
              and (fp := self.facts.footprint(c)) is not None and not fp.is_empty]
        return cs, (STRtree([fp for _, fp in cs]) if cs else None)

    def ceilings_over(self, poly) -> list:
        """Ceiling coverings over a room: most of the smaller of the two overlaps the other."""
        cs, index = self._ceilings
        if index is None:
            return []
        return [c for c, fp in (cs[i] for i in sorted(index.query(poly)))
                if fp.intersection(poly).area > 0.5 * min(fp.area, poly.area)]

    @property
    def direct_exit(self) -> bool:
        """Does an exit door on this storey lead straight outside (Pset_DoorCommon.IsExternal)?"""
        return any(e.is_a("IfcDoor") and self.facts.common(e, "IsExternal") is True
                   for e in [e for e, _ in self.exit_elements] + self.exits_without_plan)

    def outline(self, sp):
        """A space's plan outline, or None (Facts caches it, so ask only for the spaces you need)."""
        fp = self.facts.footprint(sp)
        return None if fp is None or fp.is_empty else fp

    def room_doors(self, poly):
        """(door, outline) for the doors in a room's walls."""
        return [(d, o) for d, o in self.doors if o.buffer(travel.CLEARANCE).intersects(poly)]

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
    for t in ("IfcSpace", "IfcWall", "IfcColumn", "IfcCurtainWall", "IfcDoor", "IfcWindow", "IfcCovering"):
        for e in f.model.by_type(t):
            st = f.storey_of(e)
            if st is None and t == "IfcCovering":       # tied to its room only (IfcRelCoversSpaces)
                st = next((f.storey_of(sp) for rel in getattr(e, "CoversSpaces", None) or ()
                           if (sp := getattr(rel, "RelatingSpace", None) or getattr(rel, "RelatedSpace", None))),
                          None)
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
            rd = [o for _, o in pl.room_doors(poly)]
            r = travel.space_travel(fld, poly, rd)
            if r is not None:
                out += _space_travel_findings(f, f"{pl.name} {label(sp)}", sp, poly, r, fld,
                                              ubbl.assembly_travel(lim, f.sprinklered)
                                              if _assembly_room(f, sp, poly) else lim,
                                              open_plan or f.prop(sp, PSET, "OpenPlan") is True)
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


TOUCH_M = 0.3           # a wall this close to a room's outline may enclose it, if it runs along the
                        # outline further than a wall butting on end-first (or touching a corner) would:
                        # its thickness plus TOUCH_M each side


def _encloses(room, wall: "_Wall") -> bool:
    """Does a wall run along a room's outline, rather than butt onto it?"""
    along = room.boundary.intersection(wall.near).length
    return along >= wall.thick + 2 * TOUCH_M + 0.1


@dataclass(frozen=True)
class _Enclosed:
    """A kind of room the by-laws want walled off from the rest of the building: found by name, its
    enclosing internal walls (those running along its plan outline; an external wall separates it
    from the outside, not from the building) rated at least `wall_min`, the doors in them at least
    half an hour, and its windows likewise, or not allowed at all.  A door or window is in a wall
    when it fills an opening in it, or, unhosted, its outline lies on the wall."""
    rule: str
    pattern: re.Pattern
    what: str
    clause: str
    wall_min: int
    windows_allowed: bool
    note: str


_ENCLOSED = (
    _Enclosed("GEO-RISK-ROOM", rooms.RISK_ROOM, "fire risk room", "UBBL by-law 139", ubbl.RISK_AREA_FRP_MIN, True,
              "The local authority sets the period"),
    _Enclosed("GEO-SHAFT", rooms.SHAFT, "protected shaft", "UBBL by-law 150", ubbl.PROTECTED_SHAFT_FRP_MIN, False,
              "Openings allowed: pipes, fire doors (lift landing doors included, 150(5)(c)) and ducts; one hour "
              "is the least period checked"),
)


def _enclosed_rooms(plans) -> list[Finding]:
    """By-law 139 (fire risk rooms) and 150 (protected shafts): the room is separated by
    fire-resisting construction.  Voids count (a lift or duct shaft is often drawn as one).  Floors
    and ceilings are not judged.  Warnings, since the rooms are found by name."""
    out = []
    for kind in _ENCLOSED:
        for pl in plans:
            for sp in pl.all_spaces:
                if named(sp, kind.pattern) and (poly := pl.outline(sp)) is not None:
                    out += _enclosure_findings(pl, sp, poly, kind)
    return out


def _enclosure_findings(pl: Plan, sp, poly, kind: _Enclosed) -> list[Finding]:
    where = f"{pl.name} {label(sp)}"
    around = pl.walls_along(poly)
    walls = [w for w in around if not w.external]
    if not walls:
        if around:          # walled by external walls only: separated from the outside, not the building
            return []
        return [Finding(kind.rule, "warning", f"{where}: {kind.what}, but no wall was found along its outline, "
                        "so its separation was not checked", kind.clause, [sp.GlobalId])]
    ids = {w.el.id() for w in walls}
    on_walls = unary_union([w.near for w in walls])
    near_room = poly.buffer(TOUCH_M)

    def in_its_walls(o, outline):
        """In an enclosing wall, and next to the room (a long wall has other rooms' doors too)."""
        drawn = outline is not None and not outline.is_empty
        if (h := host_wall(o)) is not None:
            return h.id() in ids and (not drawn or outline.intersects(near_room))
        return drawn and outline.intersects(on_walls) and outline.intersects(near_room)

    openings = [o for o, outline in pl.openings if in_its_walls(o, outline)]
    weak = []
    for e in [w.el for w in walls] + openings:
        if e.is_a("IfcWindow") and not kind.windows_allowed:
            weak.append((e, f"window, not allowed in a {kind.what}"))
            continue
        need = kind.wall_min if e.id() in ids else ubbl.FIRE_DOOR_FRP_MIN
        m = pl.facts.rating(e)
        if m is None or m < need:
            weak.append((e, f"{'unrated or unreadable' if m is None else f'{m} min'} (needs {need})"))
    if not weak:
        return []
    what = "; ".join(f"{label(e)} {why}" for e, why in weak[:6]) + (" ..." if len(weak) > 6 else "")
    return [Finding(kind.rule, "warning", f"{where}: {kind.what} not separated by fire-resisting construction: "
                    f"{what}. {kind.note}", kind.clause, [sp.GlobalId] + [e.GlobalId for e, _ in weak])]


def assembly_capacity(f: Facts, sp, area) -> tuple[int, str] | None:
    """By-law 180: (persons, how) for a room used for assembly, or None for any other room.  Fixed
    seats (MY_FireSafety.FixedSeats) count one each; otherwise the net floor area over the factor
    for the use its name gives."""
    seats = f.prop(sp, PSET, "FixedSeats")
    if seats is not None:
        return int(seats), f"{int(seats)} fixed seats"
    if named(sp, rooms.CIRCULATION):
        return None
    use = next((u for u, pat in rooms.ASSEMBLY_USE if named(sp, pat)), None)
    if use is None:
        return None
    per = ubbl.ASSEMBLY_LOAD[use]
    return math.ceil(area / per), f"{area:.0f} m2 at {per:g} m2 a person ({use.replace('_', ' ')} use)"


def _assembly_room(f: Facts, sp, poly) -> tuple[int, str] | None:
    """(persons, how) for a place of assembly (by-law 179: 100 persons or more), else None."""
    cap = assembly_capacity(f, sp, f.floor_area(sp) or poly.area)
    return cap if cap is not None and ubbl.assembly_class(cap[0]) is not None else None


def _assembly_rooms(f: Facts, plans) -> list[Finding]:
    """By-laws 179-183: a room for 100 or more persons used for assembly has the exits its class
    needs (doors in its walls, 183(d)-(f)), enough exit width at 100 persons a 552 mm unit
    (181, 183(a)-(b)), and no exit door under 700 mm (181).  The doors are those whose plan
    outline touches the room, wherever they lead; how remote they are from each other is not judged."""
    out = []
    for pl in plans:
        for sp, poly in pl.spaces:
            if (cap := _assembly_room(f, sp, poly)) is None:
                continue
            persons, how = cap
            cls, need = ubbl.assembly_class(persons), ubbl.assembly_exits(persons)
            where = f"{pl.name} {label(sp)}: class {cls} place of assembly for {persons} persons ({how})"
            doors = [d for d, _ in pl.room_doors(poly)]
            widths = [(d, d.OverallWidth * f.scale * 1000 if d.OverallWidth else None) for d in doors]
            units = sum(ubbl.assembly_units(w) for _, w in widths if w is not None)
            narrow = [d for d, w in widths if w is not None and w < ubbl.ASSEMBLY_MIN_DOOR_MM]
            unknown = [d for d, w in widths if w is None]
            gids = [sp.GlobalId] + [d.GlobalId for d in doors]
            detail = {"persons": persons, "class": cls, "exits": len(doors), "exits_needed": need, "units": units}
            if len(doors) < need:
                out.append(Finding("GEO-ASSEMBLY-EXITS", "error", f"{where} has {len(doors)} exit(s); "
                                   f"class {cls} needs at least {need}", "UBBL by-law 179, 183(d)-(f)", gids, detail))
            if units * ubbl.ASSEMBLY_PERSONS_PER_UNIT < persons:
                out.append(Finding("GEO-ASSEMBLY-WIDTH", "warning" if unknown else "error",
                                   f"{where}: its doors give {units:g} units of 552 mm, enough for "
                                   f"{units * ubbl.ASSEMBLY_PERSONS_PER_UNIT:.0f}"
                                   + (f" (no width given for {', '.join(label(d) for d in unknown)})" if unknown else ""),
                                   "UBBL by-law 181, 183(a)-(b)", gids, detail))
            if narrow:
                out.append(Finding("GEO-ASSEMBLY-WIDTH", "error",
                                   f"{where}: exit door(s) {', '.join(label(d) for d in narrow)} narrower than "
                                   f"{ubbl.ASSEMBLY_MIN_DOOR_MM} mm", "UBBL by-law 181", [d.GlobalId for d in narrow]))
    return out


def _surface_class(f: Facts, el):
    """(element, class) for the face of a wall or ceiling: its lining's class when a covering on it
    (IfcRelCoversBldgElements) states one, the worst of several, else its own; class None if unread."""
    linings = [c for rel in getattr(el, "HasCoverings", None) or () for c in rel.RelatedCoverings]
    stated = [(c, k) for c in linings if (k := flame_class(f.common(c, "SurfaceSpreadOfFlame"))) is not None]
    if stated:
        return max(stated, key=lambda ck: ck[1])
    return el, flame_class(f.common(el, "SurfaceSpreadOfFlame"))


_SURFACES = ("IfcWall", "IfcCurtainWall", "IfcCovering")     # what has a class of surface spread of flame
_PLACE_WORDS = {"exits": "an exit (stair)", "access": "an access to exits", "other": "a room"}


def _flame_spread(f: Facts, plans) -> list[Finding]:
    """By-laws 204-206 and the Eighth Schedule: the walls along a space's outline and the ceilings
    over it have a SurfaceSpreadOfFlame class no worse than the schedule allows for the place
    (a stair is an exit, a corridor or lobby an access to exits, by name) and the purpose group.
    Sprinklers relax it (205(3)); an occupied basement with no exit door straight outside needs
    Class 0 or 1, storage and service rooms aside (205(4)).  The small-area allowances (206(2)-(3)),
    the hospital allowance for rooms of 4 or fewer, and plastic ceilings (207) are not judged."""
    clause = "UBBL by-law 204-206, Eighth Schedule"
    if not any(any(e.is_a(t) for t in _SURFACES) and f.common(e, "SurfaceSpreadOfFlame") is not None
               for e in f.carriers("SurfaceSpreadOfFlame")):
        return [Finding("GEO-FLAME-DATA", "info", "No wall or ceiling states its class of surface spread of "
                        "flame (Pset_WallCommon / Pset_CoveringCommon .SurfaceSpreadOfFlame), so by-laws "
                        "204-206 were not checked", clause, [])]             if any(f.model.by_type(t) for t in _SURFACES) else []
    try:
        key = ubbl.use_key(f.group, f.use)
    except KeyError:
        return []           # IFC-TRAVEL-USE reports the unknown use
    row = ubbl.eighth_row(key)
    if row is None:
        return [Finding("GEO-FLAME", "info", f"The Eighth Schedule copy used has no row for "
                        f"{ubbl.PURPOSE_GROUPS[f.group].lower()} (group {f.group}), so the classes of "
                        "surface spread of flame were not checked", clause, [])]
    small_shop = key == "V" and f.sizes.building_area <= ubbl.SMALL_SHOP_M2 and len(f.floor_storeys) <= 1
    out, unread = [], {}
    for pl in plans:
        underground = f.is_basement(pl.storey) and not pl.direct_exit
        for sp, poly in pl.spaces:
            place = "exits" if named(sp, rooms.STAIR) else "access" if named(sp, rooms.CIRCULATION) else "other"
            occupied_basement = underground and f.group != "I" and not named(sp, rooms.STORAGE_OR_SERVICE)
            weak = []
            for surface, els in (("walls", [w.el for w in pl.walls_along(poly)]),
                                 ("ceiling", pl.ceilings_over(poly))):
                need = ubbl.flame_limit(row, place, surface, f.sprinklered, small_shop, occupied_basement)
                if need is None:
                    continue
                for e in els:
                    shown, c = _surface_class(f, e)
                    if c is None:
                        unread[e.id()] = e
                    elif c > need:
                        weak.append((shown, c, need))
            if weak:
                what = "; ".join(f"{label(e)} Class {c} (needs {n} or better)" for e, c, n in weak[:6]) \
                    + (" ..." if len(weak) > 6 else "")
                out.append(Finding("GEO-FLAME", "warning",
                                   f"{pl.name} {label(sp)}: {_PLACE_WORDS[place]}"
                                   + (", underground" if occupied_basement else "")
                                   + (", sprinklered" if f.sprinklered else "") + f": {what}", clause,
                                   [sp.GlobalId] + [e.GlobalId for e, _, _ in weak]))
    if unread:
        out.append(Finding("GEO-FLAME-DATA", "info", f"{len(unread)} wall(s) or ceiling(s) around rooms state "
                           "no class of surface spread of flame the checker can read (Class 0 to 4)", clause,
                           [e.GlobalId for e in unread.values()]))
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
    declared = f.prop(sp, PSET, "MaxTravelDistance")
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
    vs = [geom.volume(f.mesh(sp)) for sp in f.model.by_type("IfcSpace")]
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
    for e in f.carriers("FireApplianceAccess"):
        if e.is_a("IfcProduct") and f.prop(e, PSET, "FireApplianceAccess") is True:
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
                p = f.location(h)
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
        cid = f.prop(sp, PSET, "CompartmentId")
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
        declared = {s: f.common(s, "Compartmentation") for s in slabs}
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
          heatmaps: dict | None = None, facts: Facts | None = None) -> list[Finding]:
    """heat_dir: write a travel-distance PNG per storey there, and record each in `heatmaps`
    (storey name -> path) when given.  `facts`: the model already read (run.ifc shares one with
    ifccheck)."""
    f = facts or Facts(model)
    pls = plans(f)
    out = _enclosed_rooms(pls) + _assembly_rooms(f, pls)
    if f.group in ubbl.PURPOSE_GROUPS:
        out += _flame_spread(f, pls)
        out += _travel(f, pls, heat_dir, cell, {} if heatmaps is None else heatmaps)
        out += _exit_spacing(f, pls)
    out += _access(f)
    out += _groups(f)
    out += _basement(f)
    return out
