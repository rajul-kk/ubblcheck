"""Fire resistance ratings as models write them (an IfcLabel such as 'FRP 60 min' or '1 HR').

The checker (model.rating) and the IDS pack (ids_build) both read the convention from here.
"""
from __future__ import annotations

import re

# FireRating is an IfcLabel.  The IFC-MY convention: minutes, optionally prefixed "FRP", an EN
# class (REI, EI, R) or a door mark (FD, FRD), optionally followed by a minutes word; or hours
# ("1 HR", "1/2 hour", "2 HR FRP").  The checker reads any number of minutes or hours, in any case;
# the IDS pack (ids_build) accepts the standard periods as written here.
RATING_PREFIXES = ("FRP", "REI", "EI", "R", "FD", "FRD")
MINUTE_WORDS = ("min", "mins", "minute", "minutes", "MIN")
HOUR_WORDS = ("hr", "hrs", "hour", "hours", "HR", "HRS")
STANDARD_MINUTES = ("30", "60", "90", "120", "180", "240")     # regex fragments
STANDARD_HOURS = ("1/2", "1", r"1\.5", "2", "3", "4")


def rating_pattern(minutes=STANDARD_MINUTES, hours=STANDARD_HOURS) -> str:
    """The FireRating convention as a regular expression (XSD-compatible) over the given periods
    (regex fragments).  Group 3 holds the minutes, group 6 the hours."""
    words = lambda ws: "|".join(re.escape(w) for w in ws)     # noqa: E731
    return (f"(({words(RATING_PREFIXES)}) ?)?({'|'.join(minutes)}) ?({words(MINUTE_WORDS)})?"
            f"|(FRP ?)?({'|'.join(hours)}) ?({words(HOUR_WORDS)})( FRP)?")


_RATING = re.compile(rating_pattern(minutes=[r"\d+"], hours=["1/2", r"\d+(?:\.\d+)?"]), re.I)


# SurfaceSpreadOfFlame is an IfcLabel too: the class of by-law 204 (BS 476 Part 7), 'Class 0' (or
# 'Class O') the best, then 1 to 4.
_FLAME = re.compile(r"(?:class\s*)?([0o1-4])", re.I)


def flame_class(label) -> int | None:
    """'Class 0', 'Class O', 'class 1', '2' -> 0..4 (lower is better); None if unreadable."""
    m = _FLAME.fullmatch(str(label).strip()) if label is not None else None
    return None if m is None else (0 if m.group(1) in "oO" else int(m.group(1)))


def frp_minutes(label) -> int | None:
    """'60', 'FRP 60 min', 'REI 120', 'FD60', '120 minutes', '1 HR', '2 HR FRP', '1/2 hour' -> minutes;
    None if unreadable."""
    if label is None:
        return None
    m = _RATING.fullmatch(str(label).strip())
    if not m:
        return None
    if m.group(3) is not None:
        return int(m.group(3))
    return int(round((0.5 if m.group(6) == "1/2" else float(m.group(6))) * 60))
