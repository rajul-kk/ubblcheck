# Public models run through the checker

The tests use small synthetic models. To see how the checker behaves on models made in real
authoring tools, nine openly published models were run through `ubblcheck ifc` on
2026-11-11. None of them is Malaysian, so none of them carries `MY_FireSafety`. Each was run
twice:

- **as published**, to check that nothing crashes and that missing data is reported as
  missing;
- **annotated**, with a purpose group and sub-use on the building and one compartment per
  storey on every space. This is the least a Malaysian user would add, and it lets the rule
  checks run.

The models are not kept in the repository.

| Model | Source | Tool, schema | Annotated as |
|---|---|---|---|
| Building-Architecture (×3) | buildingSMART Certification-datasets, CC BY 4.0 | IFC2X3, IFC4, IFC4X3 | IV |
| AC20-FZK-Haus | KIT, via web-ifc public test files | ArchiCAD, IFC4 | I |
| C20-Institute-Var-2 | KIT, via web-ifc public test files | ArchiCAD, IFC4 | II school |
| Office_A_20110811 | NIBS common BIM files, via web-ifc | Revit, IFC2X3 | IV |
| duplex | NIBS common BIM files, via web-ifc | Revit, IFC2X3 | III flat |
| dental_clinic | Revit sample, via web-ifc | Revit, IFC2X3 | II hospital |
| schependomlaan | Dutch housing project, via web-ifc | IFC2X3 | III flat |

## What the runs showed, and what changed

| Found | Effect | Now |
|---|---|---|
| IFC2X3 has no `IfcFireSuppressionTerminal`, `IfcSensor` or `IfcAlarm`. Revit 2X3 exports describe these objects through their type objects instead. | Office_A and dental_clinic crashed in the Tenth Schedule check. | `model.objects()` reads the predefined type from the occurrence or its type object, in any schema. |
| No model says which doors are fire exits. | Every storey got "0 exit doors" and "0 exit width" errors, plus one "no exit" error per storey. | When no door states `Pset_DoorCommon.FireExit`, the checker gives one warning for exits and one for travel distance. |
| The C20 basement slab doesn't say whether it is a compartment floor. | An error said it "must be" one. | An error only when the slab is declared not to be a compartment floor. When nothing is declared, a warning asks for the property. Only floor slabs are considered. |
| Room names "Hallway" and "entry hall" matched the Group VII word *hall*. | The houses were reported as containing a place of assembly. | Hallways, entrance halls, foyers and passages count as circulation. *Hall* matches only as a whole word. |
| A Revit space named "OPEN TO BELOW" sits on the dental clinic's roof storey. | The void counted as floor area, and its storey counted as a storey of the building. | Voids, double-height volumes and `GFA` area zones count as zero floor area. |
| "Building purpose group is None" | Read like a crash. | Says the group is not declared, and which property to set. |
| With its four external doors flagged as fire exits, the duplex's upper storey still had "0 exit doors": it is left by the stair inside each unit. | Two exit errors on the upper storey, and two more on a Revit space named "Roof". | A storey with no exit door counts the escape stairs coming up from the storey below. Spaces named only "Roof" or "Attic" count as zero floor area. With its stairs flagged as well, the duplex has no exit findings. |
| The dental clinic's gas store is walled with Revit walls typed "Rated 1-HR", and each carries `Pset_WallCommon.FireRating` "1 HR". | The risk-room check read a rating only from an object of class exactly `IfcWall`, so the clinic's `IfcWallStandardCase` walls (every IFC2X3 export) were listed as unrated. | Ratings are read from the common property set of the element's class or any of its parents. |

## What is left, as published

Every model runs. The remaining errors are the IDS pack reporting missing `MY_FireSafety` and
`Pset_*` data, which is correct: a Bomba model must carry it. Once annotated, the only rule
error is one IFC-COMP-AREA finding on the dental clinic. Its first floor is about 2,450 m²,
over the 2,000 m² limit for a Group II building at that height, so it is true for the
classification given. (A second one, on the floor above, went away once Revit rooms named
"ROOF", drawn over the flat roofs, stopped counting as floor area.) The other findings are warnings that ask for data (fire ratings,
fire exits, fire appliance access, volumes) or name active systems that an architectural
model doesn't contain.

## Not yet tried

These were all architecture models from Europe and the US. A real Malaysian Bomba model is
still the test that matters (see `ambitions.md`).

## Time and peak memory

The baseline later changes are compared against. Measured on 2026-11-13 on the development
laptop (i7-1355U, 16 GB, otherwise idle), one fresh process per model, `run.ifc` with heatmaps.
Peak memory is the whole process tree's resident memory, sampled every 0.1 s. "Before" is
the code before the speed-up (commit 003b8fd). The findings are identical in both.

| Input | Before | Now |
|---|---|---|
| duplex | 2.9 s, 111 MB | 2.0 s, 117 MB |
| AC20-FZK-Haus | 3.9 s, 121 MB | 3.0 s, 130 MB |
| Office_A | 7.3 s, 121 MB | 3.4 s, 128 MB |
| Building-Architecture IFC2X3 / IFC4 | 1.5 s, 98 MB | 1.5 s, 100 MB |
| C20-Institute-Var-2 | 12.2 s, 156 MB | 5.4 s, 197 MB |
| dental_clinic | 19.3 s, 168 MB | 7.1 s, 181 MB |
| schependomlaan (49 MB) | 36.4 s, 347 MB | 15.1 s, 460 MB |
| 163-page PDF, read | 24.4 s, 1247 MB | 11.1 s, 254 MB |

Opening schependomlaan alone peaks at about 345 MB. The rest of the larger peak now comes
from tessellating on every core at once; 4 threads would save about 25 MB and cost about 2 s.
Repeated runs of the same model vary by about 10% in peak memory.
