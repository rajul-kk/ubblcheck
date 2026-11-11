"""Build the IFC-MY fire rule pack as buildingSMART IDS files.

Two files:
  my-ubbl-passive.ids  data an architect's model needs before passive fire
                       checks can run (UBBL Part VII)
  my-ubbl-active.ids   fire-system objects a mechanical model needs for the
                       active submission (UBBL Part VIII)

Property naming.  Standard IFC property sets are used wherever one carries the
datum (Pset_WallCommon.FireRating, Pset_DoorCommon.FireExit, ...).  Data with
no IFC home (UBBL purpose group, compartment id, declared travel distance) goes
in a custom set, MY_FireSafety.  The CIDB NBeS IFC parameter guideline is not
public; MY_FireSafety is provisional and is meant to be renamed to whatever
NBeS fixes once that guideline can be read.

Run:  python -m ubblcheck.ids_build [outdir]   (default: the packaged src/ubblcheck/ids)
"""
from __future__ import annotations

import os
import sys

from ifctester import ids

from . import ubbl

CUSTOM_PSET = "MY_FireSafety"
VERSION = "0.1.0"
AUTHOR = "ubblcheck@example.org"   # IDS requires an e-mail shaped author; placeholder

# FireRating is an IfcLabel in IFC.  Convention used here: minutes, optionally
# prefixed "FRP" (or the EN classes REI, EI, R) and suffixed "min", or hours ("1 HR", "1/2 hour").
# Keep in step with ifccheck.frp_minutes.
FRP_PATTERN = (r"((FRP|REI|EI|R) ?)?(30|60|90|120|180|240) ?(min|mins|minutes|MIN)?"
               r"|(FRP ?)?(1/2|1|1\.5|2|3|4) ?(hr|hrs|hour|hours|HR|HRS)")
IFC = ["IFC2X3", "IFC4"]


def _enum(values):
    return ids.Restriction(options={"enumeration": list(values)}, base="string")


def _pattern(p):
    return ids.Restriction(options={"pattern": p}, base="string")


def _spec(name, ident, desc, instr, ifc=IFC, min_occurs=0):
    return ids.Specification(name=name, identifier=ident, description=desc, instructions=instr,
                             ifcVersion=ifc, minOccurs=min_occurs,
                             maxOccurs="unbounded")


def passive() -> ids.Ids:
    doc = ids.Ids(title="IFC-MY fire data: passive (UBBL 1984 Part VII)", version=VERSION,
                  author=AUTHOR, description=(
                      "Model data needed to check a building against UBBL 1984 Part VII "
                      "(fire requirements) before a Bomba passive submission. "
                      f"{CUSTOM_PSET} is a provisional property set pending the CIDB NBeS IFC guideline."),
                  purpose="Pre-submission check", milestone="Bomba passive submission")

    s = _spec("Building purpose group", "MY-P01",
              "Every building declares its UBBL purpose group.",
              "By-law 134 and Fifth Schedule. Values I-VIII.", min_occurs=1)
    s.applicability.append(ids.Entity(name="IFCBUILDING"))
    s.requirements.append(ids.Property(propertySet=CUSTOM_PSET, baseName="PurposeGroup",
                                       value=_enum(ubbl.PURPOSE_GROUPS), dataType="IFCLABEL"))
    doc.specifications.append(s)

    s = _spec("Building height", "MY-P02",
              "Every building states its height as measured under by-law 135(a).",
              "Height thresholds in the Fifth Schedule (28 m) and by-law 137 (30 m) depend on it.",
              min_occurs=1)
    s.applicability.append(ids.Entity(name="IFCBUILDING"))
    s.requirements.append(ids.Property(propertySet=CUSTOM_PSET, baseName="BuildingHeight",
                                       dataType="IFCLENGTHMEASURE"))
    doc.specifications.append(s)

    s = _spec("Sprinkler protection declared", "MY-P03",
              "Every building says whether it has an automatic sprinkler installation.",
              "By-law 136 proviso doubles compartment limits; Seventh Schedule travel "
              "distances differ for sprinklered buildings.", min_occurs=1)
    s.applicability.append(ids.Entity(name="IFCBUILDING"))
    s.requirements.append(ids.Property(propertySet="Pset_BuildingCommon",
                                       baseName="SprinklerProtectionAutomatic", dataType="IFCBOOLEAN"))
    doc.specifications.append(s)

    s = _spec("Storey elevation", "MY-P04", "Every storey has an elevation.",
              "Needed to find floors more than 9 m above ground (by-law 137).", min_occurs=1)
    s.applicability.append(ids.Entity(name="IFCBUILDINGSTOREY"))
    s.requirements.append(ids.Attribute(name="Elevation"))
    doc.specifications.append(s)

    s = _spec("Space compartment", "MY-P05",
              "Every space names the fire compartment it belongs to.",
              "By-law 136: storey area and volume are limited per compartment.", min_occurs=1)
    s.applicability.append(ids.Entity(name="IFCSPACE"))
    s.requirements.append(ids.Property(propertySet=CUSTOM_PSET, baseName="CompartmentId",
                                       dataType="IFCLABEL"))
    doc.specifications.append(s)

    s = _spec("Space floor area", "MY-P06", "Every space carries a net floor area quantity.",
              "By-law 175-176 and Seventh Schedule: occupant load is floor area over the load factor.",
              min_occurs=1)
    s.applicability.append(ids.Entity(name="IFCSPACE"))
    s.requirements.append(ids.Property(propertySet="Qto_SpaceBaseQuantities", baseName="NetFloorArea"))
    doc.specifications.append(s)

    s = _spec("Space travel distance", "MY-P07",
              "Every space states the longest travel distance from it to the nearest storey exit.",
              "By-law 165 and Seventh Schedule. The value is the designer's measured distance "
              "along the walking line; the check compares it with the limit.", min_occurs=1)
    s.applicability.append(ids.Entity(name="IFCSPACE"))
    s.requirements.append(ids.Property(propertySet=CUSTOM_PSET, baseName="MaxTravelDistance",
                                       dataType="IFCLENGTHMEASURE"))
    doc.specifications.append(s)

    for ent, pset, ident, what in (("IFCWALL", "Pset_WallCommon", "MY-P08", "wall"),
                                   ("IFCSLAB", "Pset_SlabCommon", "MY-P09", "slab")):
        s = _spec(f"Compartment {what} fire rating", ident,
                  f"Every {what} marked as compartmentation has a fire resistance period.",
                  "By-law 148 and Ninth Schedule. FireRating in minutes (e.g. 60, FRP 60 min) or hours (1 HR).")
        s.applicability.append(ids.Entity(name=ent))
        s.applicability.append(ids.Property(propertySet=pset, baseName="Compartmentation",
                                            value="TRUE", dataType="IFCBOOLEAN"))
        s.requirements.append(ids.Property(propertySet=pset, baseName="FireRating",
                                           value=_pattern(FRP_PATTERN), dataType="IFCLABEL"))
        doc.specifications.append(s)

    s = _spec("Door exit flag", "MY-P10", "Every door says whether it is a fire exit.",
              "By-law 167-177: storey exits are counted and their widths summed.", min_occurs=1)
    s.applicability.append(ids.Entity(name="IFCDOOR"))
    s.requirements.append(ids.Property(propertySet="Pset_DoorCommon", baseName="FireExit",
                                       dataType="IFCBOOLEAN"))
    doc.specifications.append(s)

    s = _spec("Exit door width", "MY-P11", "Every exit door has an overall width.",
              "By-law 176 and Seventh Schedule: exit capacity in 550 mm units of clear width.")
    s.applicability.append(ids.Entity(name="IFCDOOR"))
    s.applicability.append(ids.Property(propertySet="Pset_DoorCommon", baseName="FireExit",
                                        value="TRUE", dataType="IFCBOOLEAN"))
    s.requirements.append(ids.Attribute(name="OverallWidth"))
    doc.specifications.append(s)

    s = _spec("Fire door rating format", "MY-P12",
              "A door fire rating, where given, is a fire resistance period.",
              "By-law 162-163: half-hour and one-hour doors.")
    s.applicability.append(ids.Entity(name="IFCDOOR"))
    s.applicability.append(ids.Property(propertySet="Pset_DoorCommon", baseName="FireRating",
                                        dataType="IFCLABEL"))
    s.requirements.append(ids.Property(propertySet="Pset_DoorCommon", baseName="FireRating",
                                       value=_pattern(FRP_PATTERN), dataType="IFCLABEL"))
    doc.specifications.append(s)

    s = _spec("Stair exit flag", "MY-P13", "Every stair says whether it is an escape stair.",
              "By-law 168 and 177: staircase number and width.")
    s.applicability.append(ids.Entity(name="IFCSTAIR"))
    s.requirements.append(ids.Property(propertySet="Pset_StairCommon", baseName="FireExit",
                                       dataType="IFCBOOLEAN"))
    doc.specifications.append(s)
    return doc


# Active systems: IFC4 entity / predefined type and the by-law behind it.
# indoor objects must sit on a storey; hydrants and inlets may sit on the site.
# All carry a Tag so the model can be matched to the tags on the 2D drawings.
ACTIVE_OBJECTS = [
    ("MY-A01", "Fire hydrant", "IFCFIRESUPPRESSIONTERMINAL", "FIREHYDRANT", False,
     "By-law 225(2): at least one hydrant within 91.5 m of fire brigade access"),
    ("MY-A02", "Breeching inlet", "IFCFIRESUPPRESSIONTERMINAL", "BREECHINGINLET", False,
     "By-law 229-231: dry or wet rising system inlets"),
    ("MY-A03", "Hose reel", "IFCFIRESUPPRESSIONTERMINAL", "HOSEREEL", True,
     "By-law 225(1) and Tenth Schedule"),
    ("MY-A04", "Sprinkler head", "IFCFIRESUPPRESSIONTERMINAL", "SPRINKLER", True,
     "By-law 225(1), 226 and Tenth Schedule"),
    ("MY-A05", "Fire alarm break glass", "IFCALARM", "BREAKGLASSBUTTON", True,
     "By-law 237 and Tenth Schedule"),
    ("MY-A06", "Fire alarm bell", "IFCALARM", "BELL", True, "By-law 237 and Tenth Schedule"),
    ("MY-A07", "Smoke detector", "IFCSENSOR", "SMOKESENSOR", True,
     "By-law 153 (lift lobbies), 225(1) and Tenth Schedule"),
    ("MY-A08", "Heat detector", "IFCSENSOR", "HEATSENSOR", True, "By-law 225(1) and Tenth Schedule"),
]


def active() -> ids.Ids:
    doc = ids.Ids(title="IFC-MY fire data: active (UBBL 1984 Part VIII)", version=VERSION,
                  author=AUTHOR, description=(
                      "How fire-system objects are modelled for a Bomba active submission. Which "
                      "systems a building needs (Tenth Schedule, by-law 229, sprinkler flag) is "
                      "decided by the rule checks, not here."),
                  purpose="Pre-submission check", milestone="Bomba active submission")
    for ident, name, ent, ptype, indoor, instr in ACTIVE_OBJECTS:
        where = " and contained in a storey" if indoor else ""
        s = _spec(name, ident, f"{name} objects are {ent}.{ptype}, tagged{where}.", instr, ifc=["IFC4"])
        s.applicability.append(ids.Entity(name=ent, predefinedType=ptype))
        s.requirements.append(ids.Attribute(name="Tag"))
        if indoor:
            s.requirements.append(ids.PartOf(name="IFCBUILDINGSTOREY",
                                             relation="IFCRELCONTAINEDINSPATIALSTRUCTURE"))
        doc.specifications.append(s)
    return doc


def build(outdir: str) -> list[str]:
    os.makedirs(outdir, exist_ok=True)
    paths = []
    for name, fn in (("my-ubbl-passive.ids", passive), ("my-ubbl-active.ids", active)):
        p = os.path.join(outdir, name)
        fn().to_xml(p)
        paths.append(p)
    return paths


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    for p in build(argv[0] if argv else os.path.join(os.path.dirname(__file__), "ids")):
        print(p)


if __name__ == "__main__":
    main()
