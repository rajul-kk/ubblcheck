"""UBBL 1984 fire tables as data.

Values are transcribed from the JKR consolidated text of the Uniform Building
By-Laws 1984 (the Fifth, Seventh, Ninth and Tenth Schedules and the Part VII and VIII
rules that read them; docs/bylaws.md lists which).  Each table keeps
the clause it came from so a finding can cite it.  Where the consolidated text
looks like a transcription slip the value used here is noted in ERRATA so a
reviewer can check it against the gazetted copy of the state in question.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

PURPOSE_GROUPS = {
    "I": "Small residential",
    "II": "Institutional",
    "III": "Other residential",
    "IV": "Office",
    "V": "Shop",
    "VI": "Factory",
    "VII": "Place of assembly",
    "VIII": "Storage and general",
}

INF = math.inf

# Fifth Schedule, dimensions of buildings and compartments (by-law 136).
# (group, single_storey, height_limit_m or None, max_storey_area_m2, max_volume_m3)
# height_limit: rows apply to height <= limit ("not exceeding"); None = any height.
_DIM = [
    ("II", False, None, 2000, INF),
    ("III", False, 28, 3000, 8500),
    ("III", False, INF, 2000, 5500),
    ("V", False, None, 2000, 7000),
    ("VI", False, 28, INF, 28000),
    ("VI", False, INF, 2000, 5500),
    ("VIII", False, 28, INF, 21000),
    ("VIII", False, INF, 1000, INF),
    ("II", True, None, 3000, INF),
    ("III", True, None, 3000, INF),
]


@dataclass(frozen=True)
class CompartmentLimit:
    area_m2: float
    volume_m3: float
    clause: str


def compartment_limit(group: str, height_m: float, single_storey: bool = False,
                      sprinklered: bool = False) -> CompartmentLimit | None:
    """Limit on storey area and volume of a compartment, or None if the
    Fifth Schedule sets none (groups I, IV, VII; single-storey IV-VIII).

    By-law 136 proviso: limits double in a building with an automatic
    sprinkler installation."""
    for g, ss, hmax, area, vol in _DIM:
        if g != group or ss != single_storey:
            continue
        if hmax is not None and hmax != INF and height_m > hmax:
            continue
        k = 2 if sprinklered else 1
        return CompartmentLimit(area * k, vol * k, "UBBL by-law 136, Fifth Schedule"
                                + (" (doubled: sprinklered)" if sprinklered else ""))
    return None


# Seventh Schedule, maximum travel distances (by-law 165(4)).
# key -> (group, dead_end_m, unsprinklered_m, sprinklered_m); None = NR.
TRAVEL = {
    "I": ("I", None, None, None),
    "II.hospital": ("II", 9, 30, 45),
    "II.school": ("II", 6, 45, 60),
    "II.open_plan": ("II", None, 30, 45),
    "II.flexible_plan": ("II", None, 45, 60),
    "III.hotel": ("III", 10, 30, 45),
    "III.flat": ("III", 10, 30, 45),
    "III.dormitory": ("III", 0, 30, 45),
    "IV": ("IV", 15, 45, 60),
    "V": ("V", 15, 45, 60),
    "VI.general": ("VI", 15, 30, 45),
    "VI.high_hazard": ("VI", 0, 22, 22.5),
    "VI.open_structure": ("VI", None, None, None),
    "VII": ("VII", None, 45, 61),
    "VIII.low_ordinary": ("VIII", None, None, None),
    "VIII.high_hazard": ("VIII", None, 22.5, 30),
    "VIII.parking": ("VIII", 15, 30, 45),
}
# default sub-use for a group with several rows
DEFAULT_USE = {"II": "II.hospital", "III": "III.flat", "VI": "VI.general", "VIII": "VIII.low_ordinary"}

ERRATA = {
    "NINTH.V.1": "Ninth Schedule Part 1, first Shop line: the JKR text prints the floor area as "
                 "'1 150'; 150 m2 is used, which fits the 7.5 m / 500 m2 line below it.",
    "NINTH.VI.2": "Ninth Schedule Part 1, second Factory line, and Part 2, first Factory line: the "
                  "period prints as '?'; half an hour is used, as in the matching lines of other groups.",
    "NINTH.VII.3": "Ninth Schedule Part 1, third Assembly line: the period prints as '(b)'; 1(b) is "
                   "used, as in the matching Office line.",
    "TENTH.II.edu.d": "Tenth Schedule, educational buildings of 11 storeys and above: the "
                      "extinguishing column prints '---'; hose reels (G) are required here, as for "
                      "6 to 10 storeys.",
    "TENTH.VIII.gap": "Tenth Schedule, single-storey general storage: no line covers 500 m2 to "
                      "1 400 m3; the 250-500 m2 line (G, 2) is used.",
    "TENTH.II.ac": "Tenth Schedule II 1(vii), educational buildings with central air-conditioning: "
                   "three result lines stand against 'two or more storeys 500 sq. m - 1 000 sq. m "
                   "exceeding 1 000 sq. m'.  Read as: over 1 000 m2 (AG, 2), 500-1 000 m2 (G, 1 & 2), "
                   "otherwise two or more storeys (G, 1 & 2), lighting b or c throughout.",
    "TENTH.III.dorm.3": "Tenth Schedule III 2(a): 3 storeys falls in both '2 - 3 storeys' (G, 2, a) and "
                        "'3 storeys to 10 storeys' (G, 1 & 2, a); the stricter second line is used.",
    "TENTH.III.hotel.10": "Tenth Schedule III 1(ii): 'less than 10 rooms' and '11 to 20 rooms' leave "
                          "10 rooms out; it is read with the first line.",
    "TENTH.VIII.park": "Tenth Schedule VIII 1, car parks: 'Open car-parks above ground' prints G, 2, a "
                       "and its two sub-lines (4 storeys and below, 5 storeys and over) both H & G, 2, a; "
                       "H & G, 2, a is used for every multi-storey car park.",
    "V": "JKR text prints the Shops row as 15 / 60 / 45 (sprinklered below unsprinklered, "
         "unlike every other row); 45 / 60 is used here. Check the gazetted state copy.",
}


def use_key(group: str, use: str | None = None) -> str:
    if use:
        k = use if "." in use or use in TRAVEL else f"{group}.{use}"
        if k in TRAVEL:
            return k
        raise KeyError(f"unknown use {use!r} for purpose group {group}")
    return DEFAULT_USE.get(group, group)


@dataclass(frozen=True)
class TravelLimit:
    travel_m: float | None
    dead_end_m: float | None
    clause: str = "UBBL by-law 165(4), Seventh Schedule"


def travel_limit(group: str, sprinklered: bool, use: str | None = None) -> TravelLimit:
    _, dead, unspr, spr = TRAVEL[use_key(group, use)]
    return TravelLimit(spr if sprinklered else unspr, dead)


# Seventh Schedule, occupant load and capacity of exits (by-law 175-177).
# use -> (m2 per person, basis) ; capacity: group -> persons per 550 mm unit
OCCUPANT_LOAD = {
    "II.classroom": (2, "net"),
    "II.workshop": (4.5, "net"),
    "II.nursery": (3.5, "net"),
    "II.sleeping": (12, "gross"),
    "II.inpatient": (24, "gross"),
    "III": (20, "gross"),
    "III.flat": (4, "gross"),
    "III.hotel_public": (24, "gross"),
    "IV": (10, "gross"),
    "V.street": (3, "gross"),
    "V.other": (6, "gross"),
    "V.storage": (10, "gross"),
    "VI": (10, "gross"),
    "VII": (1.5, "net"),
    "VII.concentrated": (0.7, "net"),
    "VII.standing": (0.3, "net"),
}
DEFAULT_LOAD_USE = {"II": "II.classroom", "III": "III", "IV": "IV", "V": "V.other", "VI": "VI", "VII": "VII"}

# persons per unit of exit width: (doors_outside, horizontal, ramp_main, ramp_sec, escalator, stairs)
EXIT_CAPACITY = {
    "II": (100, 100, 100, 60, None, 60),
    "II.hospital": (30, 30, 30, 30, None, 22),
    "III": (100, 60, 60, 60, 45, 45),
    "IV": (100, 100, 100, 60, 60, 60),
    "V": (100, 100, 100, 60, 60, 60),
    "VI": (100, 100, 100, 60, 60, 60),
    "VII": (100, 100, 100, 75, 75, 75),
}
EXIT_KINDS = ("door", "horizontal", "ramp_main", "ramp_secondary", "escalator", "stair")
UNIT_WIDTH_MM = 550


def load_row(group: str | None, use: str | None = None) -> str | None:
    """The Seventh Schedule occupant-load row for a use in a group ('classroom', or 'II.classroom'),
    or the group's own row when no use is given.  None when there is no such row."""
    key = use if use in OCCUPANT_LOAD else (f"{group}.{use}" if use else DEFAULT_LOAD_USE.get(group))
    return key if key in OCCUPANT_LOAD else None


def occupant_load_for_row(area_m2: float, row: str) -> int:
    """Persons in `area_m2` at the load factor of a row from load_row()."""
    per, _ = OCCUPANT_LOAD[row]
    return math.ceil(area_m2 / per)


def occupant_load(area_m2: float, group: str, use: str | None = None) -> int:
    row = load_row(group, use)
    if row is None:
        raise KeyError(f"no occupant load factor for {group}/{use}")
    return occupant_load_for_row(area_m2, row)


def exit_units(persons: int, group: str, kind: str = "door", use: str | None = None) -> int:
    """Units of 550 mm exit width needed for `persons` (by-law 176(e))."""
    cap = EXIT_CAPACITY.get(f"{group}.{use}") or EXIT_CAPACITY[group]
    c = cap[EXIT_KINDS.index(kind)]
    if c is None:
        raise ValueError(f"exit kind {kind} not permitted for group {group}")
    return max(1, math.ceil(persons / c))


def width_units(clear_width_mm: float) -> int:
    """Whole 550 mm units in a clear opening width."""
    return int(clear_width_mm // UNIT_WIDTH_MM)


# By-law 140, fire appliance access: building volume -> share of perimeter
# that must abut a >= 12 m road or open space.
ACCESS_SCALE = [(7000, 1 / 6), (28000, 1 / 4), (56000, 1 / 2), (84000, 3 / 4), (112000, 1.0)]
ACCESS_ROAD_MIN_M = 12


def access_perimeter_share(volume_m3: float) -> float:
    """Minimum proportion of perimeter abutting fire appliance access.
    0 up to 7000 m3 ("in excess of 7000"); 1.0 means island site."""
    share = 0.0
    for v, s in ACCESS_SCALE:
        if volume_m3 > v:
            share = s
    return share


# By-laws 179-183 and 188, places of assembly.
ASSEMBLY_LOAD = {"concentrated": 0.65, "less_concentrated": 1.35, "standing": 3.0}   # 180: m2 net per person
ASSEMBLY_CLASSES = (("A", 1000), ("B", 300), ("C", 100))                         # 179: capacity at least
ASSEMBLY_UNIT_MM = 552          # 181: unit of exit width
ASSEMBLY_HALF_UNIT_MM = 300     # 181: 300 mm over one or more full units counts as half a unit
ASSEMBLY_MIN_DOOR_MM = 700      # 181: no individual access to exit narrower
ASSEMBLY_PERSONS_PER_UNIT = 100  # 183(a), (b)
ASSEMBLY_TRAVEL_M = (45, 60)    # 188: unsprinklered, sprinklered


def assembly_class(capacity: int) -> str | None:
    """By-law 179: class A, B or C of a place of assembly, or None under 100 persons."""
    return next((c for c, least in ASSEMBLY_CLASSES if capacity >= least), None)


def assembly_exits(capacity: int) -> int:
    """By-law 183(d)-(f): separate exits a place of assembly needs (0 under 100 persons)."""
    cls = assembly_class(capacity)
    return {"A": 4, "B": 3 if capacity > 600 else 2, "C": 2}.get(cls, 0)


def assembly_travel(lim: "TravelLimit", sprinklered: bool) -> "TravelLimit":
    """By-law 188: travel in a place of assembly is at most 45 m, 60 m sprinklered, where the
    building's own limit (`lim`) is looser."""
    m = ASSEMBLY_TRAVEL_M[1 if sprinklered else 0]
    if lim.travel_m is not None and lim.travel_m <= m:
        return lim
    return TravelLimit(m, lim.dead_end_m, "UBBL by-law 188")


def assembly_units(width_mm: float) -> float:
    """By-law 181: units of 552 mm exit width in an opening; 300 mm over a full unit is half a unit."""
    full, rest = divmod(width_mm, ASSEMBLY_UNIT_MM)
    return full + (0.5 if full >= 1 and rest >= ASSEMBLY_HALF_UNIT_MM else 0.0)


# Eighth Schedule (by-laws 204, 206): the lowest class of surface allowed, Class 0 best, by place:
# key (as TRAVEL) -> (exits, access to exits, other spaces' walls, other spaces' ceilings); None = NR.
# The public copy prints no rows for groups VII and VIII (its notes define the assembly classes, so
# rows were lost): those groups are not checked.
EIGHTH = {
    "I": (None, None, None, None),
    "II": (0, 0, 3, 3),                 # institutional, schools, open or flexible plan
    "II.hospital": (0, 0, 1, 1),        # 2 in a room for 4 or fewer: not applied (needs bed counts)
    "III": (1, 1, 3, 3),                # flats, dormitories
    "III.hotel": (0, 0, 3, 3),
    "IV": (1, 1, 3, 3),
    "V": (0, 0, 3, 2),                  # shop class A and B; class C (small shop): other spaces 3
    "VI": (3, 3, 3, 3),
}
SMALL_SHOP_M2 = 300                     # Eighth Schedule note: class C shop, ground floor only


def eighth_row(key: str) -> tuple | None:
    """The Eighth Schedule row for a TRAVEL key (its group's row when the use has none), or None."""
    return EIGHTH.get(key, EIGHTH.get(key.split(".")[0]))


BASEMENT_FLAME_CLASS = 1    # by-law 205(4): occupied underground spaces, Class 0 or 1


def flame_limit(row: tuple, place: str, surface: str, sprinklered: bool,
                small_shop: bool = False, underground: bool = False) -> int | None:
    """Worst class allowed (0-4) for a surface ('walls' or 'ceiling') in a place ('exits', 'access'
    or 'other'), or None (no requirement).  Sprinklers let each class go one lower, to Class 3 at
    most (by-law 205(3)); an occupied space underground with no direct exit outside is Class 1 at
    worst, sprinklered or not (205(4))."""
    exits, access, walls, ceiling = row
    need = {"exits": exits, "access": access}.get(place, walls if surface == "walls" else ceiling)
    if place == "other" and need is not None and small_shop:
        need = 3
    if need is not None and sprinklered and need < 3:
        need += 1
    if underground:
        need = BASEMENT_FLAME_CLASS if need is None else min(need, BASEMENT_FLAME_CLASS)
    return need


# Other numeric thresholds used by checks.
COMPARTMENT_FLOOR_HEIGHT_M = 30     # by-law 137
COMPARTMENT_FLOOR_ABOVE_M = 9       # by-law 137
BASEMENT_FLOOR_M2 = 100             # by-law 138(d): a basement larger than this has a compartment floor over it
DIRECT_DISTANCE_FRACTION = 2 / 3    # by-law 165(1)
ROOM_TRAVEL_M = 15                  # by-law 165(3)
ROOM_PERSONS = 6                    # by-law 165(3): rooms for this many or fewer are measured from the door
HYDRANT_DISTANCE_M = 91.5           # by-law 225(2): a hydrant this close to fire brigade access
RISER_HEIGHT_M = 18.3               # by-law 229(1): above this topmost floor, a rising system and a fire lift
WET_RISER_HEIGHT_M = 30.5           # by-law 231: above this, the rising system is wet
FIRE_DOOR_FRP_MIN = 30   # minutes: a fire door (by-law 162) resists fire half an hour at least
MIN_EXITS = 2                        # by-law 177(d)
COMMAND_CENTRE_HEIGHT_M = 30.5      # by-law 238: buildings over this height have a command and control centre
FIRE_LOBBY_MIN_M2 = 5.57             # by-law 242(a): floor area of each fire-fighting access lobby
PROTECTED_SHAFT_FRP_MIN = 60         # by-law 150(3): protecting structures of an hour or more; 1 hour is the floor used here
RISK_AREA_FRP_MIN = 60               # by-law 139: the local authority sets the period; 1 hour is the floor used here
EXIT_SPACING_M = 5                   # by-law 174(1): between the nearest edges of the openings
SINGLE_STAIR_TOP_M = 12              # by-law 194: topmost floor at most this high
SINGLE_STAIR_CONDITIONS = (
    "every element of structure has at least 1 hour FRP (a)",
    "the stair from ground to first floor is separated from the rest of the ground floor by a "
    "2-hour wall (i)",
    "travel distance is at most 12 m from the room door to the exit, and 12 m within the room "
    "(iii); up to 30 m on ground and first storeys with escape windows (iv)",
    "no use other than domestic or office, except a shop or car park on the ground storey (b)",
)


def single_stair_allowed(group: str | None, use: str | None, top_floor_m: float) -> bool:
    """By-law 194: may this building have a single staircase?  Domestic (group I, or group III
    flats) or office (group IV) use, with the topmost floor at most 12 m up.  The other conditions
    are listed in SINGLE_STAIR_CONDITIONS for the submitting person to confirm."""
    domestic_or_office = group in ("I", "IV") or (group == "III" and use in (None, "flat"))
    return domestic_or_office and top_floor_m <= SINGLE_STAIR_TOP_M + 1e-9


# ---------------------------------------------------------------- Ninth Schedule
# Minimum periods of fire resistance for elements of structure (by-law 213; also cited by
# 142, 147, 158, 162 and 216).  Part 1, buildings other than single storey:
# (group, max_storeys, height_m, floor_area_m2, volume_m3, above_hours, basement_hours, marks)
# height: the line applies to buildings not exceeding it; "over 28" is no limit.
# floor area: of each storey of the building, or of the compartment the element is in;
# volume: of the building, or of that compartment.  The first line all three fit applies.
# marks: (a)-(c) are the notes below; x = buildings only (not compartments) except group
# III; y = limits in the area and volume columns double when sprinklered throughout.
_NINTH_1 = [
    ("I", 3, INF, INF, INF, 0.5, 1.0, "a x"),
    ("I", 4, INF, 250, INF, 1.0, 1.0, "b x"),
    ("I", None, INF, INF, INF, 1.0, 1.5, ""),
    ("II", None, 28, 2000, INF, 1.0, 1.5, ""),
    ("II", None, INF, 2000, INF, 1.5, 2.0, ""),
    ("III", 2, INF, 500, INF, 0.5, 1.0, "x"),
    ("III", 3, INF, 250, INF, 1.0, 1.0, "b"),
    ("III", None, 28, 3000, 8500, 1.0, 1.5, ""),
    ("III", None, INF, 2000, 5500, 1.5, 2.0, ""),
    ("IV", None, 7.5, 250, INF, 0.0, 1.0, "c x"),
    ("IV", None, 7.5, 500, INF, 0.5, 1.0, ""),
    ("IV", None, 15, INF, 3500, 1.0, 1.0, "b"),
    ("IV", None, 28, 5000, 14000, 1.0, 1.5, ""),
    ("IV", None, INF, INF, INF, 1.5, 2.0, ""),
    ("V", None, 7.5, 150, INF, 0.0, 1.0, "c x"),        # ERRATA NINTH.V.1
    ("V", None, 7.5, 500, INF, 0.5, 1.0, ""),
    ("V", None, 15, INF, 3500, 1.0, 1.0, "b"),
    ("V", None, 28, 1000, 7000, 1.0, 2.0, ""),
    ("V", None, INF, 2000, 7000, 2.0, 4.0, "y"),
    ("VI", None, 7.5, 250, INF, 0.0, 1.0, "c x"),
    ("VI", None, 7.5, INF, 1700, 0.5, 1.0, ""),         # ERRATA NINTH.VI.2
    ("VI", None, 15, INF, 4250, 1.0, 1.0, "b"),
    ("VI", None, 28, INF, 8500, 1.0, 2.0, ""),
    ("VI", None, 28, INF, 28000, 2.0, 4.0, ""),
    ("VI", None, INF, 2000, 5500, 2.0, 4.0, ""),
    ("VII", None, 7.5, 250, INF, 0.0, 1.0, "c x"),
    ("VII", None, 7.5, 500, INF, 0.5, 1.0, ""),
    ("VII", None, 15, INF, 3500, 1.0, 1.0, "b"),        # ERRATA NINTH.VII.3
    ("VII", None, 28, 5000, 14000, 1.0, 1.5, ""),
    ("VII", None, INF, INF, INF, 1.5, 2.0, ""),
    ("VIII", None, 7.5, 150, INF, 0.0, 1.0, "c x"),
    ("VIII", None, 7.5, 300, INF, 0.5, 1.0, ""),
    ("VIII", None, 15, INF, 1700, 1.0, 1.0, "b"),
    ("VIII", None, 15, INF, 3500, 1.0, 2.0, ""),
    ("VIII", None, 28, INF, 7000, 2.0, 4.0, ""),
    ("VIII", None, 28, INF, 21000, 4.0, 4.0, ""),
    ("VIII", None, INF, 1000, INF, 4.0, 4.0, ""),
]
# Part 2, single storey buildings: (group, floor_area_m2, hours)
_NINTH_2 = [
    ("I", INF, 0.5), ("II", 3000, 0.5), ("III", 3000, 0.5),
    ("IV", 3000, 0.5), ("IV", INF, 1.0),
    ("V", 2000, 0.5), ("V", 3000, 1.0), ("V", INF, 2.0),
    ("VI", 2000, 0.5), ("VI", 3000, 1.0), ("VI", INF, 2.0),   # ERRATA NINTH.VI.2
    ("VII", 3000, 0.5), ("VII", INF, 1.0),
    ("VIII", 500, 0.5), ("VIII", 1000, 1.0), ("VIII", 3000, 2.0), ("VIII", INF, 4.0),
]
NINTH_CLAUSE = "UBBL by-law 213, 216, Ninth Schedule"
WALL_MIN_CLAUSE = "UBBL by-law 214(1)"
EXTERNAL_WALL_MIN = 30      # by-law 214(1)(a); 214(2) frees a non-loadbearing external wall that may be an
                            # unprotected area, which needs the boundary, so only loadbearing ones are held to it
SEPARATING_WALL_MIN = 60    # by-law 214(1)(b)


@dataclass(frozen=True)
class FireResistance:
    above_min: int          # ground and upper storeys
    basement_min: int       # basement storeys, and the floor over a basement
    marks: str              # notes (a)-(c), x, y of the line used
    line: str               # the line, for the report

    @property
    def clause(self) -> str:
        return NINTH_CLAUSE


def _fmt(v):
    return "no limit" if v == INF else f"{v:g}"


def fire_resistance(group: str, height_m: float, floor_area_m2: float, volume_m3: float,
                    storeys: int = 2, sprinklered: bool = False,
                    compartment: bool = False) -> FireResistance | None:
    """Minimum FRP for elements of structure, or None if the building (or compartment)
    is larger than every line for its group, which means it has to be compartmented.

    storeys: storeys at or above ground; 1 selects Part 2 (single storey buildings).
    compartment: the area and volume are a compartment's.  Lines marked x apply to whole
    buildings only (except in group III), so they are skipped."""
    if storeys <= 1:
        for g, area, hrs in _NINTH_2:
            if g == group and floor_area_m2 <= area:
                m = int(round(hrs * 60))
                return FireResistance(m, m, "", f"Part 2: {group}, floor area up to {_fmt(area)} m2")
        return None
    for g, nst, h, area, vol, up, base, marks in _NINTH_1:
        if g != group or (nst is not None and storeys > nst):
            continue
        if compartment and "x" in marks and group != "III":
            continue
        k = 2 if sprinklered and "y" in marks else 1
        if height_m <= h and floor_area_m2 <= area * k and volume_m3 <= vol * k:
            line = (f"Part 1: {group}" + (f", up to {nst} storeys" if nst else "")
                    + f", height {_fmt(h)} m, floor area {_fmt(area * k)} m2, volume {_fmt(vol * k)} m3")
            return FireResistance(int(round(up * 60)), int(round(base * 60)), marks, line)
    return None


def element_frp(req: FireResistance, kind: str, basement: bool, compartment_floor: bool = True,
                basement_area_m2: float | None = None) -> int:
    """Required minutes for one element.  kind: 'floor', 'beam', 'column' or 'wall'.

    Note (b): a floor that is not a compartment floor needs only half an hour (its
    beams still need the full period).  Notes (a), (c): a basement of 50 m2 or less
    needs half an hour, or none."""
    if basement:
        m = req.basement_min
        if basement_area_m2 is not None and basement_area_m2 <= 50:
            if "c" in req.marks:
                m = 0
            elif "a" in req.marks:
                m = 30
        return m
    m = req.above_min
    if kind == "floor" and "b" in req.marks and not compartment_floor:
        m = min(m, 30)
    return m


# ---------------------------------------------------------------- Tenth Schedule
# Fire extinguishment, alarm systems and emergency lighting (by-law 225(1), 237(1)).
# The table is data/tenth.yaml; see its header for the row syntax and the codes.
TENTH_CLAUSE = "UBBL by-law 225(1), 237(1), Tenth Schedule"
EXTINGUISHING = {"A": "automatic sprinklers", "B": "water spray system", "C": "high-expansion foam",
                 "D": "carbon dioxide system", "E": "halogenated system", "F": "other automatic system",
                 "G": "hose reels", "H": "hydrant system"}
ALARMS = {"1": "automatic fire detectors", "2": "manual electrical fire alarm",
          "3": "signal indicator alarm", "4": "manual alarm"}
LIGHTING = {"a": "signal point units", "b": "central battery", "c": "generators"}
TENTH_INPUTS = ("storeys", "height_m", "floor_area_m2", "total_area_m2", "volume_m3", "rooms")
_COND = {"storeys": ("storeys", True), "rooms": ("rooms", True), "height": ("height_m", False),
         "floor_area": ("floor_area_m2", False), "total_area": ("total_area_m2", False),
         "volume": ("volume_m3", False)}      # row key -> (input, max is inclusive)
_TENTH = None


def _tenth_data() -> dict:
    global _TENTH
    if _TENTH is None:
        import os
        import yaml
        with open(os.path.join(os.path.dirname(__file__), "data", "tenth.yaml"), encoding="utf-8") as fh:
            _TENTH = yaml.safe_load(fh)
    return _TENTH


@dataclass(frozen=True)
class TenthRow:
    use: str
    title: str
    ref: str
    ext: tuple            # alternatives, each a frozenset of codes; () = none
    alarm: frozenset
    light: frozenset      # any one of these
    refer: str | None = None
    errata: str | None = None
    case_by_case: bool = False   # the Fire Department sets the systems; no line applies in general

    @property
    def clause(self) -> str:
        return TENTH_CLAUSE

    def requires(self, code: str) -> bool:
        """True if every way of meeting this line includes `code` (an extinguishing letter or alarm digit)."""
        if code in ALARMS:
            return code in self.alarm
        return bool(self.ext) and all(code in alt for alt in self.ext)

    def describe(self) -> str:
        parts = []
        if self.ext:
            parts.append(" or ".join(" and ".join(EXTINGUISHING[c] for c in sorted(alt)) for alt in self.ext))
        if self.alarm:
            parts.append(" and ".join(ALARMS[c] for c in sorted(self.alarm)))
        if self.light:
            parts.append("emergency lighting by " + " or ".join(LIGHTING[c] for c in sorted(self.light)))
        return "; ".join(parts) or "no fixed system, alarm or emergency lighting"


@dataclass(frozen=True)
class TenthLookup:
    use: str
    title: str
    row: TenthRow | None          # the line that applies, if one could be chosen
    missing: tuple = ()           # inputs needed to choose a line (row is None)

    @property
    def decided(self) -> bool:
        return self.row is not None


def tenth_uses() -> dict[str, str]:
    """Tenth Schedule use key -> title."""
    return {k: v["title"] for k, v in _tenth_data()["uses"].items()}


def tenth_use(group: str, sub_use: str | None = None, override: str | None = None) -> str:
    """The Tenth Schedule use: `override` if given, else the default for the Seventh Schedule use."""
    uses = _tenth_data()["uses"]
    if override:
        if override not in uses:
            raise KeyError(f"unknown Tenth Schedule use {override!r}")
        return override
    key = use_key(group, sub_use)
    default = _tenth_data()["default_use"]
    for k in (key, group):
        if k in default:
            return default[k]
    raise KeyError(f"no Tenth Schedule use for {key}; name one")


def _row(use, title, r) -> TenthRow:
    ext = tuple(frozenset(alt) for alt in str(r.get("ext") or "").split("|") if alt)
    alarm = frozenset(c for c in str(r.get("alarm") or "").split("&") if c)
    light = frozenset(c for c in str(r.get("light") or "").split("|") if c)
    return TenthRow(use, title, r["ref"], ext, alarm, light, r.get("refer"), r.get("errata"),
                    bool(r.get("case_by_case")))


def tenth(use: str, **inputs) -> TenthLookup:
    """The Tenth Schedule line for a use and a building's size.

    inputs (any may be None when not known): storeys, height_m (topmost floor above fire
    appliance access), floor_area_m2 (largest storey), total_area_m2, volume_m3, rooms.
    The first row whose conditions hold applies.  If the first row that could still apply
    depends on an input that is not known, no line is chosen and `missing` names the inputs."""
    bad = set(inputs) - set(TENTH_INPUTS)
    if bad:
        raise TypeError(f"unknown inputs {sorted(bad)}")
    spec = _tenth_data()["uses"][use]
    for r in spec["rows"]:
        unknown, ok = [], True
        for key, (name, inclusive) in _COND.items():
            if key not in r:
                continue
            lo, hi = r[key]
            v = inputs.get(name)
            if v is None:
                unknown.append(name)
                continue
            if (lo is not None and v < lo) or (hi is not None and (v > hi if inclusive else v >= hi)):
                ok = False
                break
        if not ok:
            continue
        if unknown:
            return TenthLookup(use, spec["title"], None, tuple(unknown))
        return TenthLookup(use, spec["title"], _row(use, spec["title"], r))
    return TenthLookup(use, spec["title"], None)
