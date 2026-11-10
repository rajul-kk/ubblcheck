# ubbl-precheck

Pre-submission checks for Malaysian fire (Bomba) submissions under the Uniform
Building By-Laws 1984, Parts VII (passive) and VIII (active).

Most rejected Bomba submissions fail on paperwork, not engineering. Typical
causes:

- an item is missing from the drawings;
- the architect's passive set and the M&E engineer's active set disagree;
- the yellow access road is left on the active drawings;
- a ramp or travel distance breaks a limit;
- the occupancy load table is missing.

This tool catches those problems before submission. It checks; it does not
approve, and it does not replace the submitting person's judgement.

There are two parts:

| Part | Input | Output |
|---|---|---|
| **IFC-MY rule pack** | an IFC model | BCF issues, JSON, PDF report |
| **Drawing-set checker** | the passive and active PDF sets and a short project form | JSON and a PDF report that gives each finding with its sheet and by-law |

## Install

```
python -m venv .venv
.venv/Scripts/python -m pip install --upgrade pip   # editable installs need pip >= 21.3
.venv/Scripts/pip install -e .[test]                # Windows; .venv/bin/pip elsewhere
```

## Checking a drawing set

```
ubblcheck form project.yaml          # write a blank project form, then fill it in
ubblcheck pdf project.yaml -o out    # out/project.json, out/project-report.pdf
```

The form gives the purpose group, the state, whether the building is
sprinklered, the topmost floor height, the building volume, the submission
type and the PDF paths. The PDFs must be vector exports from CAD with a
text layer. Scanned pages are reported as unchecked.

| Check | What it looks at | Basis |
|---|---|---|
| Completeness | Each item on the IPM passive and active lists, per sheet type (`data/checklist.yaml`). Some items apply only to every floor plan, some only to tall or sprinklered buildings | IPM guide, by-law per item |
| Passive vs active | Every passive floor plan and site plan has an active counterpart. Fire door, hydrant, breeching inlet, hose reel, extinguisher, stair and fire lift tags match level by level. Area figures agree | IPM: inconsistency fails the application |
| Yellow road on active | Yellow linework on an active floor or site plan | IPM active floor/site 1 |
| Colour coding on passive | Red compartment walls on floor plans; yellow access route on the site plan | IPM passive floor 7, site 4 |
| Ramp gradient | `1:n` on ramp lines against 1:12, or 1:15 in Selangor | IPM passive site 3 |
| Travel distance, dead end | Annotated distances against the Seventh Schedule for the purpose group and sprinkler status | by-law 165, Seventh Schedule |
| Order | An active submission without a passive approval reference | IPM: active follows passive approval |

## Checking an IFC model

```
ubblcheck ifc model.ifc -o out       # out/model.bcf, out/model.json, out/model-report.pdf
ubblcheck ifc model.ifc -o out --heatmaps    # plus out/heatmaps/travel-<storey>.png
```

This runs the IDS files in `src/ubblcheck/ids/`, which check that the model
carries the data. It then runs the UBBL rules, which check that the data
complies:

- compartment area and volume (Fifth Schedule);
- travel distances and occupant load against exit width (Seventh Schedule);
- compartment floors above 30 m (by-law 137);
- fire doors in compartment walls (by-law 162);
- fire resistance of columns, beams, floors and loadbearing or compartment walls
  against the Ninth Schedule line for the building's group, height, storey area and
  volume (by-law 142), including the basement column and notes (a)-(c);
- rising mains and a fire lift above 18.3 m (by-law 229–231).

It also measures from the geometry instead of trusting declared values:

- walking travel distance to the storey exits, following by-law 165(1)
  (0.3 m clearance round walls, start 0.3 m from the remote point);
- the share of the perimeter on a 12 m fire appliance access (by-law 140);
- hydrant distance from that access (by-law 225(2));
- the purpose group implied by room names, compared with the declared one;
- mixed-use compartments (by-law 138(c)).

`--heatmaps` writes a travel-distance map per storey. The method and its
tolerance are in [`docs/geometric-rules.md`](docs/geometric-rules.md).

Open the BCF file in Revit, ArchiCAD or a BCF viewer to go to each object.

The IDS files work in any IDS-aware tool. Revit and ArchiCAD set-up files and
the property mapping are in [`docs/ifc-my.md`](docs/ifc-my.md).

## Sources

- Uniform Building By-Laws 1984, JKR consolidated text: Parts VII and VIII,
  and the Fifth and Seventh Schedules. Tables are in
  `src/ubblcheck/ubbl.py`, with clause references.
- IPM Group, *Lukisan Perlindungan Kebakaran Aktif dan Pasif Bomba*
  (Lai Woon Fatt, 27 June 2022). This is the source of the item lists for
  passive and active floor plans, site plans and general information, the
  rejection causes, and the Selangor ramp limit.
- MS 1183:2015 is referenced by the guide but not encoded.

## Limits

- **The text layer is matched by pattern.** A drawing that says a thing in
  words the patterns do not know will be reported as missing. Add patterns
  in `checklist.yaml`; every item lists its own.
- **Colour is detected from vector stroke and fill.** Hatches drawn as
  images are not seen.
- **The Tenth Schedule is not yet a table.** Which active systems a building
  class needs is partly encoded as conditions (height, sprinklers).
- **Ninth Schedule periods use the largest compartment.** When spaces carry
  `CompartmentId`, the storey area and volume of the largest compartment pick the
  line for every element, so an element in a smaller compartment may be held to a
  longer period than it needs. Part 2 of the Ninth Schedule's specification tables
  (notional periods of materials) is not encoded.
- **The IFC pack's custom property set is provisional.** `MY_FireSafety`
  should be aligned with the CIDB NBeS IFC guideline, which is not public.
- **One possible misprint in the source.** In the JKR text, the Shops
  travel-distance row looks transposed. Three Ninth Schedule cells are garbled in
  the same text. Each value used is listed in `ubbl.ERRATA`.
- **Not yet tried on a real submission set.** The PDF checker has only seen
  the synthetic sets in `tests/`. The IFC checker has run on the
  buildingSMART `Building-Architecture.ifc` sample, a SketchUp export with
  no fire data; it reports the missing data and does not crash.

## Tests

```
.venv/Scripts/python -m pytest -q
```

The tests build synthetic IFC models (`tests/ifcmodel.py`) and synthetic
vector drawing sets (`tests/drawings.py`), then plant one defect at a time.
