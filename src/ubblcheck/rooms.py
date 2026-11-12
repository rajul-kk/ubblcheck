"""What rooms and objects are, from their names, in English and Malay.

Models seldom say what a room is used for except in its name, so the rules find rooms by name:
voids, fire risk rooms, fire-fighting lobbies, the command centre, basement stores, foam inlets,
and the purpose group a room's use suggests.  Every pattern is here, so a new word (or a new
language) is added in one place.
"""
from __future__ import annotations

import re

# not floor area: voids, double-height volumes, roof and ceiling spaces drawn as rooms
VOID = re.compile(r"open to below|\bvoid\b|terbuka ke bawah|double (height|volume)|upper part of"
                  r"|^\s*(roof|attic)( space)?\s*$|\b(roof|ceiling) space\b", re.I)

# by-law 238: the fire command centre, not any control room (CCTV, security, lift)
COMMAND_CENTRE = re.compile(r"command\s*(and|&)\s*control|fire\s+(command|control)\b|\bFCC\b"
                            r"|(bilik|pusat)\s+(kawalan|arahan)\s+(bomba|kebakaran)", re.I)

# by-law 242: a lobby, named with a fire-fighting word ('FFL' is finished floor level on drawings,
# so not used)
FIRE_LOBBY = re.compile(r"^(?=.*(\blobb(y|ies)\b|\blobi\b))(?=.*(fire\s*-?\s*fight|fire\s*m[ae]n|\bbomba\b))",
                        re.I | re.S)

# by-law 139(a)-(f), and the plant rooms Bomba treats the same way
RISK_ROOM = re.compile(r"boiler|laundr|linen|\bLPG\b|gas (store|storage|room)|gen-?set|generator room|transformer"
                       r"|substation|hazardous|\bdobi\b|dandang|janakuasa|pengubah|pencawang|bilik (linen|gas|genset)",
                       re.I)

# by-law 233: boiler rooms and storage areas ('store' alone is left out: in English it is as often a
# shop as a storage room)
BOILER_OR_STORE = re.compile(r"boiler|dandang|storage|store ?room|\bstores\b|\bstor\b|gudang", re.I)

FOAM_INLET = re.compile(r"foam[\s_-]*inlet|inlet[\s_-]*(busa|buih)|(busa|buih)[\s_-]*inlet"
                        r"|salur masuk (busa|buih)", re.I)

# room-use words -> purpose group (by-law 134).  Ancillary rooms carry no group.
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


def use_group(name: str) -> str | None:
    """The purpose group a room's name suggests, or None (ancillary, or no use word)."""
    if not name or ANCILLARY.search(name):
        return None
    return next((g for g, pat in USE_WORDS if re.search(pat, name, re.I)), None)
