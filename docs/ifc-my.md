# IFC-MY fire rule pack

A free set of [IDS](https://www.buildingsmart.org/standards/bsi-standards/information-delivery-specification-ids/)
files and authoring templates. Together they say what a model needs to
carry so that UBBL 1984 fire checks can run on it.

| File | For |
|---|---|
| `src/ubblcheck/ids/my-ubbl-passive.ids` | Architect's model, Bomba passive submission (UBBL Part VII) |
| `src/ubblcheck/ids/my-ubbl-active.ids` | M&E model, Bomba active submission (UBBL Part VIII) |
| `templates/revit/MY_FireSafety_SharedParameters.txt` | Revit shared parameters |
| `templates/revit/MY_FireSafety_IfcPropertySets.txt` | Revit IFC exporter, user-defined property sets |
| `templates/archicad/MY_FireSafety_properties.csv` | ArchiCAD Property Manager set-up sheet |

Any IDS-aware tool reads the IDS files, including IfcTester, Solibri, BIMcollab
ZOOM and usBIM. `ubblcheck ifc model.ifc` runs them together with the rule
checks below and writes a BCF file you can open in the authoring tool.

## Two layers

1. **Data present (IDS).** These checks ask whether the model says what the
   rules need. Is the purpose group declared? Does each space name its
   compartment? Do exit doors have widths?
2. **Data complies (`ubblcheck.ifccheck`).** These checks compare the declared
   data with the Fifth and Seventh Schedules and the Part VII/VIII by-laws.
   IDS cannot express sums across objects or limits that depend on a
   building-level value, so this layer is plain Python.

## Where each datum lives

Standard IFC property sets are used wherever one already holds the value.
Data with no IFC home goes in the custom set `MY_FireSafety`.

| Datum | IFC location | Basis |
|---|---|---|
| Purpose group I–VIII | `IfcBuilding` · `MY_FireSafety.PurposeGroup` | by-law 134, Fifth Schedule |
| Seventh Schedule row (hospital, school, flat…) | `IfcBuilding` · `MY_FireSafety.SubUse` | Seventh Schedule |
| Building height | `IfcBuilding` · `MY_FireSafety.BuildingHeight` | by-law 135(a) |
| Tenth Schedule row, when the group's default does not fit (`II.library`, `VIII.storage`…) | `IfcBuilding` · `MY_FireSafety.TenthUse` | Tenth Schedule |
| Number of rooms (hotels) | `IfcBuilding` · `MY_FireSafety.Rooms` | Tenth Schedule III 1 |
| Sprinklered | `IfcBuilding` · `Pset_BuildingCommon.SprinklerProtectionAutomatic` | by-law 136 proviso |
| Compartment | `IfcSpace` · `MY_FireSafety.CompartmentId` | by-law 136 |
| Compartment of an element of structure (optional) | `IfcColumn`, `IfcBeam`, `IfcSlab`, `IfcWall` · `MY_FireSafety.CompartmentId`, comma-separated for a wall between compartments | Ninth Schedule: picks the line from that compartment's size; without it, from the spaces the element touches |
| Floor area | `IfcSpace` · `Qto_SpaceBaseQuantities.NetFloorArea` | by-law 175 |
| Travel distance, dead end | `IfcSpace` · `MY_FireSafety.MaxTravelDistance`, `DeadEndDistance` | by-law 165 |
| Occupant-load row | `IfcSpace` · `MY_FireSafety.OccupancyUse` | Seventh Schedule |
| Compartment wall / floor | `Pset_WallCommon` / `Pset_SlabCommon` `.Compartmentation`, `.FireRating` | by-law 148, Ninth Schedule |
| Fire exit, exit width | `Pset_DoorCommon.FireExit`, `IfcDoor.OverallWidth` | by-law 167–177 |
| Escape stair | `Pset_StairCommon.FireExit` | by-law 168 |
| Self-closing fire door | `Pset_DoorCommon.SelfClosing` | by-law 164 |
| External, loadbearing wall | `Pset_WallCommon.IsExternal`, `.LoadBearing` (exported by Revit and ArchiCAD) | by-law 214(1)(a) |
| Separating wall (between buildings) | `IfcWall` · `MY_FireSafety.SeparatingWall` | by-law 214(1)(b) |
| Fire lift | `Pset_TransportElementElevator.FireFightingLift` | by-law 229(5) |
| Hydrant, breeching inlet, hose reel, sprinkler | `IfcFireSuppressionTerminal` with a predefined type and a `Tag` | by-law 225, 229–231 |
| Break glass, bell | `IfcAlarm` BREAKGLASSBUTTON / BELL | by-law 237 |
| Smoke / heat detector | `IfcSensor` SMOKESENSOR / HEATSENSOR | by-law 225, Tenth Schedule |

`FireRating` is a free label in IFC. The pack accepts minutes (`60`,
`FRP 60 min`) or hours (`1 HR`, `1/2 hour`).

The **tags** on active objects (`H1`, `BI1`, `HR3`) should match the tags
on the 2D drawings. The PDF checker uses the same tags to compare the
passive and active sets.

## Rule checks

| Rule | Checks | Basis |
|---|---|---|
| IFC-COMP-AREA / -VOL | Storey area and volume of each compartment against the Fifth Schedule, doubled when sprinklered | by-law 136 |
| IFC-TRAVEL, IFC-DEADEND | Declared distances against the Seventh Schedule | by-law 165(4) |
| IFC-EXIT-COUNT | At least two exits on every occupied storey: exit doors, or, on a storey with none, escape stairs coming up from the storey below | by-law 167, 177(d) |
| IFC-EXIT-SINGLE | One exit, in a domestic or office building whose topmost floor is at most 12 m: a warning listing the by-law 194 conditions, not an error | by-law 194 |
| IFC-OCCUPANCY | A space's OccupancyUse names a Seventh Schedule row for the building's group; otherwise its occupants can't be counted | by-law 175 |
| IFC-EXIT-WIDTH | Occupant load against units of 550 mm exit width | by-law 175–176 |
| IFC-COMP-FLOOR | In buildings over 30 m, floors more than 9 m up are compartment floors | by-law 137 |
| IFC-FIREDOOR | A door in a compartment wall has an FRP of at least 30 min, and is warned if weaker than the wall | by-law 162 |
| IFC-DOORCLOSER | No fire door (rated 30 min or more) or exit door is declared not self-closing; one warning lists the doors that don't say | by-law 164(1), 173(2) |
| IFC-FRP | Elements of structure against the Ninth Schedule; loadbearing external walls at least 30 min and separating walls at least 60 min whatever the line | by-law 213, 214(1), 216 |
| IFC-COMMAND-CENTRE | Over 30.5 m, a space named as the command and control centre (warning: found by name) | by-law 238 |
| IFC-FIRE-LOBBY | A space named as a fire-fighting access lobby has at least 5.57 m² (a warning when its area can't be read) | by-law 242(a) |
| IFC-RISER, IFC-FIRELIFT | A topmost floor over 18.3 m needs a rising system and a fire lift | by-law 229–231 |

## Limits and open points

- **NBeS alignment is provisional.** CIDB's NBeS IFC parameter guideline is
  not public. `MY_FireSafety` and its property names should be renamed to
  the NBeS ones once that guideline can be read. The standard `Pset_*`
  items will not change.
- **Travel distance is declared, not computed.** The model states the
  distance and the check compares it with the limit. Computing walking
  paths from geometry is not done.
- **Tenth Schedule: presence, not layout.** The rule checks look up the
  building's Tenth Schedule line and warn when a required system (hose reels,
  hydrants, detectors, manual alarms) has no object in the model; that is
  normal for an architectural model. Coverage and spacing are not checked.
- **Exits are counted per storey, not per dwelling.** A duplex block with one
  stair in each unit has two stairs on the upper storey, so it passes the
  two-exit rule though each unit has one. By-law 194 then applies unit by
  unit, which the checker cannot see. Stair widths are not checked: the
  model has no width property the checker can rely on.
- **Rooms found by name.** The command centre and fire-fighting lobbies are
  recognised from the space's Name, LongName or room type, in English or
  Malay. A room with another name is missed, so a missing command centre
  is a warning. Without `BuildingHeight`, the 30.5 m test uses the top
  storey's elevation, which is lower than the by-law 135(a) height.
- **Ninth Schedule: listed elements only.** Element FRP is checked against the
  Ninth Schedule for walls, floors, columns and beams. Notional periods of
  materials (Part 2) are not encoded.
- **One possible misprint.** In the JKR text of the Seventh Schedule, the
  Shops travel-distance row prints sprinklered as lower than unsprinklered.
  The tool uses 45 m unsprinklered and 60 m sprinklered, and `ubbl.ERRATA`
  records the doubt.
- **Not checked in the authoring tools.** The Revit and ArchiCAD templates
  have not been loaded into either tool. Check one export with
  `ubblcheck ifc` before relying on them.

## Setting up Revit

1. Manage › Shared Parameters: open `MY_FireSafety_SharedParameters.txt`.
2. Manage › Project Parameters:
   - Add `PurposeGroup`, `SubUse`, `BuildingHeight`, `TenthUse`, `Rooms` and
     `SprinklerProtectionAutomatic` to Project Information.
   - Add `CompartmentId`, `MaxTravelDistance`, `DeadEndDistance` and
     `OccupancyUse` to Rooms.
   - Add `Compartmentation` to Walls and Floors, `FireExit` to Doors and
     Stairs, and `FireFightingLift` to Specialty Equipment or whatever
     category your lifts use.
3. IFC export setup › Property Sets:
   - Tick *Export user defined property sets* and point it at
     `MY_FireSafety_IfcPropertySets.txt`.
   - Tick *Export base quantities* so rooms get `Qto_SpaceBaseQuantities`.
4. Export IFC4 Reference View and run `ubblcheck ifc`.

## Setting up ArchiCAD

1. Options › Property Manager:
   - Create the group `MY_FireSafety`.
   - Add the properties listed in `MY_FireSafety_properties.csv`, using the
     types shown.
   - Make the Zone properties available for Zones, and the building ones for
     the project.
2. File › Interoperability › IFC › IFC Translators › Property Mapping:
   - Map the `MY_FireSafety` group to an IFC property set of the same name
     on `IfcSpace` and `IfcBuilding`.
   - Map `Compartmentation` and `FireExit` to the built-in `Pset_*Common`
     entries.
3. Turn on *Export IFC base quantities*. Export IFC4 and run `ubblcheck ifc`.
