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
| Sprinklered | `IfcBuilding` · `Pset_BuildingCommon.SprinklerProtectionAutomatic` | by-law 136 proviso |
| Compartment | `IfcSpace` · `MY_FireSafety.CompartmentId` | by-law 136 |
| Floor area | `IfcSpace` · `Qto_SpaceBaseQuantities.NetFloorArea` | by-law 175 |
| Travel distance, dead end | `IfcSpace` · `MY_FireSafety.MaxTravelDistance`, `DeadEndDistance` | by-law 165 |
| Occupant-load row | `IfcSpace` · `MY_FireSafety.OccupancyUse` | Seventh Schedule |
| Compartment wall / floor | `Pset_WallCommon` / `Pset_SlabCommon` `.Compartmentation`, `.FireRating` | by-law 148, Ninth Schedule |
| Fire exit, exit width | `Pset_DoorCommon.FireExit`, `IfcDoor.OverallWidth` | by-law 167–177 |
| Escape stair | `Pset_StairCommon.FireExit` | by-law 168 |
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
| IFC-EXIT-COUNT | At least two exit doors on every occupied storey | by-law 177(d) |
| IFC-EXIT-WIDTH | Occupant load against units of 550 mm exit width | by-law 175–176 |
| IFC-COMP-FLOOR | In buildings over 30 m, floors more than 9 m up are compartment floors | by-law 137 |
| IFC-FIREDOOR | A door in a compartment wall has an FRP of at least 30 min, and is warned if weaker than the wall | by-law 162 |
| IFC-RISER, IFC-FIRELIFT | A topmost floor over 18.3 m needs a rising system and a fire lift | by-law 229–231 |

## Limits and open points

- **NBeS alignment is provisional.** CIDB's NBeS IFC parameter guideline is
  not public. `MY_FireSafety` and its property names should be renamed to
  the NBeS ones once that guideline can be read. The standard `Pset_*`
  items will not change.
- **Travel distance is declared, not computed.** The model states the
  distance and the check compares it with the limit. Computing walking
  paths from geometry is not done.
- **Tenth Schedule not encoded.** Which active systems a building class needs
  is not yet a table here. The active IDS checks how objects are modelled,
  not whether a system is required.
- **Ninth Schedule not encoded.** Element FRP by purpose group and height is
  still to be transcribed, so wall and floor FRP values are only
  format-checked.
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
   - Add `PurposeGroup`, `SubUse`, `BuildingHeight` and
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
