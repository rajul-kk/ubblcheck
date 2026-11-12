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
    """'60', 'FRP 60 min', 'REI 120', 'FD60', '120 minutes', '1 HR', '2 HR FRP', '1/2 hour' -> minutes;
    None if unreadable.  Accepts what ids_build.FRP_PATTERN accepts (a test holds them together)."""
    if label is None:
        return None
    s = str(label).strip().lower()
    m = re.fullmatch(r"(?:(?:frp|rei|ei|r|fd|frd) ?)?(\d+) ?(?:min|mins|minute|minutes)?", s)
    if m:
        return int(m.group(1))
    m = re.fullmatch(r"(frp ?)?(1/2|\d+(?:\.\d+)?) ?(hr|hrs|hour|hours)( frp)?", s)
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

def objects(model, cls: str, types) -> list:
    """Occurrences of `cls` whose predefined type, their own or their type object's, is in `types`.
    IFC2X3 has no occurrence class for most fire objects (a sprinkler is an IfcFlowTerminal there),
    so in that schema the type object (`cls` + "Type") decides."""
    types = set(types)
    try:
        return [e for e in model.by_type(cls) if (uel.get_predefined_type(e) or "") in types]
    except RuntimeError:            # class not in this schema
        pass
    try:
        typed = model.by_type(cls + "Type")
    except RuntimeError:
        return []
    return [e for t in typed if (t.PredefinedType or "") in types for e in uel.get_types(t)]


_VOID = re.compile(r"open to below|\bvoid\b|terbuka ke bawah|double (height|volume)|upper part of"
                   r"|^\s*(roof|attic)( space)?\s*$|\b(roof|ceiling) space\b", re.I)


def space_labels(space) -> list[str]:
    """What a space is called: its Name, LongName and ObjectType (room type), where given."""
    return [str(x) for x in (space.Name, getattr(space, "LongName", None), getattr(space, "ObjectType", None)) if x]


def named(space, pattern) -> bool:
    return any(pattern.search(x) for x in space_labels(space))


def not_floor(space) -> bool:
    """A space that is not floor area: a void or double-height volume drawn as a room, or a gross-area
    zone (IfcSpace GFA, as Revit exports area plans) that would count the rooms inside it twice."""
    if (getattr(space, "PredefinedType", None) or "") == "GFA":
        return True
    return named(space, _VOID)


def exits_declared(f: "Facts") -> bool:
    """Does any door say whether it is a fire exit?  When none does, exit checks have nothing to go on."""
    return any(_prop(d, "Pset_DoorCommon", "FireExit") is not None for d in f.model.by_type("IfcDoor"))


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
        # a roof or services level exported as a storey holds no spaces (or only voids); it is not a
        # storey of the building
        with_spaces = {self.storey_of(sp) for sp in model.by_type("IfcSpace") if not not_floor(sp)}
        occupied = [s for s in self.above_ground if s in with_spaces]
        self.occupied = occupied if occupied else self.above_ground

    @property
    def top_floor_m(self) -> float:
        """Height of the topmost floor above the ground storey (by-law 194: 'top most floor')."""
        return max((self.elev(s) for s in self.occupied), default=0.0)

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
        """Declared floor area, else the area of the space's geometry, else 0.  Voids count as 0."""
        if not_floor(space):
            return 0.0
        a = self.area(space)
        if a is None:
            from . import geom
            fp = geom.footprint(geom.mesh(space))
            a = fp.area if fp is not None else 0.0
        return a

    def persons(self, space, area: float | None = None) -> int | None:
        """Occupant load of a space (Seventh Schedule): its floor area, or `area` when given, over
        the load factor of its OccupancyUse, else of the building's group.  None when there is no
        row for it: an unknown use (reported once, IFC-OCCUPANCY) or a group with no load row."""
        row = ubbl.load_row(self.group, _prop(space, PSET, "OccupancyUse"))
        if row is None:
            return None
        return ubbl.occupant_load_for_row(self.floor_area(space) if area is None else area, row)

    def floor_volume(self, space) -> float | None:
        """Declared volume, else the volume of the space's geometry, else None.  Voids count as 0."""
        if not_floor(space):
            return 0.0
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
                           (f"Building purpose group {f.group!r} is not one of I-VIII" if f.group else
                            "Building purpose group is not declared (MY_FireSafety.PurposeGroup); the UBBL "
                            "rules that depend on it were skipped"),
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
    out += _occupancy_uses(f)
    out += _exits(f)
    out += _compartment_floors(f, height)
    out += _fire_doors(f)
    out += _door_closers(f)
    out += _ninth(f, height)
    out += _systems(f)
    out += _tenth(f, height)
    out += _tall_building_rooms(f, height)
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
    columns, beams, floor slabs, and walls that are loadbearing, compartment or separating walls."""
    for cls, (ps, kind) in _STRUCTURE.items():
        for el in f.model.by_type(cls):
            if cls == "IfcSlab" and (el.PredefinedType or "") not in _FLOOR_SLABS:
                continue
            if cls == "IfcWall" and not (_prop(el, ps, "LoadBearing") is True
                                         or _prop(el, ps, "Compartmentation") is True
                                         or _prop(el, PSET, "SeparatingWall") is True):
                continue
            st = f.storey_of(el)
            if st is not None:
                yield el, ps, kind, st


def _exempt_single_storey(el, ps, kind, compartment) -> bool:
    """By-law 216(1): in a single-storey building, the structural frame, beams and columns, and internal
    loadbearing walls that are not compartment walls, need no Ninth Schedule period.  (A beam or
    column in or carrying a wall still needs that wall's period; the model can't say which those are,
    so the info finding names the condition.)"""
    if kind in ("column", "beam"):
        return True
    return (kind == "wall" and not compartment and _prop(el, ps, "IsExternal") is not True
            and _prop(el, PSET, "SeparatingWall") is not True)


def _wall_minimum(el, ps) -> tuple[int, str]:
    """By-law 214(1): minutes a wall needs whatever its Ninth Schedule line, and why (0 if none)."""
    if _prop(el, PSET, "SeparatingWall") is True:
        return ubbl.SEPARATING_WALL_MIN, "separating wall"
    if _prop(el, ps, "IsExternal") is True and _prop(el, ps, "LoadBearing") is True:
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

    def __init__(self, f: Facts, height, sizes):
        self.f, self.height, self.sizes = f, height, sizes
        self.lines, self.too_big = {}, []

    def __call__(self, cid):
        if cid not in self.lines:
            label, area, vol = self.sizes.basis(cid)
            self.lines[cid] = ubbl.fire_resistance(self.f.group, self.height, area, vol, self.sizes.storeys,
                                                   self.f.sprinklered, compartment=cid is not None)
            if self.lines[cid] is None:
                self.too_big.append((label, area, vol))
        return self.lines[cid]


def _element_need(f: Facts, el, ps, kind, st, compartment, lines: _NinthLines) -> _Need | None:
    """The strictest period over every compartment the element is in (by-law 216(2)) and, for a
    wall, by-law 214(1).  None when every compartment is oversized (IFC-FRP-SIZE)."""
    sizes = lines.sizes
    basement = f.is_basement(st)
    over_basement = (kind == "floor" and bool(f.basements)
                     and st == min(f.above_ground, key=f.elev, default=None))
    barea = sizes.storey_area.get(st.id()) if basement else None
    needs = []
    for cid in _compartments_of(f, el, st, sizes):
        req = lines(cid)
        if req is not None:
            needs.append(_Need(ubbl.element_frp(req, kind, basement or over_basement, compartment, barea),
                               ubbl.NINTH_CLAUSE, req.line, sizes.basis(cid)[0]))
    if kind == "wall":
        least, why = _wall_minimum(el, ps)
        if least:
            needs.append(_Need(least, ubbl.WALL_MIN_CLAUSE, why))
    return max(needs, key=lambda n: n.minutes, default=None)


def _ninth(f: Facts, height):
    """Ninth Schedule periods for elements of structure.  The line is chosen from the size of the
    compartment each element is in (or bounds); without compartments, from the building."""
    sizes = dimensions(f)
    if not sizes.storey_area:
        return []
    lines = _NinthLines(f, height, sizes)
    single = sizes.storeys <= 1 and not f.basements
    short, unread, exempt = defaultdict(list), defaultdict(list), defaultdict(int)
    for el, ps, kind, st in _structure(f):
        compartment = _prop(el, ps, "Compartmentation") is True
        if single and _exempt_single_storey(el, ps, kind, compartment):
            exempt[kind] += 1
            continue
        have = frp_minutes(_prop(el, ps, "FireRating"))
        if have is None:
            if not compartment:     # compartment walls and floors: the IDS pack (MY-P08, MY-P09) reports them
                unread[kind].append(el)
            continue
        need = _element_need(f, el, ps, kind, st, compartment, lines)
        if need is not None and have < need.minutes:
            short[(st.Name, kind, need)].append((el, have))
    return (_ninth_notes(f, height, exempt, lines.too_big, unread)
            + [_frp_short(storey, kind, need, els) for (storey, kind, need), els
               in sorted(short.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2].minutes, kv[0][2].line))])


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


def _ninth_notes(f: Facts, height, exempt, too_big, unread) -> list[Finding]:
    """What the Ninth Schedule check left out, and why: exempt, oversized, or unreadable."""
    out = []
    if exempt:
        what = ", ".join(f"{n} {k}{'s' if n > 1 else ''}" for k, n in sorted(exempt.items()))
        out.append(Finding("IFC-FRP-SINGLE", "info",
                           f"Single-storey building: {what} need no fire resistance period (by-law 216(1)), "
                           "except a beam or column in or carrying a wall, which needs that wall's period",
                           "UBBL by-law 216(1)", [], {"exempt": dict(exempt)}))
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
    if not exits_declared(f):
        return [Finding("IFC-EXIT-DATA", "warning", "No door states whether it is a fire exit "
                        "(Pset_DoorCommon.FireExit); exit count and width were not checked",
                        "UBBL by-law 175-177", [])]
    load = defaultdict(int)
    for sp in f.model.by_type("IfcSpace"):
        st = f.storey_of(sp)
        if st is None:
            continue
        load[st] += f.persons(sp) or 0      # an unknown use is reported by _occupancy_uses
    exits = storey_exits(f)
    top = f.top_floor_m
    single_ok = ubbl.single_stair_allowed(f.group, f.use, top)
    for st, persons in sorted(load.items(), key=lambda kv: f.elev(kv[0])):
        storey = st.Name or st.GlobalId
        ds, via_stairs = exits.get(st, ([], False))
        gids = [d.GlobalId for d in ds]
        noun = "escape stair" if via_stairs else "exit door"
        if persons > 0 and len(ds) == 1 and single_ok:
            out.append(Finding("IFC-EXIT-SINGLE", "warning",
                               f"{storey}: one exit ({noun}). By-law 194 allows a single staircase here (topmost "
                               f"floor {top:.1f} m, at most {ubbl.SINGLE_STAIR_TOP_M} m) provided that: "
                               + "; ".join(ubbl.SINGLE_STAIR_CONDITIONS),
                               "UBBL by-law 194", gids, {"top_floor_m": top}))
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
           if _prop(sp, PSET, "OccupancyUse") is not None and not not_floor(sp)
           and ubbl.load_row(f.group, _prop(sp, PSET, "OccupancyUse")) is None]
    if not bad:
        return []
    uses = sorted({str(_prop(sp, PSET, "OccupancyUse")) for sp in bad})
    rows = sorted(k.split(".", 1)[1] for k in ubbl.OCCUPANT_LOAD if k.startswith(f"{f.group}."))
    return [Finding("IFC-OCCUPANCY", "error",
                    f"{len(bad)} space(s) have an OccupancyUse with no Seventh Schedule row for group {f.group} "
                    f"({', '.join(uses)}); their occupants are not counted in the exit checks. "
                    f"Rows for this group: {', '.join(rows) or 'none (leave OccupancyUse empty)'}",
                    "UBBL by-law 175, Seventh Schedule", [sp.GlobalId for sp in bad], {"uses": uses})]


def storey_exits(f: Facts) -> dict:
    """Each storey's exits, as {storey: (elements, by stair)}.  The exits are the doors flagged
    Pset_DoorCommon.FireExit.  A storey with none escapes by its escape stairs
    (Pset_StairCommon.FireExit) if it has any (by-law 167): a stair is contained in the storey it
    starts from and serves the next storey up.  A stair up from a basement is not a way out of the
    storey above it.  Counting stairs only where there is no exit door
    keeps a door into a stair enclosure and the stair itself from being counted twice."""
    doors, stairs = defaultdict(list), defaultdict(list)
    for d in f.model.by_type("IfcDoor"):
        st = f.storey_of(d)
        if st is not None and _prop(d, "Pset_DoorCommon", "FireExit") is True:
            doors[st].append(d)
    above = dict(zip(f.storeys, f.storeys[1:]))
    for s in f.model.by_type("IfcStair"):
        st = f.storey_of(s)
        if st in above and not f.is_basement(st) and _prop(s, "Pset_StairCommon", "FireExit") is True:
            stairs[above[st]].append(s)
    out = {st: (ds, False) for st, ds in doors.items()}
    for st, ss in stairs.items():
        out.setdefault(st, (ss, True))
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


def host_wall(door):
    """The wall (or other element) whose opening a door or window fills."""
    for rel in getattr(door, "FillsVoids", []) or []:
        opening = rel.RelatingOpeningElement
        for v in getattr(opening, "VoidsElements", []) or []:
            return v.RelatingBuildingElement
    return None


def _fire_doors(f: Facts):
    out = []
    for d in f.model.by_type("IfcDoor"):
        wall = host_wall(d)
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


def _door_closers(f: Facts):
    """Fire doors (rated half an hour or more, by-law 164(1)) and exit doors (by-law 173(2)) close by
    themselves.  Declared not self-closing is an error; doors that don't say are listed in one warning."""
    out, unknown = [], []
    for d in f.model.by_type("IfcDoor"):
        r = frp_minutes(_prop(d, "Pset_DoorCommon", "FireRating"))
        why = [w for w, applies in (("fire door", r is not None and r >= 30),
                                    ("exit door", _prop(d, "Pset_DoorCommon", "FireExit") is True)) if applies]
        if not why:
            continue
        closing = _prop(d, "Pset_DoorCommon", "SelfClosing")
        if closing is False:
            out.append(Finding("IFC-DOORCLOSER", "error",
                               f"Door {d.Name or d.GlobalId} ({' and '.join(why)}) is declared not self-closing",
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
    return "UBBL by-law " + ", ".join(c for w, c in (("fire door", "164(1)"), ("exit door", "173(2)")) if w in why)


# by-law 238: the fire command centre, not any control room (CCTV, security, lift)
_COMMAND_CENTRE = re.compile(r"command\s*(and|&)\s*control|fire\s+(command|control)\b|\bFCC\b"
                             r"|(bilik|pusat)\s+(kawalan|arahan)\s+(bomba|kebakaran)", re.I)
# by-law 242: a lobby, and a fire-fighting word ('FFL' is finished floor level on drawings, so not used)
_LOBBY = re.compile(r"\blobb(y|ies)\b|\blobi\b", re.I)
_FIRE_FIGHTING = re.compile(r"fire\s*-?\s*fight|fire\s*m[ae]n|\bbomba\b", re.I)


def _fire_lobby(space) -> bool:
    return any(_LOBBY.search(x) and _FIRE_FIGHTING.search(x) for x in space_labels(space))


def _tall_building_rooms(f: Facts, height) -> list[Finding]:
    """Rooms found by name.  By-law 238: over 30.5 m, a command and control centre.  By-law 242(a):
    each fire-fighting access lobby at least 5.57 m2.  A missing room is a warning, since the
    checker only knows rooms by their names."""
    out = []
    spaces = f.model.by_type("IfcSpace")
    if height > ubbl.COMMAND_CENTRE_HEIGHT_M and not any(named(sp, _COMMAND_CENTRE) for sp in spaces):
        out.append(Finding("IFC-COMMAND-CENTRE", "warning",
                           f"Building is {height:.1f} m high (over {ubbl.COMMAND_CENTRE_HEIGHT_M} m) and no space is "
                           "named as a command and control centre (e.g. 'Fire Command Centre', 'Bilik Kawalan Bomba')",
                           "UBBL by-law 238", [f.building.GlobalId] if f.building else []))
    for sp in spaces:
        if not _fire_lobby(sp):
            continue
        a = f.floor_area(sp)
        if a <= 0:
            out.append(Finding("IFC-FIRE-LOBBY-DATA", "warning",
                               f"Fire-fighting access lobby {sp.LongName or sp.Name or sp.GlobalId} has no floor area "
                               "the checker can read; its 5.57 m2 minimum was not checked", "UBBL by-law 242(a)",
                               [sp.GlobalId]))
        elif a < ubbl.FIRE_LOBBY_MIN_M2:
            out.append(Finding("IFC-FIRE-LOBBY", "error",
                               f"Fire-fighting access lobby {sp.LongName or sp.Name or sp.GlobalId}: {a:.2f} m2, "
                               f"under the {ubbl.FIRE_LOBBY_MIN_M2} m2 minimum", "UBBL by-law 242(a)",
                               [sp.GlobalId], {"area_m2": a}))
    return out


def _terminals(f: Facts, ptype):
    return objects(f.model, "IfcFireSuppressionTerminal", (ptype,))


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


# Objects that show a Tenth Schedule system is in the model: code -> (IFC class, predefined types, name)
_TENTH_OBJECTS = {
    "G": ("IfcFireSuppressionTerminal", ("HOSEREEL",), "hose reels (IfcFireSuppressionTerminal HOSEREEL)"),
    "H": ("IfcFireSuppressionTerminal", ("FIREHYDRANT",), "hydrants (IfcFireSuppressionTerminal FIREHYDRANT)"),
    "1": ("IfcSensor", ("SMOKESENSOR", "HEATSENSOR"), "smoke or heat detectors (IfcSensor)"),
    "2": ("IfcAlarm", ("BREAKGLASSBUTTON", "MANUALPULLBOX", "BELL", "SIREN"),
          "break glass units or alarm bells (IfcAlarm)"),
}


def tenth_inputs(f: Facts, height: float) -> dict:
    """The building's size as the Tenth Schedule reads it."""
    sizes = dimensions(f)
    occupied = {s.id() for s in f.occupied}
    rooms = _prop(f.building, PSET, "Rooms") if f.building else None
    return {"storeys": len(f.occupied), "height_m": height,
            "floor_area_m2": sizes.building_area,
            "total_area_m2": sum(a for sid, a in sizes.storey_area.items() if sid in occupied),
            "volume_m3": max(sizes.comp_volume.values(), default=0.0) or sizes.building_volume,
            "rooms": int(rooms) if rooms is not None else None}


def _tenth(f: Facts, height: float) -> list[Finding]:
    """Fire extinguishment and alarm systems the Tenth Schedule line requires, against the objects in
    the model.  Emergency lighting and alarm types 3-4 have no IFC object here and are not checked."""
    gid = [f.building.GlobalId] if f.building else []
    try:
        use = ubbl.tenth_use(f.group, f.use, _prop(f.building, PSET, "TenthUse") if f.building else None)
    except KeyError as e:
        return [Finding("IFC-TENTH", "warning", f"{e.args[0]}: set MY_FireSafety.TenthUse on the building",
                        ubbl.TENTH_CLAUSE, gid)]
    look = ubbl.tenth(use, **tenth_inputs(f, height))
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
