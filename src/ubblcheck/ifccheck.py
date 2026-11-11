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
from dataclasses import dataclass, field

import ifcopenshell
import ifcopenshell.util.element as uel
import ifcopenshell.util.unit as uunit
from ifctester import ids

from . import ubbl
from .findings import Finding

IDS_DIR = os.path.join(os.path.dirname(__file__), "ids")
PSET = "MY_FireSafety"


def frp_minutes(label) -> int | None:
    """'60', 'FRP 60 min', 'REI 120', '120 minutes', '1 HR', '1/2 hour' -> minutes; None if unreadable."""
    if label is None:
        return None
    s = str(label).strip().lower()
    m = re.fullmatch(r"(?:(?:frp|rei|ei|r) ?)?(\d+) ?(?:min|mins|minute|minutes)?", s)
    if m:
        return int(m.group(1))
    m = re.fullmatch(r"(frp ?)?(1/2|\d+(?:\.\d+)?) ?(hr|hrs|hour|hours)", s)
    if m:
        v = 0.5 if m.group(2) == "1/2" else float(m.group(2))
        return int(round(v * 60))
    return None


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


# ---------------------------------------------------------------- model facts

def _prop(el, pset, name, default=None):
    return uel.get_psets(el).get(pset, {}).get(name, default)


BASEMENT_BELOW_M = -1.0     # a storey more than 1 m below the ground storey is a basement


class Facts:
    """The few numbers the rules need, read once from the model in metres.

    Quantities are converted with their own project unit: Revit, for one, exports
    lengths in millimetres but areas in square metres."""

    def __init__(self, model: ifcopenshell.file):
        self.model = model
        self.scale = uunit.calculate_unit_scale(model)  # project length unit -> metres
        self.area_scale = uunit.calculate_unit_scale(model, "AREAUNIT")
        self.volume_scale = uunit.calculate_unit_scale(model, "VOLUMEUNIT")
        b = (model.by_type("IfcBuilding") or [None])[0]
        self.building = b
        self.group = _prop(b, PSET, "PurposeGroup") if b else None
        self.use = _prop(b, PSET, "SubUse") if b else None
        self.height = _prop(b, PSET, "BuildingHeight") if b else None
        if self.height is not None:
            self.height = float(self.height) * self.scale
        self.sprinklered = bool(_prop(b, "Pset_BuildingCommon", "SprinklerProtectionAutomatic", False)) if b else False
        self.storeys = sorted(model.by_type("IfcBuildingStorey"), key=lambda s: s.Elevation or 0.0)
        ground = [s for s in self.storeys if (s.Elevation or 0.0) >= -1e-6]
        self.ground_elev = (ground[0].Elevation or 0.0) * self.scale if ground else 0.0
        self.basements = [s for s in self.storeys if self.is_basement(s)]
        self.above_ground = [s for s in self.storeys if not self.is_basement(s)]
        # a roof or services level exported as a storey holds no spaces; it is not a storey of the building
        with_spaces = {self.storey_of(sp) for sp in model.by_type("IfcSpace")}
        occupied = [s for s in self.above_ground if s in with_spaces]
        self.occupied = occupied if occupied else self.above_ground

    def storey_of(self, el):
        c = uel.get_container(el) or uel.get_aggregate(el)
        while c is not None and not c.is_a("IfcBuildingStorey"):
            c = uel.get_aggregate(c)
        return c

    def elev(self, storey) -> float:
        return (storey.Elevation or 0.0) * self.scale - self.ground_elev

    def is_basement(self, storey) -> bool:
        return self.elev(storey) < BASEMENT_BELOW_M

    def area(self, space) -> float | None:
        """Declared floor area (Qto_SpaceBaseQuantities) in m2, or None."""
        q = uel.get_psets(space, qtos_only=True).get("Qto_SpaceBaseQuantities", {})
        a = q.get("NetFloorArea", q.get("GrossFloorArea"))
        return None if a is None else float(a) * self.area_scale

    def volume(self, space) -> float | None:
        """Declared volume in m3 (or declared area x height), or None."""
        q = uel.get_psets(space, qtos_only=True).get("Qto_SpaceBaseQuantities", {})
        v = q.get("NetVolume", q.get("GrossVolume"))
        if v is not None:
            return float(v) * self.volume_scale
        a = self.area(space)
        h = q.get("Height", q.get("FinishCeilingHeight"))
        return None if a is None or h is None else a * float(h) * self.scale

    def floor_area(self, space) -> float:
        """Declared floor area, else the area of the space's geometry, else 0."""
        a = self.area(space)
        if a is None:
            from . import geom
            fp = geom.footprint(geom.mesh(space))
            a = fp.area if fp is not None else 0.0
        return a

    def floor_volume(self, space) -> float | None:
        """Declared volume, else the volume of the space's geometry, else None."""
        v = self.volume(space)
        if v is None:
            from . import geom
            v = geom.volume(geom.mesh(space))
        return v


# ---------------------------------------------------------------- rule pass

def check_rules(model: ifcopenshell.file) -> list[Finding]:
    f = Facts(model)
    out: list[Finding] = []
    if f.group not in ubbl.PURPOSE_GROUPS:
        out.append(Finding("IFC-GROUP", "error",
                           f"Building purpose group is {f.group!r}; rules need one of I-VIII",
                           "UBBL by-law 134, Fifth Schedule",
                           [f.building.GlobalId] if f.building else []))
        return out
    height = f.height
    if height is None and f.storeys:
        height = max(f.elev(s) for s in f.storeys)
        out.append(Finding("IFC-HEIGHT", "info",
                           f"BuildingHeight not given; using top storey elevation {height:.1f} m "
                           "(lower than the by-law 135(a) height)", "UBBL by-law 135(a)", []))
    height = height or 0.0
    single = len(f.occupied) <= 1
    out += _compartments(f, height, single)
    out += _travel(f)
    out += _exits(f)
    out += _compartment_floors(f, height)
    out += _fire_doors(f)
    out += _ninth(f, height)
    out += _systems(f)
    return out


@dataclass
class Sizes:
    """Floor areas and volumes for the Ninth Schedule look-up, by storey and by compartment."""
    storey_area: dict            # storey id -> m2
    comp_area: dict              # compartment id -> largest storey area of the compartment, m2
    comp_volume: dict            # compartment id -> m3
    storey_comps: dict           # storey id -> compartment ids with a space on that storey
    comp_spaces: list            # (storey id, space, compartment id), to place elements
    building_area: float         # largest storey area of the building
    building_volume: float
    storeys: int                 # storeys at or above ground, not counting roof levels
    footprints: dict = field(default_factory=dict)   # space id -> plan footprint, filled as needed

    def footprint(self, space):
        if space.id() not in self.footprints:
            from . import geom
            self.footprints[space.id()] = geom.footprint(geom.mesh(space))
        return self.footprints[space.id()]

    def basis(self, cid=None) -> tuple[str, float, float]:
        """(label, storey area, volume) of a compartment, or of the building when cid is None."""
        if cid is None:
            return "building", self.building_area, self.building_volume
        return f"compartment {cid}", self.comp_area[cid], self.comp_volume[cid]


def dimensions(f: Facts) -> Sizes:
    st_area = defaultdict(float)
    comp_st_area = defaultdict(float)
    comp_vol = defaultdict(float)
    storey_comps = defaultdict(set)
    comp_spaces = []
    total_vol = 0.0
    for sp in f.model.by_type("IfcSpace"):
        st = f.storey_of(sp)
        if st is None:
            continue
        a = f.floor_area(sp)
        v = f.floor_volume(sp) or 0.0
        st_area[st.id()] += a
        total_vol += v
        cid = _prop(sp, PSET, "CompartmentId")
        if cid is not None:
            cid = str(cid)
            comp_st_area[(cid, st.id())] += a
            comp_vol[cid] += v
            storey_comps[st.id()].add(cid)
            comp_spaces.append((st.id(), sp, cid))
    comp_area = defaultdict(float)
    for (cid, _), a in comp_st_area.items():
        comp_area[cid] = max(comp_area[cid], a)
    return Sizes(dict(st_area), dict(comp_area), dict(comp_vol), dict(storey_comps), comp_spaces,
                 max(st_area.values(), default=0.0), total_vol, len(f.occupied))


def _compartments_of(f: Facts, el, st, sizes: Sizes) -> list:
    """The compartments an element of structure is in or bounds, for the Ninth Schedule.

    In order: the element's own MY_FireSafety.CompartmentId (several separated by commas, for a
    wall between compartments); else the compartments of the spaces its plan footprint touches;
    else every compartment on its storey, so the element is held to the longest of their periods.
    [None] means the building is not divided into compartments."""
    own = _prop(el, PSET, "CompartmentId")
    if own is not None:
        ids = [c.strip() for c in re.split(r"[,;]", str(own)) if c.strip() in sizes.comp_area]
        if ids:
            return ids
    on_storey = sorted(sizes.storey_comps.get(st.id(), ()))
    if len(on_storey) > 1 and getattr(el, "Representation", None):
        from . import geom
        fp = geom.footprint(geom.mesh(el))
        if fp is not None:
            near = fp.buffer(0.3)
            touched = set()
            for sid, sp, cid in sizes.comp_spaces:
                if sid == st.id() and cid not in touched:
                    sfp = sizes.footprint(sp)
                    if sfp is not None and sfp.intersects(near):
                        touched.add(cid)
            if touched:
                return sorted(touched)
    if on_storey:
        return on_storey
    return list(sizes.comp_area) or [None]


# element class -> (common pset, kind)
_STRUCTURE = {"IfcColumn": ("Pset_ColumnCommon", "column"), "IfcBeam": ("Pset_BeamCommon", "beam"),
              "IfcSlab": ("Pset_SlabCommon", "floor"), "IfcWall": ("Pset_WallCommon", "wall")}
_FLOOR_SLABS = ("FLOOR", "BASESLAB", "NOTDEFINED", "")


def _structure(f: Facts):
    """(element, common pset, kind, storey) for each element of structure the Ninth Schedule covers:
    columns, beams, floor slabs, and walls that are loadbearing or compartment walls."""
    for cls, (ps, kind) in _STRUCTURE.items():
        for el in f.model.by_type(cls):
            if cls == "IfcSlab" and (el.PredefinedType or "") not in _FLOOR_SLABS:
                continue
            if cls == "IfcWall" and not (_prop(el, ps, "LoadBearing") is True
                                         or _prop(el, ps, "Compartmentation") is True):
                continue
            st = f.storey_of(el)
            if st is not None:
                yield el, ps, kind, st


def _ninth(f: Facts, height):
    """Ninth Schedule periods for elements of structure.  The line is chosen from the size of the
    compartment each element is in (or bounds); without compartments, from the building."""
    out = []
    sizes = dimensions(f)
    if not sizes.storey_area:
        return out
    reqs, too_big = {}, []

    def requirement(cid):
        if cid not in reqs:
            label, area, vol = sizes.basis(cid)
            reqs[cid] = ubbl.fire_resistance(f.group, height, area, vol, sizes.storeys, f.sprinklered,
                                             compartment=cid is not None)
            if reqs[cid] is None:
                too_big.append((label, area, vol))
        return reqs[cid]

    basements = {s.id() for s in f.basements}
    ground = min(f.above_ground, key=f.elev, default=None)
    over_basement = ground.id() if (basements and ground is not None) else None
    short = defaultdict(list)
    unread = defaultdict(list)
    for el, ps, kind, st in _structure(f):
        compartment = _prop(el, ps, "Compartmentation") is True
        have = frp_minutes(_prop(el, ps, "FireRating"))
        if have is None:
            if not compartment:     # compartment walls and floors: the IDS pack (MY-P08, MY-P09) reports them
                unread[kind].append(el)
            continue
        base = st.id() in basements or (kind == "floor" and st.id() == over_basement)
        barea = sizes.storey_area.get(st.id()) if st.id() in basements else None
        needs = []
        for cid in _compartments_of(f, el, st, sizes):
            req = requirement(cid)
            if req is not None:
                needs.append((ubbl.element_frp(req, kind, base, compartment, barea), sizes.basis(cid)[0], req))
        if not needs:
            continue                # every compartment it is in is oversized: IFC-FRP-SIZE below
        need, label, req = max(needs, key=lambda n: n[0])
        if have < need:
            short[(st.Name, kind, need, label, req.line)].append((el, have))
    if too_big:
        what = "; ".join(f"{label} ({area:.0f} m2 storey, {vol:.0f} m3)" for label, area, vol in too_big[:6])
        more = f" and {len(too_big) - 6} more" if len(too_big) > 6 else ""
        out.append(Finding("IFC-FRP-SIZE", "info",
                           f"Larger than every Ninth Schedule line for group {f.group} at {height:.1f} m: {what}{more}. "
                           "Each must be compartmented (see IFC-COMP-AREA / IFC-COMP-VOL), so the periods of "
                           "their elements are not checked", ubbl.NINTH_CLAUSE, [],
                           {"oversized": [{"basis": label, "area_m2": round(area), "volume_m3": round(vol)}
                                          for label, area, vol in too_big]}))
    if unread:
        n = sum(map(len, unread.values()))
        out.append(Finding("IFC-FRP-DATA", "warning",
                           f"{n} element(s) of structure have no FireRating the checker can read "
                           f"({', '.join(f'{len(v)} {k}' for k, v in sorted(unread.items()))}); their Ninth "
                           "Schedule period was not checked. Use minutes (60, FRP 60 min, REI 60) or hours (1 HR)",
                           ubbl.NINTH_CLAUSE, [e.GlobalId for v in unread.values() for e in v],
                           {"by_kind": {k: len(v) for k, v in unread.items()}}))
    for (storey, kind, need, label, line), els in sorted(short.items(), key=lambda kv: tuple(map(str, kv[0]))):
        worst = min(h for _, h in els)
        out.append(Finding("IFC-FRP", "error",
                           f"{storey}: {len(els)} {kind}(s) below the {need} min the Ninth Schedule requires "
                           f"(lowest {worst} min; {line}; {label})", ubbl.NINTH_CLAUSE,
                           [e.GlobalId for e, _ in els],
                           {"required_min": need, "lowest_min": worst, "line": line, "basis": label}))
    return out


def _compartments(f: Facts, height, single):
    out = []
    lim = ubbl.compartment_limit(f.group, height, single, f.sprinklered)
    if lim is None:
        return out
    area = defaultdict(float)
    vol = defaultdict(float)
    members = defaultdict(list)
    no_vol = set()
    for sp in f.model.by_type("IfcSpace"):
        cid = _prop(sp, PSET, "CompartmentId")
        st = f.storey_of(sp)
        if cid is None or st is None:
            continue
        area[(cid, st.Name)] += f.floor_area(sp)
        v = f.floor_volume(sp)
        if v is None:
            no_vol.add(cid)
        else:
            vol[cid] += v
        members[cid].append(sp.GlobalId)
    for (cid, storey), a in sorted(area.items()):
        if a > lim.area_m2:
            out.append(Finding("IFC-COMP-AREA", "error",
                               f"Compartment {cid} on {storey}: storey area {a:.0f} m2 exceeds "
                               f"{lim.area_m2:.0f} m2 for purpose group {f.group} at {height:.1f} m",
                               lim.clause, members[cid], {"area_m2": a, "limit_m2": lim.area_m2}))
    for cid, v in sorted(vol.items()):
        if v > lim.volume_m3:
            out.append(Finding("IFC-COMP-VOL", "error",
                               f"Compartment {cid}: volume {v:.0f} m3 exceeds {lim.volume_m3:.0f} m3",
                               lim.clause, members[cid], {"volume_m3": v, "limit_m3": lim.volume_m3}))
    if no_vol and lim.volume_m3 != ubbl.INF:
        out.append(Finding("IFC-COMP-VOL-DATA", "warning",
                           f"Volume limit {lim.volume_m3:.0f} m3 applies but spaces in compartment(s) "
                           f"{', '.join(sorted(map(str, no_vol)))} have no volume or height quantity",
                           lim.clause, []))
    return out


def _travel(f: Facts):
    out = []
    try:
        lim = ubbl.travel_limit(f.group, f.sprinklered, f.use)
    except KeyError as e:
        return [Finding("IFC-TRAVEL-USE", "warning", str(e), "UBBL Seventh Schedule", [])]
    if lim.travel_m is None:
        return out
    for sp in f.model.by_type("IfcSpace"):
        d = _prop(sp, PSET, "MaxTravelDistance")
        if d is None:
            continue
        d = float(d) * f.scale
        if d > lim.travel_m + 1e-9:
            out.append(Finding("IFC-TRAVEL", "error",
                               f"Space {sp.Name or sp.GlobalId}: travel distance {d:.1f} m exceeds "
                               f"{lim.travel_m:g} m ({'sprinklered' if f.sprinklered else 'unsprinklered'}, "
                               f"group {f.group})", lim.clause, [sp.GlobalId],
                               {"travel_m": d, "limit_m": lim.travel_m}))
        dead = _prop(sp, PSET, "DeadEndDistance")
        if dead is not None and lim.dead_end_m is not None and float(dead) * f.scale > lim.dead_end_m:
            out.append(Finding("IFC-DEADEND", "error",
                               f"Space {sp.Name or sp.GlobalId}: dead end {float(dead) * f.scale:.1f} m "
                               f"exceeds {lim.dead_end_m:g} m", lim.clause, [sp.GlobalId]))
    return out


def _exits(f: Facts):
    out = []
    if f.group not in ubbl.EXIT_CAPACITY:
        return out
    load = defaultdict(int)
    doors = defaultdict(list)
    for sp in f.model.by_type("IfcSpace"):
        st = f.storey_of(sp)
        if st is None:
            continue
        a = f.floor_area(sp)
        use = _prop(sp, PSET, "OccupancyUse")
        try:
            load[st.Name] += ubbl.occupant_load(a, f.group, use)
        except KeyError:
            continue
    for d in f.model.by_type("IfcDoor"):
        if _prop(d, "Pset_DoorCommon", "FireExit") is True:
            st = f.storey_of(d)
            if st is not None:
                doors[st.Name].append(d)
    for storey, persons in sorted(load.items()):
        ds = doors.get(storey, [])
        gids = [d.GlobalId for d in ds]
        if persons > 0 and len(ds) < ubbl.MIN_EXITS:
            out.append(Finding("IFC-EXIT-COUNT", "error",
                               f"{storey}: {len(ds)} exit door(s) for {persons} persons; minimum is {ubbl.MIN_EXITS}",
                               "UBBL by-law 177(d)", gids))
        need = ubbl.exit_units(persons, f.group, "door", f.use) if persons else 0
        have = sum(ubbl.width_units((d.OverallWidth or 0) * f.scale * 1000) for d in ds)
        if have < need:
            out.append(Finding("IFC-EXIT-WIDTH", "error",
                               f"{storey}: occupant load {persons} needs {need} units of 550 mm exit width; "
                               f"exit doors give {have}", "UBBL by-law 175-176, Seventh Schedule", gids,
                               {"persons": persons, "units_needed": need, "units_given": have}))
    return out


def _compartment_floors(f: Facts, height):
    out = []
    if height <= ubbl.COMPARTMENT_FLOOR_HEIGHT_M:
        return out
    for sl in f.model.by_type("IfcSlab"):
        if (sl.PredefinedType or "") not in _FLOOR_SLABS:
            continue
        st = f.storey_of(sl)
        if st is None or f.elev(st) <= ubbl.COMPARTMENT_FLOOR_ABOVE_M:
            continue
        if _prop(sl, "Pset_SlabCommon", "Compartmentation") is not True:
            out.append(Finding("IFC-COMP-FLOOR", "error",
                               f"Building is {height:.1f} m high; floor on {st.Name} "
                               f"({f.elev(st):.1f} m above ground) must be a compartment floor",
                               "UBBL by-law 137", [sl.GlobalId]))
    return out


def _host_wall(door):
    for rel in getattr(door, "FillsVoids", []) or []:
        opening = rel.RelatingOpeningElement
        for v in getattr(opening, "VoidsElements", []) or []:
            return v.RelatingBuildingElement
    return None


def _fire_doors(f: Facts):
    out = []
    for d in f.model.by_type("IfcDoor"):
        wall = _host_wall(d)
        if wall is None or not wall.is_a("IfcWall"):
            continue
        if _prop(wall, "Pset_WallCommon", "Compartmentation") is not True:
            continue
        wr = frp_minutes(_prop(wall, "Pset_WallCommon", "FireRating"))
        dr = frp_minutes(_prop(d, "Pset_DoorCommon", "FireRating"))
        name = d.Name or d.GlobalId
        if dr is None:
            out.append(Finding("IFC-FIREDOOR", "error",
                               f"Door {name} is in compartment wall {wall.Name or wall.GlobalId} but has no fire rating",
                               "UBBL by-law 162(1)-(2)", [d.GlobalId, wall.GlobalId]))
        elif dr < 30:
            out.append(Finding("IFC-FIREDOOR", "error", f"Door {name}: FRP {dr} min is below half an hour",
                               "UBBL by-law 162(3)", [d.GlobalId]))
        elif wr is not None and dr < wr:
            out.append(Finding("IFC-FIREDOOR-WALL", "warning",
                               f"Door {name}: FRP {dr} min is less than its compartment wall's {wr} min; "
                               "confirm against the Ninth Schedule", "UBBL by-law 162(2), Ninth Schedule",
                               [d.GlobalId, wall.GlobalId]))
    return out


def _terminals(f: Facts, ptype):
    return [t for t in f.model.by_type("IfcFireSuppressionTerminal") if (t.PredefinedType or "") == ptype]


def _systems(f: Facts):
    out = []
    top = max((f.elev(s) for s in f.storeys), default=0.0)
    if f.sprinklered and not _terminals(f, "SPRINKLER"):
        out.append(Finding("IFC-SPRINKLER", "warning",
                           "Building is declared sprinklered but the model has no sprinkler heads "
                           "(fine for an architectural model; needed in the active model)",
                           "UBBL by-law 136 proviso, 225(1)", [f.building.GlobalId]))
    if top > 18.3:
        if not _terminals(f, "BREECHINGINLET"):
            out.append(Finding("IFC-RISER", "warning",
                               f"Topmost floor is {top:.1f} m above ground: a {'dry' if top < 30.5 else 'wet'} "
                               "rising system is required; no breeching inlet is modelled",
                               "UBBL by-law 229(1), 230(1), 231", []))
        lifts = [t for t in f.model.by_type("IfcTransportElement")
                 if _prop(t, "Pset_TransportElementElevator", "FireFightingLift") is True]
        if not lifts:
            out.append(Finding("IFC-FIRELIFT", "warning",
                               f"Topmost floor is {top:.1f} m above ground: a fire lift is required; none is "
                               "flagged (Pset_TransportElementElevator.FireFightingLift)", "UBBL by-law 229(5), 243", []))
    if top > 30.5 and _terminals(f, "BREECHINGINLET"):
        out.append(Finding("IFC-WETRISER", "info",
                           f"Topmost floor is {top:.1f} m above ground: the rising system must be wet, not dry",
                           "UBBL by-law 231", []))
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
