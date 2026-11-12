"""What an IFC model says, read the way the UBBL rules need it.

Both rule modules (ifccheck: numbers the designer declared; georules: what the geometry shows)
read the model through this one: its properties, fire ratings, names, exits and sizes.  Facts
reads each of these once, in metres.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from functools import cached_property

import ifcopenshell
import ifcopenshell.util.element as uel
import ifcopenshell.util.unit as uunit

from . import geom, rooms, ubbl
from .ratings import frp_minutes

PSET = "MY_FireSafety"

# ---------------------------------------------------------------- properties

# The IFC common property set of each element class the rules read (subclasses included).
COMMON_PSET = {"IfcWall": "Pset_WallCommon", "IfcCurtainWall": "Pset_CurtainWallCommon",
               "IfcDoor": "Pset_DoorCommon", "IfcWindow": "Pset_WindowCommon", "IfcSlab": "Pset_SlabCommon",
               "IfcColumn": "Pset_ColumnCommon", "IfcBeam": "Pset_BeamCommon", "IfcStair": "Pset_StairCommon",
               "IfcBuilding": "Pset_BuildingCommon"}


def prop(el, pset, name, default=None):
    return uel.get_psets(el).get(pset, {}).get(name, default)


def common_pset(el) -> str | None:
    return next((ps for cls, ps in COMMON_PSET.items() if el.is_a(cls)), None)


def common(el, name, default=None):
    """A property of the element's own common property set (Pset_WallCommon for a wall, ...)."""
    ps = common_pset(el)
    return default if ps is None else prop(el, ps, name, default)


def rating(el) -> int | None:
    """The element's FireRating (its common property set) in minutes, or None."""
    return frp_minutes(common(el, "FireRating"))


_FLOOR_SLABS = ("FLOOR", "BASESLAB", "NOTDEFINED", "")


def is_floor_slab(slab) -> bool:
    """A floor (not a roof, landing or other slab)."""
    return (slab.PredefinedType or "") in _FLOOR_SLABS


def host_wall(el):
    """The wall (or other element) whose opening a door or window fills."""
    for rel in getattr(el, "FillsVoids", []) or []:
        for v in getattr(rel.RelatingOpeningElement, "VoidsElements", []) or []:
            return v.RelatingBuildingElement
    return None


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
    # get_predefined_type reads a USERDEFINED type's ElementType
    return [e for t in typed if (uel.get_predefined_type(t) or "") in types for e in uel.get_types(t)]


# ---------------------------------------------------------------- names

def labels(el) -> list[str]:
    """What an element is called: its Name, LongName and ObjectType, and its type object's Name and
    ElementType, where given."""
    t = uel.get_type(el)
    return [str(x) for x in (el.Name, getattr(el, "LongName", None), getattr(el, "ObjectType", None),
                             getattr(t, "Name", None), getattr(t, "ElementType", None)) if x]


def named(el, pattern) -> bool:
    return any(pattern.search(x) for x in labels(el))


def label(el) -> str:
    """The name to show for an element in a finding: its Name and LongName ('101 Office'), else its
    GlobalId."""
    return " ".join(dict.fromkeys(x for x in (el.Name, getattr(el, "LongName", None)) if x)) or el.GlobalId


def not_floor(space) -> bool:
    """A space that is not floor area: a void or double-height volume drawn as a room, or a gross-area
    zone (IfcSpace GFA, as Revit exports area plans) that would count the rooms inside it twice."""
    return (getattr(space, "PredefinedType", None) or "") == "GFA" or named(space, rooms.VOID)


# ---------------------------------------------------------------- facts

BASEMENT_BELOW_M = -1.0     # a storey more than 1 m below the ground storey is a basement


@dataclass
class Sizes:
    """Floor areas and volumes, by storey and by compartment: the Fifth Schedule limits, the Ninth
    Schedule look-up and the Tenth Schedule size all read these."""
    storey_area: dict            # storey id -> m2
    comp_storey_area: dict       # (compartment id, storey id) -> m2
    comp_volume: dict            # compartment id -> m3
    comp_members: dict           # compartment id -> space GlobalIds
    no_volume: set               # compartment ids with a space whose volume is unknown
    storey_comps: dict           # storey id -> compartment ids with a space on that storey
    comp_spaces: list            # (storey id, space, compartment id), to place elements
    building_volume: float
    storeys: int                 # storeys at or above ground, not counting roof levels

    @cached_property
    def comp_area(self) -> dict:
        """compartment id -> its largest storey area, m2."""
        out = defaultdict(float)
        for (cid, _), a in self.comp_storey_area.items():
            out[cid] = max(out[cid], a)
        return dict(out)

    @property
    def building_area(self) -> float:
        """Largest storey area of the building."""
        return max(self.storey_area.values(), default=0.0)

    def basis(self, cid=None) -> tuple[str, float, float]:
        """(label, storey area, volume) of a compartment, or of the building when cid is None."""
        if cid is None:
            return "building", self.building_area, self.building_volume
        return f"compartment {cid}", self.comp_area[cid], self.comp_volume[cid]


class Facts:
    """The numbers the rules need, read once from the model in metres.

    Quantities are converted with their own project unit: Revit, for one, exports
    lengths in millimetres but areas in square metres."""

    def __init__(self, model: ifcopenshell.file):
        self.model = model
        self.scale = uunit.calculate_unit_scale(model)  # project length unit -> metres
        self.area_scale = uunit.calculate_unit_scale(model, "AREAUNIT")
        self.volume_scale = uunit.calculate_unit_scale(model, "VOLUMEUNIT")
        b = (model.by_type("IfcBuilding") or [None])[0]
        self.building = b
        self.group = prop(b, PSET, "PurposeGroup") if b else None
        self.use = prop(b, PSET, "SubUse") if b else None
        h = prop(b, PSET, "BuildingHeight") if b else None
        self.height = float(h) * self.scale if h is not None else None     # declared (by-law 135(a))
        self.sprinklered = bool(common(b, "SprinklerProtectionAutomatic", False)) if b else False
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
        self._meshes, self._footprints, self._volumes = {}, {}, {}

    # -- the building

    @property
    def height_estimated(self) -> bool:
        return self.height is None and bool(self.storeys)

    @cached_property
    def height_m(self) -> float:
        """The declared height, else the top storey's elevation (lower than the by-law 135(a) height)."""
        if self.height is not None:
            return self.height
        return max((self.elev(s) for s in self.storeys), default=0.0)

    @cached_property
    def top_floor_m(self) -> float:
        """Height of the topmost floor above the ground storey (by-laws 194, 229: 'topmost floor')."""
        return max((self.elev(s) for s in self.occupied), default=0.0)

    @cached_property
    def _travel(self) -> tuple[ubbl.TravelLimit | None, str | None]:
        try:
            return ubbl.travel_limit(self.group, self.sprinklered, self.use), None
        except KeyError as e:
            return None, e.args[0]

    @property
    def travel(self) -> ubbl.TravelLimit | None:
        """The Seventh Schedule travel limits, or None when the group or sub-use has no row."""
        return self._travel[0]

    @property
    def travel_error(self) -> str | None:
        """Why there are no travel limits."""
        return self._travel[1]

    @cached_property
    def single_stair_allowed(self) -> bool:
        return ubbl.single_stair_allowed(self.group, self.use, self.top_floor_m)

    # -- storeys and places

    def storey_of(self, el):
        c = uel.get_container(el) or uel.get_aggregate(el)
        while c is not None and not c.is_a("IfcBuildingStorey"):
            c = uel.get_aggregate(c)
        return c

    def elev(self, storey) -> float:
        return (storey.Elevation or 0.0) * self.scale - self.ground_elev

    def is_basement(self, storey) -> bool:
        return self.elev(storey) < BASEMENT_BELOW_M

    def mesh(self, el):
        """The element's geometry (cached: tessellating is the slowest step), or None."""
        if el.id() not in self._meshes:
            self._meshes[el.id()] = geom.mesh(el)
        return self._meshes[el.id()]

    def footprint(self, el):
        """Plan footprint of an element's geometry (cached), or None."""
        if el.id() not in self._footprints:
            self._footprints[el.id()] = geom.footprint(self.mesh(el))
        return self._footprints[el.id()]

    # -- spaces

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
            fp = self.footprint(space)
            a = fp.area if fp is not None else 0.0
        return a

    def floor_volume(self, space) -> float | None:
        """Declared volume, else the volume of the space's geometry, else None.  Voids count as 0."""
        if not_floor(space):
            return 0.0
        v = self.volume(space)
        if v is None:
            if space.id() not in self._volumes:
                self._volumes[space.id()] = geom.volume(self.mesh(space))
            v = self._volumes[space.id()]
        return v

    def persons(self, space, area: float | None = None) -> int | None:
        """Occupant load of a space (Seventh Schedule): its floor area, or `area` when given, over
        the load factor of its OccupancyUse, else of the building's group.  None when there is no
        row for it: an unknown use (reported once, IFC-OCCUPANCY) or a group with no load row."""
        row = ubbl.load_row(self.group, prop(space, PSET, "OccupancyUse"))
        if row is None:
            return None
        return ubbl.occupant_load_for_row(self.floor_area(space) if area is None else area, row)

    @cached_property
    def sizes(self) -> Sizes:
        st_area, comp_st_area, comp_vol = defaultdict(float), defaultdict(float), defaultdict(float)
        members, storey_comps, comp_spaces, no_vol = defaultdict(list), defaultdict(set), [], set()
        total_vol = 0.0
        for sp in self.model.by_type("IfcSpace"):
            st = self.storey_of(sp)
            if st is None:
                continue
            a, v = self.floor_area(sp), self.floor_volume(sp)
            st_area[st.id()] += a
            total_vol += v or 0.0
            cid = prop(sp, PSET, "CompartmentId")
            if cid is None:
                continue
            cid = str(cid)
            comp_st_area[(cid, st.id())] += a
            if v is None:
                no_vol.add(cid)
            comp_vol[cid] += v or 0.0
            members[cid].append(sp.GlobalId)
            storey_comps[st.id()].add(cid)
            comp_spaces.append((st.id(), sp, cid))
        return Sizes(dict(st_area), dict(comp_st_area), dict(comp_vol), dict(members), no_vol, dict(storey_comps),
                     comp_spaces, total_vol, len(self.occupied))

    # -- exits

    @cached_property
    def exits_declared(self) -> bool:
        """Does any door say whether it is a fire exit?  When none does, exit checks have nothing to go on."""
        return any(common(d, "FireExit") is not None for d in self.model.by_type("IfcDoor"))

    @cached_property
    def exits(self) -> dict:
        """Each storey's exits, as {storey: (elements, by stair)}.  The exits are the doors flagged
        Pset_DoorCommon.FireExit.  A storey with none escapes by its escape stairs
        (Pset_StairCommon.FireExit) if it has any (by-law 167): a stair is contained in the storey it
        starts from and serves the next storey up.  A stair up from a basement is not a way out of the
        storey above it.  Counting stairs only where there is no exit door keeps a door into a stair
        enclosure and the stair itself from being counted twice."""
        doors, stairs = defaultdict(list), defaultdict(list)
        for d in self.model.by_type("IfcDoor"):
            st = self.storey_of(d)
            if st is not None and common(d, "FireExit") is True:
                doors[st].append(d)
        above = dict(zip(self.storeys, self.storeys[1:]))
        for s in self.model.by_type("IfcStair"):
            st = self.storey_of(s)
            if st in above and not self.is_basement(st) and common(s, "FireExit") is True:
                stairs[above[st]].append(s)
        out = {st: (ds, False) for st, ds in doors.items()}
        for st, ss in stairs.items():
            out.setdefault(st, (ss, True))
        return out
