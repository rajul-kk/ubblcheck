"""UBBL 1984 fire tables as data.

Values are transcribed from the JKR consolidated text of the Uniform Building
By-Laws 1984 (Fifth and Seventh Schedules, by-laws 134-177).  Each table keeps
the clause it came from so a finding can cite it.  Where the consolidated text
looks like a transcription slip the value used here is noted in ERRATA so a
reviewer can check it against the gazetted copy of the state in question.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

SOURCE = "Uniform Building By-Laws 1984 (JKR consolidated text)"

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


def occupant_load(area_m2: float, group: str, use: str | None = None) -> int:
    key = use if use in OCCUPANT_LOAD else (f"{group}.{use}" if use else DEFAULT_LOAD_USE.get(group))
    if key not in OCCUPANT_LOAD:
        raise KeyError(f"no occupant load factor for {group}/{use}")
    per, _ = OCCUPANT_LOAD[key]
    return math.ceil(area_m2 / per)


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


# Other numeric thresholds used by checks.
COMPARTMENT_FLOOR_HEIGHT_M = 30     # by-law 137
COMPARTMENT_FLOOR_ABOVE_M = 9       # by-law 137
DIRECT_DISTANCE_FRACTION = 2 / 3    # by-law 165(1)
ROOM_TRAVEL_M = 15                  # by-law 165(3)
FIRE_DOOR_FRP_MIN = (30, 60, 120, 240)  # minutes, by-law 162-163 and Ninth Schedule
MIN_EXITS = 2                        # by-law 177(d)
